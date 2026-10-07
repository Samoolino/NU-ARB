import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.journal import TradeJournal
from arbx.risk_policy import (
    IncidentFact,
    PolicyStage,
    PositiveEdgeRiskPolicy,
    ReconciliationEvidence,
    evaluate_pretrade,
)


class Phase10RiskPolicyTests(unittest.TestCase):
    def test_negative_pnl_halts_and_zero_does_not(self):
        policy = PositiveEdgeRiskPolicy()
        self.assertTrue(policy.observe_realized_pnl(0.0).allowed)
        self.assertEqual(policy.stage, PolicyStage.ACTIVE)
        self.assertTrue(policy.observe_realized_pnl(0.01).allowed)
        result = policy.observe_realized_pnl(-0.000001)
        self.assertFalse(result.allowed)
        self.assertEqual(policy.stage, PolicyStage.RECONCILIATION_REQUIRED)
        self.assertFalse(policy.observe_realized_pnl(1.0).allowed)
        self.assertEqual(policy.stage, PolicyStage.RECONCILIATION_REQUIRED)

    def test_unknown_or_nonfinite_realized_pnl_halts(self):
        for pnl in (None, float("nan"), float("inf"), True, "0.1"):
            with self.subTest(pnl=pnl):
                policy = PositiveEdgeRiskPolicy()
                decision = policy.observe_realized_pnl(pnl)
                self.assertFalse(decision.allowed)
                self.assertEqual(policy.stage, PolicyStage.RECONCILIATION_REQUIRED)

    def test_positive_expected_and_worst_case_thresholds_are_inputs_not_guarantees(self):
        self.assertTrue(evaluate_pretrade(1.0, 0.5, 0.1, 0.01).allowed)
        self.assertEqual(
            evaluate_pretrade(1.0, 0.5, 0.1, 0.01).reason,
            "thresholds_met_not_guaranteed",
        )
        for args in (
            (0.0, 0.5, 0.1, 0.01),
            (1.0, 0.0, 0.1, 0.01),
            (None, 0.5, 0.1, 0.01),
            (1.0, 0.5, 0.0, 0.01),
            (1.0, 0.005, 0.1, 0.01),
        ):
            with self.subTest(args=args):
                self.assertFalse(evaluate_pretrade(*args).allowed)

    def test_reconciliation_requires_known_pnl_matched_balances_no_open_orders_no_partials(self):
        failures = (
            (None, "missing_reconciliation"),
            (ReconciliationEvidence(None, True, False, False), "unknown_pnl"),
            (ReconciliationEvidence(0.0, None, False, False), "unknown_balance_state"),
            (ReconciliationEvidence(0.0, False, False, False), "balance_mismatch"),
            (ReconciliationEvidence(0.0, True, None, False), "unknown_order_state"),
            (ReconciliationEvidence(0.0, True, True, False), "open_orders_present"),
            (ReconciliationEvidence(0.0, True, False, None), "unknown_fill_state"),
            (ReconciliationEvidence(0.0, True, False, True), "partial_fill_present"),
        )
        for evidence, reason in failures:
            with self.subTest(reason=reason):
                policy = PositiveEdgeRiskPolicy()
                policy.observe_realized_pnl(-1.0)
                decision = policy.reconcile(evidence)
                self.assertFalse(decision.allowed)
                self.assertEqual(policy.stage, PolicyStage.RECONCILIATION_REQUIRED)
                self.assertIn(reason, decision.reason)

    def test_recovery_requires_valid_reconciliation_then_explicit_restart(self):
        policy = PositiveEdgeRiskPolicy()
        policy.observe_realized_pnl(-0.1)
        reconciled = policy.reconcile(ReconciliationEvidence(0.0, True, False, False))
        self.assertFalse(reconciled.allowed)
        self.assertEqual(policy.stage, PolicyStage.AWAITING_OPERATOR_RESTART)
        self.assertFalse(policy.pretrade(2.0, 1.0, 0.1, 0.1).allowed)
        self.assertFalse(policy.restart(operator_confirmed=False).allowed)
        self.assertEqual(policy.stage, PolicyStage.AWAITING_OPERATOR_RESTART)
        self.assertTrue(policy.restart(operator_confirmed=True).allowed)
        self.assertEqual(policy.stage, PolicyStage.ACTIVE)

    def test_reconciliation_cannot_clear_policy_without_halt_or_from_bad_evidence(self):
        policy = PositiveEdgeRiskPolicy()
        evidence = ReconciliationEvidence(0.0, True, False, False)
        self.assertFalse(policy.reconcile(evidence).allowed)
        self.assertEqual(policy.stage, PolicyStage.ACTIVE)
        policy.observe_realized_pnl(None)
        policy.reconcile(ReconciliationEvidence(0.0, False, False, False))
        self.assertEqual(policy.stage, PolicyStage.RECONCILIATION_REQUIRED)
        self.assertFalse(policy.restart(operator_confirmed=True).allowed)
        self.assertEqual(policy.stage, PolicyStage.RECONCILIATION_REQUIRED)
        policy.observe_realized_pnl(0.0)
        self.assertEqual(policy.stage, PolicyStage.RECONCILIATION_REQUIRED)

    def test_incidents_and_halt_survive_journal_reopen_with_sanitized_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "journal.sqlite3"
            journal = TradeJournal(path)
            policy = PositiveEdgeRiskPolicy(journal)
            policy.observe_realized_pnl(-0.25)
            journal.close()

            reopened = TradeJournal(path)
            try:
                restored = PositiveEdgeRiskPolicy(reopened)
                self.assertEqual(restored.stage, PolicyStage.RECONCILIATION_REQUIRED)
                incidents = reopened.recent_risk_incidents()
            finally:
                reopened.close()

        self.assertEqual(incidents[0]["code"], "realized_loss")
        self.assertEqual(incidents[0]["realized_pnl"], -0.25)
        self.assertEqual(incidents[0]["stage"], PolicyStage.RECONCILIATION_REQUIRED.value)
        self.assertNotIn("secret", incidents[0])
        with self.assertRaises(ValueError):
            IncidentFact(
                "raw exception: secret=abc", None, None, None, None,
                PolicyStage.RECONCILIATION_REQUIRED.value,
            )

    def test_explicit_restart_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "journal.sqlite3"
            journal = TradeJournal(path)
            policy = PositiveEdgeRiskPolicy(journal)
            policy.observe_realized_pnl(-1.0)
            policy.reconcile(ReconciliationEvidence(0.0, True, False, False))
            self.assertFalse(policy.pretrade(2.0, 1.0, 0.1, 0.1).allowed)
            policy.restart(operator_confirmed=True)
            journal.close()

            reopened = TradeJournal(path)
            try:
                restored = PositiveEdgeRiskPolicy(reopened)
                self.assertTrue(restored.permits_new_trading)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
