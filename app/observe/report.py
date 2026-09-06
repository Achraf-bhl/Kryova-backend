"""What was measured, and — the part that earns the package — what was not.

A report that silently omits the spans it failed to record is the same failure
class as a clash check that skipped pairs: everything left in it looks fine, and
the thing you needed is simply absent. `app.design.assertions` already has the
vocabulary for this and it is reused here rather than reinvented — `Outcome`,
with `UNMEASURED` meaning *this was not measured*, which is never a pass.

The mapping onto a coverage row, which is the only part that needed a decision:

- `PASSED` — the site ran and every span of it was kept. The numbers stand.
- `UNMEASURED` — anything else, with the reason spelled out. Four reasons:
  nothing of that name ran inside the window; a span of it was still open when
  the report was taken; the recorder's buffer filled and discarded some; or no
  hook is installed at that site at all (`catalogue.Site.wired` is False).

Two of those come with partial data, and the partial data is still published on
the `Summary` — a floor is useful, and hiding it would be its own dishonesty.
What is refused is presenting the floor as the count. `Report.complete` is true
only when nothing is unmeasured, and `as_text()` always prints the unmeasured
section, including when it is empty, because a section that disappears when it
has nothing to say trains the reader not to look for it.

`FAILED` deliberately never appears. A coverage row is a claim about the
*instrument*, not about the part or the run — a span that raised is a recorded
measurement with `ok=False`, and it is counted in its summary's `failures`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.design.assertions import Outcome
from app.observe import catalogue
from app.observe.collect import Recorder
from app.observe.queue import QueueSnapshot
from app.observe.records import Note, Span, Summary

#: Header of the section that must always be printed.
UNMEASURED_HEADING: Final = "Not measured"


@dataclass(frozen=True, slots=True)
class Coverage:
    """Whether one declared site produced numbers worth standing behind."""

    site: str
    outcome: Outcome
    detail: str

    @property
    def measured(self) -> bool:
        return self.outcome is Outcome.PASSED


@dataclass(frozen=True, slots=True)
class Report:
    """A collection, rolled up and made honest about its own gaps."""

    label: str
    window_seconds: float
    span_count: int
    summaries: tuple[Summary, ...]
    coverage: tuple[Coverage, ...]
    notes: tuple[Note, ...]
    #: Span names that were recorded but are in no catalogue entry. A test
    #: refuses these in `app/`, so one here means a caller outside the package
    #: invented a name — recorded, reported, and not silently absorbed.
    undeclared: tuple[str, ...]
    queue: QueueSnapshot | None

    @property
    def unmeasured(self) -> tuple[Coverage, ...]:
        return tuple(row for row in self.coverage if row.outcome is Outcome.UNMEASURED)

    @property
    def complete(self) -> bool:
        """True only when every declared site produced numbers that stand."""
        return not self.unmeasured

    def summary_for(self, name: str) -> Summary | None:
        for summary in self.summaries:
            if summary.name == name:
                return summary
        return None

    def as_text(self) -> str:
        lines: list[str] = []
        title = f"Observation window: {self.window_seconds:.3f} s, {self.span_count} spans"
        if self.label:
            title = f"{self.label} — {title}"
        lines.append(title)

        if self.summaries:
            lines.append("")
            lines.append("What ran")
            for summary in self.summaries:
                d = summary.durations
                flag = " (incomplete)" if summary.incomplete else ""
                lines.append(
                    f"  {summary.name}: {d.count}x, total {d.total_seconds:.3f} s, "
                    f"median {d.median_seconds:.4f} s, max {d.max_seconds:.4f} s, "
                    f"{summary.failures} failed{flag}"
                )
                if summary.totals:
                    totals = ", ".join(f"{k}={v:g}" for k, v in summary.totals.items())
                    lines.append(f"      {totals}")

        if self.queue is not None:
            lines.append("")
            lines.extend(_queue_lines(self.queue))

        for note in self.notes:
            lines.append(f"  note: {note.text}")

        lines.append("")
        lines.append(UNMEASURED_HEADING)
        if not self.unmeasured:
            lines.append("  nothing — every declared site reported, and none were discarded.")
        for row in self.unmeasured:
            lines.append(f"  {row.site}: {row.detail}")
        if self.undeclared:
            lines.append(
                "  undeclared span names were recorded and are not in the catalogue: "
                + ", ".join(self.undeclared)
            )
        return "\n".join(lines)


def build_report(
    recorder: Recorder,
    queue: QueueSnapshot | None = None,
    sites: Sequence[catalogue.Site] | None = None,
) -> Report:
    """Roll a recorder up against the declared catalogue.

    `queue` is passed in rather than read from the module-level meter so a
    report can be built over a meter a test owns, and so building a report never
    reaches for process-wide state on its own.
    """
    declared = tuple(catalogue.SITES if sites is None else sites)
    spans = recorder.spans
    dropped = dict(recorder.dropped)
    unfinished = recorder.unfinished

    grouped: dict[str, list[Span]] = {}
    for span in spans:
        grouped.setdefault(span.name, []).append(span)

    summaries = tuple(
        sorted(
            (
                Summary.of(name, group, dropped=dropped.get(name, 0))
                for name, group in grouped.items()
            ),
            key=lambda s: s.durations.total_seconds,
            reverse=True,
        )
    )

    known = {site.name for site in declared}
    coverage = tuple(
        _coverage_for(site, grouped, dropped, unfinished, recorder) for site in declared
    )
    undeclared = tuple(sorted(name for name in grouped if name not in known))

    return Report(
        label=recorder.label,
        window_seconds=recorder.seconds,
        span_count=len(spans),
        summaries=summaries,
        coverage=coverage,
        notes=recorder.notes,
        undeclared=undeclared,
        queue=queue,
    )


def _coverage_for(
    site: catalogue.Site,
    grouped: Mapping[str, list[Span]],
    dropped: Mapping[str, int],
    unfinished: Sequence[str],
    recorder: Recorder,
) -> Coverage:
    if not site.wired:
        return Coverage(
            site=site.name,
            outcome=Outcome.UNMEASURED,
            detail=f"no hook is installed at this site. {site.not_wired_because}",
        )

    kept = len(grouped.get(site.name, ()))
    discarded = dropped.get(site.name, 0)
    still_open = sum(1 for name in unfinished if name == site.name)

    if discarded:
        return Coverage(
            site=site.name,
            outcome=Outcome.UNMEASURED,
            detail=(
                f"{kept} kept and {discarded} discarded when the recorder filled at "
                f"{recorder.max_spans} spans, so the totals are a floor and not a count. "
                "Raise max_spans and collect again."
            ),
        )
    if still_open:
        return Coverage(
            site=site.name,
            outcome=Outcome.UNMEASURED,
            detail=(
                f"{still_open} still running when the report was taken, so the "
                f"{kept} recorded here are not the whole window."
            ),
        )
    if kept == 0:
        return Coverage(
            site=site.name,
            outcome=Outcome.UNMEASURED,
            detail=(
                "the hook is installed but nothing of this name ran inside the "
                f"window, so there is no reading for it ({site.what})."
            ),
        )
    return Coverage(
        site=site.name,
        outcome=Outcome.PASSED,
        detail=f"{kept} recorded, none discarded",
    )


def _queue_lines(queue: QueueSnapshot) -> list[str]:
    lines = ["Queue"]
    lines.append(
        f"  submitted {queue.submitted}, started {queue.started}, "
        f"finished {queue.finished} ({queue.failed} failed), "
        f"pending {queue.pending}, running {queue.running}"
    )
    if queue.oldest_pending_seconds is not None:
        lines.append(f"  oldest job still queued: {queue.oldest_pending_seconds:.3f} s")
    if queue.longest_running_seconds is not None:
        lines.append(f"  longest job still running: {queue.longest_running_seconds:.3f} s")
    for label, dist in (("wait", queue.wait), ("run", queue.run)):
        if dist is None:
            lines.append(f"  {label}: no samples — nothing has completed that phase yet")
            continue
        tail = " (p95 is the maximum at this sample size)" if dist.p95_is_the_maximum else ""
        lines.append(
            f"  {label}: {dist.count} samples, median {dist.median_seconds:.4f} s, "
            f"p95 {dist.p95_seconds:.4f} s, max {dist.max_seconds:.4f} s{tail}"
        )
    if queue.windowed:
        lines.append("  wait and run cover the recent window only, not every job submitted")
    return lines
