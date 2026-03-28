import os
import uuid
import json
import threading
import chromadb
import re
import datetime
from pathlib import Path

# Calculate the actual project root
BASE_DIR = Path(__file__).resolve().parents[2]
BASE_DIR_STR = str(BASE_DIR)

# Paths
MEMORY_DIR = os.path.join(BASE_DIR_STR, "data", "erisia_memory")
HEURISTICS_FILE = os.path.join(BASE_DIR_STR, "data", "erisia_heuristics.json")
GOAL_STACK_FILE = os.path.join(BASE_DIR_STR, "data", "erisia_goal_stack.json")
TOOLS_COLLECTION_NAME = "erisia_tools"

memory_lock = threading.RLock()

# --- MEMORY SETUP ---
db_client = chromadb.PersistentClient(path=MEMORY_DIR)
knowledge_collection = db_client.get_or_create_collection(name="erisia_knowledge")
tools_collection = db_client.get_or_create_collection(name=TOOLS_COLLECTION_NAME)

def add_memory_document(document_text):
    """Thread-safe write into vector memory."""
    if not document_text:
        return
    try:
        with memory_lock:
            knowledge_collection.add(documents=[document_text], ids=[str(uuid.uuid4())])
    except Exception as e:
        print(f"[MEMORY WARNING]: Failed to write memory document. Error: {e}")

def query_memory_documents(query_text, n_results=5):
    """Thread-safe vector memory query."""
    with memory_lock:
        return knowledge_collection.query(query_texts=[query_text], n_results=n_results)

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
            return f"[HEURISTIC NO-OP]: Rule already stored -> {normalized_rule}"
        existing_rules.append(normalized_rule)
        _write_heuristics_file(existing_rules)

    return f"[HEURISTIC SAVED]: {normalized_rule}"

def _normalize_goal_items(raw):
    goals = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                goals.append(item.strip())
            elif isinstance(item, dict):
                for key in ("title", "goal", "text", "name"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        goals.append(val.strip())
                        break
    elif isinstance(raw, dict):
        nested = raw.get("goals") or raw.get("items") or raw.get("stack")
        if isinstance(nested, list):
            goals.extend(_normalize_goal_items(nested))
    return goals

def _load_subconscious_goal_stack():
    with memory_lock:
        if not os.path.exists(GOAL_STACK_FILE):
            return []
        try:
            with open(GOAL_STACK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            goals = _normalize_goal_items(data)
            return goals if goals else []
        except Exception:
            return []

def _save_subconscious_goal_stack(goals):
    safe_goals = [str(g).strip() for g in goals if str(g).strip()]
    with memory_lock:
        try:
            os.makedirs(os.path.dirname(GOAL_STACK_FILE), exist_ok=True)
            with open(GOAL_STACK_FILE, "w", encoding="utf-8") as f:
                json.dump(safe_goals, f, indent=2)
        except Exception:
            pass
