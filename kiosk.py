"""
# ==============================================
# East Van AI -- AI for the rest of us!
# https://github.com/east-van-ai
# contact: east-van-ai@proton.me
# license: MIT
# ==============================================

The Kiosk: the trusted host process between the Agent and its capabilities.

It starts the Agent in the Crib, a disposable Docker container with no
network, and serves MCP on the container's standard streams. The Agent asks
for inference through the `ask_ai` tool. The Kiosk checks the request, queries
Ollama itself, and hands back the text. The Agent never learns the model or
where it runs.

This file holds what the Kiosk is and allows: the policy, the Crib's
confinement, and the greeting. It juggles the rest. How to speak MCP lives
in `mcp_server.py`, and how to reach the model in `backend_ollama.py`.

The Kiosk is the server and the Agent the client, although the Kiosk
launches the Agent. The framing does not care who started whom.
"""

import json
import os
import sys

import backend_ollama as inference
import mcp_server

SERVER_INFO = {"name": "kiosk", "version": "0.1.0"}

IMAGE = "kiosk-crib"
#: The only host variables that reach the Crib. Each is copied by name.
PASSED = ("CARBONARA_ORDER", "CARBONARA_TEMPERATURE", "CARBONARA_STREAM_DELAY")

#: Inference calls allowed per run. A carbonara takes at most eight.
CALL_BUDGET = 20

#: The Kiosk's exit statuses. A clean run passes the Agent app's own through.
EXIT_OK = 0
EXIT_PROTOCOL_VIOLATION = 1
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

#: What each confinement flag means, in the order the greeting says it.
CONFINEMENT = (
    (("--rm",), "removed on exit"),
    (("--network", "none"), "no network"),
    (("--read-only",), "read-only"),
    (("--cap-drop", "ALL"), "no capabilities"),
    (("--security-opt", "no-new-privileges"), "no new privileges"),
    (("--user", "nobody"), "runs as nobody"),
)

MAGENTA = "\033[95m"
LIGHT_GREY = "\033[37m"
RESET = "\033[0m"


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


def docker_command():
    """Return the `docker run` line that starts the Crib."""
    passed = [flag for name in PASSED for flag in ("-e", name)]
    return [
        "docker", "run", "--rm", "-i", "--init",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "nobody",
        *passed,
        IMAGE,
    ]


def crib_facts(command):
    """Return the confinement phrases whose flags appear in a `docker run` line.

    A phrase is claimed only when its whole flag, value included, is on the
    line, so the greeting cannot outlive a flag.
    """
    facts = []
    for flag, phrase in CONFINEMENT:
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
    the inference. `infer` is passed in, so tests can hand in a fake.
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
            log_line(f"ask_ai refused: budget of {CALL_BUDGET} spent")
            raise mcp_server.ToolRefusal(f"call budget of {CALL_BUDGET} spent")
        spent += 1
        log_line(f"ask_ai #{spent} allowed")
        try:
            return infer(arguments["system"], arguments["user"], arguments.get("temperature"))
        except (OSError, KeyError, ValueError) as error:
            log_line(f"ask_ai #{spent} failed: {error}")
            raise mcp_server.ToolRefusal(f"inference failed: {error}")

    return {"ask_ai": (ASK_AI, ask_ai)}


def print_greeting():
    """Introduce the Kiosk, the model, and the Crib, from the settings in force."""
    passed = [name for name in PASSED if name in os.environ]
    log_line("Hello! I am Kiosk. The Agent app, a kitchen in the Crib, asks me, and I ask the model on its behalf.")
    log_line(f"I can pass {', '.join(PASSED)} into the Crib. Passing now: {', '.join(passed) or 'nothing'}")
    log_line(f"Crib: {IMAGE}, {', '.join(crib_facts(docker_command()))}")
    log_line(inference.describe_backend())
    log_line(f"budget: {CALL_BUDGET} ask_ai calls")
    if MCP_TRACE:
        log_line("trace: on, every MCP message shown as formatted JSON")
    else:
        log_line("trace: off (MCP_TRACE=1 shows every MCP message)")


def describe_exit(exit_status):
    """Return the log line for how the Agent app ended."""
    if exit_status == EXIT_OK:
        return f"the Agent app finished cleanly (exit {exit_status})"
    return f"the Agent app failed (exit {exit_status})"


def main():
    """Start the Crib, serve it until the Agent app exits, and return its exit status."""
    print_greeting()
    agent_app = mcp_server.launch_client(docker_command())
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
        agent_app.terminate()
        agent_app.wait()
        return EXIT_PROTOCOL_VIOLATION
    except BrokenPipeError:
        log_line("the Agent app closed its stream mid-answer")
    except KeyboardInterrupt:
        # Ctrl-C reaches the docker client too, which passes it into the Crib.
        log_line("interrupted, waiting for the Crib to stop")
        agent_app.wait()
        return EXIT_INTERRUPTED
    finally:
        try:
            agent_app.stdin.close()
        except BrokenPipeError:
            pass
    exit_status = agent_app.wait()
    log_line(describe_exit(exit_status))
    log_line("Goodbye!")
    return exit_status


if __name__ == "__main__":
    sys.exit(main())
