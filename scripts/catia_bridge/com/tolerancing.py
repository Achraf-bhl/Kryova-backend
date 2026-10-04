"""Functional Tolerancing & Annotation over COM — datums and feature control frames.

Every call here was measured on a French V5-R33 seat on 2026-09-19 (THE QUEUE E7). The four
things that are not guessable, and each of which answers `Le type ne correspond pas` or
`… a échoué` rather than anything readable:

1. **The whole library is invisible to late binding.** `part.AnnotationSets` is
   `<COMObject <unknown>>` until `CATTPSInterfaces` is generated, which reads exactly like an
   unlicensed seat. `_ensure_tps()` generates **only** that library: generating
   `MecModInterfaces` alongside it types `part.ShapeFactory` to the base `Factory` and removes
   `AddNewPad`, breaking every geometry tool in the same process (CLAUDE.md item 3c).
2. **`iSurf` is a `UserSurface`**, produced by `part.UserSurfaces.Generate(reference)`. A face
   reference is refused, and so is a plane one. `part.UserSurfaces.Count` *raises* on an empty
   collection, so never probe with it.
3. **An annotation needs a view.** `AnnotationSet.ActiveView` raises until
   `TPSViewFactory.CreateView(planeReference, 0)` has made one.
4. **A datum reference frame is created empty and refuses every tolerance in that state.**
   `frame.SetFrame(label, "", "")` — three strings — fills it.

The characteristic index is per family and lives in `app/catia/ops/tolerancing.py`, which is
the one place that mapping exists.
"""

from __future__ import annotations

from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext

#: Set once per process by `_ensure_tps`. Generating a type library is global and on disk, so
#: doing it repeatedly is wasted work rather than harmless.
_TPS_READY = False


def _ensure_tps() -> None:  # pragma: no cover - Windows only
    """Generate `CATTPSInterfaces`, and nothing else.

    Not `EnsureDispatch` on the application: that writes an early-binding wrapper for INFITF
    which removes `.Part` from `Documents.Add` in *every* win32com process on the machine,
    the daemon included.
    """
    global _TPS_READY
    if _TPS_READY:
        return
    from app.catia.ops.tolerancing import TYPE_LIBRARY

    try:
        from win32com.client import gencache

        gencache.EnsureModule(TYPE_LIBRARY, 0, 0, 0)
    except Exception as error:  # noqa: BLE001
        raise CatiaOperationError(
            "This workstation could not load CATIA's tolerancing type library "
            f"({error}). Functional Tolerancing & Annotation may not be installed on this "
            "seat; without the library the whole workbench is invisible to automation."
        ) from error
    _TPS_READY = True


class TolerancingMixin:
    """Datums and feature control frames on the solid, not on a drawing."""

    # -- helpers -------------------------------------------------------------

    def _annotation_set(self: ComContext):  # pragma: no cover - Windows only
        """The part's annotation set with an active view, created if absent.

        Idempotent on purpose: an operation that needed the caller to run a `start` first
        would fail on every part nobody had prepared, and there is nothing to decide here —
        a part either has a set or wants one.
        """
        from app.catia.ops.tolerancing import DEFAULT_STANDARD

        _ensure_tps()
        part = self._part()
        try:
            sets = part.AnnotationSets
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"This part exposes no annotation sets ({error}). Functional Tolerancing & "
                "Annotation is not available on this seat."
            ) from error

        annotation_set = None
        try:
            if sets.Count > 0:
                annotation_set = sets.Item(1)
        except Exception:  # noqa: BLE001 - an empty collection raises rather than answering 0
            annotation_set = None
        if annotation_set is None:
            try:
                annotation_set = sets.Add(DEFAULT_STANDARD)
            except Exception as error:  # noqa: BLE001
                raise CatiaOperationError(
                    f"CATIA refused to create a {DEFAULT_STANDARD} annotation set ({error})."
                ) from error

        # A view, or every annotation below is refused. `ActiveView` raises when there is
        # none, so the absence is caught rather than tested for.
        try:
            annotation_set.ActiveView
        except Exception:  # noqa: BLE001
            plane = part.CreateReferenceFromObject(part.OriginElements.PlaneXY)
            annotation_set.TPSViewFactory.CreateView(plane, 0)
        return annotation_set

    def _user_surface(self: ComContext, face: str):  # pragma: no cover - Windows only
        """The `UserSurface` a tolerancing call needs for `face`.

        `resolve_face` gives a `Reference`, which every `iSurf` parameter refuses — the
        conversion is the step that is impossible to guess from the error.
        """
        reference = self._face_reference(face)
        try:
            return self._part().UserSurfaces.Generate(reference)
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not take a tolerancing surface from {face!r} ({error}). A datum "
                "or a frame is placed on one face; a whole body or a sketch is not one."
            ) from error

    # -- operations ----------------------------------------------------------

    def tolerance_datum(self: ComContext, *, face: str) -> dict[str, Any]:  # pragma: no cover
        """Create a datum on `face` and report the letter CATIA assigned."""
        annotation_set = self._annotation_set()
        surface = self._user_surface(face)
        try:
            datum = annotation_set.AnnotationFactory.CreateDatum(surface)
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused a datum on {face!r} ({error})."
            ) from error
        try:
            label = str(datum.DatumSimple().Label)
        except Exception:  # noqa: BLE001 - the datum exists either way; only the letter is lost
            label = ""
        self._part().Update()
        return {
            "face": face,
            "label": label,
            "name": str(datum.Name),
            # Said plainly because the letter is what a frame references, and a caller that
            # cannot read it back cannot build one.
            "note": (
                "CATIA assigns the letter; pass it to catia_tolerance_frame's `datums`."
                if label
                else "CATIA created the datum but did not report a letter for it."
            ),
        }

    def tolerance_frame(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        face: str,
        characteristic: str,
        datums: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a feature control frame on `face`.

        With `datums` the frame is built through a datum reference frame, which **must** be
        filled before use — an empty one refuses every characteristic, which is what made this
        look unimplementable for a day.
        """
        from app.catia.ops.tolerancing import characteristic_index

        letters = [str(letter).strip() for letter in (datums or []) if str(letter).strip()]
        try:
            index = characteristic_index(characteristic, with_datums=bool(letters))
        except ValueError as error:
            raise CatiaOperationError(str(error)) from error
        if len(letters) > 3:
            raise CatiaOperationError(
                f"A feature control frame references at most three datums; {len(letters)} were "
                "given. Primary, secondary, tertiary is the whole of it."
            )

        annotation_set = self._annotation_set()
        factory = annotation_set.AnnotationFactory
        surface = self._user_surface(face)

        try:
            if letters:
                frame = factory.CreateDatumReferenceFrame()
                boxes = (letters + ["", "", ""])[:3]
                frame.ReferenceFrame().SetFrame(boxes[0], boxes[1], boxes[2])
                annotation = factory.CreateToleranceWithDRF(index, surface, frame)
            else:
                annotation = factory.CreateToleranceWithoutDRF(index, surface)
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused a {characteristic} frame on {face!r} ({error}). A datum letter "
                "that no datum carries is the usual cause — create the datums first with "
                "catia_tolerance_datum and pass the letters it reports."
            ) from error

        self._part().Update()
        return {
            "face": face,
            "characteristic": characteristic,
            "datums": letters,
            "name": str(annotation.Name),
            # The value is CATIA's default and this bridge cannot set it. Saying so in the
            # result rather than only in the schema, because this is the field an engineer
            # would otherwise assume somebody had chosen.
            "value_set": False,
            "note": (
                "The frame carries CATIA's default tolerance value; setting the magnitude is "
                "not implemented, so edit it on the seat before the part is released."
            ),
        }

    def tolerance_list(self: ComContext) -> dict[str, Any]:  # pragma: no cover - Windows only
        """Every annotation on the part, with the datum letters among them."""
        _ensure_tps()
        part = self._part()
        try:
            sets = part.AnnotationSets
            count = sets.Count
        except Exception:  # noqa: BLE001
            return {"annotation_sets": 0, "annotations": [], "datums": []}

        annotations: list[dict[str, Any]] = []
        datums: list[str] = []
        for index in range(1, int(count) + 1):
            annotation_set = sets.Item(index)
            try:
                total = annotation_set.Annotations.Count
            except Exception:  # noqa: BLE001
                continue
            for position in range(1, int(total) + 1):
                annotation = annotation_set.Annotations.Item(position)
                entry: dict[str, Any] = {"name": str(annotation.Name)}
                try:
                    label = str(annotation.DatumSimple().Label)
                except Exception:  # noqa: BLE001 - only a datum answers this
                    label = ""
                if label:
                    entry["datum_label"] = label
                    datums.append(label)
                annotations.append(entry)
        return {
            "annotation_sets": int(count),
            "annotations": annotations,
            "datums": datums,
            # The names are the seat's language (`Rectitude`, `Localisation`), not this
            # product's vocabulary, so nothing downstream should match on them.
            "note": "Annotation names are in the seat's interface language.",
        }
