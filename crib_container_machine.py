"""The container machine flavour: the Crib as a persistent Apple container machine.

A container machine is a whole Linux environment that lives across runs.
It is created once, by hand, and the Kiosk only runs the Agent in it. Its
launch line has no confinement flags to offer, and Apple's doc warns that it
"trades away much of the isolation" of a regular container. The greeting
shows that rather than working around it.

`container machine run` joins its arguments and runs them through
`/bin/bash -c`, so the line stays free of quoting. `-e NAME` inherits the
value from the Kiosk's environment, untouched by the shell.

The machine ignores the image's `ENV` and `WORKDIR`, so the line puts back
what the Agent needs: `-w /crib`, and `python -u` for the image's
`PYTHONUNBUFFERED=1`. Without it, the Agent's output arrives late, in one
lump, behind the Kiosk's own lines.

The only stop is stopping the whole machine. The Agent runs as the Mac user
beside everything else in the machine, so no name marks off what it started.
The next run boots the machine again. `machine run` accepts `--ulimit` and,
in 1.5.0, ignores it, so the line carries no limit. Memory and CPUs are set
when the machine is made, and the Kiosk reads them back.

https://github.com/apple/container/blob/main/docs/container-machine.md
"""

import json
import subprocess

import crib_apple_container

IMAGE = "kiosk-crib-container-machine"
RUNTIME = "container machine"
#: The machine the one-time setup creates.
MACHINE = "kiosk-crib"

#: What each confinement flag means. `machine run` has none to offer.
CONFINEMENT = ()

#: What each limit flag means. `machine run` has none that holds.
LIMITS = ()

#: What the runtime does whatever the flags say, from Apple's doc.
BY_DESIGN = ("persistent", "network on", "runs as your Mac user", "your SSH agent reachable")


def build_command(passed, run_name):
    """Return the `container machine run` line that starts the Agent, passing the named variables.

    `run_name` goes unused: the machine has one name, given when it was made.
    """
    passed_flags = [flag for name in passed for flag in ("-e", name)]
    return [
        "container", "machine", "run", "-i", "-n", MACHINE, "-w", "/crib",
        *passed_flags,
        "python", "-u", "carbonara.py",
    ]


def stop_command(run_name):
    """Return the line that ends the Agent, by stopping the whole machine."""
    return ["container", "machine", "stop", MACHINE]


#: The machine is run by the same `container` command as an Apple container.
runtime_version = crib_apple_container.runtime_version


def inspect_machine():
    """Return what the machine reports about itself, or an empty dict when it does not answer."""
    try:
        answer = subprocess.run(
            ["container", "machine", "inspect", MACHINE],
            capture_output=True, text=True, check=True, timeout=10,
        )
        machines = json.loads(answer.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    return machines[0] if machines else {}


def inspect_setup():
    """Return the phrases the machine reports about its own setup.

    The home folder is chosen when the machine is created, not on the launch
    line, so the Kiosk asks the machine. "no home folder" is claimed only
    when it answers `homeMount: none`.
    """
    if inspect_machine().get("homeMount") == "none":
        return ["no home folder"]
    return []


def inspect_limits():
    """Return the memory and CPUs the machine reports, both set when it was made."""
    machine = inspect_machine()
    limits = []
    memory = machine.get("memory")
    if isinstance(memory, int):
        limits.append(f"{memory // 2**20} MB of memory")
    cpus = machine.get("cpus")
    if isinstance(cpus, int):
        limits.append(f"{cpus} CPU" if cpus == 1 else f"{cpus} CPUs")
    return limits
