"""Verification and validation: whether a number is the model's answer or the mesh's.

Master plan Phase 7. Four modules, and the split is the ASME V&V 20 split:

```
app/verify/
  quantities.py    the one scalar a study is about, read the same way at every level
  convergence.py   7.2 — Grid Convergence Index; an unconverged number cannot be stated
  provenance.py    7.3 — a result bound to geometry, mesh, material, case and solver
  benchmarks.py    7.1 — what a benchmark *is*, and the rules that stop one being invented
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
* 7.4's published validation register does not exist. `BenchmarkOutcome.to_dict`
  is the data it would be built from.

Nothing in the product imports this package yet.
"""
