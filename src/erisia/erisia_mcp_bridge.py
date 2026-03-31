"""
Erisia v0.2 — MCP Bridge Client
==================================
Connects Erisia to external MCP servers via stdio subprocesses.
All servers are lightweight Python implementations (no Node.js needed).

This allows Erisia to use external MCP tools internally AND expose them
to external agents that connect to Erisia's MCP server.

External MCP Servers (all free, no API keys):
  - mcp-server-fetch      — Web content fetching (pip install)
  - mcp-server-time       — Time/timezone utilities (pip install)
  - erisia_fs_mcp         — Local filesystem ops (built-in)
  - erisia_think_mcp      — Sequential thinking (built-in)

Usage:
    from erisia.erisia_mcp_bridge import MCPBridge
    bridge = MCPBridge()
    bridge.initialize_all()
    tools = bridge.get_all_tools()
    result = await bridge.call_tool("fetch", {"url": "https://example.com"})
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("erisia.mcp_bridge")


class MCPClientConnection:
    """Manages a single MCP server subprocess connection via stdio."""

    def __init__(self, name: str, command: list[str], cwd: str | None = None):
        self.name = name
        self.command = command
        self.cwd = cwd
        self._process: subprocess.Popen | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tools: list[dict] = []
        self._message_id = 0
        self._pending: dict[int, asyncio.Future] = {}

    async def start(self) -> bool:
        """Start the MCP server subprocess and initialize connection."""
        try:
            env_vars = {"PYTHONIOENCODING": "utf-8"}
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env={**_clean_env(), **env_vars},
            )

            assert self._process.stdin and self._process.stdout

            self._reader = self._process.stdout
            self._writer = self._process.stdin

            self._read_loop = asyncio.create_task(self._read_messages())

            await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "erisia-bridge", "version": "0.2"},
            })

            await self._send_notification("notifications/initialized", {})

            tools_result = await self._send_request("tools/list", {})
            self._tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
            logger.info(f"MCP bridge '{self.name}': connected with {len(self._tools)} tools")
            return True

        except Exception as exc:
            logger.warning(f"MCP bridge '{self.name}': failed to start ({exc})")
            return False

    async def stop(self):
        """Stop the MCP server subprocess."""
        try:
            if hasattr(self, "_read_loop"):
                self._read_loop.cancel()
            if self._writer:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
            if self._process:
                try:
                    self._process.terminate()
                    await asyncio.wait_for(self._process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
        except Exception as exc:
            logger.warning(f"MCP bridge '{self.name}': error stopping ({exc})")

    @property
    def tools(self) -> list[dict]:
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the remote MCP server."""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        content = result.get("content", [])
        parts = []
        for item in content:
            if item.get("type") == "text":
                parts.append(item["text"])
            elif item.get("type") == "image":
                parts.append(f"[Image: {item.get('mimeType', 'unknown')}]")
        return "\n".join(parts) if parts else json.dumps(result)

    async def _send_request(self, method: str, params: dict) -> Any:
        """Send a JSON-RPC request and wait for response."""
        self._message_id += 1
        msg_id = self._message_id

        message = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        payload = json.dumps(message) + "\n"
        assert self._writer
        self._writer.write(payload.encode("utf-8"))
        await self._writer.drain()

        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"MCP request timeout: {method}")

    async def _send_notification(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        payload = json.dumps(message) + "\n"
        assert self._writer
        self._writer.write(payload.encode("utf-8"))
        await self._writer.drain()

    async def _read_messages(self):
        """Read and dispatch messages from the MCP server."""
        assert self._reader
        buffer = b""
        while True:
            try:
                chunk = await self._reader.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line.decode("utf-8"))
                        await self._handle_message(message)
                    except json.JSONDecodeError:
                        logger.warning(f"MCP bridge '{self.name}': invalid JSON: {line[:100]}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"MCP bridge '{self.name}': read error ({exc})")
                break

    async def _handle_message(self, message: dict):
        """Handle an incoming JSON-RPC message."""
        if "id" in message:
            msg_id = message["id"]
            future = self._pending.pop(msg_id, None)
            if future:
                if "error" in message:
                    future.set_exception(Exception(message["error"].get("message", "Unknown error")))
                else:
                    future.set_result(message.get("result", {}))


def _clean_env() -> dict[str, str]:
    """Get a clean environment with only essential variables."""
    import os
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    erisia_src = str(Path(__file__).resolve().parents[1])
    current_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = erisia_src + (os.pathsep + current_path if current_path else "")
    return env


class MCPBridge:
    """
    Bridge between Erisia and external MCP servers.
    Manages lifecycle of multiple MCP server connections.
    """

    def __init__(self):
        self._connections: dict[str, MCPClientConnection] = {}
        self._tool_map: dict[str, str] = {}

    def register_server(self, name: str, command: list[str], cwd: str | None = None):
        """Register an external MCP server."""
        self._connections[name] = MCPClientConnection(name, command, cwd)

    async def initialize_all(self) -> dict[str, bool]:
        """Start all registered MCP servers."""
        results = {}
        for name, conn in self._connections.items():
            success = await conn.start()
            results[name] = success
            if success:
                for tool in conn.tools:
                    tool_name = tool.get("name", "")
                    if tool_name:
                        self._tool_map[tool_name] = name
        return results

    async def stop_all(self):
        """Stop all MCP server connections."""
        for conn in self._connections.values():
            await conn.stop()
        self._connections.clear()
        self._tool_map.clear()

    def get_all_tools(self) -> list[dict]:
        """Get all tools from all connected MCP servers."""
        all_tools = []
        for conn in self._connections.values():
            all_tools.extend(conn.tools)
        return all_tools

    def get_tool_schema(self, tool_name: str) -> dict | None:
        """Get the JSON schema for a specific tool (for OpenAI function calling format)."""
        for conn in self._connections.values():
            for tool in conn.tools:
                if tool.get("name") == tool_name:
                    return {
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "parameters": tool.get("inputSchema", {
                                "type": "object",
                                "properties": {},
                            }),
                        },
                    }
        return None

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by routing to the appropriate MCP server."""
        server_name = self._tool_map.get(tool_name)
        if not server_name:
            return f"Error: unknown tool '{tool_name}'"
        conn = self._connections.get(server_name)
        if not conn:
            return f"Error: server '{server_name}' not connected"
        try:
            return await conn.call_tool(tool_name, arguments)
        except Exception as exc:
            return f"Error calling {tool_name}: {exc}"

    @property
    def is_running(self) -> bool:
        return bool(self._tool_map)


_bridge_instance: MCPBridge | None = None


def get_bridge() -> MCPBridge:
    """Get or create the singleton MCP bridge."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = _create_default_bridge()
    return _bridge_instance


def _create_default_bridge() -> MCPBridge:
    """Create bridge with default external MCP servers."""
    bridge = MCPBridge()

    python = sys.executable
    erisia_dir = Path(__file__).resolve().parent

    bridge.register_server("filesystem", [python, "-m", "erisia.erisia_fs_mcp"])
    bridge.register_server("sequential_thinking", [python, "-m", "erisia.erisia_think_mcp"])

    for pkg, module in [("mcp_server_fetch", "mcp_server_fetch"), ("mcp_server_time", "mcp_server_time")]:
        try:
            __import__(pkg)
            bridge.register_server(module.replace("_", "-"), [python, "-m", module])
        except ImportError:
            logger.warning(f"Optional MCP server '{pkg}' not installed, skipping")

    return bridge


def initialize_bridge_sync() -> dict[str, bool]:
    """Synchronous wrapper for bridge initialization."""
    bridge = get_bridge()
    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(bridge.initialize_all())
        return results
    finally:
        loop.close()


def stop_bridge_sync():
    """Synchronous wrapper for bridge shutdown."""
    global _bridge_instance
    if _bridge_instance:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_bridge_instance.stop_all())
        finally:
            loop.close()
        _bridge_instance = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Erisia MCP Bridge — Testing")
    print("=" * 40)

    results = initialize_bridge_sync()
    for name, success in results.items():
        status = "connected" if success else "failed"
        print(f"  {name}: {status}")

    bridge = get_bridge()
    tools = bridge.get_all_tools()
    print(f"\nTotal tools available: {len(tools)}")
    for tool in tools:
        print(f"  - {tool['name']}: {tool.get('description', '')[:60]}")

    stop_bridge_sync()
    print("\nBridge stopped.")
