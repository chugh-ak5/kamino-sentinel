"""
Unit tests for Kamino Sentinel metrics and health factor mathematics.
"""

import unittest
from kamino_sentinel.models import ObligationMetrics, RiskLevel


class TestMetrics(unittest.TestCase):
    def test_obligation_safe_health_factor(self):
        ob = ObligationMetrics(
            pubkey="test_ob_1",
            owner="test_owner_1",
            market="test_market_1",
            total_collateral_value_usd=10000.0,
            total_borrow_value_usd=5000.0,
            borrow_limit_usd=7500.0,
            liquidation_threshold_value_usd=8000.0
        )
        ob.calculate_health()
        self.assertEqual(round(ob.health_factor, 2), 1.60)
        self.assertEqual(ob.risk_level, RiskLevel.SAFE)
        self.assertEqual(round(ob.current_ltv, 2), 0.50)

    def test_obligation_warning_health_factor(self):
        ob = ObligationMetrics(
            pubkey="test_ob_2",
            owner="test_owner_2",
            market="test_market_2",
            total_collateral_value_usd=10000.0,
            total_borrow_value_usd=7000.0,
            borrow_limit_usd=7500.0,
            liquidation_threshold_value_usd=8000.0
        )
        ob.calculate_health()
        self.assertEqual(ob.risk_level, RiskLevel.WARNING)

    def test_obligation_liquidatable(self):
        ob = ObligationMetrics(
            pubkey="test_ob_3",
            owner="test_owner_3",
            market="test_market_3",
            total_collateral_value_usd=10000.0,
            total_borrow_value_usd=8500.0,
            borrow_limit_usd=7500.0,
            liquidation_threshold_value_usd=8000.0
        )
        ob.calculate_health()
        self.assertEqual(ob.risk_level, RiskLevel.LIQUIDATABLE)

    def test_obligation_zero_borrows(self):
        ob = ObligationMetrics(
            pubkey="test_ob_4",
            owner="test_owner_4",
            market="test_market_4",
            total_collateral_value_usd=5000.0,
            total_borrow_value_usd=0.0
        )
        ob.calculate_health()
        self.assertEqual(ob.health_factor, 999.0)
        self.assertEqual(ob.risk_level, RiskLevel.SAFE)


if __name__ == "__main__":
    unittest.main()
