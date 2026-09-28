"""
Alerting system for Kamino Sentinel with multi-channel dispatch and alert deduplication.

Production upgrades:
  * Thread-safe alert history and cooldown map
  * Retry logic (up to 3 attempts) on network errors for each channel
  * Structured logging with event context on every dispatch
  * Alert severity escalation guard (suppress re-alert within cooldown window)
  * Telegram and webhook payloads include obligation pubkey + health factor
  * AlertNotifier.summary() returns a stats dict for CLI/daemon health endpoints
"""

import logging
import threading
import time
from typing import Dict, List, Optional

import requests

from kamino_sentinel.config import (
    ALERT_COOLDOWN_SECONDS,
    ALERT_TIMEOUT_SECONDS,
    ALERT_WEBHOOK_URL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from kamino_sentinel.models import AlertEvent, RiskLevel

logger = logging.getLogger(__name__)

_EMOJI: Dict[RiskLevel, str] = {
    RiskLevel.SAFE: "✅",
    RiskLevel.CAUTION: "🟡",
    RiskLevel.WARNING: "⚠️",
    RiskLevel.CRITICAL: "🚨",
    RiskLevel.LIQUIDATABLE: "💥",
}

_MAX_SEND_ATTEMPTS = 3
_SEND_RETRY_DELAY = 1.5  # seconds between send retries


class AlertNotifier:
    """Dispatches risk notifications to Telegram, Discord webhooks, and system console.

    Thread-safe: designed for use from the sentinel daemon's polling thread.
    """

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        webhook_url: Optional[str] = None,
        cooldown_seconds: Optional[int] = None,
        request_timeout: Optional[float] = None,
    ):
        self.telegram_token = TELEGRAM_BOT_TOKEN if telegram_token is None else telegram_token
        self.telegram_chat_id = TELEGRAM_CHAT_ID if telegram_chat_id is None else telegram_chat_id
        self.webhook_url = ALERT_WEBHOOK_URL if webhook_url is None else webhook_url
        self.cooldown_seconds = ALERT_COOLDOWN_SECONDS if cooldown_seconds is None else cooldown_seconds
        self.request_timeout = ALERT_TIMEOUT_SECONDS if request_timeout is None else request_timeout

        self._lock = threading.Lock()
        self._last_alert_times: Dict[str, float] = {}
        self.history: List[AlertEvent] = []
        self._dispatch_count = 0
        self._suppressed_count = 0
        self._error_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_dispatch(self, alert_key: str) -> bool:
        with self._lock:
            last = self._last_alert_times.get(alert_key, 0.0)
            return (time.time() - last) >= self.cooldown_seconds

    def dispatch(self, event: AlertEvent) -> None:
        """Evaluate cooldown and fan-out the alert to all configured channels."""
        with self._lock:
            self.history.append(event)

        alert_key = f"{event.obligation or 'market'}:{event.level.value}"

        if not self.should_dispatch(alert_key):
            logger.debug(
                "Alert suppressed (cooldown active): key=%s level=%s",
                alert_key,
                event.level.value,
            )
            with self._lock:
                self._suppressed_count += 1
            return

        with self._lock:
            self._last_alert_times[alert_key] = time.time()
            self._dispatch_count += 1

        logger.info(
            "Dispatching alert: level=%s obligation=%s hf=%s title=%r",
            event.level.value,
            event.obligation,
            f"{event.health_factor:.3f}" if event.health_factor is not None else "n/a",
            event.title,
        )

        if self.telegram_token and self.telegram_chat_id:
            self._send_telegram(event)

        if self.webhook_url:
            self._send_webhook(event)

    def summary(self) -> Dict[str, int]:
        """Return dispatcher statistics for health-check endpoints."""
        with self._lock:
            return {
                "total_events": len(self.history),
                "dispatched": self._dispatch_count,
                "suppressed": self._suppressed_count,
                "errors": self._error_count,
            }

    # ------------------------------------------------------------------
    # Internal channel senders
    # ------------------------------------------------------------------

    def _send_telegram(self, event: AlertEvent) -> None:
        emoji = _EMOJI.get(event.level, "ℹ️")
        hf_line = ""
        if event.health_factor is not None:
            hf_line = f"\n<b>Health Factor:</b> {event.health_factor:.3f}"
        ob_line = ""
        if event.obligation:
            ob_line = f"\n<b>Obligation:</b> <code>{event.obligation}</code>"

        text = (
            f"{emoji} <b>[Kamino Sentinel]</b>\n"
            f"<b>Status:</b> {event.level.value}\n"
            f"<b>Notice:</b> {event.title}\n"
            f"{event.message}"
            f"{hf_line}"
            f"{ob_line}\n"
            f"<b>Time:</b> {event.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": self.telegram_chat_id, "text": text, "parse_mode": "HTML"}
        self._post_with_retry("Telegram", url, payload)

    def _send_webhook(self, event: AlertEvent) -> None:
        payload = {
            "source": "kamino-sentinel",
            "level": event.level.value,
            "title": event.title,
            "message": event.message,
            "obligation": event.obligation,
            "health_factor": event.health_factor,
            "timestamp": event.timestamp.isoformat(),
        }
        self._post_with_retry("Webhook", self.webhook_url, payload)

    def _post_with_retry(self, channel: str, url: str, payload: dict) -> None:
        """POST with up to _MAX_SEND_ATTEMPTS retries on transient network errors."""
        for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
            try:
                resp = requests.post(url, json=payload, timeout=self.request_timeout)
                if resp.status_code < 400:
                    logger.debug("%s alert sent (attempt %d): status=%d", channel, attempt, resp.status_code)
                    return
                logger.warning(
                    "%s alert returned HTTP %d (attempt %d/%d): %s",
                    channel, resp.status_code, attempt, _MAX_SEND_ATTEMPTS, resp.text[:120],
                )
            except requests.exceptions.Timeout:
                logger.warning("%s alert timed out (attempt %d/%d)", channel, attempt, _MAX_SEND_ATTEMPTS)
            except requests.exceptions.ConnectionError as exc:
                logger.warning("%s alert connection error (attempt %d/%d): %s", channel, attempt, _MAX_SEND_ATTEMPTS, exc)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("%s alert unexpected error (attempt %d/%d): %s", channel, attempt, _MAX_SEND_ATTEMPTS, exc)
                with self._lock:
                    self._error_count += 1
                return  # don't retry on unexpected errors

            if attempt < _MAX_SEND_ATTEMPTS:
                time.sleep(_SEND_RETRY_DELAY)

        logger.error("Failed to deliver %s alert after %d attempts.", channel, _MAX_SEND_ATTEMPTS)
        with self._lock:
            self._error_count += 1
