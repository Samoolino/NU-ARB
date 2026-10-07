import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "certify-live-readiness.ps1"


def powershell_executable():
    return shutil.which("powershell.exe") or shutil.which("pwsh")


@unittest.skipUnless(powershell_executable(), "PowerShell is required for readiness-script tests")
class ReadinessScriptTests(unittest.TestCase):
    def run_readiness(self, sandbox_path=None, **switches):
        env = os.environ.copy()
        for name in list(env):
            if name == "BOT_MODE" or name == "BOT_ALLOW_ORDERS" or name.startswith("BOT_ALLOW_"):
                env.pop(name)
        env.update(switches)
        # The script must neither print nor otherwise expose unrelated secrets.
        env["BOT_BINANCE_SECRET"] = "READINESS_TEST_SECRET_MUST_NOT_APPEAR"
        command = [
            powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-RepositoryRoot",
            str(ROOT),
        ]
        if sandbox_path is not None:
            command.extend(["-SandboxEvidencePath", str(sandbox_path)])
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
        )
        self.assertNotIn("READINESS_TEST_SECRET_MUST_NOT_APPEAR", result.stdout + result.stderr)
        return result

    def test_generates_exact_seven_matrices_and_reports_missing_sandbox_evidence(self):
        result = self.run_readiness(BOT_MODE="paper", BOT_ALLOW_ORDERS="0")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["state"], "NOT_READY")
        self.assertEqual(
            [matrix["name"] for matrix in report["matrices"]],
            [
                "VENUE",
                "AUTH",
                "MARKET DATA",
                "NETWORK",
                "EXECUTION",
                "RISK",
                "SANDBOX",
            ],
        )
        for matrix in report["matrices"]:
            with self.subTest(matrix=matrix["name"]):
                self.assertGreater(len(matrix["rows"]), 0)
                for row in matrix["rows"]:
                    self.assertIn(row["status"], {"PASS", "BLOCKED", "UNVERIFIED"})
                    self.assertTrue(row["evidence"].strip())
        self.assertEqual(
            report["sideEffects"],
            {
                "networkRequests": False,
                "ordersSubmitted": False,
                "environmentChanged": False,
                "applicationCodeExecuted": False,
                "journalOrDatabaseOpened": False,
            },
        )
        sandbox = report["matrices"][-1]
        self.assertEqual(sandbox["rows"][0]["status"], "PASS")
        self.assertEqual(sandbox["rows"][1]["status"], "UNVERIFIED")
        self.assertIn("evidence file is absent", sandbox["rows"][1]["evidence"])
        self.assertEqual(report["matrices"][0]["rows"][1]["status"], "UNVERIFIED")
        self.assertEqual(report["matrices"][1]["rows"][0]["status"], "UNVERIFIED")
        self.assertEqual(report["matrices"][2]["rows"][1]["status"], "UNVERIFIED")
        self.assertEqual(report["matrices"][3]["rows"][1]["status"], "UNVERIFIED")
        self.assertEqual(report["matrices"][5]["rows"][1]["status"], "UNVERIFIED")

    def test_unsafe_or_ambiguous_switches_fail_closed(self):
        for switches in (
            {"BOT_MODE": "live", "BOT_ALLOW_ORDERS": "0"},
            {"BOT_MODE": "paper", "BOT_ALLOW_ORDERS": "1"},
            {"BOT_MODE": "paper", "BOT_ALLOW_ORDERS": "true"},
        ):
            with self.subTest(switches=switches):
                result = self.run_readiness(**switches)
                self.assertEqual(result.returncode, 1, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["state"], "NOT_READY")
                sandbox_rows = report["matrices"][-1]["rows"]
                self.assertEqual(sandbox_rows[0]["status"], "BLOCKED")

    def test_missing_switches_use_safe_code_defaults_but_do_not_prove_sandbox(self):
        result = self.run_readiness()
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["state"], "NOT_READY")
        self.assertEqual(report["matrices"][-1]["rows"][0]["status"], "PASS")
        self.assertEqual(report["matrices"][-1]["rows"][1]["status"], "UNVERIFIED")

    def test_malformed_sandbox_artifact_does_not_promote_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = pathlib.Path(directory) / "sandbox-readiness.json"
            evidence_path.write_text("{not-json", encoding="utf-8")
            result = self.run_readiness(
                sandbox_path=evidence_path,
                BOT_MODE="paper",
                BOT_ALLOW_ORDERS="0",
            )
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["state"], "NOT_READY")
        self.assertEqual(report["matrices"][-1]["rows"][1]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
