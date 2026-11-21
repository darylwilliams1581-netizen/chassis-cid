# D-RAYL base layer scaffold (P36)

This is a small, testable starting point for the D-RAYL base layer from the P33 design spec.

It is labelled **P36** because P33 and P34 are already used on this project (P33 was the offline-app prompt, P34 was the Mate Windows installer release). This folder is local only. Nothing here has been pushed to GitHub.

It is a scaffold, not the finished system. It does not run the language model, it does not talk on the network, and it does not install or copy any agents.

## What this is

D-RAYL is the shared vocabulary sitting above the weights and the sand pit. This pass only builds the **base layer** and a thin **router** that talks to it.

The base layer has two arms that the router fires at the same time:

- the **sand pit**, which stores grains of content and finds similar ones
- **memory**, which remembers what mattered last time a similar question was asked

The router then merges the two answers: grains are the raw content, memory is the context.

## What each piece is

- `config/quadrants.json` — the static quadrant map (numbers, grammar and speech, facts, procedures, personal, and miscellaneous). The router reads this at startup. Each quadrant has capability tags. If you change the boundaries later, you re-tag grains. You do not rebuild the index.
- `drayl/quadrants.py` — loads that config and matches incoming tags to a search order.
- `drayl/sandpit.py` — the sand pit interface and a simple in-memory stub. A grain has an id, text, a vector, a quadrant tag, and heat stats (how often it was hit, when it was last used, and whether it helped). Search can be told which quadrant to look in first. Retag only changes the tag.
- `drayl/memory.py` — the memory interface and stub. One program, two stores: a fast context store and a slower retrieval store, searched on two threads. Memory can bias which quadrant the sand pit searches first. It also holds links between grains and its own usage heat for those links.
- `drayl/router.py` — a thin dispatcher. This is the only program that knows the base layer exists. It matches a block to capability tags, asks memory for a quadrant bias, then runs the sand pit and memory at the same time and merges the results.
- `drayl/gate.py` — the compliance gate for write-back. A new candidate is checked for unique coverage (no near-duplicate, using a cosine threshold). If it passes, it is promoted into the sand pit. If it fails, it is written to a staging file (`near-misses.jsonl`) for later use. A third check, "does this improve later retrieval?", is named but not built yet.
- `drayl/embeddings.py` — a tiny, deterministic bag-of-words vector. No model is downloaded. The same text always gives the same vector.
- `drayl/stubs.py` — placeholders for work that is specified but not built in this pass (see below).

The encoder, decoder, infer core, and interface (screen, mic, speaker) are not in this scaffold. They sit above this layer and come later.

## How to run the tests

You only need Python 3.10+ and pytest. There are no other dependencies, and the tests make no network calls.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pytest
pytest -q
```

You should see every test pass.

## What is stubbed (not built yet)

These have an interface and a clear "not implemented" error only:

- **Sleep mode** — later this will compare the sand-pit heat map with memory, promote hot grains, evict cold ones, and merge near-duplicates. Memory will protect important but rarely used grains. The heat map will help catch grains that look relevant but make answers worse.
- **Dual map reconciliation** — there are two maps: the designed quadrant map, and a lived heat map from real use. When they disagree, the lived map probably knows something. This pass does not yet decide what to do about that.
- **Frequency-routed weights** — later, stable background weights would use cheap integer math, and sharp detail weights would use full floats. That path assignment is not built.

Also pending inside the gate: the check that a new grain "improves later retrieval". Unique grains are still promoted. The check is recorded as pending so it is not forgotten.

## A design note about memory and the router

The P33 spec lists memory with the six programs above the base layer, and also says memory sits *inside* the base layer as one of the two parallel arms. This scaffold follows the second line: memory is inside the base layer. The router is the only program above the layer that talks to it.

There is a second tension: the router should fire the sand pit and memory at the same time, but memory should also be able to tell the sand pit which quadrant to search first. This pass peeks at the fast memory store (no delay) to get that bias, then runs the full sand search and the full memory recall in parallel.

## Rules kept in this folder

- Local only. No sockets, no listeners, no network calls.
- No code that copies, installs, or launches other agents.
- No secrets.
- Not pushed to GitHub, and Daryl's repo was not touched.
