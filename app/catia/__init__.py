"""CATIA V5 integration (direct COM bridge and remote socket daemon bridge)."""

from app.catia.bridge import (
    CatiaDocument,
    CatiaStatus,
    ExportFormat,
    export_active_document,
    get_status,
    is_catia_running,
    is_windows,
    launch,
    list_open_documents,
    new_part,
)
from app.catia.dispatch import (
    CATIA_TOOL_SPECS,
    CatiaError,
    CatiaUnavailable,
    call_catia,
    catia_available,
)
from app.catia.errors import (
    CATIABridgeError,
    CATIAExportError,
    CATIANotRunningError,
    CATIATimeoutError,
    CATIAUnavailableError,
)
from app.catia.tool_specs import CatiaTier, CatiaToolSpec

__all__ = [
    "CATIA_TOOL_SPECS",
    "CATIABridgeError",
    "CATIAExportError",
    "CATIANotRunningError",
    "CATIATimeoutError",
    "CATIAUnavailableError",
    "CatiaDocument",
    "CatiaError",
    "CatiaStatus",
    "CatiaTier",
    "CatiaToolSpec",
    "CatiaUnavailable",
    "ExportFormat",
    "call_catia",
    "catia_available",
    "export_active_document",
    "get_status",
    "is_catia_running",
    "is_windows",
    "launch",
    "list_open_documents",
    "new_part",
]
