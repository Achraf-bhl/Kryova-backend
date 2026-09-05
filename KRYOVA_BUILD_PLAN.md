# Kryova — Build Plan

The short-term working queue: **one phase at a time, green before the next.**
[KRYOVA_MASTER_PLAN.md](KRYOVA_MASTER_PLAN.md) is the controlling document and carries the
phase status board (Part 2) — that board is *current state*. This file is the *history*:
one line lands in **Done** for every board change that is not `not started`.

This file was created on 2026-09-05, after `CLAUDE.md` and the master plan had both been
referring to it for some time and it did not exist. Everything before that date is
reconstructed from the board and `git log`, and is marked as such — a history that says
where it came from is worth more than one that quietly implies it was written as it
happened.

---

## Now

**E2 — Selection & authoring vocabulary.** 2.1–2.4 are done. 2.5 (Part Design
completion) is the open item and the one that decides the star.

Remaining before `*E2`:

1. **`catia_stiffener`** — thicken an open profile into a rib that runs until it meets
   the surrounding material. The interesting half is the *until*: the plate is grown
   past the walls and the part is subtracted, which leaves exactly the corner void, and
   only the piece the profile actually reaches is kept.
2. **`catia_draft` with a parting line** — currently refused as
   `_UNSUPPORTED_MODES["reflect_line"]` in `occt/operations/dressup.py`.
3. Then **2.6 (surfaces / GSD)**, which the master plan marks *deferrable within the
   era* — so the star may land before it if 2.6 moves to E-later.

Hardware-blocked and **not** counted against the star: the CATIA-seat halves of E1's and
E3's conformance runs need a Windows seat.

## Next

**E4 — Visual verification.** Nothing started. E3's OCCT side is complete, so the
measurement vocabulary a visual check would assert against already exists.

---

## Done

Newest first. Each line names the board row it moved and the commit that moved it.

- **2026-09-05** — E2 → *2.5 partial: swept features, drawn curves, threads*
  (`catia_rib`, `catia_slot`, `catia_thread`, and the open-curve sketch vocabulary —
  line, polyline, arc, three-point arc, ellipse, spline, axis). Coverage 53 → **63/201**.
  Drawn segments now chain, so four `catia_sketch_line` calls make a profile a pad can
  extrude; ribs verified against Pappus's theorem; a thread is an annotation that
  provably does not change the mass, and an unreadable designation reports no pitch
  rather than a guessed one. Measurement contract 1.1 → 1.3, and the version is now
  checked at import against the newest entry — it had already drifted once, which would
  have put a version into a provenance record in which four of its own quantities did
  not exist.
- **2026-09-05** — E2 → *2.5 continues: pad limits, multi-body, listings, solid combine*
  (`7ef04a6`). `up_to_next` / `up_to_last` / `up_to_plane` resolved against the geometry,
  all exact against hand-computed volumes. `up_to_next` means opposite things for a pad
  and a pocket and is tested both ways. Coverage 30 → 53/201.
- **2026-09-05** — E2 → *2.5 started: patterns, transforms, holes, thickness*
  (`eb4d89a`). A pattern repeats the *material a feature added*, recovered as a
  generation difference, so one implementation covers pad/pocket/shaft/boolean.
- **2026-09-05** — E1 → **DONE**, E2 → *2.1–2.4 DONE*, E3 → *OCCT side DONE*
  (`3b5faf0`). The OCCT kernel, interrogation, the measurement contract and the selection
  vocabulary land together. `feature#selector` resolves. Five regressions the phase
  introduced were caught by the first `pytest` run after it and fixed in the same commit.
- **2026-09-04** — E5 → *partial: foundation shipped*. `app/design/{assertions,diff,correct}.py`,
  109 tests, all offline. 5.1–5.4 remain open.
- **2026-09-03** — Design IR: a part is a specification the compiler builds, not a tree it
  edits (`2c54287`). This is what E2 and E5 are both built on.

### Reconstructed, not contemporaneous

Everything above 2026-09-05 was written on 2026-09-05 from the board and `git log`. The
dates and commits are real; the wording is not what was recorded at the time, because
nothing was.

---

## Known documentation gaps

Recorded here rather than fixed silently, because each is a decision someone has to make:

- `CLAUDE.md` references **`KRYOVA_PRD.md`** and **`KRYOVA_STATE_OF_THE_PROJECT.md`**.
  Neither exists in this repository. Either write them or stop pointing at them — a
  reference to a missing document sends the next reader looking for context that is not
  there, which is worse than saying the context does not exist.
- `KRYOVA_CAPABILITY_ROADMAP.md` is named as the audit E2 grew out of and is likewise
  absent from the working tree.
- `ADDED_SYMBOLS.md` is an untracked one-off dump of the symbols added between
  2026-09-01 and 2026-09-03. It is not referenced by anything; delete it or track it
  deliberately.
