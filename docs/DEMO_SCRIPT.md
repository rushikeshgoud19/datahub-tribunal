# Demo video script — 2:40 target (hard limit 3:00)

Record at **1920×1080**, browser zoom **110%**, everything else closed. Speak at a normal
pace — the timings below assume roughly 150 words/minute and already include pauses.

**Before you hit record**

```bash
# terminal 1 — DataHub must already be up
wsl -d Ubuntu -u root -e bash -lc "docker ps --format '{{.Names}} {{.Status}}'"

# terminal 2 — the console, LIVE mode
cd ~/OneDrive/Desktop/datahub-tribunal
export DATAHUB_GMS_URL=http://localhost:8080
export MISTRAL_API_KEY=<your keys, comma separated>
PYTHONPATH=src .venv/Scripts/python.exe -m tribunal.cli serve
```

Open `http://localhost:8077`. Have `http://localhost:9002` ready in a second tab, already
logged in. **Pick an asset you have NOT ruled on yet** — otherwise Tribunal correctly refuses
to re-litigate and you lose the main shot.

---

## 0:00–0:22 — the problem (talk over the empty console)

> "Someone asks in Slack: can we deprecate this table? Three people weigh in, the most
> confident one wins, the table gets dropped. Six weeks later a dashboard breaks and nobody
> can reconstruct why the call was made. The decision survived. The reasoning didn't.
>
> This is Tribunal. It turns that question into an argument with a record."

## 0:22–0:40 — grounding is real

Type `order_items` in the asset box. Let the dropdown show real URNs. Pick the Snowflake one.
Type the question: **"Should we deprecate this table, or does something still depend on it?"**

> "It resolves the asset in DataHub, and before anything argues, it reads what DataHub
> actually knows — lineage two hops downstream, owners, schema, query history."

Click **Convene the tribunal**. Point at the briefing panel as it appears.

> "That's not a prompt I wrote. That's the catalog."

## 0:40–1:25 — the argument (the money shot)

Let the four advocates fill in. Don't narrate every one — let it breathe.

> "Four advocates, four different Mistral models, each with a different job. Impact asks who
> breaks. Evidence argues only from the metadata. Minimal wants the smallest safe change. Cost
> prices being wrong.
>
> They answer in isolation — they can't see each other. That's deliberate. If they could, they'd
> converge on whoever spoke first, and you'd be paying four times for one opinion."

Scores land.

> "Now the judge scores each one and names a specific defect. Note it isn't choosing — the
> threshold is applied in code. When I let the judge decide, it voted 'adopt' eighteen times out
> of eighteen, including on questions built to be contentious. With four competent models,
> at least one answer is always defensible. So the judgement is the model's; the decision is code's."

If it goes to round 2, point at the amber lines reversing.

> "Agreement was low, so it doesn't ship the answer — it sends the critique back and the two
> strongest advocates redraft."

## 1:25–2:00 — the ruling and the dissent

> "Here's the ruling. And here — this is the part I care about — is the objection that
> survived it.
>
> A verdict with the dissent stripped out looks more authoritative and is far less useful. This
> line is what tells the next engineer whether the reasoning still applies to their situation."

Read the dissent aloud. It's usually the best sentence on screen.

## 2:00–2:32 — write-back (the category-1 payoff)

Point at the green write-back line, then switch to the DataHub tab and open the asset.

> "And it goes back. Not a log file — a native DataHub Decision document, linked to the asset,
> with every position and every score inside it. The asset description gets stamped, the asset
> gets tagged.
>
> Which means the next person who opens this table sees the ruling. And so does the next agent."

Now the closer — run the **same question again** in the console.

> "Watch. Same question, second time."

It refuses.

> "It found the prior ruling and refused to re-litigate. A panel that re-argues a settled
> question just produces a confident second opinion nobody asked for."

## 2:32–2:40 — close

> "Tribunal. Four advocates, a held-out judge, and a decision your catalog remembers.
> Apache 2.0, and it runs on a laptop."

---

## Fallbacks

- **Something 429s mid-record** — keep going, the UI shows it honestly. Or cut and re-run.
- **Deliberation runs long** — it's 10–20s; if it drags, cut between R0 and the scores landing.
- **Everything is broken on the day** — record `tribunal serve --demo` instead. It's a real
  captured run and the notice says so, which is still honest. Do not present it as live.

## Do not

- Don't speed up the footage to make it look faster than it is.
- Don't hide the token/call counters. Showing the real cost is part of the point.
- Don't claim it's autonomous. A human asks the question and a human acts on the ruling.
