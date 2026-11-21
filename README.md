# D-RAYL teacher arm (P39)

This is the P36 base layer and P38 school mode, plus a **teacher arm**: a small service that sits beside the router so an external AI teacher (Grok, ChatGPT, Codex, Copilot, Gemini) can inject grains straight into the sand pit.

It is labelled **P39** because earlier numbers are already used (P33 offline-app prompt, P34 installer, P36 base layer, P37 chat box, P38 school mode). Nothing here is pushed until you say so, and the earlier P36 / P38 folders were not changed.

It is a scaffold, not the finished system. It does not run the language model.

## What the teacher arm does

You run a teacher yourself, on your own computer, and you paste or bridge the call locally. **This service never calls out to any AI.** It only listens on `127.0.0.1` (your machine). It will refuse to bind to any other address, including `0.0.0.0`.

Each injection is a D-RAYL block with:

- the concept text
- a quadrant tag (for example `numbers`)
- an optional trusted flag
- a source `teacher_id` (for example `grok` or `codex`)

Every grain still goes through the existing compliance gate. Empty text is rejected. Near-duplicates are staged, not promoted.

## Trust (school mode for teachers)

A teacher may *ask* for the trusted flag, but the flag only sticks if you have put that teacher's ID on a local allowlist: `config/trusted_teachers.json`. That file ships empty. If a teacher is not on the list, the grain is stored as untrusted and the record says `trust requested, not granted`. The grain always keeps `teacher_id` and this trust provenance, so you can see who asked and whether you granted it.

Without that rule, any AI could mark its own grains as ground truth.

## Disagreements

When two different teachers emit grains about the same concept that contradict each other (different numbers, opposite negation, or clearly conflicting wording), **both grains are staged**, a conflict record is written, and **nothing is auto-resolved**. Neither grain stays live in the sand pit until you, or a higher-trusted source, break the tie.

You resolve a conflict with:

```bash
python scripts/teacher.py resolve CONFLICT_ID --choose GRAIN_ID
```

A later trusted source can also break the tie: an allowlisted teacher injecting a trusted grain, or a school-mode trusted document (a textbook you vouched for) loaded on the same concept. Untrusted teachers cannot.

## Throttle

Injections are limited so volume can scale with the machine:

- a maximum batch size
- a per-teacher token-bucket rate limit
- a bounded backing queue used when the pit is busy (sleep mode running, or an index rebuild)

When the pit is busy, the arm returns status `queued` (HTTP 429) and drains the queue once the pit is free. If the queue is full, the request is rejected with status `queue_full`. Limits live in `config/teacher_limits.json`. Raise them on a bigger box.

## Auth token

On first run the server writes a token to `data/teacher_token` (this folder is gitignored; the token is never committed). Every inject request must send that token in the `X-DRAYL-Token` header, or as `Authorization: Bearer …`.

## How to run it

Python 3.10 or newer, stdlib only (pytest is only for tests).

```bash
python scripts/teacher.py serve --port 8765
```

Then, from the same computer, POST a D-RAYL block to `http://127.0.0.1:8765/inject`:

```json
{
  "teacher_id": "grok",
  "trusted": false,
  "block": {
    "text": "The product of two negative numbers is positive.",
    "tags": ["math"],
    "quadrant": "numbers",
    "concept_key": "product of two negative numbers"
  }
}
```

A batch is a `blocks` (or `injections`) list, with a `teacher_id` at the top.

You can also call it in Python without HTTP:

```python
from drayl.teacher import TeacherArm
result = arm.inject(
    text="The product of two negative numbers is positive.",
    quadrant="numbers",
    teacher_id="grok",
    trusted=False,
)
```

List open conflicts with `python scripts/teacher.py conflicts`.

## Saving lessons (durable store)

A lesson is only reported as saved once it is on disk. Every accepted change (a promoted grain, a staged near-miss, a conflict opened or resolved, and the audit line for it) is written to `data/drayl.sqlite3` in one SQLite transaction, and that transaction is committed **before** the teacher gets HTTP 200 (or 409 for a staged conflict). Responses carry `"saved": true` and a `store_generation` number.

- **Killed mid-way?** SQLite's journal rolls back anything half-written, so you restart to the last committed lesson: never a half-saved one.
- **Two teachers at once?** Writes queue on SQLite's write lock (`BEGIN IMMEDIATE`). Two processes (the server and the `resolve` command, say) can share the file: each one reloads if the other committed first, so neither overwrites the other's work.
- **Save failed** (disk full, file locked)? You get HTTP **503** with `"status": "not_saved"` and `"saved": false`, the in-memory pit is put back to the last saved state, and nothing already stored is touched.
- **Nothing is deleted.** The database refuses DELETE on grains, conflicts, staging and audit rows. Superseded and parked grains keep their rows.
- **Older `data/sandpit.json` and `data/conflicts.jsonl`** are imported on start (insert-if-missing only; a stored grain is never overwritten) and the files are left as they are.
- **Ctrl+C** still writes a JSON snapshot to `--data`, now via a unique temp file and an atomic rename. It is an extra safeguard, not the record.

Look up one stored grain and its audit trail:

```bash
python scripts/teacher.py show GRAIN_ID
```

This changes *when* things are saved, not *what* is decided: trust, approval and conflict rules are the same as before.

## How to run the tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pytest
pytest -q
```

On Windows, use `python -m venv .venv` then `.venv\Scripts\activate`. You should see every test pass, including the older P38 tests. There are no outbound network calls. The HTTP tests only talk to `127.0.0.1`.

## School mode and the nursery (from P38)

You can still load a document, such as a year nine maths book you have converted to text, and mark it as a trusted source. Every grain made from that document inherits the tag.

- Trusted grains skip the "does this improve later retrieval?" proving period. The source itself is the proof. The gate still rejects empty text and an exact copy of an already-trusted grain.
- During sleep, a trusted grain can override an untrusted near-duplicate **in the same quadrant**. The old grain is marked `superseded_by=<trusted id>` and moved to staging. It is never silently deleted. Untrusted grains never override trusted ones, and conflicts across quadrants are left alone.
- When retrieval returns both, a trusted grain wins a tie or a near-tie (within a small margin, default 0.05 cosine). A clearly better untrusted match still wins.
- The tag is stored with the grain, next to its vector, quadrant, heat and source (`name` + sha256 of the document). You can query `sandpit.query(trusted=True, quadrant=..., source=...)`.

**Important:** "Trusted" only means *you* vouched for the source. A wrong textbook would spread errors, so use school mode deliberately.

From this folder:

```bash
python scripts/nursery.py school samples/year9-maths-sample.md
python scripts/nursery.py flood --queries 20
python scripts/nursery.py sleep
```

- `seed <files...>` — chunk `.txt` / `.md` files into untrusted grains and assign a quadrant from the map.
- `school <file>` — one-step load of a fundamental document with `trusted=True`.
- `flood --queries N` — fire synthetic queries through the router so the heat map grows.
- `sleep` — trusted grains override untrusted near-duplicates in the same quadrant.
- `show` — print a short summary.

The sample file `samples/year9-maths-sample.md` is original short notes (Pythagoras, order of operations, directed numbers). It is not copied from any textbook.

PDFs are not read directly. Convert them to `.txt` or `.md` first, then seed or pack the text files.

## School pack (send it to school before it starts work)

```bash
python scripts/school_pack.py samples --trust-list samples/trust.json --queries 20 --output school-pack.zip
```

The zip contains the sand pit, heat map, quadrant config, memory store, a manifest, and a one-line README: *Unzip on target PC, point the chat box at this folder, done.*

The original P37 chat box was not edited. This repo includes a copy at `chatbox-pack-support` that can load a pack:

```bash
python -m drayl_chat --brain path/to/unzipped-pack
```

On Windows you can set `DRAYL_BRAIN` to that folder and double-click `Run-Chat.cmd`. See `chatbox-pack-support/HOW-TO-LOAD-A-PACK.md`.

## What is still stubbed

- **Dual map reconciliation** — the designed quadrant map vs the lived heat map.
- **Frequency-routed weights** — integer vs float math paths.

Sleep mode has a minimal school-mode pass only. The broader plan (hot/cold tiers, memory-protected cold grains, catching grains that poison answers) is not built yet.

## Design notes

- Memory sits *inside* the base layer as one of the two arms. The router is the only program above the layer that talks to it.
- The router peeks at the fast memory store for a quadrant bias, then fires the full sand search and the full memory recall together.
- The teacher arm sits *beside* the router. It does not replace it. Teachers call in; the arm never calls out.
- A teacher-requested trusted flag is honoured only if that `teacher_id` is on your local allowlist. Otherwise it is downgraded and recorded as `trust requested, not granted`.
- When two teachers contradict each other, both grains leave the active pit and wait in staging. A human CLI command, an allowlisted teacher, or a school-mode trusted grain can break the tie later. Untrusted teachers cannot.
- Same-concept disagreements are detected by a high cosine score or a shared concept key, plus a simple contradiction check (different numbers, opposite negation, or conflicting wording). Paraphrases of the *same* fact that share a concept key but use very different words may also be staged for a human to check. That is deliberate: the arm does not auto-resolve.
- The default trust margin is 0.05 cosine. It is configurable on the router (`trust_margin`).
- Trusted near-duplicates of untrusted grains are still promoted. Sleep is what later overrides the untrusted copy, in the same quadrant only.
