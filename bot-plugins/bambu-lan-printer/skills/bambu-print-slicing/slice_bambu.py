#!/usr/bin/env python3
"""slice_bambu.py - headless slicing with the Bambu Studio command line.

Resolves the stock Bambu Studio system profiles (machine, process, filament)
into flat JSON, applies optional key=value overrides to the process (or
filament/machine with a prefix), slices one model to a plate-sliced
.gcode.3mf that Bambu printers accept for LAN printing, and prints a JSON
summary. Python 3 standard library only. It never talks to a printer.

Example:
  python3 slice_bambu.py --model part.stl --printer-model A1M --nozzle 0.4 \
      --filament "Generic TPU" --layer 0.20 \
      wall_loops=3 sparse_infill_density=15% sparse_infill_pattern=gyroid

Overrides: plain keys go to the process profile; prefix with "filament:" or
"machine:" to target those (e.g. filament:nozzle_temperature=225). A value
for a list-type key is applied to every list slot.
"""
import argparse, glob, hashlib, json, os, platform, re, shutil, subprocess, sys, zipfile

PRINTER_MODELS = {
    "A1M": "Bambu Lab A1 mini", "A1MINI": "Bambu Lab A1 mini", "A1": "Bambu Lab A1",
    "A2L": "Bambu Lab A2L", "P1P": "Bambu Lab P1P", "P1S": "Bambu Lab P1S",
    "P2S": "Bambu Lab P2S", "X1": "Bambu Lab X1", "X1C": "Bambu Lab X1 Carbon",
    "X1E": "Bambu Lab X1E", "X2D": "Bambu Lab X2D", "H2D": "Bambu Lab H2D",
    "H2DP": "Bambu Lab H2D Pro", "H2S": "Bambu Lab H2S", "H2C": "Bambu Lab H2C",
}
PROCESS_PREFERENCE = ["Standard", "Optimal", "Strength", "High Quality", "Fine", "Draft"]
DROP_KEYS = {"inherits", "include", "instantiation", "setting_id", "base_id", "renamed_from"}


def find_cli():
    cands = [os.environ.get("BAMBU_STUDIO_CLI", "")]
    if platform.system() == "Darwin":
        cands += ["/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
                  os.path.expanduser("~/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")]
    elif platform.system() == "Windows":
        cands += [r"C:\Program Files\Bambu Studio\bambu-studio.exe"]
    cands += [shutil.which(n) or "" for n in ("bambu-studio", "BambuStudio")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    sys.exit("Bambu Studio CLI not found; set BAMBU_STUDIO_CLI")


def find_profiles(cli):
    env = os.environ.get("BAMBU_PROFILES")
    if env:
        return env
    d = os.path.dirname(os.path.realpath(cli))
    for rel in ("../Resources/profiles/BBL", "resources/profiles/BBL", "../share/BambuStudio/profiles/BBL",
                "../resources/profiles/BBL"):
        p = os.path.normpath(os.path.join(d, rel))
        if os.path.isdir(p):
            return p
    sys.exit("stock profiles (profiles/BBL) not found; set BAMBU_PROFILES")


class Profiles:
    def __init__(self, root):
        self.root, self.idx = root, {}
        for kind in ("machine", "process", "filament"):
            m = {}
            for f in glob.glob(os.path.join(root, kind, "*.json")):
                try:
                    with open(f, encoding="utf-8") as fh:
                        d = json.load(fh)
                except Exception:
                    continue
                m[d.get("name", os.path.splitext(os.path.basename(f))[0])] = (f, d)
            self.idx[kind] = m

    def raw(self, kind, name):
        if name not in self.idx[kind]:
            sys.exit(f"{kind} profile not found: {name}")
        return self.idx[kind][name][1]

    def resolve(self, kind, name):
        d = self.raw(kind, name)
        out = self.resolve(kind, d["inherits"]) if d.get("inherits") else {}
        for inc in d.get("include", []):  # gcode template fragments (machine)
            out.update({k: v for k, v in self.raw(kind, inc).items() if k not in DROP_KEYS | {"name", "type", "from"}})
        out.update({k: v for k, v in d.items() if k not in DROP_KEYS})
        out["name"], out["from"] = name, "system"
        return out

    def leaves(self, kind):
        return {n: d for n, (_, d) in self.idx[kind].items() if str(d.get("instantiation", "")).lower() == "true"}


def pick_process(pr, machine, layer, name=None):
    if name:
        return name
    want = f"{float(layer):.2f}mm"
    cands = [n for n, d in pr.leaves("process").items()
             if n.startswith(want) and machine in d.get("compatible_printers", [])]
    if not cands:
        sys.exit(f"no {want} process profile compatible with {machine}")
    rank = lambda n: next((i for i, w in enumerate(PROCESS_PREFERENCE) if f" {w} @" in n), 99)
    return sorted(cands, key=lambda n: (rank(n), len(n)))[0]


def pick_filament(pr, machine, filament):
    """Prefer the printer-specific variant (e.g. 'Generic TPU @BBL A1M') over a
    generic base preset with the same family name."""
    leaves = pr.leaves("filament")
    fam = filament.split(" @")[0]
    cands = [n for n in leaves if n == filament or n.split(" @")[0] == fam]
    explicit = [n for n in cands if machine in leaves[n].get("compatible_printers", [])]
    if filament in explicit:
        return filament
    if explicit:
        return sorted(explicit, key=len)[0]
    loose = [n for n in cands if not leaves[n].get("compatible_printers")]
    if loose:
        return sorted(loose, key=len)[0]
    sys.exit(f"no filament profile '{filament}' compatible with {machine}")


def coerce(base, value):
    if isinstance(base, list):
        return [value] * max(1, len(base))
    return value


def apply_overrides(cfgs, pairs):
    applied = {}
    for p in pairs:
        if "=" not in p:
            sys.exit(f"bad override (want key=value): {p}")
        k, v = p.split("=", 1)
        target = "process"
        if ":" in k and k.split(":", 1)[0] in cfgs:
            target, k = k.split(":", 1)
        cfg = cfgs[target]
        if k not in cfg:
            # not set in the stock profile chain; the slicer still honors valid keys
            # (e.g. brim_type), but a typo would be silently ignored, so report it
            applied.setdefault("_keys_not_in_stock_profile", []).append(f"{target}:{k}")
        cfg[k] = coerce(cfg.get(k), v)
        applied[f"{target}:{k}"] = v
    return applied


def summarize(path):
    s = {"gcode_3mf": path}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        s["plates"] = sorted(n for n in names if re.match(r"Metadata/plate_\d+\.gcode$", n))
        if "Metadata/plate_1.gcode" in names:
            g = z.read("Metadata/plate_1.gcode")
            md5 = z.read("Metadata/plate_1.gcode.md5").decode().strip() if "Metadata/plate_1.gcode.md5" in names else None
            s["gcode_md5_ok"] = md5 is not None and md5.upper() == hashlib.md5(g).hexdigest().upper()
            zs = [float(m) for m in re.findall(rb"^G[01] [^;\n]*Z([\d.]+)", g, re.M)]
            layer_z = [float(m) for m in re.findall(rb"^; Z_HEIGHT: ([\d.]+)", g, re.M)]
            s["max_layer_z_mm"] = max(layer_z) if layer_z else None
            s["max_z_move_mm"] = max(zs) if zs else None
            xs, ys = [], []
            for line in g.split(b"\n"):
                if line.startswith(b"G1 ") and b" E" in line and b"X" in line:
                    mx, my = re.search(rb"X([-\d.]+)", line), re.search(rb"Y([-\d.]+)", line)
                    e = re.search(rb" E([-\d.]+)", line)
                    if mx and my and e and float(e.group(1)) > 0:
                        xs.append(float(mx.group(1))); ys.append(float(my.group(1)))
            if xs:
                s["extrusion_bbox_mm"] = {"x": [min(xs), max(xs)], "y": [min(ys), max(ys)]}
            head = g[:4000].decode("utf-8", "replace")
            m = re.search(r"total estimated time: ([^\n;]+)", head)
            if m:
                s["estimated_time_text"] = m.group(1).strip()
        if "Metadata/slice_info.config" in names:
            info = z.read("Metadata/slice_info.config").decode("utf-8", "replace")
            for key in ("prediction", "weight"):
                m = re.search(rf'key="{key}" value="([^"]*)"', info)
                if m:
                    s[key] = m.group(1)
            if "prediction" in s:
                sec = int(float(s["prediction"]))
                s["estimated_time"] = f"{sec // 3600}h{sec % 3600 // 60:02d}m"
            s["filament_g"] = float(s.pop("weight")) if "weight" in s else None
            s["slice_warnings"] = re.findall(r'<warning msg="([^"]*)"[^>]*/?>', info)
        s["thumbnails"] = [n for n in names if re.match(r"Metadata/(plate|top|pick)_\d+(_small)?\.png$", n)]
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="STL/3MF/STEP file to slice")
    ap.add_argument("--printer-model", required=True, help="A1M, A1, P1S, P1P, X1C, X1, X1E, P2S, H2D, H2S, ... or full name")
    ap.add_argument("--nozzle", default="0.4")
    ap.add_argument("--filament", default="Bambu PLA Basic", help='e.g. "Generic TPU", "Bambu TPU 95A HF", "Bambu PETG HF"')
    ap.add_argument("--layer", default="0.20", help="layer height in mm, picks the stock process profile")
    ap.add_argument("--process", help="exact stock process profile name (overrides --layer choice)")
    ap.add_argument("--outdir", help="output directory (default: <model dir>/sliced)")
    ap.add_argument("--name", help="output base name (default: model name)")
    ap.add_argument("--arrange", default="1", help="1 = center on plate without rotating (default), 0 = keep file XY")
    ap.add_argument("--orient", default="0", help="0 = keep orientation as exported (default), 1 = auto-orient")
    ap.add_argument("--bed-type", default="Textured PEI Plate",
                    help='"Textured PEI Plate" (stock on A1/A1 mini/P1S), "Cool Plate", "Engineering Plate", "High Temp Plate"')
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("overrides", nargs="*", help="key=value (process), filament:key=value, machine:key=value")
    a = ap.parse_args()

    cli = find_cli()
    pr = Profiles(find_profiles(cli))
    printer = PRINTER_MODELS.get(a.printer_model.upper().replace(" ", ""), a.printer_model)
    machine_name = f"{printer} {a.nozzle} nozzle"
    if machine_name not in pr.idx["machine"]:
        sys.exit(f"machine profile not found: {machine_name}")
    process_name = pick_process(pr, machine_name, a.layer, a.process)
    filament_name = pick_filament(pr, machine_name, a.filament)

    cfgs = {"machine": pr.resolve("machine", machine_name),
            "process": pr.resolve("process", process_name),
            "filament": pr.resolve("filament", filament_name)}
    cfgs["process"]["curr_bed_type"] = a.bed_type
    applied = apply_overrides(cfgs, a.overrides)
    if applied:
        cfgs["process"]["name"] = process_name + " (overrides)"
        cfgs["process"]["from"] = "User"
        cfgs["process"]["inherits"] = process_name

    model = os.path.abspath(a.model)
    base = a.name or os.path.splitext(os.path.basename(model))[0]
    outdir = os.path.abspath(a.outdir or os.path.join(os.path.dirname(model), "sliced"))
    pdir = os.path.join(outdir, "profiles")
    os.makedirs(pdir, exist_ok=True)
    paths = {}
    for k, cfg in cfgs.items():
        paths[k] = os.path.join(pdir, f"{base}.{k}.json")
        with open(paths[k], "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1)

    out_name = f"{base}.gcode.3mf"
    out_path = os.path.join(outdir, out_name)
    if os.path.exists(out_path):
        os.remove(out_path)
    cmd = [cli, "--orient", a.orient, "--arrange", a.arrange, "--slice", "0",
           "--load-settings", f"{paths['machine']};{paths['process']}",
           "--load-filaments", paths["filament"],
           "--outputdir", outdir, "--export-3mf", out_name, model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
    log = (proc.stdout or "") + (proc.stderr or "")
    with open(os.path.join(outdir, f"{base}.slice.log"), "w", encoding="utf-8") as fh:
        fh.write(log)
    result = {"ok": False, "returncode": proc.returncode, "command": cmd, "bed_type": a.bed_type,
              "profiles": {"machine": machine_name, "process": process_name, "filament": filament_name},
              "overrides": applied}
    rj = os.path.join(outdir, "result.json")
    if os.path.exists(rj):
        try:
            with open(rj, encoding="utf-8") as fh:
                cr = json.load(fh)
            result["cli_result"] = {k: cr.get(k) for k in ("return_code", "error_string")}
            plates = cr.get("sliced_plates") or []
            if plates:
                result["cli_objects"] = [{"name": o.get("name"), "bbox": o.get("bbox")}
                                         for o in plates[0].get("objects", [])]
                wm = plates[0].get("warning_message")
                result["cli_warnings"] = (wm if isinstance(wm, list) else [wm]) if wm else []
                result["total_prediction_s"] = plates[0].get("total_predication")
        except Exception:
            pass
    result["warnings"] = sorted(set(re.findall(r"\[(?:warning|error)\]\s*(.*)", log)))[:30]
    if proc.returncode == 0 and os.path.exists(out_path):
        result.update(summarize(out_path))
        result["ok"] = bool(result.get("plates")) and result.get("gcode_md5_ok", False)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
