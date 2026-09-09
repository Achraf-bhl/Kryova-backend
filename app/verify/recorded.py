"""Validation results recorded from a run, and the rule that stops one rotting.

Master plan 7.1/7.4, the join between them. `register.py` refuses to execute a
benchmark — a validation case is a mesh convergence study on an unauthenticated
public route, which is a denial-of-service tool with a nice name — so a case that
*runs* can only reach the published register as a **recorded** outcome. This
module is the recording.

## The problem a recorded result has and a live one does not

A number recorded on Tuesday is a claim about the code as it was on Tuesday.
Nothing stops the solver changing on Wednesday, and a register that kept
publishing "validated" would then be making a claim nobody has checked against
the code that would actually run — the exact failure Decision 3 exists to
prevent, arriving through the back door of a cache.

So the artefact carries a **fingerprint** of the code that produced it, and a
recorded outcome whose fingerprint no longer matches the working tree is not
published. It is not quietly refreshed and not silently dropped either: the
register says the results were discarded and why, so the visible state goes back
to "nothing is validated", which is the honest answer when the evidence is
stale.

**The fingerprint is deliberately coarse.** It covers every `.py` under
`app/solve/`, `app/mesh/` and `app/verify/` rather than the one solver a case
happened to use, because a benchmark's answer depends on the mesher, the load
vocabulary, the quantity reader and the convergence rule as much as on the
solver. Over-invalidating costs a re-run; under-invalidating publishes a claim
that is no longer true, and only one of those two is recoverable.

## Nothing here raises

Missing file, unreadable file, wrong schema version, a `Target` that will not
reconstruct — every one returns *no outcomes* with the reason recorded, on the
same rule `app.retrieval.KnowledgeService.search` follows: a published register
that 500s because an artefact is malformed publishes nothing at all, which is
strictly worse than publishing the eleven analyses and saying the benchmark
evidence could not be read.

## Writing one

```
venv/bin/python -m app.verify.recorded            # run the catalogue, rewrite the artefact
venv/bin/python -m app.verify.recorded --check    # exit non-zero if it is stale
```

The `--check` form is what belongs in CI: it re-runs nothing, it only compares
the recorded fingerprint against the tree, so it is instant and it fails the
build the moment a solver change orphans the published evidence.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Final

from app.verify.benchmarks import (
    BenchmarkOutcome,
    Outcome,
    Suite,
    Target,
    TargetBasis,
    run_suite,
)

#: Bumped when the artefact's shape changes. An artefact written by an older
#: version is discarded rather than read with today's assumptions — a validation
#: record read wrongly is worse than one not read at all.
SCHEMA_VERSION: Final = 1

#: Where the committed artefact lives. Under `data/` with the other things that
#: are produced by a run and read by the product, not under `app/`.
ARTEFACT_PATH: Final = Path(__file__).resolve().parents[2] / "data" / "verify" / (
    "validation-outcomes.json"
)

#: What the source of a recorded answer is: the solvers, the mesher, and the
#: four verification modules that decide what a case *is* and when its number may
#: be stated. A change anywhere in these orphans every recorded outcome.
#:
#: `register.py`, `commitments.py`, `changelog.py` and this module are
#: deliberately **out**. They publish a result and do not compute one, so
#: including them would invalidate the artefact every time somebody reworded a
#: note — and an artefact that goes stale for reasons nobody believes is one
#: people start regenerating without reading, which is the whole guard gone.
FINGERPRINTED: Final[tuple[str, ...]] = (
    "app/solve",
    "app/mesh",
    "app/verify/benchmarks.py",
    "app/verify/convergence.py",
    "app/verify/nafems.py",
    "app/verify/provenance.py",
    "app/verify/quantities.py",
)

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]


def _fingerprinted_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for entry in FINGERPRINTED:
        target = base / entry
        files.extend(target.rglob("*.py") if target.is_dir() else [target])
    return sorted(files)


def code_fingerprint(root: Path | None = None) -> str:
    """A digest of every source file a benchmark's answer depends on.

    Over the file *contents* keyed by relative path, sorted, so it is stable
    across machines and checkouts and moves the moment anything that decides an
    answer does.

    **Both normalisations below exist to make that first claim true, and it was
    false until 2026-09-09.** Measured on the Windows seat, where a recorded run
    made on Linux was discarded and the trust page published *nothing is
    validated* — the honest response to a fingerprint mismatch, and here a
    mismatch that meant nothing at all:

    * **The key is `as_posix()`, not `str()`.** `str(PurePath)` is
      `app\\solve\\deck.py` on Windows and `app/solve/deck.py` on Linux, so the
      same tree fingerprints differently by platform.
    * **CRLF is folded out of the content.** Git's `core.autocrlf=true` is the
      default on a Windows install, so every `.py` in the working tree arrives
      with CRLF while the committed blob has LF. Hashing raw bytes therefore
      hashes the checkout's line-ending policy along with the source.

    Neither alone is enough — the two together are what reproduce the digest a
    Linux recording carries, and `TestTheFingerprintIsStableAcrossCheckouts`
    pins each separately. Normalising here rather than demanding a
    `.gitattributes` because this must hold on any checkout it is handed,
    including one made before such a file existed.
    """
    base = _REPO_ROOT if root is None else root
    digest = hashlib.sha256()
    for path in _fingerprinted_files(base):
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).digest())
    return "sha256:" + digest.hexdigest()


def _target_from_dict(payload: dict[str, Any]) -> Target:
    """Rebuild a target through its own constructor.

    Through the constructor on purpose, the same argument `register._republish`
    makes: a frozen dataclass is only frozen against ordinary assignment, and
    every rule about what a target may claim lives in `__post_init__`. An
    artefact carrying a `PUBLISHED` target with no source therefore fails to
    load rather than being republished.
    """
    basis = TargetBasis(payload["basis"])
    if basis is TargetBasis.UNKNOWN:
        return Target(basis=basis, unit=payload["unit"], reason=payload.get("reason", ""))
    return Target(
        basis=basis,
        unit=payload["unit"],
        value=payload["value"],
        tolerance=payload["tolerance"],
        tolerance_reason=payload["tolerance_reason"],
        source=payload["source"],
    )


def _outcome_from_dict(payload: dict[str, Any]) -> BenchmarkOutcome:
    return BenchmarkOutcome(
        benchmark_id=payload["id"],
        title=payload["title"],
        analysis=payload["analysis"],
        outcome=Outcome(payload["outcome"]),
        target=_target_from_dict(payload["target"]),
        measured_value=payload.get("measured_value"),
        relative_deviation=payload.get("relative_deviation"),
        detail=payload.get("detail", ""),
        seconds=payload.get("seconds", 0.0),
        provenance=payload.get("provenance"),
        convergence=payload.get("convergence"),
    )


def record(suite: Suite, *, recorded_at: str) -> dict[str, Any]:
    """Run every case in `suite` and return the artefact to write.

    Takes the timestamp rather than reading the clock so the caller decides
    whether this is a real run or a test, and so an artefact is reproducible
    from its inputs.
    """
    outcomes = run_suite(suite, include_slow=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "suite": suite.name,
        "recorded_at": recorded_at,
        "code_fingerprint": code_fingerprint(),
        "outcomes": [outcome.to_dict() for outcome in outcomes],
    }


def load(path: Path | None = None) -> tuple[tuple[BenchmarkOutcome, ...], str]:
    """Recorded outcomes that still describe this working tree, and why not.

    Returns `((), reason)` for every way of having nothing: no artefact, an
    artefact from another schema, an artefact whose fingerprint no longer
    matches, or one that will not parse. The reason is published, so "there is
    no evidence" and "the evidence went stale on Wednesday" never read alike.
    """
    location = ARTEFACT_PATH if path is None else path
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (), (
            "No recorded benchmark run was found, so no case that this product can "
            "execute is reported here. Run `python -m app.verify.recorded` to record one."
        )
    except (OSError, json.JSONDecodeError) as exc:
        return (), f"The recorded benchmark run could not be read ({exc.__class__.__name__})."

    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return (), (
            "The recorded benchmark run was written by a different version of this "
            "module and was not read. Record it again."
        )

    recorded_fingerprint = payload.get("code_fingerprint")
    if recorded_fingerprint != code_fingerprint():
        return (), (
            "The recorded benchmark run was discarded: the solver, mesher or "
            "verification code has changed since it was made, so its results no longer "
            f"describe the code that would run today (recorded {payload.get('recorded_at')}). "
            "Nothing is published from a run that has been overtaken."
        )

    try:
        outcomes = tuple(_outcome_from_dict(item) for item in payload["outcomes"])
    except (KeyError, TypeError, ValueError) as exc:
        return (), (
            "The recorded benchmark run did not survive reconstruction and was not "
            f"published ({exc.__class__.__name__}: {exc})."
        )

    return outcomes, ""


def _main(argv: list[str]) -> int:
    from datetime import UTC, datetime

    from app.verify.nafems import NAFEMS_SUITE

    if "--check" in argv:
        _, reason = load()
        if reason:
            print(reason)
            return 1
        print(f"Recorded validation run is current: {ARTEFACT_PATH}")
        return 0

    artefact = record(NAFEMS_SUITE, recorded_at=datetime.now(UTC).isoformat())
    ARTEFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTEFACT_PATH.write_text(
        json.dumps(artefact, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    passed = sum(1 for o in artefact["outcomes"] if o["outcome"] == "validated")
    print(f"{passed}/{len(artefact['outcomes'])} validated -> {ARTEFACT_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover - a command, not a code path
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "ARTEFACT_PATH",
    "FINGERPRINTED",
    "SCHEMA_VERSION",
    "code_fingerprint",
    "load",
    "record",
]
