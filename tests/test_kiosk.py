"""Tests for the Kiosk: its ask_ai policy, its Docker line, its greeting, and its trace labels."""

import contextlib
import io
import json
import os
import re
import unittest
from unittest import mock

import kiosk
import mcp_server


class Fake:
    """An inference function that records its calls."""

    def __init__(self):
        """Start with no calls."""
        self.calls = []

    def __call__(self, system_message, user_message, temperature):
        """Record the call and answer with a fixed text."""
        self.calls.append((system_message, user_message, temperature))
        return "al dente"


class PolicyTest(unittest.TestCase):
    """The ask_ai handler checks each call before any inference."""

    def setUp(self):
        """Silence the Kiosk's stderr lines and build a fresh registry."""
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.fake = Fake()
        self.definition, self.ask_ai = kiosk.build_tools(self.fake)["ask_ai"]

    def test_registry(self):
        """The registry offers ask_ai, with its schema, and nothing else."""
        self.assertEqual(list(kiosk.build_tools(self.fake)), ["ask_ai"])
        self.assertEqual(self.definition, kiosk.ASK_AI)

    def test_allowed_call(self):
        """A good call reaches inference and returns its text."""
        self.assertEqual(self.ask_ai({"system": "cook", "user": "boil", "temperature": 0.3}), "al dente")
        self.assertEqual(self.fake.calls, [("cook", "boil", 0.3)])

    def test_bad_arguments(self):
        """Arguments that break the schema are refused, uninferred."""
        for arguments in (
            {"system": "cook"},
            {"system": "cook", "user": 3},
            {"system": "cook", "user": "boil", "temperature": 9},
            {"system": "cook", "user": "boil", "temperature": True},
            {"system": "cook", "user": "boil", "model": "llama3.2"},
            "not an object",
        ):
            with self.subTest(arguments=arguments), self.assertRaises(mcp_server.ToolRefusal):
                self.ask_ai(arguments)
        self.assertEqual(self.fake.calls, [])

    def test_budget(self):
        """The call past the budget is refused, uninferred."""
        good = {"system": "cook", "user": "boil"}
        for _ in range(kiosk.CALL_BUDGET):
            self.ask_ai(good)
        with self.assertRaisesRegex(mcp_server.ToolRefusal, "budget"):
            self.ask_ai(good)
        self.assertEqual(len(self.fake.calls), kiosk.CALL_BUDGET)

    def test_budget_is_per_registry(self):
        """Each run's registry starts its own count."""
        good = {"system": "cook", "user": "boil"}
        for _ in range(kiosk.CALL_BUDGET):
            self.ask_ai(good)
        _, fresh = kiosk.build_tools(self.fake)["ask_ai"]
        self.assertEqual(fresh(good), "al dente")

    def test_inference_failure(self):
        """Ollama failing is refused, carrying the reason."""
        def broken(system_message, user_message, temperature):
            """Fail like an unreachable Ollama."""
            raise ConnectionRefusedError("connection refused")

        _, ask_ai = kiosk.build_tools(broken)["ask_ai"]
        with self.assertRaisesRegex(mcp_server.ToolRefusal, "connection refused"):
            ask_ai({"system": "cook", "user": "boil"})

    def test_refusal_on_the_wire(self):
        """Through the server, a refused call is an isError result, as before the split."""
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"},
                "name": "ask_ai", "arguments": {"system": "cook"},
            },
        }
        response = mcp_server.handle_message(request, kiosk.build_tools(self.fake), kiosk.SERVER_INFO)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["content"], [{"type": "text", "text": "user must be a string"}])


class DockerCommandTest(unittest.TestCase):
    """The line that starts the Crib."""

    def test_confinement(self):
        """The Crib is removed, interactive, off the network, and not a TTY."""
        command = kiosk.docker_command()
        self.assertEqual(command[:2], ["docker", "run"])
        self.assertIn("--rm", command)
        self.assertIn("-i", command)
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertNotIn("-t", command)
        self.assertNotIn("-it", command)
        self.assertEqual(command[-1], "kiosk-crib")

    def test_named_variables_only(self):
        """Only the three CARBONARA_* names pass, with no values on the line."""
        command = kiosk.docker_command()
        passed = [command[i + 1] for i, token in enumerate(command) if token == "-e"]
        self.assertEqual(passed, ["CARBONARA_ORDER", "CARBONARA_TEMPERATURE", "CARBONARA_STREAM_DELAY"])

    def test_user_nobody(self):
        """The Kiosk itself drops root, rather than trusting the image."""
        command = kiosk.docker_command()
        self.assertEqual(command[command.index("--user") + 1], "nobody")


class GreetingTest(unittest.TestCase):
    """The Kiosk's greeting claims only what is in force."""

    def test_all_facts(self):
        """The real Docker line earns every confinement phrase, in order."""
        self.assertEqual(
            kiosk.crib_facts(kiosk.docker_command()),
            ["removed on exit", "no network", "read-only", "no capabilities",
             "no new privileges", "runs as nobody"],
        )

    def test_missing_flag_drops_its_phrase(self):
        """Take a flag off the line, and its phrase goes with it."""
        for flag, phrase in kiosk.CONFINEMENT:
            with self.subTest(phrase=phrase):
                command = kiosk.docker_command()
                start = command.index(flag[0])
                del command[start:start + len(flag)]
                self.assertNotIn(phrase, kiosk.crib_facts(command))

    def test_flag_without_its_value(self):
        """A flag with another value is not the flag: --network host is no claim."""
        command = ["docker", "run", "--network", "host", "kiosk-crib"]
        self.assertEqual(kiosk.crib_facts(command), [])

    def greeting(self, environment):
        """Return the Kiosk's greeting under the given environment."""
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), contextlib.redirect_stderr(stderr):
            kiosk.print_greeting()
        return stderr.getvalue()

    def test_greeting_names_settings(self):
        """The greeting opens with Hello and names the model, the address, and the budget."""
        text = self.greeting({})
        self.assertIn("Hello! I am Kiosk.", text.splitlines()[0])
        self.assertIn("The Agent app", text.splitlines()[0])
        self.assertIn(kiosk.inference.describe_backend(), text.splitlines()[3])
        self.assertIn(f"budget: {kiosk.CALL_BUDGET} ask_ai calls", text)
        self.assertIn("no network", text)

    def test_greeting_names_only_set_variables(self):
        """Only the CARBONARA_* variables actually set are named as passed."""
        self.assertIn("Passing now: nothing", self.greeting({}))
        text = self.greeting({"CARBONARA_ORDER": "x", "HOME": "/Users/someone"})
        self.assertIn("Passing now: CARBONARA_ORDER\n", text)
        self.assertNotIn("HOME", text)

    def test_greeting_names_the_allowlist(self):
        """The second line names every variable the Kiosk would pass."""
        second = self.greeting({}).splitlines()[1]
        self.assertIn(f"I can pass {', '.join(kiosk.PASSED)} into the Crib.", second)


class TraceTest(unittest.TestCase):
    """The Kiosk shows the server's raw lines as formatted JSON."""

    ANSI = re.compile(r"\033\[[0-9;]*m")

    def trace(self, direction, line):
        """Return what trace_line prints, without colour codes."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            kiosk.trace_line(direction, line)
        return self.ANSI.sub("", stderr.getvalue())

    def test_formatted(self):
        """A valid message prints under its label, indented, and parses back the same."""
        message = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": {}}}
        lines = self.trace("in", json.dumps(message)).splitlines()
        self.assertEqual(lines[0], "[MCP Agent app → Kiosk]")
        self.assertEqual(lines[1:3], ["{", '  "jsonrpc": "2.0",'])
        self.assertEqual(json.loads("\n".join(lines[1:])), message)

    def test_labels(self):
        """The server's out becomes the Kiosk-to-Agent-app label."""
        self.assertEqual(self.trace("out", "[]").splitlines()[0], "[MCP Kiosk → Agent app]")

    def test_not_json_as_it_came(self):
        """A line that is not JSON prints exactly as it came, under its label."""
        self.assertEqual(self.trace("in", "[DISPATCHER] hello  {"), "[MCP Agent app → Kiosk]\n[DISPATCHER] hello  {\n")

    def test_non_ascii_as_written(self):
        """A model's accents read as written, not as escapes."""
        text = self.trace("out", json.dumps({"text": "crème, già"}))
        self.assertIn('"text": "crème, già"', text)
        self.assertNotIn("\\u", text)

    def test_greeting_reports_trace(self):
        """The greeting says the trace is formatted when on, and how to turn it on when off."""
        for trace, expected in ((True, "trace: on, every MCP message shown as formatted JSON"), (False, "MCP_TRACE=1")):
            with self.subTest(trace=trace):
                stderr = io.StringIO()
                with mock.patch.object(kiosk, "MCP_TRACE", trace), contextlib.redirect_stderr(stderr):
                    kiosk.print_greeting()
                self.assertIn(expected, stderr.getvalue().splitlines()[-1])


class ExitTest(unittest.TestCase):
    """The last line says in words how the Agent app ended."""

    def test_clean(self):
        """Exit 0 is a clean finish."""
        self.assertEqual(kiosk.describe_exit(kiosk.EXIT_OK), "the Agent app finished cleanly (exit 0)")

    def test_failed(self):
        """Anything else is a failure, with its status kept."""
        for exit_status in (1, 2, -15):
            with self.subTest(exit_status=exit_status):
                self.assertEqual(kiosk.describe_exit(exit_status), f"the Agent app failed (exit {exit_status})")


class FakeCrib:
    """A stand-in for the Crib's process that writes one byte that is not UTF-8."""

    def __init__(self, command, **options):
        """Decode the output with the encoding and error handler the Kiosk asks for."""
        errors = options.get("errors", "strict")
        self.stdout = io.TextIOWrapper(io.BytesIO(b"\xff{}\n"), encoding=options["encoding"], errors=errors)
        self.stdin = io.StringIO()

    def terminate(self):
        """Stop, which a fake does by doing nothing."""

    def wait(self):
        """Report the status a stopped Crib would."""
        return -15


class MainTest(unittest.TestCase):
    """The Kiosk's run, with a fake in place of the Crib."""

    def test_invalid_utf8(self):
        """A byte that is not UTF-8 stops the Crib as a protocol violation, not a crash."""
        stderr = io.StringIO()
        with mock.patch.object(mcp_server.subprocess, "Popen", FakeCrib), contextlib.redirect_stderr(stderr):
            self.assertEqual(kiosk.main(), kiosk.EXIT_PROTOCOL_VIOLATION)
        self.assertIn("protocol violation, stopping the Crib", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
