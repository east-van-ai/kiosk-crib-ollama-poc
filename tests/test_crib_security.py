"""Tests for the Crib itself: what the runtime does with the Kiosk's flags, not what the Kiosk's code does.

Every probe runs in a real Crib, started with the flavour's own launch line,
so a flag the runtime ignores fails here and nowhere else. A flavour is
skipped when its runtime is not running or its image is not built, so the
rest of the suite still needs neither.

The container machine has no test here. It is persistent, and its only stop
is stopping the machine, which is not a test's to do.
"""

import os
import socket
import subprocess
import unittest

import crib_apple_container
import crib_docker

#: Seconds one probe may take, image start included.
PROBE_WAIT = 60
#: A name the DNS probe asks for. The Mac must resolve it, or the probe proves nothing.
KNOWN_NAME = "github.com"

REACH_INTERNET = """
import socket
try:
    socket.create_connection(("1.1.1.1", 53), timeout=5)
    print("connected")
except OSError as error:
    print("no route:", error)
"""

RESOLVE_NAME = f"""
import socket
try:
    print("resolved", socket.getaddrinfo("{KNOWN_NAME}", 443)[0][4][0])
except OSError as error:
    print("no DNS:", error)
"""

WRITE_FILE = """
for path in ("/crib/note", "/tmp/note", "/note"):
    try:
        open(path, "w").close()
        print("wrote", path)
    except OSError as error:
        print("refused", path)
"""

READ_STATUS = """
import os
print("uid", os.getuid())
for line in open("/proc/self/status"):
    if line.startswith(("CapEff", "NoNewPrivs")):
        print(line.split()[0], int(line.split()[1], 16))
"""

FORK_MANY = """
import os, time
forked = 0
try:
    for _ in range(200):
        if os.fork() == 0:
            time.sleep(3)
            os._exit(0)
        forked += 1
except OSError as error:
    print("stopped:", error)
print("forked", forked)
"""

FILL_MEMORY = """
blocks = []
for _ in range(128):
    blocks.append(bytearray(b"x") * (16 * 2**20))
print("survived", len(blocks) * 16, "MB")
"""

BURN_CPUS = """
import os, time
for _ in range(4):
    if os.fork() == 0:
        end = time.time() + 2
        while time.time() < end:
            pass
        os._exit(0)
for _ in range(4):
    os.wait()
spent = os.times()
print("cpu-seconds", round(spent.children_user + spent.children_system, 1))
"""

HANG = """
import time
print("ready", flush=True)
time.sleep(600)
"""


def image_built(runtime, image):
    """Say whether `runtime` answers and holds `image`."""
    try:
        answer = subprocess.run([runtime, "image", "inspect", image], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return answer.returncode == 0


class CribProbes:
    """The probes every container flavour must pass. A subclass names the flavour."""

    crib = None
    runtime = None

    @classmethod
    def setUpClass(cls):
        """Skip the flavour when its runtime or its image is missing."""
        if not image_built(cls.runtime, cls.crib.IMAGE):
            raise unittest.SkipTest(f"{cls.crib.RUNTIME} is not running, or {cls.crib.IMAGE} is not built")

    def build_probe(self, code):
        """Return the Kiosk's own launch line, running `code` in place of the Agent."""
        run_name = f"kiosk-crib-test-{os.getpid()}-{self.id().rsplit('.', 1)[-1].replace('_', '-')}"
        command = self.crib.build_command((), run_name)
        self.addCleanup(subprocess.run, self.crib.stop_command(run_name), capture_output=True)
        return command[:-1] + ["--entrypoint", "python", command[-1], "-c", code], run_name

    def run_probe(self, code):
        """Run `code` in a real Crib and return the finished process."""
        command, _ = self.build_probe(code)
        return subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=PROBE_WAIT,
        )

    def test_no_network(self):
        """The Crib has no route out."""
        self.assertIn("no route", self.run_probe(REACH_INTERNET).stdout)

    def test_no_dns(self):
        """The Crib resolves no name the Mac can, so there is no DNS to tunnel through."""
        try:
            socket.getaddrinfo(KNOWN_NAME, 443)
        except OSError:
            self.skipTest(f"the Mac cannot resolve {KNOWN_NAME} either")
        self.assertIn("no DNS", self.run_probe(RESOLVE_NAME).stdout)

    def test_read_only(self):
        """The Agent can write nowhere: not its own folder, not /tmp, not the root."""
        written = self.run_probe(WRITE_FILE).stdout
        self.assertEqual(written.count("refused"), 3, written)

    def test_nobody_without_capabilities(self):
        """The Agent runs as nobody, holding no capability."""
        status = self.run_probe(READ_STATUS).stdout
        self.assertIn("uid 65534", status)
        self.assertIn("CapEff: 0", status)

    def test_process_limit(self):
        """A fork bomb stops under the limit."""
        forks = self.run_probe(FORK_MANY).stdout
        self.assertIn("stopped:", forks)
        self.assertLess(int(forks.split("forked")[1]), 32)

    def test_memory_limit(self):
        """An Agent that fills its memory is killed, well before two gigabytes."""
        filled = self.run_probe(FILL_MEMORY)
        self.assertNotIn("survived", filled.stdout)
        self.assertEqual(filled.returncode, 137)

    def test_cpu_limit(self):
        """Four busy processes get one CPU's worth of time between them, not four."""
        burned = self.run_probe(BURN_CPUS).stdout
        self.assertLess(float(burned.split("cpu-seconds")[1]), 3.0)

    def test_stop_by_name(self):
        """The stop command ends a Crib whose Agent hangs and reads nothing."""
        command, run_name = self.build_probe(HANG)
        hung = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
        self.addCleanup(hung.stdout.close)
        self.addCleanup(hung.kill)
        self.assertEqual(hung.stdout.readline(), "ready\n")
        subprocess.run(self.crib.stop_command(run_name), capture_output=True, timeout=PROBE_WAIT)
        self.assertIsNotNone(hung.wait(timeout=PROBE_WAIT))


class DockerCribTest(CribProbes, unittest.TestCase):
    """The Docker Crib."""

    crib = crib_docker
    runtime = "docker"

    def test_no_new_privileges(self):
        """The Agent cannot gain privileges. Only Docker has the flag."""
        self.assertIn("NoNewPrivs: 1", self.run_probe(READ_STATUS).stdout)


class AppleContainerCribTest(CribProbes, unittest.TestCase):
    """The Apple container Crib."""

    crib = crib_apple_container
    runtime = "container"


if __name__ == "__main__":
    unittest.main()
