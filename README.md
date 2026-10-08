# Kamino Sentinel

[![CI](https://github.com/chugh-ak5/kamino-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/chugh-ak5/kamino-sentinel/actions/workflows/ci.yml)
[![Solana](https://img.shields.io/badge/Solana-Mainnet--Beta-9945FF?logo=solana&logoColor=white)](https://solana.com)
[![Kamino Finance](https://img.shields.io/badge/Protocol-Kamino%20Lending%20(KLend)-00D2B4)](https://kamino.finance)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

High-frequency monitoring, automated risk telemetry, and **atomic liquidation defense engine** for **Kamino Finance** on Solana.

Designed for quantitative traders, risk officers, and liquidity providers managing high-capital borrow/lend positions. Kamino Sentinel continuously tracks on-chain reserve utilization, interest rate curves, and user obligation health factors with **real-time liquidation pre-emption, multi-scenario stress shock matrices, and zero-capital flash-unwind planning**.

---

## Live Mainnet Verification Status

- **Program ID**: `KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD` (Kamino Lending Program)
- **Account Discriminator**: `8MMas8GHex6` (`sha256("account:Reserve")[:8]`)
- **Main Market Address**: `7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF`
- **Live Discovery**: Verified across **58 on-chain reserve accounts** on Solana Mainnet-Beta via Helius RPC.
- **Unit Tests**: **51 / 51 tests passing** (100% pass rate across liquidation defense math, flash-unwind equations, RPC failover, alert throttling, and obligation parsing).

---

## Architecture Overview

```
                      +-----------------------------+
                      |      Solana RPC Fleet       |
                      |  (Helius / Dedicated / RPC) |
                      +--------------+--------------+
                                     |
                                     v
+-----------------------+     +-------------------------------+
|   Jupiter Price API   | --> |     KaminoSentinel Engine     |
+-----------------------+     |  - KLend Program Accounts     |
                              |  - Reserve Utilization Curves |
                              |  - Obligation Health Modeling |
                              +--------------+----------------+
                                             |
             +-------------------------------+-------------------------------+
             |                                                               |
             v                                                               v
+-----------------------------+                               +-----------------------------+
|  Liquidation Defense Engine |                               |       Alert Dispatcher      |
|  - Liquidation Price Calc   |                               |  - Telegram Alerts + Plan   |
|  - Stress Shock Matrix      |                               |  - Webhook Broadcasts       |
|  - Atomic Flash-Unwind Plan |                               |  - Cooldown Suppression     |
|  - Jito MEV Bundle Route    |                               +-----------------------------+
+--------------+--------------+
               |
               v
+-----------------------------+
|    Interactive Terminal     |
|  - Real-time Market Table   |
|  - Position Inspector       |
|  - Auto-Deleverage Planner  |
+-----------------------------+
```

---

## Core Capabilities

- **Real-Time Reserve Telemetry**: Scans live on-chain Kamino lending reserves (`KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD`), computing exact supply amounts, borrow volumes, available liquidity, and kink-based APY curves.
- **Obligation Health & Liquidation Risk**: Tracks obligation accounts, calculating real-time Loan-to-Value (LTV), Liquidation Thresholds, and Liquidation Distance.
- **Exact Liquidation Price Analytics**: Calculates the critical threshold price ($P_{liq}$) and percentage buffer distance before liquidator auctions can seize collateral.
- **Multi-Scenario Stress Testing Matrix**: Evaluates portfolio health across rapid asset drawdowns (0% down to -50% flash crashes), tracking health factor decay and estimated liquidator penalty haircut.
- **Atomic Auto-Deleveraging & Flash-Unwind Engine**: Solves the simultaneous self-liquidation equation to restore safe Health Factors without requiring spare wallet capital (Flash borrow debt $\rightarrow$ repay obligation $\rightarrow$ withdraw unlocked collateral $\rightarrow$ swap via Jupiter V6 $\rightarrow$ repay flash loan $\rightarrow$ bundle via Jito MEV).
- **Multi-Channel Alert Dispatch**: Triggers instant notifications over Telegram or custom webhooks with actionable auto-deleveraging recommendations attached to Warning and Critical alerts.
- **Multi-Endpoint RPC Resilience**: Automatic failover, rotation, and exponential backoff across RPC endpoints.

---

## Installation

```bash
git clone git@github.com:chugh-ak5/kamino-sentinel.git
cd kamino-sentinel
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## Quickstart & CLI Commands

### 1. Automated Liquidation Defense Planner (`defend`)
Generate a step-by-step deleveraging plan to rescue a stressed obligation to a safe Health Factor (e.g., $HF \ge 1.30$):

```bash
# Self-collateral flash-unwind (zero outside capital required):
kamino-sentinel defend --collateral 10000 --borrow 7500 --target-hf 1.30 --method flash

# External capital repayment:
kamino-sentinel defend --collateral 10000 --borrow 7500 --target-hf 1.30 --method repay

# Live on-chain wallet inspection and defense:
kamino-sentinel defend --wallet <SOLANA_WALLET_ADDRESS> --target-hf 1.25
```

Sample output:
```
🛡️ Automated Deleveraging Defense Plan: Flash-Loan Self Unwind (Zero Capital Required)
-----------------------------------------------------------------
  Target Health Factor     : 1.30
  Current Health Factor    : 1.067
  Debt to Repay            : $3,519.71
  Collateral to Withdraw   : $3,532.03
  Slippage & Routing Fee   : $12.32
  Resulting Health Factor  : 1.300
  Resulting LTV            : 61.5%
  Plan Viability           : ✓ EXECUTABLE
-----------------------------------------------------------------

  Atomic Execution Route:
    [1] Flash Loan Provider (Save / Kamino / Solend): Flash Borrow USDC
        Borrow $3,519.71 of USDC with zero upfront collateral.
    [2] Kamino Lending (KLend): Repay USDC Debt
        Repay obligation debt to unlock collateral margin.
    [3] Kamino Lending (KLend): Withdraw SOL Collateral
        Withdraw $3,532.03 worth of SOL from reserve.
    [4] Jupiter Aggregator V6: Swap SOL -> USDC
        Route swap via Jupiter: convert $3,532.03 of SOL into $3,519.71 of USDC (covering $12.32 slippage/fees).
    [5] Flash Loan Provider: Repay Flash Loan
        Close flash loan within atomic transaction block.
    [6] Jito Block Engine: Submit Atomic MEV Bundle
        Bundle instructions into atomic Jito bundle with tip to guarantee zero sandwiching and priority.
```

---

### 2. Run Volatility Stress Tests & Shock Matrices (`simulate`)
Simulate how flash crashes affect liquidation distance and generate full multi-shock breakdown:

```bash
kamino-sentinel simulate --collateral 10000 --borrow 6500 --price-drop 20 --price 150 --asset SOL
```

Sample output:
```
📊 Kamino Obligation Stress Simulation & Liquidation Defense

Asset Configuration: 66.67 SOL @ $150.00 | Borrow: $6,500.00
Liquidation Threshold Price : $121.87 SOL
Liquidation Buffer Distance : 18.8% price drop

⚡ Multi-Scenario Asset Shock Matrix
Shock | Collateral (USD) | Borrow (USD) | Health Factor | Risk Level   | Est. Penalty
------+------------------+--------------+---------------+--------------+-------------
-0%   | $10,000.00       | $6,500.00    | 1.231         | CAUTION      | -           
-10%  | $9,000.00        | $6,500.00    | 1.108         | WARNING      | -           
-20%  | $8,000.00        | $6,500.00    | 0.985         | LIQUIDATABLE | $520.00     
-30%  | $7,000.00        | $6,500.00    | 0.862         | LIQUIDATABLE | $520.00     
-40%  | $6,000.00        | $6,500.00    | 0.738         | LIQUIDATABLE | $520.00     
-50%  | $5,000.00        | $6,500.00    | 0.615         | LIQUIDATABLE | $520.00     
```

---

### 3. Scan Kamino Lending Markets (`market`)
Display live reserve liquidity, utilization rates, and APYs across major Solana pools:

```bash
export SOLANA_RPC_URL="https://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY"

kamino-sentinel market --market main
```

---

### 4. Inspect a Wallet's Obligations (`user`)
Check any Solana wallet address for active borrow/lend positions and recommended remediation:

```bash
kamino-sentinel user <SOLANA_WALLET_ADDRESS>
```

---

### 5. Configuration Health Check (`health`)
Validate RPC connectivity, failover endpoints, and alert channels:

```bash
kamino-sentinel health
```

---

## Quantitative Risk Math & Formulas

### 1. Health Factor ($HF$)
$$\text{HF} = \frac{\sum (\text{Collateral Value}_i \times \text{Liquidation Threshold}_i)}{\sum \text{Borrow Value}_j}$$

- **$HF > 1.25$**: **SAFE** (Sufficient solvency buffer)
- **$1.15 < HF \le 1.25$**: **CAUTION** (Monitor asset volatility)
- **$1.05 < HF \le 1.15$**: **WARNING** (High risk of liquidation call)
- **$1.00 < HF \le 1.05$**: **CRITICAL** (Imminent liquidation risk)
- **$HF < 1.00$**: **LIQUIDATABLE** (Eligible for liquidator seizure)

### 2. Liquidation Price ($P_{liq}$) & Buffer Distance
For collateral quantity $Q_{coll}$ with aggregate liquidation threshold $LT$ and total debt $D$:
$$P_{liq} = \frac{D}{Q_{coll} \times LT}$$
$$\text{Buffer Distance (\%)} = \frac{P_{current} - P_{liq}}{P_{current}} \times 100\%$$

### 3. Self-Collateral Flash-Unwind Simultaneous Equation
When deleveraging with **zero external capital**, the user borrows $x$ debt token via flash loan, repays debt, withdraws $y = x \cdot (1 + s)$ collateral (where $s$ is DEX swap fee + slippage), and sells it via Jupiter Aggregator:

$$\text{HF}_{\text{target}} = \frac{(C - x(1 + s)) \times LT}{D - x}$$

Solving algebraically for required flash repayment $x$:
$$x = \frac{\text{HF}_{\text{target}} \cdot D - C \cdot LT}{\text{HF}_{\text{target}} - (1 + s) \cdot LT}$$

---

## Development & Testing

Run the full unit test suite:
```bash
pytest tests/ -v
```

```
============================== 51 passed in 11.27s ===============================
```

---

## Support & Tips ☕

Buy me a coffee if this saved your collateral:

- **SOL / USDC (Solana):** `7SxuVBaBArDXuKuvCaECB3arkmVN1NGxhSDA2BFZazJH`

---

## Author

**Showrojeet Chugh**  
- GitHub: [@chugh-ak5](https://github.com/chugh-ak5)  
- Focus: Quantitative Trading, Execution Engineering & DeFi Risk Infrastructure


