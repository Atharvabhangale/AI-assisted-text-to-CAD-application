"""Backend-independent representation and publication of derived artifacts.

Three things this module keeps apart, because conflating them is how an
artifact layer becomes tied to one machine:

======================  ==================================================
**identity**            :attr:`Artifact.logical_id` -- reproducible from the
                        build alone: ``<build key>:<kind>``
**content**             :attr:`Artifact.checksum`, :attr:`Artifact.size_bytes`
                        -- facts about *this* produced file
**physical location**   :attr:`Artifact.path` -- where this process happens to
                        have put it
======================  ==================================================

```
Build
  |
ArtifactManifest  (document_hash, build_key, artifacts[])
  |
Artifact
  |-- logical identity      <build key>:<kind>
  |-- kind / format         geometry | step | iges | stl | render
  |-- source document       the canonical CAD document's SHA-256
  |-- build                 the build key
  |-- size, checksum        content facts, file-backed artifacts (and render)
  +-- path                  physical, machine-specific, never identity
```

Nothing here knows about a filesystem *layout*: an artifact is told its path,
never asked to invent one. There is **no** storage service, cache, database or
remote backend, and none is implied -- a ``pathlib.Path`` is the whole of the
physical side for now.

Identity versus content
-----------------------
This distinction is the point of the module, and the two must never be merged:

* :attr:`Artifact.logical_id` is **deterministic**: the same canonical CAD
  document built with the same options always yields the same logical ids, on
  any machine, at any time. It reuses the Stage 16 build key -- no second hash
  scheme is invented for identity.
* :attr:`Artifact.checksum` is the SHA-256 of the bytes *this run* produced.
  For STEP and IGES those bytes are **not** reproducible: earlier stages
  measured a timestamp and an incrementing translator counter in their
  headers. So a checksum is content metadata and is deliberately excluded
  from :meth:`ArtifactManifest.canonical`, which is why the canonical manifest
  stays byte-deterministic while STEP bytes do not.

A future cache can use both concepts -- identity to look an artifact up,
checksum to know whether the bytes it holds are the ones it recorded. Nothing
here caches anything.

Logical format, not file extension
----------------------------------
``.step`` and ``.stp`` are two file representations of one logical output, and
so are ``.igs`` and ``.iges``. Identity follows the **logical** output:
:data:`ArtifactKind`. Two STEP files of the same build written with different
extensions therefore share a logical id and differ only in content -- path,
extension, size, checksum. The exact extension is recorded in
:attr:`Artifact.file_extension`, as content, not identity.

The alternative (extension in identity) was rejected because it would make
``build --step`` mean two different artifacts depending on a filename, which
no build option can express.

Checksums
---------
:data:`CHECKSUM_ALGORITHM` is SHA-256 throughout.

* **file-backed** -- computed from the file's actual bytes, streamed from
  disk, only *after* the write has completed. Never from the document JSON,
  the geometry, the build key or the filename.
* **render** -- computed from :func:`canonical_render_bytes`: the existing
  :meth:`~cad_core.render_model.RenderModel.to_dict` serialized with the
  canonical document's own JSON conventions plus ``sort_keys=True``. No second
  render serialization is introduced; this is the one definition, and the
  render model was measured deterministic in Stage 8.
* **geometry** -- **none**. A B-rep has no canonical byte representation here,
  so no checksum is fabricated. The geometry artifact carries measurements
  only, and :attr:`Artifact.checksum` and :attr:`Artifact.size_bytes` are both
  ``None``.

Publication
-----------
An artifact does not exist until it is publishable. For a file that means the
write returned, the file is present, its size is greater than zero and its
checksum computed -- and, for STL, that the file is structurally consistent
with its own declared triangle count. For an in-memory artifact it means the
object was constructed. See :func:`publish_file_artifact`.

A build that fails publishes nothing: the manifest lists only artifacts that
passed those checks, and the build layer deletes files it wrote before
failing. That is Stage 16's behaviour, unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

from cad_core.iges_export import IGES_EXTENSIONS
from cad_core.local_cad import LocalCadResult
from cad_core.render_model import (
    RENDER_FORMAT_VERSION,
    RenderBounds,
    RenderModel,
    TessellationSettings,
)
from cad_core.serialization import CANONICAL_ENCODING, CANONICAL_SEPARATORS
from cad_core.step_export import STEP_EXTENSIONS
from cad_core.stl_export import STL_EXTENSIONS, binary_stl_facts

PathLike = Union[str, "os.PathLike[str]"]

#: Hash used for every artifact checksum, from :mod:`hashlib`.
CHECKSUM_ALGORITHM = "sha256"

#: Bytes read per chunk when checksumming a file.
_CHECKSUM_CHUNK_BYTES = 1 << 16


class ArtifactKind(Enum):
    """The logical type of one derived artifact.

    Exactly the outputs the project already produces. This is the *logical*
    output, not a file extension: see the module docstring.
    """

    #: The local B-rep solid. In memory; measurements only, no checksum.
    GEOMETRY = "geometry"

    #: A STEP file (``.step`` or ``.stp``).
    STEP = "step"

    #: An IGES file in BRep mode (``.igs`` or ``.iges``).
    IGES = "iges"

    #: A binary STL file (``.stl``).
    STL = "stl"

    #: The neutral render representation. In memory, with a canonical-JSON
    #: checksum.
    RENDER = "render"


class ArtifactStorage(Enum):
    """Where an artifact's payload lives."""

    #: Written to the filesystem; has a path, a size and a checksum.
    FILE = "file"

    #: Held in the process; has no path.
    IN_MEMORY = "in_memory"


#: Storage per kind.
KIND_STORAGE: Mapping[ArtifactKind, ArtifactStorage] = {
    ArtifactKind.GEOMETRY: ArtifactStorage.IN_MEMORY,
    ArtifactKind.STEP: ArtifactStorage.FILE,
    ArtifactKind.IGES: ArtifactStorage.FILE,
    ArtifactKind.STL: ArtifactStorage.FILE,
    ArtifactKind.RENDER: ArtifactStorage.IN_MEMORY,
}

#: Kinds written to the filesystem, in canonical order.
FILE_KINDS: Tuple[ArtifactKind, ...] = tuple(
    kind for kind in ArtifactKind if KIND_STORAGE[kind] is ArtifactStorage.FILE
)

#: Kinds that stay in the process, in canonical order.
IN_MEMORY_KINDS: Tuple[ArtifactKind, ...] = tuple(
    kind for kind in ArtifactKind if KIND_STORAGE[kind] is ArtifactStorage.IN_MEMORY
)

#: File extensions each file kind may legitimately carry. Taken from the
#: exporters themselves, so this table cannot drift from what they accept.
#: Several extensions per kind is exactly why identity is the *kind*.
KIND_EXTENSIONS: Mapping[ArtifactKind, Tuple[str, ...]] = {
    ArtifactKind.STEP: STEP_EXTENSIONS,
    ArtifactKind.IGES: IGES_EXTENSIONS,
    ArtifactKind.STL: STL_EXTENSIONS,
}

#: What each artifact's ``format`` says: a description of the payload's shape,
#: not a MIME type and not a version.
KIND_FORMATS: Mapping[ArtifactKind, str] = {
    ArtifactKind.GEOMETRY: "brep-in-memory",
    ArtifactKind.STEP: "step",
    ArtifactKind.IGES: "iges-brep",
    ArtifactKind.STL: "stl-binary",
    ArtifactKind.RENDER: "render-model",
}

#: Default file extension per file kind, used when a caller wants one.
KIND_DEFAULT_EXTENSION: Mapping[ArtifactKind, str] = {
    kind: extensions[0] for kind, extensions in KIND_EXTENSIONS.items()
}


class ArtifactError(Exception):
    """Base class for artifact-layer failures."""


class ArtifactPublicationError(ArtifactError):
    """Raised when an artifact cannot be published.

    Publication is the gate between "the exporter returned" and "this exists":
    a missing file, an empty file, an extension the kind does not use, or an
    STL whose bytes disagree with its own triangle count.
    """


def artifact_logical_id(build_key: str, kind: ArtifactKind) -> str:
    """Return the reproducible identity of one artifact of one build.

    ``<build key>:<kind>`` -- and nothing else. Not a path, not a filename,
    not an execution id, not a timestamp, not a file checksum. The build key
    already folds in the canonical CAD document's hash and the canonical build
    options (Stage 16), so this needs no hash scheme of its own.
    """
    if not isinstance(kind, ArtifactKind):
        raise ArtifactError(f"kind must be an ArtifactKind; got {type(kind).__name__}")
    return f"{build_key}:{kind.value}"


@dataclass(frozen=True)
class Artifact:
    """One derived artifact: identity, content facts and a physical location.

    ``details`` holds deterministic, JSON-compatible measurements -- never a
    CAD kernel object, never a traceback, never anything from the environment,
    and never a full payload (the render model is described, not duplicated).
    """

    kind: ArtifactKind
    format: str
    document_hash: str
    build_key: str
    storage: ArtifactStorage

    #: Physical location, file-backed artifacts only. Machine-specific, and
    #: never part of identity.
    path: Optional[str] = None

    #: Exact extension of the produced file. Content, not identity: ``.step``
    #: and ``.stp`` are the same logical artifact.
    file_extension: Optional[str] = None

    #: Byte size. The file's real size for a file artifact, the canonical
    #: byte length for the render model, ``None`` for the B-rep.
    size_bytes: Optional[int] = None

    #: SHA-256 of the artifact's bytes. ``None`` for the B-rep, which has no
    #: canonical byte representation.
    checksum: Optional[str] = None

    #: Deterministic measurements of the payload.
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def logical_id(self) -> str:
        """Reproducible identity: ``<build key>:<kind>``."""
        return artifact_logical_id(self.build_key, self.kind)

    @property
    def in_memory(self) -> bool:
        return self.storage is ArtifactStorage.IN_MEMORY

    @property
    def output(self) -> ArtifactKind:
        """Compatibility alias for :attr:`kind` (Stage 16 name)."""
        return self.kind

    def to_dict(self) -> Dict[str, Any]:
        """Full JSON-compatible report: identity, content and location."""
        return {
            "kind": self.kind.value,
            "output": self.kind.value,
            "format": self.format,
            "storage": self.storage.value,
            "logical_id": self.logical_id,
            "document_hash": self.document_hash,
            "build_key": self.build_key,
            "path": self.path,
            "file_extension": self.file_extension,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "checksum_algorithm": (
                CHECKSUM_ALGORITHM if self.checksum is not None else None
            ),
            "details": dict(self.details),
        }

    def canonical(self) -> Dict[str, Any]:
        """Identity and deterministic properties only.

        Excludes the physical path, the byte size and the content checksum:
        those describe one produced file, and for STEP and IGES they are not
        reproducible between runs. What remains is identical for every build
        of the same document with the same options.
        """
        return {
            "logical_id": self.logical_id,
            "kind": self.kind.value,
            "format": self.format,
            "storage": self.storage.value,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ArtifactManifest:
    """Every artifact one successful build published.

    Ordering is the canonical :class:`ArtifactKind` order -- the same
    convention :class:`~cad_core.build_job.BuildOptions` already canonicalizes
    its output set with -- so a manifest does not depend on the order a caller
    happened to request outputs in. The constructor sorts, so the invariant
    cannot be bypassed.
    """

    document_hash: str
    build_key: str
    artifacts: Tuple[Artifact, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(self.artifacts, key=lambda item: _KIND_ORDER[item.kind])
        )
        object.__setattr__(self, "artifacts", ordered)
        seen = [artifact.kind for artifact in ordered]
        if len(set(seen)) != len(seen):
            raise ArtifactError(
                "a manifest holds at most one artifact per kind; got "
                + ", ".join(kind.value for kind in seen)
            )
        for artifact in ordered:
            if artifact.build_key != self.build_key:
                raise ArtifactError(
                    f"artifact {artifact.logical_id} belongs to another build"
                )
            if artifact.document_hash != self.document_hash:
                raise ArtifactError(
                    f"artifact {artifact.logical_id} names another document"
                )

    def kinds(self) -> Tuple[ArtifactKind, ...]:
        return tuple(artifact.kind for artifact in self.artifacts)

    def artifact(self, kind: ArtifactKind) -> Optional[Artifact]:
        for artifact in self.artifacts:
            if artifact.kind is kind:
                return artifact
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Full JSON-compatible report, including paths and checksums."""
        return {
            "document_hash": self.document_hash,
            "build_key": self.build_key,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    def canonical(self) -> Dict[str, Any]:
        """The reproducible manifest: identity and deterministic properties.

        No filesystem path, no size, no content checksum. Two successful builds
        of the same document with the same options produce identical canonical
        manifests, on any machine.
        """
        return {
            "document_hash": self.document_hash,
            "build_key": self.build_key,
            "artifacts": [artifact.canonical() for artifact in self.artifacts],
        }

    def canonical_bytes(self) -> bytes:
        """The canonical manifest as deterministic UTF-8 JSON.

        Uses the canonical document's own conventions -- minimal separators,
        ``ensure_ascii=False`` -- plus ``sort_keys=True``, so key order cannot
        depend on construction order.
        """
        return _canonical_json(self.canonical())

    def canonical_hash(self) -> str:
        """SHA-256 of :meth:`canonical_bytes`.

        A convenience for comparing two manifests; it is *not* an artifact
        identity and nothing keys on it.
        """
        return hashlib.new(CHECKSUM_ALGORITHM, self.canonical_bytes()).hexdigest()


_KIND_ORDER: Mapping[ArtifactKind, int] = {
    kind: index for index, kind in enumerate(ArtifactKind)
}


# --- checksums --------------------------------------------------------------


def file_checksum(path: PathLike) -> str:
    """Return the SHA-256 of a file's actual bytes, streamed from disk.

    Not the document JSON, not the geometry, not the build key, not the
    filename -- the bytes.
    """
    digest = hashlib.new(CHECKSUM_ALGORITHM)
    with open(os.fspath(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHECKSUM_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_render_bytes(model: RenderModel) -> bytes:
    """Return the canonical UTF-8 bytes of a render model.

    ``model.to_dict()`` is the render model's own existing serialization
    (Stage 8); this adds only the canonical JSON conventions used for the CAD
    document -- minimal separators, ``ensure_ascii=False`` -- plus
    ``sort_keys=True``. No second render serialization is introduced.
    """
    if not isinstance(model, RenderModel):
        raise ArtifactError(
            f"expected a RenderModel; got {type(model).__name__}"
        )
    return _canonical_json(model.to_dict())


def render_checksum(model: RenderModel) -> str:
    """SHA-256 of :func:`canonical_render_bytes`."""
    return hashlib.new(CHECKSUM_ALGORITHM, canonical_render_bytes(model)).hexdigest()


def render_model_from_canonical_bytes(payload: bytes) -> RenderModel:
    """Rebuild a :class:`RenderModel` from :func:`canonical_render_bytes`.

    The exact inverse of :func:`canonical_render_bytes`, and deliberately
    defined beside it so the two directions of the render model's byte form
    cannot drift. **No new serialization format is introduced**: this reads
    the render model's own existing ``to_dict()`` structure (Stage 8), and a
    round trip is byte-identical.

    Strict, in the way :func:`cad_core.serialization.deserialize_part` is
    strict: an unknown or missing field is an error rather than something
    silently dropped, so a future field added to
    :meth:`~cad_core.render_model.RenderModel.to_dict` fails loudly here
    instead of being lost. ``format_version`` must be the one this build of
    the project produces -- an older payload is refused, not guessed at.

    Raises:
        ArtifactError: if the bytes are not the canonical JSON of a render
            model of the current format version.
    """
    try:
        raw = json.loads(bytes(payload).decode(CANONICAL_ENCODING))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArtifactError(
            f"the render payload is not canonical render JSON ({exc})"
        ) from exc
    if not isinstance(raw, dict):
        raise ArtifactError("the render payload is not a JSON object")
    if set(raw) != set(_RENDER_FIELDS):
        missing = sorted(set(_RENDER_FIELDS) - set(raw))
        unknown = sorted(set(raw) - set(_RENDER_FIELDS))
        raise ArtifactError(
            "the render payload's fields are not a render model's: "
            f"missing {missing}, unknown {unknown}"
        )
    if raw["format_version"] != RENDER_FORMAT_VERSION:
        raise ArtifactError(
            f"the render payload is format version {raw['format_version']!r}; "
            f"this build produces {RENDER_FORMAT_VERSION!r}"
        )
    bounds = raw["bounds"]
    tessellation = raw["tessellation"]
    if not isinstance(bounds, dict) or set(bounds) != {"minimum", "maximum", "size"}:
        raise ArtifactError("the render payload's bounds are malformed")
    if not isinstance(tessellation, dict) or set(tessellation) != {
        "linear_deflection_mm",
        "angular_deflection_rad",
    }:
        raise ArtifactError("the render payload's tessellation is malformed")
    minimum = _triple(bounds["minimum"], "bounds.minimum")
    maximum = _triple(bounds["maximum"], "bounds.maximum")
    recorded_size = _triple(bounds["size"], "bounds.size")
    derived = tuple(high - low for low, high in zip(minimum, maximum))
    if recorded_size != derived:
        # ``size`` is derived from the corners, so a payload where they
        # disagree is not a render model this project wrote.
        raise ArtifactError("the render payload's bounds are inconsistent")
    model = RenderModel(
        format_version=raw["format_version"],
        part_name=_text(raw["part_name"], "part_name"),
        feature_id=_text(raw["feature_id"], "feature_id"),
        units=_text(raw["units"], "units"),
        coordinate_system=_text(raw["coordinate_system"], "coordinate_system"),
        winding=_text(raw["winding"], "winding"),
        normal_binding=_text(raw["normal_binding"], "normal_binding"),
        vertices=tuple(
            _triple(vertex, "vertices") for vertex in _sequence(raw["vertices"])
        ),
        triangles=tuple(
            _indices(triangle) for triangle in _sequence(raw["triangles"])
        ),
        normals=tuple(
            _triple(normal, "normals") for normal in _sequence(raw["normals"])
        ),
        bounds=RenderBounds(minimum=minimum, maximum=maximum),
        tessellation=TessellationSettings(
            linear_deflection_mm=_number(
                tessellation["linear_deflection_mm"], "linear_deflection_mm"
            ),
            angular_deflection_rad=_number(
                tessellation["angular_deflection_rad"], "angular_deflection_rad"
            ),
        ),
    )
    return model


# --- publication ------------------------------------------------------------


def publish_file_artifact(
    *,
    document_hash: str,
    build_key: str,
    kind: ArtifactKind,
    path: PathLike,
    details: Optional[Mapping[str, Any]] = None,
) -> Artifact:
    """Verify a written file and return its :class:`Artifact`.

    Call this only **after** the exporter has returned. The order is fixed:
    check the file exists, take its size, compute its checksum from the bytes
    on disk, then build the record -- so no artifact can exist with a checksum
    taken before the write finished.

    Raises:
        ArtifactPublicationError: if the kind is not file-backed, the
            extension is not one the exporter uses for that kind, the file is
            missing or empty, or an STL's bytes disagree with its own declared
            triangle count.
    """
    if KIND_STORAGE[kind] is not ArtifactStorage.FILE:
        raise ArtifactPublicationError(
            f"{kind.value} is not a file-backed artifact"
        )
    location = Path(os.fspath(path))
    extension = location.suffix.lower()
    permitted = KIND_EXTENSIONS[kind]
    if extension not in permitted:
        allowed = ", ".join(repr(item) for item in permitted)
        raise ArtifactPublicationError(
            f"a {kind.value} artifact is written as {allowed}; got "
            f"{extension!r}"
        )
    if not location.is_file():
        raise ArtifactPublicationError(
            f"the {kind.value} artifact was not written"
        )
    size = location.stat().st_size
    if size <= 0:
        raise ArtifactPublicationError(
            f"the {kind.value} artifact is empty"
        )

    measured: Dict[str, Any] = dict(details or {})
    if kind is ArtifactKind.STL:
        facts = binary_stl_facts(location)
        if not facts.is_structurally_consistent:
            raise ArtifactPublicationError(
                "the stl artifact's size does not match its declared "
                "triangle count"
            )
        measured.setdefault("triangle_count", facts.declared_triangles)
        measured.setdefault("is_structurally_consistent", True)

    checksum = file_checksum(location)
    return Artifact(
        kind=kind,
        format=KIND_FORMATS[kind],
        document_hash=document_hash,
        build_key=build_key,
        storage=ArtifactStorage.FILE,
        path=str(location),
        file_extension=extension,
        size_bytes=size,
        checksum=checksum,
        details=measured,
    )


def publish_geometry_artifact(
    *, document_hash: str, build_key: str, result: LocalCadResult
) -> Artifact:
    """Return the geometry artifact for a built B-rep.

    Measurements only. There is no canonical byte representation of a B-rep
    here, so both :attr:`Artifact.checksum` and :attr:`Artifact.size_bytes`
    are ``None`` -- no binary checksum is invented, and object memory is not
    passed off as artifact size. No kernel object reaches the record.
    """
    if not isinstance(result, LocalCadResult):
        raise ArtifactError(
            f"expected a LocalCadResult; got {type(result).__name__}"
        )
    box = result.bounding_box()
    shape = result.shape
    return Artifact(
        kind=ArtifactKind.GEOMETRY,
        format=KIND_FORMATS[ArtifactKind.GEOMETRY],
        document_hash=document_hash,
        build_key=build_key,
        storage=ArtifactStorage.IN_MEMORY,
        path=None,
        file_extension=None,
        size_bytes=None,
        checksum=None,
        details={
            "part_name": result.part_name,
            "feature_id": result.feature_id,
            "is_solid": result.is_solid(),
            "solid_count": result.solid_count(),
            "volume_mm3": result.volume(),
            "bounding_box": {
                "minimum": _vector(box.minimum),
                "maximum": _vector(box.maximum),
                "size": _vector(box.size),
            },
            "face_count": len(shape.Faces()),
            "edge_count": len(shape.Edges()),
            "vertex_count": len(shape.Vertices()),
        },
    )


def publish_render_artifact(
    *, document_hash: str, build_key: str, model: RenderModel
) -> Artifact:
    """Return the render artifact for a built render model.

    Metadata plus a canonical-JSON checksum and the length of those canonical
    bytes. The model itself is **not** duplicated here -- the build result
    holds it as a reference.
    """
    payload = canonical_render_bytes(model)
    return Artifact(
        kind=ArtifactKind.RENDER,
        format=KIND_FORMATS[ArtifactKind.RENDER],
        document_hash=document_hash,
        build_key=build_key,
        storage=ArtifactStorage.IN_MEMORY,
        path=None,
        file_extension=None,
        size_bytes=len(payload),
        checksum=hashlib.new(CHECKSUM_ALGORITHM, payload).hexdigest(),
        details={
            "format_version": model.format_version,
            "part_name": model.part_name,
            "feature_id": model.feature_id,
            "units": model.units,
            "coordinate_system": model.coordinate_system,
            "winding": model.winding,
            "normal_binding": model.normal_binding,
            "vertex_count": model.vertex_count(),
            "triangle_count": model.triangle_count(),
            "bounds": {
                "minimum": list(model.bounds.minimum),
                "maximum": list(model.bounds.maximum),
            },
            "tessellation": model.tessellation.to_dict(),
        },
    )


def build_manifest(
    *, document_hash: str, build_key: str, artifacts: Iterable[Artifact]
) -> ArtifactManifest:
    """Assemble a manifest, ordered canonically and checked for consistency."""
    return ArtifactManifest(
        document_hash=document_hash,
        build_key=build_key,
        artifacts=tuple(artifacts),
    )


# --- reading a manifest back ------------------------------------------------


def artifact_from_dict(record: Mapping[str, Any]) -> Artifact:
    """Rebuild an :class:`Artifact` from :meth:`Artifact.to_dict`.

    The exact inverse, defined beside it so the two directions cannot drift.
    Needed wherever a manifest crosses a boundary a Python object cannot --
    :mod:`cad_core.isolated_execution` carries one between processes.

    Strict, like :func:`cad_core.serialization.deserialize_part`: the record's
    fields must be exactly the ones :meth:`Artifact.to_dict` writes, and the
    identity, format, storage kind and checksum algorithm it claims must be
    the ones this layer defines for that kind. **Nothing is repaired and
    nothing is inferred** -- a record that disagrees with itself is an error.

    This reader does not touch the filesystem: it does not check that a
    file-backed artifact's path exists, and it recomputes no checksum. A
    caller that received the record from somewhere it does not control should
    verify the bytes itself.

    Raises:
        ArtifactError: if the record is not one this layer wrote.
    """
    if not isinstance(record, Mapping):
        raise ArtifactError(
            f"an artifact record must be a mapping; got {type(record).__name__}"
        )
    if set(record) != set(_ARTIFACT_RECORD_FIELDS):
        missing = sorted(set(_ARTIFACT_RECORD_FIELDS) - set(record))
        unknown = sorted(set(record) - set(_ARTIFACT_RECORD_FIELDS))
        raise ArtifactError(
            f"an artifact record's fields are not an Artifact's: missing "
            f"{missing}, unknown {unknown}"
        )
    try:
        kind = ArtifactKind(record["kind"])
    except ValueError:
        raise ArtifactError(f"unknown artifact kind {record['kind']!r}") from None
    if record["output"] != record["kind"]:
        raise ArtifactError(
            "an artifact record's 'output' and 'kind' disagree"
        )
    if record["format"] != KIND_FORMATS[kind]:
        raise ArtifactError(
            f"a {kind.value} artifact's format is {KIND_FORMATS[kind]!r}; the "
            f"record says {record['format']!r}"
        )
    if record["storage"] != KIND_STORAGE[kind].value:
        raise ArtifactError(
            f"a {kind.value} artifact is {KIND_STORAGE[kind].value}; the "
            f"record says {record['storage']!r}"
        )
    document_hash = record["document_hash"]
    build_key = record["build_key"]
    if record["logical_id"] != artifact_logical_id(build_key, kind):
        raise ArtifactError(
            "an artifact record's logical id is not its own build's"
        )
    checksum = record["checksum"]
    algorithm = record["checksum_algorithm"]
    if checksum is None and algorithm is not None:
        raise ArtifactError(
            f"the {kind.value} record names a checksum algorithm but no checksum"
        )
    if checksum is not None and algorithm != CHECKSUM_ALGORITHM:
        raise ArtifactError(
            f"a checksum is {CHECKSUM_ALGORITHM}; the {kind.value} record "
            f"says {algorithm!r}"
        )
    extension = record["file_extension"]
    if extension is not None and extension not in KIND_EXTENSIONS.get(kind, ()):
        raise ArtifactError(
            f"a {kind.value} artifact is not written as {extension!r}"
        )
    details = record["details"]
    if not isinstance(details, Mapping):
        raise ArtifactError(f"the {kind.value} record's details are not a mapping")
    return Artifact(
        kind=kind,
        format=record["format"],
        document_hash=document_hash,
        build_key=build_key,
        storage=KIND_STORAGE[kind],
        path=record["path"],
        file_extension=extension,
        size_bytes=record["size_bytes"],
        checksum=checksum,
        details=dict(details),
    )


def manifest_from_dict(payload: Mapping[str, Any]) -> ArtifactManifest:
    """Rebuild an :class:`ArtifactManifest` from :meth:`ArtifactManifest.to_dict`.

    Strict in the same way as :func:`artifact_from_dict`, and the manifest's
    own consistency checks then apply: one artifact per kind, and every
    artifact naming this manifest's build and document.
    """
    if not isinstance(payload, Mapping):
        raise ArtifactError(
            f"a manifest must be a mapping; got {type(payload).__name__}"
        )
    if set(payload) != {"document_hash", "build_key", "artifacts"}:
        raise ArtifactError(
            "a manifest's fields are document_hash, build_key, artifacts"
        )
    records = payload["artifacts"]
    if not isinstance(records, (list, tuple)):
        raise ArtifactError("a manifest's artifacts are a list")
    return ArtifactManifest(
        document_hash=payload["document_hash"],
        build_key=payload["build_key"],
        artifacts=tuple(artifact_from_dict(record) for record in records),
    )


# --- internals --------------------------------------------------------------


def _canonical_json(structure: Mapping[str, Any]) -> bytes:
    return json.dumps(
        structure,
        separators=CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    ).encode(CANONICAL_ENCODING)


def _vector(value: Any) -> Dict[str, float]:
    return {"x": value.x, "y": value.y, "z": value.z}


#: The keys :meth:`Artifact.to_dict` writes. Read back strictly, so a new
#: field cannot be silently dropped by the reader.
_ARTIFACT_RECORD_FIELDS: Tuple[str, ...] = (
    "kind",
    "output",
    "format",
    "storage",
    "logical_id",
    "document_hash",
    "build_key",
    "path",
    "file_extension",
    "size_bytes",
    "checksum",
    "checksum_algorithm",
    "details",
)


#: The keys :meth:`RenderModel.to_dict` writes. Read back strictly, so a new
#: field cannot be silently dropped by the reader.
_RENDER_FIELDS: Tuple[str, ...] = (
    "format_version",
    "part_name",
    "feature_id",
    "units",
    "coordinate_system",
    "winding",
    "normal_binding",
    "vertices",
    "triangles",
    "normals",
    "bounds",
    "tessellation",
)


def _sequence(value: Any) -> Tuple[Any, ...]:
    if not isinstance(value, list):
        raise ArtifactError("the render payload holds a malformed list")
    return tuple(value)


def _triple(value: Any, field_name: str) -> Tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ArtifactError(f"the render payload's {field_name} is malformed")
    return tuple(_number(item, field_name) for item in value)  # type: ignore[return-value]


def _indices(value: Any) -> Tuple[int, int, int]:
    if not isinstance(value, list) or len(value) != 3:
        raise ArtifactError("the render payload holds a malformed triangle")
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise ArtifactError("a render triangle index is not an integer")
    return (value[0], value[1], value[2])


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactError(f"the render payload's {field_name} is not a number")
    return value


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ArtifactError(f"the render payload's {field_name} is not a string")
    return value


__all__ = [
    "CHECKSUM_ALGORITHM",
    "FILE_KINDS",
    "IN_MEMORY_KINDS",
    "KIND_DEFAULT_EXTENSION",
    "KIND_EXTENSIONS",
    "KIND_FORMATS",
    "KIND_STORAGE",
    "Artifact",
    "ArtifactError",
    "ArtifactKind",
    "ArtifactManifest",
    "ArtifactPublicationError",
    "ArtifactStorage",
    "artifact_from_dict",
    "artifact_logical_id",
    "build_manifest",
    "canonical_render_bytes",
    "file_checksum",
    "manifest_from_dict",
    "publish_file_artifact",
    "publish_geometry_artifact",
    "publish_render_artifact",
    "render_checksum",
    "render_model_from_canonical_bytes",
]
