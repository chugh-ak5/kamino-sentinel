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

    def effective_liquidation_threshold(self) -> float:
        """Returns effective aggregate liquidation threshold ratio."""
        if self.total_collateral_value_usd > 0 and self.liquidation_threshold_value_usd > 0:
            return min(0.95, max(0.50, self.liquidation_threshold_value_usd / self.total_collateral_value_usd))
        return 0.80

    def get_liquidation_price(self, collateral_units: float) -> float:
        """Calculates asset price where HF reaches 1.00."""
        from kamino_sentinel.defense import LiquidationDefenseEngine
        lt = self.effective_liquidation_threshold()
        return LiquidationDefenseEngine.calculate_liquidation_price(
            collateral_amount=collateral_units,
            borrow_value_usd=self.total_borrow_value_usd,
            liquidation_threshold=lt,
        )

    def get_liquidation_distance_pct(self, current_price: float, collateral_units: float) -> float:
        """Percentage price drop buffer before liquidation."""
        from kamino_sentinel.defense import LiquidationDefenseEngine
        liq_price = self.get_liquidation_price(collateral_units)
        return LiquidationDefenseEngine.calculate_liquidation_distance_pct(
            current_price=current_price,
            liquidation_price=liq_price,
        )

    def run_stress_test(self, shock_percentages: Optional[List[float]] = None):
        """Generates stress testing scenario matrix."""
        from kamino_sentinel.defense import LiquidationDefenseEngine
        lt = self.effective_liquidation_threshold()
        return LiquidationDefenseEngine.run_stress_matrix(
            collateral_usd=self.total_collateral_value_usd,
            borrow_usd=self.total_borrow_value_usd,
            liquidation_threshold=lt,
            shock_percentages=shock_percentages,
        )

    def plan_deleverage(
        self,
        target_health_factor: float = 1.25,
        use_flash_unwind: bool = True,
        fee_and_slippage: float = 0.0035,
        collateral_symbol: str = "SOL",
        debt_symbol: str = "USDC",
    ):
        """Generates automated deleverage plan to restore health factor."""
        from kamino_sentinel.defense import LiquidationDefenseEngine
        lt = self.effective_liquidation_threshold()
        if use_flash_unwind:
            return LiquidationDefenseEngine.plan_flash_unwind(
                collateral_usd=self.total_collateral_value_usd,
                borrow_usd=self.total_borrow_value_usd,
                liquidation_threshold=lt,
                target_health_factor=target_health_factor,
                swap_fee_and_slippage=fee_and_slippage,
                collateral_symbol=collateral_symbol,
                debt_symbol=debt_symbol,
            )
        return LiquidationDefenseEngine.plan_external_repayment(
            collateral_usd=self.total_collateral_value_usd,
            borrow_usd=self.total_borrow_value_usd,
            liquidation_threshold=lt,
            target_health_factor=target_health_factor,
        )


@dataclass
class AlertEvent:
    level: RiskLevel
    title: str
    message: str
    obligation: Optional[str] = None
    health_factor: Optional[float] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    deleverage_plan: Optional[object] = None

    def format_cli(self) -> str:
        color = {
            RiskLevel.SAFE: "green",
            RiskLevel.CAUTION: "yellow",
            RiskLevel.WARNING: "bright_yellow",
            RiskLevel.CRITICAL: "red",
            RiskLevel.LIQUIDATABLE: "bold red on white"
        }.get(self.level, "white")
        base = f"[{self.timestamp.strftime('%H:%M:%S')}] [{color}][{self.level.value}][/{color}] {self.title}: {self.message}"
        if self.deleverage_plan and getattr(self.deleverage_plan, "debt_to_repay_usd", 0) > 0:
            plan = self.deleverage_plan
            base += f"\n  -> Auto-Deleverage Defense: Flash-repay ${plan.debt_to_repay_usd:,.2f} debt (unwind ${plan.collateral_to_withdraw_usd:,.2f} collateral) to reach safe HF {plan.target_health_factor:.2f}."
        return base

