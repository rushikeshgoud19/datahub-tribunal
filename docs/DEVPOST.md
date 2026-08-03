# Devpost submission copy

Paste-ready. Fields match the submission form.

---

## Project name (60 char limit)

```
Tribunal — adversarial review for data decisions
```
*(47 characters)*

## Elevator pitch (200 char limit)

```
Four AI advocates argue an irreversible data decision on different models, a held-out judge rules, and the reasoning is written back to DataHub so the next person or agent inherits it.
```
*(181 characters)*

---

## About the project

### Inspiration

Someone asks in Slack: *can we deprecate `fct_orders`?* Three people weigh in, the most
confident one wins, the table gets dropped. Six weeks later a dashboard breaks and nobody can
reconstruct why the call was made.

The decision survived. The reasoning evaporated with the thread.

Catalogs are very good at recording **what** a data asset is and **what happened to it**. They
are much worse at recording **why someone decided to change it** — which is the thing you
actually need six months later, when you are deciding whether that reasoning still applies.

### What it does

Tribunal turns an irreversible data question into an argument with a record.

1. **Resolves** the asset in DataHub.
2. **Recalls** any prior ruling. If the question is already settled, it refuses to argue — a
   panel that re-litigates a settled question just produces a confident second opinion nobody
   asked for.
3. **Grounds** the debate in what DataHub actually knows: lineage two hops downstream, owners,
   schema, recorded queries.
4. **Argues.** Four advocates on four different Mistral models, each under a different stance —
   *who breaks?* / *what does the metadata say?* / *smallest safe change* / *cost of being
   wrong* — answering **in isolation** so the panel cannot collapse into one view.
5. **Judges.** A held-out judge scores each 0–10 and names the specific defect in each.
6. **Rules** — in code, not in the prompt.
7. **Writes back** the ruling *and the argument* as a native DataHub `Decision` document linked
   to the asset, stamps the asset description, and tags it.

The next person who opens that asset sees the ruling. So does the next agent, because it is
metadata rather than a wiki page.

### How we built it

Python, the DataHub **Agent Context Kit**, and Mistral. The web console is the standard
library and one HTML file — no framework, no build step — because a judge should be able to
install one package and see it work.

Two design decisions did most of the work:

**The judge scores; code decides.** In an 18-question soak where the *judge* owned the
outcome, it chose "adopt" **18 times out of 18**, including on ten deliberately contentious
questions. With four competent models, at least one answer is always defensible, so the
refinement path was dead code. Moving the threshold into Python fixed it: on the same
questions, 25 of 28 now go to refinement.

**The dissent is preserved.** A ruling with the objection stripped out reads as more
authoritative and is far less useful. The surviving objection is what tells the next engineer
whether the reasoning applies to *their* case.

### Challenges we ran into

Most of them were the gap between "the API returned success" and "the thing actually happened":

- `update_description` writes to `editableProperties.description`, not
  `properties.description`. We verified writes against the wrong field for a while and
  concluded, wrongly, that they were failing silently.
- `add_tags` does not create a missing tag — you must `createTag` first, and every subsequent
  run then errors *"This Tag already exists!"*, which has to be treated as success.
- Newly saved documents take time to reach the search index, so our "have we already decided
  this?" check silently never fired — and it failed **open**, so the panel happily re-argued
  settled questions.
- Document titles live at `entity.info.title`, unlike most other entities.

Every one of those was a *silent* failure. That is also why Tribunal reports what it could not
read: a panel arguing from partial metadata should know it is partial.

### What we learned

Adversarial review only works if you engineer the disagreement. Four copies of one model at
one temperature produce four near-identical answers, unanimous agreement, and a completely
false sense of confidence. Different models, different assigned stances, and isolation in the
first round are what make the panel worth paying for.

And the expensive part of a decision is not making it. It is making it again.

### What's next for Tribunal

- Column-level rulings using `get_lineage(column=...)`.
- Batch review — walk a domain and flag assets whose decisions rest on metadata that has since
  changed.
- Push rulings into the PR that proposes the schema change, so the argument arrives where the
  change is reviewed.

---

## Built with

```
python, datahub, datahub-agent-context, mistral-ai, server-sent-events,
docker, wsl2, html, css, javascript
```

## "Try it out" links

```
https://github.com/rushikeshgoud19/datahub-tribunal
```

---

## Additional info (judges only)

**Challenge category:** Agents That Do Real Work

**Public repo:** `https://github.com/rushikeshgoud19/datahub-tribunal` (Apache-2.0 at root)

**URL for judges to test:**
```
https://github.com/rushikeshgoud19/datahub-tribunal

pip install -e .
tribunal serve --demo      →  http://localhost:8077
```
`--demo` replays a real deliberation captured against a live DataHub instance, at its original
pace, with **no DataHub instance and no API keys required**. The UI states that it is a
recording — it is not a simulation presented as live work.

**Artifacts:** `examples/recorded_deliberation.json` is the full captured event stream of a
real run — every advocate position, every score, the judge's defects, and the write-back.

**DataHub technologies used:** Agent Context Kit · MCP tools (`search`, `get_entities`,
`get_lineage`, `list_schema_fields`, `get_dataset_queries`, `search_documents`,
`save_document`, `update_description`, `add_tags`) · GraphQL API · DataHub CLI &
`datapack load showcase-ecommerce`

**Newly created during the submission period:** Yes — with disclosure below.

**Pre-existing code disclosure:**
The deliberation engine (`src/tribunal/panel.py`) is adapted from an "Agent Orchestra" module
the author wrote for a personal assistant project before the hackathon. What carried over is
the shape: parallel advocates, a scoring judge, a code-owned adopt/refine threshold, and a
Mistral key-pool that parks a key on HTTP 429. Everything that makes this a DataHub project
was written during the submission window — `datahub_context.py` (read/recall/write-back), the
four data-specific stances, the briefing format, the web console, the CLI, and the write-back
into `Decision` documents. This is also stated in the README.

**Open-source contribution:** the README carries a *"notes for anyone building on the Agent
Context Kit"* section documenting five undocumented behaviours we hit, with the measured
detail needed to reproduce them.
