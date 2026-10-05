"""Export a whole project to one archive, and import one back (ROAD_TO_10 7.8).

**One zip.** A `manifest.json` that lists every other member with its SHA-256 and size; the
geometry files themselves, byte-for-byte; each design as its full revision chain; the
simulations' recorded results with their provenance; and each design's technical-file
contribution (`app/core/technical_file.py`). The manifest is written last, because it is the
only member that needs every other member's hash.

**Import verifies before it accepts, and "before" means before the database.** The order is
fixed and each step refuses by name:

1. The member list is sane: no duplicates, no absolute or `..` names, a bounded count, and no
   member that the manifest does not list or list that is not present.
2. Every member is read in chunks, counted against its declared size, and hashed against the
   manifest. A truncated or altered file stops the import here, with nothing written.
3. Every design loads (`DesignSpec.from_dict` refuses a format it does not read) and its
   recomputed `digest()` equals the recorded one -- a design that *loads* but builds a
   different part under this version is refused rather than imported as the original.
4. Only then are rows created, and **geometry is re-inspected from the stored bytes**: the
   archive's recorded statistics are not trusted, since a manifest that matches its own files
   proves integrity and says nothing about what the files are.

**What import does not restore, said in the response (`NOT_RESTORED`).** Simulations and the
technical file travel in the archive as evidence and are *not* recreated as live rows: a
result is bound to the solver and mesh that produced it, and a row appearing under this
installation's name for a solve it never ran is exactly the claim Decision 3 forbids. Nor
are transcripts, the CATIA document, requirements, or memory. Imported revisions authored by
a person keep `author="user"` with no `author_id`, because that person's account is not here.

Streaming throughout (*Every heavy byte goes through app/media/*): the archive is a file on
disk, blobs are copied in chunks, and nothing here reads a geometry file whole.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import projects, technical_file
from app.core.config import settings
from app.design.spec import DesignSpec, SpecError
from app.geometry.formats import detect_format
from app.geometry.inspect import GeometryError, inspect
from app.media import MediaService
from app.models import (
    Conversation,
    DesignDocument,
    DesignRevision,
    GeometryVersion,
    MediaKind,
    Project,
    SimulationJob,
    User,
)

FORMAT = "kryova-project"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 5000
_CHUNK = 1024 * 1024

NOT_RESTORED: tuple[str, ...] = (
    "simulations (kept in the archive as evidence; a result is bound to the solver that produced it)",
    "the technical file (kept in the archive; regenerate it from the imported design)",
    "conversation transcripts",
    "the CATIA document",
    "requirements and project memory",
)


class ArchiveRefusal(ValueError):
    """An archive that cannot be exported or imported, in a sentence the user can act on."""


@dataclass
class Imported:
    project: Project
    geometry_versions: int
    designs: int
    not_restored: tuple[str, ...] = field(default=NOT_RESTORED)


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "file"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class _Writer:
    """Adds members to the zip and records each one's hash and size for the manifest."""

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.files: dict[str, dict[str, Any]] = {}

    def add_json(self, name: str, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        self.archive.writestr(name, data)
        self.files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    def add_stream(self, name: str, chunks) -> None:  # type: ignore[no-untyped-def]
        digest = hashlib.sha256()
        size = 0
        with self.archive.open(name, "w", force_zip64=True) as out:
            for chunk in chunks:
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
        self.files[name] = {"sha256": digest.hexdigest(), "size": size}


def export(
    db: Session,
    media: MediaService,
    project: Project,
    path: Path,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Write `project` to `path` and return the manifest. The caller owns the file."""
    versions = db.scalars(
        select(GeometryVersion)
        .where(GeometryVersion.project_id == project.id)
        .order_by(GeometryVersion.version_number)
    ).all()
    documents = db.scalars(
        select(DesignDocument)
        .where(DesignDocument.project_id == project.id)
        .order_by(DesignDocument.created_at)
    ).all()
    jobs = db.scalars(
        select(SimulationJob)
        .where(SimulationJob.project_id == project.id)
        .order_by(SimulationJob.created_at)
    ).all()
    number_of = {version.id: version.version_number for version in versions}

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        writer = _Writer(archive)

        geometry = []
        for version in versions:
            member = f"geometry/v{version.version_number}-{_safe_name(version.filename)}"
            writer.add_stream(member, media.iter_chunks(version.media))
            geometry.append(
                {
                    "version_number": version.version_number,
                    "filename": version.filename,
                    "file_format": version.file_format,
                    "note": version.note,
                    "file": member,
                }
            )

        design_entries = []
        for index, document in enumerate(documents, start=1):
            member = f"designs/{index}.json"
            revisions = db.scalars(
                select(DesignRevision)
                .where(DesignRevision.design_id == document.id)
                .order_by(DesignRevision.revision_number)
            ).all()
            writer.add_json(
                member,
                {
                    "name": document.name,
                    "digest": document.digest,
                    "revision_number": document.revision_number,
                    "document": document.document,
                    "placed_on_market_on": (
                        document.placed_on_market_on.isoformat()
                        if document.placed_on_market_on
                        else None
                    ),
                    "revisions": [
                        {
                            "revision_number": revision.revision_number,
                            "digest": revision.digest,
                            "document": revision.document,
                            "summary": revision.summary,
                            "author": revision.author,
                            "created_at": _iso(revision.created_at),
                        }
                        for revision in revisions
                    ],
                },
            )
            tf_member = f"technical-file/{index}.json"
            writer.add_json(
                tf_member,
                technical_file.build(db, document, now=now, project_readable=True),
            )
            design_entries.append({"file": member, "technical_file": tf_member})

        writer.add_json(
            "simulations.json",
            [
                {
                    "id": job.id,
                    "geometry_version_number": number_of.get(job.geometry_version_id),
                    "analysis": job.analysis,
                    "status": job.status.value,
                    "solver": job.solver,
                    "solver_version": job.solver_version,
                    "element_size_mm": job.element_size_mm,
                    "element_order": job.element_order,
                    "grids": job.grids,
                    "thickness_mm": job.thickness_mm,
                    "load_case": job.load_case,
                    "thermal_case": job.thermal_case,
                    "transient_case": job.transient_case,
                    "flow_case": job.flow_case,
                    "mesh_stats": job.mesh_stats,
                    "result": job.result,
                    "created_at": _iso(job.created_at),
                }
                for job in jobs
            ],
        )

        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "exported_at": now.isoformat(),
            "project": {
                "name": project.name,
                "description": project.description,
                "tags": list(project.tags or []),
                "template_key": project.template_key,
            },
            "geometry": geometry,
            "designs": design_entries,
            "simulations": "simulations.json",
            "not_restored_on_import": list(NOT_RESTORED),
            "files": writer.files,
        }
        manifest["archive_digest"] = hashlib.sha256(
            json.dumps(writer.files, sort_keys=True).encode("utf-8")
        ).hexdigest()
        archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


# -- import ---------------------------------------------------------------------------------


def _check_names(names: list[str]) -> None:
    if len(names) > MAX_MEMBERS:
        raise ArchiveRefusal(
            f"This archive holds {len(names):,} members; at most {MAX_MEMBERS:,} are accepted."
        )
    if len(set(names)) != len(names):
        raise ArchiveRefusal("This archive lists the same member name twice, so it is refused.")
    for name in names:
        posix = PurePosixPath(name)
        if posix.is_absolute() or ".." in posix.parts or "\\" in name or not name:
            raise ArchiveRefusal(f"This archive holds a member named {name!r}, which is refused.")


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        info = archive.getinfo(MANIFEST)
    except KeyError:
        raise ArchiveRefusal(
            f"This is not a Kryova project archive: it has no {MANIFEST}."
        ) from None
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ArchiveRefusal("This archive's manifest is implausibly large, so it is refused.")
    try:
        manifest = json.loads(archive.read(info))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ArchiveRefusal("This archive's manifest is not valid JSON.") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ArchiveRefusal("This is not a Kryova project archive.")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ArchiveRefusal(
            f"This archive is format version {manifest.get('format_version')!r}; this build "
            f"reads version {FORMAT_VERSION}. Refusing to guess at the difference."
        )
    return manifest


def _verify_members(archive: zipfile.ZipFile, manifest: dict[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ArchiveRefusal("This archive's manifest lists no files.")
    present = {name for name in archive.namelist() if name != MANIFEST}
    if present != set(files):
        extra = sorted(present - set(files))
        missing = sorted(set(files) - present)
        raise ArchiveRefusal(
            "This archive does not match its manifest"
            + (f": members the manifest does not list: {extra[:5]}" if extra else "")
            + (f"; listed members that are absent: {missing[:5]}" if missing else "")
            + "."
        )
    total = 0
    for name, expected in files.items():
        declared = expected.get("size")
        if not isinstance(declared, int) or declared < 0:
            raise ArchiveRefusal(f"The manifest gives {name!r} no valid size.")
        total += declared
        if total > settings.max_media_bytes:
            raise ArchiveRefusal(
                f"This archive unpacks to more than the {settings.max_media_bytes:,} byte "
                "limit, so it is refused."
            )
        digest = hashlib.sha256()
        seen = 0
        with archive.open(name) as member:
            while chunk := member.read(_CHUNK):
                seen += len(chunk)
                if seen > declared:
                    break
                digest.update(chunk)
        if seen != declared or digest.hexdigest() != expected.get("sha256"):
            raise ArchiveRefusal(
                f"{name!r} does not match the hash recorded in the manifest, so nothing was "
                "imported. The file is damaged or was changed after it was exported."
            )


def _load_designs(
    archive: zipfile.ZipFile, manifest: dict[str, Any]
) -> list[tuple[dict[str, Any], DesignSpec]]:
    loaded = []
    for entry in manifest.get("designs") or []:
        data = json.loads(archive.read(entry["file"]))
        try:
            head = DesignSpec.from_dict(data["document"])
            if head.digest() != data["digest"]:
                raise ArchiveRefusal(
                    f"The design {data.get('name')!r} loads but its digest is now "
                    f"{head.digest()[:12]}, not the recorded {str(data['digest'])[:12]}: this "
                    "build reads it as a different design, so it is refused."
                )
            for revision in data["revisions"]:
                if DesignSpec.from_dict(revision["document"]).digest() != revision["digest"]:
                    raise ArchiveRefusal(
                        f"Revision {revision['revision_number']} of {data.get('name')!r} no "
                        "longer matches its recorded digest, so it is refused."
                    )
        except (SpecError, KeyError, TypeError) as exc:
            raise ArchiveRefusal(
                f"A design in this archive cannot be read: {exc}. Nothing was imported."
            ) from None
        loaded.append((data, head))
    return loaded


def verify(path: Path) -> dict[str, Any]:
    """Run steps 1 and 2 only, and return the manifest. Raises `ArchiveRefusal`."""
    try:
        with zipfile.ZipFile(path) as archive:
            _check_names(archive.namelist())
            manifest = _read_manifest(archive)
            _verify_members(archive, manifest)
            return manifest
    except zipfile.BadZipFile:
        raise ArchiveRefusal("This file is not a zip archive.") from None


def import_archive(
    db: Session,
    media: MediaService,
    user: User,
    path: Path,
    *,
    organisation_id: str | None = None,
    name: str | None = None,
) -> Imported:
    """Verify `path`, then create the project it holds. The caller commits or rolls back."""
    stored = []
    try:
        with zipfile.ZipFile(path) as archive:
            _check_names(archive.namelist())
            manifest = _read_manifest(archive)
            _verify_members(archive, manifest)
            designs = _load_designs(archive, manifest)

            described = manifest.get("project") or {}
            project = Project(
                name=(name or described.get("name") or "Imported project")[:255],
                description=described.get("description"),
                owner_id=user.id,
                tags=projects.normalise_tags(list(described.get("tags") or [])),
                template_key=described.get("template_key"),
            )
            if organisation_id is not None:
                project.organisation_id = organisation_id
            db.add(project)
            db.flush()

            count = 0
            for entry in manifest.get("geometry") or []:
                file_format = detect_format(entry["filename"]) or entry["file_format"]
                with archive.open(entry["file"]) as source:
                    row = media.store_stream(
                        owner_id=user.id,
                        kind=MediaKind.CAD,
                        filename=entry["filename"],
                        stream=source,  # type: ignore[arg-type]  # ZipExtFile reads like BinaryIO
                    )
                stored.append(row)
                try:
                    stats = inspect(media.local_path(row), file_format)
                except GeometryError as exc:
                    raise ArchiveRefusal(
                        f"The geometry file {entry['filename']!r} in this archive cannot be "
                        f"read: {exc}"
                    ) from None
                db.add(
                    GeometryVersion(
                        project_id=project.id,
                        media_id=row.id,
                        version_number=int(entry["version_number"]),
                        filename=row.filename,
                        file_format=file_format,
                        note=entry.get("note"),
                        stats=stats,
                    )
                )
                count += 1

            for data, head in designs:
                conversation = Conversation(
                    owner_id=user.id,
                    project_id=project.id,
                    title=f"{head.name} (imported design)"[:255],
                )
                db.add(conversation)
                db.flush()
                document = DesignDocument(
                    conversation_id=conversation.id,
                    project_id=project.id,
                    name=head.name,
                    digest=data["digest"],
                    revision_number=int(data["revision_number"]),
                    document=data["document"],
                )
                db.add(document)
                db.flush()
                for revision in data["revisions"]:
                    db.add(
                        DesignRevision(
                            design_id=document.id,
                            revision_number=int(revision["revision_number"]),
                            digest=revision["digest"],
                            document=revision["document"],
                            summary=revision.get("summary") or "",
                            author=revision.get("author") or "user",
                            author_id=None,
                        )
                    )
            db.flush()
            return Imported(project=project, geometry_versions=count, designs=len(designs))
    except zipfile.BadZipFile:
        _drop(media, stored)
        raise ArchiveRefusal("This file is not a zip archive.") from None
    except BaseException:
        _drop(media, stored)
        raise


def _drop(media: MediaService, stored: list[Any]) -> None:
    for row in stored:
        try:
            media.delete(row)
        except Exception:  # noqa: BLE001 -- cleanup must not mask the refusal that caused it
            pass
