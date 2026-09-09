"""Verification and validation: whether a number is the model's answer or the mesh's.

Master plan Phase 7. Seven modules, and the split is the ASME V&V 20 split:

```
app/verify/
  quantities.py    the one scalar a study is about, read the same way at every level
  convergence.py   7.2 — Grid Convergence Index; an unconverged number cannot be stated
  provenance.py    7.3 — a result bound to geometry, mesh, material, case and solver
  benchmarks.py    7.1 — what a benchmark *is*, and the rules that stop one being invented
  nafems.py        7.1 — the catalogue: the standard benchmarks, encoded
  recorded.py      7.1 — a run recorded for publication, and when it stops being true
  register.py      7.4 — the roll-up, whose denominator is declared rather than discovered
  commitments.py   P10.3 — what Kryova will not claim, with the file that keeps each one
  changelog.py     P10.3 — the changes that move a number, and what to do about each
```

**What is here and what is not, stated plainly**, because the phase's own rule is
that an unmeasured claim is never a pass and the same applies to this package:

* 7.2 and 7.3 are implemented and tested (`tests/test_verify_convergence.py`,
  `tests/test_verify_provenance.py`, `tests/test_verify_quantities.py`).
* 7.1 has its machinery (`benchmarks.py`) and, since 2026-09-08, a catalogue
  (`nafems.py`): five standard NAFEMS cases, each carrying a target reproduced
  from a publicly readable vendor verification manual with the URL and the date
  it was read. **One of the five runs here** — FV52, the simply supported solid
  square plate — and it validates, converged over three grids, 1.06% from the
  published 44.092 Hz. The other four carry their published targets and a named
  blocker: two need a shape nobody has authored, one needs plane-stress
  elements, one needs a shell solver.
* 7.4's published validation register is `register.py`, and as of 2026-09-08 it
  says **1 of 11 analyses validated** — modal, against FV52 — where it had said
  0 since it was written. It never runs a benchmark: the four blocked cases come
  from the catalogue and FV52's result from a recorded run
  (`data/verify/validation-outcomes.json`), because a validation case is several
  solves and this page is served unauthenticated. **A recording that no longer
  describes the code is not published**: `recorded.py` fingerprints the solvers,
  the mesher and the four verification modules that decide an answer, and a
  mismatch takes the page back to "nothing is validated" with the reason stated.
  The closed-form checks listed beside several analyses are *verification*,
  carried in a field of their own, and they do not move a standing.
* P10.3's trust pages are served from `app/api/routes/trust.py`, unauthenticated,
  over `register.py`, `commitments.py` and `changelog.py` and nothing else. Every
  byte of that surface is a module constant; no route in it can reach a session.

`app/api/routes/trust.py` is the only importer of this package in the product.
"""
