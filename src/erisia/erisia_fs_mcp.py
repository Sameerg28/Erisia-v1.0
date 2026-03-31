"""
Erisia Filesystem MCP Server — Lightweight Python Implementation
=================================================================
Provides filesystem operations as an MCP server via stdio.
No Node.js required — pure Python, works on low-end devices.

Tools:
  - read_file        — Read file contents
  - write_file       — Write/create a file
  - list_directory   — List directory contents
  - file_info        — Get file/directory metadata
  - search_files     — Search for files by pattern
  - create_directory — Create a directory

Usage:
  python -m erisia.erisia_fs_mcp
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("erisia.fs_mcp")

_MESSAGE_ID = 0


async def run_server():
    """Run the filesystem MCP server via stdio."""
    stdin = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(stdin)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    stdout = sys.stdout.buffer

    async def send_response(response: dict):
        payload = json.dumps(response).encode("utf-8") + b"\n"
        stdout.write(payload)
        await asyncio.get_event_loop().run_in_executor(None, stdout.flush)

    async def read_message() -> dict | None:
        line = await stdin.readline()
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    initialized = False

    while True:
        msg = await read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            initialized = True
            if msg_id is not None:
                await send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "erisia-filesystem", "version": "0.1"},
                    },
                })

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list" and msg_id is not None:
            await send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": [
                    {
                        "name": "read_file",
                        "description": "Read the contents of a file",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "File path"},
                            },
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "write_file",
                        "description": "Write content to a file (creates or overwrites)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "File path"},
                                "content": {"type": "string", "description": "File content"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                    {
                        "name": "list_directory",
                        "description": "List contents of a directory",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Directory path"},
                            },
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "file_info",
                        "description": "Get file or directory metadata",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "File or directory path"},
                            },
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "search_files",
                        "description": "Search for files matching a pattern",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Starting directory"},
                                "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.py')"},
                            },
                            "required": ["path", "pattern"],
                        },
                    },
                    {
                        "name": "create_directory",
                        "description": "Create a directory (including parent directories)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Directory path to create"},
                            },
                            "required": ["path"],
                        },
                    },
                ]},
            })

        elif method == "tools/call" and msg_id is not None:
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await _handle_tool(tool_name, arguments)
            await send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result,
            })

        elif msg_id is not None:
            await send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


async def _handle_tool(name: str, args: dict) -> dict:
    """Handle a tool call and return MCP content result."""
    try:
        if name == "read_file":
            path = Path(args["path"])
            if not path.exists():
                return _error(f"File not found: {path}")
            if path.is_dir():
                return _error(f"Path is a directory: {path}")
            content = path.read_text(encoding="utf-8", errors="replace")
            return _text(content[:50000])

        elif name == "write_file":
            path = Path(args["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"], encoding="utf-8")
            return _text(f"Successfully wrote to {path}")

        elif name == "list_directory":
            path = Path(args["path"])
            if not path.exists():
                return _error(f"Directory not found: {path}")
            if not path.is_dir():
                return _error(f"Path is not a directory: {path}")
            entries = []
            for entry in sorted(path.iterdir()):
                prefix = "[DIR] " if entry.is_dir() else "[FILE] "
                entries.append(f"{prefix}{entry.name}")
            return _text("\n".join(entries) if entries else f"Directory is empty: {path}")

        elif name == "file_info":
            path = Path(args["path"])
            if not path.exists():
                return _error(f"Path not found: {path}")
            stat = path.stat()
            info = {
                "name": path.name,
                "path": str(path.resolve()),
                "type": "directory" if path.is_dir() else "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
            }
            return _text(json.dumps(info, indent=2))

        elif name == "search_files":
            path = Path(args["path"])
            pattern = args["pattern"]
            if not path.is_dir():
                return _error(f"Not a directory: {path}")
            import fnmatch
            matches = []
            for root, dirs, files in os.walk(path):
                for name in files:
                    if fnmatch.fnmatch(name, pattern):
                        matches.append(str(Path(root) / name))
                for d in dirs:
                    if fnmatch.fnmatch(d, pattern):
                        matches.append(str(Path(root) / d))
            return _text("\n".join(matches[:100]) if matches else f"No matches for '{pattern}' in {path}")

        elif name == "create_directory":
            path = Path(args["path"])
            path.mkdir(parents=True, exist_ok=True)
            return _text(f"Directory created: {path}")

        else:
            return _error(f"Unknown tool: {name}")

    except Exception as exc:
        return _error(str(exc))


def _text(content: str) -> dict:
    return {"content": [{"type": "text", "text": content}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_server())
