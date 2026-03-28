import os
import sqlite3
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_db_path() -> Path:
    raw = os.environ.get("ERISIA_EPISODIC_DB_PATH")
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate.resolve()
    return (PROJECT_ROOT / "erisia_memory.db").resolve()


DB_PATH = _resolve_db_path()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db():
    """Create the episodic memory database/table if it does not exist."""
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                event_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT
            )
            """
        )
        conn.commit()


def log_episode(event_type, content, metadata=None):
    """Insert a new episode row with the current timestamp."""
    init_db()
    if metadata is None:
        metadata = {}

    try:
        metadata_json = json.dumps(metadata, ensure_ascii=False)
    except (TypeError, ValueError):
        metadata_json = json.dumps({"_raw": str(metadata)}, ensure_ascii=False)

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO episodes (timestamp, event_type, content, metadata)
            VALUES (CURRENT_TIMESTAMP, ?, ?, ?)
            """,
            (str(event_type), str(content), metadata_json),
        )
        conn.commit()


def get_recent_context(limit=5):
    """Return the last N episodes as a readable multi-line context string."""
    init_db()
    with sqlite3.connect(str(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT timestamp, event_type, content, metadata
            FROM episodes
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    if not rows:
        return "No episodic memory recorded yet."

    rows.reverse()  # Oldest -> newest for easier narrative context.
    lines = []
    for ts, event_type, content, metadata_text in rows:
        parsed_meta = {}
        if metadata_text:
            try:
                parsed_meta = json.loads(metadata_text)
            except (json.JSONDecodeError, TypeError):
                parsed_meta = {"_raw": str(metadata_text)}

        lines.append(
            f"[{ts}] ({event_type}) {content}"
            + (f" | metadata={json.dumps(parsed_meta, ensure_ascii=False)}" if parsed_meta else "")
        )

    return "\n".join(lines)


def prune_and_reflect(api_client, summary_model="llama-3.1-8b-instant"):
    """
    Consolidate old episodic rows into a single reflection memory when DB grows.
    - No-op if total rows < 20
    - Summarizes oldest 15 rows
    - Deletes those 15 rows
    - Inserts one reflection row with the summary
    """
    init_db()

    with sqlite3.connect(str(DB_PATH)) as conn:
        total_rows = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        if int(total_rows) < 20:
            return

        oldest_rows = conn.execute(
            """
            SELECT id, timestamp, event_type, content
            FROM episodes
            ORDER BY id ASC
            LIMIT 15
            """
        ).fetchall()

    if not oldest_rows:
        return

    episode_ids = [row[0] for row in oldest_rows]
    raw_logs = "\n".join(
        f"[{row[1]}] ({row[2]}) {row[3]}"
        for row in oldest_rows
    )

    system_prompt = (
        "You are the subconscious memory manager. Summarize the following raw event logs into a "
        "single, concise paragraph of 'Core Memory' detailing what the user and AI accomplished."
    )

    summary_response = api_client.chat.completions.create(
        model=summary_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_logs},
        ],
        max_tokens=180,
    )

    summary = str(summary_response.choices[0].message.content or "").strip()
    if not summary:
        return

    placeholders = ",".join("?" for _ in episode_ids)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            f"DELETE FROM episodes WHERE id IN ({placeholders})",
            episode_ids,
        )
        conn.commit()

    log_episode("reflection", summary)
    return summary
