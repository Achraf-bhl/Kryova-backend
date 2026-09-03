"""A small starter material library.

Values are typical handbook figures for the common alloy/polymer tempers a
small team actually prototypes in. They are a sane default, not a substitute
for a supplier datasheet, and the API accepts a custom Material either way.
"""

from app.solve.types import Material

MATERIALS: dict[str, Material] = {
    material.name: material
    for material in [
        Material(
            name="aluminium-6061-t6",
            youngs_modulus_mpa=68_900,
            poissons_ratio=0.33,
            yield_strength_mpa=276,
            density_kg_m3=2700,
            thermal_expansion_per_k=23.6e-6,
        ),
        Material(
            name="aluminium-7075-t6",
            youngs_modulus_mpa=71_700,
            poissons_ratio=0.33,
            yield_strength_mpa=503,
            density_kg_m3=2810,
            thermal_expansion_per_k=23.4e-6,
        ),
        Material(
            name="steel-1018",
            youngs_modulus_mpa=205_000,
            poissons_ratio=0.29,
            yield_strength_mpa=370,
            density_kg_m3=7870,
            thermal_expansion_per_k=11.7e-6,
        ),
        Material(
            name="stainless-304",
            youngs_modulus_mpa=193_000,
            poissons_ratio=0.29,
            yield_strength_mpa=215,
            density_kg_m3=8000,
            thermal_expansion_per_k=17.3e-6,
        ),
        Material(
            name="titanium-ti6al4v",
            youngs_modulus_mpa=113_800,
            poissons_ratio=0.342,
            yield_strength_mpa=880,
            density_kg_m3=4430,
            thermal_expansion_per_k=8.6e-6,
        ),
        Material(
            name="abs",
            youngs_modulus_mpa=2_200,
            poissons_ratio=0.35,
            yield_strength_mpa=40,
            density_kg_m3=1040,
            thermal_expansion_per_k=90.0e-6,
        ),
        Material(
            name="pla",
            youngs_modulus_mpa=3_500,
            poissons_ratio=0.36,
            yield_strength_mpa=50,
            density_kg_m3=1240,
            thermal_expansion_per_k=68.0e-6,
        ),
        Material(
            name="nylon-pa12",
            youngs_modulus_mpa=1_700,
            poissons_ratio=0.39,
            yield_strength_mpa=48,
            density_kg_m3=1010,
            thermal_expansion_per_k=110.0e-6,
        ),
    ]
}
