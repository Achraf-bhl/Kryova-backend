"""The report, and the rule that makes it worth reading: it names its own gaps.

A report that silently omits the spans it failed to record is the same failure
class as a clash check that skipped pairs (`tests/test_dynamics_clearance.py`) —
everything left in it looks fine, and the thing you needed is simply absent. So
the tests here are mostly about absence: a site that never ran, a site with no
hook at all, a recorder that filled and threw records away, a span still running
when somebody asked. Each of those has to come back as `UNMEASURED` and has to
appear in the text an operator reads.

The catalogue contract at the bottom is what keeps that possible. A report can
only name a hole in an instrument it knows the shape of, so every span name in
`app/` must be declared, and every site claiming to be wired must have a call
site. Both directions are checked against the real source tree.

Offline: no database, no network, no kernel.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.design.assertions import Outcome
from app.observe import catalogue, collect, span
from app.observe.catalogue import Site
from app.observe.queue import QueueMeter
from app.observe.report import UNMEASURED_HEADING, build_report

APP = Path(__file__).resolve().parents[1] / "app"

#: A call to `span("literal")` anywhere, however the module imported it. The
#: lookbehind keeps `LiveSpan(` and any other identifier ending in "span" out.
SPAN_CALL = re.compile(r"(?<![A-Za-z0-9_])span\(\s*[\"']([\w.]+)[\"']")

WIRED = Site(name="probe.wired", module="tests", what="a site with a hook")
UNWIRED = Site(
    name="probe.unwired",
    module="tests",
    what="a site with no hook",
    wired=False,
    not_wired_because="nothing calls it; wiring it is one `with span(...)`.",
)


class TestCoverage:
    def test_a_site_that_ran_and_kept_everything_passes(self) -> None:
        with collect() as recorder:
            with span("probe.wired"):
                pass
        report = build_report(recorder, sites=[WIRED])
        (row,) = report.coverage
        assert row.outcome is Outcome.PASSED
        assert row.measured
        assert report.complete

    def test_a_site_that_never_ran_is_unmeasured_not_omitted(self) -> None:
        with collect() as recorder:
            pass
        report = build_report(recorder, sites=[WIRED])
        (row,) = report.coverage
        assert row.outcome is Outcome.UNMEASURED
        assert "nothing of this name ran" in row.detail
        assert not report.complete

    def test_a_site_with_no_hook_says_so_and_says_why(self) -> None:
        """The difference between 'it did not run' and 'nobody wired it'.

        Without the declaration these are the same silence, and the second one
        is a hole in the instrument rather than an answer about the system.
        """
        with collect() as recorder:
            pass
        report = build_report(recorder, sites=[UNWIRED])
        (row,) = report.coverage
        assert row.outcome is Outcome.UNMEASURED
        assert "no hook is installed" in row.detail
        assert "one `with span(...)`" in row.detail

    def test_a_site_declared_unwired_with_no_reason_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="no reason given"):
            Site(name="probe.x", module="tests", what="x", wired=False)

    def test_discarded_spans_make_the_numbers_a_floor_not_a_count(self) -> None:
        with collect(max_spans=2) as recorder:
            for _ in range(5):
                with span("probe.wired"):
                    pass

        report = build_report(recorder, sites=[WIRED])
        (row,) = report.coverage
        assert row.outcome is Outcome.UNMEASURED
        assert "2 kept and 3 discarded" in row.detail
        assert "floor and not a count" in row.detail

        # The partial data is still published -- a floor is useful. What is
        # refused is presenting the floor as the count.
        summary = report.summary_for("probe.wired")
        assert summary is not None
        assert summary.count == 2 and summary.incomplete and summary.dropped == 3

    def test_a_span_still_running_is_not_a_span_that_finished(self) -> None:
        with collect() as recorder:
            with span("probe.wired"):
                pass
            with span("probe.wired"):
                report = build_report(recorder, sites=[WIRED])

        (row,) = report.coverage
        assert row.outcome is Outcome.UNMEASURED
        assert "1 still running" in row.detail

    def test_a_recorded_name_in_no_catalogue_is_surfaced(self) -> None:
        with collect() as recorder:
            with span("probe.invented"):
                pass
        report = build_report(recorder, sites=[WIRED])
        assert report.undeclared == ("probe.invented",)


class TestSummaries:
    def test_summaries_are_ordered_by_where_the_time_went(self) -> None:
        with collect() as recorder:
            with span("probe.slow"):
                _burn(0.02)
            with span("probe.fast"):
                pass
            with span("probe.fast"):
                pass
        report = build_report(recorder)
        assert [s.name for s in report.summaries][0] == "probe.slow"

    def test_a_failed_span_is_counted_in_its_summary_not_in_coverage(self) -> None:
        """Coverage is a claim about the instrument, never about the run.

        A span that raised is a *successful measurement of a failure*, so it
        passes coverage and increments `failures`. Conflating the two would make
        a report of a bad run look like a report nobody took.
        """
        with collect() as recorder:
            with pytest.raises(ValueError):
                with span("probe.wired"):
                    raise ValueError("boom")

        report = build_report(recorder, sites=[WIRED])
        assert report.coverage[0].outcome is Outcome.PASSED
        summary = report.summary_for("probe.wired")
        assert summary is not None and summary.failures == 1


class TestTheTextAnOperatorReads:
    def test_the_unmeasured_section_is_printed_even_when_empty(self) -> None:
        """A section that disappears when it has nothing to say trains the
        reader not to look for it."""
        with collect() as recorder:
            with span("probe.wired"):
                pass
        text = build_report(recorder, sites=[WIRED]).as_text()
        assert UNMEASURED_HEADING in text
        assert "nothing — every declared site reported" in text

    def test_every_unmeasured_site_is_named_in_the_text(self) -> None:
        with collect() as recorder:
            pass
        text = build_report(recorder, sites=[WIRED, UNWIRED]).as_text()
        assert "probe.wired" in text
        assert "probe.unwired" in text
        assert "no hook is installed" in text

    def test_an_undeclared_name_is_named_in_the_text(self) -> None:
        with collect() as recorder:
            with span("probe.invented"):
                pass
        text = build_report(recorder, sites=[WIRED]).as_text()
        assert "probe.invented" in text

    def test_the_queue_block_says_when_it_has_no_samples(self) -> None:
        meter = QueueMeter()
        with collect() as recorder:
            pass
        text = build_report(recorder, queue=meter.snapshot(), sites=[WIRED]).as_text()
        assert "wait: no samples" in text
        assert "run: no samples" in text

    def test_notes_travel_on_the_report(self) -> None:
        with collect() as recorder:
            recorder.note("CalculiX is not installed on this machine", binary="ccx")
        report = build_report(recorder, sites=[WIRED])
        assert report.notes[0].text.startswith("CalculiX is not installed")
        assert "CalculiX is not installed" in report.as_text()


class TestTheCatalogueContract:
    """The catalogue can only name a hole in an instrument it knows.

    Both directions matter. An undeclared span means the report would omit it
    from coverage entirely -- silence where there should be a name. A site
    claiming `wired=True` with no call site means the report says "it did not
    run" about a hook that does not exist, which is the exact confusion the
    `wired` flag was added to remove.
    """

    def test_every_span_name_in_the_app_is_declared(self) -> None:
        # `app/observe/` itself is skipped: its docstrings show `span("...")`
        # as an example, and a regex cannot tell prose from a call. Nothing in
        # the package emits a span of its own -- the two it produces
        # (`jobs.wait`, `jobs.run`) go through named constants and are covered
        # by the reverse check below.
        found: dict[str, str] = {}
        for path in APP.rglob("*.py"):
            if path.parent.name == "observe":
                continue
            for name in SPAN_CALL.findall(path.read_text(encoding="utf-8")):
                found.setdefault(name, str(path.relative_to(APP.parent)))

        undeclared = {n: where for n, where in found.items() if n not in catalogue.BY_NAME}
        assert not undeclared, (
            f"span names not declared in app/observe/catalogue.py: {undeclared}. "
            "Declare the site, or the report cannot report on it."
        )
        assert found, "the regex found no span calls at all -- it has stopped matching"

    def test_every_wired_site_has_a_call_site_in_the_app(self) -> None:
        sources = {
            path: path.read_text(encoding="utf-8")
            for path in APP.rglob("*.py")
            if path.name != "catalogue.py"
        }
        missing = [
            site.name
            for site in catalogue.SITES
            if site.wired
            and not any(f'"{site.name}"' in text or f"'{site.name}'" in text
                        for text in sources.values())
        ]
        assert not missing, (
            f"declared wired but nothing emits them: {missing}. Either wire the "
            "hook or set wired=False with a reason."
        )

    def test_every_unwired_site_names_the_module_it_belongs_in(self) -> None:
        for site in catalogue.unwired():
            assert site.not_wired_because
            assert site.module.startswith("app.")

    def test_the_wired_sites_are_the_ones_this_change_installed(self) -> None:
        assert catalogue.wired_names() == {
            "mesh.gmsh.wait",
            "mesh.gmsh.session",
            "media.write",
            "media.read",
            "media.verify",
            "jobs.wait",
            "jobs.run",
        }

    def test_the_unmeasured_vocabulary_is_the_design_packages_one(self) -> None:
        """One vocabulary for 'not measured', not three."""
        from app.observe import report as report_module

        assert report_module.Outcome is Outcome


def _burn(seconds: float) -> None:
    import time

    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass
