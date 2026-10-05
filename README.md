# carbonara no-framework ollama with kiosk and crib

![CI](https://github.com/east-van-ai/kiosk-crib-ollama-poc/actions/workflows/ci.yaml/badge.svg)

This project separates an AI agent's intelligence from its authority. The
agent runs inside a disposable Docker container with no network. It cannot
reach the AI model. It can only ask. A small trusted process on the Mac, the
Kiosk, hears the request, decides whether to allow it, and asks the model on
the agent's behalf.

The agent is the no-framework stack of the carbonara demo: a small kitchen
where a model plans the steps of a carbonara and hands each one to a
single-purpose cook. It needs inference for every step. It never holds the
model, the address of the model, or even its name.

## The idea

The agent may be clever, autonomous, and hard to trust. Its code may have
bugs, its dependencies may have holes, and its prompts may be manipulated.
None of that needs fixing here, because the agent holds no authority to
misuse.

The Kiosk holds the authority. It is small enough to read in one sitting,
and it is not an AI agent. It knows the protocol and the capabilities it is
willing to offer. It does not need to understand the agent's reasoning.

The goal is not a trustworthy agent. The goal is a boundary that makes
trusting the agent unnecessary.

## Terms

- **Agent.** The application inside the container. It holds the logic, the
  state, and the reasoning loop.
- **Crib.** The Docker container that holds the agent. It is disposable, it
  has no network, and no model is installed in it.
- **Kiosk.** The trusted process on the Mac. It starts the Crib, talks to
  the agent, and performs what it allows.
- **Inference Service.** What runs the model. Here it is Ollama on the Mac.
- **Capability.** Something the agent may ask the Kiosk to do. The first one
  is `ask_ai`, which answers a prompt with the Kiosk's model.

## The picture

```text
                     Mac
┌──────────────────────────────────────────┐
│                                          │
│   Kiosk ──────────────▶ Ollama           │
│     │    ask_ai,          └── Tiny Aya   │
│     │    if allowed                      │
│     │                                    │
│     │ MCP over stdin and stdout          │
│     ▼                                    │
│   ┌───────────────┐                      │
│   │ Crib          │  no network          │
│   │   Agent       │  no model            │
│   └───────────────┘  no credentials      │
│                                          │
└──────────────────────────────────────────┘
```

The Kiosk and the agent speak MCP, the Model Context Protocol, over the
container's standard streams. The Kiosk offers tools, and the agent calls
them.

The agent's `stdout` carries protocol messages and nothing else. Its kitchen
talk goes to `stderr`, which the Kiosk passes through to the terminal. That
keeps the protocol clean enough for the Kiosk to refuse anything that is not
MCP, and to stop the Crib when it sees it.

## What the Kiosk decides

- Which capabilities exist. The agent asks for the list and gets `ask_ai`.
- Whether a request is well formed. A missing prompt, an unknown argument,
  or a temperature out of range is refused before any model sees it.
- How many requests a run may make. The budget is 20, and a carbonara takes
  at most eight.
- Which model answers, and where it runs. The agent never knows.
- Which settings reach the Crib. Three named variables pass. Nothing else
  from the Mac's environment does.

## The files

- `kiosk.py` is the Kiosk: the policy, the Crib's confinement, and the
  greeting. Run it, and it starts everything else.
- `mcp_server.py` speaks MCP for the Kiosk. It knows the protocol and no tool.
- `mcp_client.py` speaks MCP for the agent. It knows the protocol and no tool.
- `backend_ollama.py` is how the Kiosk reaches Ollama. Another backend, such
  as a Cohere API call, would sit beside it.
- `carbonara.py` is the agent: the kitchen, with one capability, `ask_ai`.

Only `carbonara.py` and `mcp_client.py` go into the Crib.

## Requirements

- An Apple Silicon Mac. No separate Linux install is needed, since Docker
  Desktop provides the container runtime.
- [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/),
  running.
- [Ollama for macOS](https://ollama.com), running.
- Cohere's Tiny Aya Global model, pulled into Ollama.
- Python 3.14 on the Mac. The Kiosk and the agent use only the standard
  library, so there is nothing to install.

Pull the model once:

```bash
ollama pull hf.co/CohereLabs/tiny-aya-global-GGUF:Q4_K_M
```

## Run it

Build the Crib once:

```bash
docker build -t kiosk-crib .
```

Then start the Kiosk, which starts everything else:

```bash
CARBONARA_ORDER="make me a carbonara for four" python kiosk.py
```

Every trip to the model passes through the Kiosk. Trimmed:

```text
[KIOSK] Hello! I am Kiosk. The Agent app, a kitchen in the Crib, asks me, and I ask the model on its behalf.
[KIOSK] I can pass CARBONARA_ORDER, CARBONARA_TEMPERATURE, CARBONARA_STREAM_DELAY into the Crib. Passing now: CARBONARA_ORDER
[KIOSK] Crib: kiosk-crib, removed on exit, no network, read-only, no capabilities, no new privileges, runs as nobody
[KIOSK] model: hf.co/CohereLabs/tiny-aya-global-GGUF:Q4_K_M, served by Ollama at http://127.0.0.1:11434
[KIOSK] budget: 20 ask_ai calls
[KIOSK] trace: off (MCP_TRACE=1 shows every MCP message)
[KIOSK] server/discover
[KIOSK] tools/list: ask_ai
[DISPATCHER] Buonasera! I am the Dispatcher. I take orders and relay results. I know nothing about cooking.
[EXPEDITOR] Salve! I am the Expeditor. I plan the dish and pick the cooks. I may ask for: ask_ai
  [DOER boil_pasta]  Ciao! I am boil_pasta, the one who boils pasta to the right doneness.
...
[DISPATCHER] received order: "make me a carbonara for four"
[KIOSK] ask_ai #1 allowed
CALL make_egg_sauce: Whisk 4 eggs with 100ml of heavy cream, 50g of Pecorino Romano ...
...
[EXPEDITOR] executing plan: ['boil_pasta', 'fry_guanciale', 'make_egg_sauce', 'grate_cheese', 'combine']
...
[DISPATCHER] "Hello! Our chef has prepared a delicious spaghetti dish with crispy guanciale, ..."
[KIOSK] the Agent app finished cleanly (exit 0)
[KIOSK] Goodbye!
```

A small model plans, and a small model is sometimes a poor cook. Heavy cream
in a carbonara is a real answer from Tiny Aya. The kitchen follows the plan
it is given.

## Everyone says hello

Before the order goes out, everyone involved introduces themselves. The
Kiosk is Canadian and says "Hello". The kitchen is Italian: "Buonasera" from
the Dispatcher, who faces the guest, "Salve" from the Expeditor, and "Ciao"
from each line cook.

It is a joke with a job. The greeting shows who is really taking part, and
every claim in it is read from the setup it describes. The Crib's line is
built from the flags the Kiosk passes to Docker, so dropping a flag drops
its words. The Expeditor names the tools the Kiosk actually offered.

Each side speaks only for what it controls. The Kiosk names the model and
the Crib's confinement. The kitchen names its crew and cannot name the
model, because it never learns it. The model does not introduce itself. A
small model's account of itself is unreliable, and the greeting is there to
tell the truth.

## Tuning

Three settings pass into the Crib, for the agent:

- `CARBONARA_ORDER` is the order the kitchen takes.
- `CARBONARA_TEMPERATURE` sets how adventurous the model is. The Kiosk
  refuses anything outside 0 to 2.
- `CARBONARA_STREAM_DELAY` paces the printed reasoning, for drama.

Two stay with the Kiosk, because they say where inference comes from:

- `CARBONARA_MODEL` names the model the Kiosk asks for.
- `OLLAMA_HOST` is where Ollama answers.

Swap the model, and the agent does not notice:

```bash
CARBONARA_MODEL=llama3.2:latest python kiosk.py
```

## Watch the wire

The greeting shows who is involved. The trace shows what passes between
them. With `MCP_TRACE=1`, the Kiosk prints every MCP message in both
directions, as formatted JSON, in light grey:

```bash
MCP_TRACE=1 python kiosk.py
```

```text
[MCP Agent app → Kiosk]
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    ...
    "name": "ask_ai",
    "arguments": {
      "system": "You are a kitchen expeditor. ...",
      "user": "make me a carbonara for four",
      "temperature": 0.3
...
[KIOSK] ask_ai #1 allowed
[MCP Kiosk → Agent app]
...
```

A line that is not JSON prints exactly as it came. A malformed message from
the agent still shows up just before the Kiosk stops the Crib.

## Try the boundary

The agent alone cannot cook. With no Kiosk on the other end, its first
request goes unanswered and it exits:

```bash
docker run --rm -i kiosk-crib < /dev/null
```

The Crib has no network, so the agent cannot go around the Kiosk. The same
image, run the way the Kiosk runs it, cannot even resolve the Mac:

```bash
docker run --rm --network none --entrypoint python kiosk-crib -c "import urllib.request; urllib.request.urlopen('http://host.docker.internal:11434', timeout=5)"
```

Docker is a containment layer, not a guarantee. The boundary also rests on
the Docker runtime, macOS, and what the Kiosk chooses to offer.

Two things sit outside the boundary. The first is the screen. The agent's
`stderr` reaches the terminal as it is, beside the Kiosk's own lines. The
agent, or a model reply it prints, can write a `[KIOSK]` line identical to
a real one, colour included. The protocol cannot be fooled this way, but a
person reading the terminal can.

The second is resources. The Crib has no memory, process, or CPU limit. A
runaway agent can use up everything Docker Desktop is given, though nothing
beyond it.

## Tests

```bash
python -m unittest
```

They need no Docker and no Ollama. A fake stands in for the model.

## Use of AI

Code and documentation are written in collaboration with remote and local AI; design
decisions, code review, semantic and auditory review, and final judgement stay human.

---

**East Van AI** · AI for the rest of us! · Vancouver, BC, Canada

[github.com/east-van-ai](https://github.com/east-van-ai) · <east-van-ai@proton.me>

Copyright (c) 2026 Go Nakamaru
