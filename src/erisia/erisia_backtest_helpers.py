from __future__ import annotations

import re
import logging
from datetime import UTC, datetime
from typing import Any


INTENT_TICKER_STOPWORDS: frozenset[str] = frozenset({
    "A", "I", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO",
    "TO", "UP", "US", "WE", "AND", "ARE", "BUT", "CSV", "FOR",
    "GET", "HAD", "HAS", "HER", "HIM", "HIS", "HOW", "ITS",
    "LET", "MAY", "NOT", "NOW", "OLD", "ONE", "OUR", "OUT",
    "OWN", "RUN", "SAY", "SHE", "THE", "TOO", "TWO", "USE",
    "WAS", "WHO", "WHY", "OPEN", "SHOW", "FROM", "MOST", "LAST",
    "EACH", "BOTH", "INTO", "OVER", "SUCH", "THAN", "THAT",
    "THEM", "THEN", "THEY", "THIS", "WITH", "WILL", "YOUR",
    "EVERY", "RECENT", "TRADES", "BACKTEST", "PORTFOLIO",
})


def infer_topic(message: str) -> str:
    """Infer topic category from message content."""
    lower = message.lower()
    if any(word in lower for word in ["backtest", "oracle", "trade", "stock", "market"]):
        return "trading"
    if any(word in lower for word in ["build", "code", "implement", "fix", "error"]):
        return "development"
    if any(word in lower for word in ["goal", "plan", "mission", "objective"]):
        return "planning"
    if any(word in lower for word in ["who am i", "identity", "report", "profile"]):
        return "self_reflection"
    return "general"


def detect_backtest_intent(user_input: str) -> dict[str, str] | None:
    """
    Detect natural language backtest commands from the user.
    Returns a parameter dict if intent is detected, None otherwise.
    """
    normalized = user_input.strip().lower()
    file_operation_phrases = (
        "open the", "show me", "read the", "load the",
        "display the", "print the",
    )
    if any(phrase in normalized for phrase in file_operation_phrases):
        return None

    portfolio_patterns = [
        r"portfolio backtest",
        r"backtest.*portfolio",
        r"test.*portfolio",
        r"run portfolio",
    ]
    for pattern in portfolio_patterns:
        if re.search(pattern, normalized):
            year_matches = re.findall(r"\b(20\d{2})\b", normalized)
            start = f"{year_matches[0]}-01-01" if len(year_matches) > 0 else "2022-01-01"
            end = f"{year_matches[1]}-12-31" if len(year_matches) > 1 else "2024-12-31"
            return {"type": "portfolio", "start": start, "end": end}

    year_pattern = r"\b(20\d{2})\b"
    backtest_triggers = [
        "backtest", "back test", "back-test",
        "test oracle", "run oracle", "oracle test",
        "run a backtest", "run backtest",
    ]

    if not any(trigger in normalized for trigger in backtest_triggers):
        return None

    tickers_found = [
        t for t in re.findall(r"\b([A-Z]{1,5})\b", user_input)
        if t not in INTENT_TICKER_STOPWORDS
    ]
    years_found = re.findall(year_pattern, user_input)

    ticker = next(iter(tickers_found), "AAPL")
    start = f"{years_found[0]}-01-01" if years_found else "2022-01-01"
    end = f"{years_found[1]}-12-31" if len(years_found) > 1 else "2024-12-31"
    return {"type": "single", "ticker": ticker, "start": start, "end": end}


def extract_best_worst_regimes(report: Any) -> tuple[str, str]:
    """Extract best and worst regime names from a BacktestReport."""
    try:
        regime_breakdown = report.regime_breakdown
        populated = [
            (regime, data)
            for regime, data in regime_breakdown.items()
            if isinstance(data, dict) and data.get("trades", 0) > 0
        ]
        if not populated:
            return "UNKNOWN", "UNKNOWN"
        best = max(populated, key=lambda x: float(x[1].get("avg_pnl", 0.0)))[0]
        worst = min(populated, key=lambda x: float(x[1].get("avg_pnl", 0.0)))[0]
        return best, worst
    except (AttributeError, TypeError, ValueError):
        return "UNKNOWN", "UNKNOWN"


def utc_now_string() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def feed_backtest_to_reasoning_engine(
    report: Any,
    graph_memory: Any,
    logger: logging.Logger,
) -> None:
    """Convert backtest results into causal graph knowledge."""
    if report is None or graph_memory is None:
        return

    try:
        ticker = str(getattr(report, "ticker", "UNKNOWN"))
        win_rate = float(getattr(report, "win_rate", 0.0))
        sharpe = float(getattr(report, "sharpe_ratio", 0.0))
        regime_breakdown = getattr(report, "regime_breakdown", {})

        if win_rate > 0.5:
            graph_memory.add_memory_relation(
                ticker,
                "has_positive_edge_in",
                f"QUANT_ONLY strategy (win_rate={win_rate:.0%})",
            )
        else:
            graph_memory.add_memory_relation(
                ticker,
                "underperforms_in",
                f"QUANT_ONLY strategy (win_rate={win_rate:.0%})",
            )

        if isinstance(regime_breakdown, dict):
            for regime, data in regime_breakdown.items():
                if not isinstance(data, dict):
                    continue
                trades = int(data.get("trades", 0))
                regime_win_rate = float(data.get("win_rate", 0.0))
                avg_pnl = float(data.get("avg_pnl", 0.0))
                if trades == 0:
                    continue

                if regime_win_rate > 0.6:
                    graph_memory.add_memory_relation(regime, "is_favorable_regime_for", f"{ticker} trading")
                elif regime_win_rate < 0.35:
                    graph_memory.add_memory_relation(regime, "causes_losses_for", f"{ticker} trading")

                if avg_pnl < 0 and regime == "BEAR":
                    graph_memory.add_memory_relation("BEAR regime", "causes", f"long entry losses on {ticker}")

        if sharpe < 0:
            graph_memory.add_memory_relation(
                f"{ticker} QUANT_ONLY",
                "causes",
                "negative risk-adjusted returns",
            )

        logger.info("Backtest results for %s fed to reasoning engine", ticker)
    except Exception as exc:
        logger.error("Failed to feed backtest to reasoning: %s", exc)
