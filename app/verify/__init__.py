"""Verification and validation: whether a number is the model's answer or the mesh's.

Master plan Phase 7. Seven modules, and the split is the ASME V&V 20 split:

```
app/verify/
  quantities.py    the one scalar a study is about, read the same way at every level
  convergence.py   7.2 — Grid Convergence Index; an unconverged number cannot be stated
  provenance.py    7.3 — a result bound to geometry, mesh, material, case and solver
  benchmarks.py    7.1 — what a benchmark *is*, and the rules that stop one being invented
  register.py      7.4 — the roll-up, whose denominator is declared rather than discovered
  commitments.py   P10.3 — what Kryova will not claim, with the file that keeps each one
  changelog.py     P10.3 — the changes that move a number, and what to do about each
```

**What is here and what is not, stated plainly**, because the phase's own rule is
that an unmeasured claim is never a pass and the same applies to this package:

* 7.2 and 7.3 are implemented and tested (`tests/test_verify_convergence.py`,
  `tests/test_verify_provenance.py`, `tests/test_verify_quantities.py`).
* 7.1 has its **machinery** and no cases. There is no NAFEMS catalogue in this
  codebase. Nothing in Kryova has been validated against a published benchmark,
  and `benchmarks.py` exists so that when one is written its targets cannot be
  invented — `TargetBasis.UNKNOWN` is how a case whose published value the author
  could not verify is encoded, and it forbids carrying a number.
* 7.4's published validation register is `register.py`, and it says that
  **nothing is validated**: it walks a declared list of every analysis the
  product reports a number from and attaches whatever outcomes exist, so eleven
  analyses come back `UNVALIDATED` with a reason rather than being omitted. The
  closed-form checks it lists beside several of them are *verification*, carried
  in a field of their own, and they do not move a standing.
* P10.3's trust pages are served from `app/api/routes/trust.py`, unauthenticated,
  over `register.py`, `commitments.py` and `changelog.py` and nothing else. Every
  byte of that surface is a module constant; no route in it can reach a session.

`app/api/routes/trust.py` is the only importer of this package in the product.
"""
