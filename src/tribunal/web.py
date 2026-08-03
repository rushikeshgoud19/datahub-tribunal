"""Tribunal web console - stdlib only, one command to run.

    tribunal serve            # needs DataHub + MISTRAL_API_KEY
    tribunal serve --demo     # replays a recorded deliberation, no setup at all

WHY STDLIB: a judge should be able to clone, install one package and see the thing
work. Every dependency between them and a running demo is a chance to lose them, so
the server is http.server and the front end is one HTML file with no build step.

WHY DEMO MODE: DataHub runs locally, so "a URL judges can test" is otherwise
impossible - they would need Docker, a DataHub instance and their own model keys
before seeing anything. --demo replays a REAL captured deliberation from
examples/, clearly labelled as a replay. It is a recording, not a simulation
pretending to be live, and the UI says so.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import datahub_context as dh
from . import panel

HERE = Path(__file__).parent
STATIC = HERE / "static"
EXAMPLES = HERE.parent.parent / "examples"

_state: Dict[str, Any] = {"demo": False}


def _keys() -> List[str]:
    raw = os.environ.get("MISTRAL_API_KEY", "")
    return [k.strip() for k in raw.replace(";", ",").split(",") if k.strip()]


def _client():
    from datahub.sdk import DataHubClient
    server = os.environ.get("DATAHUB_GMS_URL")
    token = os.environ.get("DATAHUB_GMS_TOKEN")
    if server:
        return DataHubClient(server=server, token=token)
    return DataHubClient.from_env()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the console readable
        pass

    # ---------------------------------------------------------------- helpers
    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any):
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse(self, obj: Any) -> bool:
        """Write one event. Returns False once the client has gone away."""
        try:
            self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            self.wfile.flush()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---------------------------------------------------------------- routes
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if p.startswith("/static/"):
            name = p.split("/static/", 1)[1]
            ctype = ("text/css" if name.endswith(".css")
                     else "text/javascript" if name.endswith(".js") else "text/plain")
            return self._file(name, ctype + "; charset=utf-8")

        if p == "/api/health":
            out = {"demo": _state["demo"], "keys": len(_keys())}
            if not _state["demo"]:
                try:
                    from datahub_agent_context import DataHubContext
                    with DataHubContext(_client()):
                        out["datahub"] = dh.connected()
                except Exception as e:  # noqa: BLE001
                    out["datahub"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            return self._json(200, out)

        if p == "/api/search":
            term = (q.get("q") or [""])[0].strip()
            if _state["demo"]:
                return self._json(200, {"assets": _demo_assets(term)})
            if not term:
                return self._json(200, {"assets": []})
            try:
                from datahub_agent_context import DataHubContext
                with DataHubContext(_client()):
                    return self._json(200, {"assets": dh.find_asset(term, 8)})
            except Exception as e:  # noqa: BLE001
                return self._json(200, {"assets": [], "error": str(e)[:200]})

        if p == "/api/ask":
            return self._ask(q)

        return self._json(404, {"error": "not found"})

    def _file(self, name: str, ctype: str):
        f = STATIC / name
        try:
            return self._send(200, f.read_bytes(), ctype)
        except Exception:  # noqa: BLE001
            return self._json(404, {"error": f"missing {name}"})

    # ---------------------------------------------------------------- ask
    def _ask(self, q):
        question = (q.get("question") or [""])[0].strip()
        urn = (q.get("urn") or [""])[0].strip()
        write = (q.get("write") or ["1"])[0] != "0"
        force = (q.get("force") or ["0"])[0] == "1"

        self._sse_open()
        if not question:
            self._sse({"kind": "error", "error": "no question"})
            return

        if _state["demo"]:
            return self._replay()

        keys = _keys()
        if not keys:
            self._sse({"kind": "error", "error": "MISTRAL_API_KEY not set"})
            return

        try:
            from datahub_agent_context import DataHubContext
            with DataHubContext(_client()):
                briefing = ""
                if urn:
                    prior = dh.prior_decisions(question, urn)
                    if prior and not force:
                        self._sse({"kind": "prior", "decisions": prior})
                        self._sse({"kind": "closed"})
                        return
                    ctx = dh.gather_context(urn)
                    briefing = dh.as_briefing(ctx)
                    self._sse({"kind": "briefing", "urn": urn, "text": briefing})

                alive = [True]

                def on_event(ev):
                    if alive[0] and not self._sse(ev):
                        alive[0] = False

                res = panel.deliberate(question, briefing, keys, on_event=on_event)
                if not res.get("ok"):
                    self._sse({"kind": "error", "error": res.get("error")})
                    self._sse({"kind": "closed"})
                    return

                if write and urn:
                    self._sse({"kind": "writing"})
                    out = dh.record_decision(
                        question=question, verdict=res["ruling"],
                        reasoning=panel.reasoning_markdown(res),
                        related_assets=[urn], tag="TribunalReviewed")
                    self._sse({"kind": "written", "ok": bool(out.get("ok")),
                               "document_urn": out.get("document_urn", ""),
                               "errors": out.get("errors") or []})
                self._sse({"kind": "done", "result": {
                    k: v for k, v in res.items() if k != "transcript"}})
        except Exception as e:  # noqa: BLE001
            self._sse({"kind": "error", "error": f"{type(e).__name__}: {e}"})
        self._sse({"kind": "closed"})

    def _replay(self):
        """Replay a captured deliberation at its original pace.

        Timings come from the recording, so the pauses a viewer sees are the real
        ones - a replay that runs instantly would misrepresent what this costs.
        """
        path = EXAMPLES / "recorded_deliberation.json"
        try:
            events = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            self._sse({"kind": "error", "error": f"no recording: {e}"})
            self._sse({"kind": "closed"})
            return
        self._sse({"kind": "demo_notice",
                   "text": "Replaying a real deliberation recorded against a live "
                           "DataHub instance. Nothing is being generated now."})
        last = 0.0
        for ev in events:
            gap = float(ev.get("t", last)) - last
            last = float(ev.get("t", last))
            time.sleep(max(0.0, min(gap, 2.5)))
            if not self._sse(ev):
                return
        self._sse({"kind": "closed"})


def _demo_assets(term: str) -> List[Dict[str, str]]:
    try:
        rows = json.loads((EXAMPLES / "demo_assets.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rows = []
    t = term.lower()
    return [r for r in rows if not t or t in r.get("name", "").lower()][:8]


def serve(port: int = 8077, demo: bool = False) -> int:
    _state["demo"] = demo
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    mode = "DEMO (replaying a recording)" if demo else "LIVE"
    print(f"Tribunal console -> http://localhost:{port}   [{mode}]")
    if not demo:
        print(f"  DataHub: {os.environ.get('DATAHUB_GMS_URL', '(from ~/.datahubenv)')}")
        print(f"  Mistral keys: {len(_keys())}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0
