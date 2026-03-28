from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, TypedDict

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError  # type: ignore

DEFAULT_DB_NAME = "oracle_memory.db"
MODEL_NAME = "ServiceNow-AI/Apriel-1.5-15b-Thinker"
SYSTEM_PROMPT = """
        You are a ruthless, institutional Quantitative AI. You are analyzing daily technical indicators and sentiment to execute a trade.

        You now have Institutional Vision. Weigh the following indicators:
        1. Moving Averages (SMA 5 & 14): Determine the baseline trend.
        2. RSI (14): Identify oversold (<30) or overbought (>70) conditions.
        3. MACD: Look for trend acceleration (Histogram expanding) or crossovers.
        4. Volume Surge: A surge > 1.5 implies strong institutional backing. A surge < 0.8 means retail noise.
        5. ATR (14): High ATR means high volatility (higher risk/reward).
        6. Sentiment: -1.0 (Bearish) to 1.0 (Bullish).

        You are STRICTLY FORBIDDEN from writing any preambles, reasoning steps, or conversational text.
        You must output ONLY a raw, valid JSON object. Do not wrap it in markdown formatting or backticks.
        
        The JSON must contain exactly three keys:
        {
            "action": "BUY",  // Must be BUY, SELL, or HOLD
            "confidence": 75, // Integer between 0 and 100
            "reasoning": "One short sentence explaining the logic using MACD, Volume, or Trend."
        }
        """


class IntelPayload(TypedDict):
    ticker: str
    close: Optional[float]
    sma_5: Optional[float]
    sma_14: Optional[float]
    rsi_14: Optional[float]
    avg_sentiment: Optional[float]


@dataclass
class DatabaseConfig:
    """Configuration for the Oracle SQLite ledger."""

    path: Path = Path(__file__).resolve().parents[2] / DEFAULT_DB_NAME


class ChiefInvestmentOfficer:
    """Aggregates intel and generates trade decisions via Together AI."""

    def __init__(self, db_config: Optional[DatabaseConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        load_dotenv(Path(__file__).resolve().parents[2] / "config" / ".env")
        self.db_config = db_config or DatabaseConfig()
        self.logger = logger or logging.getLogger("oracle.warroom")
        self.client: Optional[OpenAI] = self._init_client()
        self._ensure_db_dir()

    def _ensure_db_dir(self) -> None:
        self.db_config.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_config.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_client(self) -> Optional[OpenAI]:
        api_key = os.getenv("TOGETHER_API_KEY")
        if not api_key:
            self.logger.error("TOGETHER_API_KEY is not set. Decision engine cannot call Together AI.")
            return None
        return OpenAI(api_key=api_key, base_url="https://api.together.xyz/v1")

    def gather_intel(self, ticker: str) -> IntelPayload:
        """Fetch latest technicals and average recent sentiment for a ticker."""
        intel: IntelPayload = {
            "ticker": ticker.upper(),
            "close": None,
            "sma_5": None,
            "sma_14": None,
            "rsi_14": None,
            "avg_sentiment": None,
        }

        with self._connect() as conn:
            price_row = conn.execute(
                """
                SELECT close, sma_5, sma_14, rsi_14
                FROM market_data
                WHERE ticker = ?
                ORDER BY date DESC
                LIMIT 1;
                """,
                (ticker.upper(),),
            ).fetchone()

            sentiment_row = conn.execute(
                """
                SELECT AVG(sentiment_score) AS avg_sentiment
                FROM (
                    SELECT sentiment_score
                    FROM news_sentiment
                    WHERE ticker = ? AND sentiment_score IS NOT NULL
                    ORDER BY datetime(timestamp) DESC
                    LIMIT 10
                );
                """,
                (ticker.upper(),),
            ).fetchone()

        if price_row:
            intel["close"] = price_row["close"]
            intel["sma_5"] = price_row["sma_5"]
            intel["sma_14"] = price_row["sma_14"]
            intel["rsi_14"] = price_row["rsi_14"]
        else:
            self.logger.warning("No market data found for %s", ticker.upper())

        if sentiment_row and sentiment_row["avg_sentiment"] is not None:
            intel["avg_sentiment"] = float(sentiment_row["avg_sentiment"])
        else:
            self.logger.warning("No sentiment data found for %s", ticker.upper())

        return intel

    def generate_trade_signal(self, intel: IntelPayload) -> Dict[str, object]:
        """Send intel to Together AI and parse a strict JSON trade signal."""
        if self.client is None:
            return {"action": "HOLD", "confidence": 0, "reasoning": "TOGETHER_API_KEY not configured"}

        user_prompt = json.dumps(intel)
        try:
            response = self.client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=2048,  # Increased slightly to allow the Thinker room to write out its JSON
                temperature=0.0,
            )
        except OpenAIError as exc:
            self.logger.exception("LLM decision call failed: %s", exc)
            return {"action": "HOLD", "confidence": 0, "reasoning": "LLM call failed"}

        raw_text = self._extract_text(response)
        
        # --- THE JSON SNIPER PATCH ---
        decision: Dict[str, object] = {}
        try:
            # Find the first '{' and the last '}' to extract only the JSON payload
            start_idx = raw_text.find('{')
            end_idx = raw_text.rfind('}')
            
            if start_idx != -1 and end_idx != -1:
                clean_json = raw_text[start_idx:end_idx+1]
                decision = json.loads(clean_json)
            else:
                raise ValueError("No brackets found in response.")
        except Exception as e:
            self.logger.error("Invalid JSON extraction: %s | Raw text: %s", e, raw_text)
            return {"action": "HOLD", "confidence": 0, "reasoning": "Invalid model response"}

        action = str(decision.get("action", "HOLD")).upper()
        if action not in {"BUY", "SELL", "HOLD"}:
            action = "HOLD"
        confidence_raw = decision.get("confidence", 0)
        confidence = int(confidence_raw) if isinstance(confidence_raw, (int, float, str)) else 0
        reasoning = str(decision.get("reasoning", "No reasoning provided"))

        return {
            "action": action,
            "confidence": max(0, min(100, confidence)),
            "reasoning": reasoning,
        }

    @staticmethod
    def _extract_text(response: object) -> str:
        """
        Safely pull text content from OpenAI response.
        Supports both list-of-parts and plain string content formats.
        """
        try:
            message = response.choices[0].message  # type: ignore[attr-defined]
            content = message.content  # type: ignore[attr-defined]
            if isinstance(content, list):
                return "".join(part.get("text", "") for part in content if isinstance(part, dict))
            return str(content)
        except Exception:
            return ""


def configure_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    return logging.getLogger("oracle.warroom")


def format_war_room_report(ticker: str, intel: dict, decision: dict) -> str:
    close_px = float(intel.get("close", 0.0))
    sma5 = float(intel.get("sma_5", 0.0))
    sma14 = float(intel.get("sma_14", 0.0))
    rsi = float(intel.get("rsi_14", 0.0))
    macd = float(intel.get("macd_line", 0.0))
    surge = float(intel.get("volume_surge", 0.0))
    atr = float(intel.get("atr_14", 0.0))
    sentiment = intel.get("avg_sentiment", "None")

    action = decision.get("action", "HOLD")
    confidence = decision.get("confidence", 0)
    reasoning = decision.get("reasoning", "No reasoning provided.")

    return f"""
==================== WAR ROOM REPORT ====================
TICKER: {ticker.upper()}
---- INSTITUTIONAL INTEL ----
Close        : ${close_px:.2f}
Volume Surge : {surge:.2f}x Normal Volume
SMA_5 / 14   : {sma5:.2f} / {sma14:.2f}
RSI_14       : {rsi:.2f}
MACD Line    : {macd:.2f}
ATR (Volat.) : {atr:.2f}
Avg Sentiment: {sentiment}
---- DECISION ----
ACTION     : {action}
CONFIDENCE : {confidence}%
REASONING  : {reasoning}
=========================================================
"""

def run_demo() -> None:
    logger = configure_logger()
    cio = ChiefInvestmentOfficer(logger=logger)
    try:
        intel = cio.gather_intel("AAPL")
        decision = cio.generate_trade_signal(intel)
    except Exception:
        logger.exception("War Room demo failed.")
        return

    report = format_war_room_report("AAPL", intel, decision)  # type: ignore
    print(report)


if __name__ == "__main__":
    run_demo()
