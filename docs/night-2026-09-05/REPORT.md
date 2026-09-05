# Night run — 2026-09-05, 20:46 onward

Continuous session on the Windows seat, driven by a scheduled job every 30 minutes from
20:00 to 08:00 PC time. Backend `f9e4c75` → this branch.

Machine: Windows 11, Python 3.14.3, RTX 5070 Laptop (8151 MiB), CATIA V5-6R2023 (French
seat), Ollama `qwen3-coder:30b`.

---

## What shipped

| Phase | Before | After |
|---|---|---|
| E5 — Assertions & self-correction | 5.1, 5.3 done; **5.4 open** | **5.4 DONE.** Only 5.2 remains, blocked on Phase 11 |
| E18 — Machine missions | not started | **partial** — the harness exists, M1 green, M2–M9 declared PENDING |
| E6 — Solver federation | not started | **in progress** — deck writer, load mapping, `.frd` reader |
| E16 — Tool retrieval & planning | partial | unchanged, but **now carries a measurement** |

Four commits: `b811a3c`, `b8445dd`, `5e799db`, `975fb9e`.

---

## The measurement that matters most

**The geometry works. The agent is the bottleneck. This was measured on both backends and
the failure is identical on each.**

Rung 2 of the prompt ladder — *"Design a mounting flange: 100 mm square plate, 12 mm thick,
aluminium, 40 mm central bore, four 9 mm holes on a 70 mm bolt circle, 8 mm corner fillets,
then measure it and tell me the mass"* — six features that must agree with one another.

| Backend | Result |
|---|---|
| OCCT (`GEOMETRY_BACKEND=occt`) | opened with `catia_pad` on a conversation with no document; invented the argument `profile`; invented `catia_circle` and `catia_pattern_circle`; **never called `catia_sketch_create`**. Nothing built. |
| CATIA seat (`GEOMETRY_BACKEND=catia`) | invented `catia_create_part`; recovered to `catia_new_part`; switched workbench **twice**; then asked the user what to do. Part created on the seat, **no features**. |

Before concluding the model was at fault, all three places this could have been *our* bug
were checked, and all three were already correct:

1. the CATIA prompt variant is the one selected (`AGENT_SYSTEM_CATIA_DOCS`, 18,396 chars);
2. it now teaches the four-step modelling order explicitly — verified in the string actually
   sent, not in the source;
3. `app/ai/state.py`'s per-turn brief **already** said *"Call catia_new_part to start a part
   this conversation owns, before any other geometry operation"*, placed beside the user's
   message, which is the one location a small local model reliably reads.

Told twice, in the two places built for telling it, and it still opened with a pad. Adding
the modelling order to the system prompt moved **exactly one thing**: `depth_mm` became
`length_mm`. Nothing else changed.

**The good news is the half that worked.** Every invented tool, every invented argument and
every out-of-order call came back as a named refusal from our own validation. No incorrect
geometry was built on either backend. The refusal for an unknown tool even offered the three
nearest real names. That is the validation layer doing its entire job.

This is Phase 16's problem, and the E16 board row now records it: 108 tool schemas are ~16k
prompt tokens re-evaluated every turn (`prompt_eval_cached_count: 0`), and the model cannot
hold a six-feature intent across them. The master plan's first *"fact to keep in view"* —
whether an LLM can hold coherent design intent at scale — is now **observed here rather than
anticipated**.

### Rung reached

**Rung 1 passed** (one solid from a sketch, OCCT, earlier today). **Rung 2 failed on both
backends.** Per the standing rule, the next session starts at rung 2 again, not rung 3.

---

## Screenshots

Per the standing rule added today, every CATIA result is screenshotted and looked at. PNGs
are gitignored in this directory; regenerate with `scripts/shot.ps1`.

| File | What it showed |
|---|---|
| `00-smoke.png` | CATIA with `Part5` open and `Corps principal` **empty** — proving an earlier chat run had reached the real seat and then failed to put anything in it. No viewport render could have shown this. |
| `01-catia-after-rung2.png` | Title bar `CATIA V5 - [New_Part.CATPart]`, tree `Part6` → `Corps principal`, no features. The agent's `catia_new_part` genuinely reached the seat; nothing after it did. |

Both confirm the French seat (`Démarrer`, `Fichier`, `Affichage`, `Corps principal`).

**Observation worth keeping:** the name passed to `catia_new_part` becomes the *document*
name (`New_Part.CATPart` in the title bar) while the tree root keeps CATIA's own
`Part6`. Two different names for one thing, and only one of them is ours.

---

## Ollama and the GPU

Checked during the CATIA run, which is the only time the answer means anything:

```
NAME               SIZE     PROCESSOR          CONTEXT
qwen3-coder:30b    20 GB    70%/30% CPU/GPU    32768
nvidia-smi: 6524 MiB used, 99% utilisation
```

**The GPU is being used.** An 8151 MiB card cannot hold a 20 GB model, so 30% resident and
the rest from system RAM is the expected and correct state — not a misconfiguration. CATIA's
own viewport holds part of the same card. Wall time 255 s for that turn.

---

## Defects and findings

### F1 — My own rationale was wrong, and measuring it corrected it. **Fixed.**

`missions.py` originally justified filleting *before* drilling as **necessary**, expecting
the bare word `vertical` to also catch the bore's seam (which `CLAUDE.md` warns about).
Measured: it does not, on this geometry. Bore-first gives the same volume to 1e-12 and the
same eleven faces, scoped or unscoped.

What *is* load-bearing is the `feature` scope. With a 20×20×10 boss standing on the slab,
scoped removes 171.68 mm³ — the slab's four corners — and unscoped removes 386.28 mm³,
having rounded the boss as well **and reported success**. That is the same defect the
2026-09-05 verification found in the design suite's own bracket fixture. The order now
stands as machining order and the code says so.

### F2 — A guard I wrote was dead code with a worse message. **Fixed.**

`deck.py`'s empty-selection check was unreachable: `select_nodes` already refuses an empty
selection *and* names the selector. It is now a re-raise that adds the one thing the lower
layer cannot know — **which** fixture asked, the first question on a case with three.

### F3 — Von Mises was about to exist twice. **Fixed.**

`frd.py` first re-derived the invariant. `linear_static.von_mises` already takes the same six
components in the same order, so it is now imported. Two implementations of one formula can
drift, and a drift *there* would surface as a disagreement about the *stresses* — a far more
alarming finding than the truth.

### F4 — `/health` is no longer the unconditional stub CLAUDE.md describes. **Reported.**

It returns `{"status","version","git_sha","built_at","checks":{"database","media_store"}}`
and genuinely checks both. The landmine entry saying it "returns `{"status":"ok"}`
unconditionally, so it cannot be used as a readiness probe" is stale.

### F5 — The `.frd` parser is documented, not verified. **Stated, not hidden.**

`ccx` is not installed on this machine, so every fixture is built from the CalculiX manual
rather than captured from a run. A test written from the same documentation as the code can
be wrong in the same direction. `FrdFile.describe()` exists so the first real run reports
every block found and every record it could not classify — the pattern
`catia_describe_dialog` uses for unrecognised Win32 control classes.

**Installing CalculiX is the next thing that needs a human decision**, since it is a
system-level install rather than a Python dependency.

---

## Still open

- **6.1's remainder**: the `ccx` subprocess, and the nodal-to-element stress bridge, which is
  a real choice — averaging a node's neighbours smooths the peak a factor of safety comes
  from.
- **6.5's oracle comparison** against the hand-written solver; blocked on the binary.
- **Rung 2 of the prompt ladder**, on either backend.
- **D9 and D10** from the previous session, still unfixed and still reported.
