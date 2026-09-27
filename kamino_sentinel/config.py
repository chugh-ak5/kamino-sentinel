"""
Configuration and constants for Kamino Sentinel.
"""

from typing import Dict, List

# Primary and fallback Solana RPC endpoints
DEFAULT_RPC_ENDPOINTS: List[str] = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana",
]

# Kamino Lending Program IDs and Market Addresses on Solana Mainnet
KLEND_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"

KNOWN_MARKETS: Dict[str, Dict[str, str]] = {
    "main": {
        "name": "Kamino Main Lending Market",
        "address": "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF",
        "description": "Primary high-liquidity lending market (SOL, USDC, USDT, JitoSOL)"
    },
    "jlp": {
        "name": "JLP Market",
        "address": "DxXdAyU3kCjnyggvHmY5nAwg5cRbbmdyX3npfCBWHAE4",
        "description": "Jupiter LP leveraged market"
    },
    "altcoins": {
        "name": "Altcoins Market",
        "address": "ByYi7enFCLALpdtx4rKqg8U83GvX289569u6xQYm16xT",
        "description": "High-volatility tokens and memecoins"
    }
}

# Known Mint Mappings
TOKEN_MINTS: Dict[str, Dict[str, any]] = {
    "So11111111111111111111111111111111111111112": {"symbol": "SOL", "decimals": 9},
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"symbol": "USDC", "decimals": 6},
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {"symbol": "USDT", "decimals": 6},
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {"symbol": "JitoSOL", "decimals": 9},
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {"symbol": "mSOL", "decimals": 9},
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": {"symbol": "bSOL", "decimals": 9},
    "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4": {"symbol": "JLP", "decimals": 6},
}

# Health Factor Alert Levels
HEALTH_CRITICAL_THRESHOLD = 1.05  # Imminent liquidation risk
HEALTH_WARNING_THRESHOLD = 1.15   # High liquidation risk under moderate volatility
HEALTH_CAUTION_THRESHOLD = 1.25   # Watchlist threshold

# Telemetry Poll Interval (seconds)
DEFAULT_POLL_INTERVAL = 30
