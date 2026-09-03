"""Numeric ceilings for every tool parameter, in one place.

These are **typo guards, not physics**. Nothing in CATIA stops you modelling a
40-metre bracket; what stops a model is that a language model which means 12 mm
and emits 12000 should be refused in the schema rather than build a part the
size of a house and fail the mesher ten minutes later.

They live here rather than inline for one concrete reason: the same ceiling is
referenced by the server schema, the daemon's re-validation table and the tests
that assert the two agree. A literal repeated in three files is a literal that
will disagree with itself.

Units follow the project rule: mm-N-MPa, degrees for angles. Nothing converts.
"""

from typing import Final

#: The largest coordinate or length any tool accepts, in mm. Ten metres.
#: A part legitimately larger than this exists (a wing spar), and the answer
#: for it is a deliberate raise here after someone has looked at it, not an
#: open bound that lets every mistyped dimension through.
MAX_LENGTH_MM: Final = 10_000.0

#: Radii, diameters, fillets, chamfers: a bound an order of magnitude tighter
#: than a raw length, because these are always local features of a part.
MAX_FEATURE_MM: Final = 1_000.0

#: Coordinates may be negative, so they need a symmetric bound rather than the
#: `exclusiveMinimum: 0` a length carries.
MIN_COORD_MM: Final = -MAX_LENGTH_MM
MAX_COORD_MM: Final = MAX_LENGTH_MM

#: A full turn. Used wherever an angle sweeps rather than tilts.
MAX_ANGLE_DEG: Final = 360.0

#: A tilt, not a sweep: draft angles, chamfer angles, plane angles. 90° would
#: be degenerate for most of them, so the ceiling sits just under it.
MAX_TILT_DEG: Final = 89.0

#: Ceiling on repeat counts (pattern instances, polygon sides, spline points).
#: A 100-instance pattern is already a slow rebuild; 10 000 is a hang.
MAX_INSTANCES: Final = 100

#: Points a model may hand to one polyline or spline in a single call.
MAX_POINTS: Final = 50

#: Elements in one selection list, one constraint set, one dimension chain.
MAX_SELECTION: Final = 50

#: Characters in a feature, sketch, parameter or document name. CATIA itself
#: tolerates more; this is the length past which a name is certainly not a name.
MAX_NAME_CHARS: Final = 120

#: Characters in a free-text note, annotation or label.
MAX_TEXT_CHARS: Final = 500

#: Density bound for a material, kg/m³. Osmium is 22 590; the ceiling leaves
#: headroom for tungsten alloys without admitting a unit error of 1000×.
MAX_DENSITY_KG_M3: Final = 30_000.0

#: Thickness of a wall, shell, sheet or ply. Separate from MAX_FEATURE_MM
#: because a thickness of half a metre is a mistake in every workbench that
#: has the concept.
MAX_THICKNESS_MM: Final = 500.0

#: Scale and affinity ratios. A 100× scale is a unit error, not an intention.
MIN_RATIO: Final = 0.001
MAX_RATIO: Final = 100.0

#: Forces (N) and moments (N·mm) an FEA load may carry. Generous, because a
#: press fit legitimately reaches meganewtons, but not unbounded.
MAX_FORCE_N: Final = 1e9
MAX_MOMENT_N_MM: Final = 1e12

#: Pressure in MPa. 10 GPa is past any engineering pressure and well short of
#: the float range, so a misplaced decimal still lands inside and a unit
#: confusion (Pa for MPa) lands outside.
MAX_PRESSURE_MPA: Final = 10_000.0

#: Acceleration in mm/s². Standard gravity is 9 806.65 mm/s²; the ceiling
#: allows a 100 g shock load.
MAX_ACCELERATION_MM_S2: Final = 1e7

#: Angular velocity in rad/s, for centrifugal loads.
MAX_ANGULAR_VELOCITY_RAD_S: Final = 1e5

#: Temperature in °C, for thermal load cases.
MIN_TEMPERATURE_C: Final = -273.15
MAX_TEMPERATURE_C: Final = 5_000.0
