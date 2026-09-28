"""Tests for alert dispatch, cooldown and channel fan-out."""

from unittest.mock import MagicMock

import pytest

from kamino_sentinel.alerts import AlertNotifier
from kamino_sentinel.models import AlertEvent, RiskLevel


def _event(obligation="obligation-1", level=RiskLevel.WARNING, message="health dropped"):
    return AlertEvent(
        level=level,
        title=f"Position Health Degraded: {level.value}",
        message=message,
        obligation=obligation,
        health_factor=1.05,
    )


@pytest.fixture
def notifier():
    n = AlertNotifier(cooldown_seconds=60)
    n._send_telegram = MagicMock()
    n._send_webhook = MagicMock()
    return n


# --- cooldown --------------------------------------------------------------

def test_first_alert_dispatches(notifier):
    notifier.dispatch(_event())
    assert notifier.summary().get("dispatched", 0) == 1


def test_duplicate_alert_suppressed_within_cooldown(notifier):
    notifier.dispatch(_event())
    notifier.dispatch(_event())
    s = notifier.summary()
    assert s.get("dispatched", 0) == 1
    assert s.get("suppressed", 0) == 1


def test_should_dispatch_true_when_never_seen(notifier):
    assert notifier.should_dispatch("brand-new-key") is True


def test_should_dispatch_false_immediately_after_dispatch(notifier):
    notifier.dispatch(_event(obligation="k1"))
    assert notifier.should_dispatch(f"k1:{RiskLevel.WARNING.value}") is False


def test_zero_cooldown_allows_repeat(notifier):
    notifier.cooldown_seconds = 0
    notifier.dispatch(_event(obligation="k2"))
    assert notifier.should_dispatch(f"k2:{RiskLevel.WARNING.value}") is True


def test_distinct_keys_are_independent(notifier):
    notifier.dispatch(_event(obligation="alpha"))
    assert notifier.should_dispatch(f"beta:{RiskLevel.WARNING.value}") is True


# --- summary ---------------------------------------------------------------

def test_summary_returns_dict(notifier):
    assert isinstance(notifier.summary(), dict)


def test_summary_tracks_dispatched_alerts(notifier):
    notifier.dispatch(_event(obligation="s1"))
    notifier.dispatch(_event(obligation="s2"))
    assert notifier.summary().get("dispatched", 0) == 2


# --- channel fan-out -------------------------------------------------------

def test_telegram_called_when_configured():
    n = AlertNotifier(
        telegram_token="tok", telegram_chat_id="123", cooldown_seconds=0
    )
    n._send_telegram = MagicMock()
    n._send_webhook = MagicMock()
    n.dispatch(_event())
    assert n._send_telegram.call_count == 1


def test_webhook_called_when_configured():
    n = AlertNotifier(webhook_url="https://hook.example/x", cooldown_seconds=0)
    n._send_telegram = MagicMock()
    n._send_webhook = MagicMock()
    n.dispatch(_event())
    assert n._send_webhook.call_count == 1


def test_no_channels_configured_does_not_raise():
    n = AlertNotifier(
        telegram_token=None, telegram_chat_id=None, webhook_url=None, cooldown_seconds=0
    )
    n._send_telegram = MagicMock()
    n._send_webhook = MagicMock()
    n.dispatch(_event())  # must not raise
    assert n._send_telegram.call_count == 0
    assert n._send_webhook.call_count == 0
