"""Tests for the MCP client, against the MCP server over real pipes, with no Kiosk."""

import io
import json
import os
import pathlib
import subprocess
import sys
import threading
import unittest

import mcp_client
import mcp_server

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER_INFO = {"name": "test-server", "version": "0.0.0"}
ECHO = {"name": "echo", "description": "Say it back.", "inputSchema": {"type": "object"}}


def echo_tool(arguments):
    """Answer with the words, or refuse when there are none."""
    if "words" not in arguments:
        raise mcp_server.ToolRefusal("nothing to echo")
    return arguments["words"]


class RoundTripTest(unittest.TestCase):
    """The client and the server over real pipes."""

    def setUp(self):
        """Run the server's loop in a thread on a pair of pipes, and capture what it receives."""
        to_server_r, to_server_w = os.pipe()
        to_client_r, to_client_w = os.pipe()
        server_reader = os.fdopen(to_server_r, encoding="utf-8")
        server_writer = os.fdopen(to_client_w, "w", encoding="utf-8")
        self.client_reader = os.fdopen(to_client_r, encoding="utf-8")
        self.client_writer = os.fdopen(to_server_w, "w", encoding="utf-8")
        self.received = []

        def run():
            """Serve until the client closes its end."""
            mcp_server.serve_client(
                server_reader, server_writer, {"echo": (ECHO, echo_tool)}, SERVER_INFO,
                trace=lambda direction, line: self.received.append(line) if direction == "in" else None,
            )
            server_reader.close()
            server_writer.close()

        self.thread = threading.Thread(target=run)
        self.thread.start()
        self.addCleanup(self.client_reader.close)
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.client_writer.close)

    def test_call_tool(self):
        """open_channel() lists the tools, and call_tool returns the server's text."""
        self.assertEqual(mcp_client.open_channel(self.client_reader, self.client_writer), ["echo"])
        self.assertEqual(mcp_client.call_tool("echo", {"words": "ciao"}), "ciao")

    def test_refusal(self):
        """A refused call raises, carrying the server's reason."""
        mcp_client.open_channel(self.client_reader, self.client_writer)
        with self.assertRaisesRegex(mcp_client.McpError, "nothing to echo"):
            mcp_client.call_tool("echo", {})

    def test_needed_tool_missing(self):
        """open_channel() raises when a needed tool is not offered."""
        with self.assertRaisesRegex(mcp_client.McpError, "does not offer ask_ai"):
            mcp_client.open_channel(self.client_reader, self.client_writer, needed=("echo", "ask_ai"))

    def test_client_info(self):
        """The caller's name rides on every request."""
        mcp_client.open_channel(
            self.client_reader, self.client_writer, client_info={"name": "carbonara", "version": "0.1.0"}
        )
        mcp_client.call_tool("echo", {"words": "ciao"})
        self.assertEqual(len(self.received), 3)
        for line in self.received:
            meta = json.loads(line)["params"]["_meta"]
            self.assertEqual(meta["io.modelcontextprotocol/clientInfo"], {"name": "carbonara", "version": "0.1.0"})


class SilentServerTest(unittest.TestCase):
    """No server on the other end."""

    def test_no_answer(self):
        """open_channel() raises when the stream closes unanswered."""
        with self.assertRaisesRegex(mcp_client.McpError, "the server did not answer"):
            mcp_client.open_channel(io.StringIO(""), io.StringIO())


class ClaimStdoutTest(unittest.TestCase):
    """The Agent's stdout carries protocol traffic only."""

    def test_prints_go_to_stderr(self):
        """After the claim, print lands on stderr and the protocol line on stdout."""
        script = (
            "import mcp_client\n"
            "protocol = mcp_client.claim_stdout()\n"
            "print('[DISPATCHER] kitchen talk')\n"
            "protocol.write('{}\\n')\n"
            "protocol.flush()\n"
        )
        run = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, check=True
        )
        self.assertEqual(run.stdout, "{}\n")
        self.assertIn("kitchen talk", run.stderr)


if __name__ == "__main__":
    unittest.main()
