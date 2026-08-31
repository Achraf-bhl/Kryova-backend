"""The interface the mock and the real COM backend both implement.

One abstract base with one method per tool, so `session.py` never branches on
which backend it is talking to. That is what makes mock mode a genuine test of
the system rather than a separate code path: the frame handling, the schema
re-validation, the tier enforcement, the watchdog and the reconnect logic are
all the same objects either way, and only the leaves below differ.

Every method returns the plain data dictionary that becomes the `data` field of
a `result` frame. Raising `CatiaOperationError` becomes `{"ok": false, "error":
...}`; anything else raising is caught one level up and reported the same way,
so a COM exception cannot take the daemon down.
"""

from abc import ABC, abstractmethod
from typing import Any


class CatiaOperationError(RuntimeError):
    """The operation failed in a way worth telling the agent about verbatim."""


class CatiaBackend(ABC):
    """One method per tool, minus the ones the server answers itself."""

    #: Reported in the `hello` frame.
    catia_version: str = "unknown"
    is_mock: bool = False
    capabilities: tuple[str, ...] = ("part", "sketch", "measure", "export", "capture")

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Release whatever the backend holds. Called once on shutdown."""

    @abstractmethod
    def health(self) -> None:
        """Raise `CatiaOperationError` if CATIA is not usable right now.

        Called before every operation. On the real backend this is what turns a
        modal dialog or a closed CATIA into an immediate, explicit error instead
        of a call that blocks until the server's timeout fires.

        Must be free of side effects: it is issued on a short-lived watchdog
        thread, so anything it acquires dies with that thread. Repairs go in
        `reattach`.
        """

    def ensure_connected(self) -> None:
        """Make this thread's connection usable, reconnecting if stale. Optional.

        The default does nothing, which is right for any backend holding no
        connection at all -- the mock, for one. `CatiaCom` overrides it, because
        its COM handle points into a single CATIA process and does not survive
        that process being closed and reopened.

        Called on the operation thread, immediately before the operation, and
        never on the watchdog: a COM proxy belongs to the apartment of the
        thread that acquired it, so only this thread can repair its own.
        """

    # -- documents -----------------------------------------------------------

    @abstractmethod
    def new_part(self, *, name: str) -> dict[str, Any]: ...

    @abstractmethod
    def open_document(
        self,
        *,
        doc_name: str | None = None,
        remote_path: str | None = None,
        fallback_checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    # -- parameters ----------------------------------------------------------

    @abstractmethod
    def list_parameters(self) -> dict[str, Any]: ...

    @abstractmethod
    def set_parameter(self, *, name: str, value: float, unit: str) -> dict[str, Any]: ...

    # -- material ------------------------------------------------------------

    @abstractmethod
    def set_material(self, *, material: str, density_kg_m3: float) -> dict[str, Any]:
        """Record the part's material and apply it in CATIA where possible.

        `density_kg_m3` comes from Kryova's material library, not from the
        model and not from CATIA: it is what every reported mass is computed
        from, so it must not depend on whether this workstation happens to be
        licensed for the Material Library.
        """

    # -- sketches and features ----------------------------------------------

    @abstractmethod
    def sketch_rectangle(
        self, *, plane: str, width_mm: float, height_mm: float
    ) -> dict[str, Any]: ...

    @abstractmethod
    def sketch_circle(self, *, plane: str, diameter_mm: float) -> dict[str, Any]: ...

    @abstractmethod
    def pad(
        self,
        *,
        sketch: str,
        length_mm: float,
        symmetric: bool = False,
        reversed: bool = False,  # noqa: A002 - the protocol field is named this
    ) -> dict[str, Any]: ...

    @abstractmethod
    def pocket(
        self, *, sketch: str, depth_mm: float | None = None, through_all: bool = False
    ) -> dict[str, Any]: ...

    @abstractmethod
    def hole(
        self,
        *,
        face: str,
        position: str,
        diameter_mm: float,
        depth_mm: float | None = None,
        through_all: bool = True,
        inset_mm: float | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def fillet(
        self, *, radius_mm: float, feature: str | None = None, edges: str = "all"
    ) -> dict[str, Any]: ...

    @abstractmethod
    def chamfer(
        self,
        *,
        length_mm: float,
        angle_deg: float = 45.0,
        feature: str | None = None,
        edges: str = "all",
    ) -> dict[str, Any]: ...

    @abstractmethod
    def sketch_polygon(self, *, plane: str, sides: int, diameter_mm: float) -> dict[str, Any]: ...

    @abstractmethod
    def shaft(self, *, sketch: str, angle_deg: float = 360.0) -> dict[str, Any]: ...

    @abstractmethod
    def groove(self, *, sketch: str, angle_deg: float = 360.0) -> dict[str, Any]: ...

    @abstractmethod
    def mirror(self, *, plane: str) -> dict[str, Any]: ...

    @abstractmethod
    def sketch_revolve_profile(
        self,
        *,
        plane: str,
        outer_diameter_mm: float,
        length_mm: float,
        inner_diameter_mm: float | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def sketch_groove_profile(
        self,
        *,
        plane: str,
        shaft_diameter_mm: float,
        width_mm: float,
        depth_mm: float,
        distance_from_end_mm: float,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def pattern_rectangular(
        self,
        *,
        plane: str,
        count: int,
        spacing_mm: float,
        second_count: int = 1,
        second_spacing_mm: float | None = None,
        feature: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def pattern_circular(
        self,
        *,
        count: int,
        plane: str = "XY",
        total_angle_deg: float = 360.0,
        feature: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def shell(self, *, thickness_mm: float) -> dict[str, Any]: ...

    @abstractmethod
    def delete_feature(self, *, feature: str) -> dict[str, Any]: ...

    @abstractmethod
    def update(self) -> dict[str, Any]: ...

    # -- inspection ----------------------------------------------------------

    @abstractmethod
    def list_features(self) -> dict[str, Any]: ...

    @abstractmethod
    def measure(self) -> dict[str, Any]: ...

    @abstractmethod
    def capture_view(
        self, *, view: str = "iso", label: str = "", max_inline_bytes: int | None = None
    ) -> dict[str, Any]: ...

    # -- transfer and safety -------------------------------------------------

    @abstractmethod
    def export_step(
        self, *, note: str | None = None, max_inline_bytes: int | None = None
    ) -> dict[str, Any]: ...

    @abstractmethod
    def checkpoint(self, *, label: str, max_inline_bytes: int | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def restore(self, *, checkpoint: dict[str, Any]) -> dict[str, Any]: ...


#: Tool name -> backend method. Kept as data so `session.py` cannot reach a
#: method that is not on this list, whatever arrives on the wire.
TOOL_METHODS: dict[str, str] = {
    "catia_new_part": "new_part",
    "catia_open_document": "open_document",
    "catia_list_parameters": "list_parameters",
    "catia_set_parameter": "set_parameter",
    "catia_set_material": "set_material",
    "catia_sketch_rectangle": "sketch_rectangle",
    "catia_sketch_circle": "sketch_circle",
    "catia_pad": "pad",
    "catia_pocket": "pocket",
    "catia_hole": "hole",
    "catia_fillet": "fillet",
    "catia_chamfer": "chamfer",
    "catia_sketch_polygon": "sketch_polygon",
    "catia_shaft": "shaft",
    "catia_groove": "groove",
    "catia_mirror": "mirror",
    "catia_sketch_revolve_profile": "sketch_revolve_profile",
    "catia_sketch_groove_profile": "sketch_groove_profile",
    "catia_pattern_rectangular": "pattern_rectangular",
    "catia_pattern_circular": "pattern_circular",
    "catia_shell": "shell",
    "catia_delete_feature": "delete_feature",
    "catia_list_features": "list_features",
    "catia_measure": "measure",
    "catia_capture_view": "capture_view",
    "catia_export_step": "export_step",
    "catia_checkpoint": "checkpoint",
    "catia_restore": "restore",
    "catia_update": "update",
}
