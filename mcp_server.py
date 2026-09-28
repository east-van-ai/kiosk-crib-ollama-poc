"""An MCP server on a pair of line streams: the protocol, and no tool of its own.

It speaks MCP 2026-07-28 with stdio framing: one JSON-RPC message per line,
stateless, with the protocol version on every request. It answers discovery,
lists the tools it is given, and calls them. What each tool does, and whether
a call is allowed, belongs to whoever registers it.

It can also launch the client and open its streams, the reverse of stdio's
usual direction. What command to launch belongs to the caller.

https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio
https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts

Tools arrive as a registry, name to (definition, handler). A handler takes
the call's arguments and returns text, or raises ToolRefusal.
"""

import json
import subprocess

PROTOCOL_VERSION = "2026-07-28"

INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
UNSUPPORTED_PROTOCOL_VERSION = -32022


class ProtocolError(Exception):
    """The client wrote something on its protocol stream that is not MCP."""


class ToolRefusal(Exception):
    """A tool declined or failed a call. The client sees it as an isError result."""


def ignore_line(text):
    """Discard a log line, when nobody listens."""


def result_response(message_id, result, server_info):
    """Return a JSON-RPC response carrying a complete result."""
    result = {"resultType": "complete", **result}
    result["_meta"] = {"io.modelcontextprotocol/serverInfo": server_info}
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error_response(message_id, code, text, data=None):
    """Return a JSON-RPC error response."""
    error = {"code": code, "message": text}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def tool_error(message_id, text, server_info):
    """Return a tool result that reports a refused or failed call."""
    return result_response(
        message_id, {"content": [{"type": "text", "text": text}], "isError": True}, server_info
    )


def call_tool(message_id, params, tools, server_info, log):
    """Answer a `tools/call` by handing the arguments to the named tool."""
    name = params.get("name")
    if not isinstance(name, str) or name not in tools:
        log(f"refused unknown tool {name!r}")
        return error_response(message_id, INVALID_PARAMS, f"Unknown tool: {name}")
    _, handler = tools[name]
    try:
        text = handler(params.get("arguments", {}))
    except ToolRefusal as refusal:
        return tool_error(message_id, str(refusal), server_info)
    return result_response(message_id, {"content": [{"type": "text", "text": text}]}, server_info)


def handle_message(message, tools, server_info, log=ignore_line):
    """Return the response to one message from the client, or None for a notification."""
    if not isinstance(message, dict) or "method" not in message:
        message_id = message.get("id") if isinstance(message, dict) else None
        return error_response(message_id, INVALID_REQUEST, "Not a JSON-RPC request")
    if "id" not in message:
        return None

    message_id = message["id"]
    method = message["method"]
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    requested = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None

    if not isinstance(requested, str):
        return error_response(message_id, INVALID_PARAMS, "Missing protocol version in _meta")
    if requested != PROTOCOL_VERSION:
        log(f"refused protocol version {requested}")
        return error_response(
            message_id,
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": [PROTOCOL_VERSION], "requested": requested},
        )

    if method == "server/discover":
        log("server/discover")
        return result_response(message_id, {
            "supportedVersions": [PROTOCOL_VERSION],
            "capabilities": {"tools": {}},
            "ttlMs": 0,
            "cacheScope": "private",
        }, server_info)
    if method == "tools/list":
        log(f"tools/list: {', '.join(tools)}")
        definitions = [definition for definition, _ in tools.values()]
        return result_response(
            message_id, {"tools": definitions, "ttlMs": 0, "cacheScope": "private"}, server_info
        )
    if method == "tools/call":
        return call_tool(message_id, params, tools, server_info, log)

    log(f"refused unknown method {method!r}")
    return error_response(message_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def launch_client(command):
    """Start the client as a subprocess and return it, its stdin and stdout piped as UTF-8 text.

    A byte that is not UTF-8 arrives as "\\xff", which no JSON parses, so
    `serve_client` refuses it as a ProtocolError instead of crashing.
    """
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        errors="backslashreplace",
    )


def serve_client(reader, writer, tools, server_info, trace=None, log=ignore_line):
    """Answer the client's messages, one per line, until it closes its stream.

    `trace(direction, line)`, when given, receives each line as read, before
    parsing, with direction "in", and each response as written, with "out".
    `log(text)` receives the protocol events: discovery, listing, refusals.
    """
    for line in reader:
        if not line.strip():
            continue
        if trace:
            trace("in", line.rstrip("\n"))
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            raise ProtocolError(f"not an MCP message: {line.strip()[:80]!r}")
        response = handle_message(message, tools, server_info, log)
        if response is not None:
            answer = json.dumps(response)
            writer.write(answer + "\n")
            writer.flush()
            if trace:
                trace("out", answer)
