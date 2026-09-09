# GUI ladder run — 2026-09-09

**First run under the method (the ladder was rewritten the same day). Level 1 passed;
Level 2 did not, after four attempts. The run stopped there, per the ladder's own rule
that you do not advance until a level passes.**

Three real Kryova defects were found and fixed along the way, all of them between the model
and the tools, and none visible to the offline suite.

| | |
|---|---|
| Backend | `GEOMETRY_BACKEND=occt` |
| Model | `qwen3.5:9b`, **81% GPU / 19% CPU**, `num_ctx` 32768, 6536 MiB resident |
| Backend HEAD | `b030b3e` (+ the three fixes below) |
| Driven through | the web GUI on `localhost`, `/api/v1/ai/chat` — never `dispatch`, never `pytest` |

**Why `occt`.** The ladder's own "next run" note says to write each prompt against what
shipped 2026-09-06 → 09 — plane analyses, conduction, convergence studies, the requirements
check, sheet-metal folding, the assembly repository. Those are open-kernel and solver
features; sheet metal is explicitly open-kernel only, its CATIA half being queue item E1 and
unwritten. The CATIA seat is therefore *not* exercised by this run, and tier 3 of
`WINDOWS_VERIFICATION.md` remains open.

---

## L1 — PASS

> Make an aluminium tube 120 mm long, 40 mm outside diameter, with a 30 mm bore straight
> through. Tell me its volume and its mass.

Picked a tube because the brief forbids reusing the Level 1 shape and a rectangular pad has
been it too many times.

| | mine | measured | |
|---|---|---|---|
| volume | 65,973.446 mm³ | 65,973 mm³ | ✓ |
| mass @ 2700 kg/m³ | 0.17813 kg | 0.178 kg (0.17812830…) | ✓ |
| length / OD / bore | 120 / 40 / 30 | 120.000 / 40.000 / 30.000 | ✓ |

The drawing above the composer updated, and a Cut Z section shows a concentric bore with the
annular cut face hatched — a real tube, not a cylinder reported as one.
`L1-tube-answer.png`, `L1-tube-section.png`.

The agent took 18 operations to get there and wasted several (two sketches it never used, a
sketch on a plane called `top` that does not exist, a `catia_hole_at` against `top`). Every
one came back as a named refusal and it recovered from each. That is the level working.

## L2 — FAIL, four attempts, and three defects fixed

> Make a steel base plate 120 x 80 x 10 mm. Put a cylindrical boss on the middle of its top
> face, 30 mm tall, with a diameter three times the plate thickness. Then drill one hole
> straight down the centre, through the boss and out the bottom of the plate, with a diameter
> half the boss diameter. Finally round the four vertical corners of the plate with a radius
> equal to the plate thickness. Tell me the finished mass.

Relationships rather than numbers, as the brief requires: *three times the plate thickness*,
*half the boss diameter*, *a radius equal to the plate thickness*. My arithmetic:
**109,278.760 mm³, 0.86002 kg**.

| attempt | where it stopped | whose fault |
|---|---|---|
| 1 | padded the plate 120 mm instead of 10, then looped | model — caught cleanly |
| 2 | told `catia_pad` was "not implemented", abandoned padding entirely | **Kryova** |
| 3 | `distance_mm="10"` refused as a string, three times, then asked for help | **Kryova**, by precedent |
| 4 | looped on `catia_list_features` | model — caught cleanly |

**The part is buildable and the numbers are exactly right.** Driving the same sequence
through the runner — plate, offset plane, boss, bore, fillets, material — gives
**109,278.760 mm³** and **0.86002 kg**, matching the hand calculation to the digit. So this
is not a missing capability. It is the model failing, four different ways, to find the route.

### What the route is, and why it is hard to find

There is no way to sketch on a face. `catia_sketch_create(support="top")` is refused because
sketching on a face of the part needs `feature#selector`, which CLAUDE.md records as roadmap
A3 and blocked behind A1. The working route is `catia_plane_offset` from XY by the plate
thickness, then sketch on that plane. The refusal *does* name it — but the model reached for
`support="top"`, `limit="up_to_surface"`, `catia_shaft` and a surface extrude before it ever
tried an offset plane, and on the one run where it did, it sent the distance as a string.

**This is the finding worth carrying:** "put a boss on the top face" is the most ordinary
Level 2 sentence there is, and the open kernel has no direct expression of it. Until A1/A3
land, every Level 2 prompt of this shape depends on a 9B model discovering a two-step
workaround from a refusal message.

Pictures: `L2-attempt1.png`, `L2-attempt4.png`.

---

## Defects found and fixed

**1. A correct mass carried a note saying it did not exist.** `catia_measure` returned
`mass_kg: 0.178…` with `density_kg_m3: 2700` beside a provenance record reading
`mass_kg: unavailable — no density has been set on this part`. The measurement cache is
density-free by design; `_weighed` wrote the mass on top and never touched the sidecar. The
number is right and nothing raises — but `assertions.py` reads the sidecar per path, so an
assertion on `mass_kg` verified UNMEASURED for ever against a mass the kernel had integrated
correctly. Decision 3's "unmeasured is never a pass", firing on a number that *was* measured.
Fixed in `document._weighed`, which now records the measured basis and copies the sidecar
first — the caller passes a shallow copy, so attaching directly would have written today's
density into the cache that exists to be density-free. Both halves pinned and broken.

**2. A refusal denied that a working tool existed.** `catia_pad` with
`limit="up_to_surface"` came back as *"catia_pad is not implemented in the open kernel yet
(116 of 205 operations are)"*. `catia_pad` is implemented — it had succeeded two calls
earlier in the same conversation. `OperationNotSupported` is raised both for a missing
operation and for a capability within one, and carries a `subject` so the two can be told
apart; `_execute_locally` discarded it, along with a reason that says exactly what to do
(*"Extrude past the surface and cut with catia_boolean, or stop at a plane with
limit='up_to_plane'"*). The agent believed the message, stopped using pad, and went looking
for CATIA's interface. Fixed: a capability refusal keeps the kernel's own sentence; a missing
operation still reports coverage.

**3. `distance_mm="10"` was refused, correctly, and unrecoverably.** The validator is right —
the model sent a string. But it then told the user this was "a tool-system quirk that won't
resolve by retrying" and stopped. This is the scalar case of `_parse_array_strings`, which
exists for exactly this on ladder H4/H5, with the same reasoning recorded: *the refusal is
accurate and it does not help*. Added `_parse_number_strings`, tightly scoped — the text must
parse as JSON and yield a real number. `"10 mm"` is deliberately **not** repaired, because
this codebase is mm-N-MPa and converts nothing, so a unit-carrying string differs in a way no
repair here is entitled to resolve. `"1,5"`, `"ten"`, `""`, `"true"` and a fractional string
in an integer field all still reach the validator untouched.

## Observations, not defects

- **The part panel says "Nothing has been built in this conversation yet" mid-turn while a
  solid exists.** It refreshes at end of turn and is correct there. During a long turn it
  states something false; worth softening the wording rather than the behaviour.
- The agent narrated a false claim about the product twice ("the pad operation isn't
  implemented in this kernel", "the distance_mm argument keeps being reported as a string
  ... despite me passing a numeric value"). The first was Kryova's message; the second was
  not — it really did send a string. Worth knowing that this model will assert a product
  defect confidently and be wrong.
- The loop-breaker, the no-op-pad refusal, the unused-sketch report and the "not verified in
  this turn" panel all fired correctly and repeatedly. The honesty machinery is the part of
  this run that worked best.

## Where the run stopped

**At Level 2, on the model's inability to reach a boss on a face.** Levels 3–6 were not
attempted, and under the ladder's rules should not be: a Level 4 pass on a shaky Level 2
measures nothing.

The next run should decide one of two things first — whether to unblock face sketching (A1/A3),
or whether the offer needs to teach the offset-plane route explicitly, the way the four frozen
prompts teach their sixteen tools.

---

# Addendum — 2026-09-09, later: the face-sketching wall was ours, not the model's

**I was wrong in the section above.** "There is no way to sketch on a face" is not true, and
was not true when I wrote it. What was true is that `catia_sketch_create` refused one while
citing a phase that had already shipped, and I believed the message for the same reason the
model did.

`elements.plane_frame` is the single resolver every "which plane" argument goes through, and
its own docstring records that each operation once had a private accept-list.
`catia_sketch_create` was the last one still holding its own. So
`catia_plane_offset(reference="slab#top")` resolved a planar face while
`catia_sketch_create(support="slab#top")` refused it — and nothing on either side hinted the
two disagreed.

Worse, **the CATIA seat had accepted a bare `support="top"` all along**, resolving it against
the part's bounding box (`FACE_PLANES`/`FACE_AXES` in the bridge). The identical call built a
part on `GEOMETRY_BACKEND=catia` and was refused on `occt`. That is the one thing Decision 1's
conformance rests on not happening, and it is why the run above kept failing on the seat's
most ordinary sentence.

## Fixed

1. **`catia_sketch_create` now uses the shared resolver**, so `support="slab#top"` and
   `support="Pad.1#top"` work. The refusal for an unknown plane names the *syntax* with a
   feature the part actually has (`support='Pad.1#top'`) and the selector words, instead of a
   phase number that reads as unbuilt.
2. **Bare face words resolve on the open kernel too** — `top`, `bottom`, `front`, `back`,
   `left`, `right` — with the table **copied from the bridge** rather than invented, so the
   two backends cannot drift apart by taste. Pinned by a test that checks the boss lands on
   the *right side*, because a support resolving to the wrong face builds a boss hanging
   underneath and reports the same success.
3. **`catia_assembly_clash(components=[])` no longer refuses.** An empty *optional* list is
   the same as not sending it, so the documented default ("all of them") applies. A
   *required* empty list — a pattern with no points — still reaches the validator.
4. **The "removed no material" refusal now names the second cause.** A sketch on a face
   extrudes along that face's outward normal, which points away from the material, so a cut
   needs `reversed: true`. That is a real sharp edge and the message now says so.

With those, the Level 2 part builds from the sentence as written:

```
sketch on "top" -> circle 30 -> pad 30 -> sketch on "top" -> circle 15
  -> pocket 40 reversed -> fillet R10 vertical -> steel
= 109,278.760 mm3, 0.86002 kg   (hand calculation: 109,278.760, 0.86002)
```

## The retry on a rebooted CATIA

CATIA and both Kryova servers were shut down and restarted, and the backend was started
**without the `occt` override** — which is what had sent the earlier run to the open kernel
and is why nothing appeared in CATIA. The chip read *"This workstation is connected, running
V5-R33."*

It then built on the seat for real: `Extrusion.1` at 0.75456 kg, `Révolution.1`, `Poche.1`,
`Congé arête.2` — French names, seat timings of 0.5–2.9 s per operation, and a part in the
tree (`L2-catia-after-reboot.png`, title `Steel-base-plate-v2.CATPart`, tree carrying
`Corps principal`, `Kryova Construction`, `Steel`).

**It stopped on the context window**, not on a defect:

> The conversation no longer fits the model's 32768-token context window (32753 tokens sent),
> so the oldest messages were dropped before the model saw them.

That is the wall for a seat run of this length: 24+ operations at ~150 tokens of result each,
plus a 25-tool payload, exhausts `qwen3.5:9b`'s window before a four-feature part is finished.
The plate was right (0.75456 kg against 0.7555 expected); the boss and bore were undersized
and it had begun a `v2` part after catching its own Ø90-instead-of-Ø30 error.

`catia_capture_view` could not be obtained for this run: the conversation was already at its
context limit, so any further turn — including one asking only for a picture — fails the same
way. The application-window screenshot is the evidence that stands.

## Still open

- **The context window is now the binding constraint on the seat**, ahead of tool coverage.
  Worth measuring before the next ladder run: how many operations a part of this size costs,
  and whether the result payloads can be trimmed.
- The seat and the open kernel now agree on face words. **Nothing tests that they agree** —
  that is queue item B1 (`compare_backends`), still unrun with a real seat on its right-hand
  side.
