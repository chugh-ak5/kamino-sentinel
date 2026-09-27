"""
Data models for Kamino Sentinel telemetry.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    LIQUIDATABLE = "LIQUIDATABLE"


@dataclass
class ReserveMetrics:
    pubkey: str
    symbol: str
    mint: str
    decimals: int
    total_supply: float
    total_borrows: float
    utilization_rate: float
    supply_apy: float
    borrow_apy: float
    loan_to_value: float
    liquidation_threshold: float
    price_usd: float

    @property
    def available_liquidity(self) -> float:
        return max(0.0, self.total_supply - self.total_borrows)


@dataclass
class Position:
    symbol: str
    mint: str
    amount: float
    value_usd: float
    price_usd: float
    is_collateral: bool


@dataclass
class ObligationMetrics:
    pubkey: str
    owner: str
    market: str
    collaterals: List[Position] = field(default_factory=list)
    borrows: List[Position] = field(default_factory=list)
    total_collateral_value_usd: float = 0.0
    total_borrow_value_usd: float = 0.0
    borrow_limit_usd: float = 0.0
    liquidation_threshold_value_usd: float = 0.0
    health_factor: float = float("inf")
    current_ltv: float = 0.0
    risk_level: RiskLevel = RiskLevel.SAFE

    def calculate_health(self) -> None:
        if self.total_borrow_value_usd <= 0.0001:
            self.health_factor = 999.0
            self.current_ltv = 0.0
            self.risk_level = RiskLevel.SAFE
            return

        self.current_ltv = (self.total_borrow_value_usd / self.total_collateral_value_usd) if self.total_collateral_value_usd > 0 else 1.0

        if self.liquidation_threshold_value_usd > 0:
            self.health_factor = self.liquidation_threshold_value_usd / self.total_borrow_value_usd
        elif self.total_collateral_value_usd > 0:
            # Fallback 80% liquidation threshold assumption
            self.health_factor = (self.total_collateral_value_usd * 0.80) / self.total_borrow_value_usd
        else:
            self.health_factor = 0.0

        if self.health_factor < 1.0:
            self.risk_level = RiskLevel.LIQUIDATABLE
        elif self.health_factor <= 1.05:
            self.risk_level = RiskLevel.CRITICAL
        elif self.health_factor <= 1.15:
            self.risk_level = RiskLevel.WARNING
        elif self.health_factor <= 1.25:
            self.risk_level = RiskLevel.CAUTION
        else:
            self.risk_level = RiskLevel.SAFE


@dataclass
class AlertEvent:
    level: RiskLevel
    title: str
    message: str
    obligation: Optional[str] = None
    health_factor: Optional[float] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def format_cli(self) -> str:
        color = {
            RiskLevel.SAFE: "green",
            RiskLevel.CAUTION: "yellow",
            RiskLevel.WARNING: "bright_yellow",
            RiskLevel.CRITICAL: "red",
            RiskLevel.LIQUIDATABLE: "bold red on white"
        }.get(self.level, "white")
        return f"[{self.timestamp.strftime('%H:%M:%S')}] [{color}][{self.level.value}][/{color}] {self.title}: {self.message}"
