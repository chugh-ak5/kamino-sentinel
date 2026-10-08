"""
Liquidation defense and automated deleveraging engine for Kamino Lending obligations.

Provides:
- Exact liquidation price and distance-to-liquidation analytics
- Multi-scenario asset stress shock matrices
- Atomic self-collateral flash-unwind and external capital repayment optimizers
"""

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import List, Optional


class DeleveragingMethod(str, Enum):
    EXTERNAL_REPAY = "EXTERNAL_REPAY"
    FLASH_UNWIND = "FLASH_UNWIND"


@dataclass
class StressResult:
    shock_pct: float
    collateral_usd: float
    borrow_usd: float
    health_factor: float
    current_ltv: float
    risk_level: str
    is_liquidatable: bool
    potential_penalty_usd: float  # Estimated liquidation penalty (8% default)


@dataclass
class ExecutionStep:
    step_number: int
    protocol: str
    action: str
    amount_usd: float
    detail: str


@dataclass
class DeleveragePlan:
    method: DeleveragingMethod
    target_health_factor: float
    current_health_factor: float
    debt_to_repay_usd: float
    collateral_to_withdraw_usd: float
    fee_and_slippage_usd: float
    resulting_collateral_usd: float
    resulting_borrow_usd: float
    resulting_health_factor: float
    resulting_ltv: float
    is_viable: bool
    unviable_reason: Optional[str] = None
    steps: List[ExecutionStep] = field(default_factory=list)


class LiquidationDefenseEngine:
    """Core mathematical engine for liquidation pre-emption and portfolio stress testing."""

    @staticmethod
    def calculate_liquidation_price(
        collateral_amount: float,
        borrow_value_usd: float,
        liquidation_threshold: float = 0.80,
    ) -> float:
        """
        Calculates the collateral asset price at which Health Factor drops to 1.00.
        P_liq = Borrow_USD / (Collateral_Units * Liquidation_Threshold)
        """
        if collateral_amount <= 0 or liquidation_threshold <= 0:
            return 0.0
        return borrow_value_usd / (collateral_amount * liquidation_threshold)

    @staticmethod
    def calculate_liquidation_distance_pct(
        current_price: float,
        liquidation_price: float,
    ) -> float:
        """
        Calculates the percentage drop in collateral price before liquidation triggers.
        """
        if current_price <= 0:
            return 0.0
        if liquidation_price >= current_price:
            return 0.0
        return ((current_price - liquidation_price) / current_price) * 100.0

    @staticmethod
    def run_stress_matrix(
        collateral_usd: float,
        borrow_usd: float,
        liquidation_threshold: float = 0.80,
        shock_percentages: Optional[List[float]] = None,
        liquidation_penalty: float = 0.08,
    ) -> List[StressResult]:
        """
        Generates a stress test matrix evaluating obligation health across various market drawdowns.
        """
        if shock_percentages is None:
            shock_percentages = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0]

        results: List[StressResult] = []
        for shock in shock_percentages:
            stressed_collateral = max(0.0, collateral_usd * (1.0 - (shock / 100.0)))
            lt_value = stressed_collateral * liquidation_threshold

            if borrow_usd <= 0.0001:
                hf = 999.0
                ltv = 0.0
                risk = "SAFE"
            else:
                ltv = (borrow_usd / stressed_collateral) if stressed_collateral > 0 else 1.0
                hf = lt_value / borrow_usd if borrow_usd > 0 else 0.0

                if hf < 1.0:
                    risk = "LIQUIDATABLE"
                elif hf <= 1.05:
                    risk = "CRITICAL"
                elif hf <= 1.15:
                    risk = "WARNING"
                elif hf <= 1.25:
                    risk = "CAUTION"
                else:
                    risk = "SAFE"

            is_liq = hf < 1.0
            penalty = (borrow_usd * liquidation_penalty) if is_liq else 0.0

            results.append(
                StressResult(
                    shock_pct=shock,
                    collateral_usd=stressed_collateral,
                    borrow_usd=borrow_usd,
                    health_factor=round(hf, 3),
                    current_ltv=round(ltv, 4),
                    risk_level=risk,
                    is_liquidatable=is_liq,
                    potential_penalty_usd=round(penalty, 2),
                )
            )

        return results

    @staticmethod
    def plan_external_repayment(
        collateral_usd: float,
        borrow_usd: float,
        liquidation_threshold: float = 0.80,
        target_health_factor: float = 1.25,
    ) -> DeleveragePlan:
        """
        Calculates required external capital repayment to bring Health Factor to target_health_factor.
        Formula:
            HF_target = (Collateral * LT) / (Borrow - Repay)
            Borrow - Repay = (Collateral * LT) / HF_target
            Repay = Borrow - (Collateral * LT) / HF_target
        """
        current_hf = (
            (collateral_usd * liquidation_threshold) / borrow_usd
            if borrow_usd > 0
            else 999.0
        )

        if current_hf >= target_health_factor:
            return DeleveragePlan(
                method=DeleveragingMethod.EXTERNAL_REPAY,
                target_health_factor=target_health_factor,
                current_health_factor=round(current_hf, 3),
                debt_to_repay_usd=0.0,
                collateral_to_withdraw_usd=0.0,
                fee_and_slippage_usd=0.0,
                resulting_collateral_usd=collateral_usd,
                resulting_borrow_usd=borrow_usd,
                resulting_health_factor=round(current_hf, 3),
                resulting_ltv=round(borrow_usd / collateral_usd, 4) if collateral_usd > 0 else 0.0,
                is_viable=True,
                steps=[
                    ExecutionStep(
                        step_number=1,
                        protocol="Kamino Lending",
                        action="No action required",
                        amount_usd=0.0,
                        detail=f"Current HF {current_hf:.2f} is already at or above target {target_health_factor:.2f}.",
                    )
                ],
            )

        target_borrow = (collateral_usd * liquidation_threshold) / target_health_factor
        required_repay = max(0.0, borrow_usd - target_borrow)

        resulting_borrow = borrow_usd - required_repay
        resulting_hf = (
            (collateral_usd * liquidation_threshold) / resulting_borrow
            if resulting_borrow > 0
            else 999.0
        )
        resulting_ltv = (resulting_borrow / collateral_usd) if collateral_usd > 0 else 0.0

        steps = [
            ExecutionStep(
                step_number=1,
                protocol="External Wallet",
                action="Fund Debt Asset",
                amount_usd=round(required_repay, 2),
                detail=f"Deposit ${required_repay:,.2f} worth of debt token into the signer wallet.",
            ),
            ExecutionStep(
                step_number=2,
                protocol="Kamino Lending (KLend)",
                action="Repay Obligation Debt",
                amount_usd=round(required_repay, 2),
                detail=f"Call repay_obligation_reserve to reduce borrow balance to ${resulting_borrow:,.2f}.",
            ),
        ]

        return DeleveragePlan(
            method=DeleveragingMethod.EXTERNAL_REPAY,
            target_health_factor=target_health_factor,
            current_health_factor=round(current_hf, 3),
            debt_to_repay_usd=round(required_repay, 2),
            collateral_to_withdraw_usd=0.0,
            fee_and_slippage_usd=0.0,
            resulting_collateral_usd=round(collateral_usd, 2),
            resulting_borrow_usd=round(resulting_borrow, 2),
            resulting_health_factor=round(resulting_hf, 3),
            resulting_ltv=round(resulting_ltv, 4),
            is_viable=True,
            steps=steps,
        )

    @staticmethod
    def plan_flash_unwind(
        collateral_usd: float,
        borrow_usd: float,
        liquidation_threshold: float = 0.80,
        target_health_factor: float = 1.25,
        swap_fee_and_slippage: float = 0.0035,  # 0.35% combined slippage + flash fee
        collateral_symbol: str = "SOL",
        debt_symbol: str = "USDC",
    ) -> DeleveragePlan:
        """
        Solves the simultaneous self-deleveraging equation:
        The user takes a flash loan of $x debt token, repays $x debt,
        withdraws $y = x * (1 + fee) collateral, swaps it via DEX aggregator,
        and repays the flash loan.

        Equation:
            HF_target = [ (C - x * (1 + s)) * LT ] / (D - x)
            HF_target * (D - x) = (C - x * (1 + s)) * LT
            HF_target * D - x * HF_target = C * LT - x * (1 + s) * LT
            x * [ HF_target - (1 + s) * LT ] = HF_target * D - C * LT
            x = [ HF_target * D - C * LT ] / [ HF_target - (1 + s) * LT ]
        """
        current_hf = (
            (collateral_usd * liquidation_threshold) / borrow_usd
            if borrow_usd > 0
            else 999.0
        )

        if current_hf >= target_health_factor:
            return DeleveragePlan(
                method=DeleveragingMethod.FLASH_UNWIND,
                target_health_factor=target_health_factor,
                current_health_factor=round(current_hf, 3),
                debt_to_repay_usd=0.0,
                collateral_to_withdraw_usd=0.0,
                fee_and_slippage_usd=0.0,
                resulting_collateral_usd=collateral_usd,
                resulting_borrow_usd=borrow_usd,
                resulting_health_factor=round(current_hf, 3),
                resulting_ltv=round(borrow_usd / collateral_usd, 4) if collateral_usd > 0 else 0.0,
                is_viable=True,
                steps=[
                    ExecutionStep(
                        step_number=1,
                        protocol="Kamino Lending",
                        action="Position Safe",
                        amount_usd=0.0,
                        detail=f"Current HF {current_hf:.2f} is already at or above target {target_health_factor:.2f}.",
                    )
                ],
            )

        denom = target_health_factor - ((1.0 + swap_fee_and_slippage) * liquidation_threshold)
        if denom <= 0:
            return DeleveragePlan(
                method=DeleveragingMethod.FLASH_UNWIND,
                target_health_factor=target_health_factor,
                current_health_factor=round(current_hf, 3),
                debt_to_repay_usd=0.0,
                collateral_to_withdraw_usd=0.0,
                fee_and_slippage_usd=0.0,
                resulting_collateral_usd=collateral_usd,
                resulting_borrow_usd=borrow_usd,
                resulting_health_factor=round(current_hf, 3),
                resulting_ltv=0.0,
                is_viable=False,
                unviable_reason=(
                    f"Target HF ({target_health_factor}) is too close to or below effective liquidation factor "
                    f"({(1.0 + swap_fee_and_slippage) * liquidation_threshold:.4f}); mathematical singularity."
                ),
            )

        numerator = (target_health_factor * borrow_usd) - (collateral_usd * liquidation_threshold)
        x_debt_repay = numerator / denom

        if x_debt_repay < 0:
            x_debt_repay = 0.0

        collateral_to_withdraw = x_debt_repay * (1.0 + swap_fee_and_slippage)
        fee_cost = collateral_to_withdraw - x_debt_repay

        if collateral_to_withdraw > collateral_usd:
            return DeleveragePlan(
                method=DeleveragingMethod.FLASH_UNWIND,
                target_health_factor=target_health_factor,
                current_health_factor=round(current_hf, 3),
                debt_to_repay_usd=round(x_debt_repay, 2),
                collateral_to_withdraw_usd=round(collateral_to_withdraw, 2),
                fee_and_slippage_usd=round(fee_cost, 2),
                resulting_collateral_usd=0.0,
                resulting_borrow_usd=round(borrow_usd - x_debt_repay, 2),
                resulting_health_factor=0.0,
                resulting_ltv=1.0,
                is_viable=False,
                unviable_reason=(
                    f"Position is critically underwater: Unwinding requires ${collateral_to_withdraw:,.2f} "
                    f"collateral, but total available collateral is only ${collateral_usd:,.2f}."
                ),
            )

        # Cap debt repay to total borrow
        if x_debt_repay > borrow_usd:
            x_debt_repay = borrow_usd
            collateral_to_withdraw = x_debt_repay * (1.0 + swap_fee_and_slippage)
            fee_cost = collateral_to_withdraw - x_debt_repay

        resulting_collateral = max(0.0, collateral_usd - collateral_to_withdraw)
        resulting_borrow = max(0.0, borrow_usd - x_debt_repay)

        if resulting_borrow <= 0.0001:
            resulting_hf = 999.0
            resulting_ltv = 0.0
        else:
            resulting_hf = (resulting_collateral * liquidation_threshold) / resulting_borrow
            resulting_ltv = resulting_borrow / resulting_collateral if resulting_collateral > 0 else 1.0

        steps = [
            ExecutionStep(
                step_number=1,
                protocol="Flash Loan Provider (Save / Kamino / Solend)",
                action=f"Flash Borrow {debt_symbol}",
                amount_usd=round(x_debt_repay, 2),
                detail=f"Borrow ${x_debt_repay:,.2f} of {debt_symbol} with zero upfront collateral.",
            ),
            ExecutionStep(
                step_number=2,
                protocol="Kamino Lending (KLend)",
                action=f"Repay {debt_symbol} Debt",
                amount_usd=round(x_debt_repay, 2),
                detail=f"Repay obligation debt to unlock collateral margin.",
            ),
            ExecutionStep(
                step_number=3,
                protocol="Kamino Lending (KLend)",
                action=f"Withdraw {collateral_symbol} Collateral",
                amount_usd=round(collateral_to_withdraw, 2),
                detail=f"Withdraw ${collateral_to_withdraw:,.2f} worth of {collateral_symbol} from reserve.",
            ),
            ExecutionStep(
                step_number=4,
                protocol="Jupiter Aggregator V6",
                action=f"Swap {collateral_symbol} -> {debt_symbol}",
                amount_usd=round(collateral_to_withdraw, 2),
                detail=(
                    f"Route swap via Jupiter: convert ${collateral_to_withdraw:,.2f} of {collateral_symbol} "
                    f"into ${x_debt_repay:,.2f} of {debt_symbol} (covering ${(fee_cost):,.2f} slippage/fees)."
                ),
            ),
            ExecutionStep(
                step_number=5,
                protocol="Flash Loan Provider",
                action=f"Repay Flash Loan",
                amount_usd=round(x_debt_repay, 2),
                detail=f"Close flash loan within atomic transaction block.",
            ),
            ExecutionStep(
                step_number=6,
                protocol="Jito Block Engine",
                action="Submit Atomic MEV Bundle",
                amount_usd=0.05,
                detail="Bundle instructions into atomic Jito bundle with tip to guarantee zero sandwiching and priority.",
            ),
        ]

        return DeleveragePlan(
            method=DeleveragingMethod.FLASH_UNWIND,
            target_health_factor=target_health_factor,
            current_health_factor=round(current_hf, 3),
            debt_to_repay_usd=round(x_debt_repay, 2),
            collateral_to_withdraw_usd=round(collateral_to_withdraw, 2),
            fee_and_slippage_usd=round(fee_cost, 2),
            resulting_collateral_usd=round(resulting_collateral, 2),
            resulting_borrow_usd=round(resulting_borrow, 2),
            resulting_health_factor=round(resulting_hf, 3),
            resulting_ltv=round(resulting_ltv, 4),
            is_viable=True,
            steps=steps,
        )
