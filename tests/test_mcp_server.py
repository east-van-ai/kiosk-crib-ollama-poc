"""Tests for the MCP server: framing, dispatch, errors, and the raw trace, with no Kiosk."""

import io
import json
import unittest

import mcp_server

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}
SERVER_INFO = {"name": "test-server", "version": "0.0.0"}
ECHO = {"name": "echo", "description": "Say it back.", "inputSchema": {"type": "object"}}


def build_request(method, message_id=1, **params):
    """Return a request as a client would send it."""
    return {"jsonrpc": "2.0", "id": message_id, "method": method, "params": {"_meta": META, **params}}


def echo_tool(arguments):
    """Answer with the words, or refuse when there are none."""
    if "words" not in arguments:
        raise mcp_server.ToolRefusal("nothing to echo")
    return arguments["words"]


TOOLS = {"echo": (ECHO, echo_tool)}


class HandleTest(unittest.TestCase):
    """The server's answer to each kind of message."""

    def setUp(self):
        """Collect the log events in a list."""
        self.events = []

    def handle_message(self, request):
        """Run one message through the server."""
        return mcp_server.handle_message(request, TOOLS, SERVER_INFO, self.events.append)

    def test_discover(self):
        """server/discover names the version and the tools capability."""
        result = self.handle_message(build_request("server/discover"))["result"]
        self.assertEqual(result["supportedVersions"], ["2026-07-28"])
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["resultType"], "complete")
        self.assertIn("ttlMs", result)
        self.assertIn("cacheScope", result)
        self.assertEqual(result["_meta"], {"io.modelcontextprotocol/serverInfo": SERVER_INFO})
        self.assertEqual(self.events, ["server/discover"])

    def test_unsupported_version(self):
        """An unknown version gets -32022 with the supported list."""
        request = build_request("server/discover")
        request["params"]["_meta"] = {**META, "io.modelcontextprotocol/protocolVersion": "2025-11-25"}
        error = self.handle_message(request)["error"]
        self.assertEqual(error["code"], -32022)
        self.assertEqual(error["data"], {"supported": ["2026-07-28"], "requested": "2025-11-25"})

    def test_missing_version(self):
        """A request with no _meta is invalid params."""
        response = self.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response["error"]["code"], -32602)

    def test_tools_list(self):
        """tools/list offers the registered definitions, in order, and logs their names."""
        tools = self.handle_message(build_request("tools/list"))["result"]["tools"]
        self.assertEqual(tools, [ECHO])
        self.assertEqual(self.events, ["tools/list: echo"])

    def test_unknown_tool(self):
        """A tool not in the registry is invalid params."""
        response = self.handle_message(build_request("tools/call", name="sf_query", arguments={}))
        self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(self.events, ["refused unknown tool 'sf_query'"])

    def test_tool_name_not_a_string(self):
        """A tool name that is a list or an object is refused, not a crash."""
        for name in (["echo"], {"name": "echo"}):
            with self.subTest(name=name):
                self.events.clear()
                response = self.handle_message(build_request("tools/call", name=name, arguments={}))
                self.assertEqual(response["error"]["code"], -32602)
                self.assertEqual(self.events, [f"refused unknown tool {name!r}"])

    def test_unknown_method(self):
        """An unknown method is method-not-found."""
        self.assertEqual(self.handle_message(build_request("ping"))["error"]["code"], -32601)

    def test_notification(self):
        """A notification gets no answer."""
        self.assertIsNone(self.handle_message({"jsonrpc": "2.0", "method": "notifications/cancelled"}))

    def test_call(self):
        """A call reaches the handler, and its text comes back as content."""
        result = self.handle_message(build_request("tools/call", name="echo", arguments={"words": "ciao"}))["result"]
        self.assertEqual(result["content"], [{"type": "text", "text": "ciao"}])
        self.assertNotIn("isError", result)

    def test_refusal(self):
        """A ToolRefusal comes back as an isError result carrying its reason."""
        result = self.handle_message(build_request("tools/call", name="echo", arguments={}))["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"], [{"type": "text", "text": "nothing to echo"}])


class ServeTest(unittest.TestCase):
    """The line loop, and the raw trace."""

    def serve(self, lines):
        """Serve the given lines with the trace on; return what was written and what was traced."""
        writer = io.StringIO()
        traced = []
        mcp_server.serve_client(
            io.StringIO("".join(lines)), writer, TOOLS, SERVER_INFO,
            trace=lambda direction, line: traced.append((direction, line)),
        )
        return writer.getvalue(), traced

    def test_not_json(self):
        """A line that is not JSON is a protocol violation."""
        with self.assertRaises(mcp_server.ProtocolError):
            mcp_server.serve_client(io.StringIO("[DISPATCHER] hello\n"), io.StringIO(), TOOLS, SERVER_INFO)

    def test_both_directions_raw(self):
        """The request is traced as read, the response as written."""
        # Odd spacing on purpose: re-serializing would tidy it away.
        request = '{"jsonrpc":"2.0",  "id":7, "method":"tools/list","params":{"_meta":%s}}' % json.dumps(META)
        written, traced = self.serve([request + "\n"])
        self.assertEqual(traced, [("in", request), ("out", written.rstrip())])

    def test_no_trace(self):
        """With no trace function, the loop still answers."""
        writer = io.StringIO()
        mcp_server.serve_client(io.StringIO(json.dumps(build_request("tools/list")) + "\n"), writer, TOOLS, SERVER_INFO)
        self.assertEqual(json.loads(writer.getvalue())["result"]["tools"], [ECHO])

    def test_notification(self):
        """A notification is traced in, and nothing is traced out."""
        note = '{"jsonrpc": "2.0", "method": "notifications/cancelled"}'
        written, traced = self.serve([note + "\n"])
        self.assertEqual(written, "")
        self.assertEqual(traced, [("in", note)])

    def test_malformed_line_traced_first(self):
        """A line that is not JSON reaches the trace before the server refuses it."""
        traced = []
        with self.assertRaises(mcp_server.ProtocolError):
            mcp_server.serve_client(
                io.StringIO("[DISPATCHER] hello\n"), io.StringIO(), TOOLS, SERVER_INFO,
                trace=lambda direction, line: traced.append((direction, line)),
            )
        self.assertEqual(traced, [("in", "[DISPATCHER] hello")])

    def test_invalid_utf8(self):
        """A byte that is not UTF-8, decoded as the Kiosk decodes it, is traced and refused."""
        traced = []
        reader = io.TextIOWrapper(io.BytesIO(b"\xff{}\n"), encoding="utf-8", errors="backslashreplace")
        with self.assertRaises(mcp_server.ProtocolError):
            mcp_server.serve_client(
                reader, io.StringIO(), TOOLS, SERVER_INFO,
                trace=lambda direction, line: traced.append((direction, line)),
            )
        self.assertEqual(traced, [("in", "\\xff{}")])


if __name__ == "__main__":
    unittest.main()
