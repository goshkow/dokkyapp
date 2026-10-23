from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from koddi.storage.db import runtime_path


def mcp_config_path() -> Path:
    return runtime_path("mcp", "servers.json")


def _load_servers() -> dict[str, dict]:
    path = mcp_config_path()
    if not path.is_file():
        raise ValueError("MCP config not found")
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("MCP config must be an object")
    servers = config.get("servers", config.get("mcpServers"))
    if not isinstance(servers, dict) or any(
        not isinstance(value, dict) for value in servers.values()
    ):
        raise ValueError("MCP config requires a servers or mcpServers object")
    return servers


def _timeout(server: dict) -> float:
    timeout = float(server.get("timeout_seconds", 30))
    if not 0 < timeout <= 600:
        raise ValueError("MCP timeout_seconds must be between 0 and 600")
    return timeout


@asynccontextmanager
async def _session(server: dict):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    if server.get("disabled", False):
        raise ValueError("MCP server is disabled")
    transport = server.get("transport", server.get("type")) or (
        "http" if server.get("url") else "stdio"
    )
    timeout = _timeout(server)
    if transport == "stdio":
        command = server.get("command")
        args = server.get("args", [])
        env = server.get("env")
        if not isinstance(command, str) or not command:
            raise ValueError("stdio MCP requires a command")
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise ValueError("stdio MCP args must be an array of strings")
        if env is not None and (
            not isinstance(env, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in env.items()
            )
        ):
            raise ValueError("stdio MCP env must contain string values")
        client = stdio_client(
            StdioServerParameters(command=command, args=args, env=env, cwd=server.get("cwd"))
        )
    elif transport in {"http", "streamable-http", "streamable_http", "sse"}:
        url = server.get("url", "")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("HTTP MCP requires an http(s) URL without embedded credentials")
        headers = server.get("headers", {})
        if not isinstance(headers, dict) or any(
            not isinstance(value, str) for value in headers.values()
        ):
            raise ValueError("MCP headers must contain string values")
        factory = sse_client if transport == "sse" else streamablehttp_client
        client = factory(url, headers=headers, timeout=timeout, sse_read_timeout=timeout)
    else:
        raise ValueError(f"Unsupported MCP transport: {transport}")
    async with asyncio.timeout(timeout):
        async with client as streams:
            async with ClientSession(
                streams[0], streams[1], read_timeout_seconds=timedelta(seconds=timeout)
            ) as session:
                await session.initialize()
                yield session


async def _list_tools(session) -> list[dict]:
    tools = []
    cursor = None
    cursors = set()
    names = set()
    for _ in range(100):
        result = await session.list_tools(cursor=cursor)
        for tool in result.tools:
            item = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            if item["name"] in names:
                raise ValueError("MCP discovery returned duplicate tool names")
            names.add(item["name"])
            tools.append(item)
        cursor = result.nextCursor
        if not cursor:
            return tools
        if cursor in cursors:
            raise ValueError("MCP discovery returned a repeated pagination cursor")
        cursors.add(cursor)
    raise ValueError("MCP discovery exceeded 100 pages")


def _error(exc: BaseException, server: dict | None = None) -> str:
    if isinstance(exc, BaseExceptionGroup):
        message = "; ".join(_error(child, server) for child in exc.exceptions)
    else:
        message = str(exc) or type(exc).__name__
    for key in ("headers", "env"):
        values = (server or {}).get(key, {})
        if isinstance(values, dict):
            for value in values.values():
                if isinstance(value, str) and value:
                    message = message.replace(value, "[redacted]")
    return message


def run_sync(coroutine):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="koddi-mcp") as pool:
        return pool.submit(asyncio.run, coroutine).result()


async def mcp_tools_list_async() -> dict:
    try:
        servers = _load_servers()
    except Exception as exc:
        return {"ok": False, "error": _error(exc), "config_path": str(mcp_config_path())}
    tools = []
    statuses = []
    errors = []
    for name, server in servers.items():
        if server.get("disabled", False):
            statuses.append({"server": name, "status": "disabled"})
            continue
        try:
            async with _session(server) as session:
                discovered = await _list_tools(session)
            allowed = server.get("allowedTools")
            if allowed is not None:
                if not isinstance(allowed, list) or any(
                    not isinstance(item, str) for item in allowed
                ):
                    raise ValueError("allowedTools must be an array of tool names")
                discovered = [tool for tool in discovered if tool["name"] in allowed]
            tools.extend({**tool, "server": name} for tool in discovered)
            statuses.append({"server": name, "status": "connected", "tool_count": len(discovered)})
        except Exception as exc:
            errors.append({"server": name, "error": _error(exc, server)})
            statuses.append({"server": name, "status": "error"})
    return {
        "ok": not errors,
        "servers": list(servers),
        "tools": tools,
        "server_statuses": statuses,
        "errors": errors,
        "config_path": str(mcp_config_path()),
    }


def mcp_tools_list() -> dict:
    return run_sync(mcp_tools_list_async())


async def mcp_tool_call(server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict:
    server = None
    try:
        servers = _load_servers()
        server = servers.get(server_name)
        if server is None:
            raise ValueError(f"MCP server {server_name} not configured")
        allowed = server.get("allowedTools")
        if allowed is not None and (not isinstance(allowed, list) or tool_name not in allowed):
            return {"ok": False, "blocked": True, "error": "MCP tool is not in allowedTools"}
        async with _session(server) as session:
            tools = await _list_tools(session)
            tool = next((tool for tool in tools if tool["name"] == tool_name), None)
            if tool is None:
                raise ValueError(f"MCP tool {tool_name} was not discovered on server {server_name}")
            from jsonschema import Draft202012Validator

            schema = tool["inputSchema"]
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(arguments)
            result = await session.call_tool(tool_name, arguments)
            return {
                "ok": not result.isError,
                "server": server_name,
                "name": tool_name,
                "result": result.model_dump(mode="json", by_alias=True, exclude_none=True),
            }
    except Exception as exc:
        return {"ok": False, "error": _error(exc, server)}


def mcp_tool_call_sync(server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict:
    return run_sync(mcp_tool_call(server_name, tool_name, arguments))
