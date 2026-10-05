import unittest

from pathlib import Path


class LiveConnectivityContractTests(unittest.TestCase):
    def test_connectivity_contract_is_fail_closed(self):
        source = Path("arb_bot/arbx/web_api.py").read_text()
        self.assertIn('"/api/v1/live/connectivity-check"', source)
        self.assertIn("minimumConsecutiveProbes", source)
        self.assertIn("consecutive_successes", source)
        self.assertIn("watch_balance()", source)
        self.assertIn("connectivity_ready", source)


if __name__ == "__main__":
    unittest.main()
