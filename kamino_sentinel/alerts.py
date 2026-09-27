"""
Alerting system for Kamino Sentinel with multi-channel dispatch and alert deduplication.
"""

import logging
import time
from typing import Dict, List, Optional
import requests

from kamino_sentinel.models import AlertEvent, RiskLevel

logger = logging.getLogger(__name__)


class AlertNotifier:
    """Dispatches risk notifications to Telegram, Discord, and system consoles."""

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        webhook_url: Optional[str] = None,
        cooldown_seconds: int = 300
    ):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.webhook_url = webhook_url
        self.cooldown_seconds = cooldown_seconds
        self._last_alert_times: Dict[str, float] = {}
        self.history: List[AlertEvent] = []

    def should_dispatch(self, alert_key: str) -> bool:
        last = self._last_alert_times.get(alert_key, 0.0)
        return (time.time() - last) >= self.cooldown_seconds

    def dispatch(self, event: AlertEvent) -> None:
        self.history.append(event)
        alert_key = f"{event.obligation or 'market'}:{event.level.value}"

        if not self.should_dispatch(alert_key):
            return

        self._last_alert_times[alert_key] = time.time()

        # Send Telegram notification if configured
        if self.telegram_token and self.telegram_chat_id:
            self._send_telegram(event)

        # Send Generic Webhook if configured
        if self.webhook_url:
            self._send_webhook(event)

    def _send_telegram(self, event: AlertEvent) -> None:
        emoji = {
            RiskLevel.SAFE: "✅",
            RiskLevel.CAUTION: "🟡",
            RiskLevel.WARNING: "⚠️",
            RiskLevel.CRITICAL: "🚨",
            RiskLevel.LIQUIDATABLE: "💥"
        }.get(event.level, "ℹ️")

        text = (
            f"{emoji} <b>[Kamino Sentinel Alert]</b>\n"
            f"<b>Status:</b> {event.level.value}\n"
            f"<b>Notice:</b> {event.title}\n"
            f"{event.message}\n"
            f"<b>Time:</b> {event.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": self.telegram_chat_id, "text": text, "parse_mode": "HTML"}, timeout=6)
        except Exception as e:
            logger.error("Failed to send Telegram alert: %s", e)

    def _send_webhook(self, event: AlertEvent) -> None:
        payload = {
            "source": "kamino-sentinel",
            "level": event.level.value,
            "title": event.title,
            "message": event.message,
            "obligation": event.obligation,
            "health_factor": event.health_factor,
            "timestamp": event.timestamp.isoformat()
        }
        try:
            requests.post(self.webhook_url, json=payload, timeout=6)
        except Exception as e:
            logger.error("Failed to post alert webhook: %s", e)
