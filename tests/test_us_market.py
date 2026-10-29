import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from backend.data_manager import DataManager
from backend.kline_processor_enhanced import KLineProcessorEnhanced
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

    def test_hourly_download_keeps_only_completed_regular_session_bars_in_exchange_time(self):
        index = pd.DatetimeIndex(
            [
                "2024-03-08 09:30", "2024-03-08 10:30", "2024-03-08 15:30",
                "2024-03-11 09:30", "2024-03-11 10:30", "2024-03-11 16:30",
            ],
            tz="America/New_York",
            name="Datetime",
        )
        history = pd.DataFrame(
            {"Open": [10, 11, 12, 13, 14, 15], "High": [11, 12, 13, 14, 15, 16],
             "Low": [9, 10, 11, 12, 13, 14], "Close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5],
             "Volume": [100, 200, 300, 400, 500, 600]},
            index=index,
        )
        ticker = Mock()
        ticker.history.return_value = history
        ticker.get_history_metadata.return_value = {
            "currency": "USD",
            "exchangeTimezoneName": "America/New_York",
        }
        with patch("backend.data_manager.yf.Ticker", return_value=ticker):
            result = self.manager._fetch_hourly_kline("AAPL")

        self.assertEqual(ticker.history.call_args.kwargs["interval"], "60m")
        self.assertEqual(ticker.history.call_args.kwargs["prepost"], False)
        self.assertEqual(list(result["date"].dt.strftime("%Y-%m-%d %H:%M")), [
            "2024-03-08 09:30", "2024-03-08 10:30", "2024-03-08 15:30",
            "2024-03-11 09:30", "2024-03-11 10:30",
        ])
        self.assertFalse(result["date"].dt.tz)

    def test_hourly_download_discards_the_still_forming_candle(self):
        index = pd.DatetimeIndex(
            ["2024-03-08 09:30", "2024-03-08 10:30", "2024-03-08 11:30"],
            tz="America/New_York",
            name="Datetime",
        )
        history = pd.DataFrame(
            {"Open": [10, 11, 12], "High": [11, 12, 13], "Low": [9, 10, 11],
             "Close": [10.5, 11.5, 12.5], "Volume": [100, 200, 300]},
            index=index,
        )
        ticker = Mock()
        ticker.history.return_value = history
        ticker.get_history_metadata.return_value = {
            "currency": "USD",
            "exchangeTimezoneName": "America/New_York",
        }
        with patch("backend.data_manager.yf.Ticker", return_value=ticker), patch.object(
            self.manager, "_market_now", return_value=pd.Timestamp("2024-03-08 11:00", tz="America/New_York")
        ):
            result = self.manager._fetch_hourly_kline("AAPL")

        self.assertEqual(list(result["date"].dt.strftime("%H:%M")), ["09:30"])

    def test_hourly_cache_isolated_from_daily_cache(self):
        hourly = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-08 09:30", "2024-01-08 10:30"]),
            "open": [10, 11], "high": [11, 12], "low": [9, 10],
            "close": [10.5, 11.5], "volume": [100, 200],
        })
        daily = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-08"]),
            "open": [10], "high": [12], "low": [9], "close": [11.5], "volume": [300],
        })
        with patch.object(self.manager, "_fetch_hourly_kline", return_value=hourly) as fetch:
            loaded_hourly = self.manager.get_stock_data("AAPL", interval="hourly")
        self.manager._save_bundle_to_cache("AAPL", daily, None)

        self.assertEqual(len(loaded_hourly), 2)
        self.assertEqual(len(self.manager._load_cached_hourly_kline("AAPL")), 2)
        self.assertEqual(len(self.manager._load_cached_kline("AAPL")), 1)
        fetch.assert_called_once_with("AAPL")
        self.assertNotEqual(self.manager._hourly_cache_path("AAPL"), str(Path(self.manager.kline_dir) / "AAPL.csv"))

    def test_hourly_offline_source_is_rejected_explicitly(self):
        self.assertIn("暂不包含小时行情", self.manager.get_training_validation_error(
            "AAPL", pd.Timestamp.now().strftime("%Y-%m-%d"), source="offline", interval="hourly"
        ))

    def test_hourly_validation_accepts_first_available_exchange_date(self):
        bars = pd.DataFrame({
            "date": pd.to_datetime(["2026-10-29 09:30", "2026-10-29 10:30"]),
            "open": [10, 11], "high": [11, 12], "low": [9, 10],
            "close": [10.5, 11.5], "volume": [100, 200],
        })
        with patch.object(self.manager, "_market_now", return_value=pd.Timestamp("2026-10-29 11:00", tz="America/New_York")), patch.object(
            self.manager, "get_stock_data", return_value=bars
        ):
            self.assertIsNone(self.manager.get_training_validation_error(
                "AAPL", "2026-10-29", source="yfinance", interval="hourly"
            ))


class HourlyKLineProcessorTests(unittest.TestCase):
    def test_steps_one_revealed_hour_and_daily_view_aggregates_only_visible_bars(self):
        bars = pd.DataFrame({
            "date": pd.to_datetime([
                "2024-03-08 09:30", "2024-03-08 10:30", "2024-03-08 11:30",
                "2024-03-11 09:30", "2024-03-11 10:30",
            ]),
            "open": [100, 101, 102, 103, 104],
            "high": [101, 102, 150, 104, 105],
            "low": [99, 100, 101, 102, 103],
            "close": [100.5, 101.5, 149, 103.5, 104.5],
            "volume": [10, 20, 30, 40, 50],
        })

        class FakeManager:
            @staticmethod
            def get_stock_data(*_args, **_kwargs):
                return bars.copy()

            @staticmethod
            def get_factor_data(*_args, **_kwargs):
                return None

            @staticmethod
            def get_dividend_data(*_args, **_kwargs):
                return None

        processor = KLineProcessorEnhanced(FakeManager(), "AAPL", "2024-03-08", interval="hourly")
        first = processor.get_visible_data(view_period="hourly")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["time"], pd.Timestamp("2024-03-08 09:30", tz="America/New_York").timestamp())
        self.assertEqual(processor.get_visible_data(view_period="daily")[0]["high"], 101)

        self.assertTrue(processor.next_bar())
        second = processor.get_visible_data(view_period="hourly")
        self.assertEqual(len(second), 2)
        day = processor.get_visible_data(view_period="daily")[0]
        self.assertEqual((day["open"], day["high"], day["close"], day["volume"]), (100, 102, 101.5, 30))
        self.assertLess(day["high"], 150)
        self.assertEqual(len(processor.get_visible_data(view_period="weekly")), 1)
        processor.add_trade_marker("buy", 101.5)
        self.assertTrue(processor.next_bar())
        marker_time = processor._market_timestamp(processor.full_data.iloc[processor.current_index]["date"])
        processor.add_trade_marker("sell", 149)
        self.assertEqual(processor.get_trade_markers()[1]["time"], marker_time)
        self.assertEqual(processor.get_trade_markers(view_period="daily"), [])
        markers = processor.get_trade_markers(view_period="hourly")
        self.assertEqual(len(markers), 2)
        self.assertNotEqual(markers[0]["time"], markers[1]["time"])
        marker_dates = pd.to_datetime([item["time"] for item in markers], unit="s", utc=True).tz_convert("America/New_York")
        self.assertEqual([item.strftime("%Y-%m-%d") for item in marker_dates], ["2024-03-08", "2024-03-08"])
        self.assertEqual([item.strftime("%H:%M") for item in marker_dates], ["10:30", "11:30"])
        with self.assertRaisesRegex(ValueError, "仅适用于小时线训练"):
            KLineProcessorEnhanced(FakeManager(), "AAPL", "2024-03-08", interval="daily").get_visible_data(view_period="hourly")


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
                self.current_index = 0
                self.max_index = 1

            @staticmethod
            def get_current_bar():
                return {"open": 99.0, "close": 100.0, "bar_id": 1}

            @staticmethod
            def get_current_date():
                return "2024-01-08"

            @staticmethod
            def get_current_datetime():
                return "2024-01-08"

            @staticmethod
            def add_trade_marker(*args):
                pass

            @staticmethod
            def get_trade_markers(*_args, **_kwargs):
                return []

            @staticmethod
            def get_full_data(*_args, **_kwargs):
                return [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

            @staticmethod
            def get_volume_data(*_args, **_kwargs):
                return []

            @staticmethod
            def get_ma_data(*_args, **_kwargs):
                return {}

        fake_users = SimpleNamespace(get_user_config=lambda _user: {"settings": {}})
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as users_dir, patch.object(app_module, "data_manager", FakeDataManager()), patch.object(app_module, "user_manager", fake_users), patch.object(app_module, "KLineProcessorEnhanced", FakeProcessor), patch.object(app_module, "users_dir_path", users_dir), patch.object(app_module, "_update_api_info"), patch.object(app_module, "active_trainings", {}):
            client = app_module.app.test_client()
            started = client.post(
                "/api/training/start",
                json={"user": "tester", "mode": "specified", "stock_code": "aapl", "start_date": "2024-01-08", "initial_capital": 1000},
            )
            self.assertEqual(started.status_code, 200)
            training_id = started.get_json()["id"]
            unrevealed = client.get(f"/api/training/{training_id}/full_data")
            self.assertEqual(unrevealed.status_code, 403)
            self.assertIn("训练结束后", unrevealed.get_json()["error"])

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

            app_module.active_trainings[training_id]["status"] = "ended"
            completed = client.get(f"/api/training/{training_id}/full_data")
            self.assertEqual(completed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
