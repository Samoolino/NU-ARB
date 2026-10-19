import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx import coingecko_reference


class CoinGeckoReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        coingecko_reference._CACHE["expires"] = 0.0
        coingecko_reference._CACHE["data"] = {}

    async def test_maps_only_supported_selected_assets_to_usd_reference_prices(self):
        with patch("arbx.coingecko_reference._fetch", return_value={
            "BTC": {"priceUsd": 80000.0, "updatedAt": 1, "ageSeconds": 4.0},
            "ETH": {"priceUsd": 2000.0, "updatedAt": 1, "ageSeconds": 8.0},
        }) as fetch:
            result = await coingecko_reference.get_reference_prices(["BTC/USDT", "ETH/USDC", "MEME/USDT"])

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["provider"], "CoinGecko")
        self.assertEqual(result["prices"], {"BTC/USDT": 80000.0, "ETH/USDC": 2000.0})
        fetch.assert_called_once()

    async def test_outage_fails_soft_without_fabricating_prices(self):
        with patch("arbx.coingecko_reference._fetch", side_effect=TimeoutError):
            result = await coingecko_reference.get_reference_prices(["BTC/USDT"])

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["prices"], {})
        self.assertIsNone(result["ageSeconds"])

    async def test_cached_reference_avoids_repeated_provider_calls(self):
        data = {"BTC": {"priceUsd": 80000.0, "updatedAt": 1, "ageSeconds": 4.0}}
        with patch("arbx.coingecko_reference._fetch", return_value=data) as fetch:
            first = await coingecko_reference.get_reference_prices(["BTC/USDT"])
            second = await coingecko_reference.get_reference_prices(["BTC/USDT"])

        self.assertEqual(first["prices"], second["prices"])
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
