# Verification run, 2026-09-08 — Level 4 of the GUI prompt ladder

Driven through the real chat endpoint in the real web GUI, against a real CATIA
V5-R33 seat, per the standing rule: an end-to-end test goes through the Ollama
chatbot and never through the dispatcher.

## Conditions

| | |
|---|---|
| Database | **Local PostgreSQL 18.6** (switched this session — see [LOCAL_POSTGRES.md](../LOCAL_POSTGRES.md)) |
| Round trip | **0.135 ms**, against ~250 ms on Neon's pooled eu-west-2 endpoint |
| Schema | 25 tables at head, `alembic check` clean |
| Account | `admin@admin.com`, `platform_admin` granted out of band by `scripts/create_admin.py` |
| Model | `qwen3.5:9b`, **100% GPU**, `num_ctx=32768`, sustained **50–52 tok/s** all night |
| Seat | CATIA V5-R33, French interface, bridge auto-paired as "This workstation" |
| Backend | `109bf1a` plus the five fixes below |

Conversations: none carried over. The local database was created empty this
session, so "nothing from the last prompt is in scope" holds by construction
rather than by housekeeping.

## Result

| Prompt | Result | Rung reached / where it stopped |
|---|---|---|
| **PRO4** — punch press from a duty cycle | `~` **partial** | Physics done and honest; geometry wrong |
| **PRO1** — arbor press requirement document | `!` **fail** ×4 runs | Run 4 built its first solid, then hit the model's context window |

Neither is ticked in the ladder. Both are recorded below with what they measured.

### PRO4 — the honesty machinery works, the geometry does not

`PRO4-screen.png`, `PRO4-transcript.txt`.

**What passed, and it is the half that had never passed before.** The shear
calculation happened (π·6·3 = 56.5 mm²), a lever ratio followed from it, and the
answer said *"I have not checked it"* about frame stiffness — which is exactly
what the prompt demanded and what the 2026-09-07 run never produced. The
verification nudge fired at step 29 and held the turn open for two unmeasured
requirements; the unverified-requirements block was appended automatically.

**What failed.** The geometry is two thin flat plates, not a C-frame: no
fulcrum, no die holder, no stripper. And the shear strength was assumed at
**200 MPa** where ~350 MPa is right, so the force came out 11.3 kN instead of
≈20 kN. That last one is a model knowledge gap rather than a Kryova defect —
but it is one the product could close, because `solve/materials.py` carries no
shear strength for the agent to look up. **Worth doing (E12):** an assumed
material property that the agent states as "(typical value)" is precisely the
kind of number the verification doctrine exists to make checkable.

### PRO1 — four runs, and each one moved the failure somewhere new

`PRO1-screen.png` (the last, empty document), `PRO1-cframe.png` (the part that
has the solid), `PRO1-transcript.txt`.

| Run | Documents | Solids | Ended by |
|---|---|---|---|
| 1 | 1 | 0 | `MAX_BLOCKED_REPEATS`, reported as "ran out of tool rounds" |
| 2 | 5 | 0 | `MAX_BLOCKED_REPEATS` — *"5 empty part documents … no geometry built yet"* |
| 3 | 1 | 0 | `catia_sketch_dimension` failing on every attempt |
| 4 | 5 | **1** | the model's 32,768-token context window |

Run 4 is the first time this prompt produced a solid at all. It then created the
remaining four parts, and the turn ended because the conversation no longer fit
the window — refused loudly by `app/ai/providers/ollama.py`, which is the
correct behaviour and the reason it is legible here at all.

**The honest reading: a machine-scale prompt does not fit a 9B model's 32k
window.** That is the ceiling the ladder predicted ("expect the local model to
be the limit long before the geometry is"), and it is now measured rather than
assumed. The C-frame that was built also masses **303 kg**, an order of
magnitude over what a 1-tonne bench press frame should be — so the scale is
wrong even where the geometry is real.

## Defects found and fixed

Every one was invisible to the offline suite and left the product looking like
it was working. Each is verified by breaking the thing it guards.

**1. `catia_pad`'s "no such sketch" refusal was unrecoverable.**
`_find_sketch` said only *"Use the name a sketch tool returned"* — useless
precisely when it fires, because an agent that has invented a name has lost the
real one. PRO4 abandoned the press frame there. The *drawing* path
(`SketcherMixin._draw_target`) has listed the available names all along; the
*solid-building* path did not, which is the wrong way round — drawing into the
wrong sketch is recoverable, giving up on the solid ends the part. Now it lists
them. `scripts/catia_bridge/catia_com.py`,
`tests/test_catia_com_contract.py::TestASketchThatIsNotThereNamesTheOnesThatAre` (3).

**2. A name collision across kinds led with a recovery that cannot work.**
PRO4 had a *part* called 'Punch press assembly' and asked for a *product* of
that name. It was told to `catia_open_document` and continue — and got the part
back, which can never become the assembly it wanted; it then spent rounds
reopening documents. The single namespace is deliberate and unchanged; what
changed is which of the two remedies is offered first when the kinds differ.
`app/catia/dispatch.py`,
`tests/test_multi_document.py::TestTheRefusalLeadsWithTheRecoveryThatCanWork` (3).

**3. A turn stopped for repeating itself was reported as out of budget.**
PRO1 run 1 ended at step **31 of 60** because the agent kept re-issuing a read
the tool layer had already refused, and the banner said *"The agent ran out of
tool rounds — ask for one thing at a time."* Both halves were false: half the
budget was unspent, and narrowing the request would not have stopped the repeat.
Two exits reached identical closing code. The `done` event now carries
`stop_reason`, and the banner says the matching sentence — including that the
work so far is kept. Confirmed live in run 2's screenshot.
`app/ai/agent.py` + `Kryova-frontend` (`agent-stream.ts`,
`conversation-transcript.ts`, `use-agent-chat.ts`, `chat-view.tsx`); tests both
sides (2 backend, 3 frontend).

**4. Opening documents was being counted as building parts.**
PRO1 run 2 created five parts and put a solid in none of them; the agent
diagnosed itself in its closing words. **Neither existing guard can see this**:
`MAX_IDENTICAL_READS` needs a call repeated byte for byte and each
`catia_new_part` had a different name, while `MAX_READS_WITHOUT_PROGRESS` asks
whether anything *mutated* — and creating a part **is** a successful mutation,
so all five reset the barren counter. Creating a document is the one mutation
that changes nothing about the part: it makes the container, not the content.
New `MAX_EMPTY_DOCUMENTS` counts documents opened against solids produced, told
apart by the payload (`feature` versus `doc_name`). It nudges once, past two
documents, naming the remedy in tool order. **Measured effect: run 3 created one
document instead of five.** `app/ai/agent.py`,
`tests/test_agent.py::TestOpeningDocumentsIsNotBuildingParts` (4).

**5. `catia_sketch_dimension` failed on every attempt, as a raw COM error.**
Three consecutive runs on this French V5-R33: `AddDimensionConstraint` returns a
constraint and *reading* `.Dimension` off it raises `E_INVALIDARG` —
`(0, 'CATIAConstraint', 'La méthode Dimension a échoué', ...)`. Two things were
wrong. The agent was handed that string, which is not actionable in any
language, and re-issued the call six times across the runs. And the half-made
constraint stayed in the sketch — `AddDimensionConstraint` had already added it
— so the sketch was left holding a constraint with no dimension, and the pad
built from it then failed three times running. That is the wreckage
`_discard_failed_feature` was written for after fillets did the same thing; it
simply was not reachable from the sketcher mixin, so it is now on `ComContext`.
`scripts/catia_bridge/com/{sketcher,_context}.py`,
`tests/test_catia_com_contract.py::TestADimensionCatiaWillNotAcceptIsRefusedAndCleanedUp` (3).

## Not defects

- **A 401 on `/api/v1/catia/status` mid-turn**, twice. The access token expired
  during a seven-minute turn; the frontend caught it, called `/auth/refresh`
  (31 ms) and retried successfully. P1 rotation working. It shows in the browser
  console because a 401 *is* an error response, not because anything failed.
- **The `ABQMaterialPropertiesCatalog.CATfct` that will not close.** It is
  CATIA's own material catalogue, not a run artefact, and the cleanup script
  correctly reports it stuck rather than looping on it.

## What CATIA refused correctly, and it is worth recording

Four refusals in these runs were exactly right, and are the reason the failures
above are legible rather than mysterious: the pad that named a multi-loop
profile and said the failed feature *and its sketch* had been removed; the
crash-recovery dialog (`Restauration d'environnement`, left by a CATIA restart)
that `catia_dialog_action` refused with *"has no 'cancel' button. It offers:
&Oui, &Non"*; the duplicate sketch name; and `catia_list_faces` refusing an
out-of-range `min_area_mm2`. The interactive family works on a real seat.

## Checks

Backend `ruff` and `mypy` clean (343 files). `tests/test_agent.py` 79 passed;
`test_multi_document.py` 40 passed. Frontend `tsc`, `eslint` clean and **296
tests** green.

**Five pre-existing failures on `109bf1a`, untouched and unrelated to this
work** — reported rather than papered over, because deciding what these
contracts should now say belongs to their own phases:

- `test_catia_com_contract.py::TestFrozenScriptLibrary` — **this one is a
  tripwire doing its job.** Its comment says "if this set grows, someone added a
  script, which is exactly when a human should be looking at it".
  `KryovaFaceMap` was added and has not been reviewed in.
- `test_catia_com_contract.py::TestDocumentPathCollision` and the two
  `TestAPocketThatCutsNothingIsRefused` cases — the stub `_Part` has no
  `Bodies`, so `_feature_list` raises.
- `test_ai_context.py::test_new_part_is_refused_once_a_document_is_bound` —
  stale by design change: it asserts the one-document-per-conversation contract
  that E14.1–14.4 deliberately replaced with several documents and one active.

## What to do next

1. **Re-run PRO4** against fixes 1 and 2, which were made from its failure and
   have not been driven since. It is the cheapest measurement available.
2. **The context window is now the binding constraint, not the geometry.** PRO1
   run 4 built a solid and then ran out of window with four parts still to make.
   This is E16's ground: the offer is already narrowed to ~55 tools of 220, and
   what is left to shrink is the transcript. Nothing above rung 4 is reachable
   on a 9B until it is.
3. **Give the agent a shear strength to look up** (E12). PRO4 stating
   "~200 MPa (typical value)" is an assumed number presented in an answer, and
   the whole verification doctrine says that is the kind of number which must be
   traceable or absent.
