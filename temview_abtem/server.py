"""A small local HTTP service the TEM-view exhibit sends requests to.

    temview-abtem serve            # http://127.0.0.1:8797

  GET  /health            versions, presets, supported modes and specimens
  POST /jobs              a temview-abtem/1 request (JSON) → {"id": ...}; jobs run one at a time
  GET  /jobs/<id>         {"status": queued|running|done|error, "progress": "...", "error": ...}
  GET  /jobs/<id>/result  the result as TEM-view .npz

It listens on 127.0.0.1 only and answers browsers only from the exhibit's own sites (and localhost), so a web
page elsewhere cannot use it. Requests are data, never code, and are bounded (see request.py).

Remote mode (the "API tunnel", see TUNNEL.md): `temview-abtem serve --tokens tokens.txt` still binds 127.0.0.1;
a tunnel (Cloudflare Tunnel, Tailscale, ssh -R) forwards to it. Every request except OPTIONS then needs
`Authorization: Bearer <token>`. Each token line is `name token [max_preset] [max_queued]`; jobs are only visible to
the token that submitted them, presets above the token's ceiling are refused, and each token may have only
`max_queued` jobs waiting or running.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .request import MODES, PRESETS, SPECIMENS, RequestError, validate
from .simulate import simulate, to_npz_bytes

ORIGINS = [o.strip() for o in os.environ.get("TEMVIEW_ORIGINS", "https://advanced-em.github.io").split(",") if o.strip()]
LOCAL = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")
MAX_BODY = 20 * 1024 * 1024
PRESET_RANK = {"quick": 0, "standard": 1, "full": 2}
TOKENS: dict[str, dict] = {}  # token -> {"name", "max_preset", "max_queued"}; empty = local mode, no auth
JOBS: dict[str, dict] = {}


def load_tokens(path: str) -> dict:
    out = {}
    for line in open(path):
        line = line.split("#", 1)[0].split()
        if not line:
            continue
        if len(line) < 2 or len(line[1]) < 16:
            raise SystemExit(f"{path}: each line is 'name token [max_preset] [max_queued]', token at least 16 characters")
        mp = line[2] if len(line) > 2 else "standard"
        if mp not in PRESET_RANK:
            raise SystemExit(f"{path}: max_preset must be quick, standard or full")
        out[line[1]] = {"name": line[0], "max_preset": mp, "max_queued": int(line[3]) if len(line) > 3 else 2}
    if not out:
        raise SystemExit(f"{path}: no tokens")
    return out
Q: "queue.Queue[str]" = queue.Queue()


def _worker():
    while True:
        jid = Q.get()
        job = JOBS[jid]
        job.update(status="running", started=time.time())
        try:
            res = simulate(job["request"], progress=lambda m: job.update(progress=m))
            job.update(status="done", result=to_npz_bytes(res), seconds=res["meta"]["abtem"]["seconds"], progress="done")
        except RequestError as e:
            job.update(status="error", error=str(e))
        except Exception as e:  # report, keep serving
            job.update(status="error", error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-2000:])
        job.pop("request", None)


class Handler(BaseHTTPRequestHandler):
    server_version = "temview-abtem/0.1"

    def _origin_ok(self) -> str | None:
        o = self.headers.get("Origin")
        if o is None:
            return None  # not a browser (curl, Python): allowed, no CORS headers needed
        return o if (o in ORIGINS or LOCAL.match(o)) else ""

    def _send(self, code: int, body: bytes, ctype="application/json"):
        o = self._origin_ok()
        if o == "":
            code, body, ctype = 403, b'{"error": "origin not allowed"}', "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if o:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode())

    def do_OPTIONS(self):  # CORS preflight, including Chrome's private-network access
        o = self._origin_ok()
        self.send_response(204 if o != "" else 403)
        if o:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def _client(self):
        """Local mode: "local". Remote mode: the token's record, or None after answering 401."""
        if not TOKENS:
            return {"name": "local", "max_preset": "full", "max_queued": 10**6}
        import hmac
        h = self.headers.get("Authorization", "")
        tok = h[7:] if h.startswith("Bearer ") else ""
        for t, rec in TOKENS.items():
            if hmac.compare_digest(t, tok):
                return rec
        self._json(401, {"error": "missing or wrong token"})
        return None

    def do_GET(self):
        who = self._client()
        if who is None:
            return
        if self.path == "/health":
            import abtem
            return self._json(200, {"ok": True, "abtem": abtem.__version__, "modes": MODES, "specimens": SPECIMENS,
                                    "presets": PRESETS, "queued": Q.qsize()})
        m = re.fullmatch(r"/jobs/([0-9a-f]{32})(/result)?", self.path)
        if not m or m.group(1) not in JOBS or JOBS[m.group(1)].get("owner") != who["name"]:
            return self._json(404, {"error": "no such job"})
        job = JOBS[m.group(1)]
        if m.group(2):
            if job["status"] != "done":
                return self._json(409, {"error": f"job is {job['status']}"})
            return self._send(200, job["result"], "application/octet-stream")
        self._json(200, {k: job.get(k) for k in ("status", "progress", "error", "seconds")} | {"queue": Q.qsize()})

    def do_POST(self):
        who = self._client()
        if who is None:
            return
        if self.path != "/jobs":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= MAX_BODY:
            return self._json(413, {"error": "request too large"})
        if self._origin_ok() == "":
            return self._json(403, {"error": "origin not allowed"})
        try:
            req = validate(json.loads(self.rfile.read(n)))
        except (ValueError, RequestError) as e:
            return self._json(400, {"error": str(e)})
        if PRESET_RANK.get(req.get("preset", "standard"), 0) > PRESET_RANK[who["max_preset"]]:
            return self._json(403, {"error": f"preset {req.get('preset')} is above this token's limit ({who['max_preset']})"})
        cap = PRESETS[who["max_preset"]]
        for k in ("frozen_phonons", "scan_gpts", "max_gpts"):  # run overrides may not exceed the token's preset
            if cap.get(k) is not None and (req["run"].get(k) is None or req["run"][k] > cap[k]):
                return self._json(403, {"error": f"run.{k} is above this token's limit ({who['max_preset']}: {cap[k]})"})
        _expire()
        waiting = sum(1 for j in JOBS.values() if j.get("owner") == who["name"] and j["status"] in ("queued", "running"))
        if waiting >= who["max_queued"]:
            return self._json(429, {"error": f"{waiting} jobs already waiting for this token; try again when one finishes"})
        jid = uuid.uuid4().hex
        JOBS[jid] = {"status": "queued", "progress": "queued", "request": req, "created": time.time(), "owner": who["name"]}
        Q.put(jid)
        self._json(202, {"id": jid})

    def log_message(self, fmt, *args):  # quiet: one line per request
        print(f"{self.address_string()} {fmt % args}")


JOB_TTL_S = 3600


def _expire():
    """Forget finished jobs (and their results) after an hour, so a long-running service does not grow."""
    now = time.time()
    for jid in [k for k, j in JOBS.items() if j["status"] in ("done", "error") and now - j["created"] > JOB_TTL_S]:
        JOBS.pop(jid, None)


def make_server(port: int = 8797, tokens: str | None = None):
    TOKENS.clear()
    if tokens:
        TOKENS.update(load_tokens(tokens))
    threading.Thread(target=_worker, daemon=True).start()
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port: int = 8797, tokens: str | None = None):
    srv = make_server(port, tokens)
    mode = f"remote mode, {len(TOKENS)} token(s): {', '.join(r['name'] for r in TOKENS.values())}" if TOKENS else "local mode"
    print(f"temview-abtem bridge on http://127.0.0.1:{port} ({mode}; sites allowed: {', '.join(ORIGINS)}, localhost)")
    srv.serve_forever()
