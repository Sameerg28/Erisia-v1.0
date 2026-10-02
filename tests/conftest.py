"""Offline isolation for runtime tests, including the pre-existing Phase 1 suite.

Production Python files are copied verbatim: __file__-relative writes therefore
land in pytest temporary storage before any runtime module is imported.
"""
import importlib
import hashlib
import shutil
import signal
import socket
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected network, process, or desktop action in offline test")


@pytest.fixture(scope="session", autouse=True)
def production_goals_unchanged():
    """Read-only guard over the real goal file for the entire test session."""
    path = ROOT / "data" / "erisia_goal_stack.json"

    def fingerprint():
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    before = fingerprint()

    def check():
        assert fingerprint() == before, "Test suite changed the production goal file"

    yield check
    check()


@pytest.fixture(autouse=True)
def offline_runtime(request, tmp_path, monkeypatch):
    if request.path.name not in {
        "test_phase1_state_routing.py", "test_phase1a_characterization.py",
        "test_phase1b_single_brain.py",
        "test_phase1c_goal_store.py",
        "test_phase1d_goal_persistence.py",
    }:
        yield None
        return

    import os
    import chromadb
    from chromadb.api.types import EmbeddingFunction
    from chromadb.config import Settings

    class LocalEmbedding(EmbeddingFunction):
        """Small deterministic vectors; no model downloads or semantic claims."""
        def __init__(self):
            pass

        def __call__(self, input):
            return [[float(text.lower().count(word) + 1) for word in
                     ("baseline", "apples", "legacy", "memory")] for text in input]

        @staticmethod
        def name():
            return "phase1a-local"

        def get_config(self):
            return {}

        @staticmethod
        def build_from_config(config):
            return LocalEmbedding()

    sandbox = tmp_path / "runtime"
    package = sandbox / "src" / "erisia"
    package.mkdir(parents=True)
    for source in (ROOT / "src" / "erisia").glob("*.py"):
        shutil.copyfile(source, package / source.name)
    (sandbox / "data").mkdir()
    monkeypatch.chdir(sandbox)
    monkeypatch.syspath_prepend(str(sandbox / "src"))
    saved = {k: v for k, v in sys.modules.items() if k == "erisia" or k.startswith("erisia.")}
    for name in saved:
        del sys.modules[name]
    for name in list(os.environ):
        if name.startswith("ERISIA_") or name.endswith("API_KEY"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ERISIA_EPISODIC_DB_PATH", str(sandbox / "episodes.db"))
    monkeypatch.setenv("ERISIA_ENABLE_META_REVIEW", "0")
    monkeypatch.setenv("ERISIA_ENABLE_VOICE", "0")
    monkeypatch.setenv("ERISIA_ENABLE_MCP", "0")

    def stub(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    stub("pyautogui", press=forbidden, screenshot=forbidden)
    stub("erisia.erisia_voice", VOICE_AVAILABLE=False,
         listen_for_speech=forbidden, get_voice_manager=forbidden, speak_text=forbidden)
    stub("erisia.erisia_runtime_services", initialize_optional_services=lambda: [],
         start_optional_mcp_service=forbidden)
    stub("erisia.erisia_llm", query_llm=forbidden,
         get_tavily_client=lambda: None, get_groq_client=lambda: None)
    stub("erisia.erisia_telemetry", init_telemetry=lambda *a, **k: None)
    # Windows implements asyncio's internal wakeup socketpair using loopback.
    # Permit only socketpair construction, not general loopback/API connections.
    real_socketpair = socket.socketpair
    real_connect = socket.socket.connect

    def local_socketpair(*args, **kwargs):
        with monkeypatch.context() as local:
            local.setattr(socket.socket, "connect", real_connect)
            return real_socketpair(*args, **kwargs)

    monkeypatch.setattr(socket, "socketpair", local_socketpair)
    for attr in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, attr, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(signal, "signal", Mock())

    real_client = chromadb.PersistentClient
    clients = []

    def local_client(path, **kwargs):
        assert Path(path).resolve().is_relative_to(tmp_path.resolve())
        client = real_client(path=str(path), settings=Settings(anonymized_telemetry=False))
        clients.append(client)

        class ClientProxy:
            def get_or_create_collection(self, name, **kw):
                return client.get_or_create_collection(name=name, embedding_function=LocalEmbedding(), **kw)

            def __getattr__(self, name):
                return getattr(client, name)

        return ClientProxy()

    monkeypatch.setattr(chromadb, "PersistentClient", local_client)
    cognition = importlib.import_module("erisia.erisia_cognition")
    monkeypatch.setattr(cognition.PassiveCognitionEngine, "start", forbidden)
    try:
        yield sandbox
    finally:
        for system in {client._system for client in clients}:
            system.stop()
        for name in list(sys.modules):
            if name == "erisia" or name.startswith("erisia."):
                del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture
def core(offline_runtime, monkeypatch):
    module = importlib.import_module("erisia.erisia_core")
    monkeypatch.setattr(module, "initialize_advanced_cognition", Mock())
    monkeypatch.setattr(module, "goal_stack", None)
    monkeypatch.setattr(module, "passive_cognition_engine", None)
    monkeypatch.setattr(module, "chat_history", [])
    monkeypatch.setattr(module, "consecutive_errors", 0)
    monkeypatch.setattr(module, "load_dynamic_skills", lambda base, **kw: (base, {}))
    return module
