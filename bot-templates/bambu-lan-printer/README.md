# Bambu LAN Printer

Take 3D prints end to end on Bambu Lab printers over your home network, with no Bambu Cloud in the middle.

[![Donate](https://img.shields.io/badge/Donate-cmdlab.tech-blue)](https://cmdlab.tech/donate)

## Skills

- **bambu-printer-getting-started**: first-run setup. Covers prerequisites, LAN Only Mode and Developer Mode, macOS Local Network permission, IP, access code (kept out of chat), serial, install, and proving status, files, camera, and slicing.
- **bambu-print-slicing**: turns an STL, 3MF, or STEP into a printable `.gcode.3mf` with the Bambu Studio command line and stock Bambu profiles, applies the designer's print notes, and checks time, grams, plate fit, and warnings. Ships `slice_bambu.py`.
- **bambu-lan-printer-control**: reads status, files, and camera snapshots, and pauses, resumes, stops, uploads, and starts prints over MQTT and FTPS. Includes the end-to-end flow from model to finished print. Ships `bambu_lan.py`.

## Install

From the Cursor Marketplace, once listed: search for **bambu-lan-printer** and install it.

To install locally, clone the repository and link this plugin's folder into your Cursor plugins folder, then reload Cursor:

```sh
git clone https://github.com/cmdlabtech/bot-templates.git ~/src/bot-templates
mkdir -p ~/.cursor/plugins/local
ln -s ~/src/bot-templates/bot-templates/bambu-lan-printer ~/.cursor/plugins/local/bambu-lan-printer
```

The plugin lives in `bot-templates/bambu-lan-printer/` of the repository. The two helper scripts (`skills/bambu-lan-printer-control/bambu_lan.py` and `skills/bambu-print-slicing/slice_bambu.py`, relative to the plugin folder) need Python 3. `slice_bambu.py` uses only the standard library, and `bambu_lan.py` also needs `paho-mqtt`, which the control skill installs in a virtual environment. The skills copy them onto the computer that shares a network with the printer.

## Requirements

- A Bambu Lab printer with Developer Mode firmware (X1 01.08.03.00, P1 01.08.02.00, A1 and A1 mini 01.05.00.00, H2D 01.01.00.01 or later).
- A computer on the same network as the printer, with Python 3 and Bambu Studio installed.

## Safety

Starting, stopping, and raw G-code always need the owner's explicit confirmation. The access code lives only in a mode-600 file on the owner's computer and is never printed. Developer Mode leaves a printer open to anyone on the network who has its code, so keep printers off guest networks. Bambu does not support Developer Mode.

## Support

If this plugin saves you time, you can support the work at [cmdlab.tech/donate](https://cmdlab.tech/donate).

## License

MIT. See [LICENSE](LICENSE).

---

© 2026 CMDLAB LLC
