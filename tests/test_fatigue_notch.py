"""What a notch costs, and the hot-spot stress at a weld toe (master plan E8.5).

Three things, one file, because they are one capability: how a local stress is
made fit for an S-N curve.

* `app.fatigue.hotspot` — IIW surface extrapolation. The weights are exact
  Lagrange weights, held here to the coefficients IIW prints at the page's own
  precision; a linear field is extrapolated exactly; every refusal is named.
* `app.fatigue.notch` — Neuber's technical factor (NACA TN 2805 formula (1))
  checked against hand-worked values, and the extended Neuber rule through
  pyLife checked against the equation it solves and its classical limit.
* `app.fatigue.backend.PyLifeBackend.extended_neuber` — the root check.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction
that the Windows machine runs the tests.
"""

from __future__ import annotations

import math

import pytest

from app.fatigue.backend import FatigueBackend, available
from app.fatigue.errors import FatigueError
from app.fatigue.history import LoadHistory, SignConvention, StressBasis
from app.fatigue.hotspot import (
    IIW_SOURCE,
    POSITION_ROUND_OFF,
    RULES,
    HotSpotType,
    Method,
    Reading,
    extrapolate,
)
from app.fatigue.notch import (
    NACA_TN_2805,
    NEUBER_ACCURACY,
    Branch,
    CyclicCurve,
    LimitLoadFactor,
    NeuberConstant,
    first_loading,
    hysteresis,
    neuber_concentration,
    neuber_sensitivity,
)

SOURCE = "test fixture, not a real material"

needs_pylife = pytest.mark.skipif(
    not available(), reason="pyLife is not installed; the notch rule federates to it"
)

#: The probe curve the notch module's docstring quotes its K_p figures on. A
#: fixture, not a material anybody should design with.
PROBE = CyclicCurve(
    youngs_modulus_mpa=206_000.0,
    strength_coefficient_mpa=1184.0,
    hardening_exponent=0.187,
    source=SOURCE,
)


def _reading(position_mm: float, values: tuple[float, ...], **overrides: object) -> Reading:
    kwargs: dict[str, object] = {
        "name": f"surface stress at {position_mm:g} mm",
        "values_mpa": values,
        "basis": StressBasis.NOTCH_ROOT,
        "sign": SignConvention.SIGNED,
        "source": f"model path, node at {position_mm:g} mm",
    }
    kwargs.update(overrides)
    return Reading(position_mm=position_mm, history=LoadHistory(**kwargs))  # type: ignore[arg-type]


def _linear_field(rule_method: Method, thickness: float | None, slope: float, toe: float) -> list[Reading]:
    """Readings from σ(x) = toe + slope·x at the rule's positions, three instants."""
    positions = RULES[rule_method].positions_mm(thickness)
    return [
        _reading(x, (toe + slope * x, -(toe + slope * x), 0.5 * (toe + slope * x)))
        for x in positions
    ]


# -- hot-spot extrapolation ----------------------------------------------------


class TestTheRulesAreThePrintedOnes:
    @pytest.mark.parametrize("method", list(Method))
    def test_the_exact_weights_round_to_the_printed_coefficients(self, method: Method) -> None:
        """IIW prints two decimals for type a and whole numbers or one decimal
        for type b; the exact weights must agree at that precision and no finer."""
        rule = RULES[method]
        for exact, printed in zip(rule.weights, rule.printed_coefficients, strict=True):
            assert exact == pytest.approx(printed, abs=0.005 + 1e-12)

    @pytest.mark.parametrize("method", list(Method))
    def test_the_weights_sum_to_one_so_a_uniform_stress_is_returned_unchanged(
        self, method: Method
    ) -> None:
        assert math.fsum(RULES[method].weights) == pytest.approx(1.0, abs=1e-12)

    def test_the_linear_fine_mesh_weights_are_five_thirds_and_minus_two_thirds(self) -> None:
        assert RULES[Method.FINE_LINEAR].weights == pytest.approx((5 / 3, -2 / 3))

    def test_each_rule_names_its_equation_and_page(self) -> None:
        expected = {
            Method.FINE_LINEAR: ("(2.7)", 24, HotSpotType.A),
            Method.FINE_QUADRATIC: ("(2.8)", 24, HotSpotType.A),
            Method.COARSE_LINEAR: ("(2.9)", 24, HotSpotType.A),
            Method.EDGE_FINE_QUADRATIC: ("(2.10)", 25, HotSpotType.B),
            Method.EDGE_COARSE_LINEAR: ("(2.11)", 25, HotSpotType.B),
        }
        for method, (equation, page, kind) in expected.items():
            rule = RULES[method]
            assert (rule.equation, rule.page, rule.hot_spot_type) == (equation, page, kind)

    def test_a_type_a_rule_scales_with_thickness_and_refuses_to_go_without_it(self) -> None:
        rule = RULES[Method.FINE_QUADRATIC]
        assert rule.positions_mm(20.0) == pytest.approx((8.0, 18.0, 28.0))
        with pytest.raises(ValueError, match="plate_thickness_mm"):
            rule.positions_mm(None)
        with pytest.raises(ValueError, match="positive and finite"):
            rule.positions_mm(0.0)

    def test_a_type_b_rule_refuses_a_thickness_it_would_ignore(self) -> None:
        rule = RULES[Method.EDGE_COARSE_LINEAR]
        assert rule.positions_mm(None) == (5.0, 15.0)
        with pytest.raises(ValueError, match="not dependent on plate thickness"):
            rule.positions_mm(12.0)


class TestExtrapolationIsExactOnTheFieldItAssumes:
    @pytest.mark.parametrize(
        ("method", "thickness"),
        [
            (Method.FINE_LINEAR, 16.0),
            (Method.FINE_QUADRATIC, 16.0),
            (Method.COARSE_LINEAR, 16.0),
            (Method.EDGE_FINE_QUADRATIC, None),
            (Method.EDGE_COARSE_LINEAR, None),
        ],
    )
    def test_a_linear_stress_field_extrapolates_to_its_value_at_the_toe(
        self, method: Method, thickness: float | None
    ) -> None:
        """The printed 1.67/−0.67 pair would miss by 0.002·slope·t here; the
        exact weights do not miss at all."""
        toe, slope = 240.0, -3.5
        result = extrapolate(
            method,
            _linear_field(method, thickness, slope, toe),
            plate_thickness_mm=thickness,
            name="toe",
            location="stiffener end, test plate",
        )
        assert result.history.values_mpa == pytest.approx((toe, -toe, 0.5 * toe), abs=1e-9)

    def test_the_quadratic_rule_is_exact_on_a_quadratic_field(self) -> None:
        t = 10.0
        positions = RULES[Method.FINE_QUADRATIC].positions_mm(t)

        def field(x: float) -> float:
            return 300.0 - 12.0 * x + 0.4 * x * x

        readings = [_reading(x, (field(x), 0.0, -field(x))) for x in positions]
        result = extrapolate(
            Method.FINE_QUADRATIC, readings, plate_thickness_mm=t, name="toe", location="gusset"
        )
        assert result.history.values_mpa == pytest.approx((300.0, 0.0, -300.0), abs=1e-9)

    def test_readings_may_arrive_in_any_order(self) -> None:
        readings = _linear_field(Method.EDGE_FINE_QUADRATIC, None, 2.0, 100.0)
        forward = extrapolate(Method.EDGE_FINE_QUADRATIC, readings, name="toe", location="edge")
        backward = extrapolate(
            Method.EDGE_FINE_QUADRATIC, list(reversed(readings)), name="toe", location="edge"
        )
        assert forward.history.values_mpa == backward.history.values_mpa

    def test_the_result_is_a_signed_hot_spot_history_that_says_what_it_left_out(self) -> None:
        result = extrapolate(
            Method.FINE_LINEAR,
            _linear_field(Method.FINE_LINEAR, 12.0, 1.0, 50.0),
            plate_thickness_mm=12.0,
            name="toe",
            location="cover plate end",
        )
        history = result.history
        assert history.basis is StressBasis.HOT_SPOT
        assert history.sign is SignConvention.SIGNED
        assert IIW_SOURCE in history.source
        assert "cover plate end" in history.source
        assert "thickness correction" in history.source
        assert "misalignment" in history.source
        assert "model path" in history.source
        assert result.positions_mm == pytest.approx((4.8, 12.0))


class TestExtrapolationRefusesWhatItCannotMean:
    def test_the_wrong_number_of_readings_is_refused(self) -> None:
        readings = _linear_field(Method.FINE_LINEAR, 10.0, 1.0, 10.0)[:1]
        with pytest.raises(ValueError, match="2 reference points"):
            extrapolate(
                Method.FINE_LINEAR, readings, plate_thickness_mm=10.0, name="t", location="x"
            )

    def test_a_reading_off_its_reference_point_is_refused(self) -> None:
        readings = [_reading(4.1, (1.0, 2.0, 3.0)), _reading(10.0, (1.0, 2.0, 3.0))]
        with pytest.raises(ValueError, match="Interpolate the stress"):
            extrapolate(
                Method.FINE_LINEAR, readings, plate_thickness_mm=10.0, name="t", location="x"
            )

    def test_round_off_on_a_position_is_accepted(self) -> None:
        nudge = 1.0 + 0.5 * POSITION_ROUND_OFF
        readings = [_reading(4.0 * nudge, (1.0, 2.0, 3.0)), _reading(10.0, (1.0, 2.0, 3.0))]
        extrapolate(Method.FINE_LINEAR, readings, plate_thickness_mm=10.0, name="t", location="x")

    def test_an_unsigned_reading_is_refused(self) -> None:
        readings = [
            _reading(4.0, (1.0, 2.0, 3.0), sign=SignConvention.UNSIGNED),
            _reading(10.0, (1.0, 2.0, 3.0)),
        ]
        with pytest.raises(ValueError, match="unsigned"):
            extrapolate(
                Method.FINE_LINEAR, readings, plate_thickness_mm=10.0, name="t", location="x"
            )

    @pytest.mark.parametrize(
        ("basis", "words"),
        [(StressBasis.HOT_SPOT, "already a hot-spot"), (StressBasis.NOMINAL, "nominal stress")],
    )
    def test_a_reading_that_is_not_a_local_surface_stress_is_refused(
        self, basis: StressBasis, words: str
    ) -> None:
        readings = [_reading(4.0, (1.0, 2.0, 3.0), basis=basis), _reading(10.0, (1.0, 2.0, 3.0))]
        with pytest.raises(ValueError, match=words):
            extrapolate(
                Method.FINE_LINEAR, readings, plate_thickness_mm=10.0, name="t", location="x"
            )

    def test_readings_sampled_at_different_instants_are_refused(self) -> None:
        readings = [_reading(4.0, (1.0, 2.0, 3.0)), _reading(10.0, (1.0, 2.0, 3.0, 4.0))]
        with pytest.raises(ValueError, match="same instants"):
            extrapolate(
                Method.FINE_LINEAR, readings, plate_thickness_mm=10.0, name="t", location="x"
            )

    def test_a_toe_with_no_location_is_refused(self) -> None:
        readings = _linear_field(Method.FINE_LINEAR, 10.0, 1.0, 10.0)
        with pytest.raises(ValueError, match="source"):
            extrapolate(
                Method.FINE_LINEAR, readings, plate_thickness_mm=10.0, name="t", location="  "
            )


# -- Neuber's technical factor -------------------------------------------------


class TestNeubersTechnicalFactor:
    def test_parallel_flanks_with_a_equal_to_a_quarter_of_r_give_two_thirds(self) -> None:
        """Worked by hand: q = 1/(1 + √0.25) = 1/1.5."""
        constant = NeuberConstant(length_mm=0.25, source=SOURCE)
        assert neuber_sensitivity(1.0, constant, flank_angle_deg=0.0) == pytest.approx(2 / 3)

    def test_a_ninety_degree_notch_doubles_the_root_term(self) -> None:
        """π/(π − π/2) = 2, so q = 1/(1 + 2·0.5) = 1/2."""
        constant = NeuberConstant(length_mm=0.25, source=SOURCE)
        assert neuber_sensitivity(1.0, constant, flank_angle_deg=90.0) == pytest.approx(0.5)

    def test_a_larger_radius_is_more_notch_sensitive(self) -> None:
        constant = NeuberConstant(length_mm=0.1, source=SOURCE)
        small = neuber_sensitivity(0.5, constant, flank_angle_deg=0.0)
        large = neuber_sensitivity(5.0, constant, flank_angle_deg=0.0)
        assert 0.0 < small < large < 1.0

    def test_the_concentration_applies_kf_equal_to_neubers_kn(self) -> None:
        constant = NeuberConstant(length_mm=0.25, source="read off TN 2805 Fig. 3 by the engineer")
        concentration = neuber_concentration(
            kt=2.5,
            kt_source="Peterson chart, shoulder fillet",
            radius_mm=1.0,
            constant=constant,
            flank_angle_deg=0.0,
            location="shaft shoulder",
        )
        assert concentration.kf == pytest.approx(1.0 + (2.5 - 1.0) / 1.5)
        assert not concentration.assumed_full_sensitivity
        for text in (NACA_TN_2805, NEUBER_ACCURACY, "Peterson chart", "read off TN 2805"):
            assert text in concentration.source

    @pytest.mark.parametrize("radius", [0.0, -1.0, math.inf, math.nan])
    def test_a_radius_that_is_not_a_positive_length_is_refused(self, radius: float) -> None:
        constant = NeuberConstant(length_mm=0.1, source=SOURCE)
        with pytest.raises(ValueError, match="radius"):
            neuber_sensitivity(radius, constant, flank_angle_deg=0.0)

    @pytest.mark.parametrize("angle", [-1.0, 180.0, 200.0])
    def test_a_flank_angle_outside_zero_to_one_eighty_is_refused(self, angle: float) -> None:
        constant = NeuberConstant(length_mm=0.1, source=SOURCE)
        with pytest.raises(ValueError, match="flank angle"):
            neuber_sensitivity(1.0, constant, flank_angle_deg=angle)

    @pytest.mark.parametrize("length", [0.0, -0.1, math.inf])
    def test_a_constant_that_is_not_a_positive_length_is_refused(self, length: float) -> None:
        with pytest.raises(ValueError, match="positive length"):
            NeuberConstant(length_mm=length, source=SOURCE)

    def test_a_constant_with_no_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source"):
            NeuberConstant(length_mm=0.1, source=" ")


# -- the extended Neuber rule ----------------------------------------------------


class TestTheInputsCarryTheirOwnLimits:
    def test_a_limit_load_factor_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            LimitLoadFactor(value=0.99, source=SOURCE)

    @pytest.mark.parametrize(
        "field", ["youngs_modulus_mpa", "strength_coefficient_mpa", "hardening_exponent"]
    )
    def test_a_curve_constant_that_is_not_positive_is_refused(self, field: str) -> None:
        kwargs: dict[str, object] = {
            "youngs_modulus_mpa": 206_000.0,
            "strength_coefficient_mpa": 1184.0,
            "hardening_exponent": 0.187,
            "source": SOURCE,
        }
        kwargs[field] = 0.0
        with pytest.raises(ValueError, match="positive and finite"):
            CyclicCurve(**kwargs)  # type: ignore[arg-type]

    def test_a_negative_range_is_refused_before_the_solver_is_asked(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            hysteresis(-10.0, PROBE, LimitLoadFactor(value=3.0, source=SOURCE))

    def test_a_non_finite_elastic_stress_is_refused(self) -> None:
        with pytest.raises(ValueError, match="finite"):
            first_loading(math.nan, PROBE, LimitLoadFactor(value=3.0, source=SOURCE))


def _ramberg_osgood(stress: float, curve: CyclicCurve) -> float:
    return stress / curve.youngs_modulus_mpa + (
        stress / curve.strength_coefficient_mpa
    ) ** (1.0 / curve.hardening_exponent)


@needs_pylife
class TestTheExtendedNeuberRuleThroughPyLife:
    def test_the_answer_lies_on_the_cyclic_curve(self) -> None:
        result = first_loading(853.0, PROBE, LimitLoadFactor(value=3.0, source=SOURCE))
        assert result.branch is Branch.FIRST_LOADING
        assert result.strain == pytest.approx(_ramberg_osgood(result.stress_mpa, PROBE), rel=1e-9)

    def test_plasticity_lowers_the_stress_and_raises_the_strain(self) -> None:
        elastic = 853.0
        result = first_loading(elastic, PROBE, LimitLoadFactor(value=3.0, source=SOURCE))
        assert result.stress_mpa < elastic
        assert result.strain > elastic / PROBE.youngs_modulus_mpa

    def test_it_approaches_classical_neuber_as_the_limit_load_factor_grows(self) -> None:
        """σ·ε → L²/E as K_p → ∞: e* at L/K_p is then elastic, so L·K_p·e* = L²/E."""
        elastic = 853.0
        result = first_loading(elastic, PROBE, LimitLoadFactor(value=1e6, source=SOURCE))
        assert result.stress_mpa * result.strain == pytest.approx(
            elastic**2 / PROBE.youngs_modulus_mpa, rel=1e-4
        )

    def test_the_docstrings_probe_figures_are_what_the_rule_gives(self) -> None:
        """notch.py quotes 450 MPa at K_p = 1000 and 477 MPa at K_p = 3 for 853 MPa."""
        loose = first_loading(853.0, PROBE, LimitLoadFactor(value=1000.0, source=SOURCE))
        tight = first_loading(853.0, PROBE, LimitLoadFactor(value=3.0, source=SOURCE))
        assert loose.stress_mpa == pytest.approx(450.0, abs=1.0)
        assert tight.stress_mpa == pytest.approx(477.0, abs=1.0)

    def test_the_rule_solves_its_own_equation(self) -> None:
        elastic, kp = 853.0, 3.0
        result = first_loading(elastic, PROBE, LimitLoadFactor(value=kp, source=SOURCE))
        wanted = elastic * kp * _ramberg_osgood(elastic / kp, PROBE)
        assert result.stress_mpa * result.strain == pytest.approx(wanted, rel=1e-6)

    def test_a_hysteresis_range_lies_on_the_doubled_curve(self) -> None:
        """Masing: Δε = Δσ/E + 2(Δσ/2K')^(1/n')."""
        result = hysteresis(1200.0, PROBE, LimitLoadFactor(value=3.0, source=SOURCE))
        stress = result.stress_mpa
        masing = stress / PROBE.youngs_modulus_mpa + 2.0 * (
            stress / (2.0 * PROBE.strength_coefficient_mpa)
        ) ** (1.0 / PROBE.hardening_exponent)
        assert result.branch is Branch.HYSTERESIS
        assert result.strain == pytest.approx(masing, rel=1e-9)

    def test_an_elastic_answer_is_returned_below_yield(self) -> None:
        """At a small stress the plastic term is negligible and σ ≈ L."""
        result = first_loading(5.0, PROBE, LimitLoadFactor(value=3.0, source=SOURCE))
        assert result.stress_mpa == pytest.approx(5.0, rel=1e-4)

    def test_the_source_names_the_rule_the_curve_and_the_factor(self) -> None:
        result = first_loading(
            853.0, PROBE, LimitLoadFactor(value=3.0, source="FKM Table, bending, rectangle")
        )
        assert "extended Neuber" in result.source
        assert SOURCE in result.source
        assert "FKM Table, bending, rectangle" in result.source
        assert "pylife" in result.source.lower() or "K_p = 3" in result.source


class _NotARoot(FatigueBackend):
    """A backend whose Neuber answer is wrong, to prove the check is not pyLife's."""

    name = "wrong"
    version = "0"

    def count(self, history):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def cycles_to_failure(self, curve, amplitude_mpa):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def damage(self, collective, curve):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def correct_mean_stress(self, collective, *, m, m2):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def at_failure_probability(self, curve, probability):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def extended_neuber(self, elastic_mpa, **kwargs):  # type: ignore[no-untyped-def]
        return elastic_mpa, elastic_mpa / kwargs["youngs_modulus_mpa"]


class TestTheBackendSeam:
    def test_an_injected_backend_is_the_one_asked(self) -> None:
        """The module federates through the seam rather than importing pyLife."""
        result = first_loading(
            100.0, PROBE, LimitLoadFactor(value=3.0, source=SOURCE), backend=_NotARoot()
        )
        assert result.stress_mpa == 100.0
        assert "wrong" in result.source or "0" in result.source

    @needs_pylife
    def test_pylifes_root_is_checked_against_the_product_it_must_satisfy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Break the returned strain and the backend must refuse, not report it."""
        from app.fatigue import backend as backend_module

        real = backend_module._ExtendedNeuber

        class Skewed(real):  # type: ignore[misc, valid-type]
            def strain(self, stress, *args, **kwargs):  # type: ignore[no-untyped-def]
                return 1.5 * super().strain(stress, *args, **kwargs)

        monkeypatch.setattr(backend_module, "_ExtendedNeuber", Skewed)
        with pytest.raises(FatigueError, match="not a root"):
            first_loading(853.0, PROBE, LimitLoadFactor(value=3.0, source=SOURCE))
