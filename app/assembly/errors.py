"""What can go wrong in a product structure, said so the next move is obvious.

Three failures, and they are three different recoveries — the same argument
`app.design.assertions` makes for keeping `FAILED` and `UNMEASURED` apart, and the same
one `app.dynamics.errors` makes for keeping a malformed mechanism apart from a missing
engine:

* `StructureError` — the product as *described* is not a product. An instance names a
  component that does not exist, a component contains itself, two instances claim the
  same occurrence path. Fix the definition; nothing downstream can run.
* `ContractError` — the interface between two components is malformed, or the two sides
  disagree about a parameter they both import. Fix the contract, or one of the two
  specs — and the message names **both** parties, because a contract violation with
  only one name on it sends the wrong team to look.
* `LockError` — the product is fine and *somebody else is holding it*. A lease on a
  component another author has taken, or a commit written against a revision that is
  no longer the head. Nothing is wrong with either version; the recovery is to wait,
  to take the change somewhere else, or to merge — so it is deliberately not a
  `StructureError`, which means "fix the definition".
* `MergeConflict` — two authors changed the same component from the same base and
  arithmetic cannot say which is right. A `LockError`, because it is the same family
  of problem seen after the fact, and it names **both** sides for `ContractError`'s
  reason.
* `AssemblyError` — the base, so a caller that does not care can catch one thing.

Note what is *not* here. A contract whose claim came out false is not an exception: it
is an `AssertionResult` with outcome `FAILED`, and a claim that could not be measured is
`UNMEASURED`. Raising on either would make a clash check abort on the first bad pair and
report nothing about the other 4,999 — which is precisely the failure mode
`measure_clearance` already refuses one layer down.

Every message ends with what to do. "Invalid assembly" is not an acceptable string in
this package.
"""

from __future__ import annotations


class AssemblyError(RuntimeError):
    """Something in the product-structure layer could not be done as asked."""


class StructureError(AssemblyError):
    """The product structure is not well formed and cannot be walked."""


class ContractError(AssemblyError):
    """An interface contract is malformed, or its two sides cannot both be satisfied."""


class LockError(AssemblyError):
    """Someone else holds this part of the product, or the work is written on a stale base."""


class MergeConflict(LockError):
    """Two authors changed the same component from the same base."""


__all__ = [
    "AssemblyError",
    "ContractError",
    "LockError",
    "MergeConflict",
    "StructureError",
]
