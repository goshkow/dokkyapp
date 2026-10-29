from __future__ import annotations

import hashlib

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from koddi.integrations.mcp import mcp_config_path, mcp_tools_list_async, plugin_servers


async def discover_agent_tools() -> tuple[list[dict], dict[str, tuple[str, str]], list[dict]]:
    """Отдаёт модели точные схемы MCP, а исполнение пропускает через политику."""
    if not mcp_config_path().is_file() and not plugin_servers():
        return [], {}, []
    result = await mcp_tools_list_async()
    schemas, bindings = [], {}
    errors = list(result.get("errors", []))
    for item in result.get("tools", []):
        try:
            schema = item["inputSchema"]
            Draft202012Validator.check_schema(schema)
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ValueError("MCP input schema must describe an object")
            identity = f"{item['server']}\0{item['name']}"
            name = "mcp_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
            if name in bindings:
                raise ValueError("Duplicate MCP tool binding")
            bindings[name] = (item["server"], item["name"])
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"MCP {item['server']}/{item['name']}: "
                        + item.get("description", "External tool")
                        + " External tools remain subject to user approval and may affect external resources.",
                        "parameters": schema,
                    },
                }
            )
        except (ValueError, KeyError, TypeError, SchemaError) as exc:
            errors.append({"server": item.get("server"), "error": str(exc)})
    return schemas, bindings, errors
