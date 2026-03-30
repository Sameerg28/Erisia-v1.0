from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, cast

import pandas as pd

DEFAULT_DB_NAME = "oracle_memory.db"


@dataclass
class DatabaseConfig:
    """Configuration for the Oracle SQLite ledger."""

    path: Path = Path(__file__).resolve().parents[2] / DEFAULT_DB_NAME


class TechnicalAnalyst:
    """Computes technical indicators (SMA, RSI) and persists them to SQLite."""

    REQUIRED_COLUMNS = ("sma_5", "sma_14", "rsi_14")

    def __init__(self, db_config: Optional[DatabaseConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.db_config = db_config or DatabaseConfig()
        self.logger = logger or logging.getLogger("oracle.math")
        self._ensure_db_dir()
        self._ensure_indicator_columns()

    def _ensure_db_dir(self) -> None:
        self.db_config.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_config.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_indicator_columns(self) -> None:
        with self._connect() as conn:
            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(market_data);")}
            missing = [col for col in self.REQUIRED_COLUMNS if col not in existing_cols]
            for col in missing:
                conn.execute(f"ALTER TABLE market_data ADD COLUMN {col} REAL;")
                self.logger.info("Added missing column '%s' to market_data", col)
            if missing:
                conn.commit()

    def calculate_indicators(self, ticker: str) -> int:
        """Calculate SMA and RSI for a ticker and persist them. Returns rows updated."""
        df = self._load_price_history(ticker)
        if df.empty:
            self.logger.warning("No price history found for %s", ticker)
            return 0

        df["sma_5"] = df["close"].rolling(window=5, min_periods=1).mean()
        df["sma_14"] = df["close"].rolling(window=14, min_periods=1).mean()
        df["rsi_14"] = self._compute_rsi(df["close"], period=14)

        updates: List[Tuple[Optional[float], Optional[float], Optional[float], str, str]] = []
        for _, row in df.iterrows():
            updates.append(
                (
                    self._nan_to_none(row["sma_5"]),
                    self._nan_to_none(row["sma_14"]),
                    self._nan_to_none(row["rsi_14"]),
                    ticker.upper(),
                    row["date"],
                )
            )

        with self._connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                UPDATE market_data
                SET sma_5 = ?, sma_14 = ?, rsi_14 = ?
                WHERE ticker = ? AND date = ?;
                """,
                updates,
            )
            conn.commit()
            updated = conn.total_changes - before
            self.logger.info("Updated %s rows with indicators for %s", updated, ticker.upper())
            return updated

    def _load_price_history(self, ticker: str) -> pd.DataFrame:
        query = """
            SELECT date, close
            FROM market_data
            WHERE ticker = ?
            ORDER BY date ASC;
        """
        with self._connect() as conn:
            rows = conn.execute(query, (ticker.upper(),)).fetchall()
        if not rows:
            return pd.DataFrame(columns=["date", "close"])
        df = pd.DataFrame(rows, columns=["date", "close"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df

    @staticmethod
    def _compute_rsi(close_series: pd.Series, period: int = 14) -> pd.Series:
        """Classic RSI calculation using Wilder's smoothing."""
        delta = close_series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def _nan_to_none(value: object) -> Optional[float]:
        try:
            if value is None:
                return None
            if isinstance(value, (int, float)):
                fval = float(value)
            else:
                fval = float(str(value))
        except (TypeError, ValueError):
            return None
        if pd.isna(fval):
            return None
        return fval

    def fetch_tail(self, ticker: str, limit: int = 5) -> Sequence[sqlite3.Row]:
        query = """
            SELECT date, close, sma_5, sma_14, rsi_14
            FROM market_data
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT ?;
        """
        with self._connect() as conn:
            cursor = conn.execute(query, (ticker.upper(), limit))
            return cursor.fetchall()

import pandas as pd
import numpy as np
import math


class QuantitativeEngine:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger

    def _to_float(self, value: object) -> float:
        try:
            if value is None:
                return 0.0
            if isinstance(value, (np.floating, np.integer)):
                scalar: float | int = value.item()
                value_f = float(scalar)
            elif isinstance(value, (float, int, str, bytes)):
                value_f = float(value)
            else:
                return 0.0
            if math.isnan(value_f) or math.isinf(value_f):
                return 0.0
            return value_f
        except (TypeError, ValueError):
            return 0.0

    def enrich_with_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of the dataframe enriched with technical indicator columns."""
        if df.empty:
            return df.copy()

        try:
            data = df.copy()
            close = cast(pd.Series, pd.to_numeric(data["Close"], errors="coerce"))
            high = cast(pd.Series, pd.to_numeric(data["High"], errors="coerce"))
            low = cast(pd.Series, pd.to_numeric(data["Low"], errors="coerce"))
            volume = cast(pd.Series, pd.to_numeric(data["Volume"], errors="coerce"))
            data["Close"] = close
            data["High"] = high
            data["Low"] = low
            data["Volume"] = volume
            data["Bar_Count"] = np.arange(1, len(data) + 1, dtype=float)

            data["SMA_5"] = close.rolling(window=5, min_periods=5).mean()
            data["SMA_14"] = close.rolling(window=14, min_periods=14).mean()

            delta = close.diff()
            gain = delta.clip(lower=0.0)
            loss = (-delta).clip(lower=0.0)
            avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            rs = avg_gain / avg_loss.replace(0.0, np.nan)
            data["RSI_14"] = 100.0 - (100.0 / (1.0 + rs))

            data["EMA_9"] = close.ewm(span=9, adjust=False).mean()
            data["EMA_21"] = close.ewm(span=21, adjust=False).mean()

            ema_12 = close.ewm(span=12, adjust=False).mean()
            ema_26 = close.ewm(span=26, adjust=False).mean()
            data["MACD_Line"] = ema_12 - ema_26
            data["MACD_Signal"] = data["MACD_Line"].ewm(span=9, adjust=False).mean()
            data["MACD_Histogram"] = data["MACD_Line"] - data["MACD_Signal"]

            data["Avg_Volume_20"] = volume.rolling(window=20, min_periods=20).mean()
            data["Volume_Surge"] = volume / data["Avg_Volume_20"].replace(0.0, np.nan)

            # ═══════════════════════════════════════════════
            # ADX — Wilder's Smoothing (pure numpy, proven)
            # ═══════════════════════════════════════════════
            _period = 14

            _high = high.to_numpy(dtype=float)
            _low = low.to_numpy(dtype=float)
            _close = close.to_numpy(dtype=float)
            _n = len(_high)

            # True Range
            _tr = np.zeros(_n)
            for _i in range(1, _n):
                _hl  = _high[_i]  - _low[_i]
                _hpc = abs(_high[_i]  - _close[_i - 1])
                _lpc = abs(_low[_i]   - _close[_i - 1])
                _tr[_i] = max(_hl, _hpc, _lpc)
            _tr[0] = _high[0] - _low[0]

            # Directional Movement
            _plus_dm  = np.zeros(_n)
            _minus_dm = np.zeros(_n)
            for _i in range(1, _n):
                _up   =  _high[_i] - _high[_i - 1]
                _down = _low[_i - 1] - _low[_i]
                if _up > _down and _up > 0:
                    _plus_dm[_i] = _up
                if _down > _up and _down > 0:
                    _minus_dm[_i] = _down

            # Wilder's smoothing: seed=sum(first 14), then prev*(13/14)+curr
            def _ws(arr: np.ndarray, p: int) -> np.ndarray:
                out = np.zeros(len(arr))
                if len(arr) < p:
                    return out
                out[p - 1] = arr[:p].sum()
                k = (p - 1.0) / p
                for _j in range(p, len(arr)):
                    out[_j] = out[_j - 1] * k + arr[_j]
                return out

            def _expanding_rank_pct(series: pd.Series) -> pd.Series:
                values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
                percentiles = np.full(len(values), np.nan, dtype=float)
                finite_values = sorted({float(value) for value in values if np.isfinite(value)})
                if not finite_values:
                    return pd.Series(percentiles, index=series.index, dtype=float)

                value_to_index = {value: index + 1 for index, value in enumerate(finite_values)}
                bit = np.zeros(len(finite_values) + 1, dtype=np.int64)
                counts = np.zeros(len(finite_values) + 1, dtype=np.int64)
                total = 0

                def _update(bit_index: int) -> None:
                    while bit_index < len(bit):
                        bit[bit_index] += 1
                        bit_index += bit_index & -bit_index

                def _query(bit_index: int) -> int:
                    result = 0
                    while bit_index > 0:
                        result += int(bit[bit_index])
                        bit_index -= bit_index & -bit_index
                    return result

                for value_index, value in enumerate(values):
                    if not np.isfinite(value):
                        continue
                    compressed_index = value_to_index[float(value)]
                    total += 1
                    counts[compressed_index] += 1
                    _update(compressed_index)

                    if total <= 1:
                        percentiles[value_index] = 0.5
                        continue

                    lower_count = _query(compressed_index - 1)
                    same_value_count = counts[compressed_index]
                    average_rank = lower_count + ((same_value_count + 1) / 2.0)
                    percentiles[value_index] = average_rank / total

                return pd.Series(percentiles, index=series.index, dtype=float)

            _sm_tr   = _ws(_tr,       _period)
            _sm_pdm  = _ws(_plus_dm,  _period)
            _sm_mdm  = _ws(_minus_dm, _period)

            # Update data frame for ATR_14 (average, not sum)
            data["ATR_14"] = _sm_tr / _period

            # +DI and -DI
            _plus_di_arr = np.zeros(_n)
            _minus_di_arr = np.zeros(_n)
            np.divide(100.0 * _sm_pdm, _sm_tr, out=_plus_di_arr, where=_sm_tr > 0)
            np.divide(100.0 * _sm_mdm, _sm_tr, out=_minus_di_arr, where=_sm_tr > 0)

            # DX
            _di_sum = _plus_di_arr + _minus_di_arr
            _di_diff = np.abs(_plus_di_arr - _minus_di_arr)
            _dx = np.zeros(_n)
            np.divide(100.0 * _di_diff, _di_sum, out=_dx, where=_di_sum > 0)

            data["ADX_14"] = np.clip(_ws(_dx, _period) / _period, 0.0, 100.0)
            data["Plus_DI"] = np.clip(_plus_di_arr, 0.0, 100.0)
            data["Minus_DI"] = np.clip(_minus_di_arr, 0.0, 100.0)

            bb_mid = close.rolling(20).mean()
            bb_std = close.rolling(20).std(ddof=0)
            bb_upper = bb_mid + 2.0 * bb_std
            bb_lower = bb_mid - 2.0 * bb_std
            data["BB_Mid"] = bb_mid
            data["BB_Upper"] = bb_upper
            data["BB_Lower"] = bb_lower
            data["BB_Width"] = (bb_upper - bb_lower) / bb_mid.replace(0.0, np.nan)
            data["BB_Pct_B"] = (close - bb_lower) / (bb_upper - bb_lower).replace(0.0, np.nan)
            data["BB_Width_Pct"] = _expanding_rank_pct(data["BB_Width"])

            price_delta = close.diff()
            direction = cast(
                pd.Series,
                price_delta.gt(0.0).astype(float) - price_delta.lt(0.0).astype(float),
            )
            obv = cast(pd.Series, (direction * volume).cumsum())
            obv_ema = cast(pd.Series, obv.ewm(span=21, adjust=False).mean())
            data["OBV"] = obv
            data["OBV_Trend"] = np.where(obv > obv_ema, 1.0, np.where(obv < obv_ema, -1.0, 0.0))

            return data
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return df.copy()

    def _row_to_metrics_dict(self, row: pd.Series) -> dict[str, float]:
        """Convert an enriched dataframe row into the legacy metrics payload."""
        required_columns = (
            "Close",
            "Volume_Surge",
            "SMA_5",
            "SMA_14",
            "RSI_14",
            "EMA_9",
            "EMA_21",
            "MACD_Line",
            "MACD_Signal",
            "MACD_Histogram",
            "ATR_14",
            "ADX_14",
            "Plus_DI",
            "Minus_DI",
            "BB_Upper",
            "BB_Lower",
            "BB_Mid",
            "BB_Width",
            "BB_Pct_B",
            "BB_Width_Pct",
            "OBV",
            "OBV_Trend",
            "Bar_Count",
        )
        if any(column not in row.index for column in required_columns):
            return {}

        return {
            "close": self._to_float(row.get("Close")),
            "volume_surge": self._to_float(row.get("Volume_Surge")),
            "sma_5": self._to_float(row.get("SMA_5")),
            "sma_14": self._to_float(row.get("SMA_14")),
            "rsi_14": self._to_float(row.get("RSI_14")),
            "ema_9": self._to_float(row.get("EMA_9")),
            "ema_21": self._to_float(row.get("EMA_21")),
            "macd_line": self._to_float(row.get("MACD_Line")),
            "macd_signal": self._to_float(row.get("MACD_Signal")),
            "macd_histogram": self._to_float(row.get("MACD_Histogram")),
            "atr_14": self._to_float(row.get("ATR_14")),
            "adx_14": self._to_float(row.get("ADX_14")),
            "plus_di": self._to_float(row.get("Plus_DI")),
            "minus_di": self._to_float(row.get("Minus_DI")),
            "bb_upper": self._to_float(row.get("BB_Upper")),
            "bb_lower": self._to_float(row.get("BB_Lower")),
            "bb_mid": self._to_float(row.get("BB_Mid")),
            "bb_width": self._to_float(row.get("BB_Width")),
            "bb_pct_b": self._to_float(row.get("BB_Pct_B")),
            "bb_width_pct": (
                0.5 if pd.isna(row.get("BB_Width_Pct")) else self._to_float(row.get("BB_Width_Pct"))
            ),
            "obv": self._to_float(row.get("OBV")),
            "obv_trend": self._to_float(row.get("OBV_Trend")),
            "bar_count": self._to_float(row.get("Bar_Count")),
        }

    def calculate_indicators(self, df: pd.DataFrame) -> dict[str, float]:
        """Legacy wrapper that enriches the dataframe and returns the latest metrics."""
        if df.empty:
            return {}

        try:
            enriched = self.enrich_with_indicators(df)
            if enriched.empty:
                return {}
            last_row = enriched.iloc[-1]
            return self._row_to_metrics_dict(last_row)
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
            return {}

def configure_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    return logging.getLogger("oracle.math")


def run_demo() -> None:
    logger = configure_logger()
    analyst = TechnicalAnalyst(logger=logger)
    try:
        analyst.calculate_indicators("AAPL")
    except Exception:
        logger.exception("Indicator calculation failed.")
        return

    print("\nLast 5 rows for AAPL:")
    tail_rows = list(analyst.fetch_tail("AAPL", limit=5))
    for row in reversed(tail_rows):  # show in chronological order
        print(
            {
                "date": row["date"],
                "close": row["close"],
                "sma_5": row["sma_5"],
                "rsi_14": row["rsi_14"],
            }
        )


if __name__ == "__main__":
    run_demo()
