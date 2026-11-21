# chassis-cid

This is a blank chassis CID. Load your knowledge pack, it boots educated.

It is a downloadable D-RAYL package for a Windows or Linux PC. There is no language model in this zip. The chat box uses knowledge you load from your own notes or textbooks. It only runs on your computer (127.0.0.1). It does not copy or install itself.

You need Python 3.10 or newer. If Python is missing, get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" on Windows.

## How to use it

1. Download the zip from this repository's Releases page (or clone the repo) and unzip it.
2. Optionally, put your own `.txt` or `.md` files in the `textbooks` folder. Convert PDFs to text first. A short sample maths, English and general-facts file is already in there.
3. Build a knowledge pack. On Windows, double-click `Build-School-Pack.cmd`. On Linux or macOS, run `./build-school-pack.sh`. This marks the whole `textbooks` folder as trusted (`--trusted`), trains the sand pit offline, and writes `school-pack.zip`.
4. Start the chat box pointing at that pack. On Windows, double-click `Run-Chat.cmd`. On Linux or macOS, run `./run-chat.sh`. It opens in your browser at http://127.0.0.1:8790/ and loads `school-pack.zip` if it is next to these scripts.

You can also run the steps yourself:

```bash
python seed/scripts/school_pack.py textbooks --trusted --queries 20 --output school-pack.zip
python -m drayl_chat --brain school-pack.zip
```

(The second command is run from the `chatbox` folder.)

"Trusted" only means *you* vouched for the source. A wrong textbook would spread errors, so use it deliberately.

## What is in the folder

- `chassis/` — the P36 base layer (router, parallel dispatch, sand pit, memory, compliance gate).
- `chatbox/` — the P37 chat box, with P38 pack loading (`--brain` or `DRAYL_BRAIN`).
- `seed/` — the P38 school-mode tools, nursery script, and school-pack builder.
- `textbooks/` — sample notes (Pythagoras and other short originals). Replace or add your own.

## Tests

From a checkout, with pytest installed:

```bash
python -m pytest chassis/tests -q
python -m pytest chatbox/tests -q
python -m pytest seed/tests -q
```

There are no network calls. The chat box and seed tools stay on localhost.
