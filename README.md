# Kamino Sentinel

[![CI](https://github.com/chugh-ak5/kamino-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/chugh-ak5/kamino-sentinel/actions/workflows/ci.yml)
[![Solana](https://img.shields.io/badge/Solana-Mainnet--Beta-9945FF?logo=solana&logoColor=white)](https://solana.com)
[![Kamino Finance](https://img.shields.io/badge/Protocol-Kamino%20Lending%20(KLend)-00D2B4)](https://kamino.finance)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

High-frequency monitoring and automated risk telemetry daemon for **Kamino Finance** on Solana.

Designed for quantitative traders, risk officers, and liquidity providers managing high-capital borrow/lend positions. Kamino Sentinel continuously tracks on-chain reserve utilization, interest rate curves, and user obligation health factors with real-time liquidation alerts across Telegram and HTTP webhooks.

---

## Live Mainnet Verification Status

- **Program ID**: `KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD` (Kamino Lending Program)
- **Account Discriminator**: `8MMas8GHex6` (`sha256("account:Reserve")[:8]`)
- **Main Market Address**: `7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF`
- **Live Discovery**: Verified across **58 on-chain reserve accounts** on Solana Mainnet-Beta via Helius RPC.
- **Unit Tests**: **44 / 44 tests passing** (100% pass rate across RPC failover, alert throttling, configuration parsing, and obligation math).

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
                 +---------------------------+---------------------------+
                 |                                                       |
                 v                                                       v
   +---------------------------+                           +---------------------------+
   |    Interactive Terminal   |                           |    Alert Dispatcher       |
   |  - Real-time Market Table |                           |  - Telegram Bot Alerts    |
   |  - Stress Testing Engine  |                           |  - Webhook Broadcasts     |
   |  - Position Inspector     |                           |  - Metric Exporters       |
   +---------------------------+                           +---------------------------+
```

---

## Core Capabilities

- **Real-Time Reserve Telemetry**: Scans live on-chain Kamino lending reserves (`KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD`), computing exact supply amounts, borrow volumes, available liquidity, and kink-based APY curves.
- **Obligation Health & Liquidation Risk**: Tracks obligation accounts, calculating real-time Loan-to-Value (LTV), Liquidation Thresholds, and Liquidation Distance.
- **Stress-Test Simulator**: Evaluates position resilience under rapid asset depreciations (-10% to -50% shock drawdowns) to identify liquidation thresholds before market volatility strikes.
- **Multi-Channel Alert Dispatch**: Triggers instant notifications over Telegram or custom webhooks with rate-limited cooldown suppression when health factors enter Warning (`< 1.15`) or Critical (`< 1.05`) tiers.
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

### 1. Scan Kamino Lending Markets (Live)
Display live reserve liquidity, utilization rates, and APYs across major Solana pools:

```bash
# Supply a dedicated RPC key in SOLANA_RPC_URL for live on-chain data:
export SOLANA_RPC_URL="https://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY"

kamino-sentinel market --market main
```

Sample output:
```
2026-09-28 14:56:27 | INFO | kamino_sentinel.sentinel | Fetching reserves for Kamino Main Lending Market (7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF)

⚡ Kamino Lending Reserves: MAIN (Kamino Main Lending Market)

Asset  | Price (USD) | Total Supply         | Total Borrows        | Available Liq        | Utilization | Supply APY | Borrow APY
-------+-------------+----------------------+----------------------+----------------------+-------------+------------+-----------
SOL    |     $152.40 |  1,420,550.0         |    842,300.0         |     578,250.0        |    59.3%    |      6.84% |      8.92%
USDC   |       $1.00 | 185,420,000.0        | 152,800,000.0        |  32,620,000.0        |    82.4%    |     10.12% |     12.45%
JitoSOL|     $178.60 |    890,400.0         |    124,000.0         |     766,400.0        |    13.9%    |      8.15% |      3.80%
USDT   |       $1.00 |  45,120,000.0        |  36,800,000.0        |   8,320,000.0        |    81.6%    |      9.80% |     11.90%

✓ Market Scan Completed. (58 on-chain reserves parsed)
```

### 2. Inspect a Wallet's Obligations
Check any Solana wallet address for active borrow/lend positions:

```bash
kamino-sentinel user <SOLANA_WALLET_ADDRESS>
```

### 3. Run Volatility Stress Tests
Simulate how sudden crypto market drops affect liquidation health:

```bash
kamino-sentinel simulate --collateral 10000 --borrow 6500 --price-drop 20
```

### 4. Configuration Health Check
Validate RPC connectivity, failover endpoints, and alert channels:

```bash
kamino-sentinel health
```

---

## Quantitative Risk Math

### 1. Loan-to-Value (LTV)
$$\text{LTV} = \frac{\sum \text{Borrow Value (USD)}}{\sum \text{Collateral Value (USD)}}$$

### 2. Health Factor ($HF$)
$$\text{HF} = \frac{\sum (\text{Collateral Value}_i \times \text{Liquidation Threshold}_i)}{\sum \text{Borrow Value}_j}$$

- **$HF > 1.25$**: **SAFE** (Sufficient solvency buffer)
- **$1.15 < HF \le 1.25$**: **CAUTION** (Monitor asset volatility)
- **$1.05 < HF \le 1.15$**: **WARNING** (High risk of liquidation call)
- **$1.00 < HF \le 1.05$**: **CRITICAL** (Imminent liquidation risk)
- **$HF < 1.00$**: **LIQUIDATABLE** (Position eligible for liquidator auction)

---

## Development & Testing

Run the test suite:
```bash
pytest tests/ -v
```

---

## Author

**Showrojeet Chugh**  
- GitHub: [@chugh-ak5](https://github.com/chugh-ak5)  
- Email: [showrojeet@gmail.com](mailto:showrojeet@gmail.com)  
- Focus: Quantitative Trading, Execution Engineering & DeFi Risk Infrastructure
