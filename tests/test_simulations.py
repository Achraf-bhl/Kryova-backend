"""End-to-end API tests for the simulation pipeline.

Jobs run inline here (see conftest), so a POST returns with the run already
finished and the assertions can check real numbers rather than poll.
"""

import pytest

from tests.test_mesh import box_stl
from tests.typing import AuthenticatedTestClient

BOX = (20.0, 20.0, 60.0)  # mm
FORCE = 8_000.0  # N

# Rollers on the three faces at the origin: pure tension with a closed form.
UNIAXIAL_FIXTURES = [
    {"where": {"type": "face", "axis": "z", "side": "min"}, "dofs": ["z"]},
    {"where": {"type": "face", "axis": "x", "side": "min"}, "dofs": ["x"]},
    {"where": {"type": "face", "axis": "y", "side": "min"}, "dofs": ["y"]},
]


@pytest.fixture
def project_with_geometry(auth_client: AuthenticatedTestClient, project_id: str) -> str:
    response = auth_client.post(
        f"/api/v1/projects/{project_id}/geometry",
        files={"file": ("box.stl", box_stl(BOX), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return project_id


def load_case(material: str = "aluminium-6061-t6", force: float = FORCE) -> dict:
    materials = {
        "aluminium-6061-t6": {
            "name": "aluminium-6061-t6",
            "youngs_modulus_mpa": 68_900,
            "poissons_ratio": 0.33,
            "yield_strength_mpa": 276,
            "density_kg_m3": 2700,
        }
    }
    return {
        "name": "Axial pull",
        "material": materials[material],
        "fixtures": UNIAXIAL_FIXTURES,
        "loads": [
            {
                "where": {"type": "face", "axis": "z", "side": "max"},
                "force_n": [0.0, 0.0, force],
            }
        ],
    }


def _fields_digest(client: AuthenticatedTestClient, job: dict) -> str:
    media = client.get(f"/api/v1/media/{job['fields_media_id']}").json()
    return media["sha256"]


def run(client: AuthenticatedTestClient, project_id: str, **overrides) -> dict:
    payload = {"load_case": load_case(), "element_size_mm": 10.0, **overrides}
    response = client.post(f"/api/v1/projects/{project_id}/simulations", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


class TestRunningASimulation:
    def test_result_matches_the_hand_calculation(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        assert job["status"] == "succeeded", job["error"]

        expected_stress = FORCE / (BOX[0] * BOX[1])  # 20 MPa
        assert job["result"]["max_von_mises_mpa"] == pytest.approx(expected_stress, rel=1e-6)
        assert job["result"]["factor_of_safety"] == pytest.approx(276 / expected_stress, rel=1e-6)
        assert job["result"]["yields"] is False

    def test_mesh_statistics_are_recorded(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        stats = job["mesh_stats"]
        assert stats["element_type"] == "tet4"
        assert stats["element_count"] > 0
        assert stats["inverted_count"] == 0
        assert stats["volume_mm3"] == pytest.approx(BOX[0] * BOX[1] * BOX[2], rel=1e-6)

    def test_mass_is_reported(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        expected = BOX[0] * BOX[1] * BOX[2] * 1e-9 * 2700
        assert job["result"]["mass_kg"] == pytest.approx(expected, rel=1e-6)

    def test_defaults_to_the_latest_geometry_version(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/geometry",
            files={"file": ("box2.stl", box_stl((10.0, 10.0, 10.0)), "application/octet-stream")},
        )
        job = run(auth_client, project_with_geometry)
        assert job["mesh_stats"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)

    def test_an_explicit_version_can_be_analysed(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/geometry",
            files={"file": ("box2.stl", box_stl((10.0, 10.0, 10.0)), "application/octet-stream")},
        )
        job = run(auth_client, project_with_geometry, geometry_version=1)
        assert job["mesh_stats"]["volume_mm3"] == pytest.approx(BOX[0] * BOX[1] * BOX[2], rel=1e-6)

    def test_overloading_the_part_is_flagged(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        # 200 kN over 400 mm^2 = 500 MPa, well past 6061's 276 MPa yield.
        job = run(auth_client, project_with_geometry, load_case=load_case(force=200_000.0))
        assert job["result"]["yields"] is True
        assert job["result"]["factor_of_safety"] < 1.0


class TestResultSurface:
    def test_surface_is_ready_for_a_viewer(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        response = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}/surface"
        )
        assert response.status_code == 200
        surface = response.json()

        n = len(surface["node_positions"])
        assert n > 0
        assert len(surface["displacements"]) == n
        assert len(surface["von_mises_mpa"]) == n
        # Triangle indices must address the trimmed node list, not the full mesh.
        assert max(max(t) for t in surface["triangles"]) < n
        assert surface["max_von_mises_mpa"] == pytest.approx(
            job["result"]["max_von_mises_mpa"], rel=1e-9
        )

    def test_surface_carries_only_boundary_nodes(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        surface = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}/surface"
        ).json()
        assert len(surface["node_positions"]) < job["mesh_stats"]["node_count"]

    def test_surface_binary_stream(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        import struct

        job = run(auth_client, project_with_geometry)
        response = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}/surface/binary"
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"

        data = response.content
        header = data[:32]
        magic, version, num_nodes, num_triangles, max_vm, max_disp, _ = struct.unpack(
            "<4sIIIff8s", header
        )

        assert magic == b"KRYO"
        assert version == 1
        assert num_nodes > 0
        assert num_triangles > 0
        assert max_vm == pytest.approx(job["result"]["max_von_mises_mpa"], rel=1e-6)

    def test_surface_is_unavailable_before_success(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry, load_case=_unconstrained_case())
        assert job["status"] == "failed"
        response = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}/surface"
        )
        assert response.status_code == 409


def _unconstrained_case() -> dict:
    case = load_case()
    # A single interior point is not enough to stop the part rotating.
    case["fixtures"] = [
        {
            "where": {"type": "box", "min": [0, 0, 0], "max": [0.1, 0.1, 0.1]},
            "dofs": ["x", "y", "z"],
        }
    ]
    return case


class TestFailureReporting:
    def test_an_ill_posed_model_fails_with_an_explanation(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry, load_case=_unconstrained_case())
        assert job["status"] == "failed"
        assert "under-constrained" in job["error"]
        assert job["result"] is None

    def test_a_selection_matching_nothing_fails_clearly(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        case = load_case()
        case["loads"][0]["where"] = {"type": "box", "min": [500, 500, 500], "max": [600, 600, 600]}
        job = run(auth_client, project_with_geometry, load_case=case)
        assert job["status"] == "failed"
        assert "matched no nodes" in job["error"]

    def test_too_fine_a_mesh_is_refused(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str, monkeypatch
    ) -> None:
        from app.simulation import runner

        monkeypatch.setattr(runner.settings, "max_elements", 10)
        job = run(auth_client, project_with_geometry, element_size_mm=5.0)
        assert job["status"] == "failed"
        assert "over the 10 limit" in job["error"]


class TestSimulationLifecycle:
    def test_simulations_are_listed_newest_first(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        first = run(auth_client, project_with_geometry)
        second = run(auth_client, project_with_geometry, load_case=load_case(force=1_000.0))

        listed = auth_client.get(f"/api/v1/projects/{project_with_geometry}/simulations").json()
        assert [job["id"] for job in listed["items"]] == [
            second["id"],
            first["id"],
        ]

    def test_deleting_a_simulation_removes_its_fields(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        digest = _fields_digest(auth_client, job)
        assert auth_client.store.exists(digest)

        assert (
            auth_client.delete(
                f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}"
            ).status_code
            == 204
        )
        assert not auth_client.store.exists(digest)

    def test_deleting_a_project_removes_its_simulation_fields(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        digest = _fields_digest(auth_client, job)

        auth_client.delete(f"/api/v1/projects/{project_with_geometry}")
        assert not auth_client.store.exists(digest)

    def test_another_users_simulation_is_not_visible(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        auth_client.post(
            "/api/v1/auth/register",
            json={"email": "rival@kryova.dev", "password": "another-password"},
        )
        auth_client.post(
            "/api/v1/auth/login",
            data={"username": "rival@kryova.dev", "password": "another-password"},
        )
        auth_client.headers["x-csrf-token"] = auth_client.cookies["kryova_csrf"]

        response = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}",
        )
        assert response.status_code == 404


class TestPreconditions:
    def test_simulating_a_project_without_geometry_is_rejected(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        response = auth_client.post(
            f"/api/v1/projects/{project_id}/simulations",
            json={"load_case": load_case()},
        )
        assert response.status_code == 404
        assert "upload a CAD file first" in response.json()["detail"]

    def test_a_missing_geometry_version_is_rejected(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations",
            json={"load_case": load_case(), "geometry_version": 99},
        )
        assert response.status_code == 404

    def test_a_load_case_without_fixtures_is_rejected(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        case = load_case()
        case["fixtures"] = []
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations", json={"load_case": case}
        )
        assert response.status_code == 422


class TestMaterialLibrary:
    def test_materials_are_listed(self, client: AuthenticatedTestClient) -> None:
        materials = client.get("/api/v1/materials").json()["materials"]
        names = [m["name"] for m in materials]
        assert "aluminium-6061-t6" in names
        assert "steel-1018" in names

    def test_a_material_can_be_fetched_by_name(self, client: AuthenticatedTestClient) -> None:
        material = client.get("/api/v1/materials/steel-1018").json()
        assert material["youngs_modulus_mpa"] == 205_000

    def test_an_unknown_material_is_404(self, client: AuthenticatedTestClient) -> None:
        assert client.get("/api/v1/materials/unobtainium").status_code == 404


class TestConcurrencyQuota:
    """The queue is shared. Without a per-user ceiling one account can occupy
    every worker and everyone else waits behind it. The agent tool applies the
    same rule before proposing a run; this is the one that binds, because the
    HTTP route is reachable without the agent."""

    @staticmethod
    def _queue_jobs(client: AuthenticatedTestClient, project_id: str, count: int) -> None:
        """Leave `count` jobs sitting in QUEUED, as a real backlog would."""
        from app.models import JobStatus, SimulationJob

        db = client.media.db
        version_id = client.get(f"/api/v1/projects/{project_id}/geometry").json()["items"][0]["id"]
        for _ in range(count):
            db.add(
                SimulationJob(
                    project_id=project_id,
                    geometry_version_id=version_id,
                    status=JobStatus.QUEUED,
                    solver="linear-static",
                    load_case=load_case(),
                )
            )
        db.flush()

    def test_a_run_is_refused_once_the_quota_is_full(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str, monkeypatch
    ) -> None:
        from app.api.routes import simulations

        monkeypatch.setattr(simulations.settings, "max_concurrent_simulations_per_user", 2)
        self._queue_jobs(auth_client, project_with_geometry, 2)

        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations",
            json={"load_case": load_case(), "element_size_mm": 10.0},
        )
        assert response.status_code == 429
        assert "limit of 2" in response.json()["detail"]

    def test_the_message_says_how_to_proceed(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str, monkeypatch
    ) -> None:
        from app.api.routes import simulations

        monkeypatch.setattr(simulations.settings, "max_concurrent_simulations_per_user", 1)
        self._queue_jobs(auth_client, project_with_geometry, 1)

        detail = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations",
            json={"load_case": load_case(), "element_size_mm": 10.0},
        ).json()["detail"]
        assert "Wait for one to finish" in detail

    def test_the_quota_counts_across_a_user_s_projects_not_within_one(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str, monkeypatch
    ) -> None:
        from app.api.routes import simulations

        monkeypatch.setattr(simulations.settings, "max_concurrent_simulations_per_user", 1)
        self._queue_jobs(auth_client, project_with_geometry, 1)

        # A second project of the same user must not reset the budget.
        other = auth_client.post("/api/v1/projects", json={"name": "Second"}).json()["id"]
        auth_client.post(
            f"/api/v1/projects/{other}/geometry",
            files={"file": ("box.stl", box_stl(BOX), "application/octet-stream")},
        )
        response = auth_client.post(
            f"/api/v1/projects/{other}/simulations",
            json={"load_case": load_case(), "element_size_mm": 10.0},
        )
        assert response.status_code == 429

    def test_finished_runs_do_not_count(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str, monkeypatch
    ) -> None:
        from app.api.routes import simulations

        monkeypatch.setattr(simulations.settings, "max_concurrent_simulations_per_user", 1)
        # Jobs run inline here, so this one is already SUCCEEDED on return.
        assert run(auth_client, project_with_geometry)["status"] == "succeeded"
        assert run(auth_client, project_with_geometry)["status"] == "succeeded"


class TestElementOrder:
    def test_the_default_is_linear(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        assert job["element_order"] == 1
        assert job["mesh_stats"]["element_type"] == "tet4"

    def test_order_two_meshes_and_solves_with_tet10(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry, element_order=2)
        assert job["status"] == "succeeded", job["error"]
        assert job["element_order"] == 2
        assert job["mesh_stats"]["element_type"] == "tet10"

    def test_both_orders_agree_on_a_statically_determinate_bar(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        # Pure tension has a closed form that does not depend on the element
        # order, so the two must land on the same number.
        linear = run(auth_client, project_with_geometry)
        quadratic = run(auth_client, project_with_geometry, element_order=2)
        expected = FORCE / (BOX[0] * BOX[1])

        assert linear["result"]["max_von_mises_mpa"] == pytest.approx(expected, rel=1e-6)
        assert quadratic["result"]["max_von_mises_mpa"] == pytest.approx(expected, rel=1e-6)
        assert quadratic["result"]["node_count"] > linear["result"]["node_count"]

    def test_an_unsupported_order_is_rejected_before_anything_runs(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations",
            json={"load_case": load_case(), "element_order": 3},
        )
        assert response.status_code == 422

    def test_a_quadratic_result_surface_is_still_renderable(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        # The viewer only ever draws corner triangles; midside nodes must not
        # leak into the payload as unreferenced positions.
        job = run(auth_client, project_with_geometry, element_order=2)
        surface = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}/surface"
        ).json()

        count = len(surface["node_positions"])
        assert max(max(t) for t in surface["triangles"]) < count
        assert len(surface["von_mises_mpa"]) == count


class TestPreMeshLimits:
    """`max_elements` alone only fires once the machine has already paid for
    the mesh, and a small enough element size makes that bill unbounded."""

    def test_an_absurdly_fine_element_size_is_refused_without_meshing(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        # 0.01 mm across a 66 mm bar is ~6,600 elements along the diagonal.
        job = run(auth_client, project_with_geometry, element_size_mm=0.01)
        assert job["status"] == "failed"
        assert "finer than" in job["error"]
        assert "Use at least" in job["error"]

    def test_the_estimate_refuses_a_mesh_bomb_before_gmsh_runs(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str, monkeypatch
    ) -> None:
        import app.mesh.gmsh_mesher as mesher
        from app.simulation import runner

        monkeypatch.setattr(runner.settings, "max_elements", 100)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("gmsh ran despite an estimate over the limit")

        monkeypatch.setattr(mesher, "generate_tet_mesh", fail_if_called)
        monkeypatch.setattr(runner, "generate_tet_mesh", fail_if_called)

        job = run(auth_client, project_with_geometry, element_size_mm=1.0)
        assert job["status"] == "failed"
        assert "over the 100 limit" in job["error"]
