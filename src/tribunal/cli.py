"""Tribunal CLI - argue a data decision, then write the ruling back to DataHub.

    tribunal ask "should we deprecate fct_orders?" --asset fct_orders
    tribunal ask "is it safe to drop the email column?" --urn urn:li:dataset:(...)
    tribunal ask "..." --asset fct_orders --dry-run     # argue, write nothing

Flow:
  1. resolve the asset in DataHub
  2. check whether this was already decided - a panel that re-litigates a settled
     question produces a confident second opinion nobody asked for
  3. gather lineage / owners / schema as grounding
  4. four advocates argue in isolation, judge scores, code rules
  5. write the ruling AND the argument back as a native DataHub `Decision`

Exit codes: 0 ruled, 1 failed, 2 bad usage. ASCII output only - Windows consoles
are cp1252 and a stray glyph kills the run mid-deliberation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import datahub_context as dh
from . import panel


def _client():
    """Build a DataHubClient from env.

    get_datahub_client() only RETURNS a client already placed in context - it does
    not create one - so the entry point has to construct it. server/token come from
    DATAHUB_GMS_URL / DATAHUB_GMS_TOKEN, falling back to whatever `datahub init`
    wrote to ~/.datahubenv.
    """
    from datahub.sdk import DataHubClient
    server = os.environ.get("DATAHUB_GMS_URL")
    token = os.environ.get("DATAHUB_GMS_TOKEN")
    if server:
        return DataHubClient(server=server, token=token)
    return DataHubClient.from_env()


def _keys() -> List[str]:
    """Mistral keys from env. Multiple keys are pooled and rotated on 429."""
    raw = os.environ.get("MISTRAL_API_KEY", "")
    keys = [k.strip() for k in raw.replace(";", ",").split(",") if k.strip()]
    return keys


def _p(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def cmd_ask(args) -> int:
    keys = _keys()
    if not keys:
        _p("ERROR: set MISTRAL_API_KEY (comma-separate several to pool them).")
        return 2

    from datahub_agent_context import DataHubContext

    urn: Optional[str] = args.urn
    briefing = ""
    related: List[str] = []

    with DataHubContext(_client()):
        who = dh.connected()
        if not who["ok"]:
            _p(f"ERROR: cannot reach DataHub - {who['error']}")
            _p("Set DATAHUB_GMS_URL and DATAHUB_GMS_TOKEN, or run: datahub docker quickstart")
            return 1
        _p("connected to DataHub")

        if not urn and args.asset:
            hits = dh.find_asset(args.asset)
            if not hits:
                _p(f"ERROR: no asset matched {args.asset!r}")
                return 1
            urn = hits[0]["urn"]
            _p(f"asset:  {urn}")
            if len(hits) > 1:
                # Say which others matched. Silently picking the top hit and
                # ruling on the wrong table is the worst failure this tool has.
                _p("        (also matched: " +
                   ", ".join(h["urn"].split(",")[-2] if "," in h["urn"] else h["urn"]
                             for h in hits[1:4]) + ")")

        if urn:
            related = [urn]
            prior = dh.prior_decisions(args.question, urn)
            if prior:
                _p("PRIOR DECISIONS already in DataHub:")
                for d in prior[:3]:
                    _p(f"  - {d['title']}")
                if not args.force:
                    _p("")
                    _p("Refusing to re-litigate. Re-run with --force to argue anyway.")
                    return 0
            ctx = dh.gather_context(urn)
            briefing = dh.as_briefing(ctx)
            _p("")
            _p("--- DataHub briefing given to the panel ---")
            _p(briefing)
            _p("-------------------------------------------")
        else:
            _p("no asset given - the panel will argue ungrounded")

        _p("")
        _p("convening the panel...")

        captured = []

        def show(ev):
            if args.record:
                captured.append(ev)
            k = ev.get("kind")
            if k == "answer" and ev.get("ok"):
                _p(f"  {ev['name']:<9} {ev['text'][:96]}")
            elif k == "scores":
                _p(f"  scored: {ev['scores']}  spread={ev['spread']}  "
                   f"agreement={ev['agreement']}")
            elif k == "round" and ev.get("round") == 2:
                _p(f"  refining: {', '.join(ev.get('revising') or [])}")

        res = panel.deliberate(args.question, briefing, keys, on_event=show)
        if not res.get("ok"):
            _p(f"ERROR: {res.get('error')}")
            return 1

        _p("")
        _p("=== RULING ===")
        _p(res["ruling"])
        if res.get("dissent"):
            _p("")
            _p("dissent: " + res["dissent"])
        _p("")
        _p(f"[{res['case']} after round {res['rounds']} | agreement {res['agreement']} "
           f"| {res['calls']} calls | {res['tokens']} tokens | {res['seconds']}s]")

        if args.dry_run:
            _p("")
            _p("--dry-run: nothing written back to DataHub.")
            return 0
        if not related:
            _p("")
            _p("no asset resolved, so nothing to attach the decision to. Not writing.")
            return 0

        if args.record:
            # Capture the briefing too - a replay that skips it would show the panel
            # arguing from facts the viewer never saw, which is the one thing this
            # console exists to make visible.
            events = ([{"kind": "briefing", "t": 0, "urn": urn, "text": briefing}]
                      if briefing else [])
            events += captured
            Path(args.record).parent.mkdir(parents=True, exist_ok=True)
            Path(args.record).write_text(json.dumps(events, indent=1), encoding="utf-8")
            _p(f"recorded {len(events)} events -> {args.record}")

        _p("")
        _p("writing decision back to DataHub...")
        out = dh.record_decision(
            question=args.question,
            verdict=res["ruling"],
            reasoning=panel.reasoning_markdown(res),
            related_assets=related,
            tag=args.tag,
        )
        if out.get("ok"):
            _p("  saved as a DataHub Decision document, linked to the asset.")
            _p("  the next person or agent that looks at this asset inherits it.")
        else:
            _p("  PARTIAL: " + "; ".join(out.get("errors") or ["unknown"]))
        if args.json:
            _p(json.dumps({"ruling": res["ruling"], "dissent": res.get("dissent"),
                           "agreement": res["agreement"], "writeback": out}, indent=1))
    return 0


def cmd_check(args) -> int:
    from datahub_agent_context import DataHubContext
    with DataHubContext(_client()):
        who = dh.connected()
        _p(f"datahub: {'ok' if who['ok'] else 'FAILED - ' + str(who.get('error'))[:120]}")
        if who["ok"]:
            _p(f"  as: {json.dumps(who['me'])[:160]}")
    _p(f"mistral keys: {len(_keys())}")
    return 0 if who["ok"] and _keys() else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tribunal", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")

    a = sub.add_parser("ask", help="argue a data decision and record the ruling")
    a.add_argument("question")
    a.add_argument("--asset", help="asset name to resolve in DataHub, e.g. fct_orders")
    a.add_argument("--urn", help="exact DataHub urn (skips resolution)")
    a.add_argument("--tag", help="tag to apply to the asset, e.g. Deprecated")
    a.add_argument("--dry-run", action="store_true", help="argue but write nothing")
    a.add_argument("--force", action="store_true", help="argue even if already decided")
    a.add_argument("--json", action="store_true", help="also emit machine-readable result")
    a.add_argument("--record", metavar="PATH",
                   help="capture the event stream to PATH for `serve --demo` replay")
    a.set_defaults(fn=cmd_ask)

    c = sub.add_parser("check", help="verify DataHub and model access")
    c.set_defaults(fn=cmd_check)

    w = sub.add_parser("serve", help="run the web console")
    w.add_argument("--port", type=int, default=8077)
    w.add_argument("--demo", action="store_true",
                   help="replay a recorded deliberation; needs no DataHub and no keys")
    w.set_defaults(fn=lambda a: __import__("tribunal.web", fromlist=["serve"]).serve(a.port, a.demo))

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        _p("\ninterrupted.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
