"""End-to-end API tests for the simulation pipeline.

Jobs run inline here (see conftest), so a POST returns with the run already
finished and the assertions can check real numbers rather than poll.
"""

import pytest
from fastapi.testclient import TestClient

from tests.test_mesh import box_stl

BOX = (20.0, 20.0, 60.0)  # mm
FORCE = 8_000.0  # N

# Rollers on the three faces at the origin: pure tension with a closed form.
UNIAXIAL_FIXTURES = [
    {"where": {"type": "face", "axis": "z", "side": "min"}, "dofs": ["z"]},
    {"where": {"type": "face", "axis": "x", "side": "min"}, "dofs": ["x"]},
    {"where": {"type": "face", "axis": "y", "side": "min"}, "dofs": ["y"]},
]


@pytest.fixture
def project_with_geometry(auth_client: TestClient, project_id: str) -> str:
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


def _fields_digest(client: TestClient, job: dict) -> str:
    media = client.get(f"/api/v1/media/{job['fields_media_id']}").json()
    return media["sha256"]


def run(client: TestClient, project_id: str, **overrides) -> dict:
    payload = {"load_case": load_case(), "element_size_mm": 10.0, **overrides}
    response = client.post(f"/api/v1/projects/{project_id}/simulations", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


class TestRunningASimulation:
    def test_result_matches_the_hand_calculation(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        assert job["status"] == "succeeded", job["error"]

        expected_stress = FORCE / (BOX[0] * BOX[1])  # 20 MPa
        assert job["result"]["max_von_mises_mpa"] == pytest.approx(expected_stress, rel=1e-6)
        assert job["result"]["factor_of_safety"] == pytest.approx(276 / expected_stress, rel=1e-6)
        assert job["result"]["yields"] is False

    def test_mesh_statistics_are_recorded(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        stats = job["mesh_stats"]
        assert stats["element_type"] == "tet4"
        assert stats["element_count"] > 0
        assert stats["inverted_count"] == 0
        assert stats["volume_mm3"] == pytest.approx(BOX[0] * BOX[1] * BOX[2], rel=1e-6)

    def test_mass_is_reported(self, auth_client: TestClient, project_with_geometry: str) -> None:
        job = run(auth_client, project_with_geometry)
        expected = BOX[0] * BOX[1] * BOX[2] * 1e-9 * 2700
        assert job["result"]["mass_kg"] == pytest.approx(expected, rel=1e-6)

    def test_defaults_to_the_latest_geometry_version(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/geometry",
            files={"file": ("box2.stl", box_stl((10.0, 10.0, 10.0)), "application/octet-stream")},
        )
        job = run(auth_client, project_with_geometry)
        assert job["mesh_stats"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)

    def test_an_explicit_version_can_be_analysed(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/geometry",
            files={"file": ("box2.stl", box_stl((10.0, 10.0, 10.0)), "application/octet-stream")},
        )
        job = run(auth_client, project_with_geometry, geometry_version=1)
        assert job["mesh_stats"]["volume_mm3"] == pytest.approx(
            BOX[0] * BOX[1] * BOX[2], rel=1e-6
        )

    def test_overloading_the_part_is_flagged(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        # 200 kN over 400 mm^2 = 500 MPa, well past 6061's 276 MPa yield.
        job = run(auth_client, project_with_geometry, load_case=load_case(force=200_000.0))
        assert job["result"]["yields"] is True
        assert job["result"]["factor_of_safety"] < 1.0


class TestResultSurface:
    def test_surface_is_ready_for_a_viewer(
        self, auth_client: TestClient, project_with_geometry: str
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
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        surface = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}/surface"
        ).json()
        assert len(surface["node_positions"]) < job["mesh_stats"]["node_count"]

    def test_surface_is_unavailable_before_success(
        self, auth_client: TestClient, project_with_geometry: str
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
        {"where": {"type": "box", "min": [0, 0, 0], "max": [0.1, 0.1, 0.1]}, "dofs": ["x", "y", "z"]}
    ]
    return case


class TestFailureReporting:
    def test_an_ill_posed_model_fails_with_an_explanation(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry, load_case=_unconstrained_case())
        assert job["status"] == "failed"
        assert "under-constrained" in job["error"]
        assert job["result"] is None

    def test_a_selection_matching_nothing_fails_clearly(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        case = load_case()
        case["loads"][0]["where"] = {"type": "box", "min": [500, 500, 500], "max": [600, 600, 600]}
        job = run(auth_client, project_with_geometry, load_case=case)
        assert job["status"] == "failed"
        assert "matched no nodes" in job["error"]

    def test_too_fine_a_mesh_is_refused(
        self, auth_client: TestClient, project_with_geometry: str, monkeypatch
    ) -> None:
        from app.simulation import runner

        monkeypatch.setattr(runner.settings, "max_elements", 10)
        job = run(auth_client, project_with_geometry, element_size_mm=5.0)
        assert job["status"] == "failed"
        assert "over the 10 limit" in job["error"]


class TestSimulationLifecycle:
    def test_simulations_are_listed_newest_first(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        first = run(auth_client, project_with_geometry)
        second = run(auth_client, project_with_geometry, load_case=load_case(force=1_000.0))

        listed = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations"
        ).json()
        assert [job["id"] for job in listed] == [second["id"], first["id"]]

    def test_deleting_a_simulation_removes_its_fields(
        self, auth_client: TestClient, project_with_geometry: str
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
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        digest = _fields_digest(auth_client, job)

        auth_client.delete(f"/api/v1/projects/{project_with_geometry}")
        assert not auth_client.store.exists(digest)

    def test_another_users_simulation_is_not_visible(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        job = run(auth_client, project_with_geometry)
        auth_client.post(
            "/api/v1/auth/register",
            json={"email": "rival@kryova.dev", "password": "another-password"},
        )
        token = auth_client.post(
            "/api/v1/auth/login",
            data={"username": "rival@kryova.dev", "password": "another-password"},
        ).json()["access_token"]

        response = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


class TestPreconditions:
    def test_simulating_a_project_without_geometry_is_rejected(
        self, auth_client: TestClient, project_id: str
    ) -> None:
        response = auth_client.post(
            f"/api/v1/projects/{project_id}/simulations",
            json={"load_case": load_case()},
        )
        assert response.status_code == 404
        assert "upload a CAD file first" in response.json()["detail"]

    def test_a_missing_geometry_version_is_rejected(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations",
            json={"load_case": load_case(), "geometry_version": 99},
        )
        assert response.status_code == 404

    def test_a_load_case_without_fixtures_is_rejected(
        self, auth_client: TestClient, project_with_geometry: str
    ) -> None:
        case = load_case()
        case["fixtures"] = []
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations", json={"load_case": case}
        )
        assert response.status_code == 422


class TestMaterialLibrary:
    def test_materials_are_listed(self, client: TestClient) -> None:
        materials = client.get("/api/v1/materials").json()["materials"]
        names = [m["name"] for m in materials]
        assert "aluminium-6061-t6" in names
        assert "steel-1018" in names

    def test_a_material_can_be_fetched_by_name(self, client: TestClient) -> None:
        material = client.get("/api/v1/materials/steel-1018").json()
        assert material["youngs_modulus_mpa"] == 205_000

    def test_an_unknown_material_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/materials/unobtainium").status_code == 404
