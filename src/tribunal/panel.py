"""The panel: four advocates argue, a held-out judge rules, code decides.

Adapted from the Agent Orchestra engine (see DISCLOSURE in README). The changes
that matter for data decisions:

  * grounding is DataHub metadata, not web search - lineage and ownership are the
    facts that settle "can we deprecate this", and unlike a web result they are
    authoritative for THIS warehouse.
  * the stances are re-cut for data work. "Who breaks?" replaces "attack the
    premise", because in a catalog the decisive question is almost always who is
    downstream of the thing you want to change.
  * the verdict is written back to DataHub rather than printed.

DESIGN NOTE, learned the hard way: the judge SCORES and names defects, but CODE
decides adopt-vs-refine. When the judge was asked to decide, it chose "adopt" in
18 of 18 soak runs including ten deliberately contentious questions, because with
four competent models at least one answer is always defensible. The refinement
path was dead code until the threshold moved into Python.

READ-ONLY BY CONSTRUCTION: advocates never call tools. They receive text and
return text. Only `datahub_context.record_decision` writes, and only after a
ruling exists.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_JUDGE = "mistral-medium-latest"

# Adopt only if the best answer is one you would ship unchanged AND the panel is
# not split. Both live here rather than in a prompt - see the design note above.
ADOPT_MIN_SCORE = 9.0
ADOPT_SPREAD = 2.0
REFINE_TOP_N = 2

PANEL: List[Dict[str, str]] = [
    {"id": "impact", "name": "Impact", "model": "ministral-8b-latest",
     "stance": "WHO BREAKS?",
     "brief": "Method: name the downstream consumers in the lineage above and say "
              "concretely what fails for each if this change ships. If nothing "
              "consumes it, say so plainly - an unused asset is a different decision."},
    {"id": "evidence", "name": "Evidence", "model": "magistral-small-latest",
     "stance": "WHAT DOES THE METADATA SAY?",
     "brief": "Method: argue only from the DataHub facts supplied. Quote counts, "
              "owners, column names. Where the metadata is silent or missing, say "
              "WHICH fact is missing rather than filling the gap with a guess."},
    {"id": "minimal", "name": "Minimal", "model": "mistral-small-latest",
     "stance": "SMALLEST SAFE CHANGE",
     "brief": "Method: propose the least disruptive action that satisfies the "
              "request - deprecate rather than delete, alias rather than rename, "
              "stage rather than cut over. Say what is lost by going smaller."},
    {"id": "cost", "name": "Cost", "model": "ministral-14b-latest",
     "stance": "COST OF BEING WRONG",
     "brief": "Method: price both mistakes. What does acting wrongly cost, and what "
              "does NOT acting cost? Separate reversible from irreversible - that "
              "distinction usually decides the answer on its own."},
]

_ADVOCATE_SYS = (
    "You are {name}, one of four independent advocates advising on a data-platform "
    "decision. YOUR ASSIGNED STANCE: {stance}. {brief}\n"
    "Argue your stance honestly. If the straightforward answer is simply right, say "
    "so - a manufactured objection wastes the panel's time.\n"
    "FORMAT: plain prose, 120 words maximum. No markdown headings, no bullet lists."
)

_JUDGE_SYS = (
    "You are the presiding judge reviewing four independent advocates on a data "
    "decision.\n"
    "Score each 0-10 on whether their position is correct, complete, and free of "
    "objections the others raised and they ignored. Be harsh: 9+ means you would "
    "act on this unchanged. Merely reasonable is a 5-6.\n"
    "Name the single most important DEFECT in each - real and specific, or empty "
    "string if genuinely none.\n"
    "Reply as STRICT JSON, nothing else:\n"
    '{"scores":{"<id>":<0-10>,...},"defects":{"<id>":"<defect>",...},'
    '"best":"<id>","improved":"<the best position rewritten with your own '
    'improvements folded in, plain prose, under 200 words>"}'
)

_FINAL_SYS = (
    "You are the presiding judge delivering the ruling after revisions.\n"
    "Synthesise the revised positions into ONE ruling a data engineer can act on. "
    "State the action, the condition under which it is safe, and the strongest "
    "dissent that survives. Reply as STRICT JSON, nothing else:\n"
    '{"agreement":"HIGH|LOW","ruling":"<under 250 words>",'
    '"dissent":"<the objection a future reader must know, under 80 words>"}'
)


class KeyPool:
    """Round-robins Mistral keys, parking any that reports 429.

    A rate-limited judge stalls the whole panel, so a key that 429s is set aside
    for a cooldown instead of being retried into the ground.
    """

    def __init__(self, keys: List[str]):
        self._keys = [k for k in keys if k]
        self._i = 0
        self._cool: Dict[str, float] = {}
        self._lock = threading.Lock()

    def take(self) -> Optional[str]:
        with self._lock:
            now = time.time()
            for _ in range(len(self._keys)):
                k = self._keys[self._i % len(self._keys)]
                self._i += 1
                if self._cool.get(k, 0) <= now:
                    return k
            return None

    def park(self, key: str, seconds: float = 45.0):
        with self._lock:
            self._cool[key] = time.time() + seconds


def _call(pool: KeyPool, model: str, system: str, user: str,
          temperature: float = 0.3, max_tokens: int = 700,
          attempts: int = 3) -> Dict[str, Any]:
    """One completion. Returns {ok,text,truncated,tokens,error}; never raises."""
    last = "no key available"
    for _ in range(attempts):
        key = pool.take()
        if not key:
            time.sleep(1.5)
            continue
        body = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature, "max_tokens": max_tokens,
        }).encode()
        req = urllib.request.Request(
            MISTRAL_URL, data=body,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.loads(r.read())
            ch = d["choices"][0]
            txt = (ch["message"]["content"] or "").strip()
            # finish_reason is the authoritative truncation signal; guessing from
            # the tail mis-flags anything that legitimately ends in punctuation.
            return {"ok": bool(txt), "text": txt,
                    "truncated": ch.get("finish_reason") == "length",
                    "tokens": d.get("usage", {}).get("total_tokens", 0), "error": ""}
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 429:
                pool.park(key)
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            continue
    return {"ok": False, "text": "", "truncated": False, "tokens": 0, "error": last}


def _json_from(text: str) -> Optional[dict]:
    """Models wrap JSON in prose or fences however firmly you ask them not to."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
    return None


def deliberate(question: str, briefing: str, api_keys: List[str],
               judge_model: str = DEFAULT_JUDGE,
               on_event: Optional[Callable[[dict], None]] = None) -> Dict[str, Any]:
    """Run the panel over a data question grounded in `briefing`.

    `briefing` is DataHub's account of the asset, produced by
    datahub_context.as_briefing(). Every advocate sees the SAME facts: private
    research per advocate would multiply cost and let them argue from different
    private evidence, which is worse than arguing from none.
    """
    started = time.time()
    pool = KeyPool(api_keys)
    stats = {"calls": 0, "tokens": 0, "truncated": 0}
    transcript: List[Dict[str, Any]] = []

    def emit(kind, **kw):
        ev = {"kind": kind, "t": round(time.time() - started, 2),
              "calls": stats["calls"], "tokens": stats["tokens"], **kw}
        transcript.append(ev)
        if on_event:
            try:
                on_event(ev)
            except Exception:  # noqa: BLE001
                pass  # a broken listener must never kill the deliberation

    if not pool._keys:  # noqa: SLF001
        return {"ok": False, "error": "no Mistral API keys configured",
                "transcript": transcript}

    def run(model, sys_p, user, temp=0.3, mx=700):
        r = _call(pool, model, sys_p, user, temp, mx)
        stats["calls"] += 1
        stats["tokens"] += r.get("tokens", 0)
        if r.get("truncated"):
            stats["truncated"] += 1
        return r

    ground = f"DATAHUB SAYS:\n{briefing}\n\n" if briefing else ""
    emit("round", round=0, phase="fan-out",
         advocates=[{k: p[k] for k in ("id", "name", "model", "stance")} for p in PANEL],
         grounded=bool(briefing))

    def ask(p):
        sys_p = _ADVOCATE_SYS.format(**p)
        return p, run(p["model"], sys_p, ground + f"QUESTION: {question}", temp=0.4)

    answers: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(PANEL)) as ex:
        for p, r in ex.map(ask, PANEL):
            if r["ok"]:
                answers[p["id"]] = r["text"]
            emit("answer", id=p["id"], name=p["name"], ok=r["ok"],
                 text=r["text"][:900], error=r["error"])

    if len(answers) < 2:
        return {"ok": False, "error": f"only {len(answers)} advocate(s) answered",
                "calls": stats["calls"], "tokens": stats["tokens"],
                "transcript": transcript}

    by_id = {p["id"]: p for p in PANEL}

    def block(d: Dict[str, str], clip: int = 10000) -> str:
        return "\n\n".join(
            f'[{by_id[i]["name"]} - {by_id[i]["stance"]} - id={i}]\n{t[:clip]}'
            for i, t in d.items())

    # ---- judge scores, code decides -------------------------------------
    emit("round", round=1, phase="review", judge=judge_model)
    r = run(judge_model, _JUDGE_SYS,
            ground + f"QUESTION: {question}\n\nPOSITIONS:\n{block(answers)}",
            temp=0.2, mx=1000)
    review = _json_from(r["text"]) or {}
    scores: Dict[str, float] = {}
    for k, v in (review.get("scores") or {}).items():
        if k in answers:
            try:
                scores[k] = max(0.0, min(10.0, float(v)))
            except (TypeError, ValueError):
                pass
    defects = {k: str(v) for k, v in (review.get("defects") or {}).items()
               if k in answers and str(v).strip()}

    if scores:
        best = max(scores, key=lambda k: scores[k])
        spread = max(scores.values()) - min(scores.values())
    else:
        # No usable scores means no evidence to adopt on. Escalate rather than
        # rubber-stamp: escalating costs tokens, adopting blind costs correctness.
        best, spread = None, 10.0
    agreement = "HIGH" if spread <= ADOPT_SPREAD else "LOW"
    emit("scores", scores=scores, defects={k: v[:200] for k, v in defects.items()},
         best=best, spread=round(spread, 1), agreement=agreement)

    if best and scores[best] >= ADOPT_MIN_SCORE and agreement == "HIGH" \
            and review.get("improved"):
        return _done(question, review["improved"], "", agreement, 1, stats,
                     "ADOPT", judge_model, started, transcript, scores, defects, emit)

    # ---- refine: only the contenders redraft ----------------------------
    critiques = dict(defects)
    for i in answers:
        critiques.setdefault(i, "Strengthen your weakest claim and make it specific.")
    emit("critique", agreement=agreement, critiques={k: v[:400] for k, v in critiques.items()})

    ranked = sorted(answers, key=lambda i: scores.get(i, 0), reverse=True)
    contenders = set(ranked[:REFINE_TOP_N])
    emit("round", round=2, phase="refine", revising=[i for i in ranked if i in contenders])
    peers = block(answers, clip=420)

    def revise(p):
        if p["id"] not in contenders:
            return p, {"ok": False, "text": "", "tokens": 0, "error": ""}
        user = (ground + f"QUESTION: {question}\n\nTHE JUDGE'S CRITIQUE OF YOUR "
                f"POSITION:\n{critiques.get(p['id'], '')}\n\nALL POSITIONS "
                f"(you may now see them):\n{peers}\n\nGive your revised position. "
                "Keep your stance. Under 120 words.")
        return p, run(p["model"], _ADVOCATE_SYS.format(**p), user, temp=0.35)

    revised: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(PANEL)) as ex:
        for p, rr in ex.map(revise, PANEL):
            if rr["ok"]:
                revised[p["id"]] = rr["text"]
                emit("revision", id=p["id"], name=p["name"], ok=True, text=rr["text"][:900])

    final_pool = {**answers, **revised}
    r = run(judge_model, _FINAL_SYS,
            ground + f"QUESTION: {question}\n\nREVISED POSITIONS:\n{block(final_pool)}",
            temp=0.25, mx=1100)
    fin = _json_from(r["text"]) or {}
    ruling = fin.get("ruling") or r["text"] or "The panel could not reach a ruling."
    return _done(question, ruling, fin.get("dissent", ""),
                 str(fin.get("agreement", agreement)).upper(), 2, stats,
                 "SYNTHESISE", judge_model, started, transcript, scores, defects, emit)


def _done(question, ruling, dissent, agreement, rounds, stats, case,
          judge, started, transcript, scores, defects, emit):
    emit("verdict", case=case, agreement=agreement, ruling=ruling, dissent=dissent)
    return {"ok": True, "question": question, "ruling": ruling, "dissent": dissent,
            "agreement": agreement, "rounds": rounds, "case": case, "judge": judge,
            "calls": stats["calls"], "tokens": stats["tokens"],
            "truncated": stats["truncated"], "scores": scores, "defects": defects,
            "seconds": round(time.time() - started, 1), "transcript": transcript}


def reasoning_markdown(res: Dict[str, Any]) -> str:
    """The argument, formatted for the Decision document written back to DataHub.

    Deliberately includes the losing positions and their scores. A ruling with the
    dissent stripped out looks more authoritative and is far less useful: the next
    engineer needs to know what was argued against it to judge whether it still
    applies to their case.
    """
    by_id = {p["id"]: p for p in PANEL}
    lines = []
    if res.get("dissent"):
        lines.append(f"**Strongest surviving objection:** {res['dissent']}\n")
    lines.append(f"**How it was decided:** {res.get('case')} after round "
                 f"{res.get('rounds')}, panel agreement {res.get('agreement')} "
                 f"({res.get('calls')} model calls, {res.get('tokens')} tokens).\n")
    lines.append("**Positions argued:**\n")
    scores, defects = res.get("scores") or {}, res.get("defects") or {}
    seen = set()
    for ev in res.get("transcript") or []:
        if ev.get("kind") not in ("answer", "revision") or not ev.get("ok"):
            continue
        key = (ev["id"], ev["kind"])
        if key in seen:
            continue
        seen.add(key)
        p = by_id.get(ev["id"], {})
        sc = scores.get(ev["id"])
        head = f"- **{p.get('name', ev['id'])}** ({p.get('stance', '')})"
        if sc is not None:
            head += f" - scored {sc:g}/10"
        if ev["kind"] == "revision":
            head += " *(revised)*"
        lines.append(head + f": {ev['text']}")
        if defects.get(ev["id"]) and ev["kind"] == "answer":
            lines.append(f"  - *judge noted:* {defects[ev['id']]}")
    return "\n".join(lines)
