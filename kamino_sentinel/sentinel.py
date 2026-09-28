"""
Kamino Sentinel Monitoring Core Engine.
"""

import logging
import time
from typing import Callable, Dict, List, Optional

from kamino_sentinel.alerts import AlertNotifier
from kamino_sentinel.config import (
    DEFAULT_POLL_INTERVAL,
    KLEND_PROGRAM_ID,
    KNOWN_MARKETS,
    MAX_CONCURRENT_RPC_CALLS,
    RESERVE_DISCRIMINATOR_B58,
)
from kamino_sentinel.rpc import RpcError
from kamino_sentinel.models import AlertEvent, ObligationMetrics, ReserveMetrics, RiskLevel
from kamino_sentinel.parser import KaminoAccountParser, decode_base64_account
from kamino_sentinel.rpc import PriceOracleClient, SolanaRpcClient

logger = logging.getLogger(__name__)


class KaminoSentinel:
    """Core telemetry daemon monitoring Kamino lending markets and user positions."""

    def __init__(
        self,
        rpc_client: Optional[SolanaRpcClient] = None,
        oracle_client: Optional[PriceOracleClient] = None,
        notifier: Optional[AlertNotifier] = None
    ):
        self.rpc = rpc_client or SolanaRpcClient()
        self.oracle = oracle_client or PriceOracleClient()
        self.notifier = notifier or AlertNotifier()

        # Runtime counters, useful for health/observability endpoints.
        self.stats: Dict[str, int] = {
            "scans": 0,
            "obligations_seen": 0,
            "alerts_dispatched": 0,
            "parse_errors": 0,
            "rpc_errors": 0,
        }

    def get_market_overview(self, market_key: str = "main", allow_demo: bool = False) -> List[ReserveMetrics]:
        market_meta = KNOWN_MARKETS.get(market_key, KNOWN_MARKETS["main"])
        market_address = market_meta["address"]

        logger.info("Fetching reserves for %s (%s)", market_meta["name"], market_address)

        # Query on-chain reserve accounts with discriminator + lending market filters
        filters = [
            {"memcmp": {"offset": 0, "bytes": RESERVE_DISCRIMINATOR_B58}},
            {"memcmp": {"offset": 32, "bytes": market_address}}
        ]

        try:
            raw_accounts = self.rpc.get_program_accounts(
                program_id=KLEND_PROGRAM_ID,
                filters=filters
            )
        except RpcError as exc:
            self.stats["rpc_errors"] += 1
            logger.error("RPC failure fetching reserves: %s", exc)
            raw_accounts = []

        reserves: List[ReserveMetrics] = []
        for item in raw_accounts:
            pubkey = item.get("pubkey", "")
            acc_data = item.get("account", {}).get("data")
            try:
                raw_bytes = decode_base64_account(acc_data)
                res = KaminoAccountParser.parse_reserve_account(pubkey, raw_bytes)
            except Exception as exc:  # noqa: BLE001 - one bad account must not kill the scan
                self.stats["parse_errors"] += 1
                logger.warning("Skipping unparseable reserve %s: %s", pubkey, exc)
                continue
            if res:
                reserves.append(res)

        # IMPORTANT: never silently substitute fabricated data for a failed scan.
        # Demo/benchmark data is only returned when the caller explicitly opts in.
        if not reserves:
            if allow_demo:
                logger.warning(
                    "No live reserves returned; falling back to DEMO benchmark data "
                    "(allow_demo=True). These figures are illustrative, NOT live on-chain values."
                )
                reserves = self._get_benchmark_reserves(market_key)
            else:
                logger.error(
                    "No live reserve accounts returned for market '%s'. "
                    "Returning empty result. (Pass allow_demo=True to use illustrative demo data.)",
                    market_key,
                )

        return reserves

    def _get_benchmark_reserves(self, market_key: str) -> List[ReserveMetrics]:
        """Return ILLUSTRATIVE demo data for Kamino core reserves.

        WARNING: These are static, hand-written sample figures used only for
        demos, UI development, and offline testing. They are NOT live on-chain
        values and must never be presented as such.
        """
        benchmarks = [
            ReserveMetrics(
                pubkey="d4A2prbA2whesmvHaL88BHecTjbvMjSM2KfFY2qKMSr",
                symbol="SOL",
                mint="So11111111111111111111111111111111111111112",
                decimals=9,
                total_supply=1420550.0,
                total_borrows=842300.0,
                utilization_rate=0.5929,
                supply_apy=0.0684,
                borrow_apy=0.0892,
                loan_to_value=0.75,
                liquidation_threshold=0.80,
                price_usd=152.40
            ),
            ReserveMetrics(
                pubkey="Ga4r3spc5arha1oxPjVzU27p16wz1p9x6wV6jW85x4X",
                symbol="USDC",
                mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                decimals=6,
                total_supply=185420000.0,
                total_borrows=152800000.0,
                utilization_rate=0.8241,
                supply_apy=0.1012,
                borrow_apy=0.1245,
                loan_to_value=0.80,
                liquidation_threshold=0.85,
                price_usd=1.00
            ),
            ReserveMetrics(
                pubkey="8wG3spX7Vz6bVz88xTjbvMjSM2KfFY2qKMSrd4A2prb",
                symbol="JitoSOL",
                mint="J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
                decimals=9,
                total_supply=890400.0,
                total_borrows=124000.0,
                utilization_rate=0.1393,
                supply_apy=0.0815,
                borrow_apy=0.0380,
                loan_to_value=0.70,
                liquidation_threshold=0.75,
                price_usd=178.60
            ),
            ReserveMetrics(
                pubkey="9tQ3spX7Vz6bVz88xTjbvMjSM2KfFY2qKMSrd4A2prc",
                symbol="USDT",
                mint="Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                decimals=6,
                total_supply=45120000.0,
                total_borrows=36800000.0,
                utilization_rate=0.8156,
                supply_apy=0.0980,
                borrow_apy=0.1190,
                loan_to_value=0.80,
                liquidation_threshold=0.85,
                price_usd=1.00
            )
        ]
        return benchmarks

    def get_user_obligations(self, wallet_pubkey: str, market_key: str = "main") -> List[ObligationMetrics]:
        market_meta = KNOWN_MARKETS.get(market_key, KNOWN_MARKETS["main"])
        market_address = market_meta["address"]

        filters = [
            {"memcmp": {"offset": 40, "bytes": wallet_pubkey}},
            {"memcmp": {"offset": 72, "bytes": market_address}}
        ]

        try:
            raw_accounts = self.rpc.get_program_accounts(
                program_id=KLEND_PROGRAM_ID,
                filters=filters
            )
        except RpcError as exc:
            self.stats["rpc_errors"] += 1
            logger.error("RPC failure fetching obligations for %s: %s", wallet_pubkey, exc)
            return []

        obligations: List[ObligationMetrics] = []
        for item in raw_accounts:
            pubkey = item.get("pubkey", "")
            acc_data = item.get("account", {}).get("data")
            try:
                raw_bytes = decode_base64_account(acc_data)
                ob = KaminoAccountParser.parse_obligation_account(pubkey, raw_bytes)
            except Exception as exc:  # noqa: BLE001
                self.stats["parse_errors"] += 1
                logger.warning("Skipping unparseable obligation %s: %s", pubkey, exc)
                continue
            if ob:
                obligations.append(ob)

        return obligations

    def evaluate_obligation_risk(self, obligation: ObligationMetrics) -> Optional[AlertEvent]:
        obligation.calculate_health()
        if obligation.risk_level in (RiskLevel.WARNING, RiskLevel.CRITICAL, RiskLevel.LIQUIDATABLE):
            event = AlertEvent(
                level=obligation.risk_level,
                title=f"Position Health Degraded: {obligation.risk_level.value}",
                message=(
                    f"Health Factor is {obligation.health_factor:.3f} (LTV: {obligation.current_ltv * 100:.1f}%). "
                    f"Deposits: ${obligation.total_collateral_value_usd:,.2f}, "
                    f"Borrows: ${obligation.total_borrow_value_usd:,.2f}. "
                    f"Immediate collateral top-up or debt repayment advised."
                ),
                obligation=obligation.pubkey,
                health_factor=obligation.health_factor
            )
            self.notifier.dispatch(event)
            self.stats["alerts_dispatched"] += 1
            return event
        return None

    def watch(
        self,
        wallet_pubkey: str,
        interval: Optional[int] = None,
        iteration_callback: Optional[Callable[[List[ObligationMetrics], List[AlertEvent]], None]] = None,
        max_iterations: Optional[int] = None,
    ) -> None:
        """Poll a wallet's obligations forever (or `max_iterations` times).

        Each cycle is fully isolated: a failure in one obligation, or in the
        RPC layer, is logged and the daemon keeps running. This is what makes
        it safe to run under systemd/Docker without a supervisor restart loop.
        """
        interval = interval or DEFAULT_POLL_INTERVAL
        logger.info(
            "Starting Kamino Sentinel watch daemon for %s (interval: %ds, max_iterations: %s)",
            wallet_pubkey,
            interval,
            max_iterations if max_iterations is not None else "unlimited",
        )

        iteration = 0
        while True:
            if max_iterations is not None and iteration >= max_iterations:
                logger.info("Reached max_iterations (%d); exiting watch loop.", max_iterations)
                return
            iteration += 1
            self.stats["scans"] += 1

            try:
                obs = self.get_user_obligations(wallet_pubkey)
                self.stats["obligations_seen"] += len(obs)

                alerts: List[AlertEvent] = []
                for ob in obs:
                    try:
                        alert = self.evaluate_obligation_risk(ob)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Risk evaluation failed for obligation %s: %s",
                            getattr(ob, "pubkey", "?"),
                            exc,
                        )
                        continue
                    if alert:
                        alerts.append(alert)

                if iteration_callback:
                    try:
                        iteration_callback(obs, alerts)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("iteration_callback raised: %s", exc)

                logger.info(
                    "Scan #%d complete: %d obligations, %d alerts (endpoint: %s)",
                    iteration,
                    len(obs),
                    len(alerts),
                    self.rpc.current_endpoint,
                )
            except KeyboardInterrupt:
                logger.info("Watch loop interrupted; shutting down cleanly.")
                return
            except Exception as exc:  # noqa: BLE001
                self.stats["rpc_errors"] += 1
                logger.error("Error in sentinel polling cycle: %s", exc)

            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                logger.info("Watch loop interrupted during sleep; shutting down cleanly.")
                return
