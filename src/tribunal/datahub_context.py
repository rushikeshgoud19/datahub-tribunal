"""DataHub read/write layer for Tribunal.

Tribunal answers irreversible data questions - "should we deprecate fct_orders?",
"is it safe to drop this column?" - by making four advocates argue them and having
a judge rule. This module is the half that talks to DataHub:

  READ   the decision is grounded in what DataHub actually knows about the asset:
         its schema, who owns it, what feeds it, and - the part that usually
         decides the answer - what breaks downstream if it goes away.

  RECALL before arguing, look for a Decision document about the same asset. A
         panel that re-litigates a question already settled last quarter is worse
         than useless; it produces a confident second opinion nobody asked for.

  WRITE  the verdict AND the reasoning go back into DataHub as a native `Decision`
         document linked to the affected assets, plus a tag on the asset itself.
         The reasoning is the point. An outcome without it ("deprecated") tells the
         next engineer what happened; the argument tells them whether it still
         applies to their situation.

Everything here is best-effort: DataHub being unreachable degrades the answer to an
ungrounded one, it never takes the tribunal down. Every function returns a plain
dict/str and raises nothing.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("tribunal.datahub")

# Imported lazily inside functions. The kit pulls in the datahub SDK, which is
# heavy and prints an ExperimentalWarning on import - neither belongs in the cost
# of importing this module, and a missing install should degrade rather than crash.
_TAG_PREFIX = "urn:li:tag:"


def _tools():
    from datahub_agent_context import mcp_tools  # noqa: WPS433
    return mcp_tools


def connected() -> Dict[str, Any]:
    """Who are we talking to, if anyone. Used by the CLI to fail loudly at startup."""
    try:
        me = _tools().get_me()
        return {"ok": True, "me": me}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def find_asset(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Resolve a human phrase ("fct_orders") to real DataHub entities."""
    try:
        res = _tools().search(query=query, num_results=limit)
        items = res.get("searchResults") or res.get("results") or []
        out = []
        for it in items:
            ent = it.get("entity") or it
            urn = ent.get("urn") or it.get("urn")
            if urn:
                out.append({"urn": urn, "type": ent.get("type", ""),
                            "name": ent.get("name") or ent.get("properties", {}).get("name", "")})
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("search failed: %s", e)
        return []


def gather_context(urn: str, max_hops: int = 2) -> Dict[str, Any]:
    """Everything the panel should know before it argues about `urn`.

    Downstream lineage is fetched with more hops than upstream on purpose. For a
    deprecation or schema-change question, "what depends on this" is the fact that
    decides the answer; "what feeds this" is usually context.
    """
    t = _tools()
    ctx: Dict[str, Any] = {"urn": urn, "errors": []}

    def attempt(key, fn):
        try:
            ctx[key] = fn()
        except Exception as e:  # noqa: BLE001
            ctx[key] = None
            ctx["errors"].append(f"{key}: {type(e).__name__}: {e}")

    attempt("entity", lambda: (t.get_entities([urn]) or [None])[0])
    attempt("downstream", lambda: t.get_lineage(urn=urn, upstream=False,
                                                max_hops=max_hops, max_results=40))
    attempt("upstream", lambda: t.get_lineage(urn=urn, upstream=True,
                                              max_hops=1, max_results=20))
    attempt("fields", lambda: t.list_schema_fields(urn=urn, limit=60))
    attempt("queries", lambda: t.get_dataset_queries(urn=urn, count=5))
    return ctx


def _count(node: Any) -> int:
    """Lineage payload shape varies by entity type; count defensively."""
    if not isinstance(node, dict):
        return 0
    for key in ("total", "count"):
        if isinstance(node.get(key), int):
            return node[key]
    for key in ("results", "relationships", "entities"):
        v = node.get(key)
        if isinstance(v, list):
            return len(v)
    return 0


def _names(node: Any, limit: int = 8) -> List[str]:
    if not isinstance(node, dict):
        return []
    rows = node.get("results") or node.get("relationships") or node.get("entities") or []
    out = []
    for r in rows[:limit]:
        e = r.get("entity") if isinstance(r, dict) else None
        e = e or r
        if isinstance(e, dict):
            out.append(str(e.get("name") or e.get("urn") or "")[:90])
    return [x for x in out if x]


def as_briefing(ctx: Dict[str, Any], max_chars: int = 1800) -> str:
    """Render the DataHub facts as text the advocates can actually argue over.

    Deliberately terse and factual. The panel is asked to reason about the asset,
    not to admire a JSON dump - and every token spent on formatting is one not
    spent on the argument.
    """
    ent = ctx.get("entity") or {}
    props = ent.get("properties") or {}
    # DataHub keeps INGESTED metadata in `properties` and every human/API edit in
    # `editableProperties`. Reading only the former shows a blank asset even when
    # somebody has documented it, so prefer the editable overlay and fall back.
    editable = ent.get("editableProperties") or {}
    desc = editable.get("description") or props.get("description") or ""
    lines = [f"ASSET: {ctx.get('urn')}"]
    if props.get("name"):
        lines.append(f"name: {props['name']}")
    if desc:
        lines.append(f"documented as: {str(desc)[:400]}")

    owners = ent.get("ownership") or {}
    owner_names = _names(owners, 5) or []
    lines.append(f"owners: {', '.join(owner_names) if owner_names else 'NONE RECORDED'}")

    down_n, up_n = _count(ctx.get("downstream")), _count(ctx.get("upstream"))
    lines.append(f"downstream dependents: {down_n}")
    dn = _names(ctx.get("downstream"))
    if dn:
        lines.append("  depends on it: " + "; ".join(dn))
    lines.append(f"upstream sources: {up_n}")

    fields = ctx.get("fields") or {}
    frows = fields.get("fields") or fields.get("results") or []
    if frows:
        names = [str((f or {}).get("fieldPath") or (f or {}).get("name") or "")[:40]
                 for f in frows[:25]]
        lines.append(f"columns ({len(frows)}): " + ", ".join(n for n in names if n))

    q = ctx.get("queries") or {}
    qrows = q.get("queries") or q.get("results") or []
    if qrows:
        lines.append(f"recent queries recorded: {len(qrows)}")

    if ctx.get("errors"):
        # State what could NOT be read. A panel arguing from partial metadata
        # should know it is partial, rather than treating silence as "nothing there".
        lines.append("NOT AVAILABLE: " + "; ".join(ctx["errors"])[:200])

    return "\n".join(lines)[:max_chars]


_Q_STOP = {"should", "we", "the", "a", "an", "is", "are", "it", "this", "that", "to",
           "of", "for", "in", "on", "do", "can", "our", "be", "and", "or", "if",
           "safe", "ok", "okay", "still", "any", "how", "what", "why", "table"}


def prior_decisions(question: str, urn: Optional[str] = None,
                    limit: int = 5) -> List[Dict[str, Any]]:
    """Decision documents already in DataHub that may settle this question.

    MEASURED 2026-08-03: keyword `search_documents(query="deprecate")` returns 0 on
    a freshly written document while `query="*"` returns it immediately - the
    document body is not full-text searchable straight away. Relying on keyword
    search meant this check silently never fired, and it failed OPEN: the panel
    happily re-argued a settled question and produced a confident second opinion.

    So: list documents, then match on the TITLE, which we control (every ruling is
    titled "Decision: <question>"). Less clever, actually works.
    """
    try:
        res = _tools().search_documents(query="*", num_results=100)
        rows = res.get("documents") or res.get("results") or res.get("searchResults") or []
    except Exception as e:  # noqa: BLE001
        log.warning("document listing failed: %s", e)
        return []

    terms = {w for w in re.findall(r"[a-z0-9_]{3,}", question.lower()) if w not in _Q_STOP}
    if not terms:
        return []

    out = []
    for r in rows:
        d = r.get("entity") or r
        # Documents carry their title at entity.info.title - NOT .title or
        # .properties.title, which is where most other DataHub entities keep it.
        # Reading the wrong path returned an empty title for every row, so this
        # check silently found nothing and the panel re-argued settled questions.
        info = d.get("info") or {}
        props = d.get("properties") or {}
        title = str(info.get("title") or props.get("title") or d.get("title") or "")
        if not title:
            continue
        hay = title.lower()
        hits = sum(1 for t in terms if t in hay)
        # Two shared significant terms, or one when the question is that short.
        if hits >= min(2, len(terms)):
            out.append({"urn": d.get("urn", ""), "title": title[:160], "overlap": hits})
    out.sort(key=lambda x: -x["overlap"])
    return out[:limit]


def _ensure_tag(tag_urn: str, name: str) -> None:
    """Create the tag if DataHub does not already know it.

    Uses the SDK graph directly: the agent-context kit exposes add_tags but no
    create_tag, and applying a tag that has never been defined is rejected.
    """
    from datahub_agent_context import get_graph
    graph = get_graph()
    try:
        graph.execute_graphql(
            """mutation createTag($input: CreateTagInput!) { createTag(input: $input) }""",
            variables={"input": {"id": name, "name": name,
                                 "description": "Applied by Tribunal after a panel ruling."}},
        )
    except Exception as e:  # noqa: BLE001
        # "already exists" is the SUCCESS case on every run after the first. Letting
        # it surface as an error made a healthy write-back report PARTIAL forever.
        if "already exists" not in str(e).lower():
            raise


def record_decision(question: str, verdict: str, reasoning: str,
                    related_assets: Optional[List[str]] = None,
                    tag: Optional[str] = None) -> Dict[str, Any]:
    """Write the ruling back so the next person or agent inherits it.

    Saved as DataHub's native `Decision` document type and linked to the assets it
    concerns, so it surfaces from the asset page and from Ask DataHub - not only to
    whoever happened to run the tribunal.

    The document carries the ARGUMENT, not just the outcome. "Deprecated" tells the
    next engineer what was done; the dissent tells them whether the reasoning still
    holds in their case, which is the part that stops a decision being re-litigated
    from scratch every quarter.
    """
    t = _tools()
    result: Dict[str, Any] = {"document": None, "tag": None, "errors": []}
    body = (
        f"## Question\n{question}\n\n"
        f"## Ruling\n{verdict}\n\n"
        f"## How the panel reached it\n{reasoning}\n\n"
        f"---\n*Recorded by Tribunal - four advocates argued this on independent "
        f"models and a held-out judge ruled. The dissent is preserved above on "
        f"purpose: it is what tells you whether this ruling still applies to your "
        f"situation.*"
    )
    try:
        result["document"] = t.save_document(
            document_type="Decision",
            title=("Decision: " + question.strip())[:200],
            content=body,
            related_assets=related_assets or [],
        )
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"save_document: {type(e).__name__}: {e}")

    # Also stamp a pointer onto the ASSET's own description.
    #
    # MEASURED 2026-08-03 on DataHub v1.5.0.6 quickstart: save_document returns
    # success with a real urn, and get_entities can fetch that urn back - but the
    # document never enters the search index. Verified straight against GraphQL:
    # searchAcrossEntities(types:[DOCUMENT]) reports total=4, only the datapack's
    # own documents, never ours. A ruling nobody can find is not inherited
    # knowledge, which is the entire promise here, so the asset description gets a
    # short pointer too. That IS indexed and it is the first thing a human opens.
    doc_urn = ""
    if isinstance(result["document"], dict):
        doc_urn = str(result["document"].get("urn") or "")
    if related_assets:
        stamp = (f"\n\n---\n**Tribunal ruling ({time.strftime('%Y-%m-%d')}):** "
                 f"{verdict.strip()[:400]}"
                 + (f"\n\nFull argument: `{doc_urn}`" if doc_urn else ""))
        for asset in related_assets:
            try:
                t.update_description(entity_urn=asset, operation="append",
                                     description=stamp)
                result["description_appended"] = True
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"update_description: {type(e).__name__}: {e}")

    if tag and related_assets:
        # add_tags does NOT create a missing tag: batchAddTags validates the urn and
        # fails with "Urn does not exist". Measured on v1.5.0.6. So mint it first.
        urn = tag if tag.startswith(_TAG_PREFIX) else _TAG_PREFIX + tag
        try:
            _ensure_tag(urn, tag)
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"create_tag: {type(e).__name__}: {e}")
        try:
            result["tag"] = t.add_tags(tag_urns=[urn], entity_urns=related_assets)
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"add_tags: {type(e).__name__}: {e}")

    result["document_urn"] = doc_urn
    result["ok"] = bool(result["document"]) and not result["errors"]
    return result
