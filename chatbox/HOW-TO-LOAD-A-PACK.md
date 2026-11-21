# How to load a school pack

One line, as on the pack itself: unzip on target PC, point the chat box at this folder, done.

1. Copy the school pack zip to the PC (USB, shared drive, whatever you use). No internet is needed.
2. Unzip it. You should see `manifest.json`, `sandpit.json`, `heat.json`, `memory.sqlite` and a `config` folder.
3. From this chat box folder, run:

       python -m drayl_chat --brain C:\path\to\unzipped-pack

   Or set `DRAYL_BRAIN` to that folder and double-click `Run-Chat.cmd`.

`--brain` and `DRAYL_BRAIN` are the adapter. They tell the chat box to use the
pack's grains, heat map, memory and quadrant map instead of the starter grains.

This is a copy of the P37 chat box. Do not need to edit the original P37 tree.
