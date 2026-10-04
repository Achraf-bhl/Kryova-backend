"""The solvers' own test corpora, run against the builds Kryova pins — master plan 20.4.

**What this answers, and the one word it must not borrow.** The assumption this
exists to kill is *"we ship CalculiX, so the solver is verified"*. Both solvers
publish a corpus of test cases, and both corpora are real, large and worth
running; neither is evidence of that sentence, and each is weaker or stronger
than it looks in a way that can be stated exactly. So this module runs them
against pinned builds, records what came back, and says — per corpus, derived
from the recorded run rather than typed — **what a green run establishes and
what it does not**.

## CalculiX: the examples compare against CalculiX's own earlier output

The manual describes its examples, in its own words (`CORPORA["calculix-2.20"]
.maintainer_says`), as simple examples suitable to test distinct features and
to check whether the installation is correct. The archive also carries what the
manual does not mention: a `compare` script, `datcheck.pl` and `frdcheck.pl`,
and a `.dat.ref` / `.frd.ref` beside most decks. The scripts flag a case whose
numbers differ from the reference by more than 0.1 % of the largest value in
the same block — so there **is** a tolerance, and a pass is a measurement.

**But the reference is CalculiX's own output.** `compare` refuses a `.dat`
whose line count differs from its `.dat.ref`, which is only a sensible rule
because the reference *is* a `.dat` — a recorded run of some CalculiX build, in
its own layout, line for line. Agreeing with it shows that this build reproduces
that build. It is regression evidence and installation evidence. It is **not**
agreement with an independently derived answer, so it is neither code
verification in ASME's sense (`app.verify.standards.DEFINITIONS`) nor anything
like validation; a defect present when the reference was recorded is present in
the reference.

**The harness is the maintainer's, not a re-implementation.** `compare` is
parsed out of the archive being run (`CompareRules`), and the numerical judgment
is `datcheck.pl` / `frdcheck.pl` themselves, invoked as shipped. The rules differ
between releases — 2.23 skips a refined-mesh deck 2.20 does not, and moves two
substructure `.mtx` files into place that 2.20 does not — and a harness that
typed one release's list would quietly run the other release wrong. A `compare`
with a rule `CompareRules` does not model is refused by name.

**The build is the fleet image's.** `Dockerfile` installs Debian bookworm's
`calculix-ccx`, which bookworm freezes at 2.20-1; that is the binary this
product's job layer shells out to in production, so it is the one run here,
with the 2.20 corpus. The Windows seat measured THE QUEUE's CalculiX facts on
2.23 — a different build, which this does not cover.

## code_aster: every check says where its reference value came from

code_aster installs its test base with the solver (4,540 cases in 18.0.12, every
one labelled `verification` in its `.export`). Each `TEST_RESU` check always
compares `VALE_CALC` — code_aster's own previously computed value — at
`TOLE_MACHINE` (default 1e-6): that half is non-regression. A check that also
names a `REFERENCE` compares `VALE_REFE` at `PRECISION` (default 1e-3), and the
catalogue types the reference as `ANALYTIQUE`, `SOURCE_EXTERNE`, `AUTRE_ASTER`
or `NON_DEFINI` (`code_aster/Cata/Commons/c_test_reference.py`). The run prints
every check with its type, so this module counts them by type rather than
counting passed cases, because a case is not one kind of evidence:

* `ANALYTIQUE` — a closed-form value. Agreement with one is **code
  verification**, the same kind `app.verify.benchmarks` records.
* `SOURCE_EXTERNE` — a value from outside code_aster. The catalogue does not say
  what the source is, and a case's documentation (the V-manual) does; this
  module does not read them, so it **does not classify these** — some may be
  another code's answer and some may be a measurement, and guessing would put
  the word validation on a check nobody read.
* `AUTRE_ASTER` — another code_aster computation: self-consistency.
* `NON_REGRESSION` / `NON_DEFINI` — code_aster's own earlier output: regression.

A case can also check itself in Python, through `code_aster.TestCase`, whose
assertions print ` OK  assertAlmostEqual passed` and name no reference at all.
They decide the job diagnostic the way a `TEST_RESU` line does, so they are
counted — as `unstated`, classified as nothing. Leaving them out was measured
to matter: on 18.0.12 it reported `sslp306a` — twenty assertions, among them
a plate's deflection against its closed form — as a case holding no check.

**code_aster answers no Kryova number today.** Decision 2 names it as the
federated specialist solver, and no module in `app/` invokes it. So its corpus
is run as the evidence the day it is integrated will need — pinned to the
conda-forge build a first integration would take — and the statement says so,
because a green code_aster run beside a CalculiX result is otherwise read as
being about that result.

## Where it runs

The nightly workflow (`.github/workflows/nightly.yml`) runs both against their
pins and compares each run to the recorded baseline in `data/verify/corpora/`.
**A case that reproduced in the baseline and does not now fails the job**;
cases that newly reproduce are reported and do not. The baseline records the
cases that did *not* reproduce on the pinned build too, with why — those are
published findings, never removed to make the file tidy.

**A code_aster time limit is not a check, so a run may scale it.** Every case
carries `P time_limit` in its `.export`, and a case that overruns it stops with
`<S>_CPU_LIMIT` — on the recording machine, fifteen cases that reproduced used
more than half their limit and one used 0.76 of it. A CI runner slower than that
machine would turn those into failures that are about the runner, so the nightly
passes `--time-factor` to `run_ctest`. The factor is recorded in the report, and
a run is refused comparison with a baseline recorded at a different one: under
two limits, a case that times out under one is neither a regression nor a
recovery.

This module imports nothing outside the standard library, because it runs
inside the `debian:bookworm-slim` container that holds the fleet's CalculiX,
under that image's own Python, where the project's virtualenv does not exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1

#: Where the recorded baselines live, one file per corpus id.
BASELINE_DIR: Final = Path(__file__).resolve().parents[2] / "data" / "verify" / "corpora"

#: Per-case ceiling. `app.solve.calculix.run.DEFAULT_TIMEOUT_S`'s value, for its
#: reason: a limit that fires mid-solve turns a slow answer into a failure.
DEFAULT_TIMEOUT_S: Final = 900.0


class CorpusError(Exception):
    """The corpus or the build is not the one this module was asked to run.

    Raised before anything runs, never for a case that fails: a failing case is
    a result, and a result belongs in the report.
    """


class Solver(StrEnum):
    CALCULIX = "calculix"
    CODE_ASTER = "code_aster"


class CaseOutcome(StrEnum):
    """What happened to one case. Only `REPRODUCED` is a pass."""

    #: Every comparison the corpus makes for this case came back within its own
    #: tolerance.
    REPRODUCED = "reproduced"
    #: The solver ran and at least one number fell outside the corpus's tolerance.
    DEVIATED = "deviated"
    #: The solver wrote no result file — it refused the deck or crashed.
    NO_OUTPUT = "no-output"
    #: The corpus ships no reference for this case, so nothing was compared.
    NO_REFERENCE = "no-reference"
    #: The result and its reference differ in line count, which `compare` treats
    #: as a failure before comparing a single number.
    SIZE_MISMATCH = "size-mismatch"
    #: The result file contains NaN.
    NOT_A_NUMBER = "nan"
    #: code_aster stopped with an error diagnostic.
    ERRORED = "errored"
    #: code_aster ran to the end and the case holds no check at all.
    NO_CHECKS = "no-checks"
    TIMED_OUT = "timed-out"
    #: Excluded by the corpus's own harness (a generated deck, for instance).
    SKIPPED = "skipped"


class ReferenceKind(StrEnum):
    """Where the value a check compared against came from.

    The distinction this module exists to keep: the same green tick means
    "matches a closed form" for one check and "matches what this solver printed
    last year" for another.
    """

    #: An earlier output of the same solver. Regression evidence only.
    RECORDED_OUTPUT = "recorded-output"
    #: A closed-form value. Code verification.
    CLOSED_FORM = "closed-form"
    #: From outside the solver, source unstated here. Not classified.
    EXTERNAL = "external"
    #: Another computation with the same solver. Self-consistency.
    SAME_SOLVER = "same-solver"
    #: An assertion in the case's own Python that names no reference. Counted,
    #: because it decides the job; classified as nothing, because it says nothing.
    UNSTATED = "unstated"


#: code_aster's `REFERENCE` vocabulary, as its catalogue spells it, onto ours.
#: `NON_REGRESSION` is not in the catalogue's `into=` list; it is the label the
#: run prints for the always-present `VALE_CALC` comparison.
ASTER_REFERENCES: Final[dict[str, ReferenceKind]] = {
    "NON_REGRESSION": ReferenceKind.RECORDED_OUTPUT,
    "NON_DEFINI": ReferenceKind.RECORDED_OUTPUT,
    "ANALYTIQUE": ReferenceKind.CLOSED_FORM,
    "SOURCE_EXTERNE": ReferenceKind.EXTERNAL,
    "AUTRE_ASTER": ReferenceKind.SAME_SOLVER,
}


@dataclass(frozen=True, slots=True)
class Corpus:
    """One solver's corpus, pinned to one build, with its sources."""

    id: str
    solver: Solver
    #: The build the corpus is run against, as its package manager names it.
    build: str
    #: Where that build comes from and what pins it.
    build_pin: str
    #: The version string the binary itself reports; checked before a run.
    reports_version: str
    #: The maintainer's own description of the corpus, quoted, with its source.
    maintainer_says: str
    #: Whether any Kryova code path invokes this solver today.
    answers_kryova_numbers: bool
    #: A downloadable archive and its SHA-256, or empty where the corpus is
    #: installed with the build itself.
    archive_url: str = ""
    archive_sha256: str = ""
    #: The test directory inside the archive.
    archive_root: str = ""

    def __post_init__(self) -> None:
        if not self.maintainer_says.strip() or "Read 20" not in self.maintainer_says:
            raise ValueError(
                f"{self.id}: the maintainer's description must be quoted with the "
                "document and the date it was read — a paraphrase from memory is "
                "exactly what `app.verify.standards` exists to stop."
            )
        if bool(self.archive_url) != bool(self.archive_sha256):
            raise ValueError(
                f"{self.id}: an archive URL needs its SHA-256, or the corpus run is "
                "whatever the server returned that day."
            )


CORPORA: Final[dict[str, Corpus]] = {
    "calculix-2.20": Corpus(
        id="calculix-2.20",
        solver=Solver.CALCULIX,
        build="calculix-ccx 2.20-1 (Debian bookworm, amd64)",
        build_pin=(
            "deb.debian.org pool/main/c/calculix-ccx/calculix-ccx_2.20-1_amd64.deb, "
            "SHA256 a448239f3caf5a324f9589a636e595e3e676153489f75843e509decb6809f346 "
            "(apt-cache show, 2026-09-14). The package the Dockerfile's runtime stage "
            "installs; bookworm is a frozen suite, so the name resolves to this build."
        ),
        reports_version="Version 2.20",
        maintainer_says=(
            "'The verification examples are simple examples suitable to test distinct "
            "features. They can be used to check whether the installation of CalculiX "
            "is correct, or to find examples when using a new feature.' — G. Dhondt, "
            "CalculiX CrunchiX User's Manual version 2.20, section 11 'Verification "
            "examples', p. 784, http://www.dhondt.de/ccx_2.20.pdf (SHA256 684564dd9dbd18"
            "e3da4e3c4443b8546a99ba62b71dc7f88e31cfe80eab4d0e57). Read 2026-09-14."
        ),
        answers_kryova_numbers=True,
        archive_url="http://www.dhondt.de/ccx_2.20.test.tar.bz2",
        archive_sha256="79848d88dd1e51839d1aed68fb547ff12ad3202c3561c02c2f3a8ceda0f2eb82",
        archive_root="CalculiX/ccx_2.20/test",
    ),
    "code_aster-18.0.12": Corpus(
        id="code_aster-18.0.12",
        solver=Solver.CODE_ASTER,
        build="code-aster 18.0.12 py312_nompi_h60fb801_0 (conda-forge, linux-64)",
        build_pin=(
            "conda-forge code-aster=18.0.12=py312_nompi_h60fb801_0 with python=3.12, "
            "solved by micromamba 2.9.0 on 2026-09-14 against libopenblas 0.3.34 and "
            "mumps-seq 5.8.2. The corpus is the share/aster/tests directory that build "
            "installs, so the build string pins both."
        ),
        reports_version="18.0.12",
        maintainer_says=(
            "Every one of the 4,540 .export files in share/aster/tests of this build "
            "carries 'P testlist ... verification'; the TEST_RESU catalogue types each "
            "reference as into=('ANALYTIQUE', 'SOURCE_EXTERNE', 'AUTRE_ASTER', "
            "'NON_DEFINI') with PRECISION defaut=1.0e-3 and VALE_CALC checked at "
            "TOLE_MACHINE defaut=1.0e-6 — code_aster/Cata/Commons/c_test_reference.py "
            "in the same build. Read 2026-09-14."
        ),
        answers_kryova_numbers=False,
    ),
}


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: str
    outcome: CaseOutcome
    seconds: float = 0.0
    #: Checks by where their reference came from, split by verdict:
    #: `{"closed-form": {"ok": 12, "nook": 0, "skip": 0}, ...}`.
    checks: dict[str, dict[str, int]] = field(default_factory=dict)
    #: The corpus's own words about a failure, verbatim, or the largest
    #: deviation it reported.
    detail: str = ""
    #: For a case that did not reproduce, the comment block at the top of its
    #: deck. The manual says to read it when an example gives problems, and on
    #: 2.20 it is where five of the twelve failures explain themselves ("run
    #: example beamwrite and copy beamwrite.rout to beamread.rin" — which
    #: `compare` itself never does).
    deck_says: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome is CaseOutcome.REPRODUCED

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"outcome": str(self.outcome), "seconds": round(self.seconds, 2)}
        if self.checks:
            out["checks"] = self.checks
        if self.detail:
            out["detail"] = self.detail
        if self.deck_says:
            out["deck_says"] = self.deck_says
        return out


# ---------------------------------------------------------------------------
# CalculiX
# ---------------------------------------------------------------------------

_SKIP_RE: Final = re.compile(r"if \[ \$i = (\S+) \]\s*then\s*continue\s*fi")
_MTX_RE: Final = re.compile(
    r"if \[ \$i = (\S+)\.inp \]\s*then\s*rm -f \S+\s*mv (\S+)\.mtx (\S+)\.dat\s*fi"
)
_ANY_CASE_RULE_RE: Final = re.compile(r"if \[ \$i = ")
_DEVIATION_RE: Final = re.compile(
    r"relative error w\.r\.t\. largest value within same block: ([0-9.eE+-]+) %"
)

#: The lines of `compare` whose behaviour `run_calculix_case` reproduces. A
#: script missing any of them is a script this module has not read.
_COMPARE_MUST_SAY: Final = (
    "OMP_NUM_THREADS=1",
    "./datcheck.pl",
    "./frdcheck.pl",
    'grep "NaN"',
    'grep "^ -5"',
    "wc -l",
)


@dataclass(frozen=True, slots=True)
class CompareRules:
    """The per-release rules in a CalculiX `compare` script."""

    #: Deck file names `compare` does not run.
    skipped: frozenset[str]
    #: Case names whose `.mtx` is moved over their `.dat` before comparing.
    mtx_as_dat: frozenset[str]

    @classmethod
    def parse(cls, script: str) -> CompareRules:
        missing = [line for line in _COMPARE_MUST_SAY if line not in script]
        if missing:
            raise CorpusError(
                "This compare script does not read the way the one this harness was "
                f"written from does: it never says {missing}. Read the script before "
                "running its corpus through a harness that assumes it."
            )
        skipped = frozenset(_SKIP_RE.findall(script))
        mtx = frozenset(name for name, a, b in _MTX_RE.findall(script) if name == a == b)
        rules = len(_ANY_CASE_RULE_RE.findall(script))
        if rules != len(skipped) + len(mtx):
            raise CorpusError(
                f"The compare script has {rules} per-case rule(s) and this harness "
                f"understood {len(skipped) + len(mtx)} ({sorted(skipped)} skipped, "
                f"{sorted(mtx)} .mtx moved). A rule it cannot read would run a case "
                "differently from the maintainer, so nothing is run."
            )
        return cls(skipped=skipped, mtx_as_dat=mtx)


def _line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 16), b""))


def _check(script: str, case: str, test_dir: Path) -> str:
    """Run `datcheck.pl` or `frdcheck.pl` as shipped and return what it printed."""
    completed = subprocess.run(
        ["perl", script, case],
        cwd=test_dir,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    return (completed.stdout + completed.stderr).strip()


def _largest_deviation(report: str) -> str:
    found = [float(value) for value in _DEVIATION_RE.findall(report)]
    if not found:
        return report.splitlines()[0] if report else ""
    return f"largest deviation {max(found):.4g} % of the block maximum"


def _run_solver(argv: Sequence[str], cwd: Path, timeout_s: float) -> bool:
    """Run one solve in its own process group; False when it timed out."""
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    with subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ) as process:
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_process_group(process.pid)
            process.wait()
            return False
    return True


def _kill_process_group(pid: int) -> None:
    """SIGKILL the whole group `start_new_session=True` created.

    POSIX only, and so is this whole harness: it runs the solvers' own suites
    under Linux — `debian:bookworm-slim` for CalculiX, a conda environment for
    code_aster, both named in the module docstring — and Windows has no
    equivalent of a process group killed by signal.

    Reached through `getattr` because `os.killpg` and `signal.SIGKILL` do not
    exist on Windows *at all*, so a bare reference is a type error there even
    inside a branch that can never run. Found the first time mypy was run on
    Windows, 2026-09-17; on Linux the names resolve and nothing reports it.
    """
    killpg = getattr(os, "killpg", None)
    sigkill = getattr(signal, "SIGKILL", None)
    if killpg is None or sigkill is None:  # pragma: no cover - POSIX in every real run
        raise RuntimeError(
            "Stopping a timed-out solver needs POSIX process groups, which this "
            "platform does not have. The corpora harness runs the solvers' suites "
            "on Linux; run it there."
        )
    killpg(pid, sigkill)


def deck_header(deck: Path, *, limit: int = 12) -> str:
    """The leading `**` comment lines of a CalculiX deck, without the markers."""
    lines = []
    try:
        with deck.open(errors="replace") as handle:
            for line in handle:
                if not line.startswith("**"):
                    break
                text = line[2:].strip()
                if text:
                    lines.append(text)
                if len(lines) >= limit:
                    break
    except OSError:
        return ""
    return " ".join(lines)


def run_calculix_case(
    test_dir: Path,
    case: str,
    rules: CompareRules,
    *,
    ccx: str = "ccx",
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> CaseResult:
    """One pass of `compare`'s loop body, for one deck, in the corpus directory.

    The order of the checks is `compare`'s, because the first one that fails is
    the one the maintainer's run would report.
    """
    result = _run_calculix_case(test_dir, case, rules, ccx=ccx, timeout_s=timeout_s)
    if result.passed or result.outcome is CaseOutcome.SKIPPED:
        return result
    return CaseResult(
        result.case,
        result.outcome,
        result.seconds,
        result.checks,
        result.detail,
        deck_header(test_dir / f"{case}.inp"),
    )


def _run_calculix_case(
    test_dir: Path, case: str, rules: CompareRules, *, ccx: str, timeout_s: float
) -> CaseResult:
    if f"{case}.inp" in rules.skipped:
        return CaseResult(case, CaseOutcome.SKIPPED, detail="skipped by compare")

    dat, frd = test_dir / f"{case}.dat", test_dir / f"{case}.frd"
    dat_ref, frd_ref = test_dir / f"{case}.dat.ref", test_dir / f"{case}.frd.ref"
    dat.unlink(missing_ok=True)
    frd.unlink(missing_ok=True)

    started = time.monotonic()
    finished = _run_solver([ccx, case], test_dir, timeout_s)
    seconds = time.monotonic() - started
    if not finished:
        return CaseResult(case, CaseOutcome.TIMED_OUT, seconds, detail=f"over {timeout_s:.0f} s")

    if case in rules.mtx_as_dat:
        dat.unlink(missing_ok=True)
        mtx = test_dir / f"{case}.mtx"
        if mtx.exists():
            mtx.rename(dat)

    if not dat.exists():
        return CaseResult(case, CaseOutcome.NO_OUTPUT, seconds, detail=f"{dat.name} does not exist")
    if not dat_ref.exists():
        return CaseResult(case, CaseOutcome.NO_REFERENCE, seconds, detail=f"{dat_ref.name} does not exist")
    if _line_count(dat) != _line_count(dat_ref):
        return CaseResult(case, CaseOutcome.SIZE_MISMATCH, seconds, detail=f"{dat.name} and its reference differ in size")
    if b"NaN" in dat.read_bytes():
        return CaseResult(case, CaseOutcome.NOT_A_NUMBER, seconds, detail=f"{dat.name} contains NaN")

    deviations = [_check("./datcheck.pl", case, test_dir)]

    frd_has_results = frd.exists() and any(
        line.startswith(" -5") for line in frd.read_text(errors="replace").splitlines()
    )
    compared_frd = False
    if frd_has_results or frd_ref.exists():
        if not frd.exists():
            return CaseResult(case, CaseOutcome.NO_OUTPUT, seconds, detail=f"{frd.name} does not exist")
        if not frd_ref.exists():
            return CaseResult(case, CaseOutcome.NO_REFERENCE, seconds, detail=f"{frd_ref.name} does not exist")
        if _line_count(frd) != _line_count(frd_ref):
            return CaseResult(case, CaseOutcome.SIZE_MISMATCH, seconds, detail=f"{frd.name} and its reference differ in size")
        deviations.append(_check("./frdcheck.pl", case, test_dir))
        compared_frd = True

    compared = 2 if compared_frd else 1
    printed = "\n".join(text for text in deviations if text)
    outcome = CaseOutcome.DEVIATED if printed else CaseOutcome.REPRODUCED
    verdict = "nook" if printed else "ok"
    checks = {str(ReferenceKind.RECORDED_OUTPUT): {"ok": 0, "nook": 0, "skip": 0}}
    checks[str(ReferenceKind.RECORDED_OUTPUT)][verdict] = compared
    return CaseResult(case, outcome, seconds, checks, _largest_deviation(printed))


def calculix_cases(test_dir: Path) -> list[str]:
    """The decks `compare`'s `for i in *.inp` visits, in its order (C collation)."""
    return sorted(path.name[: -len(".inp")] for path in test_dir.glob("*.inp"))


def run_calculix(
    test_dir: Path,
    *,
    ccx: str = "ccx",
    only: Iterable[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Iterator[CaseResult]:
    """Every case, sequentially, in one directory — as `compare` runs them.

    Sequential and in place because that is how the maintainer's run resolves
    any file one deck leaves for another. Splitting the corpus across
    directories was not measured, so it is not done: the whole 2.20 corpus is a
    few minutes on one core, and a faster run that answered differently would
    not be the maintainer's run.
    """
    rules = CompareRules.parse((test_dir / "compare").read_text(errors="replace"))
    wanted = set(only) if only is not None else None
    for case in calculix_cases(test_dir):
        if wanted is None or case in wanted:
            yield run_calculix_case(test_dir, case, rules, ccx=ccx, timeout_s=timeout_s)


def ccx_version(ccx: str = "ccx") -> str:
    completed = subprocess.run(
        [ccx, "-v"], capture_output=True, text=True, errors="replace", check=False, timeout=60
    )
    return (completed.stdout + completed.stderr).strip()


def fetch_archive(corpus: Corpus, into: Path) -> Path:
    """Download the corpus archive, refuse it unless the hash matches, unpack it."""
    if not corpus.archive_url:
        raise CorpusError(f"{corpus.id} is installed with its build; there is nothing to fetch.")
    into.mkdir(parents=True, exist_ok=True)
    archive = into / Path(corpus.archive_url).name
    if not archive.exists():
        with urllib.request.urlopen(corpus.archive_url, timeout=600) as response:  # noqa: S310
            with archive.open("wb") as out:
                shutil.copyfileobj(response, out)
    digest = sha256_of(archive)
    if digest != corpus.archive_sha256:
        raise CorpusError(
            f"{archive.name} hashes to {digest}, not the pinned {corpus.archive_sha256}. "
            "A corpus that changed under the same name is a different corpus; "
            "re-read it and re-pin rather than running it."
        )
    with tarfile.open(archive, "r:bz2") as tar:
        _extract(tar, into)
    test_dir = into / corpus.archive_root
    if not (test_dir / "compare").is_file():
        raise CorpusError(f"{archive.name} unpacked with no {corpus.archive_root}/compare.")
    return test_dir


def _extract(tar: tarfile.TarFile, into: Path) -> None:
    """Unpack, refusing a member that would land outside `into`.

    `filter="data"` does this, and is missing from bookworm's Python 3.11.2 —
    the interpreter the CalculiX corpus runs under — so the check is spelled out
    where the filter is absent rather than skipped.
    """
    if hasattr(tarfile, "data_filter"):
        try:
            tar.extractall(into, filter="data")
        except tarfile.FilterError as refused:
            raise CorpusError(f"The archive would unpack outside {into}: {refused}") from refused
        return
    root = into.resolve()
    for member in tar.getmembers():
        target = (into / member.name).resolve()
        if root not in (target, *target.parents) or member.issym() or member.islnk():
            raise CorpusError(f"The archive member {member.name!r} would unpack outside {into}.")
    tar.extractall(into)  # noqa: S202 - every member checked above


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# code_aster
# ---------------------------------------------------------------------------

_DIAGNOSTIC_RE: Final = re.compile(r"-+ DIAGNOSTIC JOB : (\S+)")
_ASTER_CHECK_RE: Final = re.compile(
    r"^\s*(OK|NOOK|SKIP)\s+(" + "|".join(ASTER_REFERENCES) + r")\b", re.MULTILINE
)
#: `code_aster.Utilities.Tester.TestCase.writeResult`'s two formats, on 18.0.12.
_ASTER_ASSERTION_RE: Final = re.compile(r"^\s*(OK|NOOK)\s+\w+ (?:passed|failed)\b", re.MULTILINE)


def read_aster_message(case: str, text: str | None, *, seconds: float = 0.0) -> CaseResult:
    """Classify one code_aster case from its `.mess` output."""
    if text is None:
        return CaseResult(case, CaseOutcome.NO_OUTPUT, seconds, detail="no .mess was written")

    checks: dict[str, dict[str, int]] = {}
    for verdict, label in _ASTER_CHECK_RE.findall(text):
        row = checks.setdefault(str(ASTER_REFERENCES[label]), {"ok": 0, "nook": 0, "skip": 0})
        row[verdict.lower()] += 1
    for verdict in _ASTER_ASSERTION_RE.findall(text):
        row = checks.setdefault(str(ReferenceKind.UNSTATED), {"ok": 0, "nook": 0, "skip": 0})
        row[verdict.lower()] += 1
    nook = sum(row["nook"] for row in checks.values())

    diagnostics = _DIAGNOSTIC_RE.findall(text)
    diagnostic = diagnostics[-1] if diagnostics else ""
    if not diagnostic:
        outcome = CaseOutcome.NO_OUTPUT
    elif "CPU_LIMIT" in diagnostic or "TIMELIMIT" in diagnostic:
        outcome = CaseOutcome.TIMED_OUT
    elif diagnostic.startswith(("<F>", "<S>", "<E>")):
        outcome = CaseOutcome.ERRORED
    elif diagnostic == "NO_TEST_RESU":
        outcome = CaseOutcome.NO_CHECKS
    elif diagnostic == "NOOK_TEST_RESU" or nook:
        outcome = CaseOutcome.DEVIATED
    elif diagnostic in ("OK", "<A>_ALARM"):
        outcome = CaseOutcome.REPRODUCED if checks else CaseOutcome.NO_CHECKS
    else:
        outcome = CaseOutcome.ERRORED
    return CaseResult(case, outcome, seconds, checks, f"diagnostic {diagnostic or 'missing'}")


def aster_cases(tests_dir: Path, family: str) -> list[str]:
    """Sequential cases of one family, as `run_ctest` selects them."""
    names = []
    for export in sorted(tests_dir.glob(f"{family}*.export")):
        labels = next(
            (line.split()[2:] for line in export.read_text(errors="replace").splitlines()
             if line.startswith("P testlist")),
            [],
        )
        if "sequential" in labels:
            names.append(export.stem)
    return names


def run_code_aster(
    tests_dir: Path,
    resutest: Path,
    *,
    family: str,
    run_ctest: str = "run_ctest",
    jobs: int = 1,
    only: Iterable[str] | None = None,
    time_factor: float = 1.0,
) -> Iterator[CaseResult]:
    """Run a family through code_aster's own `run_ctest`, then read each case.

    Each case's seconds are what ctest printed for it, read from the runner's
    log. Not the JUnit file `run_ctest` writes, which takes its times from
    ctest's cost data and, for a case that failed, records something else —
    `ssll501a` failed in 1.65 s on 18.0.12 and the file says 66.0, which is its
    time limit times 1.1.

    `resutest` must not exist. `run_ctest` meets an existing one by asking on
    the terminal whether to delete it, and with no terminal it dies on the
    question — measured 2026-09-14, when this function created the directory
    itself and the whole family came back as 664 cases with no output. So the
    directory is refused rather than removed (it may be somebody's last run),
    stdin is closed so a future prompt fails at once instead of waiting for a
    CI job's timeout, and a run that wrote no message for any case at all is
    the runner failing, raised with what it printed — never a report.
    """
    wanted = set(only) if only is not None else None
    cases = [case for case in aster_cases(tests_dir, family) if wanted is None or case in wanted]
    if not cases:
        return
    if resutest.exists():
        raise CorpusError(
            f"{resutest} already exists, and run_ctest would stop to ask whether to "
            "delete it. Point --work at a fresh directory."
        )
    resutest.parent.mkdir(parents=True, exist_ok=True)
    log = resutest.parent / "run_ctest.log"
    pattern = "_(" + "|".join(re.escape(case) for case in cases) + ")$"
    with log.open("w", encoding="utf-8") as out:
        subprocess.run(
            [
                *shlex.split(run_ctest), "-R", pattern, f"--resutest={resutest}",
                "-j", str(jobs), "--timefactor", repr(time_factor),
            ],
            cwd=resutest.parent,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            check=False,
        )
    said = log.read_text(encoding="utf-8", errors="replace")
    if not any((resutest / f"{case}.mess").exists() for case in cases):
        raise CorpusError(
            f"run_ctest wrote no message file for any of {len(cases)} case(s), so the "
            "runner failed rather than the cases. It said:\n"
            + "\n".join(said.strip().splitlines()[-12:])
        )
    timings = ctest_times(said)
    for case in cases:
        message = resutest / f"{case}.mess"
        text = message.read_text(errors="replace") if message.exists() else None
        yield read_aster_message(case, text, seconds=timings.get(case, 0.0))


#: ctest's per-test line: ` 85/664 Test  #648: ASTER_18.0.12_sslv154b ....***Failed  104.14 sec`.
_CTEST_TIME_RE: Final = re.compile(
    r"^\s*\d+/\d+ Test\s+#\d+: ASTER_[0-9.]+_(\w+) .*?([0-9]+(?:\.[0-9]+)?) sec\s*$", re.MULTILINE
)


def ctest_times(log: str) -> dict[str, float]:
    """Elapsed seconds per case, as ctest printed them — pass or fail."""
    return {name: float(seconds) for name, seconds in _CTEST_TIME_RE.findall(log)}


# ---------------------------------------------------------------------------
# The report, the baseline and the statement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CorpusReport:
    corpus: Corpus
    #: What the binary said about itself.
    reported_version: str
    results: tuple[CaseResult, ...]
    recorded_at: str
    seconds: float
    #: What every code_aster case's own time limit was multiplied by. 1.0 for
    #: CalculiX, whose corpus has no per-case limit to scale.
    time_factor: float = 1.0

    def outcome_counts(self) -> dict[str, int]:
        counts = Counter(str(result.outcome) for result in self.results)
        return {str(outcome): counts.get(str(outcome), 0) for outcome in CaseOutcome}

    def check_counts(self) -> dict[str, dict[str, int]]:
        totals: dict[str, dict[str, int]] = {
            str(kind): {"ok": 0, "nook": 0, "skip": 0} for kind in ReferenceKind
        }
        for result in self.results:
            for kind, row in result.checks.items():
                for verdict, count in row.items():
                    totals[kind][verdict] += count
        return totals

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "corpus": self.corpus.id,
            "build": self.corpus.build,
            "reported_version": self.reported_version,
            "recorded_at": self.recorded_at,
            "seconds": round(self.seconds, 1),
            "time_factor": self.time_factor,
            "outcomes": self.outcome_counts(),
            "checks": self.check_counts(),
            "statement": statement(self),
            "cases": {result.case: result.to_dict() for result in self.results},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CorpusReport:
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise CorpusError(
                f"A corpus report written with schema {payload.get('schema_version')!r}; "
                f"this module reads {SCHEMA_VERSION}. Re-record it."
            )
        corpus = CORPORA[payload["corpus"]]
        results = tuple(
            CaseResult(
                case=name,
                outcome=CaseOutcome(row["outcome"]),
                seconds=float(row.get("seconds", 0.0)),
                checks=row.get("checks", {}),
                detail=row.get("detail", ""),
                deck_says=row.get("deck_says", ""),
            )
            for name, row in payload["cases"].items()
        )
        return cls(
            corpus=corpus,
            reported_version=payload["reported_version"],
            results=results,
            recorded_at=payload["recorded_at"],
            seconds=float(payload["seconds"]),
            time_factor=float(payload["time_factor"]),
        )


def statement(report: CorpusReport) -> dict[str, list[str]]:
    """What this run establishes and what it does not, from its own numbers."""
    corpus = report.corpus
    counts = report.outcome_counts()
    checks = report.check_counts()
    ran = sum(counts.values()) - counts[str(CaseOutcome.SKIPPED)]
    reproduced = counts[str(CaseOutcome.REPRODUCED)]

    def total(kind: ReferenceKind) -> int:
        row = checks[str(kind)]
        return row["ok"] + row["nook"]

    establishes = [
        f"{reproduced} of {ran} cases in the {corpus.id} corpus reproduced on "
        f"{corpus.build} (the binary reports {report.reported_version!r}).",
    ]
    does_not = []

    if corpus.solver is Solver.CALCULIX:
        establishes.append(
            "Those cases produce the same numbers, within 0.1 % of each block's largest "
            "value, as the CalculiX build that recorded the reference files — this build "
            "is installed correctly and has not regressed on the features they exercise."
        )
        does_not += [
            "That any answer is correct. Every reference is an earlier CalculiX output, "
            "so a defect present when it was recorded is present in the reference; none "
            "of this is code verification against an independent answer.",
            "Anything about validation: no case compares against a measurement.",
            "Anything about the Windows seat's CalculiX 2.23, or any build but this one.",
            "Anything about Kryova's own decks, meshes or load vocabulary: these are the "
            "maintainer's decks, not the ones app/solve/calculix writes.",
        ]
    else:
        closed = checks[str(ReferenceKind.CLOSED_FORM)]
        establishes.append(
            f"{closed['ok']} of {total(ReferenceKind.CLOSED_FORM)} checks against a "
            "closed-form value (ANALYTIQUE) agreed within their stated PRECISION — code "
            "verification of code_aster, for the models those cases pose."
        )
        establishes.append(
            f"{checks[str(ReferenceKind.RECORDED_OUTPUT)]['ok']} non-regression checks "
            "reproduced code_aster's own earlier values."
        )
        does_not += [
            f"What the {total(ReferenceKind.EXTERNAL)} SOURCE_EXTERNE checks mean. Their "
            "source is stated in each case's documentation, which this harness does not "
            "read, so none is counted as verification of any kind and none as validation.",
            f"What the {total(ReferenceKind.UNSTATED)} assertions written in the cases' own "
            "Python compare against. code_aster prints them passed or failed with no "
            "reference, so none is counted as verification of any kind.",
            "Anything about a Kryova result. No Kryova code path invokes code_aster today; "
            "this is the evidence an integration would start from, not evidence about it.",
            "Parallel (MPI) behaviour: only cases labelled sequential were run.",
        ]
        if report.time_factor != 1.0:
            does_not.append(
                f"That each case finishes inside code_aster's own time limit: every limit "
                f"was multiplied by {report.time_factor:g} for this run."
            )
    return {"establishes": establishes, "does_not_establish": does_not}


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    #: Reproduced in the baseline and not now — a regression, and a failure.
    lost: tuple[str, ...]
    #: Did not reproduce in the baseline and does now — reported, not a failure.
    gained: tuple[str, ...]
    #: In the baseline and absent from this run.
    missing: tuple[str, ...]

    @property
    def regressed(self) -> bool:
        return bool(self.lost or self.missing)


def compare_to_baseline(report: CorpusReport, baseline: CorpusReport) -> BaselineComparison:
    if report.corpus.id != baseline.corpus.id:
        raise CorpusError(
            f"A {report.corpus.id} run cannot be compared with a {baseline.corpus.id} baseline."
        )
    if report.time_factor != baseline.time_factor:
        raise CorpusError(
            f"This run scaled every time limit by {report.time_factor:g} and the baseline by "
            f"{baseline.time_factor:g}. A case that times out under one limit and not the "
            "other would be counted as a regression or a recovery, and it is neither. "
            "Run at the baseline's factor, or re-record the baseline at this one."
        )
    now = {result.case: result for result in report.results}
    then = {result.case: result for result in baseline.results}
    lost = tuple(sorted(c for c, r in then.items() if r.passed and c in now and not now[c].passed))
    gained = tuple(sorted(c for c, r in now.items() if r.passed and c in then and not then[c].passed))
    missing = tuple(sorted(c for c in then if c not in now))
    return BaselineComparison(lost=lost, gained=gained, missing=missing)


def require_build(corpus: Corpus, reported: str) -> None:
    """Refuse to run a corpus against a binary that is not its pinned build.

    The version must stand alone: 2.20 is not a prefix match for 2.201.
    """
    if not re.search(re.escape(corpus.reports_version) + r"(?![\w.]*\d)", reported):
        raise CorpusError(
            f"{corpus.id} is pinned to {corpus.build}, whose binary reports "
            f"{corpus.reports_version!r}; this one reports {reported[:200]!r}. A run on "
            "another build would be recorded as evidence about the pinned one."
        )


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.verify.corpora", description=__doc__.splitlines()[0])
    parser.add_argument("corpus", choices=sorted(CORPORA))
    parser.add_argument("--work", type=Path, required=True, help="scratch directory for the run")
    parser.add_argument("--report", type=Path, help="where to write this run's JSON report")
    parser.add_argument("--baseline", type=Path, help="fail if a case the baseline reproduced does not now")
    parser.add_argument("--only", nargs="*", help="run just these cases")
    parser.add_argument("--ccx", default="ccx")
    parser.add_argument("--run-ctest", default="run_ctest")
    parser.add_argument("--aster-tests", type=Path, help="share/aster/tests of the pinned build")
    parser.add_argument("--family", default="ssl", help="code_aster case-name prefix (V3 is ssl)")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument(
        "--time-factor", type=float, default=1.0,
        help="multiply every code_aster case's own time limit (run_ctest --timefactor)",
    )
    args = parser.parse_args(argv)
    if args.only is not None and args.baseline is not None:
        parser.error(
            "--only and --baseline together would report every case left out as missing "
            "from the run, which reads as a regression. Compare a whole run, or none."
        )

    corpus = CORPORA[args.corpus]
    if corpus.solver is Solver.CALCULIX and args.time_factor != 1.0:
        parser.error(
            "CalculiX's examples carry no time limit of their own to scale; --timeout is "
            "the one limit that run has."
        )
    baseline = None
    if args.baseline:
        # Read before the run, so a baseline it cannot be compared with is
        # refused now rather than after an hour of solving.
        baseline = CorpusReport.from_dict(json.loads(args.baseline.read_text(encoding="utf-8")))
        if baseline.time_factor != args.time_factor:
            parser.error(
                f"the baseline was recorded with --time-factor {baseline.time_factor:g}, and a "
                f"run at {args.time_factor:g} would count a case timing out under only one of "
                "the two limits as a regression or a recovery."
            )
    started = time.monotonic()
    if corpus.solver is Solver.CALCULIX:
        reported = ccx_version(args.ccx)
        require_build(corpus, reported)
        test_dir = fetch_archive(corpus, args.work)
        results = tuple(run_calculix(test_dir, ccx=args.ccx, only=args.only, timeout_s=args.timeout))
    else:
        if args.aster_tests is None:
            parser.error("--aster-tests is required for a code_aster corpus")
        reported = aster_version(args.aster_tests)
        require_build(corpus, reported)
        results = tuple(
            run_code_aster(
                args.aster_tests,
                args.work / "resutest",
                family=args.family,
                run_ctest=args.run_ctest,
                jobs=args.jobs,
                only=args.only,
                time_factor=args.time_factor,
            )
        )

    report = CorpusReport(
        corpus=corpus,
        reported_version=reported.splitlines()[0] if reported else "",
        results=results,
        recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        seconds=time.monotonic() - started,
        time_factor=args.time_factor,
    )
    payload = report.to_dict()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"{corpus.id} on {corpus.build}: {payload['outcomes']}")
    for heading, lines in payload["statement"].items():
        print(heading.replace("_", " ") + ":")
        for line in lines:
            print(f"  - {line}")

    if baseline is not None:
        comparison = compare_to_baseline(report, baseline)
        if comparison.gained:
            print(f"Newly reproducing (not a failure): {', '.join(comparison.gained)}")
        if comparison.regressed:
            print(f"REGRESSED — reproduced in the baseline and not now: {', '.join(comparison.lost)}")
            if comparison.missing:
                print(f"Missing from this run: {', '.join(comparison.missing)}")
            return 1
    return 0


def aster_version(tests_dir: Path) -> str:
    """The version the build's installation records beside its test base.

    `share/aster/config.json`'s `version_tag`, which is where 18.0.12 from
    conda-forge keeps it (`config.txt` next to it carries no version at all).
    Every `.mess` also prints `Version 18.0.12` in its banner; this is read
    before the run instead, so a wrong build is refused before an hour of it.
    """
    config = tests_dir.parent / "config.json"
    try:
        tag = json.loads(config.read_text(encoding="utf-8")).get("version_tag", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return f"{config.parent.name}/config.json version_tag {tag}" if tag else ""


if __name__ == "__main__":  # pragma: no cover - a command, not a code path
    try:
        sys.exit(_main(sys.argv[1:]))
    except CorpusError as refused:
        print(f"Refused: {refused}", file=sys.stderr)
        sys.exit(2)
