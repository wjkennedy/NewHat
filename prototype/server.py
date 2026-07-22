#!/usr/bin/env python3
"""Dependency-free local server for the New Hat vertical-slice prototype."""
from __future__ import annotations

import io
import json
import sys
import base64
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "pystitch" / "src"))


def as_dst(operations: list[dict]) -> bytes:
    """Compile the browser's richer operation list through the local DST codec."""
    from pystitch import EmbPattern, write_dst

    if not any(operation.get("kind") == "stitch" for operation in operations):
        raise ValueError("A DST requires at least one stitch operation")
    pattern = EmbPattern()
    pattern.metadata("name", "New Hat Prototype")
    for operation in operations:
        kind = operation.get("kind")
        x, y = float(operation.get("x", 0)) * 10, float(operation.get("y", 0)) * 10
        if kind == "stitch":
            pattern.stitch_abs(x, y)
        elif kind == "jump":
            pattern.move_abs(x, y)
        elif kind == "trim":
            pattern.trim()
        elif kind == "color":
            pattern.color_change()
    pattern.end()
    output = io.BytesIO()
    write_dst(pattern, output)
    return output.getvalue()


def inspect_dst(dst: bytes) -> list[dict]:
    """Decode exactly what DST retained, for use by the simulator."""
    from pystitch import read_dst
    from pystitch.EmbConstant import COLOR_CHANGE, COMMAND_MASK, END, JUMP, STITCH, TRIM

    names = {STITCH: "stitch", JUMP: "jump", TRIM: "trim", COLOR_CHANGE: "color"}
    pattern = read_dst(io.BytesIO(dst))
    decoded = []
    for x, y, command in pattern.stitches:
        command &= COMMAND_MASK
        if command == END:
            break
        if command in names:
            decoded.append({"kind": names[command], "x": x / 10, "y": y / 10})
    return decoded


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/export.dst", "/api/preview.dst", "/api/inspect.dst", "/api/digitize"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            if path == "/api/inspect.dst":
                dst = body
            elif path == "/api/digitize":
                request = json.loads(body)
                encoded = request["image"].split(",", 1)[1]
                from digitizer import digitize
                plan = digitize(
                    base64.b64decode(encoded),
                    width_mm=float(request["width_mm"]),
                    colors=int(request["colors"]),
                    profile=str(request.get("profile", "standard")),
                )
                payload = json.dumps(plan.json()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            else:
                dst = as_dst(json.loads(body)["operations"])
        except Exception as error:
            self.send_error(400, f"Could not compile stitch plan: {error}")
            return
        if path in {"/api/preview.dst", "/api/inspect.dst"}:
            payload = json.dumps({"operations": inspect_dst(dst)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", 'attachment; filename="new-hat-job.dst"')
        self.send_header("Content-Length", str(len(dst)))
        self.end_headers()
        self.wfile.write(dst)


if __name__ == "__main__":
    address = ("127.0.0.1", 8000)
    print(f"New Hat prototype: http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, Handler).serve_forever()
