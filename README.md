# D-RAYL Chat Box v1 (P37)

A small, downloadable chat box for a Windows PC. It shows the plumbing of the D-RAYL design: you type a message, it is turned into a D-RAYL block, the router asks the sand pit and memory at the same time, the results are merged, and a reply appears on a black screen with aqua accents.

This is the chat infrastructure only. It is not Mate with the big SmolLM3-3B model, and it is not a robot or a Pi install.

**Honest limit:** there is no infer engine in this first version. Replies are composed from a handful of stored example grains, plus memory of what helped last time. That is enough to prove the path works on your computer, offline.

## What you need

- Windows 10 or 11 (a `run.sh` script is also included for Linux).
- Python 3.10 or newer. Nothing else to install to run the chat box.
- No internet once it is on your computer. It only listens on `127.0.0.1` (this computer).

## How to run it on Windows

1. Unzip the folder anywhere you like, for example onto the Desktop.
2. Double-click `Run-Chat.cmd`.
3. If Python is missing, the script will say so and point you to [python.org/downloads](https://www.python.org/downloads/). During setup, tick **Add python.exe to PATH**, then run `Run-Chat.cmd` again.
4. Your browser should open to `http://127.0.0.1:8790/`. If it does not, type that address yourself. If 8790 is busy, the next free port is used and printed in the window.
5. Type a message, for example `Hello`, `What is two plus two?`, or `What is D-RAYL?`
6. Leave the black command window open while you chat. Press Ctrl+C in that window, or close it, to stop.

On Linux or macOS:

```bash
chmod +x run.sh
./run.sh
```

## What happens when you send a message

1. **Encoder** turns your text into a D-RAYL block (tokens, capability tags, and symbol ids in a fixed 65,000-symbol space).
2. **Router** (the only part that talks to the base layer) peeks at memory for a quadrant hint, then fires the sand pit and memory **at the same time**.
3. **Sand pit** is a small in-memory vector store. It searches with hashed bag-of-words vectors and cosine similarity, starting in the most relevant quadrant.
4. **Memory** is a SQLite store of past questions and what helped.
5. **Merge** keeps the sand grains and the memory notes, and can boost a grain that helped last time.
6. **Decoder** turns the result into D-RAYL symbols and readable text.
7. **Infer** is a stub. It does not run a model. It writes an honest reply from the stored grains.
8. A **heat map** line is appended to `data/heatmap.jsonl` (which grains and quadrants were used).
9. A **candidate grain** for that exchange is written to `data/staging/` as a JSON file. It is **never** added to the sand pit automatically.

## Buttons and extra commands

- **Sleep** — a manual tidy-up. Grains that get used a lot are promoted to the hot tier. Unused ones are demoted to cold. Protected grains are not demoted. Sleep will not change anything until at least one chat has been logged. You can also run:

  `python -m drayl_chat --sleep`

- **Review staging** — lists candidate files and whether each one passes the compliance gate.
- **Promote passing** — moves only the candidates that pass the gate into the sand pit, then deletes those staging files. Failures stay in staging. Same thing from a command line:

  `python -m drayl_chat --promote`

The gate checks that a candidate is not empty, is not too short or too long, and is not a near-duplicate of a grain already in the sand pit.

Other useful flags:

```text
python -m drayl_chat --no-browser
python -m drayl_chat --port 8790
python -m drayl_chat --data-dir C:\path\to\data
```

## Safety rules in this box

- Binds to `127.0.0.1` only. It will refuse any other bind address.
- Makes no outbound internet calls.
- Does not copy, install, or launch other agents.
- Holds no secrets or API keys.
- Write-back never auto-promotes.

## Folder layout

```text
drayl-chatbox/
  Run-Chat.cmd          double-click this on Windows
  run.sh                Linux/macOS starter
  README.md             this file
  config/quadrants.json static quadrant map (numbers, grammar_speech, and a few more)
  config/seed_grains.json  the starter grains
  data/                 created and updated as you chat
    heatmap.jsonl
    memory.sqlite
    sandpit_state.json
    staging/            candidate grains, not yet in the sand pit
  drayl_chat/           the Python program
  tests/                pytest tests (pytest is only needed to run tests)
```

## Running the tests

Tests need pytest (the chat box itself does not):

```bash
python -m pip install pytest
python -m pytest -q
```

The tests check that both arms of the router really run in parallel (with a timing proof), that merge logic is correct, and that the gate, heat map, and sleep pass their basics. There is also a smoke test that starts the local server, posts a chat message, and checks that a reply comes back and that heat-map and staging files were written.

## What is stubbed, and known limits

- **No infer engine.** Replies are assembled from stored grains. An optional Ollama hook exists in the code as a placeholder and is disabled. It is never called.
- **Tiny sand pit.** Only a handful of example grains ship with v1. The “AI” will not know things that were not stored.
- **Hashed bag-of-words vectors**, not a real embedding model. Matches are rough, which is fine for plumbing.
- **Heat map / sleep** are simple count-based tools, not a full dual-map reconciler or distillation loop.
- **Symbol space** is a deterministic hash into 65,000 ids, not a trained vocabulary.
- P33 and P36 described a blade box, a Pi front end, speakers, mics, and a wake word. None of that is in this download. This is just the chat box.

Label: **D-RAYL Chat Box v1 (P37)**. P33–P36 were already used for earlier work, so this chat box is numbered P37.
