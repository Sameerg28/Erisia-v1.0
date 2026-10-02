"""
MemoryStore — single access boundary for semantic Chroma memory.

Authoritative implementation: MemoryManager (used by erisia_core production path).
erisia_memory_manager is a legacy facade that must not open a second Chroma client.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

from erisia.erisia_memory import MemoryManager

logger = logging.getLogger("erisia.memory_store")

_default_store: Optional["MemoryStore"] = None
_default_store_lock = threading.Lock()


class MemoryStore:
    """
    Thin facade over MemoryManager.

    Exposes the same surface core already uses so callers can migrate
    incrementally without changing cognitive behavior.
    """

    def __init__(self, memory_dir: str | Path | None = None, manager: MemoryManager | None = None):
        if manager is not None:
            self._manager = manager
        else:
            if memory_dir is None:
                from erisia.erisia_config import get_config

                memory_dir = get_config().paths.memory_dir
            self._manager = MemoryManager(Path(memory_dir))

    @property
    def manager(self) -> MemoryManager:
        return self._manager

    @property
    def memory_dir(self) -> Path:
        return self._manager.memory_dir

    @property
    def lock(self) -> threading.RLock:
        return self._manager.lock

    @property
    def collection(self):
        return self._manager.collection

    @property
    def tools_collection(self):
        return self._manager.tools_collection

    @property
    def db_client(self):
        return self._manager.db_client

    def add_memory(
        self,
        document: str,
        metadata: dict | None = None,
        doc_id: str | None = None,
    ) -> str | None:
        return self._manager.add_memory(document, metadata=metadata, doc_id=doc_id)

    def query_memory(self, query_text: str, n_results: int = 5) -> list[str]:
        return self._manager.query_memory(query_text, n_results=n_results)

    def upsert_tool(self, tool_id: str, document: str, metadata: dict) -> None:
        self._manager.upsert_tool(tool_id, document, metadata)

    def query_tools(self, query_text: str, n_results: int = 3) -> Any:
        return self._manager.query_tools(query_text, n_results=n_results)

    # Aliases used by legacy memory_manager callers
    def add(self, document_text: str) -> str | None:
        return self.add_memory(document_text)

    def query(self, query_text: str, n_results: int = 5) -> Any:
        """Return Chroma-shaped query result for legacy callers."""
        if not query_text:
            return {"documents": [[]]}
        with self.lock:
            try:
                return self.collection.query(
                    query_texts=[query_text],
                    n_results=n_results,
                )
            except Exception as exc:
                logger.error("MemoryStore.query failed: %s", exc)
                return {"documents": [[]]}


def get_memory_store(memory_dir: str | Path | None = None) -> MemoryStore:
    global _default_store
    if memory_dir is not None:
        with _default_store_lock:
            if (
                _default_store is not None
                and Path(_default_store.memory_dir).resolve()
                == Path(memory_dir).resolve()
            ):
                return _default_store
        return MemoryStore(memory_dir=memory_dir)

    with _default_store_lock:
        if _default_store is None:
            _default_store = MemoryStore()
        return _default_store


def bind_memory_store(store: MemoryStore) -> MemoryStore:
    global _default_store
    with _default_store_lock:
        _default_store = store
    return store


def reset_memory_store_for_tests() -> None:
    global _default_store
    with _default_store_lock:
        _default_store = None
