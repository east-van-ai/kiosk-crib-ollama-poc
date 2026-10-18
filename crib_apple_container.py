"""The Apple container flavour: the Crib as a disposable Apple container.

Each container gets its own lightweight VM. The flags match Docker's but
one: `container run` has no `--security-opt`, so the Crib cannot ask for no
new privileges.

A signal to `container run` is not a stop. Version 1.5.0 answers SIGTERM with
"missing signal in xpc message" and keeps the container running, so the Crib
is stopped by name.

https://github.com/apple/container/blob/main/docs/command-reference.md
"""

import re
import subprocess

IMAGE = "kiosk-crib-apple-container"
RUNTIME = "Apple container"

#: What each confinement flag means, in the order the greeting says it.
CONFINEMENT = (
    (("--rm",), "removed on exit"),
    (("--network", "none"), "no network"),
    (("--read-only",), "read-only"),
    (("--cap-drop", "ALL"), "no capabilities"),
    (("--user", "nobody"), "runs as nobody"),
)

#: What each limit flag means, in the order the greeting says it.
LIMITS = (
    (("--memory", "256m"), "256 MB of memory"),
    (("--cpus", "1"), "1 CPU"),
    (("--ulimit", "nproc=32"), "at most 32 processes"),
)

#: What the runtime does whatever the flags say. Nothing, for a container.
BY_DESIGN = ()


def build_command(passed, run_name):
    """Return the `container run` line that starts the Crib as `run_name`, passing the named variables.

    `--progress none` keeps the image's start-up lines out of the greeting.
    """
    passed_flags = [flag for name in passed for flag in ("-e", name)]
    return [
        "container", "run", "--rm", "-i", "--init", "--progress", "none", "--name", run_name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--user", "nobody",
        "--memory", "256m",
        "--cpus", "1",
        "--ulimit", "nproc=32",
        *passed_flags,
        IMAGE,
    ]


def stop_command(run_name):
    """Return the line that ends the Crib named `run_name`, whatever the Agent is doing."""
    return ["container", "kill", run_name]


def runtime_version():
    """Return the `container` version as it reports it, or None when it does not answer."""
    try:
        answer = subprocess.run(
            ["container", "--version"], capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"version (\S+)", answer.stdout)
    return found.group(1) if found else None


def inspect_setup():
    """Return the phrases the runtime reports about a Crib set up beforehand. None, for a container."""
    return []


def inspect_limits():
    """Return the limits set outside the launch line. None, for a container."""
    return []
