"""The reference assembly every renderer threshold is measured on — P6 task 2, QUEUE G1.

**A threshold against an unnamed scene is not a threshold.** `docs/RENDERER_DECISION.md`
§4 says so and then records that the scene does not exist: the plan names M5's stamping
press, and the largest assembly this repository could produce when that was written was
M6's belt conveyor with **three** components. Three against a two-thousand-part target.
So §4 defines the reference assembly **by its properties** and calls building a synthetic
one "a prerequisite of the decision, not part of it". This is that generator.

**What changed since §4 was written, and it does not change the conclusion.** M5 landed on
2026-09-16 and M8 on 2026-09-17, so "M5 is not built and is blocked on E13" is no longer
true. It does not help: M5 is **eleven** parts and M8 is **eight** occurrences. The gap to
2,000 was never about which rung had landed, and a real machine of the right size is still
several eras away. §4's preference for M5 "the day it exists" stands for the day this
repository can build a machine of that *size*, not merely that name.

**So every measurement taken on this is labelled synthetic**, which is §4's own
instruction and the reason `properties()` reports `synthetic=True` rather than leaving a
reader to remember. A threshold met on a synthetic scene and missed on a real one is
exactly the confusion that document exists to prevent.

What the table asks for, and how each property is obtained
----------------------------------------------------------
| Property | §4 | Here |
|---|---|---|
| Occurrences | 2,000 | `OCCURRENCES`, exact |
| Distinct components | 120 | `DISTINCT_COMPONENTS`, exact |
| Instanced fraction | ≥ 90% | 99%: the twenty frames are placed once each, everything else repeats |
| Deepest nesting | ≥ 4 | 5: machine → station → module → subassembly → cluster → part |
| Triangles, level 0 | 8–12 M | **not decided here** — see below |
| Triangles after LOD | ≤ 1.5 M | **not decided here** |

**The two triangle rows are deliberately not the generator's business**, and saying so is
better than producing a number. They are a property of `app/render/display.py`'s levels
applied to these parts, so they are *measured* by tessellating the result — which needs
the kernel, and importing the kernel here would drag ~166 MB of OCP into every caller of
`app/render/`. `tests/test_render_reference.py` measures them through the kernel and
records what it found; if the count falls outside §4's band the honest response is to
change the part sizes here until it does not, not to widen the band.

**It is deterministic and pinned by digest.** Two runs a month apart must compare, so
nothing here is random: the variation across components is arithmetic on the index, and
`ProductStructure.digest()` over the result is asserted in the tests. A generator whose
output drifts would make every historical measurement incomparable, which is the failure
that makes a performance threshold worthless a year later.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from app.assembly.placement import at, compose, turned
from app.assembly.structure import ProductStructure, StructureBuilder

#: §4's occurrence target, exactly. P6 task 2's own "2,000-part machine".
OCCURRENCES: Final = 2_000

#: §4's distinct-component target. "A real machine reuses heavily; this is the ratio that
#: makes instancing matter."
DISTINCT_COMPONENTS: Final = 120

#: The floor §4 sets on the fraction of occurrences whose component is placed more than
#: once. Below it, instancing is untested — and instancing is the whole reason a
#: 2,000-part machine is servable at all (`scene_for` makes forty bolts one bolt's bytes).
MIN_INSTANCED_FRACTION: Final = 0.90

#: §4's floor on tree depth, which is what exercises `subtree`, explode and hide/isolate.
MIN_DEPTH: Final = 4

ROOT: Final = "machine"

#: The shape of the tree. Leaf occurrences land at depth **5**, one past §4's floor,
#: because a floor met exactly is a floor the next change breaks. Measured, not asserted:
#: a first draft stopped at subassemblies and came out at exactly 4.
STATIONS: Final = 5
MODULES_PER_STATION: Final = 4
SUBASSEMBLIES_PER_MODULE: Final = 5
CLUSTERS_PER_SUBASSEMBLY: Final = 2
PER_CLUSTER: Final = 10

#: How the 120 distinct components divide. **Each frame is placed exactly once**, which is
#: what keeps the instanced fraction off 100%: a scene where *everything* is instanced does
#: not exercise the mixed case a real machine has, where a handful of weldments are unique
#: and everything else repeats. 20 single-use occurrences in 2,000 gives 99%, comfortably
#: over §4's 90% floor and not equal to it.
FRAME_COMPONENTS: Final = 20
PART_COMPONENTS: Final = DISTINCT_COMPONENTS - FRAME_COMPONENTS


@dataclass(frozen=True)
class ReferenceProperties:
    """What the generated assembly actually is, measured rather than declared.

    Every field is counted off the structure by walking it, so a change to the generator
    that quietly stopped meeting §4 shows up here rather than in a benchmark six months
    later. `synthetic` is not a parameter: this artefact is synthetic, §4 requires every
    measurement taken on it to say so, and a flag a caller could set to False would be a
    way to stop saying it.
    """

    occurrences: int
    distinct_components: int
    instanced_occurrences: int
    depth: int
    digest: str
    synthetic: bool = True

    @property
    def instanced_fraction(self) -> float:
        if self.occurrences == 0:
            return 0.0
        return self.instanced_occurrences / self.occurrences

    def meets_specification(self) -> tuple[bool, tuple[str, ...]]:
        """Whether §4's table is met, and every row that is not.

        Returns the failures rather than a bare bool for `app/verify/`'s reason: a
        criterion that fails with no statement of which part failed sends a reader to
        re-derive it.
        """
        failures: list[str] = []
        if self.occurrences != OCCURRENCES:
            failures.append(f"occurrences {self.occurrences}, expected {OCCURRENCES}")
        if self.distinct_components != DISTINCT_COMPONENTS:
            failures.append(
                f"distinct components {self.distinct_components}, expected {DISTINCT_COMPONENTS}"
            )
        if self.instanced_fraction < MIN_INSTANCED_FRACTION:
            failures.append(
                f"instanced fraction {self.instanced_fraction:.3f}, "
                f"below {MIN_INSTANCED_FRACTION}"
            )
        if self.depth < MIN_DEPTH:
            failures.append(f"depth {self.depth}, below {MIN_DEPTH}")
        return not failures, tuple(failures)

    def to_dict(self) -> dict[str, object]:
        met, failures = self.meets_specification()
        return {
            "occurrences": self.occurrences,
            "distinct_components": self.distinct_components,
            "instanced_occurrences": self.instanced_occurrences,
            "instanced_fraction": self.instanced_fraction,
            "depth": self.depth,
            "digest": self.digest,
            "synthetic": self.synthetic,
            "meets_specification": met,
            "failures": list(failures),
        }


def _part_name(index: int) -> str:
    return f"part_{index:03d}"


def _frame_name(index: int) -> str:
    return f"frame_{index:02d}"


def _placement(index: int, spacing_mm: float):
    """A deterministic, varied transform. Arithmetic on the index, never random.

    Varied because §4 asks for replication "with varied transforms": a scene whose every
    instance sits at the same place would let a renderer cull or batch it in a way no real
    machine permits, and the measurement would flatter itself. Rotations are included
    because an instance frame is a full transform and the glTF writer refuses a mirrored
    one — a generator that only ever translated would never exercise that path.
    """
    angle = (index % 12) * (math.tau / 12.0)
    return compose(
        at(
            spacing_mm * float(index % 7),
            spacing_mm * float((index // 7) % 5),
            spacing_mm * 0.25 * float(index % 3),
        ),
        turned((0.0, 0.0, 1.0), angle),
    )


def reference_assembly() -> ProductStructure:
    """§4's synthetic reference assembly: 2,000 occurrences over 120 components, 5 deep.

    Built by replication with varied transforms, as §4 specifies. The counts are chosen to
    land on 2,000 exactly rather than approximately, because "about two thousand" is not
    something two runs a month apart can be compared on.

    Structure: `machine` → 5 stations → 4 modules each → 5 subassemblies each → 2 clusters
    each → parts. That is 200 clusters of ten occurrences, which is 2,000 exactly, and it
    puts every leaf at depth 5.
    """
    builder = StructureBuilder()
    builder.define(ROOT, description="Synthetic reference assembly for renderer thresholds.")

    for index in range(PART_COMPONENTS):
        builder.define(
            _part_name(index),
            design=f"reference part {index:03d}",
            material="steel-1018",
            description="A replicated part. Its size varies with its index; see `reference.py`.",
        )
    for index in range(FRAME_COMPONENTS):
        builder.define(
            _frame_name(index),
            design=f"reference frame {index:02d}",
            material="steel-1018",
            description="Placed once. What keeps the instanced fraction off 100%.",
        )

    for station in range(STATIONS):
        builder.define(f"station_{station}", description=f"Station {station}.")
        for module in range(MODULES_PER_STATION):
            module_name = f"station_{station}_module_{module}"
            builder.define(module_name, description=f"Module {module} of station {station}.")
            for sub in range(SUBASSEMBLIES_PER_MODULE):
                sub_name = f"{module_name}_sub_{sub}"
                builder.define(sub_name, description=f"Subassembly {sub}.")
                for cluster in range(CLUSTERS_PER_SUBASSEMBLY):
                    cluster_name = f"{sub_name}_cluster_{cluster}"
                    builder.define(cluster_name, description=f"Cluster {cluster}.")
                    builder.add(
                        sub_name, cluster_name, placement=at(0.0, 180.0 * cluster, 0.0)
                    )
                builder.add(module_name, sub_name, placement=at(0.0, 0.0, 120.0 * sub))
            builder.add(f"station_{station}", module_name, placement=at(0.0, 400.0 * module, 0.0))
        builder.add(ROOT, f"station_{station}", placement=at(900.0 * station, 0.0, 0.0))

    # 200 clusters of ten occurrences each is 2,000 exactly. The first twenty clusters
    # carry one frame apiece — every frame placed once, and the only un-instanced
    # occurrences in the machine — and nine parts; the other 180 carry ten parts.
    clusters = [
        f"station_{station}_module_{module}_sub_{sub}_cluster_{cluster}"
        for station in range(STATIONS)
        for module in range(MODULES_PER_STATION)
        for sub in range(SUBASSEMBLIES_PER_MODULE)
        for cluster in range(CLUSTERS_PER_SUBASSEMBLY)
    ]
    for position, cluster_name in enumerate(clusters):
        slots = PER_CLUSTER
        if position < FRAME_COMPONENTS:
            builder.add(
                cluster_name,
                _frame_name(position),
                placement=at(0.0, 0.0, 0.0),
                note="Placed exactly once in the machine: the un-instanced remainder.",
            )
            slots -= 1
        for slot in range(slots):
            index = (position * PER_CLUSTER + slot) % PART_COMPONENTS
            builder.add(
                cluster_name,
                _part_name(index),
                placement=_placement(position * PER_CLUSTER + slot, 35.0),
            )
    return builder.build(ROOT)


def properties(structure: ProductStructure | None = None) -> ReferenceProperties:
    """Walk the assembly and report what it actually is.

    Counted, never declared. The generator and the specification are two statements about
    one artefact and this is what holds them together — the same discipline
    `app/verify/register.py` applies to its own notes and counts.
    """
    product = reference_assembly() if structure is None else structure
    occurrences = list(product.occurrences())
    uses: dict[str, int] = {}
    for occurrence in occurrences:
        uses[occurrence.component] = uses.get(occurrence.component, 0) + 1
    instanced = sum(count for count in uses.values() if count > 1)
    return ReferenceProperties(
        occurrences=len(occurrences),
        distinct_components=len(uses),
        instanced_occurrences=instanced,
        depth=max((occurrence.depth for occurrence in occurrences), default=0),
        digest=product.digest(),
    )


def component_size_mm(index: int) -> tuple[float, float, float]:
    """The box a replicated part is drawn as, varying with its index.

    Varied so the scene's triangle budget is not one part's count times 2,000, and bounded
    so no part is so large it dominates the view or so small it falls below a display
    level's deflection. The spread is deterministic arithmetic for `reference_assembly`'s
    reason: a random size would make two runs incomparable.

    **Not used by the structure itself** — the structure is a graph of names and frames,
    and nothing in `app/render/` builds geometry. It is here because the triangle rows of
    §4's table are measured by tessellating parts of these sizes, and the sizes belong
    beside the assembly they describe rather than inside the test that reads them.
    """
    width = 12.0 + 4.0 * float(index % 11)
    depth = 10.0 + 3.0 * float((index // 11) % 9)
    height = 8.0 + 5.0 * float((index // 3) % 7)
    return (width, depth, height)


SIZES: Final[Mapping[str, tuple[float, float, float]]] = {
    _part_name(index): component_size_mm(index) for index in range(PART_COMPONENTS)
}


__all__ = [
    "DISTINCT_COMPONENTS",
    "MIN_DEPTH",
    "MIN_INSTANCED_FRACTION",
    "OCCURRENCES",
    "ROOT",
    "SIZES",
    "ReferenceProperties",
    "component_size_mm",
    "properties",
    "reference_assembly",
]
