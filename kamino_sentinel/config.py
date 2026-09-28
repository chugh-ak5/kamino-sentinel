"""
Configuration and constants for Kamino Sentinel.

Every runtime-tunable value can be overridden through environment variables so
the daemon can be deployed to production (systemd, Docker, Kubernetes) without
code changes. Defaults are safe for local development.
"""

import os
from pathlib import Path
from typing import Any, Dict, List

# Automatically load .env if present in current directory or project root
def _load_env_file():
    env_paths = [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for p in env_paths:
        if p.exists() and p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip("'\"")
                            if k and k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass
            break

_load_env_file()


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Solana RPC
# ---------------------------------------------------------------------------
# Production deployments SHOULD supply a dedicated RPC provider (Helius,
# Triton, QuickNode, ...) via SOLANA_RPC_URL. Public endpoints are heavily
# rate-limited and will intermittently fail on getProgramAccounts.
PUBLIC_RPC_ENDPOINTS: List[str] = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
]

DEFAULT_RPC_ENDPOINTS: List[str] = _env_list("SOLANA_RPC_URL", PUBLIC_RPC_ENDPOINTS)

# Per-request timeout. Kept short so a hung endpoint fails over quickly instead
# of stalling the whole scan.
RPC_TIMEOUT_SECONDS: float = _env_float("RPC_TIMEOUT_SECONDS", 8.0)

# Total wall-clock budget for a single logical RPC call across all endpoints.
RPC_TOTAL_BUDGET_SECONDS: float = _env_float("RPC_TOTAL_BUDGET_SECONDS", 20.0)

# Retry/backoff behaviour for transient failures (429/5xx/network).
RPC_MAX_RETRIES: int = _env_int("RPC_MAX_RETRIES", 3)
RPC_BACKOFF_BASE_SECONDS: float = _env_float("RPC_BACKOFF_BASE_SECONDS", 0.5)
RPC_BACKOFF_MAX_SECONDS: float = _env_float("RPC_BACKOFF_MAX_SECONDS", 8.0)


# ---------------------------------------------------------------------------
# Kamino program / markets
# ---------------------------------------------------------------------------
KLEND_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"
RESERVE_DISCRIMINATOR_HEX = "2bf2ccca1af73b7f"
RESERVE_DISCRIMINATOR_B58 = "8MMas8GHex6"
RESERVE_DISCRIMINATOR_BYTES = bytes.fromhex(RESERVE_DISCRIMINATOR_HEX)
OBLIGATION_DISCRIMINATOR_HEX = "a8ce8d763e22873f"
OBLIGATION_DISCRIMINATOR_B58 = "KzR9u3M2y6r"
OBLIGATION_DISCRIMINATOR_BYTES = bytes.fromhex(OBLIGATION_DISCRIMINATOR_HEX)

KNOWN_MARKETS: Dict[str, Dict[str, str]] = {
    "main": {
        "name": "Kamino Main Lending Market",
        "address": "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF",
        "description": "Primary high-liquidity lending market (SOL, USDC, USDT, JitoSOL)",
    },
    "jlp": {
        "name": "JLP Market",
        "address": "DxXdAyU3kCjnyggvHmY5nAwg5cRbbmdyX3npfCBWHAE4",
        "description": "Jupiter LP leveraged market",
    },
    "altcoins": {
        "name": "Altcoins Market",
        "address": "ByYi7enFCLALpdtx4rKqg8U83GvX289569u6xQYm16xT",
        "description": "High-volatility tokens and memecoins",
    },
}

TOKEN_MINTS: Dict[str, Dict[str, Any]] = {
    "So11111111111111111111111111111111111111112": {"symbol": "SOL", "decimals": 9},
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"symbol": "USDC", "decimals": 6},
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {"symbol": "USDT", "decimals": 6},
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {"symbol": "JitoSOL", "decimals": 9},
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {"symbol": "mSOL", "decimals": 9},
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": {"symbol": "bSOL", "decimals": 9},
    "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4": {"symbol": "JLP", "decimals": 6},
}


# ---------------------------------------------------------------------------
# Risk thresholds (health factor = weighted collateral / borrows)
# ---------------------------------------------------------------------------
HEALTH_CRITICAL_THRESHOLD: float = _env_float("HEALTH_CRITICAL_THRESHOLD", 1.05)
HEALTH_WARNING_THRESHOLD: float = _env_float("HEALTH_WARNING_THRESHOLD", 1.15)
HEALTH_CAUTION_THRESHOLD: float = _env_float("HEALTH_CAUTION_THRESHOLD", 1.25)


# ---------------------------------------------------------------------------
# Daemon / telemetry
# ---------------------------------------------------------------------------
DEFAULT_POLL_INTERVAL: int = _env_int("POLL_INTERVAL_SECONDS", 30)
MAX_CONCURRENT_RPC_CALLS: int = _env_int("MAX_CONCURRENT_RPC_CALLS", 5)


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = _env_str("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = _env_str("TELEGRAM_CHAT_ID", "")
ALERT_WEBHOOK_URL: str = _env_str("ALERT_WEBHOOK_URL", "")
ALERT_COOLDOWN_SECONDS: int = _env_int("ALERT_COOLDOWN_SECONDS", 300)
ALERT_TIMEOUT_SECONDS: float = _env_float("ALERT_TIMEOUT_SECONDS", 10.0)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = _env_str("LOG_LEVEL", "INFO").upper()
LOG_FORMAT: str = _env_str(
    "LOG_FORMAT", "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
LOG_JSON: bool = _env_bool("LOG_JSON", False)


def validate() -> List[str]:
    """Return a list of human-readable configuration warnings.

    Called at CLI startup so misconfiguration surfaces immediately rather than
    at the first failed RPC call.
    """
    warnings: List[str] = []

    if not DEFAULT_RPC_ENDPOINTS:
        warnings.append("No Solana RPC endpoints configured (SOLANA_RPC_URL is empty).")

    if DEFAULT_RPC_ENDPOINTS and all(
        "api.mainnet-beta.solana.com" in ep or "publicnode" in ep
        for ep in DEFAULT_RPC_ENDPOINTS
    ):
        warnings.append(
            "Only public RPC endpoints configured; expect rate limits. "
            "Set SOLANA_RPC_URL to a dedicated provider for production."
        )

    if RPC_TIMEOUT_SECONDS <= 0:
        warnings.append("RPC_TIMEOUT_SECONDS must be > 0.")

    if RPC_TOTAL_BUDGET_SECONDS < RPC_TIMEOUT_SECONDS:
        warnings.append(
            "RPC_TOTAL_BUDGET_SECONDS is smaller than RPC_TIMEOUT_SECONDS; "
            "failover may never trigger."
        )

    if RPC_MAX_RETRIES < 0:
        warnings.append("RPC_MAX_RETRIES must be >= 0.")

    if RPC_BACKOFF_MAX_SECONDS < RPC_BACKOFF_BASE_SECONDS:
        warnings.append("RPC_BACKOFF_MAX_SECONDS must be >= RPC_BACKOFF_BASE_SECONDS.")

    if DEFAULT_POLL_INTERVAL < 1:
        warnings.append("POLL_INTERVAL_SECONDS must be >= 1.")

    if not (HEALTH_CRITICAL_THRESHOLD < HEALTH_WARNING_THRESHOLD < HEALTH_CAUTION_THRESHOLD):
        warnings.append(
            "Health thresholds must satisfy CRITICAL < WARNING < CAUTION."
        )

    if bool(TELEGRAM_BOT_TOKEN) != bool(TELEGRAM_CHAT_ID):
        warnings.append(
            "Telegram alerting requires BOTH TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )

    return warnings
