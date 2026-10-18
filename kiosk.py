"""
# ==============================================
# East Van AI -- AI for the rest of us!
# https://github.com/east-van-ai
# contact: east-van-ai@proton.me
# license: MIT
# ==============================================

The Kiosk: the trusted host process between the Agent and its capabilities.

It starts the Agent in the Crib and serves MCP on the Crib's standard
streams. The Crib's flavour, how it is made, is picked per run from `CRIB`.
The Agent asks for inference through the `ask_ai` tool. The Kiosk checks the
request, queries Ollama itself, and hands back the text. The Agent never
learns the model or where it runs.

This file holds what the Kiosk is and allows: the policy and the greeting.
It juggles the rest. How to speak MCP lives in `mcp_server.py`, how to reach
the model in `backend_ollama.py`, and how to make each Crib in `crib_*.py`.

The Kiosk is the server and the Agent the client, although the Kiosk
launches the Agent. The framing does not care who started whom.
"""

import json
import os
import shlex
import subprocess
import sys
import threading

import backend_ollama as inference
import crib_apple_container
import crib_container_machine
import crib_docker
import mcp_server

SERVER_INFO = {"name": "kiosk", "version": "0.2.0"}

#: The Crib's flavours, by the name `CRIB` picks them with. The first is the default.
CRIBS = {
    "docker": crib_docker,
    "apple-container": crib_apple_container,
    "container-machine": crib_container_machine,
}

#: The only host variables that reach the Crib. Each is copied by name.
PASSED = ("CARBONARA_ORDER", "CARBONARA_TEMPERATURE", "CARBONARA_STREAM_DELAY")

#: Inference calls allowed per run. A carbonara takes at most eight.
CALL_BUDGET = 20
#: Seconds a run may take. A carbonara takes about thirty.
RUN_LIMIT = 300
#: Seconds a stop may take. Stopping a container machine takes about ten.
STOP_WAIT = 30

#: The Kiosk's exit statuses. A clean run passes the Agent app's own through.
#: Ctrl-C sends SIGINT, signal 2; the shell reports it as status 128 + 2 = 130.
#: 124 is what `timeout` reports for a command it had to stop.
EXIT_OK = 0
EXIT_PROTOCOL_VIOLATION = 1
EXIT_USAGE = 2
EXIT_BUDGET_SPENT = 3
EXIT_TIME_LIMIT = 124
EXIT_INTERRUPTED = 130

#: Print every MCP message as it crosses, when MCP_TRACE is 1.
MCP_TRACE = os.environ.get("MCP_TRACE") == "1"
#: The trace's labels for the server's "in" and "out".
TRACE_LABELS = {"in": "Agent app → Kiosk", "out": "Kiosk → Agent app"}

ASK_AI = {
    "name": "ask_ai",
    "description": "Answer a prompt with the Kiosk's model.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "system": {"type": "string"},
            "user": {"type": "string"},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        },
        "required": ["system", "user"],
        "additionalProperties": False,
    },
}

MAGENTA = "\033[95m"
LIGHT_GREY = "\033[37m"
RESET = "\033[0m"


class BudgetSpent(Exception):
    """The Agent asked for one inference call more than a run allows."""


def pick_crib():
    """Return the flavour module `CRIB` names, Docker when unset, or None for an unknown name."""
    return CRIBS.get(os.environ.get("CRIB", "docker"))


def log_line(text):
    """Print one Kiosk line to stderr, beside the Agent's own output."""
    print(f"{MAGENTA}[KIOSK]{RESET} {text}", file=sys.stderr, flush=True)


def trace_line(direction, line):
    """Print one message that crossed the boundary, in light grey, on stderr.

    Valid JSON prints indented under its label. A line that is not JSON
    prints exactly as it came, since it is what makes the Kiosk stop the Crib.
    """
    try:
        shown = json.dumps(json.loads(line), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        shown = line
    print(f"{LIGHT_GREY}[MCP {TRACE_LABELS[direction]}]\n{shown}{RESET}", file=sys.stderr, flush=True)


def crib_facts(command, confinement):
    """Return the confinement phrases whose flags appear on a Crib's launch line.

    A phrase is claimed only when its whole flag, value included, is on the
    line, so the greeting cannot outlive a flag.
    """
    facts = []
    for flag, phrase in confinement:
        width = len(flag)
        if any(tuple(command[i:i + width]) == flag for i in range(len(command))):
            facts.append(phrase)
    return facts


def ask_ai_problem(arguments):
    """Return what is wrong with an `ask_ai` call's arguments, or None when they are fine."""
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    unknown = set(arguments) - set(ASK_AI["inputSchema"]["properties"])
    if unknown:
        return f"unknown arguments: {', '.join(sorted(unknown))}"
    for name in ("system", "user"):
        if not isinstance(arguments.get(name), str):
            return f"{name} must be a string"
    temperature = arguments.get("temperature")
    if temperature is None:
        return None
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        return "temperature must be a number"
    if not 0 <= temperature <= 2:
        return "temperature must be between 0 and 2"
    return None


def build_tools(infer):
    """Return the Kiosk's tool registry, with `ask_ai` performed by `infer`.

    The handler holds the policy: the argument check, then the budget, then
    the inference. A bad argument is refused and the run goes on. A call past
    the budget raises BudgetSpent, which ends the run. `infer` is passed in,
    so tests can hand in a fake.
    """
    spent = 0

    def ask_ai(arguments):
        """Check one `ask_ai` call against the policy, then perform it."""
        nonlocal spent
        problem = ask_ai_problem(arguments)
        if problem:
            log_line(f"ask_ai refused: {problem}")
            raise mcp_server.ToolRefusal(problem)
        if spent >= CALL_BUDGET:
            raise BudgetSpent(f"budget of {CALL_BUDGET} ask_ai calls spent")
        spent += 1
        log_line(f"ask_ai #{spent} allowed")
        try:
            return infer(arguments["system"], arguments["user"], arguments.get("temperature"))
        except (OSError, KeyError, ValueError) as error:
            log_line(f"ask_ai #{spent} failed: {error}")
            raise mcp_server.ToolRefusal(f"inference failed: {error}")

    return {"ask_ai": (ASK_AI, ask_ai)}


def print_greeting(crib, command):
    """Introduce the Kiosk, the model, and the Crib, from the settings in force.

    `command` is the list the Kiosk launches, so the Crib line and the printed
    command describe what actually runs.
    """
    passed = [name for name in PASSED if name in os.environ]
    log_line("Hello! I am Kiosk. The Agent app, a kitchen in the Crib, asks me, and I ask the model on its behalf.")
    log_line(f"I can pass {', '.join(PASSED)} into the Crib. Passing now: {', '.join(passed) or 'nothing'}")
    facts = crib_facts(command, crib.CONFINEMENT) + crib.inspect_setup()
    runtime = " ".join(filter(None, [crib.RUNTIME, crib.runtime_version()]))
    log_line(f"Crib: {', '.join([f'{crib.IMAGE} on {runtime}', *facts])}")
    if crib.BY_DESIGN:
        log_line(f"by design: {', '.join(crib.BY_DESIGN)}")
    limits = crib_facts(command, crib.LIMITS) + crib.inspect_limits()
    log_line(f"limits: {', '.join(limits) or 'none'}")
    log_line(f"command: {shlex.join(command)}")
    log_line(inference.describe_backend())
    log_line(f"stops: after {CALL_BUDGET} ask_ai calls, after {RUN_LIMIT} seconds, on any message that is not MCP")
    if MCP_TRACE:
        log_line("trace: on, every MCP message shown as formatted JSON")
    else:
        log_line("trace: off (MCP_TRACE=1 shows every MCP message)")


def describe_exit(exit_status):
    """Return the log line for how the Agent app ended."""
    if exit_status == EXIT_OK:
        return f"the Agent app finished cleanly (exit {exit_status})"
    return f"the Agent app failed (exit {exit_status})"


def stop_crib(crib, run_name):
    """Stop the Crib by name, with its runtime's own command.

    A signal to the launch command is not a stop: `container` 1.5.0 answers
    one with an error and keeps the container running.
    """
    try:
        subprocess.run(crib.stop_command(run_name), capture_output=True, timeout=STOP_WAIT)
    except (OSError, subprocess.SubprocessError) as error:
        log_line(f"the stop command failed: {error}")


def await_crib(agent_app):
    """Return the launch command's exit status, killing it when it outlives the stopped Crib."""
    try:
        return agent_app.wait(timeout=STOP_WAIT)
    except subprocess.TimeoutExpired:
        log_line("the Crib's command is still running, killing it")
        agent_app.kill()
        return agent_app.wait()


def main():
    """Start the Crib, serve it until the Agent app exits or a stop fires, and return the exit status."""
    crib = pick_crib()
    if crib is None:
        log_line(f"unknown CRIB {os.environ['CRIB']!r}, pick one of: {', '.join(CRIBS)}")
        return EXIT_USAGE
    run_name = f"kiosk-crib-run-{os.getpid()}"
    command = crib.build_command(PASSED, run_name)
    print_greeting(crib, command)
    agent_app = mcp_server.launch_client(command)
    timed_out = threading.Event()

    def stop_late():
        """Stop a Crib that has run past the time limit, from beside the blocked serve loop."""
        timed_out.set()
        log_line(f"time limit of {RUN_LIMIT} seconds passed, stopping the Crib")
        stop_crib(crib, run_name)

    timer = threading.Timer(RUN_LIMIT, stop_late)
    timer.daemon = True
    timer.start()
    stopped = None
    try:
        mcp_server.serve_client(
            agent_app.stdout,
            agent_app.stdin,
            build_tools(inference.ask_model),
            SERVER_INFO,
            trace=trace_line if MCP_TRACE else None,
            log=log_line,
        )
    except mcp_server.ProtocolError as error:
        log_line(f"protocol violation, stopping the Crib: {error}")
        stopped = EXIT_PROTOCOL_VIOLATION
    except BudgetSpent as error:
        log_line(f"{error}, stopping the Crib")
        stopped = EXIT_BUDGET_SPENT
    except BrokenPipeError:
        if not timed_out.is_set():
            log_line("the Agent app closed its stream mid-answer")
    except KeyboardInterrupt:
        log_line("interrupted, stopping the Crib")
        stopped = EXIT_INTERRUPTED
    finally:
        timer.cancel()
        try:
            agent_app.stdin.close()
        except BrokenPipeError:
            pass
    if timed_out.is_set():
        stopped = EXIT_TIME_LIMIT
    elif stopped is not None:
        stop_crib(crib, run_name)
    exit_status = await_crib(agent_app)
    if stopped is not None:
        return stopped
    log_line(describe_exit(exit_status))
    log_line("Goodbye!")
    return exit_status


if __name__ == "__main__":
    sys.exit(main())
