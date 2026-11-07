"""Headless connection probe for diagnostics and automated testing.

    python -m tvconnect_desktop.tools.probe --host 192.168.1.50 --mode native --seconds 10 --out frame.png
    python -m tvconnect_desktop.tools.probe --host 192.168.1.50 --mode adb --key 3 --key 20

Prints the pairing code, connection state, frame count, RTT and saves the last decoded frame.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

from ..devices.trust_store import TrustStore  # noqa: E402
from ..logging_setup import setup_logging  # noqa: E402
from ..net.connections import AdbConnection, NativeConnection  # noqa: E402
from ..settings import load_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=47800)
    ap.add_argument("--mode", choices=["native", "adb"], default="native")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--key", type=int, action="append", default=[], help="Android keycode to send after video starts")
    ap.add_argument("--tap", action="append", default=[], help="normalized x,y to tap after video starts")
    ap.add_argument("--text", default="")
    ap.add_argument("--paste", default="", help="paste this text into the focused TV field (shared clipboard)")
    ap.add_argument("--copy", action="store_true", help="request text from the TV (TV -> PC clipboard)")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds after the first frame before sending input")
    ap.add_argument("--resolution", default="")
    ap.add_argument("--fps", type=int, default=0)
    ap.add_argument("--input", choices=["auto", "native", "adb"], default="")
    args = ap.parse_args()

    setup_logging(verbose=False)
    logging.getLogger().handlers[-1].setLevel(logging.WARNING)
    app = QGuiApplication(sys.argv[:1])
    settings = load_settings()
    if args.resolution:
        settings.resolution = args.resolution
    if args.fps:
        settings.fps = args.fps
    if args.input:
        settings.input_mode = args.input
    conn = (
        NativeConnection(settings, TrustStore(), args.host, args.port)
        if args.mode == "native"
        else AdbConnection(settings, args.host)
    )
    state = {"frames": 0, "last": None, "rtt": [], "result": 1, "first_frame": False}

    def on_frame(img: QImage) -> None:
        state["frames"] += 1
        state["last"] = img
        if not state["first_frame"]:
            state["first_frame"] = True
            print(f"[probe] first frame {img.width()}x{img.height()}", flush=True)
            QTimer.singleShot(int(args.delay * 1000), send_actions)

    def send_actions() -> None:
        # Spaced out so each action's effect is visible in the stream / logs.
        steps = [("key", k) for k in args.key] + [("tap", t) for t in args.tap] + ([("text", args.text)] if args.text else [])
        steps += ([("paste", args.paste)] if args.paste else []) + ([("copy", "")] if args.copy else [])
        for i, (kind, val) in enumerate(steps):
            def run(kind=kind, val=val):
                print(f"[probe] send {kind} {val}", flush=True)
                if kind == "key":
                    conn.router.key(val)
                elif kind == "paste":
                    conn.paste_text(val)
                elif kind == "copy":
                    conn.request_tv_clipboard()
                elif kind == "tap":
                    x, y = (float(v) for v in val.split(","))
                    conn.router.tap(x, y)
                else:
                    conn.router.text(val)
            QTimer.singleShot(i * 1500, run)

    def finish() -> None:
        img = state["last"]
        if img is not None and args.out:
            img.save(args.out)
            print(f"[probe] saved {args.out}", flush=True)
        rtts = state["rtt"]
        avg = sum(rtts) / len(rtts) if rtts else float("nan")
        print(f"[probe] frames={state['frames']} decoded={conn.decoder.frames_decoded} "
              f"dropped={conn.decoder.frames_dropped} rtt_avg={avg:.1f}ms decode={conn.decoder.last_decode_ms:.1f}ms",
              flush=True)
        state["result"] = 0 if state["frames"] > 0 else 2
        conn.close()
        QTimer.singleShot(300, app.quit)

    conn.decoder.frame_ready.connect(on_frame)
    conn.state.connect(lambda s, d: print(f"[probe] state {s}: {d}", flush=True))
    conn.pairing.connect(lambda sas, fp: print(f"[probe] PAIRING CODE {sas} (TV key {fp}) — confirm on TV", flush=True))
    conn.ready.connect(lambda info: (print(f"[probe] ready {info}", flush=True),
                                     QTimer.singleShot(int(args.seconds * 1000), finish)))
    conn.video_status.connect(lambda m: print(f"[probe] video {m}", flush=True))
    conn.notice.connect(lambda m: print(f"[probe] notice: {m}", flush=True))
    conn.adb_status.connect(lambda m: print(f"[probe] {m}", flush=True))
    conn.rtt.connect(lambda ms: state["rtt"].append(ms))
    conn.clipboard_from_tv.connect(lambda t, src: print(f"[probe] clipboard from TV ({src}): {t!r}", flush=True))

    def on_closed(reason: str, err: bool) -> None:
        print(f"[probe] closed: {reason} (error={err})", flush=True)
        if err:
            state["result"] = 3
            app.quit()

    conn.closed.connect(on_closed)
    conn.start()
    QTimer.singleShot(int((args.seconds + 120) * 1000), app.quit)  # hard limit (pairing may take time)
    app.exec()
    return state["result"]


if __name__ == "__main__":
    sys.exit(main())
