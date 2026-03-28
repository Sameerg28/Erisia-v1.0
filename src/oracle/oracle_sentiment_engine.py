from __future__ import annotations
import re
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# Dynamically locate the config/.env file
project_root = Path(__file__).resolve().parents[2]
env_path = project_root / "config" / ".env"

# Load the keys into the environment
load_dotenv(dotenv_path=env_path)
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from openai import OpenAI, OpenAIError  # type: ignore

DEFAULT_DB_NAME = "oracle_memory.db"
SYSTEM_PROMPT = (
    "You are a quantitative financial AI. Read the headline and summary. "
    "Return ONLY a single float value between -1.0 (Extreme Bearish) and 1.0 (Extreme Bullish). "
    "0.0 is neutral. No formatting, no markdown, no explanation."
)


@dataclass
class DatabaseConfig:
    """Configuration for the Oracle SQLite ledger."""

    path: Path = Path(__file__).resolve().parents[2] / DEFAULT_DB_NAME


class SentimentProcessor:
    """Scores unscored headlines using Together AI and persists the results."""

    def __init__(self, db_config: Optional[DatabaseConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.db_config = db_config or DatabaseConfig()
        self.logger = logger or logging.getLogger("oracle.sentiment")
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
            self.logger.error("TOGETHER_API_KEY is not set in the environment.")
            return None
        return OpenAI(
            api_key=api_key,
            base_url="https://api.together.xyz/v1",
        )

    def fetch_unscored_news(self, limit: int = 10) -> List[sqlite3.Row]:
        """Retrieve unscored news entries."""
        query = """
            SELECT id, headline, raw_summary
            FROM news_sentiment
            WHERE sentiment_score IS NULL
            ORDER BY id ASC
            LIMIT ?;
        """
        with self._connect() as conn:
            cursor = conn.execute(query, (limit,))
            return cursor.fetchall()

    def analyze_sentiment(self, headline: str, summary: str) -> float:
        if self.client is None:
            self.logger.error("Sentiment client unavailable (missing TOGETHER_API_KEY). Returning neutral score.")
            return 0.0

        try:
            # We update the prompt to force the model to put its final answer in a specific box
            sys_prompt = (
                "You are a quantitative financial AI. Analyze the text and determine market sentiment "
                "from -1.0 (Extreme Bearish) to 1.0 (Extreme Bullish). 0.0 is neutral. "
                "You may think out loud, but you MUST put your final float score inside <score> tags. "
                "Example: <score>0.75</score>"
            )
            
            try:
                response = self.client.chat.completions.create(
                    model="ServiceNow-AI/Apriel-1.5-15b-Thinker",
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": f"Headline: {headline}\nSummary: {summary}"},
                    ],
                    temperature=0.1,
                    max_tokens=200,
                )
            except OpenAIError as exc:
                self.logger.exception("Together AI sentiment call failed: %s", exc)
                return 0.0

            content = self._extract_text(response)
            if not content:
                raise ValueError("Empty sentiment response content.")
            
            # Use Regex to snipe the number out of the <score> tags
            match = re.search(r"<score>\s*([-\d\.]+)\s*</score>", content)
            
            if match:
                score_str = match.group(1)
            else:
                # Fallback: if it forgets the tags, grab the very last number it typed
                numbers = re.findall(r"[-+]?\d*\.?\d+", content)
                if not numbers:
                    raise ValueError(f"No decimals found in response: {content[:50]}...")
                score_str = numbers[-1]
                
            score = float(score_str)
            
            # Clamp the score to ensure it never exceeds our -1.0 to 1.0 bounds
            return max(-1.0, min(1.0, score))
            
        except Exception as e:
            self.logger.error(f"Sentiment parsing failed: {e}")
            return 0.0

    @staticmethod
    def _extract_text(response: object) -> str:
        """
        Safely pull text content from OpenAI response.
        Supports both list-of-parts and plain string content formats.
        """
        try:
            message = response.choices[0].message  # type: ignore[attr-defined]
            content = message.content  # type: ignore[attr-defined]
            if isinstance(content, list):  # Newer OpenAI responses may return content parts
                return "".join(part.get("text", "") for part in content if isinstance(part, dict))
            return str(content)
        except Exception:
            return ""

    def process_batch(self, limit: int = 10) -> int:
        """Fetch unscored headlines, score them, and persist results. Returns count processed."""
        rows = self.fetch_unscored_news(limit=limit)
        if not rows:
            self.logger.info("No unscored news items found.")
            return 0

        updated = 0
        with self._connect() as conn:
            for row in rows:
                score = self.analyze_sentiment(row["headline"], row["raw_summary"] or "")
                try:
                    conn.execute(
                        "UPDATE news_sentiment SET sentiment_score = ? WHERE id = ?;",
                        (score, row["id"]),
                    )
                    updated += 1
                    self.logger.info("Scored ID %s: %+0.3f", row["id"], score)
                except sqlite3.Error as exc:
                    self.logger.exception("Failed to update sentiment for ID %s: %s", row["id"], exc)
            conn.commit()
        return updated

    def fetch_recent_scored(self, limit: int = 5) -> List[sqlite3.Row]:
        """Retrieve most recently scored rows for verification."""
        query = """
            SELECT id, ticker, timestamp, headline, sentiment_score
            FROM news_sentiment
            WHERE sentiment_score IS NOT NULL
            ORDER BY id DESC
            LIMIT ?;
        """
        with self._connect() as conn:
            cursor = conn.execute(query, (limit,))
            return cursor.fetchall()


def configure_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    return logging.getLogger("oracle.sentiment")


def run_demo() -> None:
    logger = configure_logger()
    processor = SentimentProcessor(logger=logger)
    try:
        processed = processor.process_batch(limit=10)
        logger.info("Batch processing complete. %s rows updated.", processed)
    except Exception:
        logger.exception("Sentiment batch failed.")
        return

    print("\nRecently scored rows:")
    for row in processor.fetch_recent_scored(limit=5):
        print(dict(row))


if __name__ == "__main__":
    run_demo()
