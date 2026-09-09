"""Measure how much of the master plan is finished, by reading the master plan.

The figure is *derived*, never typed. This file exists because a hand-written
count is the same defect the verification register was burned by twice: the
blocked-case count was written into a test as a literal, and unblocking a case
then looked like a regression. A percentage typed into a document has that
problem permanently — it is wrong the moment the next status line changes, and
nothing says so.

So the plan carries a generated block between two markers, and this script is
the only thing that writes it:

    venv/bin/python -m scripts.plan_progress            # print the table
    venv/bin/python -m scripts.plan_progress --write    # regenerate the block
    venv/bin/python -m scripts.plan_progress --check    # non-zero if it is stale

Two sources, both inside `KRYOVA_MASTER_PLAN.md`, so there is nothing to keep in
step with anything else:

1. **Task counts** come from the status line under each task — the same five
   forms the plan's own maintenance rules define.
2. **Effort weights** come from Part 4, parsed out of the prose rather than
   copied here. If Part 4 is reworded so a phase loses its effort figure, the
   parse *fails loudly* rather than quietly weighting that phase at zero.

What the percentage means, and does not
---------------------------------------
`DONE` counts 1, `PARTIAL` counts a half, `IN PROGRESS` a quarter, `BLOCKED` and
`NOT STARTED` count nothing. The half is a convention, not a measurement — a
`PARTIAL` task is not literally half-built — so the number is a *shape*, and the
per-phase rows below it are the part worth reading.

It is also progress against the *plan*, which is not the same as progress toward
a working product: nearly every `DONE` here is proven by the offline suite, and
the plan's stop gates are what convert that into an end-to-end claim. The
generated block says so, because a bare percentage on its own reads as a promise.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PLAN = Path(__file__).resolve().parent.parent / "KRYOVA_MASTER_PLAN.md"

BEGIN = "<!-- progress:begin -->"
END = "<!-- progress:end -->"

#: The five status forms the plan's maintenance rules allow, and what each is
#: worth. The half for `PARTIAL` is a convention and the docstring says so.
WEIGHT: dict[str, float] = {
    "DONE": 1.0,
    "PARTIAL": 0.5,
    "IN PROGRESS": 0.25,
    "BLOCKED": 0.0,
    "NOT STARTED": 0.0,
}

_PHASE_RE = re.compile(r"^##### Phase ([EP][0-9.]+) — (.*?) #####")
_STATUS_RE = re.compile(rf"^ *> \*{{0,2}}({'|'.join(WEIGHT)})")
_COMPLETE_RE = re.compile(r"^ *> .*PHASE COMPLETE")
_TASK_RE = re.compile(r"^(\d+)\. ")
_QUOTED_RE = re.compile(r"^ *> ?")

# Part 4, engineering track: "1. Era I, geometry engine (E1–E2) — 14. Hard, ..."
# and the single-phase form "6. Era VI, agent (E16) — 10. ..."  The dash between
# the phases is an en-dash in the document; accept either.
_ERA_RE = re.compile(r"\((E[0-9.]+)(?:[–-](E[0-9.]+))?\)\s*—\s*(\d+)")
# Part 4, product track: "1. P1 identity and sessions — 3. Standard, ..."
_PRODUCT_RE = re.compile(r"^\d+\.\s+(P\d+)\b.*?—\s*(\d+)")


@dataclass
class Phase:
    key: str
    title: str
    counts: Counter[str] = field(default_factory=Counter)
    complete: bool = False
    months: float = 0.0
    #: The prose of the `✅ PHASE COMPLETE` line, including its continuation.
    marker: str = ""
    #: Task numbers whose status is anything but `DONE`, in document order.
    open_tasks: list[int] = field(default_factory=list)

    @property
    def tasks(self) -> int:
        return sum(self.counts.values())

    def unnamed_residuals(self) -> list[int]:
        """Open tasks a phase-complete marker does not mention.

        A phase may carry the marker with a task still open — E1 closed with its
        operation mapping deliberately at 108/201 and the plan says why — but
        then the marker has to *say which*, or "complete" and "PARTIAL" sit ten
        lines apart contradicting each other and a reader cannot tell which one
        is stale. Naming the task turns the contradiction into a scope note.
        """
        if not self.complete:
            return []
        return [n for n in self.open_tasks if not re.search(rf"\btask {n}\b", self.marker)]

    @property
    def fraction(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(WEIGHT[k] * n for k, n in self.counts.items()) / self.tasks

    @property
    def untouched(self) -> bool:
        """No task in this phase has ever been finished."""
        return self.counts["DONE"] == 0


def read_phases(text: str) -> list[Phase]:
    """Every phase in document order, with its task statuses attached."""
    phases: list[Phase] = []
    current: Phase | None = None
    task: int | None = None
    in_marker = False
    for line in text.splitlines():
        heading = _PHASE_RE.match(line)
        if heading:
            current = Phase(key=heading.group(1), title=heading.group(2))
            phases.append(current)
            task, in_marker = None, False
            continue
        if current is None:
            continue
        if _COMPLETE_RE.match(line):
            current.complete = True
            current.marker = line
            in_marker = True
            continue
        if in_marker:
            # The marker runs on across quoted continuation lines; a blank one
            # or anything unquoted ends it.
            if line.startswith(">"):
                current.marker += " " + _QUOTED_RE.sub("", line)
                continue
            in_marker = False
        numbered = _TASK_RE.match(line)
        if numbered:
            task = int(numbered.group(1))
        status = _STATUS_RE.match(line)
        if status:
            current.counts[status.group(1)] += 1
            if status.group(1) != "DONE" and task is not None:
                current.open_tasks.append(task)
    if not phases:
        raise SystemExit("no phases found — has the plan's heading format changed?")
    return phases


def attach_effort(phases: list[Phase], text: str) -> None:
    """Read Part 4's engineer-month figures onto the phases they cover.

    An era's months are split evenly across the phases in it. That is crude —
    E1 is emphatically not half of Era I — but the alternative is a per-phase
    figure this plan does not state, and inventing one would be exactly the kind
    of number Decision 3 exists to refuse.
    """
    order = [p.key for p in phases]
    by_key = {p.key: p for p in phases}

    section = text.split("## Part 4 — Effort, honestly", 1)
    if len(section) != 2:
        raise SystemExit("Part 4 not found — effort cannot be weighted")
    body = section[1].split("## Part 5", 1)[0]

    for line in body.splitlines():
        era = _ERA_RE.search(line)
        if era and line.lstrip().startswith(tuple("123456789")):
            first, last, months = era.group(1), era.group(2), int(era.group(3))
            lo = order.index(first)
            hi = order.index(last) if last else lo
            covered = order[lo : hi + 1]
            for key in covered:
                by_key[key].months = months / len(covered)
            continue
        product = _PRODUCT_RE.match(line.strip())
        if product:
            by_key[product.group(1)].months = float(product.group(2))

    missing = [p.key for p in phases if p.months == 0.0]
    if missing:
        raise SystemExit(f"Part 4 gives no effort for: {', '.join(missing)}")


def _roll_up(phases: list[Phase]) -> tuple[int, float, float, float, int]:
    tasks = sum(p.tasks for p in phases)
    done = sum(WEIGHT[k] * n for p in phases for k, n in p.counts.items())
    months = sum(p.months for p in phases)
    earned = sum(p.months * p.fraction for p in phases)
    return tasks, done, months, earned, sum(1 for p in phases if p.complete)


def render(phases: list[Phase], date: str) -> str:
    """The generated block, as it appears in the plan."""
    tracks = [
        ("Engineering — E1–E18", [p for p in phases if p.key.startswith("E")]),
        ("Product — P1–P10", [p for p in phases if p.key.startswith("P")]),
        ("**Programme**", phases),
    ]
    out = [
        BEGIN,
        f"**Measured {date}** by `venv/bin/python -m scripts.plan_progress`, which reads the status",
        "line under every task in this file and the engineer-month figures in Part 4. Do not edit the",
        "block by hand — regenerate it with `--write`, and `--check` says whether it has gone stale.",
        "",
        "| Track | Phases complete | Tasks | Effort |",
        "|---|---|---|---|",
    ]
    for label, group in tracks:
        tasks, done, months, earned, complete = _roll_up(group)
        out.append(
            f"| {label} | {complete}/{len(group)} | {done:.0f}/{tasks} = "
            f"{100 * done / tasks:.0f}% | {earned:.0f}/{months:.0f} eng-months = "
            f"{100 * earned / months:.0f}% |"
        )
    out += [
        "",
        "Weighting: `DONE` 1, `PARTIAL` ½, `IN PROGRESS` ¼, `BLOCKED` and `NOT STARTED` 0. The half is",
        "a convention rather than a measurement, so read the per-phase rows, not the headline.",
        "",
        "| | Phases |",
        "|---|---|",
    ]

    finished = [p for p in phases if p.complete]
    untouched = [p for p in phases if not p.complete and p.untouched]
    flight = [p for p in phases if not p.complete and not p.untouched]
    out.append(f"| ✅ complete | {', '.join(p.key for p in finished) or '—'} |")
    out.append(
        "| in flight | "
        + ", ".join(f"{p.key} {100 * p.fraction:.0f}%" for p in flight)
        + " |"
    )
    out.append(
        "| nothing finished yet | " + ", ".join(p.key for p in untouched) + " |"
    )
    out += [
        "",
        "**What this is not.** It is progress against the plan, not against a shipped product. Almost",
        "every `DONE` above is proven by the offline suite on Linux; the stop gates in Part 2 are what",
        "turn that into an end-to-end claim, and none of them has run yet. Two figures inside the plan",
        "are also deliberately not progress: E1.3's operation count is scaffolding depth (it says so),",
        "and roughly 36 of the remaining engineer-months are the work Part 4 marks as needing a real",
        "mechanical engineer, which does not compress.",
        END,
    ]
    return "\n".join(out)


def table(phases: list[Phase]) -> str:
    """The human form, for reading in a terminal."""
    rows = [
        f"{'':1}{'phase':7}{'tasks':>6}{'DONE':>6}{'PART':>6}{'PROG':>6}"
        f"{'BLKD':>6}{'NOT':>6}{'%':>6}{'e-mo':>7}  title"
    ]
    for p in phases:
        c = p.counts
        rows.append(
            f"{'*' if p.complete else ' '}{p.key:7}{p.tasks:6}{c['DONE']:6}"
            f"{c['PARTIAL']:6}{c['IN PROGRESS']:6}{c['BLOCKED']:6}"
            f"{c['NOT STARTED']:6}{100 * p.fraction:6.0f}{p.months:7.1f}  {p.title[:48]}"
        )
    tasks, done, months, earned, complete = _roll_up(phases)
    rows += [
        "",
        f"{complete}/{len(phases)} phases complete · "
        f"{done:.1f}/{tasks} tasks = {100 * done / tasks:.1f}% · "
        f"{earned:.1f}/{months:.0f} eng-months = {100 * earned / months:.1f}%",
    ]
    return "\n".join(rows)


def _split(text: str) -> tuple[str, str, str]:
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{PLAN.name} has no {BEGIN} … {END} block to fill")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return head, text[len(head) : len(text) - len(tail)], tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="regenerate the block in the plan")
    parser.add_argument("--check", action="store_true", help="exit non-zero if the block is stale")
    parser.add_argument("--date", default=_dt.date.today().isoformat())
    args = parser.parse_args(argv)

    text = PLAN.read_text()
    phases = read_phases(text)
    attach_effort(phases, text)
    fresh = render(phases, args.date)

    if args.write:
        head, _, tail = _split(text)
        PLAN.write_text(head + fresh + tail)
        print(f"{PLAN.name}: progress block regenerated ({args.date})")
        return 0

    if args.check:
        _, recorded, _ = _split(text)
        # The date line moves on every run and says nothing about staleness.
        strip = lambda s: [  # noqa: E731
            ln for ln in s.strip().splitlines() if not ln.startswith("**Measured ")
        ]
        if strip(recorded) == strip(fresh):
            print(f"{PLAN.name}: progress block is current")
            return 0
        print(f"{PLAN.name}: progress block is STALE — rerun with --write", file=sys.stderr)
        return 1

    print(table(phases))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
