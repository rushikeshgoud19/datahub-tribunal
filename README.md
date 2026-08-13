<div align="center">

# Tribunal

**Adversarial review for irreversible data decisions — grounded in DataHub, written back to DataHub.**

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![DataHub](https://img.shields.io/badge/DataHub-Agent%20Context%20Kit-1890FF)
![Dependencies](https://img.shields.io/badge/dependencies-one-brightgreen)
![Build](https://img.shields.io/badge/build%20step-none-brightgreen)
![License](https://img.shields.io/badge/License-Apache%202.0-blue)

*One model answering confidently is the failure mode, not the feature.*

</div>

---

Someone asks *"can we deprecate `fct_orders`?"* in Slack. Three people weigh in, one of them
is confident, the table gets dropped. Six weeks later a dashboard breaks and nobody can
reconstruct why the call was made — the reasoning evaporated with the thread.

Tribunal makes that decision an argument with a record. Four advocates argue the question in
isolation on **different models**, each under a different stance. A held-out judge scores them
and names the specific defect in each. Then the ruling **and the argument behind it** are
written back into DataHub as a native `Decision` document linked to the asset, so the next
person — or the next agent — inherits the reasoning, not just the outcome.

The decision is **not** made by the model that sounds most sure. It is made by code, applying
a threshold to scores the judge produced — for a reason measured, not assumed. See
[the judge scores; code decides](#the-judge-scores-code-decides).

Built for **[Build with DataHub: The Agent Hackathon](https://datahub.devpost.com/)**,
Challenge 1 — *Agents That Do Real Work*.

---

## Try it in 30 seconds — no DataHub, no API keys

```bash
pip install -e .
tribunal serve --demo
# open http://localhost:8077
```

`--demo` replays a **real deliberation** captured against a live DataHub instance, at its
original pace. It is a recording and the UI says so — it is not a simulation dressed up as
live work.

## Run it for real

```bash
datahub docker quickstart                      # a local DataHub
datahub datapack load showcase-ecommerce       # 1,049 sample entities

export DATAHUB_GMS_URL=http://localhost:8080
export MISTRAL_API_KEY=key1,key2               # comma-separate to pool and rotate on 429

tribunal serve                                 # web console
tribunal ask "should we deprecate this table?" --asset orders      # or the CLI
tribunal check                                 # verify connectivity
```

---

## How it works

```
    ask ──► resolve asset in DataHub
             │
             ├─► RECALL   already ruled on? → refuse to re-litigate
             │
             ├─► GROUND   lineage (2 hops downstream) · owners · schema · queries
             │
             ├─► ARGUE    4 advocates, 4 models, 4 stances, in isolation
             │              Impact    WHO BREAKS?
             │              Evidence  THE METADATA
             │              Minimal   SMALLEST CHANGE
             │              Cost      COST OF ERROR
             │
             ├─► JUDGE    scores 0-10 + names a defect in each
             │            CODE decides: adopt (≥9 and agreed) or refine
             │
             └─► RECORD   Decision document + asset description + tag
                          ↑ the next agent finds this before arguing again
```

### The judge scores; code decides

The judge does **not** choose the outcome. It scores each advocate 0–10 and names defects;
`ADOPT_MIN_SCORE` and `ADOPT_SPREAD` are applied in Python.

This is not stylistic. In an 18-question soak where the *judge* held the decision, it chose
"adopt" **18 times out of 18** — including on ten deliberately contentious questions — because
with four competent models at least one answer is always defensible. The entire refinement
path was dead code until the threshold moved out of the prompt.

### The dissent is kept on purpose

A ruling with the objection stripped out reads as more authoritative and is far less useful.
The surviving dissent is what tells the next engineer whether the reasoning still applies to
*their* situation. It is stored in the Decision document and shown in the UI.

### Grounding is real, or absent

Advocates argue from DataHub's actual account of the asset. When metadata is missing, the
briefing says which fact is missing rather than letting a model fill the gap — the panel
knows it is arguing from partial evidence.

---

## What it uses from DataHub

| Capability | Used for |
|---|---|
| `search` | resolve a name like `orders` to a real URN |
| `get_entities`, `list_schema_fields` | schema and documentation for the briefing |
| `get_lineage` | downstream dependents (2 hops) and upstream sources |
| `get_dataset_queries` | evidence of actual use |
| `search_documents` | find prior rulings before arguing |
| `save_document` (`Decision`) | write the ruling + argument back, linked to the asset |
| `update_description` | stamp a pointer on the asset itself |
| `add_tags` / `createTag` | mark the asset as reviewed |

Everything is **read-only until a ruling exists**. Advocates never call tools — they receive
text and return text. Only `record_decision` writes, and only after the judge has ruled.

---

## Notes for anyone building on the Agent Context Kit

Things that cost us time, measured against DataHub `v1.5.0.6` quickstart:

1. **`update_description` writes to `editableProperties.description`, not
   `properties.description`.** Reading back the wrong one makes a successful write look like a
   silent no-op. `properties` is ingested metadata; `editableProperties` is the human/API overlay.
2. **`add_tags` does not create a missing tag** — `batchAddTags` fails with *"Urn does not
   exist"*. You must `createTag` first, and on every later run that mutation returns *"This Tag
   already exists!"*, which has to be treated as success.
3. **Document titles live at `entity.info.title`**, not `.title` or `.properties.title` as on
   most other entities. Reading the wrong path yields an empty title for every row.
4. **Newly saved documents take time to reach the search index.** `save_document` returns a
   real URN and `get_entities` can fetch it immediately, but `search_documents` will not find
   it for a while. Match on titles you control rather than assuming body text is searchable.
5. `get_datahub_client()` **returns** a client from context, it does not create one — the entry
   point has to construct `DataHubClient(server=..., token=...)` itself.

---

## Disclosure of pre-existing code

Per the hackathon rules, disclosing prior work:

The deliberation engine in `src/tribunal/panel.py` is **adapted from an agent-deliberation
module the author wrote before this hackathon**. What carried over is the general shape:
parallel advocates, a scoring judge, a code-owned adopt/refine threshold, and the Mistral
key-pool that parks a key on HTTP 429.

Everything that makes this a DataHub project was written during the submission window:
`datahub_context.py` (read / recall / write-back), the four data-specific stances, the
briefing format, the web console, the CLI, the demo recording, and the write-back into
`Decision` documents. `src/tribunal/panel.py` retains a design note describing the soak result
that shaped it.

No other pre-existing code is included. Dependencies are `datahub-agent-context` and the
Python standard library — the web console deliberately has no framework and no build step.

---

## Related work

**[stepproof](https://github.com/rushikeshgoud19/stepproof)** — the same instinct pointed at a
different problem. Tribunal distrusts what one model says is *true*. `stepproof` distrusts what
an agent says it *did*: it checks the claimed effect against real state and seals it into a
hash-chained ledger. Zero dependencies.

## Licence

Apache 2.0 — see [LICENSE](LICENSE).
