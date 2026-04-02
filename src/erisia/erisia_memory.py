import chromadb
import shutil
import logging
import threading
import uuid
from pathlib import Path

logger = logging.getLogger("erisia_memory")

class MemoryManager:
    """SINGLE RESPONSIBILITY: Manage Episodic and Semantic Vector Memory."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.lock = threading.RLock()
        self.db_client = self._initialize_chromadb()
        # Primary knowledge collection
        self.collection = self.db_client.get_or_create_collection(name="erisia_knowledge")
        # Secondary tools collection for semantic tool retrieval
        self.tools_collection = self.db_client.get_or_create_collection(name="erisia_tools")

    def _initialize_chromadb(self):
        """Safely boot the vector database with corruption recovery."""
        try:
            return chromadb.PersistentClient(path=str(self.memory_dir))
        except Exception as _chroma_exc:
            logger.warning(f"ChromaDB corruption detected: {_chroma_exc}. Attempting recovery...")
            _backup = Path(f"{self.memory_dir}_corrupted_backup")
            try:
                if _backup.exists():
                    shutil.rmtree(str(_backup))
                shutil.copytree(str(self.memory_dir), str(_backup))
                logger.info(f"Backup saved to {_backup}")
                shutil.rmtree(str(self.memory_dir))
                self.memory_dir.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(self.memory_dir))
                logger.info("ChromaDB recovery successful")
                return client
            except Exception as recovery_error:
                logger.critical(f"ChromaDB recovery FAILED: {recovery_error}")
                raise RuntimeError(f"Cannot recover ChromaDB: {recovery_error}") from recovery_error

    def add_memory(self, document: str, metadata: dict | None = None, doc_id: str | None = None):
        """Insert a new memory into the vector store."""
        if not document:
            return None
        doc_id = doc_id or str(uuid.uuid4())
        meta = metadata or {}
        if not meta:
            meta = {"timestamp": str(uuid.uuid4())}
        with self.lock:
            try:
                self.collection.add(
                    documents=[document],
                    metadatas=[meta],
                    ids=[doc_id]
                )
                return doc_id
            except Exception as e:
                logger.error(f"Failed to add memory: {e}")
                return None

    def query_memory(self, query_text: str, n_results: int = 5) -> list[str]:
        """Retrieve semantically relevant memories."""
        if not query_text:
            return []
        with self.lock:
            try:
                results = self.collection.query(
                    query_texts=[query_text],
                    n_results=n_results
                )
                if results:
                    documents = results.get("documents")
                    if documents and len(documents) > 0:
                        return documents[0]
            except Exception as e:
                logger.error(f"Failed to query memory: {e}")
        return []

    def upsert_tool(self, tool_id: str, document: str, metadata: dict):
        """Thread-safe upsert into the tools collection."""
        with self.lock:
            try:
                self.tools_collection.upsert(
                    ids=[tool_id],
                    documents=[document],
                    metadatas=[metadata]
                )
            except Exception as e:
                logger.error(f"Failed to upsert tool '{tool_id}': {e}")

    def query_tools(self, query_text: str, n_results: int = 3):
        """Thread-safe query of the tools collection."""
        with self.lock:
            try:
                return self.tools_collection.query(
                    query_texts=[query_text],
                    n_results=n_results
                )
            except Exception as e:
                logger.error(f"Failed to query tools: {e}")
                return {}
