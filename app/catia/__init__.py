"""CATIA V5 integration. Windows-only at runtime, importable everywhere."""

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
from app.catia.errors import (
    CATIABridgeError,
    CATIAExportError,
    CATIANotRunningError,
    CATIATimeoutError,
    CATIAUnavailableError,
)

__all__ = [
    "CATIABridgeError",
    "CATIAExportError",
    "CATIANotRunningError",
    "CATIATimeoutError",
    "CATIAUnavailableError",
    "CatiaDocument",
    "CatiaStatus",
    "ExportFormat",
    "export_active_document",
    "get_status",
    "is_catia_running",
    "is_windows",
    "launch",
    "list_open_documents",
    "new_part",
]
