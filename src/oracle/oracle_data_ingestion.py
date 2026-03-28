from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
import calendar
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# External libraries (installed via requirements.txt)
import feedparser  # type: ignore
import yfinance as yf  # type: ignore
from pandas import DataFrame, Timestamp  # type: ignore

DEFAULT_DB_NAME = "oracle_memory.db"

MarketRow = Tuple[str, str, float, float, float, float, Optional[int]]
NewsRow = Tuple[str, str, str, str, Optional[float], str]


@dataclass
class DatabaseConfig:
    """Configuration for the Oracle SQLite ledger."""

    path: Path = Path(__file__).resolve().parents[2] / DEFAULT_DB_NAME


class OracleDatabase:
    """Lightweight SQLite wrapper with schema management and typed inserts."""

    def __init__(self, config: DatabaseConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.config.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize_schema(self) -> None:
        """Create required tables if they do not exist."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS market_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticker TEXT NOT NULL,
                        date TEXT NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume INTEGER,
                        UNIQUE (ticker, date)
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS news_sentiment (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticker TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        headline TEXT NOT NULL,
                        source TEXT,
                        sentiment_score REAL,
                        raw_summary TEXT,
                        UNIQUE (ticker, timestamp, headline)
                    );
                    """
                )
                conn.commit()
            self.logger.info("SQLite schema verified at %s", self.config.path)
        except sqlite3.Error as exc:
            self.logger.exception("Failed to initialize schema: %s", exc)
            raise

    def insert_market_data(self, rows: Sequence[MarketRow]) -> int:
        """Persist market rows. Returns number of inserted rows."""
        if not rows:
            return 0
        try:
            with self._connect() as conn:
                before = conn.total_changes
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO market_data
                    (ticker, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    rows,
                )
                conn.commit()
                return conn.total_changes - before
        except sqlite3.Error as exc:
            self.logger.exception("Failed to insert market data: %s", exc)
            raise

    def insert_news_items(self, rows: Sequence[NewsRow]) -> int:
        """Persist news rows. Returns number of inserted rows."""
        if not rows:
            return 0
        try:
            with self._connect() as conn:
                before = conn.total_changes
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO news_sentiment
                    (ticker, timestamp, headline, source, sentiment_score, raw_summary)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    rows,
                )
                conn.commit()
                return conn.total_changes - before
        except sqlite3.Error as exc:
            self.logger.exception("Failed to insert news items: %s", exc)
            raise

    def fetch_last_market_entries(self, limit: int) -> List[sqlite3.Row]:
        """Fetch the most recent market rows."""
        query = """
            SELECT ticker, date, open, high, low, close, volume
            FROM market_data
            ORDER BY date DESC
            LIMIT ?;
        """
        with self._connect() as conn:
            cursor = conn.execute(query, (limit,))
            return cursor.fetchall()

    def fetch_last_news_entries(self, limit: int) -> List[sqlite3.Row]:
        """Fetch the most recent news rows."""
        query = """
            SELECT ticker, timestamp, headline, source, sentiment_score, raw_summary
            FROM news_sentiment
            ORDER BY datetime(timestamp) DESC
            LIMIT ?;
        """
        with self._connect() as conn:
            cursor = conn.execute(query, (limit,))
            return cursor.fetchall()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.config.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


class MarketDataEngine:
    """Fetches historical market data using yfinance and stores it in SQLite."""

    def __init__(self, database: OracleDatabase, logger: logging.Logger) -> None:
        self.database = database
        self.logger = logger

    async def fetch_and_store_historical(self, ticker: str, days: int) -> int:
        """Download and persist N days of historical data for a ticker."""
        if days <= 0:
            raise ValueError("Parameter 'days' must be a positive integer.")

        try:
            df = await asyncio.to_thread(self._download_history, ticker, days)
        except Exception as exc:
            self.logger.exception("yfinance download failed for %s: %s", ticker, exc)
            raise

        if df.empty:
            self.logger.warning("No market data returned for %s", ticker)
            return 0

        rows: List[MarketRow] = []
        for idx, row in df.iterrows():
            date_str = self._normalize_date(idx)
            volume_val = int(row["Volume"]) if not self._is_nan(row["Volume"]) else None
            rows.append(
                (
                    ticker.upper(),
                    date_str,
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    volume_val,
                )
            )

        inserted = await asyncio.to_thread(self.database.insert_market_data, rows)
        self.logger.info(
            "Successfully ingested %s days of %s data (%s new rows)",
            days,
            ticker.upper(),
            inserted,
        )
        return inserted

    @staticmethod
    def _download_history(ticker: str, days: int) -> DataFrame:
        """Blocking helper to download data with yfinance."""
        period = f"{days}d"
        ticker_obj = yf.Ticker(ticker)
        df = ticker_obj.history(period=period)
        # Ensure consistent column casing
        df = df.rename(
            columns={col: col.capitalize() for col in df.columns}
        )
        return df

    @staticmethod
    def _normalize_date(value: object) -> str:
        if isinstance(value, Timestamp):
            return value.date().isoformat()
        if isinstance(value, datetime):
            return value.date().isoformat()
        return datetime.fromisoformat(str(value)).date().isoformat()

    @staticmethod
    def _is_nan(value: object) -> bool:
        try:
            if value is None:
                return False
            if isinstance(value, (int, float)):
                fval = float(value)
            else:
                fval = float(str(value))
            return fval != fval  # NaN check
        except (TypeError, ValueError):
            return False


class NewsRadar:
    """Parses Yahoo Finance RSS feeds and stores headlines for later scoring."""

    def __init__(self, database: OracleDatabase, logger: logging.Logger) -> None:
        self.database = database
        self.logger = logger

    @staticmethod
    def _as_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("title", "name", "value"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
        if isinstance(value, list):
            parts = [NewsRadar._as_text(item).strip() for item in value]
            return " ".join([p for p in parts if p])
        return str(value)

    async def fetch_latest_headlines(self, ticker: str) -> int:
        """Fetch and store the newest headlines for a ticker."""
        feed_url = f"https://finance.yahoo.com/rss/headline?s={ticker}"
        try:
            feed = await asyncio.to_thread(feedparser.parse, feed_url)
        except Exception as exc:
            self.logger.exception("Failed to parse RSS feed for %s: %s", ticker, exc)
            raise

        if not getattr(feed, "entries", []):
            self.logger.warning("No headlines returned for %s", ticker)
            return 0

        rows: List[NewsRow] = []
        for entry in feed.entries:
            headline = self._as_text(entry.get("title")).strip()
            if not headline:
                continue

            timestamp = self._extract_timestamp(entry)
            source = self._as_text(entry.get("source")).strip() or "Yahoo Finance"
            summary = self._as_text(entry.get("summary") or entry.get("description")).strip()
            rows.append(
                (
                    ticker.upper(),
                    timestamp,
                    headline,
                    source,
                    None,  # Sentiment to be computed in Pillar 2
                    summary,
                )
            )

        inserted = await asyncio.to_thread(self.database.insert_news_items, rows)
        self.logger.info(
            "Successfully ingested headlines for %s (%s new rows)",
            ticker.upper(),
            inserted,
        )
        return inserted

    @staticmethod
    def _extract_timestamp(entry: dict) -> str:
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            ts = calendar.timegm(published)
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def configure_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    return logging.getLogger("oracle.data.ingestion")


async def run_smoke_test() -> None:
    logger = configure_logger()
    db = OracleDatabase(DatabaseConfig(), logger)
    db.initialize_schema()

    market_engine = MarketDataEngine(db, logger)
    news_radar = NewsRadar(db, logger)

    try:
        await market_engine.fetch_and_store_historical("AAPL", 30)
        await news_radar.fetch_latest_headlines("AAPL")
    except Exception:
        logger.exception("Smoke test failed")
        return

    print("\nLast 3 market_data rows:")
    for row in db.fetch_last_market_entries(3):
        print(dict(row))

    print("\nLast 3 news_sentiment rows:")
    for row in db.fetch_last_news_entries(3):
        print(dict(row))


if __name__ == "__main__":
    asyncio.run(run_smoke_test())
