from app.api.rate_limit import RateLimiter
from app.geometry.formats import detect_format, rejection_reason


class TestGeometryFormats:
    def test_neutral_exchange_formats_are_detected(self) -> None:
        assert detect_format("bracket.STEP") == "step"
        assert detect_format("bracket.stp") == "step"
        assert detect_format("bracket.igs") == "iges"
        assert detect_format("bracket.stl") == "stl"

    def test_catia_native_formats_are_not_claimed_as_supported(self) -> None:
        """OpenCASCADE cannot read CATIA V5 without the commercial Datakit plugin.

        Advertising these would move the failure from a clear upload rejection to
        an opaque mesher crash minutes later.
        """
        for name in ("part.CATPart", "asm.CATProduct", "view.cgr"):
            assert detect_format(name) is None

    def test_native_cad_rejection_explains_the_conversion(self) -> None:
        assert "STEP" in rejection_reason("part.CATPart")
        assert "STEP" in rejection_reason("part.sldprt")

    def test_unknown_extension_falls_back_to_listing_what_is_supported(self) -> None:
        reason = rejection_reason("notes.txt")
        assert "step" in reason and "stl" in reason


class TestRateLimiterEviction:
    def test_allows_up_to_the_limit_then_blocks(self) -> None:
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        assert [limiter.check("ip") for _ in range(4)] == [True, True, True, False]

    def test_distinct_keys_do_not_share_a_budget(self) -> None:
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.check("a") and limiter.check("b")

    def test_expired_keys_are_evicted_rather_than_accumulating(self) -> None:
        """One request each from many addresses must not grow the map forever.

        The map is bounded by the sweep threshold, not by how many distinct
        addresses have ever been seen -- 50 callers must not mean 50 entries.
        """
        limiter = RateLimiter(max_requests=5, window_seconds=0, sweep_threshold=10)
        for i in range(50):
            limiter.check(f"ip-{i}")
        assert len(limiter._requests) <= 10

    def test_active_keys_survive_a_sweep(self) -> None:
        limiter = RateLimiter(max_requests=5, window_seconds=60, sweep_threshold=2)
        limiter.check("keep-me")
        for i in range(5):
            limiter.check(f"other-{i}")
        assert "keep-me" in limiter._requests
