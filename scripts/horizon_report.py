"""Measure this deployment's own turns per duration bucket (master plan E22.3).

THE QUEUE D6. `app/verify/horizon.py` is the harness; this is the one script that
points it at the machine's real conversation history and writes the artefact.

**Why the labels are in here and not in the harness.** `measure(outcomes=...)` is
how a *success* rate comes to exist, and a success label is a judgement about
whether the agent did what was asked — it cannot be computed from the transcript,
which is the whole reason the harness refuses to infer one. The judgements below
come from `docs/GUI_PROMPT_LADDER.md`'s run log, which is the record of what a
person watched happen. They are data about past runs, so they live beside the
script that reads them rather than in `app/`.

**How a run-log entry is tied to a conversation, and where it is refused.** The
run log quotes each prompt verbatim and a conversation's title is its first user
message, so the match is an exact string prefix rather than a resemblance. Three
rules keep that from becoming a guess:

1. **Only the first turn of a conversation is labelled.** A rung's outcome is
   about the prompt the run log quotes. What the engineer asked next is not in
   the log, and carrying the rung's verdict forward onto follow-up turns would
   invent labels — badly, because several of those follow-ups are the *challenge*
   that got a correct answer out of a run recorded as a failure.
2. **A prompt with several matching conversations is labelled only when the log
   gives one outcome for all of them**, so the label does not depend on resolving
   which attempt is which. The 2026-09-09 base plate is the case this rule is for:
   the log records four attempts and six conversations carry that prompt, and
   every one of them is a FAIL either way.
3. **A mixed entry is left unlabelled.** The 2026-09-10 night L3 bracket is
   recorded as two attempts that ran zero tool calls followed by a pass after the
   defect was fixed, across four conversations. Deciding which conversation is
   which from its step count would be reading the answer off the data the label
   is supposed to be independent of.

Run: `venv/bin/python -m scripts.horizon_report [--out data/verify/horizon.json]`
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Conversation
from app.verify import horizon


@dataclass(frozen=True, slots=True)
class LadderOutcome:
    """One run-log entry, as a claim about every conversation matching it."""

    #: The opening words of the prompt, exactly as the run log quotes them.
    prompt_prefix: str
    #: The run log's date heading. A prompt is re-used across runs on purpose —
    #: the 2026-09-10 L2 pass is "the same prompt as the four failures of
    #: 2026-09-09", and that comparison is only clean because the date separates
    #: them.
    run_date: str
    level: str
    succeeded: bool
    note: str


#: Every run-log entry that survives the three rules in this module's docstring.
#: Entries the log records and this table deliberately omits are listed in
#: `UNLABELLED` below, so a reader can see what was declined rather than assume
#: it was missed.
LADDER_OUTCOMES: Final[tuple[LadderOutcome, ...]] = (
    LadderOutcome(
        prompt_prefix="Make an aluminium tube 120 mm long",
        run_date="2026-09-09",
        level="L1",
        succeeded=True,
        note="65,973 mm3 and 0.178 kg against 65,973.446 and 0.17813.",
    ),
    LadderOutcome(
        prompt_prefix="Make a steel base plate 120 x 80 x 10 mm",
        run_date="2026-09-09",
        level="L2",
        succeeded=False,
        note=(
            "Four attempts in the log, six conversations carrying the prompt. All "
            "failed, so the label does not depend on which is which. The part is "
            "buildable and measures exactly right through the runner -- the model "
            "failed four different ways to find the route."
        ),
    ),
    LadderOutcome(
        prompt_prefix="Make a steel hexagonal prism",
        run_date="2026-09-10",
        level="L1",
        succeeded=True,
        note="77,942 mm3 against 77,942.286. 8 steps, zero refusals, 23 s.",
    ),
    LadderOutcome(
        prompt_prefix="Make an aluminium mounting plate 140 mm by 90 mm",
        run_date="2026-09-10",
        level="L2",
        succeeded=True,
        note="0.4127186544971364 kg against 0.412719 -- exact to six decimals.",
    ),
    LadderOutcome(
        prompt_prefix="I need a mild steel cantilever bracket: a flat bar 200 mm",
        run_date="2026-09-10",
        level="L4",
        succeeded=False,
        note=(
            "Stated a PASS with '9 MPa of margin' from a single-grid tet4 mesh whose "
            "own record says converged: false. Peak stress overstated by ~90%."
        ),
    ),
    LadderOutcome(
        prompt_prefix="Make an aluminium truncated cone",
        run_date="2026-09-11",
        level="L1",
        succeeded=True,
        note="175,929 mm3 against 175,929.1886 -- exact. 12 steps, 848 s.",
    ),
)


#: Run-log entries deliberately not labelled, with the rule that declined each.
UNLABELLED: Final[tuple[tuple[str, str], ...]] = (
    (
        "2026-09-10 night L3 -- 'Make a mild steel bracket 100 mm by 60 mm and 8 mm thick'",
        "Rule 3: the log records two attempts that ran zero tool calls and then a pass "
        "after the defect was fixed. Four conversations carry the prompt, so no single "
        "outcome covers them, and telling them apart by step count would read the label "
        "off the trace it is meant to judge.",
    ),
    (
        "2026-09-11 L2 -- 'Make a mild steel spacer block 90 mm'",
        "Rule 3: recorded as FAIL then PASS on the re-run. Two conversations carry the "
        "prompt and chronology would resolve them, but 're-run' as evidence of order is "
        "an inference the log does not state, and both runs built the right part -- the "
        "first is a failure only because two Kryova defects fired during it.",
    ),
    (
        "Every turn after the first in a labelled conversation",
        "Rule 1: the run log says nothing about them. Several are the engineer's "
        "challenge to an answer, and the 2026-09-10 L4 note records that the agent got "
        "it entirely right when challenged -- so carrying the rung's FAIL forward would "
        "label a correct turn wrong.",
    ),
    (
        "The 2026-09-07 and 2026-09-08 conversations (the arbor press and after)",
        "They predate the run log's current format, which began on 2026-09-09. The "
        "retired named prompts are in this file's git history, not in the log.",
    ),
)


def build_labels(db, traces: list[horizon.TurnTrace]) -> dict[tuple[str, int], bool]:
    """Match run-log entries onto the first turn of each matching conversation."""
    titles = {
        row.id: (row.title or "", row.created_at)
        for row in db.scalars(select(Conversation))
    }
    first_turn: dict[str, horizon.TurnTrace] = {}
    for trace in sorted(traces, key=lambda t: (t.conversation_id, t.sequence)):
        first_turn.setdefault(trace.conversation_id, trace)

    labels: dict[tuple[str, int], bool] = {}
    for outcome in LADDER_OUTCOMES:
        for conversation_id, trace in first_turn.items():
            title, _ = titles.get(conversation_id, ("", None))
            if not title.startswith(outcome.prompt_prefix):
                continue
            if trace.started_at.strftime("%Y-%m-%d") != outcome.run_date:
                continue
            labels[(conversation_id, trace.sequence)] = outcome.succeeded
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/verify/horizon.json")
    args = parser.parse_args()

    with SessionLocal() as db:
        traces = horizon.read_traces(db)
        labels = build_labels(db, traces)

    report = horizon.measure(traces, outcomes=labels)
    payload = report.to_dict()
    payload["unlabelled"] = [
        {"entry": entry, "why": why} for entry, why in UNLABELLED
    ]
    payload["labels_from"] = "docs/GUI_PROMPT_LADDER.md run log"

    out = Path(args.out)
    out.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"{report.traces} turns, {report.labelled} labelled -> {out}")
    print(f"{'bucket':>14} {'turns':>6} {'compl.':>8} {'labelled':>9} {'success':>8}")
    for one in report.buckets:
        rate = "-" if one.completion_rate is None else f"{one.completion_rate:.2f}"
        success = "-" if one.success_rate is None else f"{one.success_rate:.2f}"
        print(f"{one.bucket.label:>14} {one.turns:6d} {rate:>8} {one.labelled:9d} {success:>8}")
    print()
    print(report.statement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
