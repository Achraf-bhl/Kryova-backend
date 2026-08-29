from pathlib import Path

# Canonical format name -> accepted file extensions.
#
# Only formats the mesher can actually open belong here. gmsh reads these
# through its OpenCASCADE importer, which handles the neutral exchange formats
# below natively.
#
# CATIA's native formats (.CATPart/.CATProduct/.cgr) are deliberately absent.
# Stock OpenCASCADE cannot parse them -- CATIA V5 import requires the
# commercial Datakit plugin, which is not part of OCC and therefore not part of
# gmsh. Listing them here would only move the failure from a clear "unsupported
# format" rejection at upload to an opaque mesher crash minutes later. Users
# with CATIA data export to STEP, which CATIA writes natively.
GEOMETRY_FORMATS: dict[str, tuple[str, ...]] = {
    "step": ("step", "stp"),
    "iges": ("iges", "igs"),
    "stl": ("stl",),
}

# Extensions we recognise well enough to explain the rejection, rather than
# reporting the generic "unsupported format" that an unknown suffix gets.
UNSUPPORTED_FORMAT_HINTS: dict[str, str] = {
    "catpart": "CATIA V5 part files cannot be read directly. In CATIA, use "
    "File > Save As and choose STEP (.step), then upload that.",
    "catproduct": "CATIA V5 assembly files cannot be read directly. In CATIA, use "
    "File > Save As and choose STEP (.step), then upload that.",
    "cgr": "CATIA graphical representations (.cgr) contain display tessellation "
    "only, not the solid geometry a mesh needs. Export the source part as STEP.",
    "sldprt": "SolidWorks part files cannot be read directly. Export as STEP (.step).",
    "ipt": "Inventor part files cannot be read directly. Export as STEP (.step).",
    "3dm": "Rhino files cannot be read directly. Export as STEP (.step).",
}

_EXTENSION_TO_FORMAT = {ext: fmt for fmt, exts in GEOMETRY_FORMATS.items() for ext in exts}


def _suffix(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def detect_format(filename: str) -> str | None:
    """Canonical geometry format for a filename, or None if unsupported."""
    return _EXTENSION_TO_FORMAT.get(_suffix(filename))


def rejection_reason(filename: str) -> str:
    """Why `filename` was rejected, phrased so the user knows what to do next."""
    hint = UNSUPPORTED_FORMAT_HINTS.get(_suffix(filename))
    if hint:
        return hint
    supported = ", ".join(sorted(_EXTENSION_TO_FORMAT))
    return f"Unsupported geometry format. Supported extensions: {supported}."
