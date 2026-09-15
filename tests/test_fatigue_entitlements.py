"""Every document `app/fatigue/` takes from is in the entitlement register (E21.5).

Offline. **Written on Linux on 2026-09-15 and not run there as pytest** (the user's rule). The URL
scan below was checked by a one-off grep: the three URLs cited in `app/fatigue/` are
`eurocode3.py`'s, `hotspot.py`'s and `notch.py`'s.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.fatigue.entitlements import REGISTER, TRIPS_9_2, Document, Holding, Taken, document

ROOT = Path(__file__).resolve().parents[1]
URL = re.compile(r"https://[^\s\"')]+")


def _cited_urls() -> dict[str, set[str]]:
    """URL -> modules citing it, over every module of app/fatigue except the register itself."""
    cited: dict[str, set[str]] = {}
    for path in sorted((ROOT / "app" / "fatigue").glob("*.py")):
        if path.name == "entitlements.py":
            continue
        # adjacent string literals split a URL across lines; join them before scanning
        text = re.sub(r'"\s*\n\s*"', "", path.read_text(encoding="utf-8"))
        for url in URL.findall(text):
            cited.setdefault(url, set()).add(f"app/fatigue/{path.name}")
    return cited


class TestEveryCitedDocumentIsRegistered:
    def test_every_url_a_fatigue_module_cites_is_a_held_document(self) -> None:
        held = {entry.read_from: entry for entry in REGISTER if entry.holding is not Holding.NOT_HELD}
        cited = _cited_urls()
        assert cited, "the scan found no citations at all, so it is not reading the modules"
        for url, modules in cited.items():
            assert url in held, f"{sorted(modules)} cite {url}, which the register does not carry"
            assert modules <= set(held[url].used_by), (url, modules, held[url].used_by)

    def test_every_module_the_register_names_exists_and_cites_its_document(self) -> None:
        cited = _cited_urls()
        for entry in REGISTER:
            for module in entry.used_by:
                assert (ROOT / module).is_file(), module
            if entry.used_by:
                citing = cited.get(entry.read_from, set())
                assert citing, f"{entry.key} lists users but no module cites {entry.read_from}"

    def test_bs_7608_and_the_fkm_static_guideline_are_not_held_and_not_named(self) -> None:
        for key in ("fkm-7-en", "bs-7608"):
            assert document(key).holding is Holding.NOT_HELD
            assert document(key).taken is Taken.NOTHING
        sources = "\n".join(
            p.read_text(encoding="utf-8")
            for p in (ROOT / "app" / "fatigue").glob("*.py")
            if p.name != "entitlements.py"
        )
        assert "Analytical Strength Assessment" not in sources
        assert "Rechnerischer Festigkeitsnachweis" not in sources

    def test_every_module_naming_fkm_is_listed_as_taking_it_through_pylife(self) -> None:
        fkm = document("fkm-via-pylife")
        assert fkm.taken is Taken.THROUGH_PYLIFE
        naming = {
            f"app/fatigue/{p.name}"
            for p in (ROOT / "app" / "fatigue").glob("*.py")
            if p.name != "entitlements.py" and "FKM" in p.read_text(encoding="utf-8")
        }
        # factors.py names FKM only as one of the charts a reader might be using, and takes nothing
        assert naming - {"app/fatigue/factors.py"} <= set(fkm.used_by)


class TestTheRegisterRefusesAnUnsourcedEntry:
    def _entry(self, **overrides) -> Document:  # type: ignore[no-untyped-def]
        fields = dict(
            key="x",
            title="t",
            publisher="p",
            holding=Holding.THIRD_PARTY_COPY,
            taken=Taken.TABLES,
            read_from="https://example.org/x.pdf",
            read_on="2026-09-15",
            price="Not read.",
            terms="Not read.",
            used_by=("app/fatigue/eurocode3.py",),
        )
        fields.update(overrides)
        return Document(**fields)  # type: ignore[arg-type]

    def test_a_held_document_without_where_it_was_read(self) -> None:
        with pytest.raises(ValueError, match="where and when"):
            self._entry(read_on="")

    def test_taking_from_a_document_that_is_not_held(self) -> None:
        with pytest.raises(ValueError, match="not held"):
            self._entry(holding=Holding.NOT_HELD)

    def test_a_method_through_pylife_needs_no_copy(self) -> None:
        entry = self._entry(holding=Holding.NOT_HELD, taken=Taken.THROUGH_PYLIFE, read_from="", read_on="")
        assert entry.taken is Taken.THROUGH_PYLIFE

    def test_users_without_anything_taken(self) -> None:
        with pytest.raises(ValueError, match="exactly when"):
            self._entry(taken=Taken.NOTHING)


def test_the_principle_is_quoted_as_the_wto_prints_it() -> None:
    assert TRIPS_9_2.startswith("Copyright protection shall extend to expressions")
    assert TRIPS_9_2.endswith("mathematical concepts as such.")
