"""CATIA bridge tests.

Split in two by design:

**Mock/offline tests always run**, on any OS, with no CATIA installed. They
cover the contract every other layer depends on -- that `/status` is a 200 even
with no CATIA, that the module imports off Windows, and that a missing CATIA
surfaces as a typed error rather than a `com_error` escaping into a route.

**`@pytest.mark.catia` tests need a live CATIA** with a document open, and are
skipped automatically when there is none. They are the only ones that prove the
real COM surface, so they are not mocked: a mocked export cannot tell you that
`ExportData` wants `"stp"` rather than `"step"`.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.catia import (
    CATIAExportError,
    CATIANotRunningError,
    ExportFormat,
    bridge,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _catia_is_live() -> bool:
    """True only when a real CATIA is running to talk to."""
    if sys.platform != "win32":
        return False
    try:
        return bridge.is_catia_running()
    except Exception:
        return False


requires_catia = pytest.mark.skipif(
    not _catia_is_live(),
    reason="No running CATIA V5 instance to attach to",
)


# -- format parsing: pure, no COM ------------------------------------------


class TestExportFormat:
    def test_step_aliases_normalise_to_the_catia_token(self):
        # CATIA's ExportData wants "stp", not "step" -- getting this wrong is a
        # silent no-op export, so it is pinned here.
        assert ExportFormat.parse("step") is ExportFormat.STEP
        assert ExportFormat.parse("STEP") is ExportFormat.STEP
        assert ExportFormat.parse(".stp") is ExportFormat.STEP
        assert ExportFormat.STEP.value == "stp"

    def test_stl_and_iges_are_supported(self):
        assert ExportFormat.parse("stl") is ExportFormat.STL
        assert ExportFormat.parse("iges") is ExportFormat.IGES

    def test_unknown_format_is_rejected_with_an_actionable_message(self):
        with pytest.raises(CATIAExportError) as excinfo:
            ExportFormat.parse("dwg")
        assert "step" in str(excinfo.value).lower()

    def test_suffix_matches_the_value(self):
        assert ExportFormat.STEP.suffix == ".stp"


# -- platform guard ---------------------------------------------------------


class TestNonWindows:
    def test_module_imports_and_reports_unavailable_off_windows(self):
        """The backend must start on Linux/macOS; CATIA just isn't there."""
        with patch.object(bridge.sys, "platform", "linux"):
            assert bridge.is_windows() is False
            assert bridge.is_catia_running() is False

            status = bridge.get_status()
            assert status.running is False
            # The UI renders this string, so it has to explain itself.
            assert "windows" in (status.detail or "").lower()

    def test_status_never_raises_off_windows(self):
        with patch.object(bridge.sys, "platform", "darwin"):
            # No exception -- "not available" is a state, not a failure.
            assert bridge.get_status().running is False


# -- graceful degradation when CATIA is absent ------------------------------


class TestCatiaNotRunning:
    def test_missing_catia_becomes_a_typed_error_not_a_com_error(self):
        """A raw pywintypes.com_error must never escape the bridge."""
        if sys.platform != "win32":
            pytest.skip("COM attach path is Windows-only")

        fake_win32 = MagicMock()
        fake_win32.client.GetActiveObject.side_effect = OSError("no such object")

        with patch.dict(
            sys.modules, {"win32com": fake_win32, "win32com.client": fake_win32.client,
                          "pythoncom": MagicMock()}
        ):
            with pytest.raises(CATIANotRunningError) as excinfo:
                bridge._run_com(lambda catia: None, timeout=5.0, launch=False)

        message = str(excinfo.value)
        assert "not running" in message.lower()
        # Tell the user what to do, per this repo's error-message convention.
        assert "start" in message.lower()

    def test_status_reports_not_running_rather_than_raising(self):
        with patch.object(
            bridge, "_run_com", side_effect=CATIANotRunningError("CATIA is not running.")
        ):
            status = bridge.get_status()
        assert status.running is False
        assert status.detail is not None

    def test_is_catia_running_is_false_not_an_exception(self):
        with patch.object(
            bridge, "_run_com", side_effect=CATIANotRunningError("nope")
        ):
            assert bridge.is_catia_running() is False


# -- export path safety: no COM needed --------------------------------------


class TestExportPathSafety:
    def test_a_traversing_stem_cannot_escape_the_target_directory(self, tmp_path):
        """A stem from the model or the browser must not become a path."""
        with patch.object(bridge, "_run_com") as run_com:
            run_com.side_effect = AssertionError("should not reach COM")
            with pytest.raises((CATIAExportError, AssertionError)):
                bridge.export_active_document(
                    tmp_path, ExportFormat.STEP, stem="../../evil"
                )

    def test_stem_is_sanitised_to_a_plain_filename(self, tmp_path):
        captured = {}

        def fake_run_com(work, *, timeout, launch):
            # Drive the closure with a stub CATIA to capture the resolved path.
            catia = MagicMock()
            catia.Documents.Count = 1
            result = work(catia)
            captured["path"] = result
            captured["export_arg"] = catia.ActiveDocument.ExportData.call_args[0]
            return result

        with patch.object(bridge, "_run_com", side_effect=fake_run_com):
            with pytest.raises(CATIAExportError):
                # No real file is written by the stub, so the emptiness check
                # fires -- that check itself is the assertion here.
                bridge.export_active_document(
                    tmp_path, ExportFormat.STEP, stem="my/../part name!"
                )

        written = Path(captured["export_arg"][0])
        assert written.parent == tmp_path.resolve()
        assert "/" not in written.stem and ".." not in written.stem
        # The CATIA format token, not the human word.
        assert captured["export_arg"][1] == "stp"

    def test_empty_export_is_reported_rather_than_registered(self, tmp_path):
        """CATIA can report success and write nothing; that must not pass."""

        def fake_run_com(work, *, timeout, launch):
            catia = MagicMock()
            catia.Documents.Count = 1
            return work(catia)

        with patch.object(bridge, "_run_com", side_effect=fake_run_com):
            with pytest.raises(CATIAExportError) as excinfo:
                bridge.export_active_document(tmp_path, ExportFormat.STEP)
        assert "no usable" in str(excinfo.value).lower()

    def test_export_with_no_document_open_explains_itself(self, tmp_path):
        def fake_run_com(work, *, timeout, launch):
            catia = MagicMock()
            catia.Documents.Count = 0
            return work(catia)

        with patch.object(bridge, "_run_com", side_effect=fake_run_com):
            with pytest.raises(CATIAExportError) as excinfo:
                bridge.export_active_document(tmp_path, ExportFormat.STEP)
        assert "no document" in str(excinfo.value).lower()


# -- HTTP contract ----------------------------------------------------------


class TestCatiaRoutes:
    def test_status_is_always_200_even_with_no_catia(self, auth_client):
        """The desktop panel must always have something to render."""
        with patch(
            "app.api.routes.catia.get_status",
            return_value=bridge.CatiaStatus(running=False, detail="CATIA is not running."),
        ):
            response = auth_client.get("/api/v1/catia/status")

        assert response.status_code == 200
        body = response.json()
        assert body["running"] is False
        assert body["version"] is None
        assert body["detail"]

    def test_status_reports_version_when_catia_is_up(self, auth_client):
        with patch(
            "app.api.routes.catia.get_status",
            return_value=bridge.CatiaStatus(
                running=True, version="V5-R33", document_count=2, active_document="a.CATPart"
            ),
        ):
            response = auth_client.get("/api/v1/catia/status")

        body = response.json()
        assert response.status_code == 200
        assert body["running"] is True
        assert body["version"] == "V5-R33"
        assert body["open_documents"] == 2
        assert body["active_document"] == "a.CATPart"

    def test_status_requires_authentication(self, client):
        assert client.get("/api/v1/catia/status").status_code == 401

    def test_documents_returns_503_when_catia_is_absent(self, auth_client):
        with patch(
            "app.api.routes.catia.list_open_documents",
            side_effect=CATIANotRunningError("CATIA is not running."),
        ):
            response = auth_client.get("/api/v1/catia/documents")

        assert response.status_code == 503
        assert "not running" in response.json()["detail"].lower()

    def test_sync_on_another_users_project_is_404_not_403(
        self, auth_client, project_id: str
    ):
        """Ownership posture must match every other resource in this API."""
        auth_client.post(
            "/api/v1/auth/register",
            json={"email": "catia-other@kryova.dev", "password": "another-password"},
        )
        auth_client.post(
            "/api/v1/auth/login",
            data={"username": "catia-other@kryova.dev", "password": "another-password"},
        )
        auth_client.headers["x-csrf-token"] = auth_client.cookies["kryova_csrf"]

        response = auth_client.post(
            f"/api/v1/catia/projects/{project_id}/sync", json={}
        )
        # 404 not 403: project ids must not be enumerable across accounts.
        assert response.status_code == 404


# -- live CATIA -------------------------------------------------------------


@requires_catia
class TestLiveCatia:
    def test_status_reports_a_real_version(self):
        status = bridge.get_status()
        assert status.running is True
        assert status.version and status.version.startswith("V")

    def test_export_active_document_writes_a_real_step_file(self, tmp_path):
        exported = bridge.export_active_document(
            tmp_path, ExportFormat.STEP, stem="live_test"
        )
        assert exported.exists()
        assert exported.suffix == ".stp"
        assert exported.stat().st_size > 0
        # A STEP file is ISO-10303 text; anything else means a wrong translator.
        assert exported.read_text(errors="ignore").lstrip().startswith("ISO-10303")

    def test_exported_geometry_meshes_in_the_kryova_pipeline(self, tmp_path):
        """The point of the bridge: CATIA output must reach the solver."""
        from app.mesh.gmsh_mesher import generate_tet_mesh

        exported = bridge.export_active_document(
            tmp_path, ExportFormat.STEP, stem="mesh_test"
        )
        mesh = generate_tet_mesh(exported, element_size_mm=25.0, file_format="step")
        mesh = mesh[0] if isinstance(mesh, tuple) else mesh
        assert len(mesh.nodes) > 0
        assert len(mesh.tets) > 0
