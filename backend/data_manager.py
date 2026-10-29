import csv
import json
import os
import random
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

try:
    from xtquant import xtdata

    XTDATA_AVAILABLE = True
except ImportError:
    XTDATA_AVAILABLE = False

try:
    from mootdx.quotes import Quotes

    MOOTDX_AVAILABLE = True
except ImportError:
    MOOTDX_AVAILABLE = False


class DataManager:
    """管理美股日线、周线、小时行情下载与缓存，以及离线增量补数。"""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = data_dir
        # Separate paths prevent old A-share files from being interpreted as US symbols.
        self.market_data_dir = os.path.join(data_dir, "us_market")
        self.kline_dir = os.path.join(self.market_data_dir, "kline_raw")
        # Intraday bars have different timestamp granularity and Yahoo history
        # limits, so never share or overwrite the daily CSV cache.
        self.hourly_dir = os.path.join(self.market_data_dir, "kline_hourly")
        self.factor_dir = os.path.join(self.market_data_dir, "factor")
        self.dividend_dir = os.path.join(self.market_data_dir, "corporate_actions")
        self.offline_dir = os.path.join(data_dir, "us_market_offline")

        os.makedirs(self.kline_dir, exist_ok=True)
        os.makedirs(self.hourly_dir, exist_ok=True)
        os.makedirs(self.factor_dir, exist_ok=True)
        os.makedirs(self.dividend_dir, exist_ok=True)
        os.makedirs(self.offline_dir, exist_ok=True)

        self.stock_list = None
        self.stock_names: Dict[str, str] = {}
        self._refreshed_symbols = set()
        self._refreshed_hourly_symbols = set()
        self._last_data_error: Dict[str, str] = {}
        self._last_hourly_data_error: Dict[str, str] = {}
        self._offline_stock_codes_cache: Optional[List[str]] = None
        self._offline_date_range_cache: Dict[str, Optional[Tuple[pd.Timestamp, pd.Timestamp]]] = {}

    US_WATCHLIST = {
        "AAPL": "Apple", "AMZN": "Amazon", "AVGO": "Broadcom", "BRK-B": "Berkshire Hathaway",
        "COST": "Costco", "GOOGL": "Alphabet", "JPM": "JPMorgan Chase", "LLY": "Eli Lilly",
        "META": "Meta Platforms", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla",
        "UNH": "UnitedHealth", "V": "Visa", "WMT": "Walmart",
    }

    def download_stock_list(self) -> bool:
        """使用内置的高流动性美股样例列表供盲盒模式使用。"""
        self.stock_list = pd.DataFrame(
            [{"code": code, "name": name} for code, name in self.US_WATCHLIST.items()]
        )
        self.stock_names = dict(self.US_WATCHLIST)
        return True

    def load_stock_list(self) -> bool:
        """加载股票列表缓存。"""
        try:
            return self.download_stock_list()
        except Exception as e:
            print(f"加载股票列表失败: {e}")
            return self.download_stock_list()

    def get_available_sources(self) -> List[Dict]:
        """返回当前可用数据源列表。"""
        return [
            {
                "value": "yfinance",
                "label": "Yahoo Finance（yfinance）",
                "available": True,
                "kind": "online",
                "supports_incremental_sync": True,
                "supports_factor": False,
                "description": "免密钥历史行情；受 Yahoo Finance 覆盖范围、限流和网络可用性影响。",
            },
            {
                "value": "offline",
                "label": "本地离线数据",
                "available": True,
                "kind": "offline",
                "supports_incremental_sync": False,
                "supports_factor": True,
                "description": "读取 data/us_market_offline 中的本地美股文件。",
            },
        ]

    def _get_market_suffix(self, stock_code: str) -> str:
        if stock_code.startswith(("60", "68", "69")):
            return ".SH"
        if stock_code.startswith(("00", "30")):
            return ".SZ"
        if stock_code.startswith(("43", "83", "87", "92")):
            return ".BJ"
        return ".SZ"

    def _format_xt_code(self, stock_code: str) -> str:
        return f"{stock_code}{self._get_market_suffix(stock_code)}"

    def _get_tdx_market_code(self, stock_code: str) -> int:
        if stock_code.startswith(("60", "68", "69")):
            return 1
        return 0

    def _normalize_stock_code(self, stock_code: str) -> str:
        code = str(stock_code or "").strip().upper()
        # Yahoo symbols may contain a dot (BRK.B), dash (BRK-B), or caret (^GSPC).
        # Keep the full symbol: stripping punctuation can silently select another security.
        # The public UI accepts common US ticker symbols (including BRK-B), not
        # Yahoo suffixes for other exchanges or FX/crypto/index instruments.
        if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,9}", code):
            return ""
        return code

    def _get_offline_file(self, stock_code: str) -> Optional[str]:
        stock_code = self._normalize_stock_code(stock_code)
        if not stock_code:
            return None
        candidate = os.path.join(self.offline_dir, f"{stock_code}.csv")
        return candidate if os.path.isfile(candidate) else None

    def _read_csv_with_fallback(self, path: str, **kwargs) -> pd.DataFrame:
        last_error = None
        for encoding in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return pd.read_csv(path, encoding=encoding, **kwargs)
            except UnicodeDecodeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return pd.read_csv(path, **kwargs)

    def _coalesce_duplicate_columns(self, data: pd.DataFrame) -> pd.DataFrame:
        if data is None or data.empty:
            return data

        normalized_columns = [str(col).lstrip("\ufeff").strip() for col in data.columns]
        data = data.copy()
        data.columns = normalized_columns

        if not pd.Index(normalized_columns).duplicated().any():
            return data

        collapsed_columns: List[str] = []
        collapsed_series: Dict[str, pd.Series] = {}
        for column in normalized_columns:
            if column in collapsed_series:
                continue

            same_name_columns = data.loc[:, data.columns == column]
            if isinstance(same_name_columns, pd.Series) or same_name_columns.shape[1] == 1:
                collapsed_series[column] = same_name_columns.iloc[:, 0] if isinstance(same_name_columns, pd.DataFrame) else same_name_columns
            else:
                collapsed_series[column] = same_name_columns.bfill(axis=1).iloc[:, 0]
            collapsed_columns.append(column)

        return pd.DataFrame({column: collapsed_series[column] for column in collapsed_columns}, index=data.index)

    def _parse_date_series(self, series: pd.Series) -> pd.Series:
        raw = series.copy()
        if not isinstance(raw, pd.Series):
            raw = pd.Series(raw)

        raw_str = raw.astype(str).str.strip()
        raw_str = raw_str.replace({"": None, "nan": None, "NaT": None, "None": None})
        parsed = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
        numeric_mask = raw_str.notna() & raw_str.str.fullmatch(r"\d{8}")

        if numeric_mask.any():
            parsed.loc[numeric_mask] = pd.to_datetime(
                raw_str.loc[numeric_mask],
                format="%Y%m%d",
                errors="coerce",
            )

        if (~numeric_mask).any():
            parsed.loc[~numeric_mask] = pd.to_datetime(raw_str.loc[~numeric_mask], errors="coerce")

        return parsed

    def _read_last_nonempty_line(self, path: str, encoding: str) -> Optional[str]:
        with open(path, "rb") as file:
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            buffer = b""

            while file_size > 0:
                read_size = min(4096, file_size)
                file_size -= read_size
                file.seek(file_size)
                buffer = file.read(read_size) + buffer

                for raw_line in reversed(buffer.splitlines()):
                    stripped = raw_line.strip()
                    if stripped:
                        return stripped.decode(encoding, errors="ignore")

        return None

    def _extract_csv_field(self, line: str, index: int) -> Optional[str]:
        try:
            row = next(csv.reader([line]))
        except Exception:
            return None
        if index >= len(row):
            return None
        return row[index]

    def _get_offline_stock_codes(self) -> List[str]:
        if self._offline_stock_codes_cache is not None:
            return list(self._offline_stock_codes_cache)

        stock_codes = set()
        for filename in os.listdir(self.offline_dir):
            if not filename.lower().endswith(".csv"):
                continue
            stock_code = self._normalize_stock_code(os.path.splitext(filename)[0])
            if stock_code:
                stock_codes.add(stock_code)

        self._offline_stock_codes_cache = sorted(stock_codes)
        return list(self._offline_stock_codes_cache)

    def _filter_stock_codes_by_sector(self, stock_codes: List[str], sector: str) -> List[str]:
        # Retained for API compatibility; the former A-share board filters do not apply.
        return list(stock_codes)

    def _resolve_training_range(self, date_start: str, date_end: str) -> Tuple[pd.Timestamp, pd.Timestamp]:
        raw_start = str(date_start or "").strip()
        raw_end = str(date_end or "").strip()

        if len(raw_start) == 9 and raw_start[:4].isdigit() and raw_start[4] == "-" and raw_start[5:].isdigit():
            start_year = int(raw_start[:4])
            end_year = int(raw_start[5:])
            range_start = pd.Timestamp(year=start_year, month=1, day=1)
            range_end = pd.Timestamp(year=end_year, month=12, day=31)
        else:
            range_start = pd.to_datetime(raw_start)
            range_end = pd.to_datetime(raw_end or raw_start)

        if range_end < range_start:
            raise ValueError("结束日期不能早于开始日期")

        return range_start.normalize(), range_end.normalize()

    def _random_date_in_range(self, range_start: pd.Timestamp, range_end: pd.Timestamp) -> str:
        delta_days = max(0, (range_end.normalize() - range_start.normalize()).days)
        chosen = range_start.normalize() + timedelta(days=random.randint(0, delta_days))
        return chosen.strftime("%Y-%m-%d")

    def _get_offline_date_range(self, stock_code: str) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
        stock_code = self._normalize_stock_code(stock_code)
        if not stock_code:
            return None
        if stock_code in self._offline_date_range_cache:
            return self._offline_date_range_cache[stock_code]

        offline_path = self._get_offline_file(stock_code)
        if not offline_path:
            self._offline_date_range_cache[stock_code] = None
            return None

        last_error = None
        for encoding in ("utf-8", "utf-8-sig", "gbk"):
            try:
                with open(offline_path, "r", encoding=encoding, newline="") as file:
                    reader = csv.reader(file)
                    header = next(reader, None)
                    first_row = next(reader, None)

                if not header or not first_row:
                    self._offline_date_range_cache[stock_code] = None
                    return None

                normalized_header = [col.lstrip("\ufeff") for col in header]
                date_index = next(
                    (idx for idx, col in enumerate(normalized_header) if col in {"交易日", "交易日期", "date"}),
                    None,
                )
                if date_index is None:
                    self._offline_date_range_cache[stock_code] = None
                    return None

                first_date = first_row[date_index] if date_index < len(first_row) else None
                last_line = self._read_last_nonempty_line(offline_path, encoding)
                last_date = self._extract_csv_field(last_line, date_index) if last_line else None
                candidate_dates = [value for value in (first_date, last_date) if value]
                parsed_dates = self._parse_date_series(pd.Series(candidate_dates)).dropna()
                if parsed_dates.empty:
                    self._offline_date_range_cache[stock_code] = None
                    return None

                date_range = (parsed_dates.min(), parsed_dates.max())
                self._offline_date_range_cache[stock_code] = date_range
                return date_range
            except UnicodeDecodeError as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error

        self._offline_date_range_cache[stock_code] = None
        return None

    def _get_stock_date_range(
        self,
        stock_code: str,
        source: str = "yfinance",
        interval: str = "daily",
    ) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
        stock_code = self._normalize_stock_code(stock_code)
        if source == "offline":
            return self._get_offline_date_range(stock_code)

        data = self.get_stock_data(stock_code, source=source, interval=interval)
        if data is None or data.empty:
            return None
        return data["date"].min(), data["date"].max()

    def get_stock_universe(self, market: str = "all") -> List[str]:
        offline_codes = self._get_offline_stock_codes()
        return sorted(set(self.US_WATCHLIST) | set(offline_codes))

    def _normalize_daily_kline(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None

        data = self._coalesce_duplicate_columns(df.copy())
        if "股票代码" in data.columns:
            data = data.drop(columns=["股票代码"])

        if "Date" in data.columns or "Datetime" in data.columns:
            date_column = "Date" if "Date" in data.columns else "Datetime"
            rename_map = {
                date_column: "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
                "Adj Close": "adj_close",
                "Dividends": "dividends",
                "Stock Splits": "stock_splits",
            }
            data = data.rename(columns=rename_map)
            if "amount" not in data.columns and {"close", "volume"}.issubset(data.columns):
                data["amount"] = data["close"] * data["volume"]
            if "turnover_rate" not in data.columns:
                data["turnover_rate"] = 0.0
        elif "日期" in data.columns:
            rename_map = {
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "振幅": "amplitude",
                "涨跌幅": "change_pct",
                "涨跌额": "change_amount",
                "换手率": "turnover_rate",
            }
            data = data.rename(columns=rename_map)
        elif "datetime" in data.columns:
            rename_map = {
                "datetime": "date",
                "open": "open",
                "close": "close",
                "high": "high",
                "low": "low",
                "vol": "volume",
                "volume": "volume",
                "amount": "amount",
            }
            data = data.rename(columns=rename_map)
        elif "date" not in data.columns:
            columns = list(data.columns)
            expected = [
                "date",
                "open",
                "close",
                "high",
                "low",
                "volume",
                "amount",
                "amplitude",
                "change_pct",
                "change_amount",
                "turnover_rate",
            ]
            if len(columns) >= 6:
                data.columns = expected[: len(columns)]

        data = self._coalesce_duplicate_columns(data)
        data = data.drop(columns=["year", "month", "day", "hour", "minute", "code"], errors="ignore")

        required = ["date", "open", "close", "high", "low", "volume"]
        if not set(required).issubset(set(data.columns)):
            return None

        data["date"] = self._parse_date_series(data["date"])
        for col in ["open", "close", "high", "low", "volume", "amount", "turnover_rate", "adj_close", "dividends", "stock_splits"]:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors="coerce")

        data = data.dropna(subset=["date", "open", "close", "high", "low", "volume"])
        data = data.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
        return data

    def _normalize_factor_data(self, raw_df: Optional[pd.DataFrame], base_df: Optional[pd.DataFrame] = None) -> Optional[pd.DataFrame]:
        if raw_df is None or raw_df.empty:
            return None

        data = raw_df.copy()
        if "股票代码" in data.columns:
            data = data.drop(columns=["股票代码"])

        if "日期" in data.columns:
            rename_map = {
                "日期": "date",
                "收盘": "close_adj",
                "close": "close_adj",
            }
            data = data.rename(columns=rename_map)
        elif "date" not in data.columns and len(data.columns) >= 2:
            columns = list(data.columns)
            fallback = ["date", "open_adj", "close_adj", "high_adj", "low_adj", "volume", "amount"]
            data.columns = fallback[: len(columns)]

        if "date" not in data.columns:
            return None

        data["date"] = pd.to_datetime(data["date"])

        if "factor" in data.columns:
            factor_df = data[["date", "factor"]].copy()
        elif "close_adj" in data.columns and base_df is not None and not base_df.empty:
            merged = pd.merge(
                base_df[["date", "close"]],
                data[["date", "close_adj"]],
                on="date",
                how="inner",
            )
            merged["factor"] = merged["close_adj"] / merged["close"]
            factor_df = merged[["date", "factor"]]
        else:
            return None

        factor_df["factor"] = pd.to_numeric(factor_df["factor"], errors="coerce").fillna(1.0)
        factor_df = factor_df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
        return factor_df

    def _normalize_offline_data(self, data: pd.DataFrame) -> Optional[pd.DataFrame]:
        if data is None or data.empty:
            return None

        df = self._coalesce_duplicate_columns(data.copy())
        rename_map = {
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "amount": "amount",
            "turnover_rate": "turnover_rate",
            "factor": "factor",
            "交易日": "date",
            "交易日期": "date",
            "开盘价": "open",
            "最高价": "high",
            "最低价": "low",
            "收盘价": "close",
            "成交量（手）": "volume",
            "成交量(手)": "volume",
            "成交额（千元）": "amount",
            "成交额(千元)": "amount",
            "换手率（%）": "turnover_rate",
            "换手率(%)": "turnover_rate",
            "复权因子": "factor",
        }
        df = df.rename(columns={key: value for key, value in rename_map.items() if key in df.columns})
        df = self._coalesce_duplicate_columns(df)

        if "date" not in df.columns:
            return None

        df["date"] = self._parse_date_series(df["date"])

        required = ["date", "open", "high", "low", "close", "volume"]
        if not set(required).issubset(df.columns):
            return None

        for col in ["open", "high", "low", "close", "volume", "amount", "turnover_rate", "factor"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=required)
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
        return df

    def _resample_to_weekly(self, data: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        if data is None or data.empty:
            return data

        df = data.copy()
        df = df.sort_values("date").set_index("date")
        if set(df.columns) == {"factor"}:
            weekly_factor = df.resample("W-FRI").last().dropna().reset_index()
            return weekly_factor

        aggregations = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }

        if "amount" in df.columns:
            aggregations["amount"] = "sum"
        if "turnover_rate" in df.columns:
            aggregations["turnover_rate"] = "sum"
        if "factor" in df.columns:
            aggregations["factor"] = "last"

        weekly = df.resample("W-FRI").agg(aggregations)
        weekly = weekly.dropna(subset=["open", "high", "low", "close"])
        weekly = weekly.reset_index()
        return weekly

    def _slice_date_range(
        self,
        data: Optional[pd.DataFrame],
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
    ) -> Optional[pd.DataFrame]:
        if data is None or data.empty:
            return data

        frame = data.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        if start_date is not None:
            frame = frame[frame["date"] >= pd.to_datetime(start_date)]
        if end_date is not None:
            frame = frame[frame["date"] <= pd.to_datetime(end_date)]
        return frame.reset_index(drop=True)

    def _normalize_sync_range(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Tuple[pd.Timestamp, pd.Timestamp]:
        range_start = pd.to_datetime(start_date).normalize() if start_date else pd.Timestamp("2010-01-01")
        range_end = pd.to_datetime(end_date).normalize() if end_date else pd.Timestamp(datetime.now().date())
        if range_end < range_start:
            raise ValueError("补数结束日期不能早于开始日期")
        return range_start, range_end

    def _supports_factor_refresh(self, source: str) -> bool:
        return False

    def _fetch_stock_bundle(
        self,
        stock_code: str,
        start_date: str,
        source: str,
        end_date: Optional[str] = None,
    ) -> Dict[str, Optional[pd.DataFrame]]:
        if source != "yfinance":
            raise ValueError("仅支持 Yahoo Finance 在线数据和本地离线数据")
        stock_code = self._normalize_stock_code(stock_code)
        if not stock_code:
            raise ValueError("请输入有效的美股代码，例如 AAPL 或 BRK-B")

        exclusive_end = (pd.to_datetime(end_date) + timedelta(days=1)).strftime("%Y-%m-%d") if end_date else None
        history = None
        ticker = None
        last_error = None
        for attempt in range(2):
            try:
                ticker = yf.Ticker(stock_code)
                history = ticker.history(
                    start=start_date,
                    end=exclusive_end,
                    interval="1d",
                    auto_adjust=False,
                    actions=True,
                    timeout=20,
                )
                if history is not None and not history.empty:
                    break
                last_error = RuntimeError("Yahoo Finance returned no daily price rows")
            except Exception as exc:
                last_error = exc
            if attempt == 0:
                time.sleep(0.5)

        if history is None or history.empty:
            raise RuntimeError(
                f"Yahoo Finance 暂时未返回 {stock_code} 的行情；请核对美股代码和日期，并检查网络后重试。"
            ) from last_error

        try:
            metadata = ticker.get_history_metadata()
        except Exception as exc:
            raise RuntimeError(f"无法确认 {stock_code} 的币种和交易所信息，已停止导入行情") from exc
        if metadata.get("currency") != "USD" or metadata.get("exchangeTimezoneName") != "America/New_York":
            raise ValueError(f"{stock_code} 不是以美元计价的美国市场标的，无法用于本训练器")

        history = history.copy().rename_axis("Date").reset_index()
        if "Date" not in history.columns and "Datetime" in history.columns:
            history = history.rename(columns={"Datetime": "Date"})
        if "Date" in history.columns and isinstance(history["Date"].dtype, pd.DatetimeTZDtype):
            history["Date"] = history["Date"].dt.tz_localize(None)
        daily_df = self._normalize_daily_kline(history)
        factor_df = None
        if daily_df is not None and "adj_close" in daily_df.columns:
            factor_data = daily_df[["date", "adj_close", "close"]].copy()
            factor_data["factor"] = factor_data["adj_close"] / factor_data["close"]
            factor_df = factor_data[["date", "factor"]].replace([float("inf"), -float("inf")], pd.NA).dropna()
            daily_df = daily_df.drop(columns=["adj_close"], errors="ignore")
        return {"kline": daily_df, "factor": factor_df}

    def _fetch_hourly_kline(self, stock_code: str) -> pd.DataFrame:
        """Fetch genuine Yahoo 60-minute regular-session bars (last two years)."""
        stock_code = self._normalize_stock_code(stock_code)
        if not stock_code:
            raise ValueError("请输入有效的美股代码，例如 AAPL 或 BRK-B")

        ticker = yf.Ticker(stock_code)
        last_error = None
        history = None
        for attempt in range(2):
            try:
                history = ticker.history(
                    period="2y",
                    interval="60m",
                    prepost=False,
                    auto_adjust=False,
                    actions=False,
                    timeout=20,
                    raise_errors=True,
                )
                if history is not None and not history.empty:
                    break
                last_error = RuntimeError("Yahoo Finance returned no 60-minute price rows")
            except Exception as exc:
                last_error = exc
            if attempt == 0:
                time.sleep(0.5)

        if history is None or history.empty:
            raise RuntimeError(
                f"Yahoo Finance 暂未返回 {stock_code} 的 60 分钟行情。Yahoo 的小时线历史约限最近 730 天；"
                "请检查代码、日期和网络，或改用日线训练。"
            ) from last_error

        try:
            metadata = ticker.get_history_metadata()
        except Exception as exc:
            raise RuntimeError(f"无法确认 {stock_code} 的币种和交易所信息，已停止导入小时行情") from exc
        if metadata.get("currency") != "USD" or metadata.get("exchangeTimezoneName") != "America/New_York":
            raise ValueError(f"{stock_code} 不是以美元计价的美国市场标的，无法用于本训练器")

        frame = history.copy().rename_axis("Datetime").reset_index()
        timestamp_column = "Datetime" if "Datetime" in frame.columns else "Date"
        if timestamp_column not in frame.columns:
            raise RuntimeError("Yahoo Finance 返回的小时行情缺少时间戳")

        timestamps = pd.DatetimeIndex(pd.to_datetime(frame[timestamp_column], errors="coerce"))
        if timestamps.tz is None:
            timestamps = timestamps.tz_localize("America/New_York")
        else:
            timestamps = timestamps.tz_convert("America/New_York")
        # Yahoo's prepost=False is the primary session filter. Keep the explicit
        # exchange-time guard as defense against extended-hours rows in cached or
        # provider-returned data. Hour labels are bar start times; 15:30 is the
        # regular session's final (normally 30-minute) candle.
        minute_of_day = timestamps.hour * 60 + timestamps.minute
        session_mask = pd.Series(
            (minute_of_day >= 9 * 60 + 30) & (minute_of_day <= 15 * 60 + 30),
            index=frame.index,
        )
        # Yahoo can return the currently forming candle. A normal candle ends
        # one hour after its start; the 15:30 candle is the regular session's
        # final half-hour. For early closes the one-hour rule waits an extra
        # half-hour, which is conservative and avoids exposing a partial bar.
        bar_ends = timestamps + pd.Timedelta(hours=1)
        closing_candle = minute_of_day == 15 * 60 + 30
        bar_ends = bar_ends.where(~closing_candle, timestamps + pd.Timedelta(minutes=30))
        now_et = self._market_now()
        session_mask &= pd.Series(bar_ends <= now_et, index=frame.index)
        frame[timestamp_column] = timestamps.tz_localize(None)
        frame = frame.loc[session_mask].copy()
        normalized = self._normalize_daily_kline(frame)
        if normalized is None or normalized.empty:
            raise RuntimeError(
                f"Yahoo Finance 未返回 {stock_code} 可用的美东常规交易时段小时线；请改用日线训练。"
            )
        normalized = normalized.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
        return normalized.drop(columns=["adj_close", "dividends", "stock_splits"], errors="ignore")

    @staticmethod
    def _market_now() -> pd.Timestamp:
        return pd.Timestamp.now(tz="America/New_York")

    def _hourly_cache_path(self, stock_code: str) -> str:
        return os.path.join(self.hourly_dir, f"{stock_code}.csv")

    def _load_cached_hourly_kline(self, stock_code: str) -> Optional[pd.DataFrame]:
        path = self._hourly_cache_path(stock_code)
        if not os.path.isfile(path):
            return None
        try:
            cached = pd.read_csv(path)
            return self._normalize_daily_kline(cached)
        except Exception as exc:
            print(f"读取 {stock_code} 小时行情缓存失败: {exc}")
            return None

    def _save_hourly_kline(self, stock_code: str, data: Optional[pd.DataFrame]) -> bool:
        if data is None or data.empty:
            return False
        saved = data.copy().sort_values("date").drop_duplicates(subset=["date"], keep="last")
        saved["date"] = pd.to_datetime(saved["date"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        saved.to_csv(self._hourly_cache_path(stock_code), index=False, encoding="utf-8")
        return True

    def _fetch_stock_bundle_ak(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> Dict[str, Optional[pd.DataFrame]]:
        end_date = (end_date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
        raw_daily = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date,
            adjust="",
        )
        daily_df = self._normalize_daily_kline(raw_daily)
        if daily_df is None or daily_df.empty:
            return {"kline": None, "factor": None}

        factor_df = None
        try:
            raw_qfq = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date,
                adjust="qfq",
            )
            factor_df = self._normalize_factor_data(raw_qfq, daily_df)
        except Exception as e:
            print(f"下载 {stock_code} 复权因子失败: {e}")

        return {"kline": daily_df, "factor": factor_df}

    def _fetch_stock_bundle_xt(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> Dict[str, Optional[pd.DataFrame]]:
        if not XTDATA_AVAILABLE:
            raise RuntimeError("xtdata 不可用，请先安装并配置 xtquant。")

        xt_code = self._format_xt_code(stock_code)
        start_time = start_date.replace("-", "")
        end_time = (end_date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")

        xtdata.download_history_data2(stock_list=[xt_code], period="1d", start_time=start_time, end_time=end_time)

        raw_dict = xtdata.get_market_data(
            field_list=["time", "open", "close", "high", "low", "volume", "amount"],
            stock_list=[xt_code],
            period="1d",
            start_time=start_time,
            end_time=end_time,
            dividend_type="none",
            fill_data=True,
        )
        if not raw_dict or raw_dict.get("time") is None or raw_dict["time"].empty:
            return {"kline": None, "factor": None}

        daily_df = pd.DataFrame(
            {
                "date": raw_dict["time"].T[xt_code],
                "open": raw_dict["open"].T[xt_code],
                "close": raw_dict["close"].T[xt_code],
                "high": raw_dict["high"].T[xt_code],
                "low": raw_dict["low"].T[xt_code],
                "volume": raw_dict["volume"].T[xt_code],
                "amount": raw_dict["amount"].T[xt_code],
            }
        )
        daily_df["date"] = pd.to_datetime(daily_df["date"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
        daily_df = self._normalize_daily_kline(daily_df)

        factor_df = None
        try:
            adj_dict = xtdata.get_market_data(
                field_list=["time", "close"],
                stock_list=[xt_code],
                period="1d",
                start_time=start_time,
                end_time=end_time,
                dividend_type="front",
                fill_data=True,
            )
            if adj_dict and adj_dict.get("close") is not None and not adj_dict["close"].empty:
                factor_raw = pd.DataFrame(
                    {
                        "date": adj_dict["time"].T[xt_code],
                        "close_adj": adj_dict["close"].T[xt_code],
                    }
                )
                factor_raw["date"] = pd.to_datetime(factor_raw["date"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
                factor_df = self._normalize_factor_data(factor_raw, daily_df)
        except Exception as e:
            print(f"xtdata 复权因子获取失败: {e}")

        return {"kline": daily_df, "factor": factor_df}

    def _fetch_stock_bundle_mootdx(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> Dict[str, Optional[pd.DataFrame]]:
        if not MOOTDX_AVAILABLE:
            raise RuntimeError("mootdx 不可用，请先安装 mootdx。")

        client = None
        try:
            for kwargs in ({"market": "std"}, {}):
                try:
                    client = Quotes.factory(**kwargs)
                    break
                except TypeError:
                    continue

            if client is None:
                client = Quotes.factory()

            bars_df = None
            start_day = pd.to_datetime(start_date).strftime("%Y-%m-%d")
            requested_end = pd.to_datetime(end_date).normalize() if end_date else pd.Timestamp(datetime.now().date())
            end_day = (requested_end + timedelta(days=1)).strftime("%Y-%m-%d")

            if hasattr(client, "get_k_data"):
                try:
                    bars_df = client.get_k_data(stock_code, start_day, end_day)
                except Exception:
                    bars_df = None

            if bars_df is None or bars_df.empty:
                market_code = self._get_tdx_market_code(stock_code)
                attempts = [
                    {"symbol": stock_code, "frequency": 9, "offset": 8000, "market": market_code},
                    {"symbol": stock_code, "frequency": "day", "offset": 8000, "market": market_code},
                    {"symbol": stock_code, "frequency": 9, "offset": 8000},
                    {"symbol": stock_code, "frequency": "day", "offset": 8000},
                ]

                last_error = None
                for kwargs in attempts:
                    try:
                        candidate = client.bars(**kwargs)
                        if candidate is not None and not candidate.empty:
                            bars_df = candidate
                            break
                    except TypeError:
                        continue
                    except Exception as exc:
                        last_error = exc

                if (bars_df is None or bars_df.empty) and last_error is not None:
                    raise last_error

            if bars_df is None or bars_df.empty:
                return {"kline": None, "factor": None}

            daily_df = self._normalize_daily_kline(bars_df)
            if daily_df is None or daily_df.empty:
                return {"kline": None, "factor": None}

            daily_df = self._slice_date_range(
                daily_df,
                start_date=pd.to_datetime(start_date),
                end_date=pd.to_datetime(end_date) if end_date else requested_end,
            )
            return {"kline": daily_df, "factor": None}
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass

    def _fetch_factor_range(
        self,
        stock_code: str,
        source: str,
        start_date: str,
        end_date: str,
        base_df: Optional[pd.DataFrame] = None,
    ) -> Optional[pd.DataFrame]:
        if not self._supports_factor_refresh(source):
            return None

        start_dt = pd.to_datetime(start_date).normalize()
        end_dt = pd.to_datetime(end_date).normalize()
        base_frame = self._slice_date_range(base_df, start_dt, end_dt) if base_df is not None else None

        if source == "xtdata":
            if not XTDATA_AVAILABLE:
                return None

            xt_code = self._format_xt_code(stock_code)
            start_time = start_dt.strftime("%Y%m%d")
            end_time = end_dt.strftime("%Y%m%d")
            xtdata.download_history_data2(stock_list=[xt_code], period="1d", start_time=start_time, end_time=end_time)
            adj_dict = xtdata.get_market_data(
                field_list=["time", "close"],
                stock_list=[xt_code],
                period="1d",
                start_time=start_time,
                end_time=end_time,
                dividend_type="front",
                fill_data=True,
            )
            if not adj_dict or adj_dict.get("close") is None or adj_dict["close"].empty:
                return None

            factor_raw = pd.DataFrame(
                {
                    "date": adj_dict["time"].T[xt_code],
                    "close_adj": adj_dict["close"].T[xt_code],
                }
            )
            factor_raw["date"] = pd.to_datetime(factor_raw["date"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
            factor_df = self._normalize_factor_data(factor_raw, base_frame)
            return self._slice_date_range(factor_df, start_dt, end_dt)

        raw_qfq = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date=start_dt.strftime("%Y%m%d"),
            end_date=end_dt.strftime("%Y%m%d"),
            adjust="qfq",
        )
        factor_df = self._normalize_factor_data(raw_qfq, base_frame)
        return self._slice_date_range(factor_df, start_dt, end_dt)

    def _attach_factor_column(
        self,
        kline_df: pd.DataFrame,
        factor_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        merged = kline_df.copy().drop(columns=["factor"], errors="ignore")

        if factor_df is not None and not factor_df.empty:
            factors = factor_df[["date", "factor"]].copy()
            factors["date"] = pd.to_datetime(factors["date"])
            factors["factor"] = pd.to_numeric(factors["factor"], errors="coerce")
            merged = pd.merge(merged, factors, on="date", how="left")

        if "factor" not in merged.columns:
            merged["factor"] = 1.0

        merged["factor"] = pd.to_numeric(merged["factor"], errors="coerce").ffill().bfill().fillna(1.0)
        return merged

    def _save_bundle_to_cache(
        self,
        stock_code: str,
        kline_df: Optional[pd.DataFrame],
        factor_df: Optional[pd.DataFrame],
    ) -> bool:
        if kline_df is None or kline_df.empty:
            return False

        kline_path = os.path.join(self.kline_dir, f"{stock_code}.csv")
        save_kline = kline_df.copy()
        save_kline["date"] = save_kline["date"].dt.strftime("%Y-%m-%d")
        save_kline.to_csv(kline_path, index=False, encoding="utf-8")

        if factor_df is not None and not factor_df.empty:
            factor_path = os.path.join(self.factor_dir, f"{stock_code}.csv")
            save_factor = factor_df.copy()
            save_factor["date"] = save_factor["date"].dt.strftime("%Y-%m-%d")
            save_factor.to_csv(factor_path, index=False, encoding="utf-8")

        return True

    def download_stock_data(self, stock_code: str, start_date: str = "2010-01-01", source: str = "yfinance") -> bool:
        """下载并缓存单只股票的日线数据。"""
        stock_code = self._normalize_stock_code(stock_code)
        try:
            bundle = self._fetch_stock_bundle(stock_code, start_date, source)
            saved = self._save_bundle_to_cache(stock_code, bundle.get("kline"), bundle.get("factor"))
            if saved:
                self._last_data_error.pop(stock_code, None)
            return saved
        except Exception as e:
            self._last_data_error[stock_code] = str(e)
            print(f"下载股票 {stock_code} 数据失败: {e}")
            return False

    def _load_cached_kline(self, stock_code: str) -> Optional[pd.DataFrame]:
        kline_path = os.path.join(self.kline_dir, f"{stock_code}.csv")
        if not os.path.exists(kline_path):
            return None
        data = pd.read_csv(kline_path)
        return self._normalize_daily_kline(data)

    def _load_cached_factor(self, stock_code: str) -> Optional[pd.DataFrame]:
        factor_path = os.path.join(self.factor_dir, f"{stock_code}.csv")
        if not os.path.exists(factor_path):
            return None
        data = pd.read_csv(factor_path)
        return self._normalize_factor_data(data)

    def get_stock_data(
        self,
        stock_code: str,
        source: str = "yfinance",
        interval: str = "daily",
    ) -> Optional[pd.DataFrame]:
        """获取股票 K 线数据，支持 Yahoo 日K、小时K及周K合成。"""
        stock_code = self._normalize_stock_code(stock_code)
        if source not in {"yfinance", "offline"}:
            return None
        if interval == "hourly":
            if source != "yfinance":
                self._last_hourly_data_error[stock_code] = "本地离线数据暂不包含小时行情，请切换到 Yahoo Finance。"
                return None
            try:
                normalized = self._load_cached_hourly_kline(stock_code)
                if normalized is None:
                    normalized = self._fetch_hourly_kline(stock_code)
                    self._save_hourly_kline(stock_code, normalized)
                    self._refreshed_hourly_symbols.add(stock_code)
                elif stock_code not in self._refreshed_hourly_symbols:
                    # Refresh once per process so the most recent available bar
                    # appears while allowing the last valid cache to survive an outage.
                    try:
                        refreshed = self._fetch_hourly_kline(stock_code)
                        if refreshed is not None and not refreshed.empty:
                            normalized = refreshed
                            self._save_hourly_kline(stock_code, normalized)
                    except Exception as exc:
                        self._last_hourly_data_error[stock_code] = str(exc)
                        print(f"更新 {stock_code} 小时行情缓存失败，继续使用本地副本: {exc}")
                    finally:
                        self._refreshed_hourly_symbols.add(stock_code)
                self._last_hourly_data_error.pop(stock_code, None)
                return normalized
            except Exception as exc:
                self._last_hourly_data_error[stock_code] = str(exc)
                print(f"获取股票 {stock_code} 小时线失败: {exc}")
                return None
        try:
            if source == "offline":
                offline_path = self._get_offline_file(stock_code)
                if not offline_path:
                    return None
                data = self._read_csv_with_fallback(offline_path)
                normalized = self._normalize_offline_data(data)
            else:
                normalized = self._load_cached_kline(stock_code)
                if normalized is None:
                    if not self.download_stock_data(stock_code, start_date="2010-01-01", source=source):
                        return None
                    normalized = self._load_cached_kline(stock_code)
                elif source == "yfinance" and not normalized.empty and stock_code not in self._refreshed_symbols:
                    # Refresh full history once per process to keep older rows on
                    # the same Yahoo split-adjustment basis as newly fetched rows.
                    try:
                        bundle = self._fetch_stock_bundle(stock_code, "1900-01-01", source)
                        refreshed = bundle.get("kline")
                        if refreshed is not None and not refreshed.empty:
                            normalized = refreshed
                            self._save_bundle_to_cache(stock_code, normalized, bundle.get("factor"))
                    except Exception as exc:
                        print(f"更新 {stock_code} 行情缓存失败，继续使用本地副本: {exc}")
                    finally:
                        self._refreshed_symbols.add(stock_code)

            if normalized is None:
                return None

            normalized = normalized.drop(columns=["factor"], errors="ignore")

            if interval == "weekly":
                return self._resample_to_weekly(normalized)
            return normalized
        except Exception as e:
            print(f"获取股票 {stock_code} 数据失败: {e}")
            return None

    def get_factor_data(
        self,
        stock_code: str,
        source: str = "yfinance",
        interval: str = "daily",
    ) -> Optional[pd.DataFrame]:
        """获取复权因子数据，周K会按周最后一个交易日聚合。"""
        stock_code = self._normalize_stock_code(stock_code)
        if interval == "hourly":
            # Hourly bars already use Yahoo's current split-adjusted price basis;
            # joining daily adjustment factors would introduce lookahead.
            return None
        try:
            if source == "offline":
                offline_path = self._get_offline_file(stock_code)
                if not offline_path:
                    return None
                data = self._read_csv_with_fallback(offline_path)
                normalized = self._normalize_offline_data(data)
                if normalized is None or "factor" not in normalized.columns:
                    return None
                factor_df = normalized[["date", "factor"]].copy()
            else:
                factor_df = self._load_cached_factor(stock_code)

            if factor_df is None or factor_df.empty:
                return None

            if interval == "weekly":
                return self._resample_to_weekly(factor_df)
            return factor_df
        except Exception as e:
            print(f"获取复权因子 {stock_code} 失败: {e}")
            return None

    def get_dividend_data(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取除权除息数据。"""
        stock_code = self._normalize_stock_code(stock_code)
        try:
            dividend_path = os.path.join(self.dividend_dir, f"{stock_code}.csv")
            if os.path.exists(dividend_path):
                return pd.read_csv(dividend_path)
            return None
        except Exception as e:
            print(f"获取除权除息 {stock_code} 数据失败: {e}")
            return None

    def get_stock_name(self, stock_code: str) -> str:
        stock_code = self._normalize_stock_code(stock_code)
        if not self.stock_names:
            self.load_stock_list()
        return self.stock_names.get(stock_code, stock_code)

    def get_training_validation_error(
        self,
        stock_code: str,
        start_date: str,
        source: str = "yfinance",
        interval: str = "daily",
    ) -> Optional[str]:
        stock_code = self._normalize_stock_code(stock_code)
        if not stock_code:
            return "股票代码不能为空"

        try:
            start_dt = pd.to_datetime(start_date)
        except Exception:
            return "起始日期格式无效"

        if interval == "hourly":
            if source != "yfinance":
                return "本地离线数据暂不包含小时行情，请切换到 Yahoo Finance。"
            cutoff = self._market_now().tz_localize(None).normalize() - pd.Timedelta(days=730)
            if start_dt.normalize() < cutoff:
                return (
                    f"Yahoo Finance 的 60 分钟小时线约只提供最近 730 天，当前起始日期 {start_dt:%Y-%m-%d} "
                    f"早于可用范围；请将日期调整到 {cutoff:%Y-%m-%d} 之后，或改用日线训练。"
                )

        data = self.get_stock_data(stock_code, source=source, interval=interval)
        if data is None or data.empty:
            if source == "offline":
                if self._get_offline_file(stock_code) is None:
                    return "指定股票在离线数据中不存在"
                return "指定股票在离线数据中没有可用数据"
            if source == "yfinance" and stock_code in self._last_data_error:
                return f"Yahoo Finance 行情下载失败：{self._last_data_error[stock_code]}"
            if interval == "hourly" and stock_code in self._last_hourly_data_error:
                return f"Yahoo Finance 小时行情下载失败：{self._last_hourly_data_error[stock_code]}"
            return "指定股票暂无可用数据，请切换数据源或稍后重试"

        min_date = data["date"].min()
        max_date = data["date"].max()
        if interval == "hourly":
            # The user chooses an exchange date, not a wall-clock timestamp;
            # 00:00 is before that day's first 09:30 bar by design.
            requested_date = start_dt.normalize()
            available_start = min_date.normalize()
            available_end = max_date.normalize()
            out_of_range = requested_date < available_start or requested_date > available_end
        else:
            out_of_range = start_dt < min_date or start_dt > max_date
        if out_of_range:
            if source == "offline":
                return f"起始日期不在该股票离线数据范围内（{min_date:%Y-%m-%d} ~ {max_date:%Y-%m-%d}）"
            return f"起始日期不在该股票数据范围内（{min_date:%Y-%m-%d} ~ {max_date:%Y-%m-%d}）"

        return None

    def validate_stock_and_date(
        self,
        stock_code: str,
        start_date: str,
        source: str = "yfinance",
        interval: str = "daily",
    ) -> bool:
        """验证股票代码和起始日期是否有效。"""
        return self.get_training_validation_error(
            stock_code,
            start_date,
            source=source,
            interval=interval,
        ) is None

    def get_random_stock(
        self,
        sector: str = "all",
        date_start: str = "2024-01-01",
        date_end: str = "2026-01-01",
        source: str = "yfinance",
        interval: str = "daily",
    ) -> Tuple[str, str]:
        """随机选择股票与起始日期。"""
        range_start, range_end = self._resolve_training_range(date_start, date_end)

        if source == "offline":
            stock_codes = self._filter_stock_codes_by_sector(self._get_offline_stock_codes(), sector)
            candidates: List[Tuple[str, pd.Timestamp, pd.Timestamp]] = []

            for stock_code in stock_codes:
                date_range = self._get_offline_date_range(stock_code)
                if date_range is None:
                    continue
                stock_start, stock_end = date_range
                available_start = max(range_start, stock_start)
                available_end = min(range_end, stock_end)
                if available_start <= available_end:
                    candidates.append((stock_code, available_start, available_end))

            if not candidates:
                raise ValueError("离线数据中没有符合板块与日期范围的股票")

            stock_code, available_start, available_end = random.choice(candidates)
            return stock_code, self._random_date_in_range(available_start, available_end)

        if self.stock_list is None:
            self.load_stock_list()

        if self.stock_list is None or self.stock_list.empty:
            raise ValueError("股票列表为空，无法随机选择股票")

        stock_codes = self.stock_list["code"].dropna().astype(str).drop_duplicates().tolist()
        stock_codes = self._filter_stock_codes_by_sector(stock_codes, sector)
        if not stock_codes:
            raise ValueError("所选板块下没有可用股票")

        random.shuffle(stock_codes)
        max_attempts = min(len(stock_codes), 80)
        for stock_code in stock_codes[:max_attempts]:
            date_range = self._get_stock_date_range(stock_code, source=source, interval=interval)
            if date_range is None:
                continue
            stock_start, stock_end = date_range
            available_start = max(range_start, stock_start)
            available_end = min(range_end, stock_end)
            if available_start <= available_end:
                return stock_code, self._random_date_in_range(available_start, available_end)

        raise ValueError("所选数据源中没有符合条件的股票，请调整板块或日期范围后重试")

    def _build_offline_path(self, stock_code: str) -> str:
        stock_code = self._normalize_stock_code(stock_code)
        existing_path = self._get_offline_file(stock_code)
        if existing_path:
            return existing_path
        return os.path.join(self.offline_dir, f"{stock_code}.csv")

    def sync_offline_data(
        self,
        stock_code: str,
        source: str = "yfinance",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        force_full: bool = False,
    ) -> Dict:
        """将在线数据源按日期区间增量同步到离线目录。"""
        stock_code = self._normalize_stock_code(stock_code)
        if source == "offline":
            raise ValueError("离线数据不能作为在线补数源。")

        existing = self.get_stock_data(stock_code, source="offline", interval="daily")
        existing_factor = self.get_factor_data(stock_code, source="offline", interval="daily")
        request_start, request_end = self._normalize_sync_range(start_date=start_date, end_date=end_date)
        offline_path = self._build_offline_path(stock_code)

        range_before = None
        previous_rows = 0
        segments_to_fetch: List[Tuple[pd.Timestamp, pd.Timestamp]] = []

        if existing is not None and not existing.empty:
            existing = existing.copy()
            existing["date"] = pd.to_datetime(existing["date"])
            previous_rows = len(existing)
            local_start = existing["date"].min().normalize()
            local_end = existing["date"].max().normalize()
            range_before = {
                "start": local_start.strftime("%Y-%m-%d"),
                "end": local_end.strftime("%Y-%m-%d"),
            }

            if force_full:
                segments_to_fetch.append((request_start, request_end))
            elif source == "yfinance":
                # Yahoo rescales historical prices after splits. Re-download the
                # full local span in one request before writing a coherent snapshot.
                segments_to_fetch.append((min(request_start, local_start), max(request_end, local_end)))
            else:
                head_end = min(request_end, local_start - timedelta(days=1))
                if request_start <= head_end:
                    segments_to_fetch.append((request_start, head_end))

                tail_start = max(request_start, local_end + timedelta(days=1))
                if tail_start <= request_end:
                    segments_to_fetch.append((tail_start, request_end))
        else:
            segments_to_fetch.append((request_start, request_end))

        planned_ranges: List[Dict[str, str]] = []
        fetched_ranges: List[Dict[str, str]] = []
        missing_ranges: List[Dict[str, str]] = []
        fetched_kline_parts: List[pd.DataFrame] = []
        fetched_factor_parts: List[pd.DataFrame] = []

        for segment_start, segment_end in segments_to_fetch:
            if segment_end < segment_start:
                continue

            planned_ranges.append(
                {
                    "start": segment_start.strftime("%Y-%m-%d"),
                    "end": segment_end.strftime("%Y-%m-%d"),
                }
            )

            bundle = self._fetch_stock_bundle(
                stock_code,
                segment_start.strftime("%Y-%m-%d"),
                source,
                end_date=segment_end.strftime("%Y-%m-%d"),
            )
            segment_kline = self._slice_date_range(bundle.get("kline"), segment_start, segment_end)
            segment_factor = self._slice_date_range(bundle.get("factor"), segment_start, segment_end)

            if segment_kline is not None and not segment_kline.empty:
                fetched_kline_parts.append(segment_kline)
                fetched_ranges.append(
                    {
                        "start": pd.to_datetime(segment_kline["date"]).min().strftime("%Y-%m-%d"),
                        "end": pd.to_datetime(segment_kline["date"]).max().strftime("%Y-%m-%d"),
                    }
                )
            else:
                missing_ranges.append(
                    {
                        "start": segment_start.strftime("%Y-%m-%d"),
                        "end": segment_end.strftime("%Y-%m-%d"),
                    }
                )

            if segment_factor is not None and not segment_factor.empty:
                fetched_factor_parts.append(segment_factor)

        fetched_rows = sum(len(part) for part in fetched_kline_parts)

        if previous_rows > 0 and fetched_rows == 0 and not force_full:
            return {
                "success": True,
                "stock_code": stock_code,
                "source": source,
                "message": "在线源在待补区间未返回新数据，本地离线文件保持不变。",
                "added_rows": 0,
                "fetched_rows": 0,
                "rows_before": previous_rows,
                "rows_after": previous_rows,
                "total_rows": previous_rows,
                "latest_date": existing["date"].max().strftime("%Y-%m-%d"),
                "offline_path": offline_path,
                "requested_range": {
                    "start": request_start.strftime("%Y-%m-%d"),
                    "end": request_end.strftime("%Y-%m-%d"),
                },
                "range_before": range_before,
                "range_after": range_before,
                "planned_ranges": planned_ranges,
                "fetched_ranges": fetched_ranges,
                "missing_ranges": missing_ranges,
                "factor_refresh_mode": "unchanged",
                "local_file_changed": False,
            }

        if force_full:
            merged = pd.concat(fetched_kline_parts, ignore_index=True) if fetched_kline_parts else pd.DataFrame()
        elif existing is not None and not existing.empty:
            merged = pd.concat([existing] + fetched_kline_parts, ignore_index=True) if (existing is not None or fetched_kline_parts) else pd.DataFrame()
        else:
            merged = pd.concat(fetched_kline_parts, ignore_index=True) if fetched_kline_parts else pd.DataFrame()

        merged = self._normalize_daily_kline(merged) if merged is not None and not merged.empty else None

        if merged is None or merged.empty:
            if existing is not None and not existing.empty and not force_full:
                snapshot = self._attach_factor_column(existing, existing_factor)
                snapshot["source"] = source
                snapshot["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                save_snapshot = snapshot.copy()
                save_snapshot["date"] = save_snapshot["date"].dt.strftime("%Y-%m-%d")
                save_snapshot.to_csv(offline_path, index=False, encoding="utf-8")
                self._offline_stock_codes_cache = None
                self._offline_date_range_cache.pop(stock_code, None)

                return {
                    "success": True,
                    "stock_code": stock_code,
                    "source": source,
                    "message": "本地数据已覆盖所选区间，无需新增请求。",
                    "added_rows": 0,
                    "fetched_rows": 0,
                    "total_rows": len(snapshot),
                    "latest_date": snapshot["date"].max().strftime("%Y-%m-%d"),
                    "offline_path": offline_path,
                    "requested_range": {
                        "start": request_start.strftime("%Y-%m-%d"),
                        "end": request_end.strftime("%Y-%m-%d"),
                    },
                    "range_before": range_before,
                    "range_after": range_before,
                    "planned_ranges": planned_ranges,
                    "fetched_ranges": fetched_ranges,
                    "missing_ranges": missing_ranges,
                    "factor_refresh_mode": "unchanged",
                    "local_file_changed": False,
                }

            return {
                "success": True,
                "stock_code": stock_code,
                "source": source,
                "message": "没有获取到符合日期区间的在线数据。",
                "added_rows": 0,
                "fetched_rows": 0,
                "latest_date": existing["date"].max().strftime("%Y-%m-%d") if existing is not None and not existing.empty else None,
                "offline_path": offline_path,
                "requested_range": {
                    "start": request_start.strftime("%Y-%m-%d"),
                    "end": request_end.strftime("%Y-%m-%d"),
                },
                "range_before": range_before,
                "range_after": range_before,
                "planned_ranges": planned_ranges,
                "fetched_ranges": fetched_ranges,
                "missing_ranges": missing_ranges,
                "factor_refresh_mode": "none",
                "local_file_changed": False,
            }

        factor_refresh_mode = "carried_forward"
        merged_factor = None
        if self._supports_factor_refresh(source):
            try:
                merged_factor = self._fetch_factor_range(
                    stock_code=stock_code,
                    source=source,
                    start_date=merged["date"].min().strftime("%Y-%m-%d"),
                    end_date=merged["date"].max().strftime("%Y-%m-%d"),
                    base_df=merged,
                )
                if merged_factor is not None and not merged_factor.empty:
                    factor_refresh_mode = "full_range_refreshed"
            except Exception as exc:
                print(f"刷新 {stock_code} 全量复权因子失败: {exc}")
                merged_factor = None

        if merged_factor is None or merged_factor.empty:
            factor_candidates: List[pd.DataFrame] = []
            if not force_full and existing_factor is not None and not existing_factor.empty:
                factor_candidates.append(existing_factor)
            factor_candidates.extend(part for part in fetched_factor_parts if part is not None and not part.empty)
            merged_factor = pd.concat(factor_candidates, ignore_index=True) if factor_candidates else None
            if merged_factor is not None and not merged_factor.empty:
                merged_factor = merged_factor.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
            else:
                factor_refresh_mode = "default_ones"

        merged = self._attach_factor_column(merged, merged_factor)

        merged["source"] = source
        merged["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        save_df = merged.copy()
        save_df["date"] = save_df["date"].dt.strftime("%Y-%m-%d")
        save_df.to_csv(offline_path, index=False, encoding="utf-8")
        self._offline_stock_codes_cache = None
        self._offline_date_range_cache.pop(stock_code, None)

        added_rows = max(0, len(merged) - previous_rows)
        range_after = {
            "start": merged["date"].min().strftime("%Y-%m-%d"),
            "end": merged["date"].max().strftime("%Y-%m-%d"),
        }

        if force_full:
            message = "已按所选日期区间全量重建离线数据。"
        elif fetched_rows > 0:
            message = "已按本地时间范围增量补齐离线数据。"
        else:
            message = "本地数据已覆盖所选区间，仅刷新了离线文件。"

        return {
            "success": True,
            "stock_code": stock_code,
            "source": source,
            "message": message,
            "added_rows": added_rows,
            "fetched_rows": fetched_rows,
            "rows_before": previous_rows,
            "rows_after": len(merged),
            "total_rows": len(merged),
            "latest_date": merged["date"].max().strftime("%Y-%m-%d"),
            "offline_path": offline_path,
            "requested_range": {
                "start": request_start.strftime("%Y-%m-%d"),
                "end": request_end.strftime("%Y-%m-%d"),
            },
            "range_before": range_before,
            "range_after": range_after,
            "planned_ranges": planned_ranges,
            "fetched_ranges": fetched_ranges,
            "missing_ranges": missing_ranges,
            "factor_refresh_mode": factor_refresh_mode,
            "local_file_changed": True,
        }

    def batch_download_data(self, stock_codes: List[str] = None, start_date: str = "2010-01-01"):
        """批量下载股票数据。"""
        if stock_codes is None:
            if self.stock_list is None:
                self.load_stock_list()
            stock_codes = self.stock_list["code"].tolist()

        total = len(stock_codes)
        success_count = 0

        for index, stock_code in enumerate(stock_codes, start=1):
            print(f"进度: {index}/{total} - {stock_code}")
            if self.download_stock_data(stock_code, start_date):
                success_count += 1

        print(f"批量下载完成，成功 {success_count}/{total}")
        return success_count, total


def download_all_data():
    """示例脚本：下载部分股票日线缓存。"""
    data_manager = DataManager()
    data_manager.download_stock_list()
    sample_stocks = ["000001", "000002", "600000", "600036", "000858", "002415", "300059", "300750"]
    data_manager.batch_download_data(sample_stocks)


if __name__ == "__main__":
    download_all_data()
