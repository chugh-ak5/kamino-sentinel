"""
Unit tests for Kamino Sentinel liquidation defense, stress matrix, and auto-deleverage engine.
"""

import unittest
from kamino_sentinel.defense import (
    DeleveragingMethod,
    LiquidationDefenseEngine,
)
from kamino_sentinel.models import AlertEvent, ObligationMetrics, RiskLevel


class TestLiquidationDefense(unittest.TestCase):
    def test_liquidation_price_and_distance(self):
        # 50 SOL collateral, $6,000 USDC borrowed, 80% liquidation threshold
        # P_liq = 6,000 / (50 * 0.80) = $150.00
        liq_price = LiquidationDefenseEngine.calculate_liquidation_price(
            collateral_amount=50.0,
            borrow_value_usd=6000.0,
            liquidation_threshold=0.80,
        )
        self.assertAlmostEqual(liq_price, 150.0, places=2)

        # At $200 current price, distance to liquidation is ((200 - 150) / 200) * 100 = 25%
        dist = LiquidationDefenseEngine.calculate_liquidation_distance_pct(
            current_price=200.0,
            liquidation_price=liq_price,
        )
        self.assertAlmostEqual(dist, 25.0, places=1)

    def test_stress_matrix(self):
        matrix = LiquidationDefenseEngine.run_stress_matrix(
            collateral_usd=10000.0,
            borrow_usd=6500.0,
            liquidation_threshold=0.80,
            shock_percentages=[0.0, 20.0, 40.0],
            liquidation_penalty=0.08,
        )
        self.assertEqual(len(matrix), 3)

        # 0% shock: HF = (10000 * 0.8) / 6500 = 1.231 -> CAUTION
        self.assertEqual(matrix[0].shock_pct, 0.0)
        self.assertAlmostEqual(matrix[0].health_factor, 1.231, places=2)
        self.assertEqual(matrix[0].risk_level, "CAUTION")
        self.assertFalse(matrix[0].is_liquidatable)
        self.assertEqual(matrix[0].potential_penalty_usd, 0.0)

        # 20% shock: Collateral = $8,000 -> HF = (8000 * 0.8) / 6500 = 0.985 -> LIQUIDATABLE
        self.assertEqual(matrix[1].shock_pct, 20.0)
        self.assertAlmostEqual(matrix[1].health_factor, 0.985, places=2)
        self.assertEqual(matrix[1].risk_level, "LIQUIDATABLE")
        self.assertTrue(matrix[1].is_liquidatable)
        self.assertAlmostEqual(matrix[1].potential_penalty_usd, 6500.0 * 0.08, places=2)

    def test_external_repayment_math(self):
        # Collateral: $10,000, Borrow: $7,500, LT: 0.80, Target HF: 1.25
        # Target Borrow = (10000 * 0.8) / 1.25 = $6,400
        # Required Repay = 7,500 - 6,400 = $1,100
        plan = LiquidationDefenseEngine.plan_external_repayment(
            collateral_usd=10000.0,
            borrow_usd=7500.0,
            liquidation_threshold=0.80,
            target_health_factor=1.25,
        )
        self.assertTrue(plan.is_viable)
        self.assertEqual(plan.method, DeleveragingMethod.EXTERNAL_REPAY)
        self.assertAlmostEqual(plan.debt_to_repay_usd, 1100.0, places=2)
        self.assertAlmostEqual(plan.resulting_borrow_usd, 6400.0, places=2)
        self.assertAlmostEqual(plan.resulting_health_factor, 1.25, places=2)
        self.assertEqual(len(plan.steps), 2)

    def test_flash_unwind_math(self):
        # Self-deleveraging with zero outside capital
        collateral = 10000.0
        borrow = 7500.0
        lt = 0.80
        target_hf = 1.25
        fee = 0.0035

        plan = LiquidationDefenseEngine.plan_flash_unwind(
            collateral_usd=collateral,
            borrow_usd=borrow,
            liquidation_threshold=lt,
            target_health_factor=target_hf,
            swap_fee_and_slippage=fee,
        )
        self.assertTrue(plan.is_viable)
        self.assertEqual(plan.method, DeleveragingMethod.FLASH_UNWIND)
        self.assertGreater(plan.debt_to_repay_usd, 0.0)
        self.assertGreater(plan.collateral_to_withdraw_usd, plan.debt_to_repay_usd)
        self.assertAlmostEqual(plan.resulting_health_factor, target_hf, places=2)

        # Ensure Jito and Jupiter execution steps are generated
        step_actions = [s.action for s in plan.steps]
        self.assertTrue(any("Flash Borrow" in a for a in step_actions))
        self.assertTrue(any("Swap" in a for a in step_actions))
        self.assertTrue(any("MEV Bundle" in a for a in step_actions))

    def test_flash_unwind_underwater_infeasible(self):
        # Completely underwater position: Collateral $3,000, Borrow $6,000
        plan = LiquidationDefenseEngine.plan_flash_unwind(
            collateral_usd=3000.0,
            borrow_usd=6000.0,
            liquidation_threshold=0.80,
            target_health_factor=1.25,
        )
        self.assertFalse(plan.is_viable)
        self.assertIsNotNone(plan.unviable_reason)

    def test_obligation_model_integration(self):
        ob = ObligationMetrics(
            pubkey="test_ob_defend",
            owner="test_owner",
            market="test_market",
            total_collateral_value_usd=10000.0,
            total_borrow_value_usd=7000.0,
            liquidation_threshold_value_usd=8000.0,
        )
        ob.calculate_health()
        self.assertAlmostEqual(ob.health_factor, 1.143, places=2)
        self.assertEqual(ob.risk_level, RiskLevel.WARNING)

        # Liquidation price with 50 SOL ($200/SOL)
        liq_price = ob.get_liquidation_price(collateral_units=50.0)
        self.assertAlmostEqual(liq_price, 175.0, places=1)

        # Plan deleverage via ObligationMetrics helper
        plan = ob.plan_deleverage(target_health_factor=1.25, use_flash_unwind=True)
        self.assertTrue(plan.is_viable)
        self.assertAlmostEqual(plan.resulting_health_factor, 1.25, places=2)

    def test_alert_event_with_plan_formatting(self):
        ob = ObligationMetrics(
            pubkey="test_ob_alert",
            owner="test_owner",
            market="test_market",
            total_collateral_value_usd=10000.0,
            total_borrow_value_usd=7500.0,
            liquidation_threshold_value_usd=8000.0,
        )
        ob.calculate_health()
        plan = ob.plan_deleverage(target_health_factor=1.25, use_flash_unwind=True)

        alert = AlertEvent(
            level=RiskLevel.WARNING,
            title="Position Health Degraded",
            message="Health Factor is 1.067",
            obligation=ob.pubkey,
            health_factor=ob.health_factor,
            deleverage_plan=plan,
        )
        cli_text = alert.format_cli()
        self.assertIn("Auto-Deleverage Defense", cli_text)
        self.assertIn("Flash-repay", cli_text)


if __name__ == "__main__":
    unittest.main()
