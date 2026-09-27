"""
CLI Entrypoint for Kamino Sentinel with Zero-Dependency Fallback.
"""

import argparse
import sys
from kamino_sentinel.config import KNOWN_MARKETS
from kamino_sentinel.models import ObligationMetrics, RiskLevel
from kamino_sentinel.sentinel import KaminoSentinel


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


def cmd_market(args):
    sentinel = KaminoSentinel()
    try:
        reserves = sentinel.get_market_overview(market_key=args.market)
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
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # market command
    p_market = subparsers.add_parser("market", help="Scan Kamino Lending market reserves and APYs")
    p_market.add_argument("--market", "-m", default="main", choices=["main", "jlp", "altcoins"], help="Lending market")
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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
