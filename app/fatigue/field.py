"""Signed stress histories from what a solver returned — master plan 8.1.

`history.py` refuses to count a von Mises history, because a norm has no sign and a
fully reversed cycle reads in it as two half-range cycles. Its docstring used to end
with the fix being upstream: the solver federation had to expose a signed scalar.
It does. `SolveOutput.nodal_stress` is the full tensor at every node, in Voigt order
SXX SYY SZZ SXY SYZ SZX, from the in-house solver and from CalculiX alike, and this
module is the step from that tensor to a `LoadHistory` the assessment will count.

**Superposition, and what it rests on.** A linear-elastic part under several loads
that vary in time carries

    σ(t) = Σ_k s_k(t) · σ_k

where σ_k is the stress field solved for load k and s_k(t) is that load's signal, a
dimensionless multiple of the load as it was solved. That is exact for a linear-static
solve, and it is how an FE-based fatigue assessment is normally posed: one solve per
independent load, then a history at every node for free. Every structural `Solver` in
this repository is linear-static, so nothing here checks for anything else; the day a
nonlinear solver lands behind the seam, a `LoadChannel` built from its output is the
thing that must refuse, and `LoadChannel.solver` is carried so that check has
something to read.

**Three signed scalars, and which one to reach for.**

* `Scalar.COMPONENT` — the normal stress on a declared plane, `n·σ·n`. Linear in σ, so
  exact under any number of channels, and the right choice wherever the crack plane is
  known: across a weld toe, along a shaft's axis. The direction is an input with a
  reason, because choosing the plane is the judgement.
* `Scalar.PRINCIPAL` — the principal stress of largest magnitude, with its sign. Right
  for a free surface under one load. Under several channels that do not load the point
  proportionally, the principal axis **rotates**, and a scalar read off a rotating axis
  is not the stress on any one plane. The history reports how far the axis turned
  (`principal_axis_rotation_deg`) rather than deciding for the caller how far is too
  far; a critical-plane method is what such a point actually needs, and none is here.
* `Scalar.SIGNED_VON_MISES` — von Mises, signed by the hydrostatic stress. The common
  compromise, with a known failure: where the hydrostatic stress passes through zero
  while von Mises is large (a shaft in torsion), the sign flips and manufactures a
  full-range cycle. That errs towards more damage, not less, and the history counts the
  flips (`hydrostatic_sign_changes`) so a reviewer can see when it happened.

**Where the history was read decides what the assessment may do to it.** A node's
stress in a meshed model is a local stress, so the default basis is
`StressBasis.NOTCH_ROOT` and the assessment will refuse a stress concentration on top
of it. `NOMINAL` is allowed, for the honest case of a model that deliberately omits the
notch (the fillet was left out, Kt comes from a chart) — the caller says so. `HOT_SPOT`
is refused here: a hot-spot stress is an extrapolation to a weld toe, not a nodal value,
and nothing in this package makes one yet (master plan 8.5).

Units are the codebase's mm-N-MPa throughout; nothing here converts.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.fatigue.history import LoadHistory, SignConvention, StressBasis
from app.fatigue.sources import require_source

#: Voigt order of `SolveOutput.nodal_stress`, spelled out once so the tensor
#: assembly below and the docstring cannot disagree about it.
VOIGT_ORDER: Final[tuple[str, ...]] = ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")

#: Nodes are ranked in blocks of this many so a 100 000-node field under a
#: thousand-step signal does not allocate every history at once.
_RANK_BLOCK_NODES: Final[int] = 4096


class Scalar(StrEnum):
    """Which signed scalar a history is made of. The module docstring says which to choose."""

    COMPONENT = "component"
    PRINCIPAL = "principal"
    SIGNED_VON_MISES = "signed_von_mises"


@dataclass(frozen=True)
class LoadChannel:
    """One independently varying load: the stress it causes, and how it varies.

    `stress_mpa` is the (n_nodes, 6) nodal tensor of the load **as it was solved**,
    and `signal` is a sequence of dimensionless multiples of that load. A channel
    solved at 5 kN whose signal is `(0, 1, -1, 0)` describes the load going to +5 kN,
    to −5 kN and back.

    `source` names what the signal is — a measured strain-gauge block, a duty-cycle
    specification, a multibody run — because the signal is where most of a fatigue
    answer's uncertainty lives and the solve is where least of it does.
    """

    name: str
    stress_mpa: NDArray[np.float64]
    signal: tuple[float, ...]
    source: str
    #: The solver that produced the field, carried onto the history's source.
    solver: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A load channel's signal"))
        if not self.name.strip():
            raise ValueError("A load channel must be named; its name is how a history says where it came from.")
        stress = np.asarray(self.stress_mpa, dtype=np.float64)
        if stress.ndim != 2 or stress.shape[1] != 6:
            raise ValueError(
                f"The {self.name!r} channel's stress must be one six-component tensor per node, "
                f"(n_nodes, 6) in the order {' '.join(VOIGT_ORDER)}; got shape {stress.shape}."
            )
        if not np.isfinite(stress).all():
            raise ValueError(
                f"The {self.name!r} channel's stress field contains a non-finite value. A NaN in a "
                "tensor becomes a NaN in every history built from it and a damage that still prints."
            )
        signal = tuple(float(v) for v in self.signal)
        if len(signal) < 3:
            raise ValueError(
                f"The {self.name!r} channel's signal needs at least three samples before a cycle can "
                f"close in it; got {len(signal)}."
            )
        if not all(math.isfinite(v) for v in signal):
            raise ValueError(f"The {self.name!r} channel's signal contains a non-finite sample.")
        object.__setattr__(self, "stress_mpa", stress)
        object.__setattr__(self, "signal", signal)

    @classmethod
    def from_solve(
        cls,
        name: str,
        output: object,
        signal: Sequence[float],
        *,
        source: str,
        solver: str = "",
    ) -> LoadChannel:
        """A channel from a `SolveOutput`, refused by name when the tensor is absent.

        `SolveOutput.nodal_stress` is optional on purpose (a surrogate may predict
        von Mises and no tensor), so a solver that did not produce one is named
        rather than worked around: reconstructing a tensor from von Mises is not
        possible, and substituting von Mises is the unsigned history this module
        exists to replace.
        """
        stress = getattr(output, "nodal_stress", None)
        if stress is None:
            who = f" from {solver}" if solver else ""
            raise ValueError(
                f"The solve{who} for {name!r} returned no nodal stress tensor, so no signed stress "
                "history can be made from it. A von Mises field is a norm and cannot stand in. "
                "Run it on a solver that reports nodal stress (the in-house linear-static solver "
                "and CalculiX both do)."
            )
        return cls(name=name, stress_mpa=stress, signal=tuple(signal), source=source, solver=solver)

    @property
    def node_count(self) -> int:
        return int(self.stress_mpa.shape[0])

    @property
    def steps(self) -> int:
        return len(self.signal)


@dataclass(frozen=True)
class Direction:
    """The plane normal a `Scalar.COMPONENT` history is read across, with the reason."""

    vector: tuple[float, float, float]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", require_source(self.reason, "A stress direction"))
        v = np.asarray(self.vector, dtype=np.float64)
        length = float(np.linalg.norm(v))
        if v.shape != (3,) or not math.isfinite(length) or length == 0.0:
            raise ValueError(
                "A stress direction is a non-zero, finite 3-vector; it is normalised here, so "
                f"its length does not matter, but its existence does. Got {self.vector!r}."
            )
        object.__setattr__(self, "vector", tuple(float(c) for c in v / length))


@dataclass(frozen=True)
class NodeHistory:
    """A signed stress history at one node, and what is known about how it was read."""

    history: LoadHistory
    node: int
    position_mm: tuple[float, float, float] | None
    scalar: Scalar
    #: How far the major principal axis turned over the history, in degrees. Only
    #: for `Scalar.PRINCIPAL`; zero under one channel, by construction.
    principal_axis_rotation_deg: float | None = None
    #: How many times the hydrostatic stress changed sign while von Mises was not
    #: zero. Only for `Scalar.SIGNED_VON_MISES`.
    hydrostatic_sign_changes: int | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedNode:
    """A node and the stress range its history spans, for choosing where to assess."""

    node: int
    range_mpa: float


def _check_channels(channels: Sequence[LoadChannel]) -> None:
    if not channels:
        raise ValueError("A stress history needs at least one load channel.")
    nodes = {c.node_count for c in channels}
    if len(nodes) != 1:
        raise ValueError(
            "Every load channel must be solved on the same mesh; the channels have "
            f"{sorted(nodes)} nodes. Superposing fields from two meshes node by node adds "
            "stresses at unrelated points."
        )
    steps = {c.steps for c in channels}
    if len(steps) != 1:
        raise ValueError(
            "Every load channel's signal must have one sample per time step, the same number of "
            f"steps for all of them; got {sorted(steps)}. Resample the signals onto one time base "
            "before superposing them."
        )
    names = [c.name for c in channels]
    if len(set(names)) != len(names):
        raise ValueError(f"Two load channels share a name ({names}); a history must say which load is which.")


def tensor_history(channels: Sequence[LoadChannel], node: int) -> NDArray[np.float64]:
    """σ(t) at one node, (steps, 6), by superposing every channel's tensor with its signal."""
    _check_channels(channels)
    count = channels[0].node_count
    if not 0 <= node < count:
        raise ValueError(f"Node {node} is not on this mesh, which has nodes 0 to {count - 1}.")
    signals = np.array([c.signal for c in channels], dtype=np.float64)  # (channels, steps)
    tensors = np.array([c.stress_mpa[node] for c in channels], dtype=np.float64)  # (channels, 6)
    return signals.T @ tensors


def _matrices(voigt: NDArray[np.float64]) -> NDArray[np.float64]:
    """(…, 6) Voigt → (…, 3, 3) symmetric tensors, in `VOIGT_ORDER`."""
    sxx, syy, szz, sxy, syz, szx = (voigt[..., i] for i in range(6))
    return np.stack(
        [
            np.stack([sxx, sxy, szx], axis=-1),
            np.stack([sxy, syy, syz], axis=-1),
            np.stack([szx, syz, szz], axis=-1),
        ],
        axis=-2,
    )


def component(voigt: NDArray[np.float64], direction: Direction) -> NDArray[np.float64]:
    """n·σ·n for every tensor in (…, 6)."""
    n = np.asarray(direction.vector, dtype=np.float64)
    return np.einsum("i,...ij,j->...", n, _matrices(voigt), n)


def von_mises(voigt: NDArray[np.float64]) -> NDArray[np.float64]:
    """The von Mises norm of every tensor in (…, 6)."""
    sxx, syy, szz, sxy, syz, szx = (voigt[..., i] for i in range(6))
    return np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + syz**2 + szx**2)
    )


def signed_von_mises(voigt: NDArray[np.float64]) -> NDArray[np.float64]:
    """Von Mises signed by the first invariant. A zero hydrostatic stress signs positive.

    Positive rather than zero: signing a sheared point zero would read a shaft in
    pure torsion as unstressed, which is the one error in this family that makes a
    part look safer than it is.
    """
    trace = voigt[..., 0] + voigt[..., 1] + voigt[..., 2]
    sign = np.where(trace < 0.0, -1.0, 1.0)
    return sign * von_mises(voigt)


def principal(voigt: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """The largest-magnitude principal stress of each tensor, and its axis.

    Returns (values (…), axes (…, 3)). A tie in magnitude between a tensile and a
    compressive principal stress goes to the tensile one, which is the one that
    opens a crack.
    """
    values, vectors = np.linalg.eigh(_matrices(voigt))  # ascending
    lowest = values[..., 0]
    highest = values[..., 2]
    take_high = np.abs(highest) >= np.abs(lowest)
    chosen = np.where(take_high, highest, lowest)
    axes = np.where(take_high[..., None], vectors[..., :, 2], vectors[..., :, 0])
    return chosen, axes


def history_at(
    channels: Sequence[LoadChannel],
    node: int,
    *,
    scalar: Scalar,
    direction: Direction | None = None,
    basis: StressBasis = StressBasis.NOTCH_ROOT,
    nodes_mm: NDArray[np.float64] | None = None,
    name: str = "",
) -> NodeHistory:
    """The signed scalar history at `node`, ready for `Assessment`."""
    if basis is StressBasis.HOT_SPOT:
        raise ValueError(
            "A nodal stress is not a hot-spot stress. A hot-spot stress is extrapolated to the weld "
            "toe from read-out points ahead of it, and nothing in app.fatigue makes one yet (master "
            "plan 8.5). Read the nodal stress as NOTCH_ROOT, or as NOMINAL where the model omits the notch."
        )
    if scalar is Scalar.COMPONENT and direction is None:
        raise ValueError(
            "A component history needs the plane it is read across — the normal to the expected "
            "crack plane, with the reason it was chosen."
        )
    if scalar is not Scalar.COMPONENT and direction is not None:
        raise ValueError(
            f"A direction was given for a {scalar} history, which does not read one. Either ask "
            "for a component history or drop the direction, so the result says what was used."
        )
    tensors = tensor_history(channels, node)
    notes: list[str] = []
    rotation: float | None = None
    flips: int | None = None
    if scalar is Scalar.COMPONENT:
        assert direction is not None
        values = component(tensors, direction)
        how = f"normal stress across n = {direction.vector} ({direction.reason})"
    elif scalar is Scalar.SIGNED_VON_MISES:
        values = signed_von_mises(tensors)
        trace = tensors[:, 0] + tensors[:, 1] + tensors[:, 2]
        loaded = von_mises(tensors) > 0.0
        signs = np.where(trace < 0.0, -1, 1)[loaded]
        flips = int(np.count_nonzero(np.diff(signs)))
        how = "von Mises signed by the hydrostatic stress"
        if flips:
            notes.append(
                f"The hydrostatic stress changed sign {flips} time(s) while the point was loaded. "
                "Each change reverses the signed von Mises value and can manufacture a cycle the "
                "material never saw; that errs towards more damage. A component history across "
                "the crack plane is the better-posed question at this point."
            )
    else:
        values, axes = principal(tensors)
        peak = int(np.argmax(np.abs(values)))
        reference = axes[peak]
        loaded = np.abs(values) > 0.0
        cosines = np.clip(np.abs(axes[loaded] @ reference), 0.0, 1.0)
        rotation = float(np.degrees(np.arccos(cosines.min()))) if cosines.size else 0.0
        how = "largest-magnitude principal stress, signed"
        if len(channels) > 1:
            notes.append(
                f"{len(channels)} channels were superposed and the major principal axis turned by up "
                f"to {rotation:.3g} degrees over the history. A principal stress read off a turning "
                "axis is not the stress on one plane; a critical-plane assessment is what that point "
                "needs, and none is implemented here."
            )
    solvers = sorted({c.solver for c in channels if c.solver})
    source = (
        f"{how} at node {node}, superposed from "
        + "; ".join(f"channel {c.name!r} [{c.source}]" for c in channels)
        + (f"; stress from {', '.join(solvers)}" if solvers else "")
    )
    history = LoadHistory.from_values(
        name or f"node {node}",
        values,
        basis=basis,
        sign=SignConvention.SIGNED,
        source=source,
    )
    position = None
    if nodes_mm is not None:
        p = np.asarray(nodes_mm, dtype=np.float64)[node]
        position = (float(p[0]), float(p[1]), float(p[2]))
    return NodeHistory(
        history=history,
        node=node,
        position_mm=position,
        scalar=scalar,
        principal_axis_rotation_deg=rotation,
        hydrostatic_sign_changes=flips,
        notes=tuple(notes),
    )


def ranked_nodes(
    channels: Sequence[LoadChannel],
    *,
    scalar: Scalar,
    count: int,
    direction: Direction | None = None,
    candidates: Sequence[int] | None = None,
) -> tuple[RankedNode, ...]:
    """The `count` nodes whose signed history spans the widest range, widest first.

    **A range ranks; a damage decides.** Under one S-N curve and no mean-stress
    correction a wider range is always more damage, but a mean-stress correction can
    reorder two nodes of similar range and opposite mean, so this chooses where to run
    the assessment and never replaces running it. The range is exact — the max minus
    the min of the same history `history_at` would build — not an estimate.
    """
    _check_channels(channels)
    if count < 1:
        raise ValueError("Ask for at least one node.")
    if scalar is Scalar.COMPONENT and direction is None:
        raise ValueError("A component ranking needs the plane it is read across.")
    total = channels[0].node_count
    pool = np.arange(total) if candidates is None else np.asarray(candidates, dtype=np.int64)
    if pool.size and (pool.min() < 0 or pool.max() >= total):
        raise ValueError(f"A candidate node is not on this mesh, which has nodes 0 to {total - 1}.")
    signals = np.array([c.signal for c in channels], dtype=np.float64)  # (channels, steps)
    ranges = np.empty(pool.size, dtype=np.float64)
    for start in range(0, pool.size, _RANK_BLOCK_NODES):
        block = pool[start : start + _RANK_BLOCK_NODES]
        stacked = np.stack([c.stress_mpa[block] for c in channels], axis=0)  # (ch, nodes, 6)
        tensors = np.einsum("cs,cnv->snv", signals, stacked)  # (steps, nodes, 6)
        if scalar is Scalar.COMPONENT:
            assert direction is not None
            values = component(tensors, direction)
        elif scalar is Scalar.SIGNED_VON_MISES:
            values = signed_von_mises(tensors)
        else:
            values = principal(tensors)[0]
        ranges[start : start + block.size] = values.max(axis=0) - values.min(axis=0)
    order = np.argsort(-ranges, kind="stable")[:count]
    return tuple(RankedNode(node=int(pool[i]), range_mpa=float(ranges[i])) for i in order)


def nearest_node(nodes_mm: NDArray[np.float64], point_mm: Sequence[float]) -> tuple[int, float]:
    """The node closest to a point, and how far away it is, in mm.

    The distance is returned, not hidden: the stress at a fillet root is asked for at
    a point, and a node 2 mm away on a coarse mesh is a different answer that should
    be visible as one.
    """
    p = np.asarray(point_mm, dtype=np.float64)
    nodes = np.asarray(nodes_mm, dtype=np.float64)
    if p.shape != (3,) or nodes.ndim != 2 or nodes.shape[1] != 3 or len(nodes) == 0:
        raise ValueError("A point is a 3-vector and a mesh has (n, 3) node coordinates.")
    distances = np.linalg.norm(nodes - p, axis=1)
    index = int(np.argmin(distances))
    return index, float(distances[index])


__all__ = [
    "VOIGT_ORDER",
    "Direction",
    "LoadChannel",
    "NodeHistory",
    "RankedNode",
    "Scalar",
    "component",
    "history_at",
    "nearest_node",
    "principal",
    "ranked_nodes",
    "signed_von_mises",
    "tensor_history",
    "von_mises",
]
