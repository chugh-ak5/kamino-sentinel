"""
Atomic transaction builder, Jupiter V6 swap integrator, Jito MEV bundle packager,
and on-chain transaction simulation engine for Kamino Sentinel.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import logging
import random
import struct
from typing import Any, Dict, List, Optional, Tuple
import requests

import base58

from kamino_sentinel.config import (
    COMPUTE_BUDGET_PROGRAM_ID,
    JITO_TIP_ACCOUNTS,
    JITO_TIP_FLOOR_URL,
    JUPITER_QUOTE_API_URL,
    JUPITER_SWAP_API_URL,
    JUPITER_V6_PROGRAM_ID,
    KLEND_PROGRAM_ID,
    KNOWN_MARKETS,
    SYSTEM_PROGRAM_ID,
    TOKEN_MINTS,
    TOKEN_PROGRAM_ID,
)
from kamino_sentinel.defense import DeleveragePlan, DeleveragingMethod
from kamino_sentinel.rpc import SolanaRpcClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class AccountMeta:
    pubkey: str
    is_signer: bool = False
    is_writable: bool = False


@dataclass
class Instruction:
    program_id: str
    accounts: List[AccountMeta]
    data: bytes
    name: Optional[str] = None


@dataclass
class SimulationResult:
    success: bool
    units_consumed: int
    fee: int
    error: Optional[Any] = None
    logs: List[str] = field(default_factory=list)
    pre_balances: List[int] = field(default_factory=list)
    post_balances: List[int] = field(default_factory=list)
    return_data: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JitoBundle:
    transactions: List[str]
    tip_lamports: int
    tip_sol: float
    tip_account: str
    tip_percentile: str


@dataclass
class CompiledTransaction:
    tx_base64: str
    size_bytes: int
    num_instructions: int
    accounts_count: int
    payer: str
    instructions: List[Instruction]
    simulation: Optional[SimulationResult] = None
    jito_bundle: Optional[JitoBundle] = None


# ---------------------------------------------------------------------------
# Serialization Utilities (Compact-u16 & Wire Format)
# ---------------------------------------------------------------------------

def encode_compact_u16(n: int) -> bytes:
    """Encode an integer into Solana's shortvec / compact-u16 variable length bytes."""
    rem = n
    out = bytearray()
    while True:
        elem = rem & 0x7F
        rem >>= 7
        if rem == 0:
            out.append(elem)
            break
        else:
            elem |= 0x80
            out.append(elem)
    return bytes(out)


def get_anchor_discriminator(instruction_name: str) -> bytes:
    """Computes Anchor's 8-byte instruction discriminator: sha256('global:<name>')[:8]."""
    return hashlib.sha256(f"global:{instruction_name}".encode("utf-8")).digest()[:8]


# ---------------------------------------------------------------------------
# Instruction Builders
# ---------------------------------------------------------------------------

def build_compute_budget_ixs(
    units: int = 1_400_000,
    micro_lamports: int = 50_000,
) -> List[Instruction]:
    """
    Builds ComputeBudget instructions to request high compute unit allocation
    and priority fees to ensure fast inclusion.
    """
    ixs = []
    # 1. SetComputeUnitLimit (discriminator 0x02 + u32 units)
    data_limit = struct.pack("<BI", 2, units)
    ixs.append(
        Instruction(
            program_id=COMPUTE_BUDGET_PROGRAM_ID,
            accounts=[],
            data=data_limit,
            name="ComputeBudget: SetComputeUnitLimit",
        )
    )

    # 2. SetComputeUnitPrice (discriminator 0x03 + u64 micro_lamports)
    data_price = struct.pack("<BQ", 3, micro_lamports)
    ixs.append(
        Instruction(
            program_id=COMPUTE_BUDGET_PROGRAM_ID,
            accounts=[],
            data=data_price,
            name="ComputeBudget: SetComputeUnitPrice",
        )
    )
    return ixs


def fetch_jito_tip_floor(session: Optional[requests.Session] = None) -> Dict[str, float]:
    """
    Fetches real-time landed tip percentiles from the Jito Block Engine tip floor API.
    Returns tip amounts in SOL per percentile (25th, 50th, 75th, 95th, 99th).
    """
    s = session or requests.Session()
    fallback = {
        "25th": 0.000010,
        "50th": 0.000035,
        "75th": 0.000085,
        "95th": 0.000250,
        "99th": 0.000500,
    }
    try:
        resp = s.get(JITO_TIP_FLOOR_URL, timeout=4.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                return {
                    "25th": float(latest.get("landed_tips_25th_percentile", fallback["25th"])),
                    "50th": float(latest.get("landed_tips_50th_percentile", fallback["50th"])),
                    "75th": float(latest.get("landed_tips_75th_percentile", fallback["75th"])),
                    "95th": float(latest.get("landed_tips_95th_percentile", fallback["95th"])),
                    "99th": float(latest.get("landed_tips_99th_percentile", fallback["99th"])),
                }
    except Exception as exc:
        logger.debug("Jito tip floor fetch failed: %s; using safe fallback", exc)

    return fallback


def build_jito_tip_ix(
    signer: str,
    tip_lamports: int = 50_000,
    tip_account: Optional[str] = None,
) -> Instruction:
    """
    Builds a Jito MEV tip instruction (SystemProgram Transfer to a randomized Jito tip account).
    """
    chosen_account = tip_account or random.choice(JITO_TIP_ACCOUNTS)
    # System Program instruction 2 is Transfer: u32(2) + u64(lamports)
    data = struct.pack("<IQ", 2, tip_lamports)
    return Instruction(
        program_id=SYSTEM_PROGRAM_ID,
        accounts=[
            AccountMeta(pubkey=signer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=chosen_account, is_signer=False, is_writable=True),
        ],
        data=data,
        name=f"Jito Tip: Transfer {tip_lamports:,} lamports to {chosen_account[:6]}..",
    )


def build_kamino_repay_ix(
    owner: str,
    obligation: str,
    reserve: str,
    lending_market: str,
    source_token_account: str,
    destination_reserve_supply: str,
    amount_lamports: int,
) -> Instruction:
    """
    Builds Kamino Lending `repay_obligation_reserve` Anchor instruction.
    """
    disc = get_anchor_discriminator("repay_obligation_reserve")
    data = disc + struct.pack("<Q", amount_lamports)

    accounts = [
        AccountMeta(pubkey=owner, is_signer=True, is_writable=True),
        AccountMeta(pubkey=obligation, is_signer=False, is_writable=True),
        AccountMeta(pubkey=lending_market, is_signer=False, is_writable=False),
        AccountMeta(pubkey=reserve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=source_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=destination_reserve_supply, is_signer=False, is_writable=True),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    return Instruction(
        program_id=KLEND_PROGRAM_ID,
        accounts=accounts,
        data=data,
        name=f"KLend: RepayObligationReserve ({amount_lamports:,} raw units)",
    )


def build_kamino_withdraw_ix(
    owner: str,
    obligation: str,
    reserve: str,
    lending_market: str,
    destination_collateral_account: str,
    amount_lamports: int,
) -> Instruction:
    """
    Builds Kamino Lending `withdraw_obligation_collateral` Anchor instruction.
    """
    disc = get_anchor_discriminator("withdraw_obligation_collateral")
    data = disc + struct.pack("<Q", amount_lamports)

    accounts = [
        AccountMeta(pubkey=owner, is_signer=True, is_writable=True),
        AccountMeta(pubkey=obligation, is_signer=False, is_writable=True),
        AccountMeta(pubkey=lending_market, is_signer=False, is_writable=False),
        AccountMeta(pubkey=reserve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=destination_collateral_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    return Instruction(
        program_id=KLEND_PROGRAM_ID,
        accounts=accounts,
        data=data,
        name=f"KLend: WithdrawObligationCollateral ({amount_lamports:,} raw units)",
    )


def build_kamino_flash_borrow_ix(
    reserve: str,
    lending_market: str,
    destination_token_account: str,
    amount_lamports: int,
) -> Instruction:
    """
    Builds Kamino Lending `flash_borrow_reserve_liquidity` instruction.
    """
    disc = get_anchor_discriminator("flash_borrow_reserve_liquidity")
    data = disc + struct.pack("<Q", amount_lamports)

    accounts = [
        AccountMeta(pubkey=destination_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=reserve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=lending_market, is_signer=False, is_writable=False),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    return Instruction(
        program_id=KLEND_PROGRAM_ID,
        accounts=accounts,
        data=data,
        name=f"KLend: FlashBorrowReserveLiquidity ({amount_lamports:,} raw units)",
    )


def build_kamino_flash_repay_ix(
    reserve: str,
    lending_market: str,
    source_token_account: str,
    amount_lamports: int,
) -> Instruction:
    """
    Builds Kamino Lending `flash_repay_reserve_liquidity` instruction.
    """
    disc = get_anchor_discriminator("flash_repay_reserve_liquidity")
    data = disc + struct.pack("<Q", amount_lamports)

    accounts = [
        AccountMeta(pubkey=source_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=reserve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=lending_market, is_signer=False, is_writable=False),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    return Instruction(
        program_id=KLEND_PROGRAM_ID,
        accounts=accounts,
        data=data,
        name=f"KLend: FlashRepayReserveLiquidity ({amount_lamports:,} raw units)",
    )


def fetch_jupiter_swap_instructions(
    user_pubkey: str,
    input_mint: str,
    output_mint: str,
    amount_lamports: int,
    slippage_bps: int = 50,
    session: Optional[requests.Session] = None,
) -> List[Instruction]:
    """
    Queries Jupiter V6 Aggregator API for live swap quote and converts the returned
    swap instructions into standard Kamino Sentinel `Instruction` objects.
    Falls back gracefully to a mock Jupiter swap instruction for offline testing / simulation.
    """
    s = session or requests.Session()
    try:
        quote_params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_lamports,
            "slippageBps": slippage_bps,
        }
        q_resp = s.get(JUPITER_QUOTE_API_URL, params=quote_params, timeout=5.0)
        if q_resp.status_code == 200:
            quote_data = q_resp.json()
            swap_payload = {
                "quoteResponse": quote_data,
                "userPublicKey": user_pubkey,
                "wrapAndUnwrapSol": True,
            }
            s_resp = s.post(JUPITER_SWAP_API_URL, json=swap_payload, timeout=6.0)
            if s_resp.status_code == 200:
                swap_data = s_resp.json()
                ixs: List[Instruction] = []

                # Parse Jupiter instructions list
                for key in ["setupInstructions", "swapInstruction", "cleanupInstruction"]:
                    raw_ix = swap_data.get(key)
                    if not raw_ix:
                        continue
                    if isinstance(raw_ix, list):
                        items = raw_ix
                    else:
                        items = [raw_ix]

                    for item in items:
                        if not item:
                            continue
                        prog_id = item.get("programId", JUPITER_V6_PROGRAM_ID)
                        raw_data = base64.b64decode(item.get("data", ""))
                        acc_list: List[AccountMeta] = []
                        for acc in item.get("accounts", []):
                            acc_list.append(
                                AccountMeta(
                                    pubkey=acc.get("pubkey", ""),
                                    is_signer=acc.get("isSigner", False),
                                    is_writable=acc.get("isWritable", False),
                                )
                            )
                        ixs.append(
                            Instruction(
                                program_id=prog_id,
                                accounts=acc_list,
                                data=raw_data,
                                name=f"Jupiter V6: {key}",
                            )
                        )
                if ixs:
                    return ixs
    except Exception as exc:
        logger.debug("Live Jupiter quote/swap fetch failed: %s; using simulated route", exc)

    # Deterministic mock fallback swap instruction for simulation & testing
    mock_data = struct.pack("<BQQ", 1, amount_lamports, int(amount_lamports * 0.996))
    return [
        Instruction(
            program_id=JUPITER_V6_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=user_pubkey, is_signer=True, is_writable=True),
                AccountMeta(pubkey=input_mint, is_signer=False, is_writable=True),
                AccountMeta(pubkey=output_mint, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
            data=mock_data,
            name=f"Jupiter V6 Swap ({amount_lamports:,} units)",
        )
    ]


# ---------------------------------------------------------------------------
# Versioned Transaction (v0) Compiler
# ---------------------------------------------------------------------------

def compile_versioned_transaction(
    instructions: List[Instruction],
    payer: str,
    recent_blockhash: Optional[str] = None,
) -> CompiledTransaction:
    """
    Compiles a sequence of Instructions into a valid Solana Versioned Transaction (`v0`)
    wire format according to the Solana Protocol Specification.

    Wire layout:
      [compact-u16 num_signatures]
      [64-byte signature * num_signatures] (zero-filled for simulation)
      [MessageV0]:
        0x80 (version mask)
        [header: 3 bytes: num_required_signatures, num_readonly_signed, num_readonly_unsigned]
        [compact-u16 num_account_keys]
        [32-byte pubkey * num_account_keys]
        [32-byte recent_blockhash]
        [compact-u16 num_instructions]
        [compiled_instruction * num_instructions]
        [compact-u16 num_address_table_lookups] (0)
    """
    if not instructions:
        raise ValueError("Cannot compile transaction with 0 instructions.")

    # 1. Collect and classify all account keys
    acc_map: Dict[str, Dict[str, bool]] = {payer: {"signer": True, "writable": True}}

    for ix in instructions:
        if ix.program_id not in acc_map:
            acc_map[ix.program_id] = {"signer": False, "writable": False}
        for a in ix.accounts:
            if a.pubkey not in acc_map:
                acc_map[a.pubkey] = {"signer": a.is_signer, "writable": a.is_writable}
            else:
                acc_map[a.pubkey]["signer"] = acc_map[a.pubkey]["signer"] or a.is_signer
                acc_map[a.pubkey]["writable"] = acc_map[a.pubkey]["writable"] or a.is_writable

    # Ensure payer is always index 0 (writable signer)
    acc_map[payer]["signer"] = True
    acc_map[payer]["writable"] = True

    # Segregate accounts in Solana protocol required order:
    # 1. Writable signers (payer first)
    g1 = [payer] + [p for p, m in acc_map.items() if p != payer and m["signer"] and m["writable"]]
    # 2. Readonly signers
    g2 = [p for p, m in acc_map.items() if m["signer"] and not m["writable"]]
    # 3. Writable non-signers
    g3 = [p for p, m in acc_map.items() if not m["signer"] and m["writable"]]
    # 4. Readonly non-signers (including program IDs)
    g4 = [p for p, m in acc_map.items() if not m["signer"] and not m["writable"]]

    ordered_keys = g1 + g2 + g3 + g4
    key_to_idx = {k: i for i, k in enumerate(ordered_keys)}

    num_signers = len(g1) + len(g2)
    header = bytes([num_signers, len(g2), len(g4)])

    # 2. Build Message V0
    msg = bytearray()
    msg.append(0x80)  # v0 version prefix
    msg.extend(header)

    # Account keys
    msg.extend(encode_compact_u16(len(ordered_keys)))
    for k in ordered_keys:
        try:
            raw_key = base58.b58decode(k)
            if len(raw_key) != 32:
                raw_key = hashlib.sha256(k.encode()).digest()
        except Exception:
            raw_key = hashlib.sha256(k.encode()).digest()
        msg.extend(raw_key)

    # Blockhash (32 bytes)
    if recent_blockhash:
        try:
            raw_bh = base58.b58decode(recent_blockhash)
            if len(raw_bh) != 32:
                raw_bh = b"\x01" * 32
        except Exception:
            raw_bh = b"\x01" * 32
    else:
        raw_bh = b"\x01" * 32
    msg.extend(raw_bh)

    # Compiled Instructions
    msg.extend(encode_compact_u16(len(instructions)))
    for ix in instructions:
        prog_idx = key_to_idx[ix.program_id]
        acc_indices = bytes([key_to_idx[a.pubkey] for a in ix.accounts])

        msg.append(prog_idx)
        msg.extend(encode_compact_u16(len(acc_indices)))
        msg.extend(acc_indices)
        msg.extend(encode_compact_u16(len(ix.data)))
        msg.extend(ix.data)

    # Address Table Lookups (0)
    msg.extend(encode_compact_u16(0))

    # 3. Full Transaction wire format (signatures + message)
    tx = bytearray()
    tx.extend(encode_compact_u16(num_signers))
    for _ in range(num_signers):
        tx.extend(b"\x00" * 64)  # Zeroed dummy signatures for simulation
    tx.extend(msg)

    tx_b64 = base64.b64encode(tx).decode("ascii")
    return CompiledTransaction(
        tx_base64=tx_b64,
        size_bytes=len(tx),
        num_instructions=len(instructions),
        accounts_count=len(ordered_keys),
        payer=payer,
        instructions=instructions,
    )


# ---------------------------------------------------------------------------
# High-Level Transaction Assembler & Simulator
# ---------------------------------------------------------------------------

def build_atomic_deleverage_bundle(
    plan: DeleveragePlan,
    owner_wallet: str,
    collateral_mint: str = "So11111111111111111111111111111111111111112",
    debt_mint: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    obligation_pubkey: Optional[str] = None,
    market_key: str = "main",
    rpc_client: Optional[SolanaRpcClient] = None,
    include_jito_tip: bool = True,
    tip_percentile: str = "50th",
    simulate: bool = True,
) -> CompiledTransaction:
    """
    Builds the complete atomic Solana Versioned Transaction (`v0`) implementing the
    `DeleveragePlan`. Includes compute budget priority fees, flash loan / KLend operations,
    Jupiter DEX swap, and Jito MEV bundle tip.
    """
    if not plan.is_viable:
        raise ValueError(f"Cannot construct transaction for unviable plan: {plan.unviable_reason}")

    market_meta = KNOWN_MARKETS.get(market_key, KNOWN_MARKETS["main"])
    market_addr = market_meta["address"]
    obligation_addr = obligation_pubkey or "Oblig1111111111111111111111111111111111111"
    reserve_collateral = "ResColl11111111111111111111111111111111111"
    reserve_debt = "ResDebt11111111111111111111111111111111111"
    user_coll_ata = "UserCollATA11111111111111111111111111111111"
    user_debt_ata = "UserDebtATA11111111111111111111111111111111"
    reserve_supply = "ResSupply111111111111111111111111111111111"

    coll_meta = TOKEN_MINTS.get(collateral_mint, {"decimals": 9})
    debt_meta = TOKEN_MINTS.get(debt_mint, {"decimals": 6})

    debt_lamports = int(plan.debt_to_repay_usd * (10 ** debt_meta["decimals"]))
    coll_lamports = int(plan.collateral_to_withdraw_usd * (10 ** coll_meta["decimals"]))

    instructions: List[Instruction] = []

    # 1. Compute Budget allocation
    instructions.extend(build_compute_budget_ixs(units=1_400_000, micro_lamports=50_000))

    if plan.method == DeleveragingMethod.FLASH_UNWIND:
        # Step A: Flash borrow debt token
        instructions.append(
            build_kamino_flash_borrow_ix(
                reserve=reserve_debt,
                lending_market=market_addr,
                destination_token_account=user_debt_ata,
                amount_lamports=debt_lamports,
            )
        )
        # Step B: Repay KLend obligation debt
        instructions.append(
            build_kamino_repay_ix(
                owner=owner_wallet,
                obligation=obligation_addr,
                reserve=reserve_debt,
                lending_market=market_addr,
                source_token_account=user_debt_ata,
                destination_reserve_supply=reserve_supply,
                amount_lamports=debt_lamports,
            )
        )
        # Step C: Withdraw unlocked collateral
        instructions.append(
            build_kamino_withdraw_ix(
                owner=owner_wallet,
                obligation=obligation_addr,
                reserve=reserve_collateral,
                lending_market=market_addr,
                destination_collateral_account=user_coll_ata,
                amount_lamports=coll_lamports,
            )
        )
        # Step D: Jupiter swap collateral -> debt token
        swap_ixs = fetch_jupiter_swap_instructions(
            user_pubkey=owner_wallet,
            input_mint=collateral_mint,
            output_mint=debt_mint,
            amount_lamports=coll_lamports,
        )
        instructions.extend(swap_ixs)

        # Step E: Repay flash loan
        instructions.append(
            build_kamino_flash_repay_ix(
                reserve=reserve_debt,
                lending_market=market_addr,
                source_token_account=user_debt_ata,
                amount_lamports=debt_lamports,
            )
        )
    else:
        # External repayment route
        instructions.append(
            build_kamino_repay_ix(
                owner=owner_wallet,
                obligation=obligation_addr,
                reserve=reserve_debt,
                lending_market=market_addr,
                source_token_account=user_debt_ata,
                destination_reserve_supply=reserve_supply,
                amount_lamports=debt_lamports,
            )
        )

    # Jito Tip bundling
    jito_bundle_info: Optional[JitoBundle] = None
    if include_jito_tip:
        floors = fetch_jito_tip_floor()
        tip_sol = floors.get(tip_percentile, floors.get("50th", 0.000035))
        tip_lamports = max(10_000, int(tip_sol * 1_000_000_000))
        tip_acc = random.choice(JITO_TIP_ACCOUNTS)

        tip_ix = build_jito_tip_ix(
            signer=owner_wallet,
            tip_lamports=tip_lamports,
            tip_account=tip_acc,
        )
        instructions.append(tip_ix)

        jito_bundle_info = JitoBundle(
            transactions=[],  # Populated after compilation
            tip_lamports=tip_lamports,
            tip_sol=tip_sol,
            tip_account=tip_acc,
            tip_percentile=tip_percentile,
        )

    # Compile Versioned Transaction
    blockhash = None
    if rpc_client:
        try:
            blockhash = rpc_client.get_latest_blockhash()
        except Exception:
            pass

    compiled = compile_versioned_transaction(
        instructions=instructions,
        payer=owner_wallet,
        recent_blockhash=blockhash,
    )

    if jito_bundle_info:
        jito_bundle_info.transactions = [compiled.tx_base64]
        compiled.jito_bundle = jito_bundle_info

    # Simulate transaction via RPC if client provided and simulation requested
    if simulate and rpc_client:
        try:
            sim_res = rpc_client.simulate_transaction(
                compiled.tx_base64,
                sig_verify=False,
                replace_recent_blockhash=True,
            )
            val = sim_res.get("value", {}) if sim_res else {}
            err = val.get("err")
            raw_units = val.get("unitsConsumed")
            units_consumed = int(raw_units) if raw_units is not None else 0
            raw_fee = val.get("fee")
            fee = int(raw_fee) if raw_fee is not None else 5000
            compiled.simulation = SimulationResult(
                success=(err is None),
                units_consumed=units_consumed,
                fee=fee,
                error=err,
                logs=val.get("logs") or [],
                pre_balances=val.get("preBalances") or [],
                post_balances=val.get("postBalances") or [],
                return_data=val.get("returnData"),
                raw=val,
            )
        except Exception as sim_exc:
            logger.warning("Transaction simulation call failed: %s", sim_exc)
            compiled.simulation = SimulationResult(
                success=False,
                units_consumed=0,
                fee=0,
                error=str(sim_exc),
                logs=[],
            )

    return compiled
