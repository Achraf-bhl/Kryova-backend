"""Mixins that widen `MockCatia` beyond the part it started as.

The mock began as one class covering the original 39 tools. Growing it in place
to cover 200 would produce exactly the file the COM backend was split up to
avoid, so the same shape is used here: one module per domain, each holding real
in-memory state rather than a stub that returns success.

**What belongs here and what does not.** The mock's own docstring is firm that
being obviously approximate beats being subtly wrong — its solid is a box, and a
pocket subtracts a volume rather than cutting a shape. That rules out mocking
the surfacing and part-design tools whose whole answer *is* the geometry: a
fake loft would produce a mass and a bounding box that look real and are not.

What it does not rule out is everything whose answer is structure rather than
shape. A parameter has a value, a formula recomputes it, an assembly has a
component tree, a drawing has sheets and views. Those are modelled exactly, so
the tools that read them back give real answers — and the ones that cannot be
modelled honestly stay absent, where `implemented_tools()` reports them absent
and the server never offers them to the model.
"""

from .knowledge import MockKnowledgeMixin

#: Every mock mixin, in method-resolution order.
MOCK_MIXINS = (MockKnowledgeMixin,)

__all__ = ["MOCK_MIXINS", "MockKnowledgeMixin"]
