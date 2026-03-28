from __future__ import annotations
from .oracle_execution import ExecutionBroker

import pandas as pd
import yfinance as yf
from .oracle_math_engine import QuantitativeEngine

import asyncio
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Ensure project root and src are importable when run as a script
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
for path_entry in (PROJECT_ROOT, SRC_DIR):
    if str(path_entry) not in sys.path:
        sys.path.append(str(path_entry))

from oracle.oracle_data_ingestion import (
    DatabaseConfig as IngestDBConfig,
    MarketDataEngine,
    NewsRadar,
    OracleDatabase,
)
from oracle.oracle_sentiment_engine import (
    DatabaseConfig as SentimentDBConfig,
    SentimentProcessor,
)
from oracle.oracle_math_engine import (
    DatabaseConfig as MathDBConfig,
    TechnicalAnalyst,
    QuantitativeEngine,
)
from oracle.oracle_war_room import (
    DatabaseConfig as WarDBConfig,
    ChiefInvestmentOfficer,
    format_war_room_report,
)

DEFAULT_DB_NAME = "oracle_memory.db"
DEFAULT_WATCHLIST = ["AAPL", "TSLA", "MSFT"]


@dataclass
class FeedbackLedger:
    """Persists human feedback for trade decisions."""

    db_path: Path = Path(__file__).resolve().parents[2] / DEFAULT_DB_NAME

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ticker TEXT,
                    ai_action TEXT,
                    ai_confidence INTEGER,
                    ai_reasoning TEXT,
                    user_approved BOOLEAN,
                    user_feedback TEXT
                );
                """
            )
            conn.commit()

    def log_interaction(
        self,
        ticker: str,
        ai_action: str,
        ai_confidence: int,
        ai_reasoning: str,
        user_approved: Optional[bool],
        user_feedback: Optional[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_journal
                (ticker, ai_action, ai_confidence, ai_reasoning, user_approved, user_feedback)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    ticker.upper(),
                    ai_action,
                    ai_confidence,
                    ai_reasoning,
                    user_approved,
                    user_feedback,
                ),
            )
            conn.commit()


class OracleFramework:
    """Orchestrates the four pillars and captures human feedback."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        base_dir = Path(__file__).resolve().parents[2]
        load_dotenv(base_dir / "config" / ".env")

        self.logger = logger or logging.getLogger("oracle.master")
        db_path = base_dir / DEFAULT_DB_NAME

        ingest_config = IngestDBConfig(path=db_path)
        math_config = MathDBConfig(path=db_path)
        sentiment_config = SentimentDBConfig(path=db_path)
        war_config = WarDBConfig(path=db_path)

        self.database = OracleDatabase(ingest_config, self.logger)
        self.market_engine = MarketDataEngine(self.database, self.logger)
        self.news_radar = NewsRadar(self.database, self.logger)
        self.sentiment_processor = SentimentProcessor(db_config=sentiment_config, logger=self.logger)
        self.technical_analyst = TechnicalAnalyst(db_config=math_config, logger=self.logger)
        self.cio = ChiefInvestmentOfficer(db_config=war_config, logger=self.logger)
        self.feedback_ledger = FeedbackLedger(db_path=db_path)

        self.watchlist = self._load_watchlist(base_dir / "config" / "portfolio.json")

    def _load_watchlist(self, path: Path) -> List[str]:
        if not path.exists():
            self.logger.warning("Watchlist not found at %s; using default %s", path, DEFAULT_WATCHLIST)
            return DEFAULT_WATCHLIST
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list) and all(isinstance(t, str) for t in data):
                return data
            self.logger.warning("Invalid watchlist format; using default %s", DEFAULT_WATCHLIST)
            return DEFAULT_WATCHLIST
        except Exception as exc:
            self.logger.error("Failed to read watchlist: %s; using default %s", exc, DEFAULT_WATCHLIST)
            return DEFAULT_WATCHLIST

    async def _ingest_and_score(self, ticker: str) -> None:
        await self.market_engine.fetch_and_store_historical(ticker, 30)
        await self.news_radar.fetch_latest_headlines(ticker)
        # Score newly ingested news (batch across all tickers, safe for now)
        self.sentiment_processor.process_batch(limit=25)

    def execute_morning_briefing(self) -> None:
        for idx, ticker in enumerate(self.watchlist, start=1):
            if idx > 1:
                proceed_raw = input(f"\n[Erisia]: Proceed to analyze {ticker.upper()}? (Y/N): ").strip().lower()
                if not proceed_raw.startswith("y"):
                    print("[Erisia]: Morning Briefing paused by user.")
                    break

            print(f"\n=== Analyzing {ticker.upper()} ===")
            try:
                asyncio.run(self._ingest_and_score(ticker))
                
                # --- INSTITUTIONAL MATH UPGRADE (PYLANCE PROOF) ---
                import typing
                import yfinance as yf
                import pandas as pd
                
                # By typing as Any, Pylance stops worrying about 'None'
                df: typing.Any = yf.download(ticker, period="6mo", progress=False)
                
                if hasattr(df, "columns") and isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                
                engine = QuantitativeEngine(logger=self.logger)
                quant_data = engine.calculate_indicators(df)
                
                raw_intel = self.cio.gather_intel(ticker)
                # Force the payload into a standard dictionary so Pylance allows .update()
                intel = dict(raw_intel) if isinstance(raw_intel, dict) else getattr(raw_intel, "__dict__", {})
                intel.update(quant_data) 
                # --------------------------------------------------
                
                decision = self.cio.generate_trade_signal(intel)  # type: ignore
            except Exception:
                self.logger.exception("Pipeline failed for %s", ticker)
                continue

            report = format_war_room_report(ticker, intel, decision)
            print(report)

            action = str(decision.get("action", "HOLD")).upper()
            confidence_raw = decision.get("confidence", 0)
            try:
                confidence = int(float(str(confidence_raw)))
            except Exception:
                confidence = 0
            reasoning = str(decision.get("reasoning", ""))

            if action in {"BUY", "SELL"}:
                approval_raw = input("[Erisia]: Do you authorize this trade? (Y/N): ").strip().lower()
                user_approved = approval_raw.startswith("y")
                user_feedback = input("[Erisia]: Please provide your reasoning for the journal: ").strip()
                
                # Log to the SQLite Journal
                try:
                    self.feedback_ledger.log_interaction(
                        ticker=ticker,
                        ai_action=action,
                        ai_confidence=confidence,
                        ai_reasoning=reasoning,
                        user_approved=user_approved,
                        user_feedback=user_feedback,
                    )
                except Exception:
                    self.logger.exception("Failed to log feedback for %s", ticker)
                
                # --- THE SANDBOX EXECUTION ROUTER ---
                if user_approved:
                    print(f"\n[Erisia]: Authorization confirmed. Routing to Execution Broker...")
                    broker = ExecutionBroker(logger=self.logger)
                    
                    # Extract the live price directly from the intel payload safely
                    try:
                        raw_price = intel.get("close")
                        current_price = float(str(raw_price)) if raw_price is not None else 0.0
                    except Exception:
                        current_price = 0.0
                    
                    if current_price > 0:
                        execution_result = broker.execute_trade(
                            ticker=ticker, 
                            action=action, 
                            confidence=confidence, 
                            current_price=current_price
                        )
                        print(f"[Execution Result]: {execution_result}")
                    else:
                        print("[Execution Error]: Could not retrieve a valid current price for math.")
                else:
                    print(f"\n[Erisia]: Authorization denied. Trade cancelled.")
                # ------------------------------------

            else:
                # AUTO-HOLD Logic
                try:
                    self.feedback_ledger.log_interaction(
                        ticker=ticker,
                        ai_action=action,
                        ai_confidence=confidence,
                        ai_reasoning=reasoning,
                        user_approved=None,
                        user_feedback="AUTO-HOLD (no user action required)",
                    )
                except Exception:
                    self.logger.exception("Failed to log feedback for %s", ticker)


def configure_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    return logging.getLogger("oracle.master")


def run_briefing() -> None:
    logger = configure_logger()
    framework = OracleFramework(logger=logger)
    framework.execute_morning_briefing()


if __name__ == "__main__":
    run_briefing()
