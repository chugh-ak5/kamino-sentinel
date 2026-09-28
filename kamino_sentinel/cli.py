"""
CLI entrypoint for Kamino Sentinel.

Live data is the default. Illustrative sample data is only ever shown when
the user explicitly passes --demo.
"""

import argparse
import json
import logging
import sys

from kamino_sentinel import config
from kamino_sentinel.config import KNOWN_MARKETS
from kamino_sentinel.models import ObligationMetrics, RiskLevel
from kamino_sentinel.sentinel import KaminoSentinel

logger = logging.getLogger("kamino_sentinel.cli")


def configure_logging(level: str) -> None:
    """Configure root logging once, honouring LOG_JSON for log shippers."""
    if config.LOG_JSON:
        fmt = '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    else:
        fmt = config.LOG_FORMAT
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        stream=sys.stderr,
        force=True,
    )


def check_config(strict: bool = False) -> None:
    """Surface configuration problems at startup instead of mid-scan."""
    warnings = config.validate()
    for w in warnings:
        logger.warning("config: %s", w)
    if warnings and strict:
        print("Configuration errors detected (use --no-strict to ignore):", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        sys.exit(2)


def print_market_table(market: str, reserves):
    print(f"\n⚡ Kamino Lending Reserves: {market.upper()} ({KNOWN_MARKETS.get(market, {}).get('name', market)})\n")
    headers = ["Asset", "Price (USD)", "Total Supply", "Total Borrows", "Available Liq", "Utilization", "Supply APY", "Borrow APY"]
    rows = []
    total_tvl = 0.0

    for r in reserves:
        util_pct = r.utilization_rate * 100
        supply_val = r.total_supply * r.price_usd
        total_tvl += supply_val
        rows.append([
            r.symbol,
            f"${r.price_usd:,.2f}",
            f"{r.total_supply:,.1f}",
            f"{r.total_borrows:,.1f}",
            f"{r.available_liquidity:,.1f}",
            f"{util_pct:.1f}%",
            f"{r.supply_apy * 100:.2f}%",
            f"{r.borrow_apy * 100:.2f}%"
        ])

    col_widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * col_widths[i] for i in range(len(headers)))

    print(header_line)
    print(sep_line)
    for r in rows:
        print(" | ".join(r[i].ljust(col_widths[i]) for i in range(len(r))))

    print(f"\n✓ Market Scan Completed. Estimated Market TVL: ${total_tvl:,.2f}\n")


def cmd_health(args):
    """Print resolved configuration and a JSON health snapshot."""
    warnings = config.validate()
    snapshot = {
        "status": "degraded" if warnings else "ok",
        "rpc_endpoints": config.DEFAULT_RPC_ENDPOINTS,
        "rpc_timeout_seconds": config.RPC_TIMEOUT_SECONDS,
        "rpc_total_budget_seconds": config.RPC_TOTAL_BUDGET_SECONDS,
        "rpc_max_retries": config.RPC_MAX_RETRIES,
        "poll_interval_seconds": config.DEFAULT_POLL_INTERVAL,
        "log_level": config.LOG_LEVEL,
        "telegram_configured": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID),
        "webhook_configured": bool(config.ALERT_WEBHOOK_URL),
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(snapshot, indent=2))
    else:
        print("\n\U0001F3E5 Kamino Sentinel Health\n")
        print(f"  Status            : {snapshot['status']}")
        print(f"  RPC endpoints     : {len(snapshot['rpc_endpoints'])} configured")
        for ep in snapshot["rpc_endpoints"]:
            print(f"      - {ep}")
        print(f"  Request timeout   : {snapshot['rpc_timeout_seconds']}s")
        print(f"  Total budget      : {snapshot['rpc_total_budget_seconds']}s")
        print(f"  Max retries       : {snapshot['rpc_max_retries']}")
        print(f"  Poll interval     : {snapshot['poll_interval_seconds']}s")
        print(f"  Telegram alerts   : {'yes' if snapshot['telegram_configured'] else 'no'}")
        print(f"  Webhook alerts    : {'yes' if snapshot['webhook_configured'] else 'no'}")
        if warnings:
            print("\n  \u26A0 Warnings:")
            for w in warnings:
                print(f"      - {w}")
        print()
    if warnings and args.strict:
        sys.exit(2)


def cmd_market(args):
    sentinel = KaminoSentinel()
    try:
        reserves = sentinel.get_market_overview(
            market_key=args.market,
            allow_demo=getattr(args, "demo", False),
        )
        if not reserves:
            print(
                "\n\u26a0 No live reserve accounts returned.\n"
                "  This usually means the RPC endpoint is rate-limited or unreachable.\n"
                "  Re-run with --demo to view illustrative sample data instead.\n",
                file=sys.stderr,
            )
            sys.exit(1)
        if getattr(args, "demo", False):
            print(
                "\n\u26a0 DEMO MODE: the figures below are illustrative sample data, "
                "NOT live on-chain values.\n",
                file=sys.stderr,
            )
        print_market_table(args.market, reserves)
    except Exception as e:
        print(f"Error fetching market data: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_simulate(args):
    print("\n📊 Kamino Obligation Stress Simulation\n")
    collateral = args.collateral
    borrow = args.borrow
    price_drop = args.price_drop

    current_ob = ObligationMetrics(
        pubkey="SimulatedObligation11111111111111111111111111",
        owner="SimulatedUser11111111111111111111111111111111",
        market="7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF",
        total_collateral_value_usd=collateral,
        total_borrow_value_usd=borrow,
        borrow_limit_usd=round(collateral * 0.75, 2),
        liquidation_threshold_value_usd=round(collateral * 0.80, 2)
    )
    current_ob.calculate_health()

    stressed_collateral = collateral * (1.0 - (price_drop / 100.0))
    stressed_ob = ObligationMetrics(
        pubkey="SimulatedObligation11111111111111111111111111",
        owner="SimulatedUser11111111111111111111111111111111",
        market="7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF",
        total_collateral_value_usd=stressed_collateral,
        total_borrow_value_usd=borrow,
        borrow_limit_usd=round(stressed_collateral * 0.75, 2),
        liquidation_threshold_value_usd=round(stressed_collateral * 0.80, 2)
    )
    stressed_ob.calculate_health()

    print(f"Stress Scenario: Baseline vs -{price_drop:.1f}% Asset Price Shock")
    print("-" * 65)
    print(f"{'Metric':<25} | {'Baseline':<18} | {f'After -{price_drop:.0f}% Shock':<18}")
    print("-" * 65)
    print(f"{'Collateral Value':<25} | ${collateral:<17,.2f} | ${stressed_collateral:<17,.2f}")
    print(f"{'Borrow Value':<25} | ${borrow:<17,.2f} | ${borrow:<17,.2f}")
    print(f"{'Liquidation Limit':<25} | ${current_ob.liquidation_threshold_value_usd:<17,.2f} | ${stressed_ob.liquidation_threshold_value_usd:<17,.2f}")
    print(f"{'Current LTV':<25} | {current_ob.current_ltv * 100:<17.1f}% | {stressed_ob.current_ltv * 100:<17.1f}%")
    print(f"{'Health Factor':<25} | {current_ob.health_factor:<17.3f} | {stressed_ob.health_factor:<17.3f}")
    print(f"{'Risk Status':<25} | {current_ob.risk_level.value:<17} | {stressed_ob.risk_level.value:<17}")
    print("-" * 65)

    if stressed_ob.health_factor < 1.0:
        print("\n⚠️  CRITICAL: Position drops below liquidation threshold (HF < 1.00)! Liquidation will trigger.")
    elif stressed_ob.health_factor <= 1.15:
        print("\n⚡ WARNING: Position enters high liquidation hazard territory (HF <= 1.15). Additional margin required.")
    else:
        print("\n✓ Position maintains adequate solvency margin.")
    print()


def cmd_user(args):
    print(f"\n🔍 Querying Kamino Obligations for: {args.wallet}...\n")
    sentinel = KaminoSentinel()
    obligations = sentinel.get_user_obligations(args.wallet, market_key=args.market)

    if not obligations:
        print(f"No active loan obligations found for wallet {args.wallet} in the Kamino '{args.market}' market.")
        print("This wallet currently has no active borrow positions on-chain.\n")
        return

    for ob in obligations:
        ob.calculate_health()
        print("-" * 50)
        print(f"Obligation Pubkey: {ob.pubkey}")
        print(f"Total Collateral:  ${ob.total_collateral_value_usd:,.2f}")
        print(f"Total Borrows:     ${ob.total_borrow_value_usd:,.2f}")
        print(f"Current LTV:       {ob.current_ltv * 100:.1f}%")
        print(f"Health Factor:     {ob.health_factor:.3f}")
        print(f"Risk Assessment:   {ob.risk_level.value}")
        print("-" * 50)


def main():
    parser = argparse.ArgumentParser(prog="kamino-sentinel", description="Kamino Sentinel: Risk & Yield Telemetry Daemon for Kamino Finance on Solana.")
    parser.add_argument("--log-level", default=None, help="Override log level (DEBUG/INFO/WARNING/ERROR)")
    parser.add_argument("--no-strict", action="store_true", help="Do not exit on configuration warnings")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # market command
    p_market = subparsers.add_parser("market", help="Scan Kamino Lending market reserves and APYs")
    p_market.add_argument("--market", "-m", default="main", choices=["main", "jlp", "altcoins"], help="Lending market")
    p_market.add_argument(
        "--demo",
        action="store_true",
        help="Show illustrative sample data if live RPC returns nothing (NOT live values)",
    )
    p_market.set_defaults(func=cmd_market)

    # simulate command
    p_sim = subparsers.add_parser("simulate", help="Simulate obligation liquidation risk under price drops")
    p_sim.add_argument("--collateral", "-c", type=float, default=10000.0, help="Collateral USD value")
    p_sim.add_argument("--borrow", "-b", type=float, default=6500.0, help="Borrowed USD value")
    p_sim.add_argument("--price-drop", "-p", type=float, default=20.0, help="Simulated price drop percentage")
    p_sim.set_defaults(func=cmd_simulate)

    # user command
    p_user = subparsers.add_parser("user", help="Inspect obligations for a Solana wallet")
    p_user.add_argument("wallet", help="Solana wallet public key")
    p_user.add_argument("--market", "-m", default="main", help="Lending market")
    p_user.set_defaults(func=cmd_user)

    # health command
    p_health = subparsers.add_parser("health", help="Show resolved config and health snapshot")
    p_health.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_health.add_argument("--strict", action="store_true", help="Exit non-zero on config warnings")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    configure_logging(getattr(args, "log_level", None) or config.LOG_LEVEL)

    # `health` reports problems itself; everything else fails fast on bad config.
    if args.command != "health":
        check_config(strict=not getattr(args, "no_strict", False))

    try:
        args.func(args)
    except KeyboardInterrupt:
        logger.info("Interrupted by user; exiting.")
        sys.exit(130)


if __name__ == "__main__":
    main()
