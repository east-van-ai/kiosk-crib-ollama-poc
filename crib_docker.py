"""The Docker flavour: the Crib as a disposable Docker container.

Every container shares Docker Desktop's one Linux VM. The confinement is all
in the flags, so the runtime adds nothing by design.

`--ulimit nproc` caps the Crib's processes, although Docker's own
`--pids-limit` is the stronger flag: Apple container has no such flag, and
the two flavours take the same limits. The count is per user and Docker's
containers share one VM, so other containers running as nobody use it up too.

https://docs.docker.com/reference/cli/docker/container/run/
"""

import subprocess

IMAGE = "kiosk-crib-docker"
RUNTIME = "Docker"

#: What each confinement flag means, in the order the greeting says it.
CONFINEMENT = (
    (("--rm",), "removed on exit"),
    (("--network", "none"), "no network"),
    (("--read-only",), "read-only"),
    (("--cap-drop", "ALL"), "no capabilities"),
    (("--security-opt", "no-new-privileges"), "no new privileges"),
    (("--user", "nobody"), "runs as nobody"),
)

#: What each limit flag means, in the order the greeting says it.
LIMITS = (
    (("--memory", "256m"), "256 MB of memory"),
    (("--cpus", "1"), "1 CPU"),
    (("--ulimit", "nproc=32"), "at most 32 processes"),
)

#: What the runtime does whatever the flags say. Nothing, for Docker.
BY_DESIGN = ()


def build_command(passed, run_name):
    """Return the `docker run` line that starts the Crib as `run_name`, passing the named variables."""
    passed_flags = [flag for name in passed for flag in ("-e", name)]
    return [
        "docker", "run", "--rm", "-i", "--init", "--name", run_name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "nobody",
        "--memory", "256m",
        "--cpus", "1",
        "--ulimit", "nproc=32",
        *passed_flags,
        IMAGE,
    ]


def stop_command(run_name):
    """Return the line that ends the Crib named `run_name`, whatever the Agent is doing."""
    return ["docker", "kill", run_name]


def runtime_version():
    """Return the Docker engine's version as it reports it, or None when it does not answer."""
    try:
        answer = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return answer.stdout.strip() or None


def inspect_setup():
    """Return the phrases the runtime reports about a Crib set up beforehand. None, for Docker."""
    return []


def inspect_limits():
    """Return the limits set outside the launch line. None, for Docker."""
    return []
