from pathlib import Path

# Canonical format name -> accepted file extensions.
GEOMETRY_FORMATS: dict[str, tuple[str, ...]] = {
    "step": ("step", "stp"),
    "iges": ("iges", "igs"),
    "stl": ("stl",),
}

_EXTENSION_TO_FORMAT = {
    ext: fmt for fmt, exts in GEOMETRY_FORMATS.items() for ext in exts
}


def detect_format(filename: str) -> str | None:
    """Canonical geometry format for a filename, or None if unsupported."""
    suffix = Path(filename).suffix.lower().lstrip(".")
    return _EXTENSION_TO_FORMAT.get(suffix)
