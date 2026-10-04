"""The solvers' own corpora, and what a green run of them may be said to mean — E20.4.

The claim this file guards is narrow and easy to inflate: *this build reproduces
that corpus*, stated per kind of reference, never *the solver is verified*. So
most of what is pinned here is the harness judging a case exactly the way the
maintainer's own `compare` does (a harness that disagreed with it would be a
different corpus), and the statement being derived from the run rather than
typed. The recorded baselines themselves come from real runs on the pinned
builds, and the last class checks them against what the maintainer's script
said about the same build.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from app.verify import corpora
from app.verify.corpora import (
    CORPORA,
    BaselineComparison,
    CaseOutcome,
    CaseResult,
    CompareRules,
    CorpusError,
    CorpusReport,
    ReferenceKind,
    Solver,
    compare_to_baseline,
    read_aster_message,
    run_calculix_case,
    statement,
)

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# A compare script in the shape the two releases actually ship
# ---------------------------------------------------------------------------

#: The loop body both releases share, trimmed to the lines `CompareRules`
#: requires. The per-case rules are added per test.
COMPARE_BODY = """#!/bin/sh

export OMP_NUM_THREADS=1

for i in *.inp; do
{rules}
        ~/CalculiX/CalculiX  ${{i%.inp}} >> $tempfile 2>&1
{mtx}
        if grep "NaN" ${{i%.inp}}.dat ; then
           continue
        fi
        export sum1=`wc -l ${{i%.inp}}.dat | awk '{{print$1}}'`
        ./datcheck.pl ${{i%.inp}} >> $errorfile
        if grep "^ -5" ${{i%.inp}}.frd >| abc  ||[ -f ${{i%.inp}}.frd.ref ] ; then
            ./frdcheck.pl ${{i%.inp}} >> $errorfile
        fi
done
"""


def _skip(name: str) -> str:
    return f"\tif [ $i = {name} ]\n\tthen\n\t    continue\n\tfi\n"


def _mtx(name: str) -> str:
    return (
        f"\tif [ $i = {name}.inp ]\n\tthen\n\t    rm -f {name}.dat\n"
        f"\t    mv {name}.mtx {name}.dat\n\tfi\n"
    )


def compare_script(skips: tuple[str, ...] = (), mtx: tuple[str, ...] = ()) -> str:
    return COMPARE_BODY.format(
        rules="".join(_skip(name) for name in skips), mtx="".join(_mtx(name) for name in mtx)
    )


class TestCompareRulesAreReadFromTheArchive:
    def test_a_2_20_shaped_script_skips_its_own_list(self) -> None:
        skips = ("circ10pcent.rfn.inp", "circ10p.rfn.inp", "segmentsmooth.rfn.inp")

        rules = CompareRules.parse(compare_script(skips))

        assert rules.skipped == frozenset(skips)
        assert rules.mtx_as_dat == frozenset()

    def test_a_2_23_shaped_script_also_moves_the_substructure_matrices(self) -> None:
        rules = CompareRules.parse(
            compare_script(("circ11p.rfn.inp",), ("substructure", "substructure2"))
        )

        assert rules.skipped == {"circ11p.rfn.inp"}
        assert rules.mtx_as_dat == {"substructure", "substructure2"}

    def test_a_per_case_rule_it_cannot_read_stops_the_run(self) -> None:
        """A rule the harness skips over would run that case differently from
        the maintainer and report the difference as the build's."""
        script = compare_script().replace(
            "for i in *.inp; do\n",
            "for i in *.inp; do\n\tif [ $i = odd.inp ]\n\tthen\n\t    cp odd.x odd.y\n\tfi\n",
        )

        with pytest.raises(CorpusError, match="per-case rule"):
            CompareRules.parse(script)

    def test_a_script_that_judges_numbers_some_other_way_is_refused(self) -> None:
        script = compare_script().replace("./datcheck.pl", "./newcheck.py")

        with pytest.raises(CorpusError, match="datcheck.pl"):
            CompareRules.parse(script)


# ---------------------------------------------------------------------------
# One case, judged the way `compare` judges it
# ---------------------------------------------------------------------------

#: A stand-in `ccx`: reads `<case>.plan` (JSON) from the working directory and
#: writes the files it names. What the real solver does is not the question
#: here; what the harness makes of the files is.
FAKE_CCX = """#!{python}
import json, pathlib, sys, time
case = sys.argv[1]
plan = json.loads(pathlib.Path(case + ".plan").read_text())
time.sleep(plan.get("sleep", 0))
for name, text in plan.get("write", {{}}).items():
    pathlib.Path(name).write_text(text)
"""

#: Stand-ins for `datcheck.pl` / `frdcheck.pl`: print the maintainer's
#: deviation wording when a marker file says to, nothing otherwise.
FAKE_CHECK = """#!/usr/bin/perl
$file=$ARGV[0];
if (-e "$file.{kind}.deviates") {{
    open M, "<$file.{kind}.deviates"; $pct=<M>; chomp $pct;
    print "deviation in file $file.{kind}\\n";
    print "            relative error w.r.t. largest value within same block: $pct %\\n\\n";
}}
"""


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    test_dir = tmp_path / "test"
    test_dir.mkdir()
    ccx = test_dir / "fake_ccx"
    ccx.write_text(FAKE_CCX.format(python=sys.executable))
    for kind, script in (("dat", "datcheck.pl"), ("frd", "frdcheck.pl")):
        (test_dir / script).write_text(FAKE_CHECK.format(kind=kind))
    for path in (ccx, test_dir / "datcheck.pl", test_dir / "frdcheck.pl"):
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    (test_dir / "compare").write_text(compare_script())
    return test_dir


def _case(test_dir: Path, name: str, *, write: dict[str, str], refs: dict[str, str], **plan: object) -> None:
    (test_dir / f"{name}.inp").write_text(
        "**\n**   Structure: a test deck.\n**   Test objective: what the harness makes of it.\n**\n*NODE\n"
    )
    (test_dir / f"{name}.plan").write_text(json.dumps({"write": write, **plan}))
    for suffix, text in refs.items():
        (test_dir / f"{name}.{suffix}").write_text(text)


def _run(test_dir: Path, name: str, rules: CompareRules | None = None, **kwargs: float) -> CaseResult:
    return run_calculix_case(
        test_dir,
        name,
        rules or CompareRules.parse((test_dir / "compare").read_text()),
        ccx=str(test_dir / "fake_ccx"),
        **kwargs,
    )


DAT = "  1  2.0000E+00\n  2  3.0000E+00\n"
FRD = "    1C\n -4  DISP\n -5  D1\n -1         1 1.00000E+00\n -3\n"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "the corpora harness is POSIX-only and so is this fixture: the fake solver "
        "is a #! script and the check scripts are perl, so Windows answers "
        "WinError 193 rather than running them. The harness itself runs the "
        "solvers' own suites under Linux (debian:bookworm-slim for CalculiX, a "
        "conda environment for code_aster) -- see app/verify/corpora.py's "
        "_kill_process_group. A skip is not a pass; these run in CI on Linux."
    ),
)
class TestACaseIsJudgedTheWayCompareJudgesIt:
    def test_matching_outputs_reproduce(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "beam", write={"beam.dat": DAT, "beam.frd": FRD},
              refs={"dat.ref": DAT, "frd.ref": FRD})

        result = _run(corpus_dir, "beam")

        assert result.outcome is CaseOutcome.REPRODUCED
        assert result.passed
        assert result.checks == {"recorded-output": {"ok": 2, "nook": 0, "skip": 0}}
        assert result.deck_says == "", "a passing case needs no explanation"

    def test_a_deviation_reported_by_datcheck_is_a_deviation_with_its_size(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "beam", write={"beam.dat": DAT}, refs={"dat.ref": DAT})
        (corpus_dir / "beam.dat.deviates").write_text("3.836780")

        result = _run(corpus_dir, "beam")

        assert result.outcome is CaseOutcome.DEVIATED
        assert "3.837 %" in result.detail
        assert result.checks["recorded-output"]["nook"] == 1

    def test_a_deviation_in_the_frd_alone_still_fails_the_case(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "seg", write={"seg.dat": DAT, "seg.frd": FRD},
              refs={"dat.ref": DAT, "frd.ref": FRD})
        (corpus_dir / "seg.frd.deviates").write_text("148.364447")

        result = _run(corpus_dir, "seg")

        assert result.outcome is CaseOutcome.DEVIATED
        assert "148.4 %" in result.detail

    def test_no_dat_is_no_output_and_the_deck_explains_itself(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "read", write={}, refs={"dat.ref": DAT})

        result = _run(corpus_dir, "read")

        assert result.outcome is CaseOutcome.NO_OUTPUT
        assert "Test objective: what the harness makes of it." in result.deck_says

    def test_no_reference_is_not_a_pass(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "gen", write={"gen.dat": DAT}, refs={})

        assert _run(corpus_dir, "gen").outcome is CaseOutcome.NO_REFERENCE

    def test_a_different_line_count_fails_before_any_number_is_compared(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "beam", write={"beam.dat": DAT + "  3  4.0E+00\n"}, refs={"dat.ref": DAT})
        (corpus_dir / "beam.dat.deviates").write_text("99")

        result = _run(corpus_dir, "beam")

        assert result.outcome is CaseOutcome.SIZE_MISMATCH
        assert "99" not in result.detail, "compare stops at the size check"

    def test_nan_in_the_dat_is_its_own_failure(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "beam", write={"beam.dat": "  1  NaN\n  2  3.0E+00\n"}, refs={"dat.ref": DAT})

        assert _run(corpus_dir, "beam").outcome is CaseOutcome.NOT_A_NUMBER

    def test_an_frd_with_results_needs_a_reference_too(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "beam", write={"beam.dat": DAT, "beam.frd": FRD}, refs={"dat.ref": DAT})

        assert _run(corpus_dir, "beam").outcome is CaseOutcome.NO_REFERENCE

    def test_an_frd_with_no_result_blocks_and_no_reference_is_not_compared(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "beam", write={"beam.dat": DAT, "beam.frd": "    1C\n -3\n"}, refs={"dat.ref": DAT})

        result = _run(corpus_dir, "beam")

        assert result.outcome is CaseOutcome.REPRODUCED
        assert result.checks["recorded-output"]["ok"] == 1

    def test_stale_outputs_from_an_earlier_run_are_removed_first(self, corpus_dir: Path) -> None:
        """Otherwise a deck the solver now refuses is judged on last run's file."""
        _case(corpus_dir, "beam", write={}, refs={"dat.ref": DAT})
        (corpus_dir / "beam.dat").write_text(DAT)

        assert _run(corpus_dir, "beam").outcome is CaseOutcome.NO_OUTPUT

    def test_a_skipped_deck_is_not_run(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "circ.rfn", write={"circ.rfn.dat": DAT}, refs={})

        result = _run(corpus_dir, "circ.rfn", CompareRules(frozenset({"circ.rfn.inp"}), frozenset()))

        assert result.outcome is CaseOutcome.SKIPPED
        assert not (corpus_dir / "circ.rfn.dat").exists()

    def test_a_substructure_matrix_is_compared_as_the_dat(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "sub", write={"sub.dat": "wrong\n", "sub.mtx": DAT}, refs={"dat.ref": DAT})

        result = _run(corpus_dir, "sub", CompareRules(frozenset(), frozenset({"sub"})))

        assert result.outcome is CaseOutcome.REPRODUCED

    def test_a_solve_over_the_limit_is_timed_out_and_killed(self, corpus_dir: Path) -> None:
        _case(corpus_dir, "slow", write={"slow.dat": DAT}, refs={"dat.ref": DAT}, sleep=30)

        result = _run(corpus_dir, "slow", timeout_s=0.5)

        assert result.outcome is CaseOutcome.TIMED_OUT
        assert result.seconds < 10

    def test_cases_run_in_compares_order(self, corpus_dir: Path) -> None:
        for name in ("beamb", "Zeta", "beam10", "beam2"):
            (corpus_dir / f"{name}.inp").write_text("*NODE\n")

        assert corpora.calculix_cases(corpus_dir) == ["Zeta", "beam10", "beam2", "beamb"]


# ---------------------------------------------------------------------------
# code_aster: a check is counted by where its reference came from
# ---------------------------------------------------------------------------

#: Lines as sslv100a printed them on the pinned build, 2026-09-14.
ASTER_OK = """
 ---- RESULTAT         NUME_ORDRE       NOM_CHAM         NOM_CMP          GROUP_NO
      00000006         1                DEPL             DX               A
      REFERENCE        LEGENDE          VALE_REFE               VALE_CALC               ERREUR           TOLE
 OK   NON_REGRESSION   XXXX             5.71536019E-05          5.715360187903981E-05    3.667344E-08%   9.999999999999999E-05%
 OK   ANALYTIQUE       XXXX             5.72E-05                5.715360187903981E-05    8.111560E-02%   1.0%
SKIP  NON_REGRESSION   -                -                  -                  -                -
 OK   ANALYTIQUE       XXXX             0.0                0.0                0.0              1E-10
 OK   SOURCE_EXTERNE   XXXX             1.0                1.0                0.0              1.0%
 OK   AUTRE_ASTER      XXXX             2.0                2.0                0.0              1.0%

------------------------------------------------------------
--- DIAGNOSTIC JOB : OK
------------------------------------------------------------
"""


class TestAsterChecksAreCountedByReference:
    def test_a_passing_case_is_counted_by_kind(self) -> None:
        result = read_aster_message("sslv100a", ASTER_OK)

        assert result.outcome is CaseOutcome.REPRODUCED
        assert result.checks == {
            "recorded-output": {"ok": 1, "nook": 0, "skip": 1},
            "closed-form": {"ok": 2, "nook": 0, "skip": 0},
            "external": {"ok": 1, "nook": 0, "skip": 0},
            "same-solver": {"ok": 1, "nook": 0, "skip": 0},
        }

    def test_one_nook_is_a_deviation(self) -> None:
        text = ASTER_OK.replace(" OK   ANALYTIQUE       XXXX             5.72E-05", "NOOK  ANALYTIQUE       XXXX             5.72E-05")
        text = text.replace("DIAGNOSTIC JOB : OK", "DIAGNOSTIC JOB : NOOK_TEST_RESU")

        result = read_aster_message("sslv100a", text)

        assert result.outcome is CaseOutcome.DEVIATED
        assert result.checks["closed-form"] == {"ok": 1, "nook": 1, "skip": 0}

    def test_a_nook_line_under_an_ok_diagnostic_is_still_a_deviation(self) -> None:
        """The per-check line is the evidence; the job diagnostic is a summary."""
        text = ASTER_OK.replace(" OK   AUTRE_ASTER", "NOOK  AUTRE_ASTER")

        assert read_aster_message("x", text).outcome is CaseOutcome.DEVIATED

    @pytest.mark.parametrize(
        ("diagnostic", "outcome"),
        [
            ("<F>_ERROR", CaseOutcome.ERRORED),
            ("<S>_ERROR", CaseOutcome.ERRORED),
            ("<S>_CPU_LIMIT", CaseOutcome.TIMED_OUT),
            ("NO_TEST_RESU", CaseOutcome.NO_CHECKS),
            ("<A>_ALARM", CaseOutcome.REPRODUCED),
            ("SOMETHING_NEW", CaseOutcome.ERRORED),
        ],
    )
    def test_the_diagnostic_decides_the_rest(self, diagnostic: str, outcome: CaseOutcome) -> None:
        text = ASTER_OK.replace("DIAGNOSTIC JOB : OK", f"DIAGNOSTIC JOB : {diagnostic}")

        assert read_aster_message("x", text).outcome is outcome

    def test_no_message_file_is_no_output(self) -> None:
        assert read_aster_message("x", None).outcome is CaseOutcome.NO_OUTPUT

    def test_an_ok_job_with_no_checks_is_not_a_pass(self) -> None:
        text = "--- DIAGNOSTIC JOB : OK\n"

        assert read_aster_message("x", text).outcome is CaseOutcome.NO_CHECKS

    def test_no_reference_kind_is_named_after_validation(self) -> None:
        """An external source might be a measurement and might be another code;
        the harness has not read which, so it has no word that says either."""
        assert ReferenceKind.EXTERNAL is corpora.ASTER_REFERENCES["SOURCE_EXTERNE"]
        for kind in ReferenceKind:
            assert "valid" not in kind.value and "valid" not in kind.name.lower()

    def test_only_sequential_cases_of_the_family_are_selected(self, tmp_path: Path) -> None:
        for name, labels in (
            ("sslv100a", "submit verification sequential"),
            ("sslv100b", "verification parallel"),
            ("ssnv100a", "verification sequential"),
        ):
            (tmp_path / f"{name}.export").write_text(f"P time_limit 60\nP testlist {labels}\n")

        assert corpora.aster_cases(tmp_path, "ssl") == ["sslv100a"]


    def test_a_testcase_assertion_is_counted_and_classified_as_nothing(self) -> None:
        """sslp306a's lines on 18.0.12. Its twenty assertions include a
        deflection against a closed form, and it was reported as holding no
        check until they were read — but an assertion names no reference, so
        counting it proves nothing more."""
        text = (
            " OK        assertTrue passed\n"
            " OK  assertAlmostEqual passed\n"
            "Ran 2 tests, 2 passed, 0 in failure\n\n OK \n\n"
            "--- DIAGNOSTIC JOB : OK\n"
        )

        result = read_aster_message("sslp306a", text)

        assert result.outcome is CaseOutcome.REPRODUCED
        assert result.checks == {"unstated": {"ok": 2, "nook": 0, "skip": 0}}

    def test_a_failed_assertion_is_a_deviation(self) -> None:
        text = "NOOK assertAlmostEqual failed : 1.0 != 1.2 - sslp306a.comm:243\n--- DIAGNOSTIC JOB : OK\n"

        result = read_aster_message("sslp306a", text)

        assert result.outcome is CaseOutcome.DEVIATED
        assert result.checks["unstated"] == {"ok": 0, "nook": 1, "skip": 0}

    def test_a_summary_with_no_assertion_behind_it_is_not_a_check(self) -> None:
        """ssls131a on 18.0.12: the one TEST_TABLE sits behind a branch that
        printed "le maillage n'est pas disponible !", then the summary said OK."""
        text = "Ran 0 tests, 0 passed, 0 in failure\n\n OK \n\n--- DIAGNOSTIC JOB : OK\n"

        assert read_aster_message("ssls131a", text).outcome is CaseOutcome.NO_CHECKS

    def test_a_case_takes_the_seconds_ctest_printed_for_it_passed_or_failed(self) -> None:
        log = (
            "        Start  648: ASTER_18.0.12_sslv154b\n"
            " 81/664 Test #1068: ASTER_18.0.12_sslp102n ...........   Passed   75.76 sec\n"
            " 85/664 Test  #648: ASTER_18.0.12_sslv154b ...........***Failed  104.14 sec\n"
            "541/664 Test #3924: ASTER_18.0.12_ssll501a ...........***Failed    1.65 sec\n"
            "\t648 - ASTER_18.0.12_sslv154b (Failed)                   ASTER_18.0.12 nodes=01 sequential\n"
        )

        assert corpora.ctest_times(log) == {"sslp102n": 75.76, "sslv154b": 104.14, "ssll501a": 1.65}


#: A stand-in `run_ctest` that behaves the way 18.0.12's does in the respects
#: that decide a run: an existing `--resutest` makes it ask on the terminal (and
#: die of EOF with no terminal), it prints ctest's per-test lines, and it writes
#: one `<case>.mess` per selected case plus a JUnit file whose times are the
#: ones 18.0.12 writes for a failed case — not what the case took.
FAKE_RUN_CTEST = r'''
import os, pathlib, re, sys
args = sys.argv[1:]
resutest = pathlib.Path(next(a.split("=", 1)[1] for a in args if a.startswith("--resutest=")))
names = re.fullmatch(r"_\((.*)\)\$", args[args.index("-R") + 1]).group(1).split("|")
pathlib.Path("ran").write_text(" ".join(args))
if resutest.exists():
    input(f"{resutest} will be removed.\ndo you want to continue (y/n) ?")
if os.environ.get("FAKE_RUN_CTEST") == "silent":
    print("EOFError: EOF when reading a line")
    sys.exit(1)
if not os.path.samestat(os.fstat(0), os.stat(os.devnull)):
    print("stdin is not /dev/null, so a prompt here would wait for an answer")
    sys.exit(1)
resutest.mkdir()
message = pathlib.Path(os.environ["FAKE_MESS"]).read_text()
times = []
for index, name in enumerate(names, 1):
    if name != os.environ.get("FAKE_NO_MESS"):
        (resutest / f"{name}.mess").write_text(message)
        print(f"{index}/{len(names)} Test #{index}: ASTER_18.0.12_{name} ........   Passed    4.50 sec")
    else:
        print(f"{index}/{len(names)} Test #{index}: ASTER_18.0.12_{name} ........***Failed    0.25 sec")
    times.append(f'<testcase name="ASTER_18.0.12_{name}" time="66.0"/>')
(resutest / "run_testcases.xml").write_text("<testsuite>" + "".join(times) + "</testsuite>")
'''


class TestRunCtestIsDrivenWithoutATerminal:
    @pytest.fixture
    def aster(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        for name in ("sslv100a", "sslv100b"):
            (tests_dir / f"{name}.export").write_text("P testlist verification sequential\n")
        script = tmp_path / "fake_run_ctest.py"
        script.write_text(FAKE_RUN_CTEST)
        mess = tmp_path / "ok.mess"
        mess.write_text(ASTER_OK)
        monkeypatch.setenv("FAKE_MESS", str(mess))
        return tests_dir, f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"

    def test_each_case_is_read_from_the_message_the_runner_wrote(self, aster: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tests_dir, runner = aster
        monkeypatch.setenv("FAKE_NO_MESS", "sslv100b")

        results = list(corpora.run_code_aster(tests_dir, tmp_path / "work" / "resutest", family="ssl", run_ctest=runner, jobs=3, time_factor=4.0))

        assert [(r.case, r.outcome) for r in results] == [
            ("sslv100a", CaseOutcome.REPRODUCED),
            ("sslv100b", CaseOutcome.NO_OUTPUT),
        ]
        assert [r.seconds for r in results] == [4.5, 0.25]
        ran = (tmp_path / "work" / "ran").read_text()
        assert "-j 3" in ran
        assert "--timefactor 4.0" in ran

    def test_an_existing_results_directory_is_refused_before_the_runner_starts(self, aster: tuple[Path, str], tmp_path: Path) -> None:
        """Measured on 18.0.12: run_ctest asks whether to delete it, reads EOF
        and exits, and every case in the family reads as having no output."""
        tests_dir, runner = aster
        resutest = tmp_path / "work" / "resutest"
        resutest.mkdir(parents=True)
        (resutest / "sslv100a.mess").write_text("somebody's last run")

        with pytest.raises(CorpusError, match="already exists"):
            list(corpora.run_code_aster(tests_dir, resutest, family="ssl", run_ctest=runner))

        assert not (tmp_path / "work" / "ran").exists()
        assert (resutest / "sslv100a.mess").read_text() == "somebody's last run"

    def test_a_runner_that_wrote_nothing_is_refused_with_what_it_said(self, aster: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not a report of 664 cases with no output, which is what it was."""
        tests_dir, runner = aster
        monkeypatch.setenv("FAKE_RUN_CTEST", "silent")

        with pytest.raises(CorpusError, match="EOF when reading a line"):
            list(corpora.run_code_aster(tests_dir, tmp_path / "work" / "resutest", family="ssl", run_ctest=runner))

    def test_the_runner_is_given_no_terminal_to_ask_on(self, aster: tuple[Path, str], tmp_path: Path) -> None:
        """pytest already points fd 0 at /dev/null, so an inherited stdin would
        pass this by accident; a pipe with an answer waiting is put there
        instead, which the runner must not be handed."""
        tests_dir, runner = aster
        read_end, write_end = os.pipe()
        os.write(write_end, b"y\n")
        os.close(write_end)
        saved = os.dup(0)
        try:
            os.dup2(read_end, 0)
            results = list(corpora.run_code_aster(tests_dir, tmp_path / "work" / "resutest", family="ssl", run_ctest=runner))
        finally:
            os.dup2(saved, 0)
            os.close(saved)
            os.close(read_end)

        assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# The corpora are pinned, sourced, and honest about whose numbers they touch
# ---------------------------------------------------------------------------


class TestTheCorporaArePinnedAndSourced:
    def test_every_corpus_quotes_its_maintainer_with_a_date(self) -> None:
        for corpus in CORPORA.values():
            assert re.search(r"Read \d{4}-\d{2}-\d{2}", corpus.maintainer_says), corpus.id

    def test_a_description_from_memory_is_refused(self) -> None:
        with pytest.raises(ValueError, match="paraphrase from memory"):
            replace(CORPORA["calculix-2.20"], maintainer_says="examples that test features")

    def test_an_archive_without_its_hash_is_refused(self) -> None:
        with pytest.raises(ValueError, match="SHA-256"):
            replace(CORPORA["calculix-2.20"], archive_sha256="")

    def test_the_calculix_pin_is_the_build_the_fleet_image_installs(self) -> None:
        """The Dockerfile names no version — bookworm freezes it — so the pin is
        held to the base image's suite, and the nightly job installs the
        pinned version explicitly so a drifted archive fails loudly there."""
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        nightly = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")
        corpus = CORPORA["calculix-2.20"]

        assert "slim-bookworm AS runtime" in dockerfile
        assert "calculix-ccx" in dockerfile
        assert "Debian bookworm" in corpus.build
        assert "calculix-ccx=2.20-1" in nightly
        assert "calculix-ccx 2.20-1" in corpus.build

    def test_the_nightly_job_installs_the_code_aster_build_that_is_pinned(self) -> None:
        """From an explicit lock, every package hashed — a package spec would
        re-solve nightly and let openblas or mumps move under the same name."""
        nightly = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")
        lock_name = "data/verify/corpora/code_aster-18.0.12.explicit.txt"
        lock = (ROOT / lock_name).read_text(encoding="utf-8").splitlines()
        packages = [line for line in lock if line and not line.startswith(("#", "@"))]

        assert f"--file \"$GITHUB_WORKSPACE/{lock_name}\"" in nightly
        assert "@EXPLICIT" in lock
        assert all(re.fullmatch(r"https://conda\.anaconda\.org/conda-forge/\S+#[0-9a-f]{32}", p) for p in packages)
        assert [p for p in packages if "/code-aster-" in p] == [
            p for p in packages if "/code-aster-18.0.12-py312_nompi_h60fb801_0.conda#" in p
        ]
        assert len([p for p in packages if "/code-aster-" in p]) == 1
        assert "py312_nompi_h60fb801_0" in CORPORA["code_aster-18.0.12"].build

    def test_micromamba_is_fetched_by_version_and_checked_by_hash(self) -> None:
        nightly = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")

        assert "micro.mamba.pm/api/micromamba/linux-64/2.9.0 " in nightly
        assert re.search(r'echo "[0-9a-f]{64}  micromamba\.tar\.bz2" \| sha256sum -c -', nightly)
        assert "/linux-64/latest" not in nightly

    @pytest.mark.parametrize("corpus_id", sorted(CORPORA))
    def test_the_nightly_run_of_each_corpus_compares_with_its_recorded_baseline(self, corpus_id: str) -> None:
        nightly = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")
        baseline = f"data/verify/corpora/{corpus_id}.json"

        assert f"python3 -m app.verify.corpora {corpus_id}\n" in nightly
        assert f"--baseline {baseline}\n" in nightly
        assert (ROOT / baseline).is_file()

    @pytest.mark.parametrize("corpus_id", sorted(CORPORA))
    def test_the_nightly_scales_time_limits_as_its_baseline_was_recorded(self, corpus_id: str) -> None:
        """Otherwise the comparison refuses — but only at 03:00, after the run."""
        nightly = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")
        step = nightly[nightly.index(f"python3 -m app.verify.corpora {corpus_id}\n"):]
        step = step[: step.index("\n\n")]
        asked = re.search(r"--time-factor (\S+)", step)
        recorded = json.loads((ROOT / f"data/verify/corpora/{corpus_id}.json").read_text(encoding="utf-8"))

        assert (float(asked.group(1)) if asked else 1.0) == recorded["time_factor"]

    def test_the_package_imports_with_no_site_packages_at_all(self) -> None:
        """`python3 -m app.verify.corpora` imports `app/__init__.py` and
        `app/verify/__init__.py` first, so those are held to the rule too.
        `-S` leaves the interpreter with the standard library and nothing else."""
        completed = subprocess.run(
            [sys.executable, "-S", "-E", "-s", "-c", "import app.verify.corpora"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr

    def test_code_aster_is_still_not_invoked_by_any_kryova_code_path(self) -> None:
        """`answers_kryova_numbers=False` puts "no Kryova result" into the
        published statement. The day a module invokes code_aster that sentence
        is false, and this is where it fails."""
        invokes = re.compile(r"\b(?:run_aster|run_ctest|as_run)\b|import code_aster|from code_aster")
        callers = [
            path.relative_to(ROOT)
            for path in (ROOT / "app").rglob("*.py")
            if path.name != "corpora.py" and invokes.search(path.read_text(encoding="utf-8"))
        ]

        assert callers == []
        assert CORPORA["code_aster-18.0.12"].answers_kryova_numbers is False

    def test_the_module_needs_nothing_outside_the_standard_library(self) -> None:
        """It runs under bookworm's own Python inside the CalculiX container."""
        tree = ast.parse((ROOT / "app/verify/corpora.py").read_text(encoding="utf-8"))
        imported = {
            name.split(".")[0]
            for node in ast.walk(tree)
            for name in (
                [alias.name for alias in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
        }

        assert imported
        assert imported <= set(sys.stdlib_module_names) | {"__future__"}

    def test_a_binary_that_is_not_the_pinned_build_is_refused(self) -> None:
        with pytest.raises(CorpusError, match="Version 2.20"):
            corpora.require_build(CORPORA["calculix-2.20"], "This is Version 2.23")
        corpora.require_build(CORPORA["calculix-2.20"], "\nThis is Version 2.20\n")

    @pytest.mark.parametrize("reported", ["This is Version 2.201", "This is Version 2.20.1"])
    def test_the_pinned_version_is_not_matched_as_a_prefix(self, reported: str) -> None:
        with pytest.raises(CorpusError):
            corpora.require_build(CORPORA["calculix-2.20"], reported)

    def test_code_aster_reports_its_version_from_the_installed_config(self, tmp_path: Path) -> None:
        """Where 18.0.12 from conda-forge keeps it: `share/aster/config.json`
        beside the test base. Its `config.txt` carries none, which is what the
        first draft of this read and would have refused every run on."""
        share = tmp_path / "share" / "aster"
        (share / "tests").mkdir(parents=True)
        (share / "config.txt").write_text("BUILD_TYPE | env | - | waf\n")
        (share / "config.json").write_text(json.dumps({"version_tag": "18.0.12", "version_sha1": "n/a"}))

        reported = corpora.aster_version(share / "tests")

        corpora.require_build(CORPORA["code_aster-18.0.12"], reported)
        (share / "config.json").write_text(json.dumps({"version_tag": "17.4.0"}))
        with pytest.raises(CorpusError):
            corpora.require_build(CORPORA["code_aster-18.0.12"], corpora.aster_version(share / "tests"))
        (share / "config.json").unlink()
        assert corpora.aster_version(share / "tests") == ""

    def test_a_changed_archive_is_refused_before_it_is_unpacked(self, tmp_path: Path) -> None:
        corpus = CORPORA["calculix-2.20"]
        (tmp_path / "ccx_2.20.test.tar.bz2").write_bytes(b"not the archive")

        with pytest.raises(CorpusError, match="not the pinned"):
            corpora.fetch_archive(corpus, tmp_path)

    def test_an_archive_that_would_unpack_outside_its_directory_is_refused(self, tmp_path: Path) -> None:
        evil = tmp_path / "ccx_2.20.test.tar.bz2"
        payload = tmp_path / "payload"
        payload.write_text("x")
        with tarfile.open(evil, "w:bz2") as tar:
            tar.add(payload, arcname="../escaped")
        corpus = replace(CORPORA["calculix-2.20"], archive_sha256=corpora.sha256_of(evil))

        with pytest.raises(CorpusError, match="outside"):
            corpora.fetch_archive(corpus, tmp_path)

        assert not (tmp_path.parent / "escaped").exists()

    @pytest.mark.parametrize("member", ["../escaped", "link"])
    def test_the_same_refusal_holds_on_a_python_with_no_extraction_filter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str) -> None:
        """Bookworm's Python 3.11.2 — the one the CalculiX job runs under — has
        no `filter=`, so the check is spelled out there, and this suite's 3.12
        would never reach that branch unless made to."""
        monkeypatch.delattr(tarfile, "data_filter")
        evil = tmp_path / "ccx_2.20.test.tar.bz2"
        payload = tmp_path / "payload"
        payload.write_text("x")
        with tarfile.open(evil, "w:bz2") as tar:
            if member == "link":
                info = tarfile.TarInfo("link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                tar.add(payload, arcname=member)
        corpus = replace(CORPORA["calculix-2.20"], archive_sha256=corpora.sha256_of(evil))

        with pytest.raises(CorpusError, match="outside"):
            corpora.fetch_archive(corpus, tmp_path)

        assert not (tmp_path.parent / "escaped").exists()
        assert not (tmp_path / "link").exists()


# ---------------------------------------------------------------------------
# The statement, and the baseline
# ---------------------------------------------------------------------------


def _report(corpus_id: str, results: list[CaseResult]) -> CorpusReport:
    return CorpusReport(
        corpus=CORPORA[corpus_id],
        reported_version="This is Version 2.20" if corpus_id.startswith("calculix") else "18.0.12",
        results=tuple(results),
        recorded_at="2026-09-14T00:00:00Z",
        seconds=1.0,
    )


RECORDED = {"recorded-output": {"ok": 2, "nook": 0, "skip": 0}}


class TestTheStatementComesFromTheRun:
    def test_the_counts_in_it_are_the_runs(self) -> None:
        report = _report(
            "calculix-2.20",
            [
                CaseResult("a", CaseOutcome.REPRODUCED, checks=RECORDED),
                CaseResult("b", CaseOutcome.DEVIATED),
                CaseResult("c", CaseOutcome.SKIPPED),
            ],
        )

        said = statement(report)

        assert said["establishes"][0].startswith("1 of 2 cases")

    def test_calculix_says_its_references_are_calculix_output(self) -> None:
        said = statement(_report("calculix-2.20", [CaseResult("a", CaseOutcome.REPRODUCED)]))
        does_not = " ".join(said["does_not_establish"])

        assert "earlier CalculiX output" in does_not
        assert "none of this is code verification" in does_not
        assert "validation" in does_not
        assert not any("valid" in line for line in said["establishes"])

    def test_code_aster_counts_closed_form_agreement_as_code_verification_and_nothing_else(self) -> None:
        checks = {
            "closed-form": {"ok": 7, "nook": 1, "skip": 0},
            "external": {"ok": 3, "nook": 0, "skip": 0},
            "recorded-output": {"ok": 11, "nook": 0, "skip": 2},
        }
        said = statement(_report("code_aster-18.0.12", [CaseResult("s", CaseOutcome.DEVIATED, checks=checks)]))
        establishes = " ".join(said["establishes"])
        does_not = " ".join(said["does_not_establish"])

        assert "7 of 8 checks against a closed-form value" in establishes
        assert "11 non-regression checks" in establishes
        assert "3 SOURCE_EXTERNE checks" in does_not
        assert "0 assertions written in the cases' own Python" in does_not
        assert "No Kryova code path invokes code_aster" in does_not
        assert "time limit" not in does_not

    def test_a_scaled_time_limit_is_stated_beside_the_run(self) -> None:
        report = replace(_report("code_aster-18.0.12", [CaseResult("s", CaseOutcome.REPRODUCED)]), time_factor=4.0)

        does_not = " ".join(statement(report)["does_not_establish"])

        assert "every limit was multiplied by 4" in does_not


class TestABaselineCatchesALostCase:
    def test_a_case_that_stops_reproducing_is_a_regression(self) -> None:
        then = _report("calculix-2.20", [CaseResult("a", CaseOutcome.REPRODUCED), CaseResult("b", CaseOutcome.DEVIATED)])
        now = _report("calculix-2.20", [CaseResult("a", CaseOutcome.DEVIATED), CaseResult("b", CaseOutcome.DEVIATED)])

        assert compare_to_baseline(now, then) == BaselineComparison(lost=("a",), gained=(), missing=())
        assert compare_to_baseline(now, then).regressed

    def test_a_case_that_starts_reproducing_is_reported_and_not_a_failure(self) -> None:
        then = _report("calculix-2.20", [CaseResult("b", CaseOutcome.DEVIATED)])
        now = _report("calculix-2.20", [CaseResult("b", CaseOutcome.REPRODUCED)])

        comparison = compare_to_baseline(now, then)

        assert comparison.gained == ("b",)
        assert not comparison.regressed

    def test_a_case_that_vanished_from_the_run_is_a_regression(self) -> None:
        """Otherwise deleting the failing case is how the job goes green."""
        then = _report("calculix-2.20", [CaseResult("a", CaseOutcome.REPRODUCED)])
        now = _report("calculix-2.20", [])

        assert compare_to_baseline(now, then).regressed

    def test_a_partial_run_is_not_compared_with_a_whole_baseline(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Refused before anything runs: the cases left out would all read as
        vanished, and a red job that is nothing but the flags is a red job
        people learn to ignore."""
        with pytest.raises(SystemExit) as stopped:
            corpora._main([
                "calculix-2.20", "--work", str(tmp_path / "work"), "--only", "beamp",
                "--baseline", str(corpora.BASELINE_DIR / "calculix-2.20.json"),
            ])

        assert stopped.value.code == 2
        assert "missing" in capsys.readouterr().err
        assert not (tmp_path / "work").exists()

    def test_two_corpora_are_not_compared(self) -> None:
        with pytest.raises(CorpusError):
            compare_to_baseline(_report("calculix-2.20", []), _report("code_aster-18.0.12", []))

    def test_runs_under_different_time_limits_are_not_compared(self) -> None:
        """sslv154b stopped at 104 s against its 110 s limit on 18.0.12: under a
        factor of 1 it times out, under 4 it need not, and neither is news."""
        then = replace(_report("code_aster-18.0.12", [CaseResult("sslv154b", CaseOutcome.REPRODUCED)]), time_factor=4.0)
        now = _report("code_aster-18.0.12", [CaseResult("sslv154b", CaseOutcome.TIMED_OUT)])

        with pytest.raises(CorpusError, match="neither"):
            compare_to_baseline(now, then)

    def test_a_baseline_at_another_time_factor_is_refused_before_anything_runs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(replace(_report("code_aster-18.0.12", []), time_factor=4.0).to_dict()))

        with pytest.raises(SystemExit) as stopped:
            corpora._main([
                "code_aster-18.0.12", "--work", str(tmp_path / "work"), "--aster-tests", str(tmp_path / "none"),
                "--baseline", str(baseline),
            ])

        assert stopped.value.code == 2
        assert "--time-factor 4" in capsys.readouterr().err
        assert not (tmp_path / "work").exists()

    def test_calculix_has_no_time_limit_to_scale(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as stopped:
            corpora._main(["calculix-2.20", "--work", str(tmp_path / "work"), "--time-factor", "4"])

        assert stopped.value.code == 2
        assert "--timeout" in capsys.readouterr().err
        assert not (tmp_path / "work").exists()

    def test_a_report_round_trips(self) -> None:
        report = replace(
            _report("calculix-2.20", [CaseResult("a", CaseOutcome.DEVIATED, 1.5, RECORDED, "d", "deck")]),
            time_factor=2.5,
        )

        again = CorpusReport.from_dict(json.loads(json.dumps(report.to_dict())))

        assert again == report


# ---------------------------------------------------------------------------
# The recorded runs
# ---------------------------------------------------------------------------


def _baseline(corpus_id: str) -> tuple[dict, CorpusReport]:
    path = corpora.BASELINE_DIR / f"{corpus_id}.json"
    assert path.exists(), f"No recorded run for {corpus_id}; see the module docstring for the command."
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, CorpusReport.from_dict(payload)


class TestTheRecordedCalculixRun:
    def test_it_was_recorded_on_the_pinned_build(self) -> None:
        payload, report = _baseline("calculix-2.20")

        corpora.require_build(report.corpus, report.reported_version)
        assert payload["build"] == CORPORA["calculix-2.20"].build

    def test_its_statement_is_what_the_module_derives_today(self) -> None:
        """A statement edited in code and not re-derived would publish two."""
        payload, report = _baseline("calculix-2.20")

        assert payload["statement"] == statement(report)
        assert payload["outcomes"] == report.outcome_counts()

    def test_it_agrees_with_the_maintainers_own_compare_on_the_same_build(self) -> None:
        """Measured 2026-09-14: the archive's `compare`, unmodified but for the
        binary path, run on calculix-ccx 2.20-1 in `debian:bookworm-slim`, wrote
        exactly these twelve cases to its error file — five restart decks whose
        headers ask for a `.rout` to be copied that `compare` never copies, one
        generated deck with no reference, and six numeric deviations. The
        harness must name the same twelve, or it is running a different
        corpus."""
        maintainer_flagged = {
            "beam10psmooth.rfn", "beamhtfc2", "beamread", "beamread2", "beamread3",
            "beamread4", "beamprand", "beamptied5", "beamptied6", "induction2",
            "segment", "segmenttemp",
        }
        _, report = _baseline("calculix-2.20")

        not_reproduced = {r.case for r in report.results if r.outcome not in (CaseOutcome.REPRODUCED, CaseOutcome.SKIPPED)}

        assert not_reproduced == maintainer_flagged

    def test_the_failures_stay_in_the_file(self) -> None:
        _, report = _baseline("calculix-2.20")

        assert all(r.detail for r in report.results if not r.passed and r.outcome is not CaseOutcome.SKIPPED)


class TestTheRecordedCodeAsterRun:
    def test_it_was_recorded_on_the_pinned_build(self) -> None:
        payload, report = _baseline("code_aster-18.0.12")

        corpora.require_build(report.corpus, report.reported_version)
        assert payload["build"] == CORPORA["code_aster-18.0.12"].build
        assert report.corpus.solver is Solver.CODE_ASTER

    def test_its_statement_is_what_the_module_derives_today(self) -> None:
        payload, report = _baseline("code_aster-18.0.12")

        assert payload["statement"] == statement(report)
        assert payload["checks"] == report.check_counts()

    def test_every_case_is_the_linear_statics_family(self) -> None:
        _, report = _baseline("code_aster-18.0.12")

        assert report.results
        assert all(r.case.startswith("ssl") for r in report.results)
