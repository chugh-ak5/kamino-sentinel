"""
Unit and integration tests for Kamino Sentinel On-Chain Versioned Transaction (v0) builder,
Anchor discriminators, Jito MEV bundle packager, Jupiter V6 swap routing, and RPC simulation.
"""

import base64
import hashlib
import struct
import unittest
from unittest.mock import MagicMock, patch

import base58

from kamino_sentinel.builder import (
    AccountMeta,
    CompiledTransaction,
    Instruction,
    JitoBundle,
    SimulationResult,
    build_atomic_deleverage_bundle,
    build_compute_budget_ixs,
    build_jito_tip_ix,
    build_kamino_flash_borrow_ix,
    build_kamino_flash_repay_ix,
    build_kamino_repay_ix,
    build_kamino_withdraw_ix,
    compile_versioned_transaction,
    encode_compact_u16,
    fetch_jito_tip_floor,
    fetch_jupiter_swap_instructions,
    get_anchor_discriminator,
)
from kamino_sentinel.config import (
    COMPUTE_BUDGET_PROGRAM_ID,
    JITO_TIP_ACCOUNTS,
    JUPITER_V6_PROGRAM_ID,
    KLEND_PROGRAM_ID,
    SYSTEM_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
)
from kamino_sentinel.defense import DeleveragePlan, DeleveragingMethod, LiquidationDefenseEngine


class TestCompactU16(unittest.TestCase):
    def test_encode_compact_u16_edge_cases(self):
        # 0 -> 0x00
        self.assertEqual(encode_compact_u16(0), b"\x00")
        # 1 -> 0x01
        self.assertEqual(encode_compact_u16(1), b"\x01")
        # 127 -> 0x7f
        self.assertEqual(encode_compact_u16(127), b"\x7f")
        # 128 -> 0x80, 0x01
        self.assertEqual(encode_compact_u16(128), b"\x80\x01")
        # 255 -> 0xff, 0x01
        self.assertEqual(encode_compact_u16(255), b"\xff\x01")
        # 16383 -> 0xff, 0x7f
        self.assertEqual(encode_compact_u16(16383), b"\xff\x7f")
        # 16384 -> 0x80, 0x80, 0x01
        self.assertEqual(encode_compact_u16(16384), b"\x80\x80\x01")


class TestAnchorDiscriminators(unittest.TestCase):
    def test_anchor_discriminator_calculation(self):
        # Anchor discriminator is sha256("global:<name>")[:8]
        expected_repay = hashlib.sha256(b"global:repay_obligation_reserve").digest()[:8]
        self.assertEqual(get_anchor_discriminator("repay_obligation_reserve"), expected_repay)
        self.assertEqual(len(expected_repay), 8)

        expected_borrow = hashlib.sha256(b"global:flash_borrow_reserve_liquidity").digest()[:8]
        self.assertEqual(get_anchor_discriminator("flash_borrow_reserve_liquidity"), expected_borrow)


class TestInstructionBuilders(unittest.TestCase):
    def test_build_compute_budget_ixs(self):
        ixs = build_compute_budget_ixs(units=1_200_000, micro_lamports=75_000)
        self.assertEqual(len(ixs), 2)

        # SetComputeUnitLimit: tag=2, u32=1_200_000
        self.assertEqual(ixs[0].program_id, COMPUTE_BUDGET_PROGRAM_ID)
        tag, units = struct.unpack("<BI", ixs[0].data)
        self.assertEqual(tag, 2)
        self.assertEqual(units, 1_200_000)

        # SetComputeUnitPrice: tag=3, u64=75_000
        self.assertEqual(ixs[1].program_id, COMPUTE_BUDGET_PROGRAM_ID)
        tag, price = struct.unpack("<BQ", ixs[1].data)
        self.assertEqual(tag, 3)
        self.assertEqual(price, 75_000)

    def test_build_jito_tip_ix(self):
        signer = "Signer11111111111111111111111111111111111"
        chosen_acc = JITO_TIP_ACCOUNTS[0]
        ix = build_jito_tip_ix(signer=signer, tip_lamports=25_000, tip_account=chosen_acc)

        self.assertEqual(ix.program_id, SYSTEM_PROGRAM_ID)
        self.assertEqual(len(ix.accounts), 2)
        self.assertTrue(ix.accounts[0].is_signer)
        self.assertTrue(ix.accounts[0].is_writable)
        self.assertEqual(ix.accounts[0].pubkey, signer)

        self.assertFalse(ix.accounts[1].is_signer)
        self.assertTrue(ix.accounts[1].is_writable)
        self.assertEqual(ix.accounts[1].pubkey, chosen_acc)

        # Transfer instruction tag=2, u64=25000
        tag, lamports = struct.unpack("<IQ", ix.data)
        self.assertEqual(tag, 2)
        self.assertEqual(lamports, 25_000)

    def test_build_kamino_instructions(self):
        owner = "Owner11111111111111111111111111111111111"
        reserve = "Reserve111111111111111111111111111111111"
        market = "Market1111111111111111111111111111111111"
        user_ata = "UserAta111111111111111111111111111111111"
        supply = "Supply1111111111111111111111111111111111"

        repay_ix = build_kamino_repay_ix(
            owner=owner,
            obligation="Oblig11111111111111111111111111111111111",
            reserve=reserve,
            lending_market=market,
            source_token_account=user_ata,
            destination_reserve_supply=supply,
            amount_lamports=5_000_000,
        )
        self.assertEqual(repay_ix.program_id, KLEND_PROGRAM_ID)
        self.assertEqual(len(repay_ix.accounts), 7)
        self.assertEqual(len(repay_ix.data), 16)  # 8 bytes disc + 8 bytes u64

        flash_borrow_ix = build_kamino_flash_borrow_ix(
            reserve=reserve,
            lending_market=market,
            destination_token_account=user_ata,
            amount_lamports=10_000_000,
        )
        self.assertEqual(flash_borrow_ix.program_id, KLEND_PROGRAM_ID)
        self.assertEqual(len(flash_borrow_ix.accounts), 4)

        flash_repay_ix = build_kamino_flash_repay_ix(
            reserve=reserve,
            lending_market=market,
            source_token_account=user_ata,
            amount_lamports=10_000_000,
        )
        self.assertEqual(flash_repay_ix.program_id, KLEND_PROGRAM_ID)
        self.assertEqual(len(flash_repay_ix.accounts), 4)


class TestJitoTipFloor(unittest.TestCase):
    def test_fetch_jito_tip_floor_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "landed_tips_25th_percentile": 0.000015,
                "landed_tips_50th_percentile": 0.000045,
                "landed_tips_75th_percentile": 0.000095,
                "landed_tips_95th_percentile": 0.000300,
                "landed_tips_99th_percentile": 0.000600,
            }
        ]
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        floors = fetch_jito_tip_floor(session=mock_session)
        self.assertEqual(floors["25th"], 0.000015)
        self.assertEqual(floors["50th"], 0.000045)
        self.assertEqual(floors["75th"], 0.000095)
        self.assertEqual(floors["95th"], 0.000300)
        self.assertEqual(floors["99th"], 0.000600)

    def test_fetch_jito_tip_floor_fallback(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = RuntimeError("Network timeout")

        floors = fetch_jito_tip_floor(session=mock_session)
        self.assertIn("50th", floors)
        self.assertGreater(floors["50th"], 0.0)


class TestJupiterSwapRouting(unittest.TestCase):
    def test_fetch_jupiter_swap_success(self):
        user = "User1111111111111111111111111111111111111"
        in_mint = "So11111111111111111111111111111111111111112"
        out_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

        mock_session = MagicMock()
        quote_resp = MagicMock()
        quote_resp.status_code = 200
        quote_resp.json.return_value = {"outAmount": "1000000"}

        swap_resp = MagicMock()
        swap_resp.status_code = 200
        swap_resp.json.return_value = {
            "swapInstruction": {
                "programId": JUPITER_V6_PROGRAM_ID,
                "accounts": [
                    {"pubkey": user, "isSigner": True, "isWritable": True},
                    {"pubkey": in_mint, "isSigner": False, "isWritable": True},
                ],
                "data": base64.b64encode(b"\x01\x02\x03").decode("ascii"),
            }
        }
        mock_session.get.return_value = quote_resp
        mock_session.post.return_value = swap_resp

        ixs = fetch_jupiter_swap_instructions(
            user_pubkey=user,
            input_mint=in_mint,
            output_mint=out_mint,
            amount_lamports=1_000_000,
            session=mock_session,
        )
        self.assertEqual(len(ixs), 1)
        self.assertEqual(ixs[0].program_id, JUPITER_V6_PROGRAM_ID)
        self.assertEqual(ixs[0].data, b"\x01\x02\x03")
        self.assertEqual(len(ixs[0].accounts), 2)

    def test_fetch_jupiter_swap_fallback(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = ConnectionError("Jupiter unreachable")

        user = "User1111111111111111111111111111111111111"
        ixs = fetch_jupiter_swap_instructions(
            user_pubkey=user,
            input_mint="MintA11111111111111111111111111111111111",
            output_mint="MintB11111111111111111111111111111111111",
            amount_lamports=5_000_000,
            session=mock_session,
        )
        self.assertEqual(len(ixs), 1)
        self.assertEqual(ixs[0].program_id, JUPITER_V6_PROGRAM_ID)
        self.assertEqual(ixs[0].accounts[0].pubkey, user)
        self.assertTrue(ixs[0].accounts[0].is_signer)


class TestVersionedTransactionCompilation(unittest.TestCase):
    def test_empty_instructions_raises(self):
        with self.assertRaises(ValueError):
            compile_versioned_transaction(instructions=[], payer="Payer11111111111111111111111111111111111")

    def test_compile_single_instruction_wire_format(self):
        payer = "Payer11111111111111111111111111111111111"
        dest = "Dest111111111111111111111111111111111111"
        ix = Instruction(
            program_id=SYSTEM_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
                AccountMeta(pubkey=dest, is_signer=False, is_writable=True),
            ],
            data=b"\x02\x00\x00\x00\x64\x00\x00\x00\x00\x00\x00\x00",
            name="System Transfer",
        )
        compiled = compile_versioned_transaction(
            instructions=[ix],
            payer=payer,
            recent_blockhash="4yBR4G2hvcQyZ8j68wyThFg1iErTz6X3CVjUHuCGdopy",
        )
        self.assertIsInstance(compiled, CompiledTransaction)
        self.assertLess(compiled.size_bytes, 1232)
        self.assertEqual(compiled.num_instructions, 1)
        self.assertEqual(compiled.payer, payer)

        # Wire bytes decode verification
        raw_wire = base64.b64decode(compiled.tx_base64)
        self.assertEqual(len(raw_wire), compiled.size_bytes)

        # First byte is compact-u16 number of signatures = 1
        self.assertEqual(raw_wire[0], 1)
        # 64 dummy zero bytes for the 1 signature
        self.assertEqual(raw_wire[1:65], b"\x00" * 64)
        # Next byte is message version prefix (0x80 for v0)
        self.assertEqual(raw_wire[65], 0x80)

        # Header 3 bytes: num_required_signatures (1), num_readonly_signed (0), num_readonly_unsigned (1: SystemProgram)
        header = raw_wire[66:69]
        self.assertEqual(header[0], 1)
        self.assertEqual(header[1], 0)
        self.assertEqual(header[2], 1)


class TestAtomicDeleverageBundle(unittest.TestCase):
    def test_unviable_plan_raises(self):
        unviable_plan = DeleveragePlan(
            method=DeleveragingMethod.FLASH_UNWIND,
            target_health_factor=1.25,
            current_health_factor=0.85,
            debt_to_repay_usd=5000.0,
            collateral_to_withdraw_usd=0.0,
            fee_and_slippage_usd=0.0,
            resulting_health_factor=0.85,
            resulting_ltv=0.95,
            resulting_borrow_usd=5000.0,
            resulting_collateral_usd=5000.0,
            is_viable=False,
            unviable_reason="Position underwater",
        )
        with self.assertRaises(ValueError):
            build_atomic_deleverage_bundle(
                plan=unviable_plan,
                owner_wallet="User1111111111111111111111111111111111111",
            )

    def test_viable_flash_unwind_bundle_construction(self):
        plan = LiquidationDefenseEngine.plan_flash_unwind(
            collateral_usd=10000.0,
            borrow_usd=7500.0,
            liquidation_threshold=0.80,
            target_health_factor=1.25,
        )
        self.assertTrue(plan.is_viable)

        owner = "User1111111111111111111111111111111111111"
        compiled = build_atomic_deleverage_bundle(
            plan=plan,
            owner_wallet=owner,
            include_jito_tip=True,
            tip_percentile="50th",
            simulate=False,
        )

        self.assertIsInstance(compiled, CompiledTransaction)
        # Verify strict Solana MTU packet size <= 1232 bytes
        self.assertLessEqual(compiled.size_bytes, 1232)
        # Expected instructions: 2 compute budget + flash borrow + repay + withdraw + swap + flash repay + jito tip = 8
        self.assertEqual(compiled.num_instructions, 8)
        self.assertIsNotNone(compiled.jito_bundle)
        self.assertGreater(compiled.jito_bundle.tip_lamports, 0)
        self.assertEqual(compiled.payer, owner)

    def test_simulation_result_handling(self):
        plan = LiquidationDefenseEngine.plan_flash_unwind(
            collateral_usd=10000.0,
            borrow_usd=7500.0,
            liquidation_threshold=0.80,
            target_health_factor=1.25,
        )

        mock_rpc = MagicMock()
        mock_rpc.get_latest_blockhash.return_value = "4yBR4G2hvcQyZ8j68wyThFg1iErTz6X3CVjUHuCGdopy"
        mock_rpc.simulate_transaction.return_value = {
            "value": {
                "err": None,
                "unitsConsumed": 450,
                "fee": 5000,
                "logs": ["Program ComputeBudget111111111111111111111111111111 success"],
            }
        }

        compiled = build_atomic_deleverage_bundle(
            plan=plan,
            owner_wallet="User1111111111111111111111111111111111111",
            rpc_client=mock_rpc,
            simulate=True,
        )

        self.assertIsNotNone(compiled.simulation)
        self.assertTrue(compiled.simulation.success)
        self.assertEqual(compiled.simulation.units_consumed, 450)
        self.assertEqual(compiled.simulation.fee, 5000)
        self.assertIsNone(compiled.simulation.error)

    def test_simulation_failure_handling(self):
        plan = LiquidationDefenseEngine.plan_flash_unwind(
            collateral_usd=10000.0,
            borrow_usd=7500.0,
            liquidation_threshold=0.80,
            target_health_factor=1.25,
        )

        mock_rpc = MagicMock()
        mock_rpc.get_latest_blockhash.return_value = "4yBR4G2hvcQyZ8j68wyThFg1iErTz6X3CVjUHuCGdopy"
        mock_rpc.simulate_transaction.return_value = {
            "value": {
                "err": "InstructionError",
                "unitsConsumed": None,
                "fee": None,
                "logs": ["Program returned error"],
            }
        }

        compiled = build_atomic_deleverage_bundle(
            plan=plan,
            owner_wallet="User1111111111111111111111111111111111111",
            rpc_client=mock_rpc,
            simulate=True,
        )

        self.assertIsNotNone(compiled.simulation)
        self.assertFalse(compiled.simulation.success)
        self.assertEqual(compiled.simulation.units_consumed, 0)
        self.assertEqual(compiled.simulation.fee, 5000)
        self.assertEqual(compiled.simulation.error, "InstructionError")


class TestCliBuilderIntegration(unittest.TestCase):
    def test_print_compiled_transaction_output(self):
        import io
        from contextlib import redirect_stdout
        from kamino_sentinel.cli import print_compiled_transaction

        ix = Instruction(
            program_id=SYSTEM_PROGRAM_ID,
            accounts=[AccountMeta(pubkey="Signer11111111111111111111111111111111111", is_signer=True, is_writable=True)],
            data=b"\x01",
            name="TestIx",
        )
        sim = SimulationResult(
            success=True,
            units_consumed=12345,
            fee=5000,
            logs=["Program test success"],
        )
        bundle = JitoBundle(
            transactions=["tx1"],
            tip_lamports=10000,
            tip_sol=0.00001,
            tip_account=JITO_TIP_ACCOUNTS[0],
            tip_percentile="50th",
        )
        tx = CompiledTransaction(
            tx_base64="AQAAAA==",
            size_bytes=500,
            num_instructions=1,
            accounts_count=2,
            payer="Signer11111111111111111111111111111111111",
            instructions=[ix],
            simulation=sim,
            jito_bundle=bundle,
        )

        f = io.StringIO()
        with redirect_stdout(f):
            print_compiled_transaction(tx)
        output = f.getvalue()

        self.assertIn("Compiled Solana Versioned Transaction (v0)", output)
        self.assertIn("500 / 1232 bytes", output)
        self.assertIn("Jito MEV Bundle Packaging", output)
        self.assertIn("On-Chain RPC Simulation Result", output)
        self.assertIn("12,345 compute units", output)

    def test_cmd_defend_build_tx_simulated(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import MagicMock
        from kamino_sentinel.cli import cmd_defend

        args = MagicMock()
        args.collateral = 10000.0
        args.borrow = 7500.0
        args.target_hf = 1.25
        args.method = "flash"
        args.wallet = None
        args.market = "main"
        args.build_tx = True
        args.simulate = False
        args.jito_tip = "50th"

        f = io.StringIO()
        with redirect_stdout(f):
            cmd_defend(args)
        output = f.getvalue()

        self.assertIn("Kamino Liquidation Defense & Auto-Deleveraging Planner", output)
        self.assertIn("Compiled Solana Versioned Transaction (v0)", output)
        self.assertIn("Wire Size", output)


if __name__ == "__main__":
    unittest.main()
