"""Failures the CATIA bridge can raise.

Every COM error is re-raised as one of these, so callers never have to know
what a `pywintypes.com_error` is or which HRESULT means "CATIA is closed".
"""


class CATIABridgeError(RuntimeError):
    """Base class: something went wrong talking to CATIA."""


class CATIAUnavailableError(CATIABridgeError):
    """CATIA cannot be reached at all -- not installed, or not on Windows."""


class CATIANotRunningError(CATIABridgeError):
    """CATIA is installed but no instance is running to attach to."""


class CATIAExportError(CATIABridgeError):
    """CATIA is running but the export did not produce a usable file."""


class CATIATimeoutError(CATIABridgeError):
    """A COM call did not return in time -- usually a modal dialog in CATIA."""
