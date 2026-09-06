"""Phase 15.5 — the measurement, built before the thing being measured.

Nothing in Kryova can currently answer *why was that slow*. The phases landing
around this one are exactly the ones that will need to: a clash check over a
5,000-part assembly, an optimisation loop doing hundreds of rebuilds, a
CalculiX solve on a real mesh. The measurement has to exist first, or the first
answer to the question is a rewrite.

What this package is:

- `records` — a `Span`, a `Distribution`, a `Summary`. Frozen, clockless data.
- `collect` — `span()` and `collect()`. Near-free when nobody is collecting.
- `queue` — the job queue as numbers: depth, wait, run, failures.
- `catalogue` — every span this system can produce, *including the ones nothing
  emits yet*, so the report can name a hole instead of omitting it.
- `report` — the roll-up, and the list of what it could not measure.

What this package deliberately is not:

**No metrics vendor, no OpenTelemetry, no exporter thread.** A dependency here
is a named decision, and the useful thing at this stage is the vocabulary and
the hooks — an exporter is a later, smaller change that attaches to `Recorder`.
An exporter thread added now would also be a background thread nobody asked for,
in a process that already runs a job pool.

**No sampling, no histograms with fixed buckets, no per-second aggregation.**
Those are the shapes a metrics *system* needs at millions of points a second.
Kryova's expensive operations are seconds to minutes and number in the hundreds,
so keeping every span and computing exact order statistics is both affordable
and more honest than a bucketed estimate.

`report` is not re-exported from here on purpose: it imports
`app.design.assertions` for the `UNMEASURED` vocabulary, and the modules that
carry hooks — `app.mesh.gmsh_session`, `app.media.store` — should not pull the
design IR in behind a timing call. Import `app.observe.report` where a report is
actually built.
"""

from app.observe.catalogue import SITES, Site
from app.observe.collect import (
    INERT,
    LiveSpan,
    Recorder,
    collect,
    current_recorder,
    current_span,
    is_collecting,
    note,
    record,
    span,
)
from app.observe.queue import METER, JobTicket, QueueMeter, QueueSnapshot
from app.observe.records import Distribution, Note, Span, Summary

__all__ = [
    "INERT",
    "METER",
    "SITES",
    "Distribution",
    "JobTicket",
    "LiveSpan",
    "Note",
    "QueueMeter",
    "QueueSnapshot",
    "Recorder",
    "Site",
    "Span",
    "Summary",
    "collect",
    "current_recorder",
    "current_span",
    "is_collecting",
    "note",
    "record",
    "span",
]
