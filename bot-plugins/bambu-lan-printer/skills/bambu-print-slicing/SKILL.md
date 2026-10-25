---
name: bambu-print-slicing
description: >-
  Use when an agent needs to turn a model file (STL, 3MF, STEP) into a printable
  .gcode.3mf for a Bambu Lab printer: pick stock profiles for the printer,
  nozzle, filament, and plate, apply the designer's print notes, slice headless
  with the Bambu Studio command line on the owner's computer, and check the
  estimate before offering to print.
---
# Bambu print slicing

Slicing runs on the owner's computer that has Bambu Studio installed (run these commands on that computer), in a job folder such as `~/.bambu/jobs/<job>/`. Keep one folder per job and never overwrite or delete the owner's other files.

Use this when the owner hands you a model (STL, 3MF, STEP) and wants it printed. Slicing only reads files and writes a new file on the owner's computer; it never touches the printer, so it needs no confirmation. Uploading and starting the print follow the gates in the bambu-lan-printer-control skill.

Tested with Bambu Studio 02.08.02 on macOS (Apple silicon) for the A1 mini, P1S and X1 Carbon.

## 1. Find the slicer CLI and the stock profiles

The Bambu Studio app binary is also its command line slicer. It runs headless (no window opens) and slices a small part in a few seconds.

| OS | CLI binary | Stock profiles |
|---|---|---|
| macOS | `/Applications/BambuStudio.app/Contents/MacOS/BambuStudio` | `/Applications/BambuStudio.app/Contents/Resources/profiles/BBL/` |
| Windows | `C:\Program Files\Bambu Studio\bambu-studio.exe` | `...\Bambu Studio\resources\profiles\BBL\` |
| Linux | `bambu-studio` (AppImage or Flatpak) | `<install>/resources/profiles/BBL/` |

Check it with `"<cli>" --help`. If Bambu Studio is missing, ask the owner to install it from bambulab.com. Don't install apps on their computer without asking.

The profiles live in `machine/`, `process/` and `filament/`. Each JSON file `inherits` from a parent (for example, `0.20mm Standard @BBL A1M` inherits `fdm_process_single_0.20`, which inherits `fdm_process_single_common`, which inherits `fdm_process_common`). Machine profiles also `include` G-code template files. The CLI wants fully resolved (flat) JSON, so the helper script `slice_bambu.py` walks the chain, merges child over parent, merges the includes, and writes flat files.

## 2. Choose profiles for the printer

- **Machine:** `Bambu Lab <model> <nozzle> nozzle`, for example `Bambu Lab A1 mini 0.4 nozzle`. Model names: A1 mini, A1, A2L, P1P, P1S, P2S, X1, X1 Carbon, X1E, X2D, H2D, H2D Pro, H2S, H2C. Assume a 0.4 mm nozzle unless the owner says otherwise.
- **Process:** the stock preset whose name starts with the layer height and whose `compatible_printers` lists that machine. Prefer "Standard", then "Optimal". For example, `0.20mm Standard @BBL A1M` for the A1 mini. The P1S and P1P share the `@BBL X1C` process presets.
- **Filament:** the printer-specific variant of the material, such as `Generic TPU @BBL A1M` or `Bambu TPU 95A HF @BBL A1M`. Don't use the bare `Generic TPU` base preset. It is also marked instantiable, but it lacks the A1 mini's plate temperatures and start G-code.
- **Plate:** set `curr_bed_type`. If you leave it out, the CLI uses Cool Plate. The A1, A1 mini and P1S ship with the Textured PEI Plate, and the start G-code applies a Z offset only on that plate. Ask the owner which plate is installed if you're not sure.
- **Feed:** TPU (except "TPU for AMS") can't go through the AMS or AMS lite. Slice it the normal way, and tell the owner to load it on the external spool holder. At print time, send the print command with the AMS turned off (`use_ams: false`, which `bambu_lan.py print` does when `--ams-mapping` is omitted).

## 3. Apply the designer's overrides

Only change what the designer or owner asked for. Leave everything else at the stock values, including retraction, temperatures and the volumetric speed limit. Common process keys:

| Intent | Key=value |
|---|---|
| Wall count | `wall_loops=3` |
| Arachne walls | `wall_generator=arachne` |
| Infill | `sparse_infill_density=15%` `sparse_infill_pattern=gyroid` |
| Brim | `brim_type=outer_only` `brim_width=5` (`no_brim`, `auto_brim`) |
| Supports | `enable_support=0` (or `1` with `support_type=tree(auto)`) |
| Outer wall speed (mm/s) | `outer_wall_speed=30` |
| Avoid crossing walls | `reduce_crossing_wall=1` |
| Layer height | pick the matching stock process with `--layer` instead of overriding it |

Filament or machine keys take a prefix, for example `filament:nozzle_temperature=230`. List-type keys such as speeds and temperatures are written as one-element lists, and the helper handles that.

## 4. Slice

The helper does steps 1 to 3 and the slice in one call:

```bash
python3 ~/.bambu/slice_bambu.py \
  --model ~/.bambu/jobs/<job>/<part>.stl \
  --printer-model A1M --nozzle 0.4 --filament "Generic TPU" --layer 0.20 \
  --bed-type "Textured PEI Plate" --outdir ~/.bambu/jobs/<job>/sliced \
  wall_loops=3 sparse_infill_density=15% sparse_infill_pattern=gyroid \
  brim_type=outer_only brim_width=5 enable_support=0 outer_wall_speed=30 \
  reduce_crossing_wall=1 wall_generator=arachne
```

Under the hood, it runs this CLI command:

```bash
"<cli>" --orient 0 --arrange 1 --slice 0 \
  --load-settings "<flat machine>.json;<flat process>.json" \
  --load-filaments "<flat filament>.json" \
  --outputdir <outdir> --export-3mf <part>.gcode.3mf <part>.stl
```

- `--orient 0` keeps the part exactly as the designer exported it. Never auto-orient a part the designer stood on edge on purpose.
- `--arrange 1` centers the part on the plate without rotating it. Use `--arrange 0` to keep the file's XY position.
- `--slice 0` slices all plates. Together with `--export-3mf`, it writes a plate-sliced project.

## 5. Check the result before offering to print

The helper prints a JSON summary and exits 0 only when the slice succeeded and the G-code checksum matches. Check these fields:

- `ok`, `cli_result.error_string` ("Success."), and `plates` (it must contain `Metadata/plate_1.gcode`). The `.gcode.3mf` also holds `Metadata/plate_1.gcode.md5`, which the printer checks. This is the same file Bambu Studio's "Export plate sliced file" makes, and it's what you upload over FTPS for a LAN print.
- `estimated_time` and `filament_g` come from `Metadata/slice_info.config` (`prediction` in seconds and `weight` in grams). This is the same number the printer shows, including start-up calibration. The bare `main_predication` in the CLI's `result.json` leaves out several minutes of start-up.
- `max_layer_z_mm` should match the model's height in the orientation you meant. Get the model's size from `"<cli>" --info <part>.stl` before slicing. If the Z doesn't match, the part was laid down or scaled.
- `extrusion_bbox_mm` (including the brim) must fit inside the bed. The A1 mini bed is 180 x 180 mm, and the A1, P1 and X1 beds are 256 x 256 mm.
- `_keys_not_in_stock_profile` lists override keys the stock chain doesn't set. Valid keys such as `brim_type` still work, but a typo there is ignored silently, so check the spelling.
- `slice_warnings` and `cli_warnings`: `not_support_traditional_timelapse` is harmless. Show the owner anything else.
- The preview is `Metadata/plate_1.png` inside the 3MF (512x512). Unzip it to show the owner what will print.

Then tell the owner the printer, the profiles, the time, the grams and any warnings, and show the preview. After that, follow the upload and print gates in bambu-lan-printer-control.

## Gotchas we hit

- In zsh (the default macOS shell), `$OVERRIDES` in a variable doesn't split into words. All the overrides arrive as one argument, and only the first key gets a mangled value. Pass each `key=value` as its own argument.
- A plain family name like "Generic TPU" matches a generic base preset that is also instantiable. Always resolve to the `@BBL <model>` variant (the helper prefers it).
- Without `curr_bed_type`, the project defaults to Cool Plate.
- The CLI also drops a loose `plate_1.gcode` and `result.json` into the output folder, and overwrites `result.json` on every run. Use one output folder per job.
- The CLI writes `[trace]` log lines to stderr. The helper saves them to `<part>.slice.log`.
- On the first slice, measure twice. Compare the model's height from `--info` with `max_layer_z_mm`, and check that the brim bbox is about 2 x brim width larger than the part.

## Helper script

The helper is the file `slice_bambu.py` in this skill's folder (Python 3 standard library only). Write it exactly to `~/.bambu/slice_bambu.py` on the owner's computer; don't retype or edit it.
