"""Media layer tests: local blob store, chunked uploads, and FAISS indexes."""

import hashlib
import io

import numpy as np
import pytest

from app.media.store import LocalMediaStore, MediaError, MediaNotFound, MediaTooLarge
from app.media.vectors import LocalVectorIndex, VectorIndexError
from app.models import MediaKind
from tests.typing import AuthenticatedTestClient


class TestBlobStore:
    def test_digest_is_the_sha256_of_the_content(self, media_store: LocalMediaStore) -> None:
        data = b"a bracket, in bytes"
        info = media_store.write_bytes(data)
        assert info.digest == hashlib.sha256(data).hexdigest()
        assert info.size_bytes == len(data)
        assert not info.deduplicated

    def test_identical_content_is_stored_once(self, media_store: LocalMediaStore) -> None:
        data = b"x" * 5000
        first = media_store.write_bytes(data)
        second = media_store.write_bytes(data)

        assert first.digest == second.digest
        assert second.deduplicated
        assert len(list(media_store.iter_digests())) == 1
        assert media_store.total_bytes() == len(data)

    def test_round_trip_survives_chunking(self, media_store: LocalMediaStore) -> None:
        # Deliberately not a multiple of the 64 KiB test chunk size.
        data = bytes(range(256)) * 1000 + b"tail"
        info = media_store.write(io.BytesIO(data))
        assert b"".join(media_store.iter_chunks(info.digest)) == data

    def test_reading_uses_many_chunks_not_one_slurp(self, media_store: LocalMediaStore) -> None:
        data = b"z" * (media_store.chunk_size * 3 + 17)
        info = media_store.write_bytes(data)
        chunks = list(media_store.iter_chunks(info.digest))
        assert len(chunks) == 4
        assert sum(len(c) for c in chunks) == len(data)

    def test_oversized_stream_is_refused(self, media_store: LocalMediaStore) -> None:
        with pytest.raises(MediaTooLarge):
            media_store.write(io.BytesIO(b"y" * 5000), max_bytes=1000)

    def test_a_refused_stream_leaves_nothing_behind(self, media_store: LocalMediaStore) -> None:
        with pytest.raises(MediaTooLarge):
            media_store.write(io.BytesIO(b"y" * 5000), max_bytes=1000)
        assert list(media_store.iter_digests()) == []

    def test_verify_detects_corruption_on_disk(self, media_store: LocalMediaStore) -> None:
        info = media_store.write_bytes(b"trustworthy")
        assert media_store.verify(info.digest)

        media_store.path_for(info.digest).write_bytes(b"tampered")
        assert not media_store.verify(info.digest)

    def test_missing_blob_raises(self, media_store: LocalMediaStore) -> None:
        with pytest.raises(MediaNotFound):
            media_store.open("0" * 64)

    def test_a_bad_digest_is_rejected(self, media_store: LocalMediaStore) -> None:
        with pytest.raises(MediaError):
            media_store.path_for("../../etc/passwd")

    def test_blobs_are_sharded_into_subdirectories(self, media_store: LocalMediaStore) -> None:
        info = media_store.write_bytes(b"sharded")
        path = media_store.path_for(info.digest)
        assert path.parent.name == info.digest[2:4]
        assert path.parent.parent.name == info.digest[:2]


class TestMediaService:
    def test_a_shared_blob_survives_deleting_one_record(
        self, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        # Two records, one digest: deleting either must not orphan the other.
        media = auth_client.media
        client = auth_client
        first = media.store_stream(
            owner_id=current_user_id, kind=MediaKind.CAD, filename="a.stl", stream=io.BytesIO(b"same")
        )
        second = media.store_stream(
            owner_id=current_user_id, kind=MediaKind.CAD, filename="b.stl", stream=io.BytesIO(b"same")
        )
        assert first.sha256 == second.sha256

        media.delete(first)
        assert client.store.exists(second.sha256), "blob dropped while still referenced"

        media.delete(second)
        assert not client.store.exists(second.sha256), "blob left behind with no references"


class TestChunkedUpload:
    def upload_in_chunks(self, client: AuthenticatedTestClient, data: bytes, chunk_size: int, **extra) -> dict:
        session = client.post(
            "/api/v1/media/uploads",
            json={
                "filename": "big.stl",
                "total_size_bytes": len(data),
                "chunk_size": chunk_size,
                **extra,
            },
        ).json()
        for index in range(session["total_chunks"]):
            piece = data[index * chunk_size : (index + 1) * chunk_size]
            response = client.put(
                f"/api/v1/media/uploads/{session['id']}/chunks/{index}", content=piece
            )
            assert response.status_code == 200, response.text
        return client.post(f"/api/v1/media/uploads/{session['id']}/complete").json()

    def test_chunks_reassemble_into_the_original_file(self, auth_client: AuthenticatedTestClient) -> None:
        data = bytes(range(256)) * 700  # 179,200 bytes
        media = self.upload_in_chunks(auth_client, data, chunk_size=50_000)

        assert media["size_bytes"] == len(data)
        assert media["sha256"] == hashlib.sha256(data).hexdigest()
        assert b"".join(auth_client.store.iter_chunks(media["sha256"])) == data

    def test_progress_reports_which_chunks_are_missing(self, auth_client: AuthenticatedTestClient) -> None:
        data = b"q" * 300
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={"filename": "part.stl", "total_size_bytes": len(data), "chunk_size": 100},
        ).json()
        assert session["total_chunks"] == 3

        auth_client.put(f"/api/v1/media/uploads/{session['id']}/chunks/1", content=data[100:200])
        progress = auth_client.get(f"/api/v1/media/uploads/{session['id']}").json()
        assert progress["received_chunks"] == [1]
        assert progress["missing_chunks"] == [0, 2]

    def test_chunks_may_arrive_out_of_order(self, auth_client: AuthenticatedTestClient) -> None:
        data = b"".join(bytes([i]) * 100 for i in range(3))
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={"filename": "part.stl", "total_size_bytes": len(data), "chunk_size": 100},
        ).json()
        for index in (2, 0, 1):
            auth_client.put(
                f"/api/v1/media/uploads/{session['id']}/chunks/{index}",
                content=data[index * 100 : (index + 1) * 100],
            )
        media = auth_client.post(f"/api/v1/media/uploads/{session['id']}/complete").json()
        assert media["sha256"] == hashlib.sha256(data).hexdigest()

    def test_a_retried_chunk_is_not_counted_twice(self, auth_client: AuthenticatedTestClient) -> None:
        data = b"r" * 200
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={"filename": "part.stl", "total_size_bytes": len(data), "chunk_size": 100},
        ).json()
        for _ in range(3):
            auth_client.put(
                f"/api/v1/media/uploads/{session['id']}/chunks/0", content=data[:100]
            )
        progress = auth_client.get(f"/api/v1/media/uploads/{session['id']}").json()
        assert progress["received_chunks"] == [0]

    def test_completing_early_is_refused(self, auth_client: AuthenticatedTestClient) -> None:
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={"filename": "part.stl", "total_size_bytes": 300, "chunk_size": 100},
        ).json()
        auth_client.put(f"/api/v1/media/uploads/{session['id']}/chunks/0", content=b"a" * 100)

        response = auth_client.post(f"/api/v1/media/uploads/{session['id']}/complete")
        assert response.status_code == 409
        assert "2 chunk(s) still missing" in response.json()["detail"]

    def test_a_wrong_sized_chunk_is_refused(self, auth_client: AuthenticatedTestClient) -> None:
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={"filename": "part.stl", "total_size_bytes": 300, "chunk_size": 100},
        ).json()
        response = auth_client.put(
            f"/api/v1/media/uploads/{session['id']}/chunks/0", content=b"short"
        )
        assert response.status_code == 422
        assert "expected 100" in response.json()["detail"]

    def test_a_chunk_index_out_of_range_is_refused(self, auth_client: AuthenticatedTestClient) -> None:
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={"filename": "part.stl", "total_size_bytes": 200, "chunk_size": 100},
        ).json()
        response = auth_client.put(
            f"/api/v1/media/uploads/{session['id']}/chunks/9", content=b"a" * 100
        )
        assert response.status_code == 422

    def test_a_corrupted_transfer_is_caught_by_the_checksum(self, auth_client: AuthenticatedTestClient) -> None:
        data = b"m" * 200
        wrong = hashlib.sha256(b"something else entirely").hexdigest()
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={
                "filename": "part.stl",
                "total_size_bytes": len(data),
                "chunk_size": 100,
                "expected_sha256": wrong,
            },
        ).json()
        for index in range(2):
            auth_client.put(
                f"/api/v1/media/uploads/{session['id']}/chunks/{index}",
                content=data[index * 100 : (index + 1) * 100],
            )

        response = auth_client.post(f"/api/v1/media/uploads/{session['id']}/complete")
        assert response.status_code == 409
        assert "corrupted" in response.json()["detail"]

    def test_aborting_discards_the_staged_chunks(self, auth_client: AuthenticatedTestClient) -> None:
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={"filename": "part.stl", "total_size_bytes": 200, "chunk_size": 100},
        ).json()
        auth_client.put(f"/api/v1/media/uploads/{session['id']}/chunks/0", content=b"a" * 100)

        assert auth_client.delete(f"/api/v1/media/uploads/{session['id']}").status_code == 204
        assert auth_client.get(f"/api/v1/media/uploads/{session['id']}").json()["status"] == "aborted"
        assert auth_client.post(
            f"/api/v1/media/uploads/{session['id']}/complete"
        ).status_code == 409

    def test_another_users_session_is_not_visible(self, auth_client: AuthenticatedTestClient) -> None:
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={"filename": "part.stl", "total_size_bytes": 100, "chunk_size": 100},
        ).json()
        auth_client.post(
            "/api/v1/auth/register",
            json={"email": "nosy@kryova.dev", "password": "another-password"},
        )
        auth_client.post(
            "/api/v1/auth/login",
            data={"username": "nosy@kryova.dev", "password": "another-password"},
        )
        auth_client.headers["x-csrf-token"] = auth_client.cookies["kryova_csrf"]

        response = auth_client.get(
            f"/api/v1/media/uploads/{session['id']}",
        )
        assert response.status_code == 404


class TestChunkedGeometryUpload:
    def test_a_chunked_cad_upload_becomes_a_geometry_version(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        from tests.test_mesh import box_stl

        data = box_stl((15.0, 25.0, 35.0))
        session = auth_client.post(
            "/api/v1/media/uploads",
            json={
                "filename": "chunked.stl",
                "total_size_bytes": len(data),
                "chunk_size": 200,
                "kind": "cad",
            },
        ).json()
        for index in range(session["total_chunks"]):
            auth_client.put(
                f"/api/v1/media/uploads/{session['id']}/chunks/{index}",
                content=data[index * 200 : (index + 1) * 200],
            )
        media = auth_client.post(f"/api/v1/media/uploads/{session['id']}/complete").json()

        response = auth_client.post(
            f"/api/v1/projects/{project_id}/geometry/attach", data={"media_id": media["id"]}
        )
        assert response.status_code == 201, response.text
        version = response.json()
        assert version["filename"] == "chunked.stl"
        assert version["stats"]["triangle_count"] == 12
        assert version["stats"]["bounding_box"]["size"] == [15.0, 25.0, 35.0]


class TestMediaEndpoints:
    def test_media_content_downloads_intact(self, auth_client: AuthenticatedTestClient, project_id: str, cube_stl) -> None:
        version = auth_client.post(
            f"/api/v1/projects/{project_id}/geometry",
            files={"file": ("part.stl", cube_stl, "application/octet-stream")},
        ).json()

        response = auth_client.get(f"/api/v1/media/{version['media_id']}/content")
        assert response.status_code == 200
        assert response.content == cube_stl

    def test_verify_confirms_an_intact_blob(self, auth_client: AuthenticatedTestClient, project_id: str, cube_stl) -> None:
        version = auth_client.post(
            f"/api/v1/projects/{project_id}/geometry",
            files={"file": ("part.stl", cube_stl, "application/octet-stream")},
        ).json()

        result = auth_client.post(f"/api/v1/media/{version['media_id']}/verify").json()
        assert result["intact"] is True
        assert result["sha256"] == hashlib.sha256(cube_stl).hexdigest()

    def test_media_is_listed_for_its_owner_only(self, auth_client: AuthenticatedTestClient, project_id: str, cube_stl) -> None:
        auth_client.post(
            f"/api/v1/projects/{project_id}/geometry",
            files={"file": ("part.stl", cube_stl, "application/octet-stream")},
        )
        assert len(auth_client.get("/api/v1/media").json()) == 1

        auth_client.post(
            "/api/v1/auth/register",
            json={"email": "other@kryova.dev", "password": "another-password"},
        )
        auth_client.post(
            "/api/v1/auth/login",
            data={"username": "other@kryova.dev", "password": "another-password"},
        )
        auth_client.headers["x-csrf-token"] = auth_client.cookies["kryova_csrf"]
        listed = auth_client.get("/api/v1/media").json()
        assert listed == []


class TestVectorIndex:
    @staticmethod
    def sample(count: int = 40, dimension: int = 16) -> np.ndarray:
        rng = np.random.default_rng(20260821)
        return rng.random((count, dimension), dtype=np.float32)

    def test_search_finds_the_exact_match_first(self) -> None:
        vectors = self.sample()
        index = LocalVectorIndex.build(vectors, ids=list(range(100, 140)))

        hits = index.search(vectors[7], k=3)[0]
        assert hits[0].id == 107
        assert hits[0].score == pytest.approx(1.0, abs=1e-5)  # cosine with itself

    def test_ids_are_the_callers_own(self) -> None:
        index = LocalVectorIndex.build(self.sample(5), ids=[900, 901, 902, 903, 904])
        assert {hit.id for hit in index.search(self.sample(1), k=5)[0]} <= set(range(900, 905))

    def test_l2_metric_ranks_by_distance(self) -> None:
        vectors = np.array([[0, 0], [1, 0], [5, 0]], dtype=np.float32)
        index = LocalVectorIndex.build(vectors, ids=[0, 1, 2], metric="l2")

        hits = index.search(np.array([[0.9, 0.0]], dtype=np.float32), k=3)[0]
        assert [hit.id for hit in hits] == [1, 0, 2]

    def test_index_survives_a_save_and_load(
        self, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        client = auth_client
        vectors = self.sample()
        index = LocalVectorIndex.build(vectors, ids=list(range(40)))

        media = index.save(client.media, owner_id=current_user_id, name="chunks")
        client.media.db.commit()

        assert media.kind is MediaKind.VECTOR_INDEX
        assert media.meta["vector_count"] == 40
        assert media.meta["dimension"] == 16

        reloaded = LocalVectorIndex.load(client.media, media)
        assert reloaded.count == 40
        assert reloaded.search(vectors[3], k=1)[0][0].id == 3

    def test_a_saved_index_is_content_addressed_like_any_blob(
        self, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        client = auth_client
        index = LocalVectorIndex.build(self.sample(10), ids=list(range(10)))

        media = index.save(client.media, owner_id=current_user_id, name="chunks")
        assert client.store.verify(media.sha256)
        assert client.store.exists(media.sha256)

    def test_removing_ids_shrinks_the_index(self) -> None:
        index = LocalVectorIndex.build(self.sample(10), ids=list(range(10)))
        assert index.remove([0, 1, 2]) == 3
        assert index.count == 7

    def test_dimension_mismatch_is_rejected(self) -> None:
        index = LocalVectorIndex.build(self.sample(5, 8), ids=list(range(5)))
        with pytest.raises(VectorIndexError, match="dimension"):
            index.add(self.sample(2, 16), ids=[10, 11])

    def test_id_count_must_match_vector_count(self) -> None:
        index = LocalVectorIndex.create(8)
        with pytest.raises(VectorIndexError, match="ids"):
            index.add(self.sample(3, 8), ids=[1, 2])

    def test_searching_an_empty_index_is_refused(self) -> None:
        with pytest.raises(VectorIndexError, match="empty"):
            LocalVectorIndex.create(8).search(self.sample(1, 8))
