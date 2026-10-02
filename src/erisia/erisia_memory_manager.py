"""
LEGACY MODULE — erisia_memory_manager

Do not open a second Chroma PersistentClient here.
All semantic memory access delegates to MemoryStore (MemoryManager underneath).

New production code should import from erisia.erisia_memory_store instead.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from erisia.erisia_memory_store import get_memory_store

# Calculate the actual project root (kept for heuristics path compat)
BASE_DIR = Path(__file__).resolve().parents[2]
BASE_DIR_STR = str(BASE_DIR)

HEURISTICS_FILE = os.path.join(BASE_DIR_STR, "data", "erisia_heuristics.json")
GOAL_STACK_FILE = os.path.join(BASE_DIR_STR, "data", "erisia_goal_stack.json")
TOOLS_COLLECTION_NAME = "erisia_tools"
MEMORY_DIR = os.path.join(BASE_DIR_STR, "data", "erisia_memory")

# Shared lock: prefer the store's lock when available.
memory_lock = threading.RLock()


class _ToolsCollectionProxy:
    """Lazy proxy so callers keep using tools_collection.upsert/query."""

    def upsert(self, *args, **kwargs):
        store = get_memory_store()
        with store.lock:
            return store.tools_collection.upsert(*args, **kwargs)

    def query(self, *args, **kwargs):
        store = get_memory_store()
        with store.lock:
            return store.tools_collection.query(*args, **kwargs)

    def add(self, *args, **kwargs):
        store = get_memory_store()
        with store.lock:
            return store.tools_collection.add(*args, **kwargs)

    def get(self, *args, **kwargs):
        store = get_memory_store()
        with store.lock:
            return store.tools_collection.get(*args, **kwargs)


class _KnowledgeCollectionProxy:
    def add(self, *args, **kwargs):
        store = get_memory_store()
        with store.lock:
            return store.collection.add(*args, **kwargs)

    def query(self, *args, **kwargs):
        store = get_memory_store()
        with store.lock:
            return store.collection.query(*args, **kwargs)


# Legacy module-level names — NO second Chroma client.
tools_collection = _ToolsCollectionProxy()
knowledge_collection = _KnowledgeCollectionProxy()

# db_client is intentionally not a live second client.
db_client = None


def add_memory_document(document_text):
    """Thread-safe write into vector memory via MemoryStore."""
    if not document_text:
        return
    try:
        get_memory_store().add_memory(document_text)
    except Exception as e:
        print(f"[MEMORY WARNING]: Failed to write memory document. Error: {e}")


def query_memory_documents(query_text, n_results=5):
    """Thread-safe vector memory query via MemoryStore (Chroma-shaped result)."""
    return get_memory_store().query(query_text, n_results=n_results)


def _write_heuristics_file(rules):
    """Atomically persist heuristic rules as a JSON list."""
    safe_rules = [str(rule).strip() for rule in (rules or []) if str(rule).strip()]
    os.makedirs(os.path.dirname(HEURISTICS_FILE) or BASE_DIR_STR, exist_ok=True)
    temp_path = HEURISTICS_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(safe_rules, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, HEURISTICS_FILE)


def get_all_heuristics():
    """Load and return all saved heuristic rules."""
    with memory_lock:
        if not os.path.exists(HEURISTICS_FILE):
            _write_heuristics_file([])
            return []
        try:
            with open(HEURISTICS_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, list):
                _write_heuristics_file([])
                return []
            cleaned = [str(item).strip() for item in payload if str(item).strip()]
            if cleaned != payload:
                _write_heuristics_file(cleaned)
            return cleaned
        except Exception:
            _write_heuristics_file([])
            return []


def save_heuristic_rule(rule_text):
    """Persist a permanent coding/behavioral rule for future prompt injection."""
    normalized_rule = re.sub(r"\s+", " ", str(rule_text or "").strip())
    if not normalized_rule:
        return "[HEURISTIC ERROR]: rule_text is required."

    with memory_lock:
        existing_rules = get_all_heuristics()
        existing_lower = {str(rule).strip().lower() for rule in existing_rules}
        if normalized_rule.lower() in existing_lower:
            return f"[HEURISTIC]: Rule already exists -> {normalized_rule}"
        existing_rules.append(normalized_rule)
        _write_heuristics_file(existing_rules)
        return f"[HEURISTIC]: Saved -> {normalized_rule}"
