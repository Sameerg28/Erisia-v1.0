"""Production-grade historical backtesting engine for the Erisia Oracle."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
import logging
import math
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
import sqlite3
from typing import Any, Sequence, cast
import uuid

import numpy as np
import pandas as pd
import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
for _path_entry in (PROJECT_ROOT, SRC_DIR):
    _path_entry_str = str(_path_entry)
    if _path_entry_str not in sys.path:
        sys.path.insert(0, _path_entry_str)

from oracle.oracle_math_engine import QuantitativeEngine

LOGGER_NAME = "erisia.backtester"
DEFAULT_DATABASE_NAME = "oracle_memory.db"
DATABASE_PATH = PROJECT_ROOT / DEFAULT_DATABASE_NAME
PORTFOLIO_PATH = PROJECT_ROOT / "config" / "portfolio.json"
OUTPUT_DIRECTORY = PROJECT_ROOT / "data" / "backtest_results"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d"
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
ISO_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
REQUIRED_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
MIN_HISTORY_ROWS = 60
SIGNAL_WARMUP_BARS = 30
RSI_OVERSOLD_THRESHOLD = 30.0
RSI_OVERBOUGHT_THRESHOLD = 70.0
VOLUME_SURGE_THRESHOLD = 2.0
ATR_HOLD_THRESHOLD = 0.04
ADX_TREND_THRESHOLD = 25.0
ADX_STRONG_THRESHOLD = 30.0
VOLATILITY_WINDOW = 20
SMA_REGIME_FAST_WINDOW = 50
SMA_REGIME_SLOW_WINDOW = 200
VOLATILITY_QUANTILE = 0.60
SIDEWAYS_THRESHOLD = 0.02
BB_HIGH_VOL_PERCENTILE = 0.75
DEFAULT_INITIAL_CAPITAL = 100_000.0
DEFAULT_COMMISSION_RATE = 0.001
DEFAULT_SLIPPAGE_RATE = 0.0005
MAX_POSITION_FRACTION = 0.20
REGIME_POSITION_MULTIPLIER: dict[str, float] = {
    "BULL": 1.0,
    "BEAR": 1.0,
    "SIDEWAYS": 0.75,
    "VOLATILE": 0.50,
    "UNKNOWN": 0.0,
}
SHORT_MARGIN_REQUIREMENT: float = 1.0
SHORT_BORROW_COST_DAILY: float = 0.0001
MAX_SIMULTANEOUS_POSITIONS: int = 1
STOP_LOSS_THRESHOLD = 0.05
MOMENTUM_STOP_LOSS_THRESHOLD = 0.03
TAKE_PROFIT_THRESHOLD = 0.15
MAX_HOLD_BARS = 20
DAILY_RISK_FREE_RATE = 0.04 / 252.0
TRADING_DAYS_PER_YEAR = 252.0
EPSILON = 1e-12
MAX_CONFIDENCE = 100
PERCENT_SCALE = 100.0
BUY_PRIMARY_CONFIDENCE = 72
BUY_SECONDARY_CONFIDENCE = 65
VOLATILE_MIN_CONFIDENCE = 68
DEFAULT_MIN_CONFIDENCE = 52
HOLD_VOLATILITY_CONFIDENCE = 40
HOLD_DEFAULT_CONFIDENCE = 50
NO_SIGNAL_CONFIDENCE = 0
FALLBACK_REASON = "LLM_FALLBACK"
SUMMARY_ROW_TICKER = "SUMMARY"
SUMMARY_EXIT_REASON = "SUMMARY"
FINAL_LIQUIDATION_LOG_TEMPLATE = "Liquidating residual %s position at final close due end of data."
CHART_JS_CDN_URL = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"
HTML_BACKGROUND_COLOR = "#0d1117"
HTML_PANEL_COLOR = "#111827"
HTML_BORDER_COLOR = "#243244"
HTML_TEXT_COLOR = "#e6edf3"
HTML_MUTED_TEXT_COLOR = "#8b9bb0"
HTML_EQUITY_COLOR = "#4fd1c5"
HTML_DRAWDOWN_COLOR = "#f87171"
HTML_GRID_COLOR = "rgba(139, 155, 176, 0.18)"
HTML_BULL_BAND = "rgba(74, 222, 128, 0.14)"
HTML_BEAR_BAND = "rgba(248, 113, 113, 0.14)"
HTML_SIDEWAYS_BAND = "rgba(250, 204, 21, 0.14)"
HTML_VOLATILE_BAND = "rgba(251, 146, 60, 0.16)"
HTML_UNKNOWN_BAND = "rgba(148, 163, 184, 0.10)"
LLM_FAILURE_REASON_MARKERS = (
    "configured",
    "call failed",
    "invalid model response",
    "cannot call",
)


class BacktestMode(StrEnum):
    QUANT_ONLY = "QUANT_ONLY"
    FULL_PIPELINE = "FULL_PIPELINE"


class SignalAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class PositionDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class MarketRegime(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


class ExitReason(StrEnum):
    SIGNAL = "SIGNAL"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    MAX_HOLD = "MAX_HOLD"


@dataclass(slots=True)
class TradeRecord:
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    pnl_pct: float
    signal: str
    confidence: int
    reasoning: str
    regime: str
    hold_bars: int
    exit_reason: str
    direction: str


@dataclass(slots=True)
class BacktestReport:
    ticker: str
    mode: str
    total_trades: int
    win_rate: float
    avg_pnl_per_trade: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration: int
    profit_factor: float
    avg_hold_bars: float
    regime_breakdown: dict[str, dict[str, int | float]]
    exit_reason_breakdown: dict[str, int]
    best_trade: TradeRecord | None
    worst_trade: TradeRecord | None


@dataclass(slots=True)
class RunRequest:
    ticker: str
    start: str
    end: str
    mode: BacktestMode
    capital: float
    interval: str


@dataclass(slots=True)
class RunArtifacts:
    trades_csv: Path | None
    report_json: Path | None
    equity_html: Path | None


@dataclass(slots=True)
class RunResult:
    request: RunRequest
    report: BacktestReport | None
    artifacts: RunArtifacts | None


@dataclass(slots=True)
class PendingEntry:
    execute_index: int
    signal: str
    confidence: int
    reasoning: str
    regime: str
    allocation_value: float
    direction: str = PositionDirection.LONG.value


@dataclass(slots=True)
class PendingExit:
    execute_index: int
    exit_reason: ExitReason


@dataclass(slots=True)
class PositionState:
    ticker: str
    entry_index: int
    entry_date: str
    entry_price: float
    shares: float
    entry_cost: float
    signal: str
    confidence: int
    reasoning: str
    regime: str
    direction: str


def classify_signal_intent(
    signal: str,
    regime: str,
    has_open_position: bool,
    open_position_direction: str,
) -> str:
    """Classify a raw signal into an entry, exit, or hold intent."""
    if signal == SignalAction.BUY.value:
        if not has_open_position:
            # Never enter long in a bear market
            if regime == MarketRegime.BEAR.value:
                return "HOLD"
            return "ENTER_LONG"
        if open_position_direction == PositionDirection.SHORT.value:
            return "EXIT_SHORT"
        return "HOLD"

    if signal == SignalAction.SELL.value:
        if not has_open_position:
            if regime in (MarketRegime.BEAR.value, MarketRegime.VOLATILE.value):
                return "ENTER_SHORT"
            return "HOLD"
        if open_position_direction == PositionDirection.LONG.value:
            return "EXIT_LONG"
        return "HOLD"

    return "HOLD"


class DataLayer:
    """Loads and validates historical OHLCV market data."""

    def __init__(self, ticker: str, start: str, end: str, interval: str = "1d", logger: logging.Logger | None = None) -> None:
        self.ticker = ticker.upper()
        self.start = start
        self.end = end
        self.interval = interval
        self.logger = logger or logging.getLogger(LOGGER_NAME)

    def load(self) -> pd.DataFrame:
        """Load and clean OHLCV history from yfinance."""
        try:
            raw_frame: pd.DataFrame | None = yf.download(
                self.ticker,
                start=self.start,
                end=self.end,
                interval=self.interval,
                progress=False,
                auto_adjust=False,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            self.logger.error("yfinance download failed for %s: %s", self.ticker, exc)
            raise ValueError(f"Failed to load history for {self.ticker}") from exc

        if raw_frame is None or (isinstance(raw_frame, pd.DataFrame) and raw_frame.empty):
            raise ValueError(
                f"yfinance returned no data for {self.ticker} "
                f"between {self.start} and {self.end}"
            )
        clean_frame = _normalize_download_frame(raw_frame)
        if clean_frame.empty:
            self.logger.error("No historical data returned for %s between %s and %s", self.ticker, self.start, self.end)
            raise ValueError(f"No data returned for {self.ticker}")

        missing_columns = [column for column in REQUIRED_OHLCV_COLUMNS if column not in clean_frame.columns]
        if missing_columns:
            raise ValueError(f"Missing required OHLCV columns for {self.ticker}: {', '.join(missing_columns)}")

        clean_frame = clean_frame.loc[:, list(REQUIRED_OHLCV_COLUMNS)].copy()
        clean_frame.index = pd.to_datetime(clean_frame.index)
        clean_frame.index.name = "Date"

        rows_before_drop = len(clean_frame)
        clean_frame = clean_frame.dropna(subset=["Close"])
        rows_dropped = rows_before_drop - len(clean_frame)
        if rows_dropped > 0:
            self.logger.warning("Dropped %s rows with missing Close values for %s", rows_dropped, self.ticker)

        if len(clean_frame) < MIN_HISTORY_ROWS:
            raise ValueError(
                f"Insufficient history for {self.ticker}: {len(clean_frame)} rows returned, {MIN_HISTORY_ROWS} required"
            )

        return clean_frame.sort_index()


class SignalLayer:
    """Generates rolling bar-by-bar trade signals for the backtest."""

    def __init__(self, mode: BacktestMode, db_path: Path = DATABASE_PATH, logger: logging.Logger | None = None) -> None:
        self.mode = mode
        self.db_path = db_path
        self.logger = logger or logging.getLogger(LOGGER_NAME)
        self.quant_engine = QuantitativeEngine(logger=self.logger)
        self._metrics_cache: dict[str, dict[str, Any]] = {}
        self._war_room_client: Any | None = None
        self._war_room_method_name: str | None = None
        self._ensure_cache_table()

    def generate_signals(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Append signal, confidence, and reasoning columns using a rolling window."""
        signal_frame = df.copy()
        signal_frame["signal"] = SignalAction.HOLD.value
        signal_frame["confidence"] = NO_SIGNAL_CONFIDENCE
        signal_frame["reasoning"] = ""

        for bar_index in range(SIGNAL_WARMUP_BARS, len(signal_frame)):
            window = signal_frame.iloc[: bar_index + 1]
            bar_timestamp = signal_frame.index[bar_index]
            date_str = _index_to_date_string(bar_timestamp)

            try:
                metrics = self.quant_engine.calculate_indicators(window)
            except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                self.logger.warning("Indicator computation failed for %s on %s: %s", ticker.upper(), date_str, exc)
                signal_frame.at[bar_timestamp, "signal"] = SignalAction.HOLD.value
                signal_frame.at[bar_timestamp, "confidence"] = NO_SIGNAL_CONFIDENCE
                signal_frame.at[bar_timestamp, "reasoning"] = ""
                continue

            if not metrics:
                self.logger.warning("Indicator computation returned empty metrics for %s on %s", ticker.upper(), date_str)
                signal_frame.at[bar_timestamp, "signal"] = SignalAction.HOLD.value
                signal_frame.at[bar_timestamp, "confidence"] = NO_SIGNAL_CONFIDENCE
                signal_frame.at[bar_timestamp, "reasoning"] = ""
                continue

            self._metrics_cache[date_str] = metrics
            regime_estimate = self._estimate_bar_regime(window, metrics)
            quant_action, quant_confidence = self._determine_quant_signal(
                metrics, regime_estimate
            )
            action = quant_action
            confidence = quant_confidence
            reasoning = ""

            if self.mode is BacktestMode.FULL_PIPELINE:
                action, confidence, reasoning = self._resolve_full_pipeline_signal(
                    ticker=ticker.upper(),
                    date_str=date_str,
                    metrics=metrics,
                    quant_action=quant_action,
                    quant_confidence=quant_confidence,
                )

            signal_frame.at[bar_timestamp, "signal"] = action
            signal_frame.at[bar_timestamp, "confidence"] = confidence
            signal_frame.at[bar_timestamp, "reasoning"] = reasoning

        return signal_frame

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection to the Oracle ledger."""
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_cache_table(self) -> None:
        """Ensure the LLM signal cache table exists before signal generation."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.logger.error("Failed to prepare database directory %s: %s", self.db_path.parent, exc)
            return

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_signal_cache (
                        cache_key     TEXT PRIMARY KEY,
                        ticker        TEXT NOT NULL,
                        date_str      TEXT NOT NULL,
                        action        TEXT NOT NULL,
                        confidence    INTEGER NOT NULL,
                        reasoning     TEXT NOT NULL,
                        created_at    TEXT NOT NULL
                    );
                    """
                )
                connection.commit()
        except sqlite3.Error as exc:
            self.logger.error("Failed to ensure llm_signal_cache table: %s", exc)

    def _estimate_bar_regime(self, window: pd.DataFrame, metrics: dict[str, Any]) -> str:
        """ADX+BB+EMA regime classifier. Requires 30+ bars."""
        if len(window) < SIGNAL_WARMUP_BARS:
            return MarketRegime.UNKNOWN.value

        adx = _coerce_float(metrics.get("adx_14"))
        bb_width = _coerce_float(metrics.get("bb_width"))
        ema_9 = _coerce_float(metrics.get("ema_9"))
        ema_21 = _coerce_float(metrics.get("ema_21"))
        plus_di = _coerce_float(metrics.get("plus_di"))
        minus_di = _coerce_float(metrics.get("minus_di"))

        close = pd.to_numeric(window["Close"], errors="coerce")
        bb_mid_series = close.rolling(20).mean()
        bb_std_series = close.rolling(20).std(ddof=0)
        bb_width_series = (
            ((bb_mid_series + 2 * bb_std_series) - (bb_mid_series - 2 * bb_std_series))
            / bb_mid_series.replace(0, np.nan)
        ).dropna()

        bb_width_pct = float(bb_width_series.rank(pct=True).iloc[-1]) if len(bb_width_series) > 1 else 0.5
        if not math.isfinite(bb_width):
            bb_width = 0.0

        if adx >= ADX_TREND_THRESHOLD:
            if plus_di > minus_di and ema_9 > ema_21:
                return MarketRegime.BULL.value
            if minus_di > plus_di and ema_9 < ema_21:
                return MarketRegime.BEAR.value
            # If adx is high but trend direction is mixed, we fall through 
            # to check for high-volatility sideways action instead of unknown.

        if bb_width_pct >= BB_HIGH_VOL_PERCENTILE:
            return MarketRegime.VOLATILE.value

        if bb_width_pct < BB_HIGH_VOL_PERCENTILE:
            return MarketRegime.SIDEWAYS.value

        return MarketRegime.UNKNOWN.value

    def _determine_quant_signal_legacy(
        self,
        metrics: dict[str, Any],
        regime: str = MarketRegime.UNKNOWN.value,
    ) -> tuple[str, int]:
        """3-of-5 confirmation gate - regime-aware signal engine."""
        close = _coerce_float(metrics.get("close"))
        rsi = _coerce_float(metrics.get("rsi_14"))
        macd_l = _coerce_float(metrics.get("macd_line"))
        macd_s = _coerce_float(metrics.get("macd_signal"))
        macd_h = _coerce_float(metrics.get("macd_histogram"))
        ema_9 = _coerce_float(metrics.get("ema_9"))
        ema_21 = _coerce_float(metrics.get("ema_21"))
        adx = _coerce_float(metrics.get("adx_14"))
        bb_upper = _coerce_float(metrics.get("bb_upper"))
        bb_lower = _coerce_float(metrics.get("bb_lower"))
        bb_mid = _coerce_float(metrics.get("bb_mid"))
        bb_pct_b = _coerce_float(metrics.get("bb_pct_b"))
        bb_width = _coerce_float(metrics.get("bb_width"))
        obv_trend = _coerce_float(metrics.get("obv_trend"))
        atr = _coerce_float(metrics.get("atr_14"))
        vol_surge = _coerce_float(metrics.get("volume_surge"))
        close_price = close
        rsi_value = rsi
        macd_hist = macd_h
        macd_line = macd_l
        macd_signal = macd_s
        sma_fast = _coerce_float(metrics.get("sma_5"))
        sma_slow = _coerce_float(metrics.get("sma_14"))
        volume_surge = vol_surge
        atr_value = atr

        # ── Gate 1: Universal volatility stand-aside ──────────────────
        # Fires in ALL regimes — too volatile to trade safely
        if close_price > 0.0 and atr_value / close_price > ATR_HOLD_THRESHOLD:
            return SignalAction.HOLD.value, HOLD_VOLATILITY_CONFIDENCE

        # ── Gate 2: UNKNOWN regime — stand aside entirely ─────────────
        # No edge in undefined market conditions
        if regime == MarketRegime.UNKNOWN.value:
            return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE

        # ── Gate 3: VOLATILE regime — only take mean-reversion ────────
        # Trend rules fail in volatile conditions
        # Only the high-conviction oversold/overbought rules are allowed
        if regime == MarketRegime.VOLATILE.value:
            # Exit VOLATILE position when RSI spikes overbought
            if (rsi_value > 65.0 and macd_hist < 0.0):
                return SignalAction.SELL.value, 58
            if (rsi_value < RSI_OVERSOLD_THRESHOLD
                    and macd_hist > 0.0
                    and close_price > sma_slow):
                return SignalAction.BUY.value, BUY_PRIMARY_CONFIDENCE
            if (rsi_value > RSI_OVERBOUGHT_THRESHOLD
                    and macd_hist < 0.0
                    and close_price < sma_slow):
                return SignalAction.SELL.value, BUY_PRIMARY_CONFIDENCE
            return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE

        # ── Gate 4: SIDEWAYS regime — volume breakout only ────────────
        # Trend-following fails in ranging markets
        # Only volume surge signals are allowed
        if regime == MarketRegime.SIDEWAYS.value:
            if (volume_surge > VOLUME_SURGE_THRESHOLD
                    and close_price > sma_fast
                    and macd_line > macd_signal):
                return SignalAction.BUY.value, BUY_SECONDARY_CONFIDENCE
            return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE

        # ── Gate 5: BULL regime — trend-following rules ───────────────
        if regime == MarketRegime.BULL.value:
            # Exit existing BULL position when momentum deteriorates
            if (close_price < sma_fast
                    and macd_line < macd_signal
                    and rsi_value < 45.0):
                return SignalAction.SELL.value, 60
            # High-conviction oversold bounce
            if (rsi_value < RSI_OVERSOLD_THRESHOLD
                    and macd_hist > 0.0
                    and close_price > sma_slow):
                return SignalAction.BUY.value, BUY_PRIMARY_CONFIDENCE
            # Mid-trend continuation
            if (close_price > sma_fast > sma_slow
                    and macd_line > macd_signal
                    and 45.0 < rsi_value < 65.0):
                return SignalAction.BUY.value, 58
            # Volume-confirmed breakout
            if (volume_surge > VOLUME_SURGE_THRESHOLD
                    and close_price > sma_fast
                    and macd_line > macd_signal):
                return SignalAction.BUY.value, BUY_SECONDARY_CONFIDENCE
            return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE

        # ── Gate 6: BEAR regime — short/exit rules ────────────────────
        if regime == MarketRegime.BEAR.value:
            # High-conviction overbought in downtrend
            if (rsi_value > RSI_OVERBOUGHT_THRESHOLD
                    and macd_hist < 0.0
                    and close_price < sma_slow):
                return SignalAction.SELL.value, BUY_PRIMARY_CONFIDENCE
            # Mid-trend continuation short
            if (close_price < sma_fast < sma_slow
                    and macd_line < macd_signal
                    and 35.0 < rsi_value < 55.0):
                return SignalAction.SELL.value, 55
            return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE

        # ── Fallback ──────────────────────────────────────────────────
        return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE

    def _determine_quant_signal(
        self,
        metrics: dict[str, Any],
        regime: str = MarketRegime.UNKNOWN.value,
    ) -> tuple[str, int]:
        """3-of-5 confirmation gate - regime-aware signal engine."""

        close = _coerce_float(metrics.get("close"))
        rsi = _coerce_float(metrics.get("rsi_14"))
        macd_l = _coerce_float(metrics.get("macd_line"))
        macd_s = _coerce_float(metrics.get("macd_signal"))
        macd_h = _coerce_float(metrics.get("macd_histogram"))
        ema_9 = _coerce_float(metrics.get("ema_9"))
        ema_21 = _coerce_float(metrics.get("ema_21"))
        adx = _coerce_float(metrics.get("adx_14"))
        bb_upper = _coerce_float(metrics.get("bb_upper"))
        bb_lower = _coerce_float(metrics.get("bb_lower"))
        bb_mid = _coerce_float(metrics.get("bb_mid"))
        bb_pct_b = _coerce_float(metrics.get("bb_pct_b"))
        bb_width = _coerce_float(metrics.get("bb_width"))
        obv_trend = _coerce_float(metrics.get("obv_trend"))
        atr = _coerce_float(metrics.get("atr_14"))
        vol_surge = _coerce_float(metrics.get("volume_surge"))

        if regime == MarketRegime.UNKNOWN.value:
            return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE

        if close > 0.0 and atr / close > ATR_HOLD_THRESHOLD * 1.5:
            return SignalAction.HOLD.value, HOLD_VOLATILITY_CONFIDENCE

        def ema_cross_score() -> int:
            if ema_9 > ema_21 * 1.001:
                return 1
            if ema_9 < ema_21 * 0.999:
                return -1
            return 0

        def macd_score() -> int:
            if macd_l > macd_s and macd_h > 0:
                return 1
            if macd_l < macd_s and macd_h < 0:
                return -1
            return 0

        def rsi_score(active_regime: str) -> int:
            if active_regime == MarketRegime.BULL.value:
                if 45 < rsi < 70:
                    return 1
                if rsi > 75:
                    return -1
            elif active_regime == MarketRegime.BEAR.value:
                if 30 < rsi < 55:
                    return -1
                if rsi < 25:
                    return 1
            elif active_regime == MarketRegime.SIDEWAYS.value:
                if rsi < 35:
                    return 1
                if rsi > 65:
                    return -1
            elif active_regime == MarketRegime.VOLATILE.value:
                if rsi < 30:
                    return 1
                if rsi > 70:
                    return -1
            return 0

        def bb_score(active_regime: str) -> int:
            if active_regime == MarketRegime.SIDEWAYS.value:
                if bb_pct_b < 0.15:
                    return 1
                if bb_pct_b > 0.85:
                    return -1
            else:
                if close > bb_mid and bb_pct_b > 0.5:
                    return 1
                if close < bb_mid and bb_pct_b < 0.5:
                    return -1
            return 0

        def obv_score() -> int:
            if obv_trend > 0:
                return 1
            if obv_trend < 0:
                return -1
            return 0

        def adx_score(active_regime: str) -> int:
            if active_regime in (MarketRegime.BULL.value, MarketRegime.BEAR.value):
                return 1 if adx > 25 else 0
            return 0

        scores = [
            ema_cross_score(),
            macd_score(),
            rsi_score(regime),
            bb_score(regime),
            obv_score(),
        ]
        bull_count = sum(1 for score in scores if score == 1)
        bear_count = sum(1 for score in scores if score == -1)
        trend_bonus = adx_score(regime)
        _ = bb_upper, bb_lower, bb_width

        if regime == MarketRegime.BULL.value:
            if bull_count >= 3:
                # Require ADX > 20 for BULL entries to filter weak trends
                if adx < 20.0:
                    return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE
                confidence = 55 + (bull_count - 3) * 8 + trend_bonus * 5
                return SignalAction.BUY.value, min(confidence, 88)
            if bear_count >= 3:
                confidence = 52 + (bear_count - 3) * 6
                return SignalAction.SELL.value, min(confidence, 75)

        elif regime == MarketRegime.BEAR.value:
            if bear_count >= 3:
                confidence = 55 + (bear_count - 3) * 8 + trend_bonus * 5
                return SignalAction.SELL.value, min(confidence, 88)
            if bull_count >= 3:
                confidence = 52 + (bull_count - 3) * 6
                return SignalAction.BUY.value, min(confidence, 75)

        elif regime == MarketRegime.SIDEWAYS.value:
            if bull_count >= 3 and bb_pct_b < 0.2:
                confidence = 58 + (bull_count - 3) * 6
                return SignalAction.BUY.value, min(confidence, 80)
            if bear_count >= 3 and bb_pct_b > 0.8:
                confidence = 58 + (bear_count - 3) * 6
                return SignalAction.SELL.value, min(confidence, 80)

        elif regime == MarketRegime.VOLATILE.value:
            if bull_count >= 4 and vol_surge > 1.5:
                confidence = 60 + (bull_count - 4) * 10
                return SignalAction.BUY.value, min(confidence, 78)
            if bear_count >= 4 and vol_surge > 1.5:
                confidence = 60 + (bear_count - 4) * 10
                return SignalAction.SELL.value, min(confidence, 78)

        return SignalAction.HOLD.value, HOLD_DEFAULT_CONFIDENCE

    def _resolve_full_pipeline_signal(
        self,
        ticker: str,
        date_str: str,
        metrics: dict[str, Any],
        quant_action: str,
        quant_confidence: int,
    ) -> tuple[str, int, str]:
        """Resolve a signal through cache and War Room LLM inference with fallback logic."""
        cache_key = self._build_cache_key(ticker, date_str, metrics)
        cached_signal = self._load_cached_signal(cache_key)
        if cached_signal is not None:
            return (
                str(cached_signal["action"]),
                _coerce_int(cached_signal["confidence"]),
                str(cached_signal["reasoning"]),
            )

        llm_signal = self._invoke_war_room(metrics)
        if llm_signal is None or self._is_failed_llm_response(llm_signal):
            self.logger.error("LLM pipeline failed for %s on %s; reverting to deterministic quant signal.", ticker, date_str)
            return quant_action, quant_confidence, FALLBACK_REASON

        action = self._sanitize_action(str(llm_signal.get("action", SignalAction.HOLD.value)))
        confidence = max(0, min(MAX_CONFIDENCE, _coerce_int(llm_signal.get("confidence"))))
        reasoning = str(llm_signal.get("reasoning", "")).strip()

        self._store_cached_signal(
            cache_key=cache_key,
            ticker=ticker,
            date_str=date_str,
            action=action,
            confidence=confidence,
            reasoning=reasoning,
        )
        return action, confidence, reasoning

    def _build_cache_key(self, ticker: str, date_str: str, metrics: dict[str, Any]) -> str:
        """Build the SHA-256 cache key for a fully specified signal request."""
        payload = ticker + date_str + json.dumps(metrics, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _load_cached_signal(self, cache_key: str) -> dict[str, Any] | None:
        """Load a cached LLM signal if one already exists for the current bar."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT action, confidence, reasoning
                    FROM llm_signal_cache
                    WHERE cache_key = ?;
                    """,
                    (cache_key,),
                ).fetchone()
        except sqlite3.Error as exc:
            self.logger.error("Failed to read llm_signal_cache for %s: %s", cache_key, exc)
            return None

        if row is None:
            return None
        return {
            "action": row["action"],
            "confidence": row["confidence"],
            "reasoning": row["reasoning"],
        }

    def _store_cached_signal(self, cache_key: str, ticker: str, date_str: str, action: str, confidence: int, reasoning: str) -> None:
        """Persist a successful LLM signal response to SQLite cache."""
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO llm_signal_cache (cache_key, ticker, date_str, action, confidence, reasoning, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        action = excluded.action,
                        confidence = excluded.confidence,
                        reasoning = excluded.reasoning,
                        created_at = excluded.created_at;
                    """,
                    (
                        cache_key,
                        ticker,
                        date_str,
                        action,
                        confidence,
                        reasoning,
                        _utc_now_string(),
                    ),
                )
                connection.commit()
        except sqlite3.Error as exc:
            self.logger.error("Failed to persist LLM cache entry for %s on %s: %s", ticker, date_str, exc)

    def _invoke_war_room(self, metrics: dict[str, Any]) -> dict[str, Any] | None:
        """Call the live War Room implementation available in the current Oracle module tree."""
        client_and_method = self._get_war_room_callable()
        if client_and_method is None:
            return None

        client, method_name = client_and_method
        method = getattr(client, method_name, None)
        if not callable(method):
            self.logger.error("War Room client does not expose callable method %s", method_name)
            return None

        try:
            response = method(metrics)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            self.logger.error("War Room analyze call failed: %s", exc)
            return None

        if not isinstance(response, dict):
            self.logger.error("War Room returned non-dict response: %s", type(response).__name__)
            return None
        return cast(dict[str, Any], response)

    def _get_war_room_callable(self) -> tuple[Any, str] | None:
        """Lazily resolve the War Room class and supported inference method."""
        if self._war_room_client is not None and self._war_room_method_name is not None:
            return self._war_room_client, self._war_room_method_name

        try:
            from oracle import oracle_war_room as war_room_module
        except (ImportError, ModuleNotFoundError) as exc:
            self.logger.error("Failed to import oracle.oracle_war_room: %s", exc)
            return None

        war_room_class = getattr(war_room_module, "WarRoom", None)
        method_name = "analyze"
        if war_room_class is None:
            war_room_class = getattr(war_room_module, "ChiefInvestmentOfficer", None)
            method_name = "generate_trade_signal"
        if war_room_class is None:
            self.logger.error("No supported War Room class found in oracle.oracle_war_room")
            return None

        try:
            self._war_room_client = war_room_class(logger=self.logger)
        except TypeError:
            try:
                self._war_room_client = war_room_class()
            except TypeError as exc:
                self.logger.error("Failed to instantiate War Room class %s: %s", war_room_class.__name__, exc)
                return None

        self._war_room_method_name = method_name
        return self._war_room_client, method_name

    def _is_failed_llm_response(self, llm_signal: dict[str, Any]) -> bool:
        """Detect sentinel failure responses from the current War Room implementation."""
        action = self._sanitize_action(str(llm_signal.get("action", SignalAction.HOLD.value)))
        confidence = _coerce_int(llm_signal.get("confidence"))
        reasoning = str(llm_signal.get("reasoning", "")).lower()
        if action not in {SignalAction.BUY.value, SignalAction.SELL.value, SignalAction.HOLD.value}:
            return True
        if confidence == 0 and any(marker in reasoning for marker in LLM_FAILURE_REASON_MARKERS):
            return True
        return False

    def _sanitize_action(self, action: str) -> str:
        """Normalize arbitrary model output into a supported trading action."""
        normalized_action = action.strip().upper()
        if normalized_action in {SignalAction.BUY.value, SignalAction.SELL.value, SignalAction.HOLD.value}:
            return normalized_action
        return SignalAction.HOLD.value


class RegimeDetector:
    """Classifies historical bars into market regimes."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(LOGGER_NAME)

    def classify(self, df: pd.DataFrame) -> pd.DataFrame:
        """Append a market regime label to each bar using pure pandas logic."""
        regime_frame = df.copy()
        close_series = pd.to_numeric(regime_frame["Close"], errors="coerce")
        daily_returns = close_series.pct_change()
        sma_fast = close_series.rolling(window=SMA_REGIME_FAST_WINDOW, min_periods=SMA_REGIME_FAST_WINDOW).mean()
        sma_slow = close_series.rolling(window=SMA_REGIME_SLOW_WINDOW, min_periods=SMA_REGIME_SLOW_WINDOW).mean()
        volatility = daily_returns.rolling(window=VOLATILITY_WINDOW, min_periods=VOLATILITY_WINDOW).std()

        valid_volatility = volatility.dropna()
        volatility_threshold = float(valid_volatility.quantile(VOLATILITY_QUANTILE)) if not valid_volatility.empty else math.inf

        regime_series = pd.Series(MarketRegime.UNKNOWN.value, index=regime_frame.index, dtype="object")
        bull_mask = (close_series > sma_fast) & (sma_fast > sma_slow) & (volatility < volatility_threshold)
        bear_mask = (close_series < sma_fast) & (sma_fast < sma_slow) & (volatility < volatility_threshold)
        sideways_mask = ((sma_fast - sma_slow).abs() / sma_slow.replace(0.0, np.nan)).abs() < SIDEWAYS_THRESHOLD
        volatile_mask = volatility >= volatility_threshold

        regime_series.loc[bull_mask.fillna(False)] = MarketRegime.BULL.value
        regime_series.loc[bear_mask.fillna(False)] = MarketRegime.BEAR.value
        regime_series.loc[sideways_mask.fillna(False)] = MarketRegime.SIDEWAYS.value
        regime_series.loc[volatile_mask.fillna(False)] = MarketRegime.VOLATILE.value
        regime_frame["regime"] = regime_series
        return regime_frame


class ExecutionSimulator:
    """Simulates next-open execution, costs, and portfolio equity over time."""

    def __init__(
        self,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        commission: float = DEFAULT_COMMISSION_RATE,
        slippage: float = DEFAULT_SLIPPAGE_RATE,
        logger: logging.Logger | None = None,
    ) -> None:
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.logger = logger or logging.getLogger(LOGGER_NAME)

    def simulate(
        self,
        df: pd.DataFrame,
        metrics_cache: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[list[TradeRecord], pd.Series]:
        """Simulate trade execution and return realized trades plus equity curve."""
        trades: list[TradeRecord] = []
        equity_values: list[float] = []
        equity_index: list[pd.Timestamp] = []

        ticker = str(df.attrs.get("ticker", "UNKNOWN"))
        cash = float(self.initial_capital)
        position: PositionState | None = None
        pending_entry: PendingEntry | None = None
        pending_exit: PendingExit | None = None
        last_bar_index = len(df) - 1

        for bar_index in range(len(df)):
            timestamp = pd.Timestamp(df.index[bar_index])
            row = df.iloc[bar_index]
            open_price = _coerce_float(row.get("Open"))
            high_price = _coerce_float(row.get("High"))
            low_price = _coerce_float(row.get("Low"))
            close_price = _coerce_float(row.get("Close"))
            date_str = _index_to_date_string(timestamp)
            metrics_for_bar = (metrics_cache or {}).get(date_str, {})

            if pending_exit is not None and pending_exit.execute_index == bar_index and position is not None:
                exit_fill_price = (
                    open_price * (1.0 + self.slippage)
                    if position.direction == PositionDirection.SHORT.value
                    else open_price * (1.0 - self.slippage)
                )
                cash, trade = self._execute_exit(
                    position=position,
                    exit_date=date_str,
                    exit_price=exit_fill_price,
                    exit_reason=pending_exit.exit_reason,
                    hold_bars=bar_index - position.entry_index,
                    cash=cash,
                )
                trades.append(trade)
                position = None
                pending_exit = None

            if pending_entry is not None and pending_entry.execute_index == bar_index and position is None:
                position, cash = self._execute_entry(
                    ticker=ticker,
                    pending_entry=pending_entry,
                    entry_date=date_str,
                    entry_index=bar_index,
                    open_price=open_price,
                    cash=cash,
                    direction=pending_entry.direction,
                )
                pending_entry = None

            if position is None:
                mark_to_market_value = 0.0
            elif position.direction == PositionDirection.SHORT.value:
                mark_to_market_value = -(position.shares * close_price)
            else:
                mark_to_market_value = position.shares * close_price
            current_equity = cash + mark_to_market_value
            equity_index.append(timestamp)
            equity_values.append(current_equity)

            has_next_bar = bar_index < last_bar_index
            current_signal = str(row.get("signal", SignalAction.HOLD.value)).upper()
            current_regime = str(row.get("regime", MarketRegime.UNKNOWN.value))
            confidence = max(0, min(MAX_CONFIDENCE, _coerce_int(row.get("confidence"))))
            has_position = position is not None
            open_direction = position.direction if position is not None else PositionDirection.FLAT.value
            signal_intent = classify_signal_intent(
                signal=current_signal,
                regime=current_regime,
                has_open_position=has_position,
                open_position_direction=open_direction,
            )

            if position is not None and pending_exit is None and has_next_bar:
                hold_bars = (bar_index + 1) - position.entry_index
                if signal_intent in ("EXIT_LONG", "EXIT_SHORT"):
                    pending_exit = PendingExit(
                        execute_index=bar_index + 1,
                        exit_reason=ExitReason.SIGNAL,
                    )
                else:
                    exit_reason = self._evaluate_exit_reason(
                        current_signal=current_signal,
                        position=position,
                        high_price=high_price,
                        low_price=low_price,
                        hold_bars=hold_bars,
                        current_regime=current_regime,
                        metrics=metrics_for_bar,
                    )
                    if exit_reason is not None:
                        pending_exit = PendingExit(execute_index=bar_index + 1, exit_reason=exit_reason)
            elif (
                position is None
                and pending_entry is None
                and has_next_bar
                and MAX_SIMULTANEOUS_POSITIONS > 0
                and current_equity > 0.0
            ):
                min_confidence = (
                    VOLATILE_MIN_CONFIDENCE
                    if current_regime == MarketRegime.VOLATILE.value
                    else DEFAULT_MIN_CONFIDENCE
                )
                if (
                    current_signal in (SignalAction.BUY.value, SignalAction.SELL.value)
                    and confidence >= min_confidence
                    and signal_intent in ("ENTER_LONG", "ENTER_SHORT")
                ):
                    regime_multiplier = REGIME_POSITION_MULTIPLIER.get(
                        current_regime,
                        0.0,
                    )
                    if regime_multiplier == 0.0:
                        continue
                    kelly_fraction = (confidence / PERCENT_SCALE) * MAX_POSITION_FRACTION
                    if signal_intent == "ENTER_LONG":
                        allocation_fraction = min(
                            kelly_fraction * regime_multiplier,
                            MAX_POSITION_FRACTION,
                        )
                        entry_direction = PositionDirection.LONG.value
                    else:
                        allocation_fraction = min(
                            kelly_fraction * regime_multiplier * 0.8,
                            MAX_POSITION_FRACTION * 0.8,
                        )
                        entry_direction = PositionDirection.SHORT.value
                    allocation_value = allocation_fraction * current_equity
                    if allocation_value > 0.0:
                        pending_entry = PendingEntry(
                            execute_index=bar_index + 1,
                            signal=current_signal,
                            confidence=confidence,
                            reasoning=str(row.get("reasoning", "")),
                            regime=current_regime,
                            allocation_value=allocation_value,
                            direction=entry_direction,
                        )

        if position is not None and equity_values:
            self.logger.warning(FINAL_LIQUIDATION_LOG_TEMPLATE, ticker)
            final_row = df.iloc[-1]
            final_signal = str(final_row.get("signal", SignalAction.HOLD.value)).upper()
            final_exit_reason = self._infer_terminal_exit_reason(
                final_signal=final_signal,
                position=position,
                high_price=_coerce_float(final_row.get("High")),
                low_price=_coerce_float(final_row.get("Low")),
                hold_bars=len(df) - position.entry_index,
                current_regime=str(final_row.get("regime", MarketRegime.UNKNOWN.value)),
                metrics=(metrics_cache or {}).get(_index_to_date_string(df.index[-1]), {}),
            )
            final_cash, trade = self._execute_exit(
                position=position,
                exit_date=_index_to_date_string(df.index[-1]),
                exit_price=(
                    _coerce_float(final_row.get("Close")) * (1.0 + self.slippage)
                    if position.direction == PositionDirection.SHORT.value
                    else _coerce_float(final_row.get("Close")) * (1.0 - self.slippage)
                ),
                exit_reason=final_exit_reason,
                hold_bars=len(df) - position.entry_index,
                cash=cash,
            )
            trades.append(trade)
            cash = final_cash
            equity_values[-1] = cash

        equity_curve = pd.Series(equity_values, index=equity_index, dtype="float64", name="equity")
        return trades, equity_curve

    def _execute_entry(
        self,
        ticker: str,
        pending_entry: PendingEntry,
        entry_date: str,
        entry_index: int,
        open_price: float,
        cash: float,
        direction: str = PositionDirection.LONG.value,
    ) -> tuple[PositionState | None, float]:
        """Execute a scheduled entry order at the next bar open."""
        if direction == PositionDirection.SHORT.value:
            effective_entry_price = open_price * (1.0 - self.slippage)
            if effective_entry_price <= 0.0:
                self.logger.warning("Skipping short entry for %s on %s due to non-positive fill price.", ticker, entry_date)
                return None, cash

            shares = pending_entry.allocation_value / (
                effective_entry_price * SHORT_MARGIN_REQUIREMENT * (1.0 + self.commission)
            )
            if shares <= 0.0:
                return None, cash

            gross_sale_value = shares * effective_entry_price
            entry_commission = gross_sale_value * self.commission
            net_sale_proceeds = gross_sale_value - entry_commission
            updated_cash = cash + net_sale_proceeds
            position = PositionState(
                ticker=ticker,
                entry_index=entry_index,
                entry_date=entry_date,
                entry_price=effective_entry_price,
                shares=shares,
                entry_cost=net_sale_proceeds,
                signal=pending_entry.signal,
                confidence=pending_entry.confidence,
                reasoning=pending_entry.reasoning,
                regime=pending_entry.regime,
                direction=PositionDirection.SHORT.value,
            )
            return position, updated_cash

        effective_entry_price = open_price * (1.0 + self.slippage)
        if effective_entry_price <= 0.0:
            self.logger.warning("Skipping entry for %s on %s due to non-positive fill price.", ticker, entry_date)
            return None, cash

        shares = pending_entry.allocation_value / (effective_entry_price * (1.0 + self.commission))
        if shares <= 0.0:
            return None, cash

        gross_entry_value = shares * effective_entry_price
        entry_commission = gross_entry_value * self.commission
        total_entry_cost = gross_entry_value + entry_commission
        if total_entry_cost > cash:
            shares = cash / (effective_entry_price * (1.0 + self.commission))
            gross_entry_value = shares * effective_entry_price
            entry_commission = gross_entry_value * self.commission
            total_entry_cost = gross_entry_value + entry_commission

        if shares <= 0.0 or total_entry_cost <= 0.0:
            return None, cash

        updated_cash = cash - total_entry_cost
        position = PositionState(
            ticker=ticker,
            entry_index=entry_index,
            entry_date=entry_date,
            entry_price=effective_entry_price,
            shares=shares,
            entry_cost=total_entry_cost,
            signal=pending_entry.signal,
            confidence=pending_entry.confidence,
            reasoning=pending_entry.reasoning,
            regime=pending_entry.regime,
            direction=PositionDirection.LONG.value,
        )
        return position, updated_cash

    def _execute_exit(
        self,
        position: PositionState,
        exit_date: str,
        exit_price: float,
        exit_reason: ExitReason,
        hold_bars: int,
        cash: float,
    ) -> tuple[float, TradeRecord]:
        """Execute a scheduled exit order and realize trade PnL."""
        if position.direction == PositionDirection.SHORT.value:
            effective_exit_price = max(exit_price, 0.0)
            borrow_cost = (
                position.shares * position.entry_price *
                SHORT_BORROW_COST_DAILY * hold_bars
            )
            gross_cover_cost = position.shares * effective_exit_price
            exit_commission = gross_cover_cost * self.commission
            total_cover_cost = gross_cover_cost + exit_commission + borrow_cost
            pnl = position.entry_cost - total_cover_cost
            pnl_pct = pnl / position.entry_cost if position.entry_cost > 0.0 else 0.0
            updated_cash = cash - total_cover_cost

            trade = TradeRecord(
                ticker=position.ticker,
                entry_date=position.entry_date,
                exit_date=exit_date,
                entry_price=position.entry_price,
                exit_price=effective_exit_price,
                shares=position.shares,
                pnl=pnl,
                pnl_pct=pnl_pct,
                signal=position.signal,
                confidence=position.confidence,
                reasoning=position.reasoning,
                regime=position.regime,
                hold_bars=hold_bars,
                exit_reason=exit_reason.value,
                direction=PositionDirection.SHORT.value,
            )
            return updated_cash, trade

        effective_exit_price = max(exit_price, 0.0)
        gross_exit_value = position.shares * effective_exit_price
        exit_commission = gross_exit_value * self.commission
        net_exit_value = gross_exit_value - exit_commission
        updated_cash = cash + net_exit_value
        pnl = net_exit_value - position.entry_cost
        pnl_pct = pnl / position.entry_cost if position.entry_cost > 0.0 else 0.0

        trade = TradeRecord(
            ticker=position.ticker,
            entry_date=position.entry_date,
            exit_date=exit_date,
            entry_price=position.entry_price,
            exit_price=effective_exit_price,
            shares=position.shares,
            pnl=pnl,
            pnl_pct=pnl_pct,
            signal=position.signal,
            confidence=position.confidence,
            reasoning=position.reasoning,
            regime=position.regime,
            hold_bars=hold_bars,
            exit_reason=exit_reason.value,
            direction=PositionDirection.LONG.value,
        )
        return updated_cash, trade

    def _evaluate_exit_reason(
        self,
        current_signal: str,
        position: PositionState,
        high_price: float,
        low_price: float,
        hold_bars: int,
        current_regime: str = MarketRegime.UNKNOWN.value,
        metrics: dict[str, Any] | None = None,
    ) -> ExitReason | None:
        """Regime-aware paired exit engine."""
        _metrics = metrics or {}

        if (
            position.direction == PositionDirection.LONG.value
            and current_signal == SignalAction.SELL.value
        ):
            return ExitReason.SIGNAL
        if (
            position.direction == PositionDirection.SHORT.value
            and current_signal == SignalAction.BUY.value
        ):
            return ExitReason.SIGNAL

        ema_9 = _coerce_float(_metrics.get("ema_9"))
        ema_21 = _coerce_float(_metrics.get("ema_21"))
        bb_pct_b = _coerce_float(_metrics.get("bb_pct_b"))
        rsi = _coerce_float(_metrics.get("rsi_14"))

        if position.direction == PositionDirection.LONG.value and current_regime == MarketRegime.BULL.value:
            if ema_9 < ema_21 * 0.999:
                return ExitReason.SIGNAL
            if rsi > 78:
                return ExitReason.TAKE_PROFIT

        elif position.direction == PositionDirection.LONG.value and current_regime == MarketRegime.SIDEWAYS.value:
            if bb_pct_b > 0.80:
                return ExitReason.TAKE_PROFIT

        elif position.direction == PositionDirection.LONG.value and current_regime == MarketRegime.VOLATILE.value:
            if high_price >= position.entry_price * 1.08:
                return ExitReason.TAKE_PROFIT

        active_stop = (
            MOMENTUM_STOP_LOSS_THRESHOLD
            if position.confidence < 70
            else STOP_LOSS_THRESHOLD
        )
        active_take_profit = (
            TAKE_PROFIT_THRESHOLD
            if position.confidence >= 70
            else 0.10
        )

        if position.direction == PositionDirection.SHORT.value:
            if high_price >= position.entry_price * (1.0 + active_stop):
                return ExitReason.STOP_LOSS
            if low_price <= position.entry_price * (1.0 - active_take_profit):
                return ExitReason.TAKE_PROFIT
        else:
            if low_price <= position.entry_price * (1.0 - active_stop):
                return ExitReason.STOP_LOSS
            if high_price >= position.entry_price * (1.0 + active_take_profit):
                return ExitReason.TAKE_PROFIT

        if hold_bars >= MAX_HOLD_BARS:
            return ExitReason.MAX_HOLD
        return None

    def _infer_terminal_exit_reason(
        self,
        final_signal: str,
        position: PositionState,
        high_price: float,
        low_price: float,
        hold_bars: int,
        current_regime: str = MarketRegime.UNKNOWN.value,
        metrics: dict[str, Any] | None = None,
    ) -> ExitReason:
        """Map an end-of-data liquidation to the closest valid exit reason."""
        evaluated_reason = self._evaluate_exit_reason(
            current_signal=final_signal,
            position=position,
            high_price=high_price,
            low_price=low_price,
            hold_bars=hold_bars,
            current_regime=current_regime,
            metrics=metrics,
        )
        if evaluated_reason is not None:
            return evaluated_reason
        return ExitReason.MAX_HOLD


class AnalyticsEngine:
    """Computes backtest analytics and performance diagnostics."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(LOGGER_NAME)

    def compute(self, trades: list[TradeRecord], equity_curve: pd.Series, ticker: str, mode: BacktestMode) -> BacktestReport:
        """Compute institutional performance metrics for a completed backtest run."""
        total_trades = len(trades)
        total_pnl = float(equity_curve.iloc[-1] - equity_curve.iloc[0]) if not equity_curve.empty else 0.0
        wins = [trade for trade in trades if trade.pnl > 0.0]
        win_rate = (len(wins) / total_trades) if total_trades > 0 else 0.0
        avg_pnl_per_trade = (sum(trade.pnl for trade in trades) / total_trades) if total_trades > 0 else 0.0

        daily_returns = equity_curve.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        if daily_returns.empty:
            sharpe_ratio = 0.0
        else:
            mean_return = float(daily_returns.mean())
            std_return = float(daily_returns.std(ddof=0))
            if abs(std_return) <= EPSILON:
                sharpe_ratio = 0.0
            else:
                sharpe_ratio = ((mean_return - DAILY_RISK_FREE_RATE) / std_return) * math.sqrt(TRADING_DAYS_PER_YEAR)

        if equity_curve.empty:
            max_drawdown = 0.0
            max_drawdown_duration = 0
        else:
            rolling_max = equity_curve.cummax()
            drawdown = (equity_curve - rolling_max) / rolling_max.replace(0.0, np.nan)
            max_drawdown = float(drawdown.min()) if not drawdown.dropna().empty else 0.0
            max_drawdown_duration = _compute_max_drawdown_duration(drawdown)

        gross_profit = sum(trade.pnl for trade in trades if trade.pnl > 0.0)
        gross_loss = sum(trade.pnl for trade in trades if trade.pnl < 0.0)
        if gross_loss == 0.0:
            profit_factor = math.inf if gross_profit > 0.0 else 0.0
        else:
            profit_factor = gross_profit / abs(gross_loss)

        avg_hold_bars = (sum(trade.hold_bars for trade in trades) / total_trades) if total_trades > 0 else 0.0
        regime_breakdown = self._build_regime_breakdown(trades)
        exit_reason_breakdown = self._build_exit_reason_breakdown(trades)
        best_trade = max(trades, key=lambda trade: trade.pnl) if trades else None
        worst_trade = min(trades, key=lambda trade: trade.pnl) if trades else None

        return BacktestReport(
            ticker=ticker.upper(),
            mode=mode.value,
            total_trades=total_trades,
            win_rate=win_rate,
            avg_pnl_per_trade=avg_pnl_per_trade,
            total_pnl=total_pnl,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            max_drawdown_duration=max_drawdown_duration,
            profit_factor=profit_factor,
            avg_hold_bars=avg_hold_bars,
            regime_breakdown=regime_breakdown,
            exit_reason_breakdown=exit_reason_breakdown,
            best_trade=best_trade,
            worst_trade=worst_trade,
        )

    def _build_regime_breakdown(self, trades: list[TradeRecord]) -> dict[str, dict[str, int | float]]:
        """Aggregate trade performance statistics by regime."""
        grouped: dict[str, list[TradeRecord]] = {}
        for trade in trades:
            grouped.setdefault(trade.regime, []).append(trade)

        breakdown: dict[str, dict[str, int | float]] = {}
        for regime in grouped:
            regime_trades = grouped[regime]
            trade_count = len(regime_trades)
            regime_wins = sum(1 for trade in regime_trades if trade.pnl > 0.0)
            avg_pnl = sum(trade.pnl for trade in regime_trades) / trade_count if trade_count > 0 else 0.0
            breakdown[regime] = {
                "trades": trade_count,
                "win_rate": (regime_wins / trade_count) if trade_count > 0 else 0.0,
                "avg_pnl": avg_pnl,
            }

        for regime in MarketRegime:
            breakdown.setdefault(
                regime.value,
                {
                    "trades": 0,
                    "win_rate": 0.0,
                    "avg_pnl": 0.0,
                },
            )
        return breakdown

    def _build_exit_reason_breakdown(self, trades: list[TradeRecord]) -> dict[str, int]:
        """Aggregate realized trade counts by exit reason."""
        breakdown: dict[str, int] = {reason.value: 0 for reason in ExitReason}
        for trade in trades:
            breakdown[trade.exit_reason] = breakdown.get(trade.exit_reason, 0) + 1
        return breakdown


def _configure_logging() -> logging.Logger:
    """Configure and return the module logger."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    return logging.getLogger(LOGGER_NAME)


def _normalize_download_frame(raw_frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance output into a single-index OHLCV DataFrame."""
    if raw_frame is None or raw_frame.empty:
        return pd.DataFrame()

    normalized_frame = raw_frame.copy()
    if isinstance(normalized_frame.columns, pd.MultiIndex):
        normalized_frame.columns = normalized_frame.columns.get_level_values(0)

    rename_map: dict[str, str] = {}
    for column in normalized_frame.columns:
        column_name = str(column).strip()
        canonical_name = column_name.title().replace("Adj Close", "Adj Close")
        if column_name.lower() == "open":
            rename_map[column_name] = "Open"
        elif column_name.lower() == "high":
            rename_map[column_name] = "High"
        elif column_name.lower() == "low":
            rename_map[column_name] = "Low"
        elif column_name.lower() == "close":
            rename_map[column_name] = "Close"
        elif column_name.lower() == "volume":
            rename_map[column_name] = "Volume"
        else:
            rename_map[column_name] = canonical_name

    return normalized_frame.rename(columns=rename_map)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    """Safely coerce arbitrary numeric-like values to a finite float."""
    try:
        if value is None:
            return default
        if isinstance(value, (np.floating, np.integer)):
            numeric_value = float(value.item())
        else:
            numeric_value = float(value)
    except (TypeError, ValueError):
        return default

    if math.isnan(numeric_value) or math.isinf(numeric_value):
        return default
    return numeric_value


def _coerce_int(value: Any, default: int = 0) -> int:
    """Safely coerce arbitrary numeric-like values to an int."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _index_to_date_string(index_value: Any) -> str:
    """Format an index value as an ISO date string."""
    timestamp = pd.Timestamp(index_value)
    return timestamp.strftime(DATE_FORMAT)


def _utc_now_string() -> str:
    """Return the current UTC timestamp in a stable string format."""
    return datetime.now(UTC).strftime(ISO_TIMESTAMP_FORMAT)


def _compute_max_drawdown_duration(drawdown: pd.Series) -> int:
    """Compute the longest consecutive drawdown streak in bars."""
    longest_streak = 0
    current_streak = 0
    for value in drawdown.fillna(0.0):
        if _coerce_float(value) < 0.0:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 0
    return longest_streak


def _ensure_database_schema(db_path: Path, logger: logging.Logger) -> None:
    """Create the required cache and backtest run tables if they do not exist."""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("Failed to prepare database directory %s: %s", db_path.parent, exc)
        return

    try:
        with sqlite3.connect(str(db_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_signal_cache (
                    cache_key     TEXT PRIMARY KEY,
                    ticker        TEXT NOT NULL,
                    date_str      TEXT NOT NULL,
                    action        TEXT NOT NULL,
                    confidence    INTEGER NOT NULL,
                    reasoning     TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id        TEXT PRIMARY KEY,
                    ticker        TEXT NOT NULL,
                    mode          TEXT NOT NULL,
                    start_date    TEXT NOT NULL,
                    end_date      TEXT NOT NULL,
                    total_trades  INTEGER,
                    win_rate      REAL,
                    sharpe_ratio  REAL,
                    max_drawdown  REAL,
                    report_path   TEXT,
                    run_at        TEXT NOT NULL
                );
                """
            )
            connection.commit()
    except sqlite3.Error as exc:
        logger.error("Failed to ensure backtest database schema at %s: %s", db_path, exc)


def _ensure_output_directory(output_directory: Path, logger: logging.Logger) -> bool:
    """Ensure the output directory exists before artifact generation."""
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("Failed to create output directory %s: %s", output_directory, exc)
        return False
    return True


def _sanitize_for_json(value: Any) -> Any:
    """Convert dataclasses and non-finite numerics into JSON-safe structures."""
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, (np.floating, np.integer)):
        return _sanitize_for_json(value.item())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_sanitize_for_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_for_json(item) for key, item in value.items()}
    return value


def _report_to_json_payload(report: BacktestReport, erisia_memory_note: str) -> dict[str, Any]:
    """Build the JSON payload for the summary report artifact."""
    payload = asdict(report)
    payload["erisia_memory_note"] = erisia_memory_note
    payload["generated_at"] = _utc_now_string()
    return _sanitize_for_json(payload)


def _write_trade_log_csv(output_directory: Path, filename_stem: str, trades: list[TradeRecord], report: BacktestReport, logger: logging.Logger) -> Path | None:
    """Write the realized trade log to CSV with a trailing summary row."""
    if not _ensure_output_directory(output_directory, logger):
        return None

    output_path = output_directory / f"{filename_stem}_trades.csv"
    field_names = [
        "ticker",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
        "shares",
        "pnl",
        "pnl_pct",
        "signal",
        "confidence",
        "reasoning",
        "regime",
        "hold_bars",
        "exit_reason",
        "direction",
    ]
    summary_row: dict[str, Any] = {
        "ticker": SUMMARY_ROW_TICKER,
        "entry_date": "",
        "exit_date": "",
        "entry_price": 0.0,
        "exit_price": 0.0,
        "shares": float(report.total_trades),
        "pnl": report.total_pnl,
        "pnl_pct": report.win_rate,
        "signal": "ALL",
        "confidence": report.total_trades,
        "reasoning": (
            f"avg_pnl_per_trade={report.avg_pnl_per_trade:.2f}; "
            f"sharpe={report.sharpe_ratio:.4f}; "
            f"profit_factor={report.profit_factor:.4f}; "
            f"avg_hold_bars={report.avg_hold_bars:.2f}"
        ),
        "regime": "ALL",
        "hold_bars": report.avg_hold_bars,
        "exit_reason": SUMMARY_EXIT_REASON,
        "direction": "ALL",
    }

    try:
        with output_path.open("w", newline="", encoding="utf-8") as file_handle:
            writer = csv.DictWriter(file_handle, fieldnames=field_names)
            writer.writeheader()
            for trade in trades:
                writer.writerow(asdict(trade))
            writer.writerow(summary_row)
    except OSError as exc:
        logger.error("Failed to write trade log CSV %s: %s", output_path, exc)
        return None

    return output_path


def _select_best_and_worst_regimes(report: BacktestReport) -> tuple[str, str]:
    """Select the best and worst regimes using average realized PnL."""
    populated_regimes = [
        (regime, data)
        for regime, data in report.regime_breakdown.items()
        if _coerce_int(data.get("trades")) > 0
    ]
    if not populated_regimes:
        return MarketRegime.UNKNOWN.value, MarketRegime.UNKNOWN.value

    best_regime = max(populated_regimes, key=lambda item: _coerce_float(item[1].get("avg_pnl")))[0]
    worst_regime = min(populated_regimes, key=lambda item: _coerce_float(item[1].get("avg_pnl")))[0]
    return best_regime, worst_regime


def _build_risk_note(max_drawdown: float) -> str:
    """Convert drawdown depth into a plain-English risk characterization."""
    if max_drawdown <= -0.30:
        return "material downside risk and the need for tighter capital controls"
    if max_drawdown <= -0.15:
        return "moderate capital stress that warrants disciplined sizing"
    if max_drawdown < 0.0:
        return "contained downside pressure during the tested window"
    return "exceptionally stable capital preservation in this sample"


def _build_erisia_memory_note(report: BacktestReport) -> str:
    """Create the Hippocampus-ready memory note summarizing a run."""
    best_regime, worst_regime = _select_best_and_worst_regimes(report)
    risk_note = _build_risk_note(report.max_drawdown)
    win_rate_pct = report.win_rate * PERCENT_SCALE
    max_drawdown_pct = abs(report.max_drawdown) * PERCENT_SCALE
    return (
        f"Oracle's {report.mode} strategy on {report.ticker} achieved a {win_rate_pct:.2f}% win rate "
        f"with a Sharpe of {report.sharpe_ratio:.2f}. "
        f"She performed best in {best_regime} regimes and worst in {worst_regime} regimes. "
        f"Max drawdown of {max_drawdown_pct:.2f}% suggests {risk_note}."
    )


def _write_summary_json(output_directory: Path, filename_stem: str, report: BacktestReport, logger: logging.Logger) -> tuple[Path | None, str]:
    """Write the summary report JSON and return its path plus memory note."""
    if not _ensure_output_directory(output_directory, logger):
        return None, _build_erisia_memory_note(report)

    erisia_memory_note = _build_erisia_memory_note(report)
    payload = _report_to_json_payload(report, erisia_memory_note)
    output_path = output_directory / f"{filename_stem}_report.json"

    try:
        output_path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write summary JSON %s: %s", output_path, exc)
        return None, erisia_memory_note

    return output_path, erisia_memory_note


def _build_regime_bands(regime_series: pd.Series) -> list[dict[str, Any]]:
    """Collapse per-bar regimes into contiguous background bands for the HTML report."""
    bands: list[dict[str, Any]] = []
    if regime_series.empty:
        return bands

    normalized_regimes = regime_series.fillna(MarketRegime.UNKNOWN.value).astype(str)
    current_regime = str(normalized_regimes.iloc[0])
    start_index = 0
    for index in range(1, len(normalized_regimes)):
        next_regime = str(normalized_regimes.iloc[index])
        if next_regime != current_regime:
            bands.append({"start": start_index, "end": index - 1, "regime": current_regime})
            current_regime = next_regime
            start_index = index

    bands.append({"start": start_index, "end": len(normalized_regimes) - 1, "regime": current_regime})
    return bands


def _write_equity_curve_html(
    output_directory: Path,
    filename_stem: str,
    ticker: str,
    mode: BacktestMode,
    regime_frame: pd.DataFrame,
    equity_curve: pd.Series,
    logger: logging.Logger,
) -> Path | None:
    """Write the HTML artifact containing equity, drawdown, and regime charts."""
    if not _ensure_output_directory(output_directory, logger):
        return None

    output_path = output_directory / f"{filename_stem}_equity.html"
    date_labels = [_index_to_date_string(index_value) for index_value in equity_curve.index]
    equity_values = [round(_coerce_float(value), 4) for value in equity_curve.tolist()]
    rolling_max = equity_curve.cummax()
    drawdown_series = ((equity_curve - rolling_max) / rolling_max.replace(0.0, np.nan)).fillna(0.0) * PERCENT_SCALE
    drawdown_values = [round(_coerce_float(value), 4) for value in drawdown_series.tolist()]

    regime_series = regime_frame.reindex(equity_curve.index)["regime"].fillna(MarketRegime.UNKNOWN.value).astype(str)
    regime_values = regime_series.tolist()
    regime_bands = _build_regime_bands(regime_series)
    regime_colors = {
        MarketRegime.BULL.value: HTML_BULL_BAND,
        MarketRegime.BEAR.value: HTML_BEAR_BAND,
        MarketRegime.SIDEWAYS.value: HTML_SIDEWAYS_BAND,
        MarketRegime.VOLATILE.value: HTML_VOLATILE_BAND,
        MarketRegime.UNKNOWN.value: HTML_UNKNOWN_BAND,
    }
    regime_color_map = {
        MarketRegime.BULL.value: "#4ade80",
        MarketRegime.BEAR.value: "#f87171",
        MarketRegime.SIDEWAYS.value: "#facc15",
        MarketRegime.VOLATILE.value: "#fb923c",
        MarketRegime.UNKNOWN.value: "#94a3b8",
    }
    regime_numeric_map = {
        MarketRegime.BULL.value: 4,
        MarketRegime.BEAR.value: 1,
        MarketRegime.SIDEWAYS.value: 2,
        MarketRegime.VOLATILE.value: 3,
        MarketRegime.UNKNOWN.value: 0,
    }
    regime_numeric_values = [regime_numeric_map.get(regime, 0) for regime in regime_values]
    regime_bar_colors = [regime_color_map.get(regime, regime_color_map[MarketRegime.UNKNOWN.value]) for regime in regime_values]

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Erisia Oracle — {ticker} Backtest Report ({mode.value})</title>
  <script src="{CHART_JS_CDN_URL}"></script>
  <style>
    :root {{
      color-scheme: dark;
      --bg: {HTML_BACKGROUND_COLOR};
      --panel: {HTML_PANEL_COLOR};
      --border: {HTML_BORDER_COLOR};
      --text: {HTML_TEXT_COLOR};
      --muted: {HTML_MUTED_TEXT_COLOR};
      --equity: {HTML_EQUITY_COLOR};
      --drawdown: {HTML_DRAWDOWN_COLOR};
      --grid: {HTML_GRID_COLOR};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Helvetica Neue", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(79, 209, 197, 0.08), transparent 28%),
        linear-gradient(180deg, #0b1220 0%, var(--bg) 42%, #0b1018 100%);
      color: var(--text);
    }}
    .shell {{
      max-width: 1320px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .header {{
      margin-bottom: 22px;
      padding: 22px 24px;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(17, 24, 39, 0.96), rgba(13, 17, 23, 0.92));
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.24);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: clamp(1.7rem, 2.4vw, 2.6rem);
      letter-spacing: 0.03em;
    }}
    .subtitle {{
      margin: 0;
      color: var(--muted);
      font-size: 0.98rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
    }}
    .panel {{
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 18px;
      background: rgba(17, 24, 39, 0.88);
      box-shadow: 0 14px 32px rgba(0, 0, 0, 0.18);
    }}
    .panel h2 {{
      margin: 0 0 12px;
      font-size: 1rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .panel canvas {{
      width: 100%;
      height: 340px;
    }}
    .panel.regime canvas {{
      height: 160px;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 14px;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .swatch {{
      width: 14px;
      height: 14px;
      border-radius: 3px;
      border: 1px solid rgba(255, 255, 255, 0.08);
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="header">
      <h1>Erisia Oracle — {ticker} Backtest Report ({mode.value})</h1>
      <p class="subtitle">Institutional backtest visualization with equity, drawdown, and regime context.</p>
    </section>
    <section class="grid">
      <article class="panel">
        <h2>Equity Curve</h2>
        <canvas id="equityChart"></canvas>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:{HTML_BULL_BAND};"></span>BULL</span>
          <span class="legend-item"><span class="swatch" style="background:{HTML_BEAR_BAND};"></span>BEAR</span>
          <span class="legend-item"><span class="swatch" style="background:{HTML_SIDEWAYS_BAND};"></span>SIDEWAYS</span>
          <span class="legend-item"><span class="swatch" style="background:{HTML_VOLATILE_BAND};"></span>VOLATILE</span>
        </div>
      </article>
      <article class="panel">
        <h2>Drawdown</h2>
        <canvas id="drawdownChart"></canvas>
      </article>
      <article class="panel regime">
        <h2>Regime Overlay</h2>
        <canvas id="regimeChart"></canvas>
      </article>
    </section>
  </div>
  <script>
    const labels = {json.dumps(date_labels)};
    const equityData = {json.dumps(equity_values)};
    const drawdownData = {json.dumps(drawdown_values)};
    const regimeData = {json.dumps(regime_numeric_values)};
    const regimeLabels = {json.dumps(regime_values)};
    const regimeBands = {json.dumps(regime_bands)};
    const regimeBandColors = {json.dumps(regime_colors)};
    const regimeBarColors = {json.dumps(regime_bar_colors)};
    const commonScales = {{
      x: {{
        ticks: {{ color: "{HTML_MUTED_TEXT_COLOR}", maxTicksLimit: 12 }},
        grid: {{ color: "{HTML_GRID_COLOR}" }},
      }},
      y: {{
        ticks: {{ color: "{HTML_MUTED_TEXT_COLOR}" }},
        grid: {{ color: "{HTML_GRID_COLOR}" }},
      }},
    }};
    const regimeBandPlugin = {{
      id: "regimeBandPlugin",
      beforeDatasetsDraw(chart) {{
        const xScale = chart.scales.x;
        const yScale = chart.scales.y;
        const ctx = chart.ctx;
        if (!xScale || !yScale) {{
          return;
        }}
        ctx.save();
        for (const band of regimeBands) {{
          const startPixel = xScale.getPixelForValue(band.start);
          const endPixel = xScale.getPixelForValue(band.end);
          const nextPixel = xScale.getPixelForValue(Math.min(band.end + 1, labels.length - 1));
          const rightEdge = Number.isFinite(nextPixel) ? nextPixel : endPixel;
          ctx.fillStyle = regimeBandColors[band.regime] || regimeBandColors.UNKNOWN;
          ctx.fillRect(startPixel, yScale.top, rightEdge - startPixel, yScale.bottom - yScale.top);
        }}
        ctx.restore();
      }},
    }};
    Chart.defaults.color = "{HTML_TEXT_COLOR}";
    Chart.defaults.borderColor = "{HTML_GRID_COLOR}";
    new Chart(document.getElementById("equityChart"), {{
      type: "line",
      data: {{
        labels,
        datasets: [{{
          label: "Equity",
          data: equityData,
          borderColor: "{HTML_EQUITY_COLOR}",
          backgroundColor: "rgba(79, 209, 197, 0.12)",
          borderWidth: 2,
          fill: false,
          pointRadius: 0,
          tension: 0.18,
        }}],
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{ mode: "index", intersect: false }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              afterBody(context) {{
                const index = context[0]?.dataIndex ?? 0;
                return `Regime: ${{regimeLabels[index] || "UNKNOWN"}}`;
              }},
            }},
          }},
        }},
        scales: commonScales,
      }},
      plugins: [regimeBandPlugin],
    }});
    new Chart(document.getElementById("drawdownChart"), {{
      type: "line",
      data: {{
        labels,
        datasets: [{{
          label: "Drawdown %",
          data: drawdownData,
          borderColor: "{HTML_DRAWDOWN_COLOR}",
          backgroundColor: "rgba(248, 113, 113, 0.24)",
          fill: "origin",
          pointRadius: 0,
          tension: 0.16,
          borderWidth: 2,
        }}],
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          ...commonScales,
          y: {{
            ...commonScales.y,
            ticks: {{
              color: "{HTML_MUTED_TEXT_COLOR}",
              callback(value) {{
                return `${{value}}%`;
              }},
            }},
          }},
        }},
      }},
    }});
    new Chart(document.getElementById("regimeChart"), {{
      type: "bar",
      data: {{
        labels,
        datasets: [{{
          label: "Regime",
          data: regimeData,
          backgroundColor: regimeBarColors,
          borderWidth: 0,
          barPercentage: 1.0,
          categoryPercentage: 1.0,
        }}],
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label(context) {{
                return regimeLabels[context.dataIndex] || "UNKNOWN";
              }},
            }},
          }},
        }},
        scales: {{
          x: {{ ticks: {{ display: false }}, grid: {{ display: false }} }},
          y: {{ display: false, suggestedMin: 0, suggestedMax: 4, grid: {{ display: false }} }},
        }},
      }},
    }});
  </script>
</body>
</html>
"""

    try:
        output_path.write_text(html_content, encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write equity HTML %s: %s", output_path, exc)
        return None

    return output_path


def _write_backtest_artifacts(
    report: BacktestReport,
    trades: list[TradeRecord],
    regime_frame: pd.DataFrame,
    equity_curve: pd.Series,
    mode: BacktestMode,
    logger: logging.Logger,
) -> tuple[RunArtifacts, str]:
    """Write all requested output artifacts and return their paths plus memory note."""
    timestamp = datetime.now(UTC).strftime(TIMESTAMP_FORMAT)
    filename_stem = f"{report.ticker}_{report.mode}_{timestamp}"
    trades_csv = _write_trade_log_csv(OUTPUT_DIRECTORY, filename_stem, trades, report, logger)
    report_json, erisia_memory_note = _write_summary_json(OUTPUT_DIRECTORY, filename_stem, report, logger)
    equity_html = _write_equity_curve_html(
        output_directory=OUTPUT_DIRECTORY,
        filename_stem=filename_stem,
        ticker=report.ticker,
        mode=mode,
        regime_frame=regime_frame,
        equity_curve=equity_curve,
        logger=logger,
    )
    return RunArtifacts(trades_csv=trades_csv, report_json=report_json, equity_html=equity_html), erisia_memory_note


def _record_backtest_run(db_path: Path, request: RunRequest, report: BacktestReport, report_path: Path | None, logger: logging.Logger) -> None:
    """Persist a completed backtest run summary to SQLite."""
    try:
        with sqlite3.connect(str(db_path)) as connection:
            connection.execute(
                """
                INSERT INTO backtest_runs (
                    run_id, ticker, mode, start_date, end_date, total_trades,
                    win_rate, sharpe_ratio, max_drawdown, report_path, run_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    str(uuid.uuid4()),
                    request.ticker,
                    request.mode.value,
                    request.start,
                    request.end,
                    report.total_trades,
                    report.win_rate,
                    report.sharpe_ratio,
                    report.max_drawdown,
                    str(report_path) if report_path is not None else None,
                    _utc_now_string(),
                ),
            )
            connection.commit()
    except sqlite3.Error as exc:
        logger.error("Failed to record backtest run for %s: %s", request.ticker, exc)


def _format_percent(value: float) -> str:
    """Format a unit interval value as a percentage string."""
    return f"{value * PERCENT_SCALE:.2f}%"


def _format_drawdown(value: float) -> str:
    """Format a drawdown fraction as a percentage string."""
    return f"{value * PERCENT_SCALE:.2f}%"


def _format_profit_factor(value: float) -> str:
    """Format profit factor with explicit infinity handling."""
    if math.isinf(value):
        return "inf"
    return f"{value:.3f}"


def _build_regime_table_lines(report: BacktestReport) -> list[str]:
    """Render the standard regime breakdown table for terminal output."""
    lines = ["Regime      Trades   Win Rate   Avg PnL"]
    display_order = [
        MarketRegime.BULL.value,
        MarketRegime.BEAR.value,
        MarketRegime.SIDEWAYS.value,
        MarketRegime.VOLATILE.value,
        MarketRegime.UNKNOWN.value,
    ]
    for regime in display_order:
        metrics = report.regime_breakdown.get(regime, {"trades": 0, "win_rate": 0.0, "avg_pnl": 0.0})
        lines.append(
            f"{regime:<10}  "
            f"{_coerce_int(metrics.get('trades')):>6}   "
            f"{_format_percent(_coerce_float(metrics.get('win_rate'))):>8}   "
            f"{_coerce_float(metrics.get('avg_pnl')):>8.2f}"
        )
    return lines


def _format_artifact_path(path: Path | None) -> str:
    """Format an artifact path for terminal display."""
    return str(path) if path is not None else "WRITE_FAILED"


def _format_war_room_report(
    request: RunRequest,
    report: BacktestReport,
    artifacts: RunArtifacts,
    erisia_memory_note: str,
    trades: list[TradeRecord],
) -> str:
    """Build the terminal report for a single completed ticker run."""
    regime_lines = "\n".join(_build_regime_table_lines(report))
    long_trades = sum(1 for trade in trades if trade.direction == PositionDirection.LONG.value)
    short_trades = sum(1 for trade in trades if trade.direction == PositionDirection.SHORT.value)
    long_wins = sum(
        1
        for trade in trades
        if trade.direction == PositionDirection.LONG.value and trade.pnl > 0.0
    )
    short_wins = sum(
        1
        for trade in trades
        if trade.direction == PositionDirection.SHORT.value and trade.pnl > 0.0
    )
    long_wr = (long_wins / long_trades * 100) if long_trades > 0 else 0.0
    short_wr = (short_wins / short_trades * 100) if short_trades > 0 else 0.0
    exit_lines = "Exit Reasons:\n"
    for reason, count in report.exit_reason_breakdown.items():
        exit_lines += f"  {reason:<12}: {count}\n"
    return (
        "\n"
        "==================== ERISIA WAR ROOM REPORT ====================\n"
        f"Ticker       : {request.ticker}\n"
        f"Mode         : {request.mode.value}\n"
        f"Date Range   : {request.start} -> {request.end}\n"
        f"Total Trades : {report.total_trades}\n"
        f"Win Rate     : {_format_percent(report.win_rate)}\n"
        f"Sharpe Ratio : {report.sharpe_ratio:.3f}\n"
        f"Max Drawdown : {_format_drawdown(report.max_drawdown)}\n"
        f"Profit Factor: {_format_profit_factor(report.profit_factor)}\n"
        "---------------------------------------------------------------\n"
        f"{regime_lines}\n"
        f"Long  Trades : {long_trades:>4} | Win Rate: {long_wr:.1f}%\n"
        f"Short Trades : {short_trades:>4} | Win Rate: {short_wr:.1f}%\n"
        f"{exit_lines}"
        "---------------------------------------------------------------\n"
        f"Trades CSV   : {_format_artifact_path(artifacts.trades_csv)}\n"
        f"Report JSON  : {_format_artifact_path(artifacts.report_json)}\n"
        f"Equity HTML  : {_format_artifact_path(artifacts.equity_html)}\n"
        "---------------------------------------------------------------\n"
        f"{erisia_memory_note}\n"
        "===============================================================\n"
    )


def _load_portfolio_tickers(portfolio_path: Path, logger: logging.Logger) -> list[str]:
    """Load a sequential backtest ticker list from config/portfolio.json."""
    try:
        raw_text = portfolio_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to read portfolio file %s: %s", portfolio_path, exc)
        return []

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in portfolio file %s: %s", portfolio_path, exc)
        return []

    tickers: list[str] = []
    if not isinstance(payload, list):
        logger.error("Portfolio file %s must contain a JSON list.", portfolio_path)
        return tickers

    for item in payload:
        if isinstance(item, str) and item.strip():
            tickers.append(item.strip().upper())
            continue
        if isinstance(item, dict):
            ticker_value = item.get("ticker")
            if isinstance(ticker_value, str) and ticker_value.strip():
                tickers.append(ticker_value.strip().upper())
                continue
        logger.warning("Skipping invalid portfolio entry: %s", item)

    return tickers


def _validate_date_inputs(start: str, end: str) -> None:
    """Validate CLI date inputs before a run begins."""
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    if start_dt >= end_dt:
        raise ValueError("start date must be earlier than end date")


def _run_single_backtest(request: RunRequest, logger: logging.Logger, emit_report: bool = True) -> RunResult:
    """Execute the full backtest pipeline for one ticker."""
    _ensure_database_schema(DATABASE_PATH, logger)

    try:
        market_frame = DataLayer(
            ticker=request.ticker,
            start=request.start,
            end=request.end,
            interval=request.interval,
            logger=logger,
        ).load()
    except ValueError as exc:
        logger.error("Skipping %s: %s", request.ticker, exc)
        return RunResult(request=request, report=None, artifacts=None)

    try:
        signal_layer = SignalLayer(mode=request.mode, db_path=DATABASE_PATH, logger=logger)
        signal_frame = signal_layer.generate_signals(market_frame, request.ticker)
        regime_frame = RegimeDetector(logger=logger).classify(signal_frame)
        regime_frame.attrs["ticker"] = request.ticker
        trades, equity_curve = ExecutionSimulator(
            initial_capital=request.capital,
            commission=DEFAULT_COMMISSION_RATE,
            slippage=DEFAULT_SLIPPAGE_RATE,
            logger=logger,
        ).simulate(regime_frame, metrics_cache=signal_layer._metrics_cache)
        report = AnalyticsEngine(logger=logger).compute(
            trades=trades,
            equity_curve=equity_curve,
            ticker=request.ticker,
            mode=request.mode,
        )
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
        logger.error("Backtest pipeline failed for %s: %s", request.ticker, exc)
        return RunResult(request=request, report=None, artifacts=None)

    artifacts, erisia_memory_note = _write_backtest_artifacts(
        report=report,
        trades=trades,
        regime_frame=regime_frame,
        equity_curve=equity_curve,
        mode=request.mode,
        logger=logger,
    )
    _record_backtest_run(
        db_path=DATABASE_PATH,
        request=request,
        report=report,
        report_path=artifacts.report_json,
        logger=logger,
    )
    if emit_report:
        print(_format_war_room_report(request, report, artifacts, erisia_memory_note, trades))
    return RunResult(request=request, report=report, artifacts=artifacts)


def _format_batch_summary(results: list[RunResult]) -> str:
    """Build the consolidated cross-ticker batch summary table."""
    lines = [
        "",
        "==================== PORTFOLIO SUMMARY ====================",
        "Ticker      Sharpe     Win Rate   Trades",
    ]
    sortable_results = [result for result in results if result.report is not None]
    sortable_results.sort(key=lambda result: cast(BacktestReport, result.report).sharpe_ratio, reverse=True)

    for result in sortable_results:
        report = cast(BacktestReport, result.report)
        lines.append(
            f"{report.ticker:<10}"
            f"{report.sharpe_ratio:>8.3f}   "
            f"{_format_percent(report.win_rate):>8}   "
            f"{report.total_trades:>6}"
        )

    lines.append("===========================================================")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the Erisia backtester."""
    parser = argparse.ArgumentParser(description="Erisia Oracle historical backtester")
    parser.add_argument("--ticker", required=True, type=str, help='Ticker symbol or "PORTFOLIO"')
    parser.add_argument("--start", required=True, type=str, help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, type=str, help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument(
        "--mode",
        default=BacktestMode.QUANT_ONLY.value,
        choices=[mode.value for mode in BacktestMode],
        help="Backtest execution mode",
    )
    parser.add_argument("--capital", default=DEFAULT_INITIAL_CAPITAL, type=float, help="Initial capital base")
    parser.add_argument("--interval", default="1d", type=str, help="yfinance interval string")
    return parser.parse_args(argv)


def run_from_erisia(
    ticker: str,
    start: str,
    end: str,
    mode: str = "QUANT_ONLY",
    capital: float = DEFAULT_INITIAL_CAPITAL,
    interval: str = "1d",
    logger: logging.Logger | None = None,
) -> RunResult:
    """
    Erisia Core integration entry point.
    Called by erisia_core.py in response to natural language commands.
    Returns the full RunResult so erisia_core.py can ingest the
    erisia_memory_note directly into the Hippocampus.
    Never prints to terminal — all output is returned as structured data.
    """
    _logger = logger or logging.getLogger(LOGGER_NAME)

    try:
        _validate_date_inputs(start, end)
    except ValueError as exc:
        _logger.error("Invalid date range passed from Erisia Core: %s", exc)
        return RunResult(
            request=RunRequest(
                ticker=ticker.upper(),
                start=start,
                end=end,
                mode=BacktestMode(mode.upper()),
                capital=capital,
                interval=interval,
            ),
            report=None,
            artifacts=None,
        )

    request = RunRequest(
        ticker=ticker.upper(),
        start=start,
        end=end,
        mode=BacktestMode(mode.upper()),
        capital=capital,
        interval=interval,
    )
    return _run_single_backtest(request, _logger, emit_report=False)


def run_portfolio_from_erisia(
    start: str,
    end: str,
    mode: str = "QUANT_ONLY",
    capital: float = DEFAULT_INITIAL_CAPITAL,
    interval: str = "1d",
    logger: logging.Logger | None = None,
) -> list[RunResult]:
    """
    Portfolio-level Erisia Core integration entry point.
    Loads tickers from config/portfolio.json and runs all sequentially.
    Returns list of RunResult for Hippocampus batch consolidation.
    Never prints to terminal — all output is returned as structured data.
    """
    _logger = logger or logging.getLogger(LOGGER_NAME)
    tickers = _load_portfolio_tickers(PORTFOLIO_PATH, _logger)
    if not tickers:
        _logger.error("No tickers found in portfolio for Erisia Core invocation.")
        return []

    results: list[RunResult] = []
    for ticker in tickers:
        result = run_from_erisia(
            ticker=ticker,
            start=start,
            end=end,
            mode=mode,
            capital=capital,
            interval=interval,
            logger=_logger,
        )
        results.append(result)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Erisia backtester CLI entry point."""
    logger = _configure_logging()
    args = parse_args(argv)

    ticker_argument = str(args.ticker).strip().upper()
    mode = BacktestMode(str(args.mode).strip().upper())
    capital = float(args.capital)

    if capital <= 0.0:
        logger.error("Initial capital must be positive, received %s", capital)
        return 1

    try:
        _validate_date_inputs(str(args.start), str(args.end))
    except ValueError as exc:
        logger.error("Invalid date range: %s", exc)
        return 1

    if ticker_argument == "PORTFOLIO":
        tickers = _load_portfolio_tickers(PORTFOLIO_PATH, logger)
        if not tickers:
            logger.error("No valid tickers found in %s", PORTFOLIO_PATH)
            return 1

        results: list[RunResult] = []
        for ticker in tickers:
            request = RunRequest(
                ticker=ticker,
                start=str(args.start),
                end=str(args.end),
                mode=mode,
                capital=capital,
                interval=str(args.interval),
            )
            results.append(_run_single_backtest(request, logger))

        successful_results = [result for result in results if result.report is not None]
        if successful_results:
            print(_format_batch_summary(successful_results))
            return 0

        logger.error("All portfolio tickers failed to complete.")
        return 1

    request = RunRequest(
        ticker=ticker_argument,
        start=str(args.start),
        end=str(args.end),
        mode=mode,
        capital=capital,
        interval=str(args.interval),
    )
    result = _run_single_backtest(request, logger)
    return 0 if result.report is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
