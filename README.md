# Kiosk / Crib / Ollama PoC

![CI](https://github.com/east-van-ai/kiosk-crib-ollama-poc/actions/workflows/ci.yaml/badge.svg)
[![M8ven Score](https://m8ven.ai/badge/mcp/east-van-ai/kiosk-crib-ollama-poc)](https://m8ven.ai/mcp/east-van-ai/kiosk-crib-ollama-poc?s=readme&variant=verified)

A small proof of concept: give an AI agent useful capabilities without giving
it direct access to them.

The basic idea is simple:

**The Kiosk is the trusted side. The Agent is not. The Crib walls the Agent
in.**

The Agent runs inside the Crib, a container with no network wherever its
flavour allows it. The Kiosk runs natively on macOS and talks to the Agent
with **MCP over standard input and output**, the only way in or out.

Ollama is separate from both. It serves inference to the Kiosk and is not part
of the Agent's execution environment. The Agent never holds the model, its
address, or even its name.

The Crib comes in three flavours: a Docker container, an Apple container,
and an Apple container machine. Everything else stays the same, so the
flavours can be compared side by side.

This is not an attempt to make the Agent trustworthy. The point is to see how
much trust can be removed from the Agent, by putting its capabilities behind a
narrow interface and an isolation boundary.

The arrangement protects the secrets in the settings, such as keys,
addresses, and the model's name. Secrets in the conversation pass through
the Agent, so they are out of scope.

## Reading this page

Run the tests. They need nothing installed, and they come first.

Start with the boundary. The Agent is untrusted. The Crib contains it. The
Kiosk remains trusted.

Follow a request. The Agent asks through MCP over stdin/stdout. The Kiosk
decides whether the request is allowed and, if so, asks Ollama for
inference.

See what crosses the boundary. The Agent gets the capability it was
offered, but not the model, its address, or the credentials behind the
capability.

Run the PoC. The greetings show who is involved. The MCP trace shows what
actually passes between them.

Change the Crib. Run the same Agent and Kiosk with Docker, Apple container,
or container machine.

Try the boundary. See what each flavour prevents, what it leaves reachable,
and where the containment stops.

Weigh it against a real escape. The threat model sets the Crib beside one.

Clean up. Remove the images, and the container machine that keeps running.

Read further. Related work and sources close the page.

The question throughout: how much trust can be removed from the Agent?

## Tests

Before the long read, something to run. Clone the repo and type:

```bash
python -m unittest
```

```text
Ran 80 tests in 0.129s

OK (skipped=2)
```

There is nothing to install first. No virtual environment, no `pip`, no
container runtime, and no Ollama. The PoC uses only Python's standard
library, and any Python from 3.9 up runs the tests. A fake stands in for the
model.

The two skipped are the Crib's own tests, one set per container flavour.
`tests/test_crib_security.py` tests the Crib, not the code. It starts real
containers with the Kiosk's own launch line and tries to break out: reach
the internet, resolve a name, write a file, fork without end, fill the
memory, hog the CPU, and hang until stopped. Each set wakes up once its
runtime is running and its image is built, as Run it shows.

## Architecture

```text
                         macOS
┌─────────────────────────────────────────────────────────┐
│                                                         │
│   Kiosk                              Ollama             │
│   native macOS process ──────────▶  inference           │
│       △                 ask_ai,        └── Tiny Aya     │
│       │                 if allowed                      │
│       │                                                 │
│       │ MCP over stdin/stdout                           │
│       ▼                                                 │
│   ┌──────────────────────────────┐                      │
│   │ Crib                         │  no network          │
│   │                              │  no model            │
│   │      Agent - untrusted       │  no credentials      │
│   │                              │                      │
│   └──────────────────────────────┘                      │
│     Docker, Apple container, or container machine       │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Terms

- **Kiosk.** The trusted process on the Mac. It starts the Crib, talks to the
  Agent, and decides what the Agent may do. It is small enough to read in one
  sitting, and it is not an AI agent.
- **Crib.** The isolation boundary around the Agent. It is a container,
  made in one of three flavours, with no model installed. The Agent lives
  inside the Crib, but the Crib is not the Agent.
- **Flavour.** How a Crib is made: `docker`, `apple-container`, or
  `container-machine`. One run uses one flavour, picked with `CRIB`.
- **Agent.** The potentially untrusted AI application inside the Crib. It
  receives model output and can request MCP operations, but it never holds
  the capabilities behind them.
- **Inference Service.** What runs the model. Here it is Ollama on the Mac,
  outside the Crib.
- **MCP.** The Model Context Protocol, spoken over the Agent's standard input
  and output. No network service is exposed. The Agent's `stdout` carries MCP
  messages and nothing else. Its kitchen talk goes to `stderr`.
- **Capability.** Something the Agent may ask the Kiosk to do. The only one is
  `ask_ai`, which answers a prompt with the Kiosk's model.

## Why this arrangement?

The interesting question is not:

> Can the Agent be made trustworthy?

It is:

> **Can trusting the Agent be made unnecessary?**

The Agent may be clever, autonomous, and hard to trust. Its code may have bugs,
its dependencies may have holes, and its prompts may be manipulated. The PoC
keeps the Agent disposable and puts its capabilities on the other side of a
boundary.

If the Agent is compromised, misbehaves, or simply does something nobody
expected, what can it actually reach? That is what this small experiment sets
out to find.

## What the Kiosk decides

- Which capabilities exist. The Agent asks for the list and gets `ask_ai`.
- Whether a request is well formed. A missing prompt, an unknown argument, or
  a temperature out of range is refused before any model sees it.
- How long a run may go on. Twenty requests or five minutes, whichever
  comes first. A carbonara takes eight requests and half a minute. Past
  either, the Kiosk stops the Crib.
- How much of the Mac the Crib may use: its memory, its CPU time, and its
  number of processes.
- Which model answers, and where it runs. The Agent never knows.
- Which settings reach the Crib. Three named variables pass. Nothing else from
  the Mac's environment does.

## The files

- `kiosk.py` is the Kiosk: the policy and the greeting. Run it, and it
  starts everything else.
- `crib_docker.py`, `crib_apple_container.py`, and
  `crib_container_machine.py` each make one flavour of Crib. Each holds its
  launch command and what that command confines.
- `mcp_server.py` speaks MCP for the Kiosk. It knows the protocol and no tool.
- `mcp_client.py` speaks MCP for the Agent. It knows the protocol and no tool.
- `backend_ollama.py` is how the Kiosk reaches Ollama. Another backend, such
  as a Cohere API call, would sit beside it.
- `carbonara.py` is the Agent: the kitchen, with one capability, `ask_ai`.

Only `carbonara.py` and `mcp_client.py` go into the Crib.

## Requirements

- An Apple Silicon Mac. No separate Linux install is needed, since each
  container runtime brings its own.
- [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/),
  running, for the `docker` flavour.
- Apple's [container](https://github.com/apple/container) 1.5.0 or later, on
  macOS 26, for the two Apple flavours.
- [Ollama for macOS](https://ollama.com), running.
- Cohere's Tiny Aya Global model, pulled into Ollama.
- Python 3.9 or later on the Mac. The Kiosk and the Agent use only the
  standard library, so there is nothing to install.

Pull the model once:

```bash
ollama pull hf.co/CohereLabs/tiny-aya-global-GGUF:Q4_K_M
```

## Run it

Build the Crib once, with the runtime you use:

```bash
docker build -t kiosk-crib-docker .
```

Apple container works through a background service. Start it first, or
the Crib fails to start with `XPC connection error`:

```bash
container system start
```

```bash
container build -t kiosk-crib-apple-container .
```

Then start the Kiosk, which starts everything else. Docker is the default:

```bash
CARBONARA_ORDER="make me a carbonara for four" python kiosk.py
```

Every trip to the model passes through the Kiosk. Trimmed:

```text
[KIOSK] Hello! I am Kiosk. The Agent app, a kitchen in the Crib, asks me, and I ask the model on its behalf.
[KIOSK] I can pass CARBONARA_ORDER, CARBONARA_TEMPERATURE, CARBONARA_STREAM_DELAY into the Crib. Passing now: CARBONARA_ORDER
[KIOSK] Crib: kiosk-crib-docker on Docker 29.8.0, removed on exit, no network, read-only, no capabilities, no new privileges, runs as nobody
[KIOSK] limits: 256 MB of memory, 1 CPU, at most 32 processes
[KIOSK] command: docker run --rm -i --init --name kiosk-crib-run-4711 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user nobody --memory 256m --cpus 1 --ulimit nproc=32 -e CARBONARA_ORDER -e CARBONARA_TEMPERATURE -e CARBONARA_STREAM_DELAY kiosk-crib-docker
[KIOSK] model: hf.co/CohereLabs/tiny-aya-global-GGUF:Q4_K_M, served by Ollama at http://127.0.0.1:11434
[KIOSK] stops: after 20 ask_ai calls, after 300 seconds, on any message that is not MCP
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
built from the flags the Kiosk passes to the runtime, so dropping a flag
drops its words. The limits line is built the same way, and the runtime
reports its own version. The command line prints the command itself, the
very one the Kiosk runs, so the flags and their words sit side by side.
Each setting crosses by name only, so its value never shows. The Expeditor
names the tools the Kiosk actually offered.

Each side speaks only for what it controls. The Kiosk names the model and
the Crib's confinement. The kitchen names its crew and cannot name the
model, because it never learns it. The model does not introduce itself. A
small model's account of itself is unreliable, and the greeting is there to
tell the truth.

## Watch the wire

The greeting shows who is involved. The trace shows what passes between
them.

- `MCP_TRACE=1` prints every MCP message in both directions, as formatted
  JSON, in light grey.

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
the Agent still shows up just before the Kiosk stops the Crib.

## Three Cribs

One Agent, one image recipe, one Kiosk. Only the way the Crib is made
changes, so any difference between runs comes from the flavour.

| | Docker | Apple container | Container machine |
| --- | --- | --- | --- |
| Lifetime | disposable | disposable | persistent |
| Virtual machine | one, shared | one per container | one per machine |
| Network | none | none | on, no way to drop it |
| Read-only filesystem | yes | yes | no option |
| Capabilities | all dropped | all dropped | none, as a plain user |
| No new privileges | yes | no option | no |
| Runs as | nobody | nobody | your Mac user |
| Your SSH agent | out of reach | out of reach | reachable |
| Your home folder | not mounted | not mounted | mounted read-write by default |
| Memory and CPU | capped per run | capped per run | set when the machine is made |
| Process count | capped, shared with other containers | capped | no cap that holds |
| How the Kiosk stops it | kills the container | kills the container | stops the whole machine |

Pick a flavour with `CRIB`. Each greeting says what its Crib is:

```text
[KIOSK] Crib: kiosk-crib-docker on Docker 29.8.0, removed on exit, no network, read-only, no capabilities, no new privileges, runs as nobody
[KIOSK] limits: 256 MB of memory, 1 CPU, at most 32 processes
```

```text
[KIOSK] Crib: kiosk-crib-apple-container on Apple container 1.5.0, removed on exit, no network, read-only, no capabilities, runs as nobody
[KIOSK] limits: 256 MB of memory, 1 CPU, at most 32 processes
```

```text
[KIOSK] Crib: kiosk-crib-container-machine on container machine 1.5.0, no home folder
[KIOSK] by design: persistent, network on, runs as your Mac user, your SSH agent reachable
[KIOSK] limits: 1024 MB of memory, 4 CPUs
```

Apple container has no option for "no new privileges", so its line leaves
it out. A container machine has no confinement flags at all. Its
`by design` line lists what the runtime always does, as Apple documents it.
[Apple warns](https://github.com/apple/container/blob/main/docs/container-machine.md)
that a container machine "trades away much of the isolation"
of a regular container. It is here to show that, not to fix it.

The two containers take the same three limits, so they can be compared.
The process cap is `--ulimit nproc`, which both runtimes have. Docker's own
`--pids-limit` is left out, because Apple container has no such flag. The
cap counts per user. Docker's containers share one VM, so another container
running as nobody uses up the same count. An Apple container has its VM to
itself. A container machine accepts the flag and ignores it.

The Kiosk stops a container by name, with the runtime's own `kill`. A
signal to the command that launched it is not enough: `container` 1.5.0
answers one with an error and keeps the container running. A container
machine has no name for one run, so the Kiosk stops the whole machine, and
the next run boots it again.

A container machine is a whole Linux VM, much like one made in
[UTM](https://mac.getutm.app). The difference is where it comes from: a
container image, built from the same recipe as the other two Cribs, rather
than an installed operating system.

A container machine lives across runs, so it is set up once, by hand. Its
image adds the `/sbin/init` a machine boots, on top of
`kiosk-crib-apple-container`:

```bash
container build -f Dockerfile.machine -t kiosk-crib-container-machine .
```

```bash
container machine create --name kiosk-crib --home-mount none --memory 1G kiosk-crib-container-machine
```

```bash
CRIB=container-machine python kiosk.py
```

`--home-mount none` keeps your home folder out. The Kiosk asks the machine
before it claims "no home folder".

In container 1.5.0, the first command in a new machine fails with
`Inappropriate ioctl for device`. Run it again, and it cooks.

## Tuning

Three settings pass into the Crib, for the Agent:

- `CARBONARA_ORDER` is the order the kitchen takes.
- `CARBONARA_TEMPERATURE` sets how adventurous the model is. The Kiosk
  refuses anything outside 0 to 2.
- `CARBONARA_STREAM_DELAY` paces the printed reasoning, for drama.

Two stay with the Kiosk, because they say where inference comes from:

- `CARBONARA_MODEL` names the model the Kiosk asks for.
- `OLLAMA_HOST` is where Ollama answers.

Swap the model, and the Agent does not notice:

```bash
CARBONARA_MODEL=llama3.2:latest python kiosk.py
```

## Try the boundary

The Agent alone cannot cook. With no Kiosk on the other end, its first
request goes unanswered and it exits:

```bash
docker run --rm -i kiosk-crib-docker < /dev/null
```

Two probes test the Crib from inside. The first goes around the Kiosk,
straight to Ollama. The second reaches for the internet.

### Docker network

Docker's `--network` is `none` or `bridge`. The Kiosk uses `none`, and the
probe fails. Swap in `bridge`, Docker's default, and it reaches Ollama:

```bash
OLLAMA="import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434', timeout=5).read().decode())"
```

```bash
docker run --rm --network none --entrypoint python kiosk-crib-docker -c "$OLLAMA"
```

### Apple container network

Apple container's `--network` is `none` or `default`. The Kiosk uses
`none`, and the probe fails. Swap in `default`, and the internet answers:

```bash
INTERNET="import socket; socket.create_connection(('1.1.1.1', 53), timeout=5); print('connected')"
```

```bash
container run --rm --network none --entrypoint python kiosk-crib-apple-container -c "$INTERNET"
```

### Container machine network

A container machine has no `--network`. Its network is always on:

```bash
echo "$INTERNET" | container machine run -i -n kiosk-crib python -
```

### What each probe gets

| Crib | `--network` | `$OLLAMA` | `$INTERNET` |
| --- | --- | --- | --- |
| Docker | `none` | fails | fails |
| Docker | `bridge` | `Ollama is running` | `connected` |
| Apple container | `none` | fails | fails |
| Apple container | `default` | fails | `connected` |
| Container machine | always on | fails | `connected` |

With the network off, the Agent reaches nothing. With it on, Docker hands
the Agent the model: `host.docker.internal` leads to the Mac, where Ollama
listens. Apple's runtime has no such name. Ollama listens only on the Mac
itself by default, so it is out of reach there. That is a default, not a
wall. An Ollama open to the local network would answer.

A container is a containment layer, not a guarantee. The boundary also
rests on the runtime, macOS, and what the Kiosk chooses to offer.

One thing sits outside the boundary: the screen. The Agent's `stderr`
reaches the terminal as it is, beside the Kiosk's own lines. The Agent, or
a model reply it prints, can write a `[KIOSK]` line identical to a real
one, colour included. The protocol cannot be fooled this way, but a person
reading the terminal can.

## Threat model

In September 2026, OpenAI paused training of its most capable models after
an agent escaped its sandbox. The sandbox filtered the network, and the
agent found a gap: DNS queries that reached a public chatbot. An alert
fired, but nothing stopped the run. It was ended more than two hours
later.

Here is how the Crib answers each part.

- **A filtered network has gaps.** In the two container flavours, the Crib
  has no network to filter. With `--network none`, there is no DNS to tunnel
  through. The only way out is MCP over standard streams, and the Kiosk
  reads every message.
- **A goal-driven agent looks for a way around.** The Crib holds nothing
  worth finding: no model, no model address, no credentials. Anything useful
  sits on the Kiosk's side, behind a capability.
- **An allowed channel can carry more than intended.** DNS was allowed, and
  it became a tunnel. Every capability is a channel like that. `ask_ai`
  sends the Agent's words to a model. Today the model is local. With a
  remote backend such as Cohere, whatever the Agent puts in a prompt leaves
  the Mac.
- **A stop that does not fire is not a stop.** The Kiosk stops the Crib when
  the budget runs out, when a message is not MCP, or when the time limit
  passes. It stops the Crib by name, because a signal alone left an Apple
  container running. Memory, CPU, and process limits cap the rest. Tests
  prove each one in a real Crib.

## Clean up

The PoC leaves an image in each runtime you used, and a container machine
if you made one. The machine keeps running after the Kiosk exits.

See what is there:

```bash
docker images | grep kiosk
```

```bash
container image list | grep kiosk
```

Stop and delete the machine:

```bash
container machine stop kiosk-crib
```

```bash
container machine delete kiosk-crib
```

`container build` leaves its build VM running too. Stop it:

```bash
container builder stop
```

Remove the images:

```bash
docker rmi kiosk-crib-docker
```

```bash
container image delete kiosk-crib-apple-container kiosk-crib-container-machine
```

Last, stop Apple container's service. The commands above talk to it, so
it goes after them:

```bash
container system stop
```

## References

- Apple's
  [container machine documentation](https://github.com/apple/container/blob/main/docs/container-machine.md),
  the source of the warning quoted in Three Cribs.
- OpenAI,
  [An agent used DNS to reach an external chatbot](https://alignment.openai.com/misalignment-reports/an-agent-used-dns-to-reach-an-external-chatbot/),
  September 2026. The report behind the escape described in Threat model.
- Mark Watson,
  [Running OpenCode (and Other) Agentic Coding Tools in Apple Containers](https://marklwatson.substack.com/p/running-opencode-and-other-agentic),
  June 2026. A practical setup for coding agents in an Apple container
  machine, the third flavour here.
- [Tupper](https://github.com/lightbearco/tupper), sandboxes for AI agents on
  Apple containers. It takes the reverse arrangement: the agent stays on the
  host, and only the code it runs goes into the box.
- [awesome-agent-sandbox](https://github.com/fishman/awesome-agent-sandbox),
  a list of sandboxes for AI coding agents, for the wider landscape.

## Use of AI

Code and documentation are written in collaboration with remote and local AI; design
decisions, code review, semantic and auditory review, and final judgement stay human.

---

**East Van AI** · AI for the rest of us! · Vancouver, BC, Canada

[github.com/east-van-ai](https://github.com/east-van-ai) · <east-van-ai@proton.me>

Copyright (c) 2026 Go Nakamaru
