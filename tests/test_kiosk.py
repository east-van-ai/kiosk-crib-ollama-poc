"""Tests for the Kiosk: its ask_ai policy, its Cribs, its greeting, and its trace labels."""

import contextlib
import io
import json
import os
import re
import threading
import unittest
from unittest import mock

import kiosk
import mcp_server

RUN_NAME = "kiosk-crib-run-1"


def hush_runtimes(test):
    """Keep a test off the real runtimes: no version, no machine, and a stop command that only records.

    Returns the mock standing in for `subprocess.run` in the Kiosk, which is
    where a stop command goes.
    """
    for crib in kiosk.CRIBS.values():
        for query, answer in (("runtime_version", None), ("inspect_setup", []), ("inspect_limits", [])):
            patch = mock.patch.object(crib, query, return_value=answer)
            patch.start()
            test.addCleanup(patch.stop)
    patch = mock.patch.object(kiosk.subprocess, "run")
    test.addCleanup(patch.stop)
    return patch.start()


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
        """The call past the budget ends the run, uninferred. It is not a refusal the Agent could ride out."""
        good = {"system": "cook", "user": "boil"}
        for _ in range(kiosk.CALL_BUDGET):
            self.ask_ai(good)
        with self.assertRaisesRegex(kiosk.BudgetSpent, "budget"):
            self.ask_ai(good)
        self.assertNotIsInstance(kiosk.BudgetSpent(), mcp_server.ToolRefusal)
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


class CribSurfaceTest(unittest.TestCase):
    """Every flavour offers the Kiosk the same surface."""

    def test_names(self):
        """The three flavours, Docker first, as the default."""
        self.assertEqual(list(kiosk.CRIBS), ["docker", "apple-container", "container-machine"])

    def test_surface(self):
        """Each module has an image, a runtime, a launch line, a stop line, two flag tables, and its by-design traits."""
        for name, crib in kiosk.CRIBS.items():
            with self.subTest(crib=name):
                self.assertIsInstance(crib.IMAGE, str)
                self.assertIsInstance(crib.RUNTIME, str)
                self.assertIsInstance(crib.CONFINEMENT, tuple)
                self.assertIsInstance(crib.LIMITS, tuple)
                self.assertIsInstance(crib.BY_DESIGN, tuple)
                for function in ("build_command", "stop_command", "runtime_version", "inspect_setup", "inspect_limits"):
                    self.assertTrue(callable(getattr(crib, function)), function)


class CribCommandTest(unittest.TestCase):
    """The line that starts the Crib, per flavour."""

    def test_interactive_without_tty(self):
        """Every Crib is interactive and never a TTY, which would merge stdout and stderr."""
        for name, crib in kiosk.CRIBS.items():
            with self.subTest(crib=name):
                command = crib.build_command(kiosk.PASSED, RUN_NAME)
                self.assertIn("-i", command)
                self.assertNotIn("-t", command)
                self.assertNotIn("-it", command)

    def test_named_variables_only(self):
        """Only the three CARBONARA_* names pass, with no values on the line."""
        for name, crib in kiosk.CRIBS.items():
            with self.subTest(crib=name):
                command = crib.build_command(kiosk.PASSED, RUN_NAME)
                passed = [command[i + 1] for i, token in enumerate(command) if token == "-e"]
                self.assertEqual(passed, ["CARBONARA_ORDER", "CARBONARA_TEMPERATURE", "CARBONARA_STREAM_DELAY"])

    def test_containers(self):
        """Docker and Apple container: removed, off the network, read-only, as nobody, image last."""
        cribs = ((kiosk.crib_docker, "docker", "kiosk-crib-docker"),
                 (kiosk.crib_apple_container, "container", "kiosk-crib-apple-container"))
        for crib, runtime, image in cribs:
            with self.subTest(crib=runtime):
                command = crib.build_command(kiosk.PASSED, RUN_NAME)
                self.assertEqual(command[:2], [runtime, "run"])
                self.assertIn("--rm", command)
                self.assertIn("--read-only", command)
                self.assertEqual(command[command.index("--network") + 1], "none")
                self.assertEqual(command[command.index("--user") + 1], "nobody")
                self.assertEqual(command[command.index("--name") + 1], RUN_NAME)
                self.assertEqual(command[-1], image)

    def test_same_limits(self):
        """Docker and Apple container take the same three limit flags, so only the flavour differs."""
        limits = [crib.LIMITS for crib in (kiosk.crib_docker, kiosk.crib_apple_container)]
        self.assertEqual(limits[0], limits[1])
        self.assertEqual([flag[0] for flag, _ in limits[0]], ["--memory", "--cpus", "--ulimit"])
        for crib in (kiosk.crib_docker, kiosk.crib_apple_container):
            self.assertNotIn("--pids-limit", crib.build_command(kiosk.PASSED, RUN_NAME))

    def test_stop_by_name(self):
        """A container is stopped by the name it was started with, the machine as a whole."""
        self.assertEqual(kiosk.crib_docker.stop_command(RUN_NAME), ["docker", "kill", RUN_NAME])
        self.assertEqual(kiosk.crib_apple_container.stop_command(RUN_NAME), ["container", "kill", RUN_NAME])
        self.assertEqual(
            kiosk.crib_container_machine.stop_command(RUN_NAME), ["container", "machine", "stop", "kiosk-crib"],
        )

    def test_machine(self):
        """The container machine runs the Agent in the machine the setup made, unbuffered, in /crib.

        The machine ignores the image's ENV and WORKDIR, so the line restores both.
        """
        command = kiosk.crib_container_machine.build_command(kiosk.PASSED, RUN_NAME)
        self.assertEqual(command[:3], ["container", "machine", "run"])
        self.assertEqual(command[command.index("-n") + 1], "kiosk-crib")
        self.assertEqual(command[-3:], ["python", "-u", "carbonara.py"])
        self.assertEqual(command[command.index("-w") + 1], "/crib")


class CribChoiceTest(unittest.TestCase):
    """The Kiosk picks one flavour per run, from CRIB."""

    def test_default(self):
        """CRIB unset picks Docker."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIs(kiosk.pick_crib(), kiosk.crib_docker)

    def test_each_name(self):
        """Each name picks its own module."""
        for name, crib in kiosk.CRIBS.items():
            with self.subTest(crib=name), mock.patch.dict(os.environ, {"CRIB": name}, clear=True):
                self.assertIs(kiosk.pick_crib(), crib)

    def test_unknown(self):
        """An unknown name exits 2, names the valid ones, and starts no Crib."""
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"CRIB": "podman"}, clear=True), contextlib.redirect_stderr(stderr):
            with mock.patch.object(mcp_server.subprocess, "Popen") as popen:
                self.assertEqual(kiosk.main(), kiosk.EXIT_USAGE)
        popen.assert_not_called()
        self.assertIn("docker, apple-container, container-machine", stderr.getvalue())


class MachineSetupTest(unittest.TestCase):
    """The container machine claims no home folder only when it reports none."""

    def inspect(self, answer):
        """Return inspect_setup's phrases with `container machine inspect` faked."""
        done = mock.Mock(stdout=json.dumps(answer))
        with mock.patch.object(kiosk.crib_container_machine.subprocess, "run", return_value=done):
            return kiosk.crib_container_machine.inspect_setup()

    def test_limits(self):
        """The machine's memory and CPUs are read back from it, and claimed only when it reports them."""
        done = mock.Mock(stdout=json.dumps([{"memory": 2**30, "cpus": 4}]))
        with mock.patch.object(kiosk.crib_container_machine.subprocess, "run", return_value=done):
            self.assertEqual(kiosk.crib_container_machine.inspect_limits(), ["1024 MB of memory", "4 CPUs"])
        done = mock.Mock(stdout=json.dumps([{"cpus": 1}]))
        with mock.patch.object(kiosk.crib_container_machine.subprocess, "run", return_value=done):
            self.assertEqual(kiosk.crib_container_machine.inspect_limits(), ["1 CPU"])
        with mock.patch.object(kiosk.crib_container_machine.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(kiosk.crib_container_machine.inspect_limits(), [])

    def test_none(self):
        """homeMount none earns the phrase."""
        self.assertEqual(self.inspect([{"homeMount": "none"}]), ["no home folder"])

    def test_mounted(self):
        """A mounted home, or no such field, earns nothing."""
        for answer in ([{"homeMount": "rw"}], [{"homeMount": "ro"}], [{}], []):
            with self.subTest(answer=answer):
                self.assertEqual(self.inspect(answer), [])

    def test_unreachable(self):
        """No runtime, or no machine, earns nothing."""
        for error in (FileNotFoundError("container"), mcp_server.subprocess.CalledProcessError(1, "container")):
            with self.subTest(error=error), mock.patch.object(
                kiosk.crib_container_machine.subprocess, "run", side_effect=error
            ):
                self.assertEqual(kiosk.crib_container_machine.inspect_setup(), [])


class GreetingTest(unittest.TestCase):
    """The Kiosk's greeting claims only what is in force."""

    EXPECTED = {
        "docker": ["removed on exit", "no network", "read-only", "no capabilities",
                   "no new privileges", "runs as nobody"],
        "apple-container": ["removed on exit", "no network", "read-only", "no capabilities",
                            "runs as nobody"],
        "container-machine": [],
    }
    LIMITS = ["256 MB of memory", "1 CPU", "at most 32 processes"]

    def setUp(self):
        """Keep the greeting off the real runtimes."""
        hush_runtimes(self)

    def test_all_limits(self):
        """Each container flavour's real line earns the three limit phrases. The machine's line earns none."""
        for name, crib in kiosk.CRIBS.items():
            with self.subTest(crib=name):
                command = crib.build_command(kiosk.PASSED, RUN_NAME)
                expected = [] if name == "container-machine" else self.LIMITS
                self.assertEqual(kiosk.crib_facts(command, crib.LIMITS), expected)

    def test_missing_limit_drops_its_phrase(self):
        """Take a limit off the line, and its phrase goes with it."""
        for name, crib in kiosk.CRIBS.items():
            for flag, phrase in crib.LIMITS:
                with self.subTest(crib=name, phrase=phrase):
                    command = crib.build_command(kiosk.PASSED, RUN_NAME)
                    start = command.index(flag[0])
                    del command[start:start + len(flag)]
                    self.assertNotIn(phrase, kiosk.crib_facts(command, crib.LIMITS))

    def test_limits_line(self):
        """The limits line says what the flags set, what the machine reports, or none."""
        _, after = self.crib_lines("docker")
        self.assertEqual(after, "[KIOSK] limits: 256 MB of memory, 1 CPU, at most 32 processes")
        crib = kiosk.crib_container_machine
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            kiosk.print_greeting(crib, crib.build_command(kiosk.PASSED, RUN_NAME))
        self.assertIn("[KIOSK] limits: none\n", TraceTest.ANSI.sub("", stderr.getvalue()))

    def test_runtime_version(self):
        """The Crib line carries the runtime's version when the runtime answers, and no gap when it does not."""
        with mock.patch.object(kiosk.crib_docker, "runtime_version", return_value="29.8.0"):
            crib_line, _ = self.crib_lines("docker")
        self.assertIn("kiosk-crib-docker on Docker 29.8.0, removed on exit", crib_line)
        crib_line, _ = self.crib_lines("docker")
        self.assertIn("kiosk-crib-docker on Docker, removed on exit", crib_line)

    def test_version_parsed(self):
        """Each flavour reads the version out of its runtime's own answer, and None from silence."""
        answers = ((kiosk.crib_docker, "29.8.0\n", "29.8.0"),
                   (kiosk.crib_apple_container, "container CLI version 1.5.0 (build: release, commit: d265d66)\n", "1.5.0"),
                   (kiosk.crib_apple_container, "no number here\n", None))
        mock.patch.stopall()
        for crib, answer, version in answers:
            with self.subTest(crib=crib.RUNTIME, answer=answer):
                with mock.patch.object(crib.subprocess, "run", return_value=mock.Mock(stdout=answer)):
                    self.assertEqual(crib.runtime_version(), version)
                with mock.patch.object(crib.subprocess, "run", side_effect=FileNotFoundError):
                    self.assertIsNone(crib.runtime_version())

    def test_stops_line(self):
        """The stops line carries the budget and the time limit in force."""
        text = self.greeting({})
        self.assertIn(
            f"stops: after {kiosk.CALL_BUDGET} ask_ai calls, after {kiosk.RUN_LIMIT} seconds, "
            "on any message that is not MCP", text,
        )

    def test_all_facts(self):
        """Each flavour's real line earns exactly its expected phrases, in order."""
        for name, crib in kiosk.CRIBS.items():
            with self.subTest(crib=name):
                command = crib.build_command(kiosk.PASSED, RUN_NAME)
                self.assertEqual(kiosk.crib_facts(command, crib.CONFINEMENT), self.EXPECTED[name])

    def test_missing_flag_drops_its_phrase(self):
        """Take a flag off the line, and its phrase goes with it."""
        for name, crib in kiosk.CRIBS.items():
            for flag, phrase in crib.CONFINEMENT:
                with self.subTest(crib=name, phrase=phrase):
                    command = crib.build_command(kiosk.PASSED, RUN_NAME)
                    start = command.index(flag[0])
                    del command[start:start + len(flag)]
                    self.assertNotIn(phrase, kiosk.crib_facts(command, crib.CONFINEMENT))

    def test_flag_without_its_value(self):
        """A flag with another value is not the flag: --network host is no claim."""
        command = ["docker", "run", "--network", "host", "kiosk-crib-docker"]
        self.assertEqual(kiosk.crib_facts(command, kiosk.crib_docker.CONFINEMENT), [])

    def crib_lines(self, name, home_folder=()):
        """Return the greeting's Crib line and the line after it, for one flavour."""
        crib = kiosk.CRIBS[name]
        stderr = io.StringIO()
        with mock.patch.object(crib, "inspect_setup", return_value=list(home_folder)), contextlib.redirect_stderr(stderr):
            kiosk.print_greeting(crib, crib.build_command(kiosk.PASSED, RUN_NAME))
        lines = TraceTest.ANSI.sub("", stderr.getvalue()).splitlines()
        return lines[2], lines[3]

    def test_crib_line(self):
        """The Crib line names the image and the runtime, then the facts."""
        crib_line, _ = self.crib_lines("docker")
        self.assertEqual(
            crib_line,
            "[KIOSK] Crib: kiosk-crib-docker on Docker, removed on exit, no network, read-only, "
            "no capabilities, no new privileges, runs as nobody",
        )
        crib_line, _ = self.crib_lines("container-machine", ["no home folder"])
        self.assertEqual(crib_line, "[KIOSK] Crib: kiosk-crib-container-machine on container machine, no home folder")

    def test_by_design_only_for_machine(self):
        """Only the container machine has a by-design line, right under the Crib line."""
        for name in kiosk.CRIBS:
            with self.subTest(crib=name):
                _, after = self.crib_lines(name)
                if name == "container-machine":
                    self.assertEqual(
                        after,
                        "[KIOSK] by design: persistent, network on, runs as your Mac user, your SSH agent reachable",
                    )
                else:
                    self.assertNotIn("by design", after)

    def test_greeting_prints_command(self):
        """The greeting prints the command it is given, shell-quoted, with names and never values."""
        command = ["docker", "run", "-e", "CARBONARA_ORDER", "kiosk crib"]
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"CARBONARA_ORDER": "a secret order"}), contextlib.redirect_stderr(stderr):
            kiosk.print_greeting(kiosk.crib_docker, command)
        greeting = TraceTest.ANSI.sub("", stderr.getvalue())
        self.assertIn("[KIOSK] command: docker run -e CARBONARA_ORDER 'kiosk crib'\n", greeting)
        self.assertNotIn("a secret order", greeting)

    def test_launches_the_printed_command(self):
        """main() launches the very list the greeting printed."""
        printed = []
        with mock.patch.object(kiosk, "print_greeting", side_effect=lambda crib, command: printed.append(command)), \
                mock.patch.object(kiosk.mcp_server, "launch_client", side_effect=RuntimeError("launched")) as launch, \
                mock.patch.dict(os.environ, {"CRIB": "docker"}):
            with self.assertRaises(RuntimeError):
                kiosk.main()
        self.assertIs(launch.call_args.args[0], printed[0])

    def greeting(self, environment):
        """Return the Kiosk's greeting under the given environment."""
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), contextlib.redirect_stderr(stderr):
            kiosk.print_greeting(kiosk.crib_docker, kiosk.crib_docker.build_command(kiosk.PASSED, RUN_NAME))
        return stderr.getvalue()

    def test_greeting_names_settings(self):
        """The greeting opens with Hello and names the model, the address, and the budget."""
        text = self.greeting({})
        self.assertIn("Hello! I am Kiosk.", text.splitlines()[0])
        self.assertIn("The Agent app", text.splitlines()[0])
        self.assertIn(kiosk.inference.describe_backend(), text.splitlines()[5])
        self.assertIn(f"after {kiosk.CALL_BUDGET} ask_ai calls", text)
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
                    kiosk.print_greeting(kiosk.crib_docker, kiosk.crib_docker.build_command(kiosk.PASSED, RUN_NAME))
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


def build_call(message_id):
    """Return one good ask_ai request, as a line on the wire."""
    return json.dumps({
        "jsonrpc": "2.0", "id": message_id, "method": "tools/call",
        "params": {
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"},
            "name": "ask_ai", "arguments": {"system": "cook", "user": "boil"},
        },
    }) + "\n"


class FakeCrib:
    """A stand-in for the Crib's process, which says what it is given and records how it is ended."""

    #: What the Agent in the fake writes. One byte that is not UTF-8, unless a test says otherwise.
    output = b"\xff{}\n"

    def __init__(self, command=None, **options):
        """Decode the output with the encoding and error handler the Kiosk asks for."""
        errors = options.get("errors", "strict")
        self.stdout = io.TextIOWrapper(io.BytesIO(self.output), encoding=options.get("encoding", "utf-8"), errors=errors)
        self.stdin = io.StringIO()
        self.waits = []
        self.killed = False

    def kill(self):
        """Record the hard kill."""
        self.killed = True

    def wait(self, timeout=None):
        """Report the status a stopped Crib would, recording whether stdin was closed by then."""
        self.waits.append(self.stdin.closed)
        return -9


class MainTest(unittest.TestCase):
    """The Kiosk's run, with a fake in place of the Crib. Every stop ends the Crib by name."""

    STOP = ["docker", "kill", f"kiosk-crib-run-{os.getpid()}"]

    def setUp(self):
        """Keep the run off the real runtimes, and record the stop command."""
        self.stop = hush_runtimes(self)

    def run_main(self, crib):
        """Return main()'s exit status and its stderr, with `crib` as the launched process."""
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(stderr):
            with mock.patch.object(mcp_server, "launch_client", return_value=crib):
                with mock.patch.object(kiosk.inference, "ask_model", return_value="al dente"):
                    return kiosk.main(), stderr.getvalue()

    def stop_commands(self):
        """Return every stop command the Kiosk ran."""
        return [call.args[0] for call in self.stop.call_args_list]

    def test_invalid_utf8(self):
        """A byte that is not UTF-8 stops the Crib as a protocol violation, not a crash."""
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(stderr):
            with mock.patch.object(mcp_server.subprocess, "Popen", FakeCrib):
                self.assertEqual(kiosk.main(), kiosk.EXIT_PROTOCOL_VIOLATION)
        self.assertIn("protocol violation, stopping the Crib", stderr.getvalue())
        self.assertEqual(self.stop_commands(), [self.STOP])

    def test_clean_finish(self):
        """An Agent that finishes by itself is never stopped, and its status passes through."""
        crib = FakeCrib()
        crib.stdout = io.StringIO("")
        crib.wait = lambda timeout=None: 0
        status, text = self.run_main(crib)
        self.assertEqual(status, kiosk.EXIT_OK)
        self.assertEqual(self.stop_commands(), [])
        self.assertIn("finished cleanly", text)

    def test_budget_spent(self):
        """The call past the budget stops the Crib, and the Agent gets no answer to it."""
        crib = FakeCrib()
        crib.stdout = io.StringIO(build_call(1) + build_call(2) + build_call(3))
        answers = []
        crib.stdin.write = answers.append
        with mock.patch.object(kiosk, "CALL_BUDGET", 2):
            status, text = self.run_main(crib)
        self.assertEqual(status, kiosk.EXIT_BUDGET_SPENT)
        self.assertIn("budget of 2 ask_ai calls spent, stopping the Crib", text)
        self.assertEqual(self.stop_commands(), [self.STOP])
        self.assertEqual(len(answers), 2)

    def test_time_limit(self):
        """A silent Agent is stopped when the time limit passes, from beside the blocked read."""
        stopped = threading.Event()
        self.stop.side_effect = lambda *args, **options: stopped.set()

        def stay_silent():
            """Say nothing until the Crib is stopped, as a hung Agent would."""
            stopped.wait(10)
            return
            yield

        crib = FakeCrib()
        crib.stdout = stay_silent()
        with mock.patch.object(kiosk, "RUN_LIMIT", 0.05):
            status, text = self.run_main(crib)
        self.assertTrue(stopped.is_set())
        self.assertEqual(status, kiosk.EXIT_TIME_LIMIT)
        self.assertIn("time limit of 0.05 seconds passed, stopping the Crib", text)
        self.assertEqual(self.stop_commands(), [self.STOP])

    def test_failed_stop(self):
        """When the stop command fails and the Crib's command lingers, the Kiosk kills the command and says so."""
        self.stop.side_effect = FileNotFoundError("docker")
        crib = FakeCrib()
        crib.stdout = io.StringIO("not json\n")
        answers = [mcp_server.subprocess.TimeoutExpired("docker", 30), -9]

        def wait(timeout=None):
            """Outlive the first wait, then report the kill."""
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        crib.wait = wait
        status, text = self.run_main(crib)
        self.assertEqual(status, kiosk.EXIT_PROTOCOL_VIOLATION)
        self.assertIn("the stop command failed", text)
        self.assertIn("still running, killing it", text)
        self.assertTrue(crib.killed)

    def test_interrupted(self):
        """Ctrl-C stops the Crib by name, since not every runtime passes the signal in."""
        crib = FakeCrib()
        with mock.patch.object(mcp_server, "serve_client", side_effect=KeyboardInterrupt):
            status, _ = self.run_main(crib)
        self.assertEqual(status, kiosk.EXIT_INTERRUPTED)
        self.assertEqual(self.stop_commands(), [self.STOP])
        self.assertEqual(crib.waits, [True])


if __name__ == "__main__":
    unittest.main()
