"""An MCP client on its own standard streams: the protocol, and no tool of its own.

It speaks MCP 2026-07-28 with stdio framing, to whatever answers on the other
end. It does not know what that is, only which tools it offers. Which tool to
call, and with what, belongs to the caller.

https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio
"""

import json
import os
import sys

PROTOCOL_VERSION = "2026-07-28"

_reader = None
_writer = None
_client_info = {"name": "mcp_client", "version": "0.1.0"}
_next_id = 0


class McpError(Exception):
    """The server refused a request, failed it, or stopped answering."""


def claim_stdout():
    """Keep the real stdout for protocol traffic and send every other write to stderr.

    File descriptor 1 is duplicated for the protocol, then pointed at stderr,
    so a stray `print` or a library's write cannot corrupt the protocol.
    """
    sys.stdout.flush()
    protocol = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(protocol, "w", encoding="utf-8")


def open_channel(reader=None, writer=None, needed=(), client_info=None):
    """Open the channel, check the version and the needed tools, and return the tool names.

    With no reader or writer, the channel is the process's own stdin and
    stdout. `client_info` names the caller on every request.
    """
    global _reader, _writer, _client_info
    _reader = reader if reader is not None else sys.stdin
    _writer = writer if writer is not None else claim_stdout()
    if client_info is not None:
        _client_info = client_info

    discovered = send_request("server/discover")
    if PROTOCOL_VERSION not in discovered.get("supportedVersions", []):
        raise McpError(f"the server does not speak {PROTOCOL_VERSION}")
    tools = [tool["name"] for tool in send_request("tools/list").get("tools", [])]
    missing = [name for name in needed if name not in tools]
    if missing:
        raise McpError(f"the server does not offer {', '.join(missing)}")
    return tools


def send_request(method, params=None):
    """Send one request and return its result, skipping notifications on the way."""
    global _next_id
    _next_id += 1
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": _client_info,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    message = {
        "jsonrpc": "2.0",
        "id": _next_id,
        "method": method,
        "params": {"_meta": meta, **(params or {})},
    }
    _writer.write(json.dumps(message) + "\n")
    _writer.flush()

    for line in _reader:
        response = json.loads(line)
        if response.get("id") != _next_id:
            continue
        if "error" in response:
            error = response["error"]
            raise McpError(f"{method} refused ({error['code']}): {error['message']}")
        return response["result"]
    raise McpError(f"the server did not answer {method}")


def call_tool(name, arguments):
    """Call one tool and return its text, raising when the server reports an error."""
    result = send_request("tools/call", {"name": name, "arguments": arguments})
    text = "".join(block["text"] for block in result["content"] if block["type"] == "text")
    if result.get("isError"):
        raise McpError(f"{name} failed: {text}")
    return text
