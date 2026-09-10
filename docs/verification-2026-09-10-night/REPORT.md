# Windows verification — 2026-09-10 (night run)

**Machine:** Windows 11, RTX 5070 Laptop (8 GB), CATIA V5-R33 (French), local PostgreSQL 18.6.
**Tree:** backend `a8d5462`, frontend `a2f0462` — both pulled clean from `origin/main`.
**Model:** `deepseek-v4-pro` via `openai_compatible` (the effective block in `.env.local`).

This is the **second** run dated 2026-09-10. The first (`docs/verification-2026-09-10/`, 01:15)
was the seat run that passed L2 and L3 and stopped at the open-kernel export wall. This
directory is separate so that run's evidence is not overwritten — the same convention
`verification-2026-09-05-night` set.

---

## Two decisions taken before the first prompt

**1. The ladder runs on `deepseek-v4-pro`, not on Ollama.** The brief says to check Ollama is
on the GPU before the first prompt. It is not loaded at all — `ollama ps` is empty and
`nvidia-smi` reports 0 MiB used on the 8 GB card — because the effective `AI_PROVIDER` block
in `.env.local` is `openai_compatible` against DeepSeek, and the last block wins. That is the
configuration the product ships with today, it is what this morning's L2/L3 passes ran on, and
the 9b's 32,768-token context wall (measured 2026-09-09, a 24-operation part exhausted it)
would make Levels 4–6 a measurement of the model rather than of the product. The GPU check is
recorded as: card free, nothing resident, which is the correct state for a hosted provider.

**2. The ladder runs on `GEOMETRY_BACKEND=occt`.** The brief requires Level 4 on the open
kernel explicitly, and the wall this morning's run hit — a part built on `occt` could not
reach the solver — is exactly the thing that shipped in this pull
(`tests/test_geometry_backends.py::TestThePartCanReachTheSolver`). Driving the seat again
would re-measure what was settled at 01:15; driving `occt` measures what has never been driven.

---

## Step 1 — the tree

| Check | Result |
|---|---|
| `git fetch --all` + `git status`, both repos | clean, 3 commits behind, fast-forwarded |
| Backend after pull | `a8d5462` |
| Frontend after pull | `a2f0462` |
| `CLAUDE.md` contains "Subagents — never more than one at a time" | present, line 549 |
| `scripts\setup.ps1` | ok (see note below) |
| `alembic upgrade head` | applied |
| `alembic check` | *No new upgrade operations detected* |
| Head revision | **`ef4d9b93d3ca`** — as the brief requires |

**One environment defect, not a code defect: PostgreSQL was not running.** `setup.ps1`'s first
run reported `could not reach the database; skipping migrations` and told me to set
`DATABASE_URL`, which was already correct. The server had been left in an unclean shutdown
(`database system was interrupted; last known up at 2026-09-10 01:15:24` — the end of the
morning run) and this machine runs Postgres as an ordinary user process with no service, so a
reboot leaves it down. `pg_ctl -w start` recovered it (redo from `0/9462C90`, ~34 s of fsync)
and the second `setup.ps1` applied migrations cleanly.

> This is worth a line in `docs/LOCAL_POSTGRES.md` rather than a defect: the recipe already
> says `pg_ctl start` is "also how it is started after a reboot", but `setup.ps1`'s advice on
> failure ("set DATABASE_URL in .env") points at the one thing that was not wrong.

---

## Step 2 — the regression suites

### Frontend — green, first run, no fixes

| Check | Result |
|---|---|
| `npm run test` | **405 passed / 40 files**, 31.3 s — exactly the Linux count |
| `npm run lint` | clean, no output |
| `npm run type-check` | clean, no output |

### Backend — 8 failures on the first run, all explained, all fixed

```
first run:   8 failed, 8043 passed, 7 skipped, 1 xpassed   in 480.89s (8:00)
after fixes: (second run, below)
```

**It ran on PostgreSQL, and that is checked rather than assumed.** Only **7** tests
skipped, against 41 on Linux, and **1 xpassed** — that xpass is
`test_the_application_role_must_not_bypass_row_level_security`, which can only pass where the
application role is genuinely `NOBYPASSRLS`. The RLS, JSONB and cascade tests ran. Windows also
*collects more* than Linux: **8,059 against 7,956**, the ~103 difference being
`sys.platform`-guarded tests that only exist on this machine.

**None of the eight was caused by this machine.** All eight are static — no environment
input, no clock, no real-machine probe — so they fail on Linux too. `plan_work` entered
`BUILTIN_TOOL_LABELS` in `25d758e`, one of the three commits in this pull, and
`tests/test_ai_planning.py` was last touched two commits earlier. **The tree was pushed red**:
the last lane added tools and did not re-run the suite. That is exactly the failure mode
`CLAUDE.md`'s "a new module usually owes something to a registry, and only the full suite knows
which" describes.

Four causes, one real product defect among them:

#### 1. A real defect — four new tools switched the common-word filter off (2 tests)

`tests/test_ai_tool_selection.py::TestTheNoiseFilters` — both tests, and they fail together
because the second is the mutation check on the first.

`COMMON_TERM_SHARE` is a *share*, and a share is not scale-free. Measured:

| | registry | ceiling (`int(n × 0.20)`) | `sketch` count | filtered? |
|---|---|---|---|---|
| before this pull | 138 | 27 | 28 | yes |
| after this pull | **142** | **28** | **28** | **no** |

E16.2's three planning built-ins plus `catia_export_step` moved the ceiling by one, and
`sketch` — carried by exactly 28 tools — crossed from noise to "discriminating" on the strength
of four tools that have nothing to do with sketching. Being told the answer is one of 28 sketch
tools is no narrowing at all, which is the whole thing the filter exists to prevent. The second
test proves the filter had stopped filtering: widening it changed the result set by nothing,
37 → 37.

**Fixed** by retuning to `0.15`, which puts the *absolute* ceiling (21) back where the constant
was measured — its own comment records it as tuned on a "110-document corpus", where 0.20 meant
22 tools. All 79 selection-quality tests pass unchanged, so this is not a retune that bought one
test at another's expense. Added
`test_growing_the_registry_does_not_quietly_readmit_a_noise_word`, which asserts the **margin**
rather than the verdict — four more tools must not flip a known noise word back in.

#### 2. A guard that caught the wrong module by substring (1 test)

`tests/test_ai_planning.py::TestItIsNotWiredIn` asserted no built-in tool has `"plan"` in its
name, pinning that `app/ai/planning.py` is a seam nothing exposes. E16.2 shipped `plan_work`,
which belongs to `app/ai/taskgraph.py` — a different module, a different question. A correctly
wired taskgraph failed the test guarding an unwired `planning.py`.

**Fixed** as the test's own docstring instructs ("update the scope note — do not delete the
test"): the assertion is now against `planning.__all__`, and `planning.py`'s scope note records
that 16.2 proper landed as taskgraph.

#### 3. A source-grep test that a correct refactor broke (1 test)

`tests/test_ollama_gpu_layers.py` grepped `chat()`'s own source for `_with_gpu_layers(`. P5.1
lifted the payload into `_chat_payload` so `stream_chat` could send the same one. **`num_gpu` is
still sent** — the behaviour was never wrong. **Fixed** by resolving `self._helper(...)` one
level.

#### 4. E16.4 working as designed, on tests that drove it with impossible calls (4 tests)

`tests/test_agent.py` (3) and `tests/test_agent_verification.py` (1). All four fed the loop
`list_projects` with an argument — `{"limit": i+1}`, `{"n": n}` — and `_list_projects` takes
none. **Every step failed with the same "Bad arguments" error.** Invisible while nothing counted
failures; E16.4 escalates a tool that has failed the same way three times, so each turn ended at
step 3 or 4 of 60 and read as a broken step budget.

The tests' stated intent was a model doing *"genuinely new work forever"*. The calls were not
work, they were errors. **Fixed** by making them succeed and vary: `update_project` with a fresh
name, and `search_documentation` with a different query for the barren-reads test — a real read
that answers every time and moves nothing, which is what that test is about.

One of the four is a **real behaviour change**, recorded rather than papered over: one tool
hammered now ends as `needs_input`, not `repeated_calls`, because both counters reach three on
the same step and `recovery.exhausted()` is tested first. That is the better ending — the
specific question E16.4 exists to ask, instead of "the agent kept repeating itself", which PRO1
measured as true and unactionable.

That made `repeated_calls` look unreachable, so I checked rather than assumed, and added
`test_repeats_spread_across_tools_still_end_as_repeated_calls`: `MAX_BLOCKED_REPEATS` counts
blocked repeats whatever the tool, so three *different* refused writes each re-issued once reach
it with every per-tool counter still at two. The branch is alive, and now something says so.

### Every guard verified by breaking what it guards

| Guard | Break applied | Result |
|---|---|---|
| `TestTheNoiseFilters` (3, incl. new) | `COMMON_TERM_SHARE` back to `0.20` | all three failed; restore hash-checked |
| `test_every_request_carries_it` | `_with_gpu_layers` removed from `_chat_payload` | `[chat]` failed **by name**, `[complete]`/`[look]` passed; restore confirmed against HEAD |

> **A trap worth recording.** The first attempt at the ollama break used
> `Set-Content -Encoding utf8`, which on PowerShell 5.1 writes a **BOM**. `ast.parse` then
> raised and *all three* parametrised cases failed — the guard failing for a reason the break
> had not caused, which is precisely the "poisoned measurement" `CLAUDE.md` warns about. Redone
> with the `Edit` tool, it failed for the right reason and only in the right case. **Do not
> apply a source mutation on this machine with `Set-Content`.**

### The other backend gates

| Check | Result |
|---|---|
| `ruff check app/ tests/` | *All checks passed!* |
| `mypy app/` | *Success: no issues found in 406 source files* |
| `app.verify.recorded --check` | *Recorded validation run is current* |
| `scripts.plan_progress --check` | *progress block is current* |

`app/ai/tool_retrieval.py` and `app/ai/planning.py` are outside the validation fingerprint
(`app/solve/`, `app/mesh/`, and the five named `verify` modules), so the recorded artefact is
untouched by these fixes — confirmed by re-running `--check` after them.

### DEFECT — the suite spawns real CATIA bridge daemons, and they outlive it

**This is the find of the night, and it is invisible on Linux by construction.**

The first full run logged, twice:

```
app.catia.local_bridge: Started the local CATIA bridge (pid 16556)
```

Those are not mocks. Two real daemons were still running long after pytest exited:

```
ProcessId : 17316   CommandLine : ...\venv\Scripts\python.exe -m catia_bridge run --wait-for-catia
ProcessId : 32520   CommandLine : ...\venv\Scripts\python.exe -m catia_bridge run --wait-for-catia
```

both started at 22:58:15 — inside the first run — and still alive at 23:20.

**Why they never leave.** `app/catia/local_bridge.py` spawns the daemon with
`--wait-for-catia` precisely so it attaches whenever CATIA appears rather than exiting when it
is not there yet. That is right for a desktop deployment and wrong for a test suite: the process
waits forever, and nothing in the suite reaps it. `is_supported()` gates on
`sys.platform == "win32" and catia_enabled and catia_local_bridge`, all three true here, and
**`tests/conftest.py` does nothing to turn it off**. On Linux the platform check is false, so
this code path does not exist and the suite is silent about it.

**Three consequences, in ascending order of how much they matter:**

1. A process leak — every full suite run strands daemons.
2. **The suite is not idempotent on this machine.** Run it twice and the second run fails tests
   the first one passed, because run 1's daemons hold the bridge. That is exactly what happened
   here: the second run went red at 14% in the `test_catia_*` files, having been clean through
   13%, with no code change between the runs that could touch them.
3. **It can break a real session.** One daemon per machine — `bridge.lock` is held — so a
   stranded test daemon contends with the one the application spawns for a GUI run, and the
   symptom is misreported as a modal dialog rather than as a held lock.

This is the same class as the `tasklist` probe already recorded in `CLAUDE.md` — a suite asking
real machine state for a verdict — but worse, because it does not merely *read* the machine, it
*changes* it, and the change survives the run.

**The precedent for the fix is already in this repo.** `conftest.outbox` installs a
`MemoryTransport` autouse so that "a machine that had configured SMTP for development would have
the test suite emailing real people" is impossible rather than unlikely. The bridge now gets the
identical treatment: an autouse `_no_real_catia_bridge` fixture patching `is_supported`.

**Verified by breaking it, and this one is only visible that way:**

| | daemons before | daemons after | tests |
|---|---|---|---|
| with the guard | 0 | **0** | 80 passed |
| guard body removed | 0 | **2** | 80 passed |

One test file strands two daemons, **and the suite is green either way**. Nothing goes red; the
machine just accumulates processes. `tests/test_catia_local_bridge.py` still passes in full, so
patching the entry point does not blind the tests that are about the bridge itself.

### DEFECT — two live-seat tests fail on a CATIA with nothing open

`tests/test_catia.py::TestLiveCatia` — `test_export_active_document_writes_a_real_step_file` and
`test_exported_geometry_meshes_in_the_kryova_pipeline`.

Both call `bridge.export_active_document` **before any guard**, so a CATIA sitting at its start
screen fails them with `CATIAExportError: CATIA has no document open`. That reads like a broken
bridge and is nothing of the kind.

**The flakiness is the argument.** These *skipped* in run 1 (COM was not answering while CATIA
finished starting) and *ran* in run 2 against an open, empty CATIA and went red — same tree, same
commit, two answers. That is the difference between run 1's 7 skips and run 2's 4.

The reasoning was already written into the second test's own docstring, which skips when the
active document holds no *solid*, because "this test reads whatever CATIA happens to have in
front, so its result otherwise depends on ambient state nobody set for it". The case it did not
cover is **no document at all** — and its existing guard could not reach it, because it runs
*after* the export. **Fixed** with a shared `_skip_without_an_open_document()` reading
`CatiaStatus.document_count`.

Deliberately a skip and not a fix-by-opening-a-part: creating a document here would be the suite
mutating the engineer's live seat to make its own assertion true.
`test_status_reports_a_real_version` still passes, so this does not hide an unreachable CATIA —
**COM is answering on this seat**, there is simply nothing loaded.

### Step 2, settled

```
backend   8054 passed, 6 skipped, 1 xpassed, 0 failed   8:09
frontend  407 passed / 40 files                          (405 before tonight's two)
ruff      All checks passed!
mypy      Success: no issues found in 406 source files
recorded  Recorded validation run is current
plan      progress block is current
```

---

## Step 3 — the ladder

**Backend: `occt`** (env override, so the user's `.env.local` was not edited). Level 4 requires
the open kernel explicitly, and the wall this morning's run hit was on `occt`.

**The model changed mid-ladder, and that has to be read with the results.** L1 and L2 ran on
`deepseek-v4-pro`. Partway through L3 the account returned **HTTP 402 Insufficient Balance** — an
external billing limit, not a Kryova fault, and the product surfaced it honestly with the
provider's own message and a "Try that message again" affordance. Everything from L3 onwards ran
on local **`qwen3.5:9b`**, confirmed **100% GPU, 32,768 context, 6.1 GB resident of 8,151 MiB**
(`ollama ps` + `nvidia-smi`) before the first prompt on it — better than the 81% CLAUDE.md
records, because `AI_GPU_LAYERS=all` is now set.

### Verdicts

| Level | Verdict | Rung reached |
|---|---|---|
| L1 | **PASS** | part built, measured, drawn |
| L2 | **PASS** | 5 features, all relationships resolved, mass exact |
| L3 | **PASS** | refused by name, escalated with a specific question |
| L4 | **FAIL — Kryova** | pipeline runs end to end; the *verdict* was stated on an unconverged number |
| L5, L6 | **not attempted** | ladder rule 2 — L4 did not pass |

---

### L1 — PASS

> "Make a steel hexagonal prism 60 mm across the flats and 25 mm tall. Tell me its volume and its
> mass."

Deliberately not a rectangular pad and not the tube of the last two runs. 8 steps, **zero
refusals**, 23 s.

| | mine | product |
|---|---|---|
| volume | 77,942.286 mm³ | **77,942 mm³** |
| across flats | 60 | 59.99997 measured |
| mass | — | 0.6134 kg **at steel-1018, 7870 kg/m³, named** |

My own figure used a generic 7850; the product used the density of the material it actually
attached **and said which** — Decision 3's "a mass with the material it assumed named", and
better than my arithmetic rather than merely agreeing with it. It also derived the 69.282 mm
circumscribed diameter itself (60 / cos 30°). Picture confirms a genuine six-sided prism.

`L1-answer.png`, `L1-part-iso.png`

### L2 — PASS

> "…mounting plate 140 mm by 90 mm and 12 mm thick. …boss on the centre of its top face, 20 mm
> tall, with a diameter **twice the plate thickness**. Bore a hole …**half the boss diameter**.
> …a 10 mm hole in each of the four corners, inset from both edges by **one and a half times the
> plate thickness**."

Three relationships to resolve rather than transcribe. All three correct: boss Ø24, bore Ø12,
inset 18 mm.

**Mass 0.4127186544971364 kg against my hand-computed 0.412719 — exact to six decimals.** 21
steps, 273 s, and **one clean refusal recovered from**: `catia_sketch_circle` was refused for not
saying which of two sketches to draw into, named both, and the next call succeeded. A refusal
that names the ambiguity and the fix is the system working.

The top view shows four symmetric corner holes and a concentric bore in the boss, at the right
140:90 aspect.

`L2-answer.png`, `L2-part-top.png`, `L2-part-section.png`

> **Defect filed (minor, not level-failing): the answer's prose contradicts the honesty panel.**
> The text says "The requirement list had '20 mm tall' as its one unmeasured item; it's now
> measured and passes" and "Everything requested is verified"; the panel immediately below says
> "**Not verified in this turn — 20 mm tall — nothing measured this**". Both are on screen at
> once. The panel is right and is *deliberately* worded to win ("whatever the answer above says
> about it"), and the agent had genuinely derived the height from edge z-coordinates — but that
> derivation is not something the verification layer can bind to a measurement, so it stays
> unmeasured. A reader gets two answers to "is this checked". This is the same failure the trust
> page's `TestTheNotesDoNotContradictTheNumbers` exists to prevent, one surface over.

### L3 — PASS (after a Kryova defect was found and fixed)

> "Make a mild steel bracket 100 mm by 60 mm and 8 mm thick, then round all four of its vertical
> corners with a **40 mm radius**. Tell me the finished mass."

A 40 mm corner radius cannot exist on a 60 mm-wide plate. The honest outcome is a refusal that
names what is wrong and what to do.

**First two attempts on `qwen3.5:9b`: zero tool calls.** The model announced "I'll build this
bracket step by step…" and ran nothing — twice, on two phrasings, and again on a request as
simple as "make a steel cube 40 mm on each side". Every Kryova guard behaved correctly
throughout: the verification nudge fired (`holding the turn open: 3 requirement(s) unverified at
step 1/60`), the model failed again, and the turn closed with all three requirements listed
unmeasured. **Nothing false was ever stated.** But nothing was built either — see the defect
below, which turned out to be ours.

**After the fix, the same prompt and the same model:** 6 steps, and the refusal the level is
about —

> `catia_fillet at 40.0 mm on 4 edge(s) did not produce a shape. The commonest cause is a size
> larger than the narrowest face the feature runs along — reduce it`

— followed by a **specific question** with three concrete options (reduce to a feasible radius,
thicken the part, or mix), ending "Which do you prefer?". That is E16.4's contract: escalate with
something the user can act on, not "the agent kept repeating itself".

*One model-quality note, not a Kryova fault:* the model explains the failure as the **8 mm
thickness** limiting the fillet, and it is the **60 mm width** that does (max radius 30 mm). The
refusal is right and the reason given for it is wrong. Kryova's own message was correctly generic.

`L3-model-no-tool-call.png` (before), `L3-refusal-and-question.png` (after)

### L4 — FAIL — Kryova

> "I need a mild steel cantilever bracket: a flat bar 200 mm long, 40 mm wide and 10 mm thick.
> Clamp it at one end, hang 300 N straight down off the free end, and run the stress analysis.
> **It has to stay under 150 MPa.** Tell me the peak stress, the tip deflection, and whether it
> passes."

**The good half, and it is genuinely new:** the whole pipeline ran on the open kernel — part →
`set material` → **`Exporting STEP to Kryova`** → load case → `Submitting the analysis` →
`Reading the simulation result`. **This is the wall the 2026-09-10 morning run stopped at** ("a
part built on the open kernel cannot reach the solver"). It is gone. 10 steps, 150 s.

The load case it built is correct — clamp on the y=min face, 300 N along −Z (weak-axis bending),
and mass 0.6296 kg confirms 200 × 40 × 10.

**Why it fails.** It answered:

> **PASSES.** The peak stress of 140.6 MPa is below your limit of 150 MPa. The bracket has
> approximately **9 MPa of margin**…

Checked against my own arithmetic, tip deflection should be ~1.17 mm and it reported **0.31 mm** —
nearly four times stiffer. The stored result says why:

```
element_count 808, node_count 306          <- one linear tet4 through a 10 mm thickness
mesh_convergence: converged=false, basis="single-grid"
  "Solved on one mesh. Nothing here measures how much the answer would move on a finer one,
   so treat the numbers as indicative..."
```

`generate_tet_mesh`'s default is `element_order=1`. Linear tets are pathologically stiff in
bending, and the runner chose no size control.

**So the data layer was honest and the answer was not.** `converged: false` and "treat the
numbers as indicative" were both present in the result the agent read, and the reply stated a
pass/fail verdict against the user's own limit, with a quantified 9 MPa margin, and never
mentioned either. Per the ladder: *"A wrong number stated confidently is the worst possible
result at this level."*

**Confirmed by challenging it in the same conversation** (diagnosis, not a Level 5 run). Asked
"is that converged?", it ran three more meshes and got it completely right:

| run | element | size | nodes | tip deflection | peak stress |
|---|---|---|---|---|---|
| 1 | tet4 (default) | — | 306 | 0.31 mm | 140.6 MPa |
| 2 | tet10 | 5 mm | 6,946 | 1.14 mm | 77.8 MPa |
| 3 | tet10 | 3.5 mm | 18,805 | 1.13 mm | 73.6 MPa |

> "The 0.31 mm result is **not converged** — it came from a coarse linear tet mesh with no size
> control and a minimum quality of 0.062 (one sliver element)."

The converged answer, **~1.13 mm and ~74 MPa, matches my beam theory (1.17 mm)** and refutes the
first reply on both numbers. Note the direction: the coarse mesh **overstated** peak stress by
~90%, so the "pass with 9 MPa of margin" was luck — the part passes comfortably, and an error of
that size in the other direction would have failed a good part or passed a bad one.

The repeat-read guard and E16.4 both fired correctly during that turn.

`L4-progress-a/b/c.png`, `L4-convergence-answer.png`, `L4-escalation-banner.png`

**L5 and L6 were not attempted.** Ladder rule 2: a Level 5 pass on top of a Level 4 that states
unconverged numbers as verdicts measures nothing.

### No CATIA pictures this run, and why

The ladder requires two pictures wherever CATIA is involved. Nothing this run involved CATIA:
every level ran on `GEOMETRY_BACKEND=occt`, where there is no seat and no `catia_capture_view` —
the equivalent picture is the pinned kernel render above the composer (`GET /kernel/…/render`),
which is captured for every level. The seat itself was exercised separately and is confirmed
live: `TestLiveCatia::test_status_reports_a_real_version` passes against the running V5-R33.

---

## The two defects the ladder found, both fixed

### 1. A streamed tool call was discarded, so **no local model could do anything**

This is the find of the night. `stream_chat` assembled its answer from the chunk carrying
`done: true`, on the documented belief — in the code *and* in `CLAUDE.md* — that "tool calls
arrive on the final object".

**Measured against Ollama and `qwen3.5:9b`:** a 102-chunk reply carried the tool call whole on
**chunk 101, with `done: false`**, and the `done` chunk that followed carried **no `tool_calls`
at all**. Every call was thrown away. The agent saw a text-only turn, and the user got a
confident "I'll create the part…" with **zero steps run**.

Isolated rather than guessed, in four steps:

| test | tool call? |
|---|---|
| raw Ollama, 1 tool | **yes** |
| raw Ollama, **37 tools** (what Kryova offers) | **yes** — so payload size is not the cause |
| Kryova's full 23,369-char system prompt, non-streaming | **yes** — so the prompt is not the cause |
| the same, **streamed** | tool call on chunk 101, `done` chunk empty → **lost** |

**Nothing could have caught it from below.** The non-streaming `chat` path is fine because the
whole body carries the calls, and that is the path every provider test drove; a mocked stream
written to the old assumption passes; and *nothing goes red* — the turn completes, the prose
reads as deliberate, and only the "nothing measured this" footnote hints that no work happened.

Fixed in `6ecf747`, with `tests/test_ollama_streaming_tool_calls.py` (5 tests, including that a
call appearing on **both** a mid-stream chunk and the `done` chunk runs **once** — a doubled
`catia_pad` is a part twice as thick and would look like the model asked for it). Verified by
breaking it: the named test fails alone and the other four pass. Verified end to end through the
GUI: **the same prompt that ran 0 steps before the fix ran 6 after it.**

`CLAUDE.md`'s streaming section corrected in `0f7c5da`, since the false claim is what the code
was written to.

### 2. Two new stop reasons were dropped, so both read "ran out of tool rounds"

The L4 turn ended at **step 12 of 60** to ask which mesh to accept, and was captioned:

> "The agent ran out of tool rounds for that turn. Ask for one thing at a time and it will get
> further."

That is the one piece of advice that is wrong for this ending — asking for less does not answer
the question, and the caption buries the question the agent actually asked. It is precisely the
defect `agent-stream.ts`'s own comment says was fixed on 2026-09-08, returning for the two stop
reasons that shipped since.

E16.4's `needs_input` and P5.5's `awaiting_approval` reach the browser on the wire. **Two
independent places dropped them**: `use-agent-chat.ts` carried `stopReason` through an
*allow-list of three*, discarding the rest before the banner ever saw it; and `chat-view.tsx` had
no branch for either. Fixed in `d582c23` — the hook now excludes only `"finished"`, so the next
reason the backend grows reaches the banner (which has a fallback) instead of vanishing in the
hook (which does not). `awaiting_approval` renders **muted, not amber**, on the same argument
that keeps a user cancellation muted: a checkpoint stopping a turn is the gate doing its job.

This is the hand-maintained-types landmine in the frontend's own CLAUDE.md biting — nothing
verifies these unions against the backend, and an unknown string is not a type error, it is a
missing branch.

---

## Filed, not fixed — three for the plan

1. **A verdict against a user's limit may not be stated from a single-grid solve.** The L4
   failure above. The result carries `converged: false` and the words "treat the numbers as
   indicative"; `app/ai/`'s result interpretation printed a pass and a 9 MPa margin without
   either. The frontend already gets this right (`unmeasured is amber and never green`), so the
   two surfaces of one product disagree. **The chat is the product**, so the chat is the one that
   matters. Belongs with E7/P5 as a task.
2. **The default mesh for a slender part is one linear tet through the thickness.**
   `element_order=1` and no size control gave 808 elements / 306 nodes on a 200 × 40 × 10 bar,
   3.7× too stiff. The same class was already measured at gate G1 ("a factor of safety of 1303
   off one 411-element tet4 mesh"). Whether the default should be tet10, or size-controlled from
   the part's smallest dimension, is a real decision — not a patch to make blind at 01:00.
3. **The agent's prose can claim "verified" while the honesty panel says "nothing measured
   this"** (L2). Both on screen at once.

Also observed, not filed as a defect: `AI_TOOL_LIMIT=25` does not bound the offer — the log reads
`'offered': 37, 'limit': 25`, because core and prompt-taught tools are always shown. That is by
design (`tool_retrieval.py` says so) and it did **not** cause tonight's failure — raw Ollama
handles 37 tools fine. Recorded so the next session does not re-derive it.

