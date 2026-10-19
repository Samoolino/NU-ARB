import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class VercelConfigTests(unittest.TestCase):
    def test_static_dashboard_output_directory_is_explicit_and_preserves_api_functions(self):
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(config.get("outputDirectory"), "public")
        self.assertIn("api/scan.js", config.get("functions", {}))
        self.assertIn("api/control.js", config.get("functions", {}))


if __name__ == "__main__":
    unittest.main()
