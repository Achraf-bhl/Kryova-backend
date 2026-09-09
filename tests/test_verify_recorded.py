"""A recorded validation result, and the day it stops being true — 7.1/7.4.

The register refuses to run a benchmark, so a case that *can* run reaches the
published page only as a recording. That buys a new failure mode the live path
never had: **a number recorded on Tuesday is a claim about Tuesday's code**, and
a page that kept publishing it after the solver changed would be asserting
something nobody has checked — Decision 3 defeated through a cache rather than
through a lie.

So the artefact carries a fingerprint of everything that decides an answer, and
this file pins both halves of what that fingerprint is for: it must move when a
solver, a mesher or a verification rule moves, and it must **not** move when
somebody rewords a note in the publisher. An artefact that goes stale for
reasons nobody believes is one people regenerate without reading, and then the
guard is gone while still appearing to be there.

The other half is that nothing here raises. Missing, truncated, from another
schema, or carrying a target that will not reconstruct — every one is "no
outcomes, and here is why", published as a note, because a trust page that 500s
publishes nothing at all.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.verify import recorded
from app.verify.benchmarks import Benchmark, Outcome, Suite, Target, TargetBasis
from app.verify.nafems import NAFEMS_SUITE
from app.verify.recorded import (
    ARTEFACT_PATH,
    FINGERPRINTED,
    SCHEMA_VERSION,
    code_fingerprint,
    load,
)
from app.verify.register import published_register

_BLOCKED = Suite(
    name="one-blocked-case",
    benchmarks=(
        Benchmark(
            id="only-case",
            title="a case that cannot run",
            analysis="modal",
            description="declared so the artefact has something in it",
            target=Target(
                basis=TargetBasis.PUBLISHED,
                unit="Hz",
                value=44.092,
                tolerance=0.05,
                tolerance_reason="the band the catalogue fixed for this quantity",
                source="a manual, read on a date",
            ),
            blocked_reason="needs an element family this codebase does not have",
        ),
    ),
)


def _artefact(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "outcomes.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestARecordedRunSurvivesTheRoundTrip:
    def test_what_was_recorded_is_what_loads(self, tmp_path: Path) -> None:
        artefact = recorded.record(_BLOCKED, recorded_at="2026-09-08T00:00:00+00:00")
        path = _artefact(tmp_path, artefact)

        outcomes, reason = load(path)

        assert reason == ""
        assert [o.benchmark_id for o in outcomes] == ["only-case"]
        assert outcomes[0].outcome is Outcome.BLOCKED
        assert outcomes[0].target.value == pytest.approx(44.092)
        assert outcomes[0].target.source == "a manual, read on a date"

    def test_the_artefact_names_the_suite_and_when_it_was_taken(self) -> None:
        artefact = recorded.record(_BLOCKED, recorded_at="2026-09-08T00:00:00+00:00")

        assert artefact["suite"] == "one-blocked-case"
        assert artefact["recorded_at"] == "2026-09-08T00:00:00+00:00"
        assert artefact["schema_version"] == SCHEMA_VERSION


class TestTheFingerprintIsStableAcrossCheckouts:
    """The claim in `code_fingerprint`'s own docstring, which was false.

    Measured on the Windows seat 2026-09-09: a run recorded on Linux was
    discarded and the trust page published *nothing is validated*, because the
    digest folded in two things that are properties of the checkout rather than
    of the source. Each is pinned separately here — normalising only one of them
    still does not reproduce a Linux recording, so a single combined test could
    pass with half the fix in place.
    """

    def _tree(self, root: Path, newline: bytes) -> Path:
        (root / "app/solve").mkdir(parents=True)
        (root / "app/mesh").mkdir(parents=True)
        (root / "app/verify").mkdir(parents=True)
        for rel in (
            "app/solve/linear_static.py",
            "app/mesh/primitives.py",
            "app/verify/benchmarks.py",
            "app/verify/convergence.py",
            "app/verify/nafems.py",
            "app/verify/provenance.py",
            "app/verify/quantities.py",
        ):
            (root / rel).write_bytes(b"def f():" + newline + b"    return 1" + newline)
        return root

    def test_line_endings_do_not_move_it(self, tmp_path: Path) -> None:
        """`core.autocrlf=true` is the default on a Windows git install, so the
        working tree has CRLF where the committed blob has LF. Hashing raw bytes
        hashes the checkout's line-ending policy along with the source."""
        lf = code_fingerprint(self._tree(tmp_path / "lf", b"\n"))
        crlf = code_fingerprint(self._tree(tmp_path / "crlf", b"\r\n"))

        assert lf == crlf

    def test_the_key_is_the_posix_path(self, tmp_path: Path) -> None:
        """`str(PurePath)` is `app\\solve\\deck.py` on Windows and
        `app/solve/deck.py` on Linux. The key must be the posix form on both.

        Checked against a digest computed here with posix keys, so it goes
        through `code_fingerprint` rather than restating `as_posix()`.
        **On a posix machine the two spellings are identical and this cannot
        fail** — it is a guard for Windows checkouts, and it is the
        artefact test below that catches the same fault on either.
        """
        tree = self._tree(tmp_path / "sep", b"\n")

        expected = hashlib.sha256()
        for path in recorded._fingerprinted_files(tree):
            expected.update(path.relative_to(tree).as_posix().encode("utf-8"))
            expected.update(b"\0")
            expected.update(hashlib.sha256(path.read_bytes()).digest())

        assert code_fingerprint(tree) == "sha256:" + expected.hexdigest()

    def test_the_committed_artefact_matches_this_checkout(self) -> None:
        """The end the two above serve: whatever platform this is read on, the
        recorded run must describe it. This is the assertion that failed."""
        recorded_fp = json.loads(ARTEFACT_PATH.read_text(encoding="utf-8"))[
            "code_fingerprint"
        ]

        assert code_fingerprint() == recorded_fp


class TestAFingerprintThatMovesWhenTheAnswerCould:
    def test_it_moves_when_a_solver_changes(self, tmp_path: Path) -> None:
        tree = tmp_path / "repo"
        (tree / "app/solve").mkdir(parents=True)
        (tree / "app/mesh").mkdir(parents=True)
        (tree / "app/verify").mkdir(parents=True)
        for entry in FINGERPRINTED:
            target = tree / entry
            if target.suffix == ".py":
                target.write_text("original\n", encoding="utf-8")
        solver = tree / "app/solve/linear_static.py"
        solver.write_text("original\n", encoding="utf-8")

        before = code_fingerprint(tree)
        solver.write_text("changed\n", encoding="utf-8")

        assert code_fingerprint(tree) != before

    def test_it_does_not_move_when_only_the_publisher_changes(self) -> None:
        """The exclusion is deliberate and is the difference between a guard
        people obey and one they learn to regenerate past."""
        # `as_posix`, not `str`: on Windows the latter gives `app\verify\...`
        # and every assertion below would fail on a platform difference rather
        # than on the exclusion this test is about.
        fingerprinted = {
            path.relative_to(Path(recorded._REPO_ROOT)).as_posix()
            for path in recorded._fingerprinted_files(Path(recorded._REPO_ROOT))
        }

        assert "app/verify/nafems.py" in fingerprinted
        assert "app/solve/modal.py" in fingerprinted
        assert "app/verify/register.py" not in fingerprinted
        assert "app/verify/recorded.py" not in fingerprinted
        assert "app/verify/changelog.py" not in fingerprinted

    def test_a_recording_from_other_code_is_discarded_with_the_date_it_was_made(
        self, tmp_path: Path
    ) -> None:
        artefact = recorded.record(_BLOCKED, recorded_at="2026-01-01T00:00:00+00:00")
        artefact["code_fingerprint"] = "sha256:" + "0" * 64
        path = _artefact(tmp_path, artefact)

        outcomes, reason = load(path)

        assert outcomes == ()
        assert "changed since" in reason
        assert "2026-01-01" in reason


class TestNothingHereRaises:
    def test_a_missing_artefact_says_how_to_make_one(self, tmp_path: Path) -> None:
        outcomes, reason = load(tmp_path / "absent.json")

        assert outcomes == ()
        assert "app.verify.recorded" in reason

    def test_a_truncated_artefact_is_reported_not_raised(self, tmp_path: Path) -> None:
        path = tmp_path / "outcomes.json"
        path.write_text('{"schema_version": 1, "outcomes": [', encoding="utf-8")

        outcomes, reason = load(path)

        assert outcomes == ()
        assert "could not be read" in reason

    def test_an_artefact_from_another_schema_is_not_read_with_todays_assumptions(
        self, tmp_path: Path
    ) -> None:
        artefact = recorded.record(_BLOCKED, recorded_at="2026-09-08T00:00:00+00:00")
        artefact["schema_version"] = SCHEMA_VERSION + 1
        path = _artefact(tmp_path, artefact)

        outcomes, reason = load(path)

        assert outcomes == ()
        assert "different version" in reason

    def test_a_target_that_would_not_be_allowed_today_fails_to_load(
        self, tmp_path: Path
    ) -> None:
        """Rebuilt through `Target.__post_init__`, so an artefact carrying a
        published number with no source cannot be republished by editing a file."""
        artefact = recorded.record(_BLOCKED, recorded_at="2026-09-08T00:00:00+00:00")
        outcomes = artefact["outcomes"]
        assert isinstance(outcomes, list)
        outcomes[0]["target"]["source"] = "   "
        path = _artefact(tmp_path, artefact)

        loaded, reason = load(path)

        assert loaded == ()
        assert "reconstruction" in reason


class TestTheCommittedArtefactIsTheOneThisCodeWouldProduce:
    def test_it_exists_and_is_current(self) -> None:
        """The CI guard, as a test.

        `python -m app.verify.recorded --check` is the same comparison. Having it
        here means a solver change that orphans the published evidence fails the
        suite rather than quietly reverting the trust page to "nothing is
        validated" — which is honest, but is not something anybody should
        discover from the website.
        """
        assert ARTEFACT_PATH.exists(), (
            "No recorded validation run is committed. Run "
            "`venv/bin/python -m app.verify.recorded`."
        )

        outcomes, reason = load()

        assert reason == "", reason
        assert {o.benchmark_id for o in outcomes} == {
            b.id for b in NAFEMS_SUITE.benchmarks
        }

    def test_it_records_fv52_as_validated(self) -> None:
        outcomes, _ = load()
        fv52 = next(o for o in outcomes if o.benchmark_id == "nafems-fv52")

        assert fv52.outcome is Outcome.VALIDATED
        assert fv52.relative_deviation is not None
        assert abs(fv52.relative_deviation) < 0.05


class TestTheRegisterPublishesOnlyEvidenceThatStillHolds:
    def test_a_current_recording_reaches_the_published_register(self) -> None:
        register = published_register()
        modal = next(row for row in register.rows if row.analysis.id == "modal")

        assert register.summary.validated == 2
        assert modal.accuracy is not None
        assert modal.accuracy.cases == 1

    def test_a_stale_recording_takes_the_page_back_to_nothing_validated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fallback is the blocked-only view, not a cached green tick."""
        from app.verify.nafems import CASES

        expected_blocked = sum(1 for case in CASES if case.blocker is not None)
        monkeypatch.setattr(
            recorded, "load", lambda *_args, **_kwargs: ((), "the code has changed")
        )
        register = published_register()

        assert register.summary.validated == 0
        assert register.summary.blocked == expected_blocked
        assert "the code has changed" in register.notes

    def test_the_reason_a_recording_was_discarded_is_published(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'There is no evidence' and 'the evidence went stale' must not read
        alike to somebody deciding whether to trust a number."""
        monkeypatch.setattr(
            recorded, "load", lambda *_args, **_kwargs: ((), "recorded run discarded: X")
        )
        payload = published_register().to_dict()

        assert any("discarded" in note for note in payload["notes"])
