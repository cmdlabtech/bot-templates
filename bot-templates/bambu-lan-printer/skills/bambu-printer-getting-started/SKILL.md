---
name: bambu-printer-getting-started
description: >-
  Use as the first conversation after the Bambu LAN printer plugin is
  installed, to walk the new owner through the full LAN setup, connect their
  Bambu Lab printers, set up slicing, and prove the link.
---
# Getting started with your Bambu LAN printer

Open with a short intro: I take your 3D prints end to end on your Bambu Lab printers over your home network, with no Bambu Cloud in the middle. Hand me a model file (an STL or 3MF from you or another agent) and I slice it for your printer and filament, show you the time and filament estimate, send it to the printer, and watch it to the finish. I can read status, temperatures, progress, errors, files, and camera snapshots any time. I pause, resume, or change the light or speed only when you ask. I send a file and start a print, stop a print, or send raw G-code only after showing you the exact command and getting your OK in your own words.

Then show the checklist below in one message so they know what's coming, and walk through it one step at a time, waiting for each answer.

## What you need before we start

- A Bambu Lab printer on firmware new enough for Developer Mode: X1 01.08.03.00, P1 01.08.02.00, A1 and A1 mini 01.05.00.00, H2D 01.01.00.01 or later.
- A computer that stays on the same home network as the printer (not a guest network), where the agent can run commands.
- Python 3 on that computer. On a Mac, if `python3` asks to install the Command Line Tools, accept it.
- Bambu Studio (free, from bambulab.com) on that computer, so I can slice models for you. Open it once after installing so it finishes its first-run setup.
- Five minutes at the printer's touchscreen.

## Setup steps

1. Pick the computer. Confirm which computer on the printer's local network will run the commands. If the agent can't run commands on one, have them set that up on a computer on the printer's network and come back. On a Mac, have them open System Settings, Privacy & Security, Local Network and turn on the app that runs the commands (their terminal or agent app) before any test. Without it every connection fails with "No route to host" even though the printer is right there.
2. Turn on LAN Only Mode and Developer Mode on the printer. Link https://wiki.bambulab.com/en/knowledge-sharing/enable-developer-mode. Explain plainly that Developer Mode turns off Bambu Cloud and the Bambu Handy app for that printer, that Bambu Studio and OrcaSlicer can still send prints over LAN, and that without Developer Mode the agent can only read status.
3. Get the printer's IP address from the LAN Only screen. Recommend a DHCP reservation on their router so the address never changes and the agent doesn't lose the printer.
4. Ask the user for the access code and keep it out of chat logs. Tell them where it is on their model: X1 and H2 under Settings, General or LAN Only; P1 under Settings, WLAN; A1 and A1 mini under Settings, WLAN or LAN Only Mode. If it shows all zeros, toggle LAN Only off and on. The code changes whenever LAN Only Mode is toggled, so if the agent later gets "MQTT refused", ask for the new code.
5. Get the serial number from Settings, Device. It isn't secret, so a normal message is fine. Its first three characters tell you the model (see bambu-lan-printer-control).
6. Ask what to call this printer, then whether they have more printers, and repeat steps 3 to 6 for each.

## Install and prove it

Follow bambu-lan-printer-control to install the script, the Python virtual environment, and the mode-600 config file on that computer. If you are working somewhere other than that computer, write the config with the access code locally first, copy it over, and delete your copy, so the code never shows up in chat, logs, or memory. Then run `status`, `files`, and `snapshot`, and show them the state, temperatures, last job, and the camera picture. If anything fails, use the error to say exactly which step to recheck: no route means the Local Network permission or the wrong network, refused means the access code, and connected with no report means the serial or Developer Mode.

Save a memory for each printer with its name, model, IP, serial, and which computer reaches it, but never the access code. Remind them once to keep printers off guest networks, since anyone on the network with the code can control a printer in Developer Mode.

Then prove slicing. Follow bambu-print-slicing to install `slice_bambu.py` in `~/.bambu`, and ask which filament they usually run and which build plate is on the printer. Slice any small model they have (or a model another agent hands over) into `~/.bambu/jobs/<job>/sliced/`, and show the time, grams, and preview. Nothing goes to the printer in this step. If Bambu Studio isn't installed yet, skip this and tell them slicing will work once it is.

Finally, offer one optional routine: a check every 15 minutes while a print is running that tells them when it finishes, pauses, or errors, and stays quiet otherwise. Create it only if they say yes.
