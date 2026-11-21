# D-RAYL school mode and nursery (P38)

This is the P36 base layer, plus **school mode** (trusted source tags), a **nursery** script, and an offline **school pack** builder.

It is labelled **P38** because earlier numbers are already used (P33 offline-app prompt, P34 installer, P36 base layer, P37 chat box). This folder is local only. Nothing here has been pushed to GitHub, and the original P36 folder was not changed.

It is a scaffold, not the finished system. It does not run the language model, and it does not talk on the network.

## School mode (ground truth tagging)

You can load a document, such as a year nine maths book you have converted to text, and mark it as a trusted source. Every grain made from that document inherits the tag.

- Trusted grains skip the "does this improve later retrieval?" proving period. The source itself is the proof. The gate still rejects empty text and an exact copy of an already-trusted grain.
- During sleep, a trusted grain can override an untrusted near-duplicate **in the same quadrant**. The old grain is marked `superseded_by=<trusted id>` and moved to staging. It is never silently deleted. Untrusted grains never override trusted ones, and conflicts across quadrants are left alone.
- When retrieval returns both, a trusted grain wins a tie or a near-tie (within a small margin, default 0.05 cosine). A clearly better untrusted match still wins.
- The tag is stored with the grain, next to its vector, quadrant, heat and source (`name` + sha256 of the document). You can query `sandpit.query(trusted=True, quadrant=..., source=...)`.

**Important:** "Trusted" only means *you* vouched for the source. A wrong textbook would spread errors, so use school mode deliberately.

## Nursery script

From this folder, with Python 3.10 or newer (stdlib only plus pytest for tests):

```bash
python scripts/nursery.py school samples/year9-maths-sample.md
python scripts/nursery.py flood --queries 20
python scripts/nursery.py sleep
```

- `seed <files...>` — chunk `.txt` / `.md` files into untrusted grains and assign a quadrant from the map.
- `school <file>` — one-step load of a fundamental document with `trusted=True`.
- `flood --queries N` — fire synthetic queries (built from the seeded text, deterministic seed) through the router so the heat map grows. The sand pit is saved to a local JSON file so runs accumulate.
- `sleep` — trusted grains override untrusted near-duplicates in the same quadrant.
- `show` — print a short summary.

The sample file `samples/year9-maths-sample.md` is original short notes (Pythagoras, order of operations, directed numbers). It is not copied from any textbook.

PDFs are not read directly. Convert them to `.txt` or `.md` first, then seed or pack the text files.

## School pack (send it to school before it starts work)

Build a warmed-up brain on any machine, including a Windows PC, *before* the chat box is installed on the target computer. Then copy the zip across.

```bash
python scripts/school_pack.py samples --trust-list samples/trust.json --queries 20 --output school-pack.zip
```

Or:

```bash
python scripts/nursery.py pack samples --trusted --queries 20 --output school-pack.zip
```

- The folder is searched recursively for `.txt` and `.md` files.
- `--trusted` marks the whole folder as trusted. You can also use `--trusted-file notes.md` more than once, or a `trust.json` file mapping a file name to `true` or `false`.
- A synthetic flood then runs sample queries for maths (`numbers`), grammar (`grammar_speech`) and general (`facts` and similar) from whatever content landed in those quadrants.
- The zip contains the sand pit, heat map, quadrant config, memory store, `manifest.json` (every file loaded, its sha256, grain counts, quadrant counts, trust level per document, build time and tool version), and a one-line README: *Unzip on target PC, point the chat box at this folder, done.*

Zip extraction refuses any path that would write outside the destination folder.

## Point the chat box at the pack

The original P37 chat box was not edited. This repo includes a copy at `chatbox-pack-support` that can load a pack:

```bash
python -m drayl_chat --brain path/to/unzipped-pack
```

On Windows you can set `DRAYL_BRAIN` to that folder and double-click `Run-Chat.cmd`. See `chatbox-pack-support/HOW-TO-LOAD-A-PACK.md`.

## How to run the tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pytest
pytest -q
```

On Windows, use `python -m venv .venv` then `.venv\Scripts\activate`. You should see every test pass. There are no network calls.

## What is still stubbed

- **Dual map reconciliation** — the designed quadrant map vs the lived heat map.
- **Frequency-routed weights** — integer vs float math paths.

Sleep mode has a minimal school-mode pass only. The broader plan (hot/cold tiers, memory-protected cold grains, catching grains that poison answers) is not built yet.

## Design notes

- Memory sits *inside* the base layer as one of the two arms. The router is the only program above the layer that talks to it.
- The router peeks at the fast memory store for a quadrant bias, then fires the full sand search and the full memory recall together.
- Trusted near-duplicates of untrusted grains are still promoted. Sleep is what later overrides the untrusted copy, in the same quadrant only.
- The default trust margin is 0.05 cosine. It is configurable on the router (`trust_margin`).
