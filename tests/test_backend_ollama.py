"""Tests for the Ollama backend's settings and its line in the greeting."""

import importlib
import os
import unittest
from unittest import mock

import backend_ollama


def reload_under(environment):
    """Return the backend module as it reads the given environment."""
    with mock.patch.dict(os.environ, environment, clear=True):
        return importlib.reload(backend_ollama)


class SettingsTest(unittest.TestCase):
    """The backend reads its own settings, and describes them."""

    def tearDown(self):
        """Put the module back as the real environment has it."""
        importlib.reload(backend_ollama)

    def test_defaults(self):
        """With nothing set, Tiny Aya on the local Ollama."""
        backend = reload_under({})
        self.assertEqual(
            backend.describe_backend(),
            "model: hf.co/CohereLabs/tiny-aya-global-GGUF:Q4_K_M, served by Ollama at http://127.0.0.1:11434",
        )

    def test_settings_named(self):
        """The model and the address come from the Kiosk's environment."""
        backend = reload_under({"CARBONARA_MODEL": "llama3.2:latest", "OLLAMA_HOST": "http://mac.local:11434"})
        self.assertEqual(backend.describe_backend(), "model: llama3.2:latest, served by Ollama at http://mac.local:11434")

    def test_host_normalized(self):
        """A bare host gains a scheme, and a trailing slash goes."""
        for host, url in (("127.0.0.1:11434", "http://127.0.0.1:11434"), ("http://mac.local:11434/", "http://mac.local:11434")):
            with self.subTest(host=host):
                self.assertEqual(reload_under({"OLLAMA_HOST": host}).OLLAMA_URL, url)


if __name__ == "__main__":
    unittest.main()
