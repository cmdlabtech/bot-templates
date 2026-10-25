#!/usr/bin/env python3
"""bambu_lan.py - small LAN-only CLI for Bambu Lab printers.

Talks to a printer directly on the local network (no Bambu Cloud):
  * MQTT over TLS, port 8883, user "bblp", password = LAN access code
  * Implicit FTPS, port 990, same credentials (file list / upload)
  * Camera: JPEG stream on TLS port 6000 (A1 / A1 mini / P1 series),
            RTSPS on port 322 (X1 / H2 / P2S, needs ffmpeg for a snapshot)

Requires: Python 3.9+, `pip install paho-mqtt` (v1 or v2).
Printer requirements: LAN Only Mode ON and Developer Mode ON (firmware with
the 2025 Authorization Control blocks third-party control otherwise).

Config comes from environment variables:
  BAMBU_HOST          printer IP, e.g. 192.168.1.50
  BAMBU_SERIAL        printer serial number (Settings > Device)
  BAMBU_ACCESS_CODE   8-character LAN access code
  BAMBU_CA_FILE       optional: PEM CA to verify the printer cert (default: no verification)

Commands that move, heat, or start/stop the printer require --yes.
The access code is never printed.
"""
from __future__ import annotations

import argparse
import ftplib
import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time

MQTT_PORT = 8883
FTPS_PORT = 990
CAM_PORT = 6000
RTSP_PORT = 322
USER = "bblp"

SPEED_NAMES = {1: "silent", 2: "standard", 3: "sport", 4: "ludicrous"}


# --------------------------------------------------------------------------- config
def cfg() -> tuple[str, str, str]:
    missing = [k for k in ("BAMBU_HOST", "BAMBU_SERIAL", "BAMBU_ACCESS_CODE") if not os.environ.get(k)]
    if missing:
        sys.exit(f"error: set {', '.join(missing)} (see --help)")
    return os.environ["BAMBU_HOST"], os.environ["BAMBU_SERIAL"].strip(), os.environ["BAMBU_ACCESS_CODE"].strip()


def tls_context() -> ssl.SSLContext:
    ca = os.environ.get("BAMBU_CA_FILE")
    if ca:
        ctx = ssl.create_default_context(cafile=ca)
        ctx.check_hostname = False  # printer cert CN is the serial, not the IP
        return ctx
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # printers use a self-signed Bambu CA
    return ctx


# --------------------------------------------------------------------------- MQTT
class Printer:
    def __init__(self, host: str, serial: str, code: str, timeout: float = 10.0):
        try:
            import paho.mqtt.client as mqtt  # noqa: WPS433
        except ImportError:
            sys.exit("error: paho-mqtt not installed. Run: pip install paho-mqtt")
        self.mqtt = mqtt
        self.host, self.serial, self.timeout = host, serial, timeout
        self.report_topic = f"device/{serial}/report"
        self.request_topic = f"device/{serial}/request"
        self.state: dict = {}
        self.messages: list[dict] = []
        self.connected = threading.Event()
        self.got_full = threading.Event()
        self.lock = threading.Lock()
        self.rc = None
        if hasattr(mqtt, "CallbackAPIVersion"):
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
        else:
            self.client = mqtt.Client(protocol=mqtt.MQTTv311)
        self.client.username_pw_set(USER, code)
        self.client.tls_set_context(tls_context())
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc, *args):
        self.rc = getattr(rc, "value", rc)
        if self.rc == 0:
            client.subscribe(self.report_topic)
            self.connected.set()
        else:
            self.connected.set()

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8", "replace"))
        except ValueError:
            return
        with self.lock:
            self.messages.append(data)
            p = data.get("print")
            if isinstance(p, dict):
                deep_merge(self.state, p)
                if p.get("command") == "push_status" and "gcode_state" in p and "nozzle_temper" in p:
                    self.got_full.set()

    def __enter__(self):
        try:
            self.client.connect_timeout = min(self.timeout, 10)
            self.client.connect(self.host, MQTT_PORT, keepalive=30)
        except (OSError, socket.timeout) as e:
            sys.exit(f"error: cannot reach {self.host}:{MQTT_PORT} ({e}). Same LAN? LAN Only Mode on?")
        self.client.loop_start()
        if not self.connected.wait(self.timeout):
            self.close()
            sys.exit(f"error: MQTT connect to {self.host}:{MQTT_PORT} timed out after {self.timeout}s")
        if self.rc != 0:
            self.close()
            sys.exit(f"error: MQTT refused (rc={self.rc}). Check access code and serial.")
        time.sleep(0.3)
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        # Disconnect FIRST. paho's loop thread will not stop while a QoS 1 message
        # is still waiting for a PUBACK, and Bambu printers may never send one,
        # so loop_stop() alone can block forever.
        try:
            self.client.disconnect()
        except Exception:
            pass
        t = threading.Thread(target=self.client.loop_stop, daemon=True)
        t.start()
        t.join(3)

    def send(self, payload: dict, qos: int = 1) -> None:
        info = self.client.publish(self.request_topic, json.dumps(payload), qos=qos)
        try:
            info.wait_for_publish(timeout=min(self.timeout, 5))
        except (RuntimeError, ValueError) as e:
            self.close()
            sys.exit(f"error: publish failed ({e})")

    def wait_ack(self, section: str, command: str, seq: str) -> dict | None:
        end = time.time() + self.timeout
        while time.time() < end:
            with self.lock:
                for m in self.messages:
                    body = m.get(section)
                    if isinstance(body, dict) and body.get("command") == command and str(body.get("sequence_id")) == seq:
                        return body
            time.sleep(0.1)
        return None

    def pushall(self) -> dict:
        self.got_full.clear()
        self.send({"pushing": {"sequence_id": seq(), "command": "pushall", "version": 1, "push_target": 1}}, qos=0)
        if not self.got_full.wait(self.timeout):
            with self.lock:
                n = len(self.messages)
            if n == 0:
                self.close()
                sys.exit(f"error: connected but no report on device/<SERIAL>/report within {self.timeout}s. "
                         "Check BAMBU_SERIAL and that Developer Mode is on.")
        with self.lock:
            return dict(self.state)


def deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v


_SEQ = [int(time.time()) % 100000]


def seq() -> str:
    _SEQ[0] += 1
    return str(_SEQ[0])


def summarize(s: dict) -> dict:
    def g(k, d=None):
        return s.get(k, d)
    lights = {x.get("node"): x.get("mode") for x in g("lights_report", []) or [] if isinstance(x, dict)}
    return {
        "state": g("gcode_state"),
        "file": g("subtask_name") or g("gcode_file"),
        "progress_pct": g("mc_percent"),
        "remaining_min": g("mc_remaining_time"),
        "layer": f"{g('layer_num')}/{g('total_layer_num')}" if g("total_layer_num") is not None else None,
        "nozzle_c": f"{g('nozzle_temper')} -> {g('nozzle_target_temper')}",
        "bed_c": f"{g('bed_temper')} -> {g('bed_target_temper')}",
        "chamber_c": g("chamber_temper"),
        "speed": SPEED_NAMES.get(g("spd_lvl"), g("spd_lvl")),
        "lights": lights or None,
        "print_error": g("print_error"),
        "hms_alerts": len(g("hms", []) or []),
        "wifi": g("wifi_signal"),
        "sdcard": g("sdcard"),
    }


def mqtt_command(args, section: str, body: dict, qos: int = 1) -> int:
    host, serial, code = cfg()
    s = seq()
    payload = {section: {"sequence_id": s, **body}}
    with Printer(host, serial, code, args.timeout) as p:
        p.send(payload, qos=qos)
        ack = p.wait_ack(section, body["command"], s)
    if ack is None:
        print(json.dumps({"sent": body["command"], "ack": None,
                          "note": "no ack seen; check status. Developer Mode off can cause silent rejects."}))
        return 0
    print(json.dumps({"sent": body["command"], "result": ack.get("result"), "reason": ack.get("reason")}))
    return 0 if str(ack.get("result", "")).lower() in ("success", "") else 1


def need_yes(args, what: str) -> None:
    if not getattr(args, "yes", False):
        sys.exit(f"refusing: '{what}' changes the physical printer. Re-run with --yes after a human confirms.")


# --------------------------------------------------------------------------- FTPS
class ImplicitFTPS(ftplib.FTP_TLS):
    """Implicit-TLS FTP with TLS session reuse on the data channel (Bambu needs it)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


def ftp_connect(timeout: float) -> ImplicitFTPS:
    host, _, code = cfg()
    ftp = ImplicitFTPS(context=tls_context(), timeout=timeout)
    try:
        ftp.connect(host, FTPS_PORT)
        ftp.login(USER, code)
        ftp.prot_p()
    except (OSError, ftplib.Error) as e:
        sys.exit(f"error: FTPS to {host}:{FTPS_PORT} failed ({e})")
    return ftp


def cmd_files(args) -> int:
    ftp = ftp_connect(args.timeout)
    try:
        names = ftp.nlst(args.path)
    finally:
        ftp.quit()
    for n in names:
        print(n)
    return 0


def cmd_upload(args) -> int:
    if not os.path.isfile(args.local):
        sys.exit(f"error: no such file {args.local}")
    remote = args.remote_name or os.path.basename(args.local)
    ftp = ftp_connect(args.timeout)
    try:
        with open(args.local, "rb") as fh:
            ftp.storbinary(f"STOR {remote}", fh)
    finally:
        ftp.quit()
    print(json.dumps({"uploaded": remote, "bytes": os.path.getsize(args.local)}))
    return 0


# --------------------------------------------------------------------------- camera
def cmd_snapshot(args) -> int:
    host, _, code = cfg()
    if args.rtsp:
        if not shutil.which("ffmpeg"):
            sys.exit("error: X1/H2/P2S snapshot needs ffmpeg on PATH")
        url = f"rtsps://{USER}:{code}@{host}:{RTSP_PORT}/streaming/live/1"
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-rtsp_transport", "tcp", "-i", url,
                            "-frames:v", "1", args.out], timeout=args.timeout + 20)
        if r.returncode:
            sys.exit("error: ffmpeg failed (LAN Mode Liveview on?)")
        print(json.dumps({"snapshot": args.out}))
        return 0
    auth = struct.pack("<IIII", 0x40, 0x3000, 0, 0) + USER.encode().ljust(32, b"\0") + code.encode().ljust(32, b"\0")
    try:
        raw = socket.create_connection((host, CAM_PORT), timeout=args.timeout)
        s = tls_context().wrap_socket(raw, server_hostname=host)
        s.sendall(auth)
        hdr = recv_exact(s, 16)
        size = struct.unpack("<I", hdr[:4])[0]
        img = recv_exact(s, size)
        s.close()
    except (OSError, socket.timeout) as e:
        sys.exit(f"error: camera {host}:{CAM_PORT} failed ({e}). X1/H2/P2S: use --rtsp")
    if not (img[:2] == b"\xff\xd8" and img[-2:] == b"\xff\xd9"):
        sys.exit("error: did not receive a valid JPEG")
    with open(args.out, "wb") as fh:
        fh.write(img)
    print(json.dumps({"snapshot": args.out, "bytes": len(img)}))
    return 0


def recv_exact(s, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(min(65536, n - len(buf)))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf


# --------------------------------------------------------------------------- handlers
def cmd_status(args) -> int:
    host, serial, code = cfg()
    with Printer(host, serial, code, args.timeout) as p:
        state = p.pushall()
    if not state:
        sys.exit("error: no report received (wrong serial?)")
    print(json.dumps(state if args.raw else summarize(state), indent=2, default=str))
    return 0


def cmd_simple(name):
    def run(args):
        if name == "stop":
            need_yes(args, "stop")
        return mqtt_command(args, "print", {"command": name, "param": ""})
    return run


def cmd_light(args) -> int:
    return mqtt_command(args, "system", {
        "command": "ledctrl", "led_node": args.node, "led_mode": args.mode,
        "led_on_time": 500, "led_off_time": 500, "loop_times": 0, "interval_time": 0})


def cmd_speed(args) -> int:
    return mqtt_command(args, "print", {"command": "print_speed", "param": str(args.level)})


def cmd_gcode(args) -> int:
    need_yes(args, "gcode")
    return mqtt_command(args, "print", {"command": "gcode_line", "param": args.line.replace("\\n", "\n") + "\n"})


def cmd_print(args) -> int:
    need_yes(args, "print")
    name = args.remote_file.lstrip("/")
    if not name.endswith(".3mf"):
        sys.exit("error: only sliced .3mf (e.g. model.gcode.3mf) is supported")
    body = {
        "command": "project_file",
        "param": f"Metadata/plate_{args.plate}.gcode",
        "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
        "subtask_name": os.path.splitext(os.path.basename(name))[0],
        "url": f"ftp:///{name}", "md5": "",
        "timelapse": args.timelapse, "bed_type": "auto",
        "bed_levelling": not args.no_level, "flow_cali": False, "vibration_cali": False,
        "layer_inspect": False, "use_ams": args.ams_mapping is not None,
        "ams_mapping": json.loads(args.ams_mapping) if args.ams_mapping else "",
    }
    return mqtt_command(args, "print", body)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bambu_lan.py",
        description="LAN-only control for Bambu Lab printers (LAN Only Mode + Developer Mode required). "
                    "Reads BAMBU_HOST, BAMBU_SERIAL, BAMBU_ACCESS_CODE from the environment.",
    )
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds to wait (default 10)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="request pushall and print a status summary")
    p.add_argument("--raw", action="store_true", help="print the full merged report JSON")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("pushall", help="alias for status --raw")
    p.set_defaults(fn=lambda a: (setattr(a, "raw", True), cmd_status(a))[1])

    for name, txt in (("pause", "pause the current print"), ("resume", "resume a paused print"),
                      ("stop", "STOP (cancel) the current print; requires --yes")):
        p = sub.add_parser(name, help=txt)
        p.add_argument("--yes", action="store_true", help="confirm a physical action")
        p.set_defaults(fn=cmd_simple(name))

    p = sub.add_parser("light", help="chamber/work light on/off")
    p.add_argument("mode", choices=["on", "off"])
    p.add_argument("--node", default="chamber_light", choices=["chamber_light", "work_light"])
    p.set_defaults(fn=cmd_light)

    p = sub.add_parser("speed", help="set speed level 1=silent 2=standard 3=sport 4=ludicrous")
    p.add_argument("level", type=int, choices=[1, 2, 3, 4])
    p.set_defaults(fn=cmd_speed)

    p = sub.add_parser("gcode", help="send raw G-code (dangerous); requires --yes")
    p.add_argument("line", help='e.g. "M104 S0" (use \\n between lines)')
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_gcode)

    p = sub.add_parser("files", help="list files on the printer storage (FTPS)")
    p.add_argument("path", nargs="?", default="/")
    p.set_defaults(fn=cmd_files)

    p = sub.add_parser("upload", help="upload a local file to the printer storage (FTPS)")
    p.add_argument("local")
    p.add_argument("--remote-name")
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("print", help="start printing an uploaded .3mf; requires --yes")
    p.add_argument("remote_file", help="name on printer storage, e.g. cube.gcode.3mf")
    p.add_argument("--plate", type=int, default=1)
    p.add_argument("--ams-mapping", help='JSON list, e.g. "[0]" or "[-1,-1,-1,1,0]"; omit for external spool')
    p.add_argument("--timelapse", action="store_true")
    p.add_argument("--no-level", action="store_true", help="skip bed levelling")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_print)

    p = sub.add_parser("snapshot", help="save one camera frame as JPEG")
    p.add_argument("out", nargs="?", default="bambu_snapshot.jpg")
    p.add_argument("--rtsp", action="store_true", help="X1/H2/P2S (RTSPS 322, needs ffmpeg); default is A1/P1 port 6000")
    p.set_defaults(fn=cmd_snapshot)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # Hard ceiling so no command can hang forever (network stalls, stuck threads).
    limit = args.timeout * 3 + 15

    def _watchdog():
        sys.stderr.write(f"error: '{args.cmd}' gave no result within {limit:.0f}s; aborting\n")
        sys.stderr.flush()
        os._exit(124)

    wd = threading.Timer(limit, _watchdog)
    wd.daemon = True
    wd.start()
    try:
        return args.fn(args) or 0
    finally:
        wd.cancel()


if __name__ == "__main__":
    sys.exit(main())
