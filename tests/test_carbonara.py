"""Tests for the crew's greeting in the Agent."""

import contextlib
import io
import re
import unittest
from unittest import mock

import carbonara

ANSI = re.compile(r"\033\[[0-9;]*m")


def greeting(tools):
    """Return the crew's greeting as plain text."""
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        carbonara.print_greeting(tools)
    return ANSI.sub("", stdout.getvalue())


class GreetingTest(unittest.TestCase):
    """The crew greets from the registry and the discovered tools."""

    def test_roles_greet_in_their_voice(self):
        """The Dispatcher says Buonasera, the Expeditor Salve."""
        lines = greeting(["ask_ai"]).splitlines()
        self.assertEqual(lines[0].split("] ", 1)[1].split()[0], "Buonasera!")
        self.assertEqual(lines[1].split("] ", 1)[1].split()[0], "Salve!")

    def test_expeditor_names_discovered_tools(self):
        """The Expeditor names exactly the tools it was offered."""
        self.assertIn("I may ask for: ask_ai\n", greeting(["ask_ai"]))
        self.assertIn("I may ask for: ask_ai, sf_query\n", greeting(["ask_ai", "sf_query"]))

    def test_every_doer_says_ciao(self):
        """Each doer in the registry greets once, with its own description."""
        text = greeting(["ask_ai"])
        for name, (_, description) in carbonara.DOER_REGISTRY.items():
            with self.subTest(doer=name):
                self.assertIn(f"Ciao! I am {name}, the one who {description}.", text)
        self.assertEqual(text.count("Ciao!"), len(carbonara.DOER_REGISTRY))



class AskTest(unittest.TestCase):
    """The kitchen's one capability, over the generic client."""

    def test_ask_ai_arguments(self):
        """ask_ai calls the ask_ai tool with the messages and the kitchen's temperature."""
        with mock.patch.object(carbonara.mcp_client, "call_tool", return_value="ok") as call_tool:
            self.assertEqual(carbonara.ask_ai("cook", "boil"), "ok")
        call_tool.assert_called_once_with(
            "ask_ai", {"system": "cook", "user": "boil", "temperature": carbonara.TEMPERATURE}
        )


if __name__ == "__main__":
    unittest.main()
