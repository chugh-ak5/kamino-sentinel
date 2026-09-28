"""Tests for configuration loading and validation."""

import importlib

import pytest

from kamino_sentinel import config


def _reload(monkeypatch, **env):
    """Reload config with a controlled environment."""
    for key in (
        "SOLANA_RPC_URL",
        "RPC_TIMEOUT_SECONDS",
        "RPC_TOTAL_BUDGET_SECONDS",
        "RPC_MAX_RETRIES",
        "RPC_BACKOFF_BASE_SECONDS",
        "RPC_BACKOFF_MAX_SECONDS",
        "POLL_INTERVAL_SECONDS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ALERT_WEBHOOK_URL",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    return importlib.reload(config)


# --- env coercion helpers --------------------------------------------------

def test_env_float_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("RPC_TIMEOUT_SECONDS", "not-a-number")
    cfg = _reload(monkeypatch)
    assert cfg.RPC_TIMEOUT_SECONDS == 8.0


def test_env_int_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("RPC_MAX_RETRIES", "abc")
    cfg = _reload(monkeypatch)
    assert cfg.RPC_MAX_RETRIES == 3


def test_env_list_parses_comma_separated(monkeypatch):
    cfg = _reload(monkeypatch, SOLANA_RPC_URL="https://a.example, https://b.example")
    assert cfg.DEFAULT_RPC_ENDPOINTS == ["https://a.example", "https://b.example"]


def test_env_list_ignores_blank_entries(monkeypatch):
    cfg = _reload(monkeypatch, SOLANA_RPC_URL="https://a.example,,  ,https://b.example")
    assert cfg.DEFAULT_RPC_ENDPOINTS == ["https://a.example", "https://b.example"]


def test_env_list_falls_back_when_empty(monkeypatch):
    cfg = _reload(monkeypatch, SOLANA_RPC_URL="   ")
    assert cfg.DEFAULT_RPC_ENDPOINTS == cfg.PUBLIC_RPC_ENDPOINTS


# --- validate() ------------------------------------------------------------

def test_validate_flags_public_only_endpoints(monkeypatch):
    cfg = _reload(monkeypatch)
    warnings = cfg.validate()
    assert any("public RPC endpoints" in w for w in warnings)


def test_validate_clean_with_dedicated_endpoint(monkeypatch):
    cfg = _reload(
        monkeypatch,
        SOLANA_RPC_URL="https://mainnet.helius-rpc.com/?api-key=test",
        TELEGRAM_BOT_TOKEN="tok",
        TELEGRAM_CHAT_ID="123",
    )
    assert cfg.validate() == []


def test_validate_flags_non_positive_timeout(monkeypatch):
    cfg = _reload(monkeypatch, RPC_TIMEOUT_SECONDS="0")
    assert any("RPC_TIMEOUT_SECONDS must be > 0" in w for w in cfg.validate())


def test_validate_flags_budget_smaller_than_timeout(monkeypatch):
    cfg = _reload(monkeypatch, RPC_TIMEOUT_SECONDS="30", RPC_TOTAL_BUDGET_SECONDS="5")
    assert any("smaller than RPC_TIMEOUT_SECONDS" in w for w in cfg.validate())


def test_validate_flags_negative_retries(monkeypatch):
    cfg = _reload(monkeypatch, RPC_MAX_RETRIES="-1")
    assert any("RPC_MAX_RETRIES must be >= 0" in w for w in cfg.validate())


def test_validate_flags_backoff_inversion(monkeypatch):
    cfg = _reload(monkeypatch, RPC_BACKOFF_BASE_SECONDS="10", RPC_BACKOFF_MAX_SECONDS="2")
    assert any("RPC_BACKOFF_MAX_SECONDS" in w for w in cfg.validate())


def test_validate_flags_sub_second_poll_interval(monkeypatch):
    cfg = _reload(monkeypatch, POLL_INTERVAL_SECONDS="0")
    assert any("POLL_INTERVAL_SECONDS must be >= 1" in w for w in cfg.validate())


def test_validate_flags_half_configured_telegram(monkeypatch):
    cfg = _reload(monkeypatch, TELEGRAM_BOT_TOKEN="tok")
    assert any("BOTH TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID" in w for w in cfg.validate())


def test_validate_accepts_fully_configured_telegram(monkeypatch):
    cfg = _reload(
        monkeypatch,
        SOLANA_RPC_URL="https://mainnet.helius-rpc.com/?api-key=test",
        TELEGRAM_BOT_TOKEN="tok",
        TELEGRAM_CHAT_ID="123",
    )
    assert cfg.validate() == []


@pytest.fixture(autouse=True)
def _restore_config():
    """Ensure module-level config is restored after each test."""
    yield
    importlib.reload(config)
