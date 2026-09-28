"""The Ollama backend: how the Kiosk performs inference, over Ollama's HTTP API.

A backend holds its own settings and describes itself for the greeting.
Whether a call is allowed is not its business: the Kiosk decides that before
it ever asks.

https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-chat-completion
"""

import json
import os
import urllib.request

MODEL = os.environ.get("CARBONARA_MODEL", "hf.co/CohereLabs/tiny-aya-global-GGUF:Q4_K_M")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_URL = (OLLAMA_HOST if "://" in OLLAMA_HOST else f"http://{OLLAMA_HOST}").rstrip("/")
OLLAMA_TIMEOUT = 300


def ask_model(system_message, user_message, temperature):
    """Ask Ollama for one reply and return its text."""
    body = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
    }
    if temperature is not None:
        body["options"] = {"temperature": temperature}
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as response:
        return json.load(response)["message"]["content"]


def describe_backend():
    """Return the greeting's line for this backend, from the settings in force."""
    return f"model: {MODEL}, served by Ollama at {OLLAMA_URL}"
