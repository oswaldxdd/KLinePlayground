import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from backend.data_manager import DataManager
from backend.trade_simulator_enhanced import TradeSimulatorEnhanced


class DataManagerUsMarketTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.manager = DataManager(self.temp_dir.name)

    def test_normalizes_us_symbols_without_accepting_exchange_suffixes(self):
        self.assertEqual(self.manager._normalize_stock_code(" brk-b "), "BRK-B")
        self.assertEqual(self.manager._normalize_stock_code("AAPL"), "AAPL")
        for invalid in ("0700.HK", "EURUSD=X", "000001", "AAPL/../other"):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.manager._normalize_stock_code(invalid), "")

    def test_yfinance_fetch_normalizes_exchange_timezone_and_validates_currency(self):
        index = pd.date_range("2024-01-08", periods=3, freq="B", tz="America/New_York", name="Date")
        history = pd.DataFrame(
            {
                "Open": [10.0, 11.0, 12.0],
                "High": [11.0, 12.0, 13.0],
                "Low": [9.0, 10.0, 11.0],
                "Close": [10.5, 11.5, 12.5],
                "Adj Close": [10.4, 11.3, 12.5],
                "Volume": [100, 200, 300],
                "Dividends": [0.0, 0.1, 0.0],
                "Stock Splits": [0.0, 0.0, 0.0],
            },
            index=index,
        )
        ticker = Mock()
        ticker.history.side_effect = [pd.DataFrame(), history]
        ticker.get_history_metadata.return_value = {
            "currency": "USD",
            "exchangeTimezoneName": "America/New_York",
        }
        with patch("backend.data_manager.yf.Ticker", return_value=ticker):
            with patch("backend.data_manager.time.sleep"):
                bundle = self.manager._fetch_stock_bundle("AAPL", "2024-01-08", "yfinance", "2024-01-10")

        self.assertEqual(ticker.history.call_count, 2)
        self.assertEqual(ticker.history.call_args.kwargs["end"], "2024-01-11")
        self.assertFalse(bundle["kline"]["date"].dt.tz)
        self.assertAlmostEqual(bundle["factor"].iloc[0]["factor"], 10.4 / 10.5)
        self.assertIn("amount", bundle["kline"].columns)

    def test_rejects_non_us_currency_and_invalid_source_even_with_a_cache(self):
        ticker = Mock()
        ticker.history.return_value = pd.DataFrame(
            {"Open": [10], "High": [11], "Low": [9], "Close": [10], "Volume": [1]},
            index=pd.DatetimeIndex(["2024-01-08"], name="Date"),
        )
        ticker.get_history_metadata.return_value = {
            "currency": "HKD",
            "exchangeTimezoneName": "Asia/Hong_Kong",
        }
        with patch("backend.data_manager.yf.Ticker", return_value=ticker):
            with self.assertRaisesRegex(ValueError, "不是以美元计价的美国市场标的"):
                self.manager._fetch_stock_bundle("AAPL", "2024-01-01", "yfinance")

        self.manager._load_cached_kline = Mock(return_value=pd.DataFrame({"date": [pd.Timestamp("2024-01-01")]}))
        self.assertIsNone(self.manager.get_stock_data("AAPL", source="akshare"))
        self.manager._load_cached_kline.assert_not_called()


class UsTradeSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.users_dir = Path(self.temp_dir.name) / "users_us"
        self.simulator = TradeSimulatorEnhanced("tester", 1000, "AAPL", users_dir=str(self.users_dir))

    def test_whole_share_buy_and_same_day_sell(self):
        self.simulator.update_current_price(100, 1)
        self.assertEqual(self.simulator.get_max_buyable_quantity(), 10)

        bought = self.simulator.buy(3, 100, "2024-01-08")
        self.assertTrue(bought["success"])
        self.assertEqual(self.simulator.get_account_info("2024-01-08")["position_summary"]["available_shares"], 3)

        sold = self.simulator.sell(2, 101, "2024-01-08")
        self.assertTrue(sold["success"])
        self.assertEqual(self.simulator.total_shares, 1)
        self.assertEqual(self.simulator.current_capital, 902)

    def test_rejects_fractional_and_boolean_quantities(self):
        self.simulator.update_current_price(100, 1)
        for quantity in (1.5, float("nan"), True, "2"):
            with self.subTest(quantity=quantity):
                self.assertFalse(self.simulator.buy(quantity, 100, "2024-01-08")["success"])
        self.assertEqual(self.simulator.total_shares, 0)

    def test_database_is_written_under_the_explicit_us_user_directory(self):
        self.assertTrue(self.simulator.db_path.startswith(str(self.users_dir)))
        self.assertTrue(Path(self.simulator.db_path).is_file())


class UsTrainingApiTests(unittest.TestCase):
    def test_start_initializes_buying_power_and_trade_api_requires_integer_shares(self):
        from backend import app_enhanced as app_module

        class FakeDataManager:
            @staticmethod
            def get_training_validation_error(*args, **kwargs):
                return None

            @staticmethod
            def get_stock_name(code):
                return code

            @staticmethod
            def _normalize_stock_code(code):
                return str(code).upper()

        class FakeProcessor:
            def __init__(self, *args, **kwargs):
                pass

            @staticmethod
            def get_current_bar():
                return {"open": 99.0, "close": 100.0, "bar_id": 1}

            @staticmethod
            def get_current_date():
                return "2024-01-08"

            @staticmethod
            def add_trade_marker(*args):
                pass

            @staticmethod
            def get_trade_markers():
                return []

        fake_users = SimpleNamespace(get_user_config=lambda _user: {"settings": {}})
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as users_dir, patch.object(app_module, "data_manager", FakeDataManager()), patch.object(app_module, "user_manager", fake_users), patch.object(app_module, "KLineProcessorEnhanced", FakeProcessor), patch.object(app_module, "users_dir_path", users_dir), patch.object(app_module, "_update_api_info"), patch.object(app_module, "active_trainings", {}):
            client = app_module.app.test_client()
            started = client.post(
                "/api/training/start",
                json={"user": "tester", "mode": "specified", "stock_code": "aapl", "start_date": "2024-01-08", "initial_capital": 1000},
            )
            self.assertEqual(started.status_code, 200)
            training_id = started.get_json()["id"]
            account = client.get(f"/api/training/{training_id}/account").get_json()
            self.assertEqual(account["max_buyable_quantity"], 10)

            adjusted = client.post(
                f"/api/training/{training_id}/adjustment",
                json={"adjustment": "forward"},
            )
            self.assertEqual(adjusted.status_code, 400)
            self.assertIn("复权模式必须为 none", adjusted.get_json()["error"])

            fractional = client.post(f"/api/training/{training_id}/trade", json={"action": "buy", "quantity": 1.0})
            self.assertEqual(fractional.status_code, 400)
            self.assertIn("正整数股数", fractional.get_json()["error"])

            whole_share = client.post(f"/api/training/{training_id}/trade", json={"action": "buy", "quantity": 1})
            self.assertEqual(whole_share.status_code, 200)
            self.assertEqual(whole_share.get_json()["trade"]["quantity"], 1)


if __name__ == "__main__":
    unittest.main()
