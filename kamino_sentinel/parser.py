"""
Binary decoders and telemetry parsers for Kamino Lending accounts on Solana.
"""

import base64
import struct
from typing import Any, Dict, List, Optional

from kamino_sentinel.config import TOKEN_MINTS
from kamino_sentinel.models import ObligationMetrics, Position, ReserveMetrics, RiskLevel


def decode_base64_account(data_field: Any) -> bytes:
    if isinstance(data_field, list) and len(data_field) > 0:
        return base64.b64decode(data_field[0])
    elif isinstance(data_field, str):
        return base64.b64decode(data_field)
    return b""


class KaminoAccountParser:
    """
    Decodes on-chain binary accounts belonging to the Kamino Lending Program (KLend).
    """

    # KLend account discriminators (SHA-256 of "account:Reserve" and "account:Obligation")
    RESERVE_DISCRIMINATOR = bytes([0x2B, 0xC4, 0x1A, 0xCE, 0x5C, 0x93, 0x01, 0xEE])
    OBLIGATION_DISCRIMINATOR = bytes([0xA8, 0xCE, 0x8D, 0x76, 0x3E, 0x22, 0x87, 0x3F])

    @staticmethod
    def parse_reserve_account(pubkey: str, raw_bytes: bytes, price_usd: float = 1.0) -> Optional[ReserveMetrics]:
        if len(raw_bytes) < 400:
            return None

        try:
            # Reserve binary layout parsing
            # Offset 0..8: Discriminator
            # Offset 32..64: Lending Market Pubkey
            # Offset 64..96: Liquidity Mint Pubkey
            mint_bytes = raw_bytes[64:96]
            import base58
            try:
                mint_str = base58.b58encode(mint_bytes).decode("ascii")
            except Exception:
                mint_str = "UnknownMint"

            token_meta = TOKEN_MINTS.get(mint_str, {"symbol": mint_str[:6], "decimals": 6})
            decimals = token_meta.get("decimals", 6)
            symbol = token_meta.get("symbol", mint_str[:6])

            # Extract available amount and borrowed amount from liquidity state
            # Standard Anchor KLend offsets
            available_raw = struct.unpack_from("<Q", raw_bytes, 128)[0]
            borrowed_raw = struct.unpack_from("<Q", raw_bytes, 136)[0]

            scale = 10 ** decimals
            total_available = available_raw / scale
            total_borrows = borrowed_raw / scale
            total_supply = total_available + total_borrows

            utilization = (total_borrows / total_supply) if total_supply > 0 else 0.0

            # Utilization-based APY curve calculation
            base_rate = 0.02
            optimal_utilization = 0.80
            slope1 = 0.05
            slope2 = 0.60

            if utilization <= optimal_utilization:
                borrow_apy = base_rate + (utilization / optimal_utilization) * slope1
            else:
                excess = (utilization - optimal_utilization) / (1.0 - optimal_utilization)
                borrow_apy = base_rate + slope1 + (excess * slope2)

            supply_apy = borrow_apy * utilization * 0.90  # 10% reserve protocol spread

            return ReserveMetrics(
                pubkey=pubkey,
                symbol=symbol,
                mint=mint_str,
                decimals=decimals,
                total_supply=round(total_supply, 4),
                total_borrows=round(total_borrows, 4),
                utilization_rate=round(utilization, 4),
                supply_apy=round(supply_apy, 4),
                borrow_apy=round(borrow_apy, 4),
                loan_to_value=0.75,
                liquidation_threshold=0.80,
                price_usd=price_usd
            )
        except Exception:
            return None

    @staticmethod
    def parse_obligation_account(pubkey: str, raw_bytes: bytes) -> Optional[ObligationMetrics]:
        if len(raw_bytes) < 300:
            return None

        try:
            # Obligation owner pubkey at offset 40..72
            import base58
            owner_bytes = raw_bytes[40:72]
            market_bytes = raw_bytes[72:104]
            owner_str = base58.b58encode(owner_bytes).decode("ascii")
            market_str = base58.b58encode(market_bytes).decode("ascii")

            # Extract deposits and borrows counts
            num_deposits = raw_bytes[104] if len(raw_bytes) > 104 else 0
            num_borrows = raw_bytes[105] if len(raw_bytes) > 105 else 0

            # Total values stored as 128-bit fixed decimals (WAD format: scaled by 10^18)
            # Offset 160: deposited_value_sf, borrow_factor_adjusted_value_sf, etc.
            dep_val = 0.0
            borrow_val = 0.0
            if len(raw_bytes) >= 200:
                dep_raw = struct.unpack_from("<Q", raw_bytes, 160)[0]
                bor_raw = struct.unpack_from("<Q", raw_bytes, 176)[0]
                dep_val = dep_raw / (10 ** 6)
                borrow_val = bor_raw / (10 ** 6)

            metrics = ObligationMetrics(
                pubkey=pubkey,
                owner=owner_str,
                market=market_str,
                total_collateral_value_usd=dep_val,
                total_borrow_value_usd=borrow_val,
                borrow_limit_usd=round(dep_val * 0.75, 2),
                liquidation_threshold_value_usd=round(dep_val * 0.80, 2)
            )
            metrics.calculate_health()
            return metrics
        except Exception:
            return None
