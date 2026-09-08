"""A deterministic local filesystem cache for successful build artifacts.

Infrastructure only. This is a directory of files on the machine the build ran
on: there is **no** database, object store, Redis, cloud backend, network
service, HTTP surface, queue or worker here, and none is implied.

```
CAD document -> BuildRequest -> BuildExecutor -> ArtifactManifest -> LocalBuildCache
```

One direction. The cache consumes a :class:`~cad_core.build_job.BuildJob`, a
:class:`~cad_core.build_job.BuildResult` and an
:class:`~cad_core.artifact_registry.ArtifactManifest`; it knows nothing about
CAD features, validation rules, exporters or the geometry kernel, and it never
modifies any of them.

The cache key
-------------
The key is the **existing build key** (Stage 16) and nothing else. No second
hash scheme is introduced: the build key already folds in the canonical CAD
document's SHA-256 and the canonical output selection, so two requests that
share a build key are the same build by construction. A lookup takes the build
key as its only identity; the document hash and the requested outputs are then
*verified* against the stored manifest rather than being part of the key.

Never identity: a timestamp, an execution id, a filesystem path, a filename, a
content checksum, or the cache root.

Layout
------
```
<cache_root>/
    entries/
        <build_key>/
            manifest.json
            artifacts/
                step.step
                iges.igs
                stl.stl
                render.json
    staging/
        <build_key>.<unique>/        scratch; never read back, never identity
```

The root is always supplied by the caller. This module never consults a home
directory, a temporary directory, the current working directory or any
environment variable -- it creates ``entries/`` and ``staging/`` inside the
root it was given and nothing else.

Payload filenames are ``<kind><extension>``, derived from the artifact's own
logical kind (identity) and the extension the build actually produced
(content), so a cached file's name never depends on the original build's
directory. The manifest records payload locations **relative** to the entry
directory, so an entry stays valid if the whole cache root is moved.

Validation: never trust the manifest
------------------------------------
A cache hit is only reported when, in order:

1. the entry directory exists;
2. ``manifest.json`` exists;
3. it parses as a JSON object;
4. its cache-schema version is the one this module writes;
5. its build key matches the requested build key;
6. its document hash matches the request's document hash;
7. the artifact list is internally consistent -- one artifact per kind, every
   logical id equal to ``<build key>:<kind>``, every payload path relative and
   inside the entry directory, and the whole list accepted by
   :func:`~cad_core.artifact_registry.build_manifest`;
8. every artifact that has a payload representation has its payload file
   present, and it passes the artifact layer's own publication gate;
9. each payload's size on disk equals the recorded size;
10. each payload's SHA-256, recomputed from the bytes on disk, equals the
    recorded checksum;
11. every requested output is present in the manifest.

Any failure is a **cache MISS** with a reason -- never a partial result, never
an exception out of :func:`get_or_build`, and never a repair of the bad entry
in place. A corrupt entry is replaced only by a complete, freshly built one.

Restoring an artifact re-runs the artifact layer's publication rules against
the cached bytes (:func:`~cad_core.artifact_registry.publish_file_artifact`),
so the identity and checksum rules live in exactly one place and the cached
bytes are re-verified on every hit.

Cached and non-cached artifacts
-------------------------------
=============  ==========================================================
``step``       cached; the hit returns the **cached bytes**, not a re-export
``iges``       cached; likewise
``stl``        cached; likewise
``render``     cached as its existing canonical JSON bytes (Stage 17's
               :func:`~cad_core.artifact_registry.canonical_render_bytes`),
               and the :class:`~cad_core.render_model.RenderModel` is
               reconstructed from them on a hit. No new serialization format.
``geometry``   **not cached.** A B-rep has no canonical byte representation
               here, and one is not invented. The geometry artifact's
               *measurements* are restored, which is all that artifact ever
               contained, but :attr:`~cad_core.build_job.BuildResult.geometry`
               is ``None`` on a hit. A caller that needs the live in-memory
               solid must rebuild.
=============  ==========================================================

Because STEP and IGES bytes are not reproducible between executions (their
headers carry a timestamp and a translator counter -- measured in Stages 5 and
6), the cache deliberately does **not** claim that caching makes them
deterministic. It records the exact bytes of the one build it cached, and a hit
returns those.

Publication
-----------
A new entry is staged under ``staging/`` and made visible by a single
directory rename, after the build has succeeded, every requested artifact has
been copied and re-verified, and the manifest has been written in full. So a
partially written entry is never visible under ``entries/``.

Measured on this platform (POSIX): renaming a directory onto an existing
non-empty directory fails with ``ENOTEMPTY``, which is what makes the
concurrent case safe -- see :meth:`LocalBuildCache.publish`. No
distributed or crash-proof atomicity is claimed; see the module's
documentation in ``docs/local-build-cache.md``.

Nothing is evicted. There is no TTL, no LRU and no size policy in this stage:
the cache grows without bound.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

from cad_core.artifact_registry import (
    CHECKSUM_ALGORITHM,
    KIND_FORMATS,
    KIND_STORAGE,
    Artifact,
    ArtifactError,
    ArtifactKind,
    ArtifactManifest,
    ArtifactStorage,
    artifact_logical_id,
    build_manifest,
    canonical_render_bytes,
    file_checksum,
    publish_file_artifact,
    publish_render_artifact,
    render_model_from_canonical_bytes,
)
from cad_core.build_job import (
    BuildJob,
    BuildOutput,
    BuildRequest,
    BuildResult,
    BuildStatus,
    execute_build,
)
from cad_core.render_model import RenderModel
from cad_core.serialization import CANONICAL_ENCODING, CANONICAL_SEPARATORS

PathLike = Union[str, "os.PathLike[str]"]

#: Version of the **cache entry file format** -- not of the CAD document, not
#: of the render model, and not any part of a build identity. It exists so a
#: manifest written by a different layout is a clean MISS rather than a
#: misreading. Bumping it invalidates cached entries; it never changes a build
#: key.
CACHE_SCHEMA_VERSION = "1.0.0"

#: Name of the persisted manifest inside a cache entry.
MANIFEST_FILENAME = "manifest.json"

#: Directory under the cache root holding published entries, one per build key.
ENTRIES_DIRNAME = "entries"

#: Directory under the cache root holding entries still being written.
STAGING_DIRNAME = "staging"

#: Directory inside an entry holding the cached payloads.
PAYLOAD_DIRNAME = "artifacts"

#: Extension of the cached render payload. The bytes are exactly
#: :func:`~cad_core.artifact_registry.canonical_render_bytes`, which is JSON.
RENDER_PAYLOAD_EXTENSION = ".json"

#: Artifact kinds the cache stores a payload for. ``GEOMETRY`` is absent by
#: design: no B-rep is ever serialized.
CACHED_PAYLOAD_KINDS: Tuple[ArtifactKind, ...] = (
    ArtifactKind.STEP,
    ArtifactKind.IGES,
    ArtifactKind.STL,
    ArtifactKind.RENDER,
)

#: Artifact kinds restored from recorded measurements only, with no payload.
METADATA_ONLY_KINDS: Tuple[ArtifactKind, ...] = (ArtifactKind.GEOMETRY,)

#: Bytes copied per chunk when a payload is written into the cache.
_COPY_CHUNK_BYTES = 1 << 16

#: Fields the persisted per-artifact record carries. Unknown or missing fields
#: are a MISS, never silently tolerated.
_ARTIFACT_RECORD_FIELDS: Tuple[str, ...] = (
    "kind",
    "format",
    "storage",
    "logical_id",
    "file_extension",
    "size_bytes",
    "checksum",
    "checksum_algorithm",
    "payload_path",
    "details",
)

#: Fields the persisted manifest carries.
_MANIFEST_FIELDS: Tuple[str, ...] = (
    "cache_schema_version",
    "document_hash",
    "build_key",
    "artifacts",
)


class CacheError(Exception):
    """Base class for cache-layer failures."""


class CachePublicationError(CacheError):
    """Raised when a build cannot be published to the cache.

    Publication is refused rather than half-completed: a failed build, a
    missing artifact payload, or a checksum that disagrees with the bytes
    actually copied into the cache.
    """


class CacheMissReason(Enum):
    """Why a lookup was not a hit. Every value is a MISS, not an error."""

    #: No directory for this build key.
    NO_ENTRY = "no_entry"

    #: The entry directory exists but holds no manifest.
    NO_MANIFEST = "no_manifest"

    #: The manifest is not readable JSON, or not a JSON object, or its fields
    #: are not the ones this module writes.
    MANIFEST_UNREADABLE = "manifest_unreadable"

    #: The manifest was written by a different cache layout.
    SCHEMA_MISMATCH = "schema_mismatch"

    #: The manifest names a different build key.
    BUILD_KEY_MISMATCH = "build_key_mismatch"

    #: The manifest names a different CAD document.
    DOCUMENT_MISMATCH = "document_mismatch"

    #: The artifact list contradicts itself, or a payload path points outside
    #: the entry.
    MANIFEST_INCONSISTENT = "manifest_inconsistent"

    #: A recorded payload file is absent, empty, or refused by the artifact
    #: layer's publication rules.
    PAYLOAD_MISSING = "payload_missing"

    #: A payload's size on disk disagrees with the recorded size.
    SIZE_MISMATCH = "size_mismatch"

    #: A payload's recomputed SHA-256 disagrees with the recorded checksum.
    CHECKSUM_MISMATCH = "checksum_mismatch"

    #: A requested output is not in the manifest.
    OUTPUT_MISSING = "output_missing"


@dataclass(frozen=True)
class CacheEntry:
    """A validated cache entry, ready to be returned as a hit.

    Every artifact in :attr:`manifest` has been re-verified against the bytes
    in the cache: this is not a transcription of the stored manifest.
    """

    build_key: str
    document_hash: str
    directory: str
    manifest: ArtifactManifest

    #: Reconstructed from the cached canonical JSON when ``render`` is in the
    #: entry; ``None`` otherwise.
    render_model: Optional[RenderModel] = None

    @property
    def restores_geometry(self) -> bool:
        """Always ``False``: the in-memory B-rep is never cached."""
        return False

    def kinds(self) -> Tuple[ArtifactKind, ...]:
        return self.manifest.kinds()

    def artifact(self, kind: ArtifactKind) -> Optional[Artifact]:
        return self.manifest.artifact(kind)


@dataclass(frozen=True)
class CacheLookup:
    """The outcome of one lookup: a hit with an entry, or a reason it missed."""

    build_key: str
    hit: bool
    entry: Optional[CacheEntry] = None
    reason: Optional[CacheMissReason] = None

    #: Human-readable detail for diagnosis. Not a contract, and never shown as
    #: a build's public error message.
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "build_key": self.build_key,
            "hit": self.hit,
            "reason": self.reason.value if self.reason is not None else None,
            "kinds": (
                [kind.value for kind in self.entry.kinds()]
                if self.entry is not None
                else []
            ),
        }


class LocalBuildCache:
    """A cache of successful build artifacts under one caller-supplied root.

    The root must already exist: this class never invents a location. It
    creates only ``entries/`` and ``staging/`` inside the root it was given.
    """

    def __init__(self, root: PathLike) -> None:
        location = Path(os.fspath(root))
        if not location.is_dir():
            raise CacheError(
                f"{location} is not a directory; a cache root is always "
                "supplied by the caller and is never invented"
            )
        self._root = location

    @property
    def root(self) -> Path:
        return self._root

    @property
    def entries_directory(self) -> Path:
        return self._root / ENTRIES_DIRNAME

    @property
    def staging_directory(self) -> Path:
        return self._root / STAGING_DIRNAME

    def entry_directory(self, build_key: str) -> Path:
        """Where the entry for ``build_key`` lives. The key is the whole name."""
        _require_key(build_key)
        return self.entries_directory / build_key

    # --- lookup -----------------------------------------------------------

    def lookup(
        self,
        build_key: str,
        *,
        document_hash: Optional[str] = None,
        required_kinds: Iterable[ArtifactKind] = (),
    ) -> CacheLookup:
        """Validate the entry for ``build_key`` and report a hit or a miss.

        ``build_key`` is the whole of the identity. ``document_hash`` and
        ``required_kinds`` are **verifications** applied to whatever the entry
        claims, not part of the lookup key.

        Never raises for a corrupt entry: every failure is a
        :class:`CacheMissReason`.
        """
        _require_key(build_key)
        required = tuple(required_kinds)
        directory = self.entry_directory(build_key)
        if not directory.is_dir():
            return self._miss(
                build_key, CacheMissReason.NO_ENTRY, "no entry for this build key"
            )
        manifest_path = directory / MANIFEST_FILENAME
        if not manifest_path.is_file():
            return self._miss(
                build_key, CacheMissReason.NO_MANIFEST, "the entry has no manifest"
            )
        try:
            raw = json.loads(manifest_path.read_bytes().decode(CANONICAL_ENCODING))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_UNREADABLE,
                f"the manifest could not be read ({type(exc).__name__})",
            )
        if not isinstance(raw, dict):
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_UNREADABLE,
                "the manifest is not a JSON object",
            )
        if set(raw) != set(_MANIFEST_FIELDS):
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_UNREADABLE,
                "the manifest's fields are not the ones this cache writes",
            )
        if raw["cache_schema_version"] != CACHE_SCHEMA_VERSION:
            return self._miss(
                build_key,
                CacheMissReason.SCHEMA_MISMATCH,
                "the entry was written by a different cache layout",
            )
        if raw["build_key"] != build_key:
            return self._miss(
                build_key,
                CacheMissReason.BUILD_KEY_MISMATCH,
                "the manifest names a different build",
            )
        stored_document = raw["document_hash"]
        if document_hash is not None and stored_document != document_hash:
            return self._miss(
                build_key,
                CacheMissReason.DOCUMENT_MISMATCH,
                "the manifest names a different CAD document",
            )
        records = raw["artifacts"]
        if not isinstance(records, list) or not records:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                "the manifest lists no artifacts",
            )

        restored: list = []
        render: Optional[RenderModel] = None
        for record in records:
            outcome = self._restore(
                build_key=build_key,
                document_hash=stored_document,
                directory=directory,
                record=record,
            )
            if isinstance(outcome, CacheLookup):
                return outcome
            artifact, model = outcome
            restored.append(artifact)
            if model is not None:
                render = model

        try:
            manifest = build_manifest(
                document_hash=stored_document,
                build_key=build_key,
                artifacts=restored,
            )
        except ArtifactError as exc:
            return self._miss(
                build_key, CacheMissReason.MANIFEST_INCONSISTENT, str(exc)
            )

        present = set(manifest.kinds())
        for kind in required:
            if kind not in present:
                return self._miss(
                    build_key,
                    CacheMissReason.OUTPUT_MISSING,
                    f"the entry does not hold the requested {kind.value} output",
                )

        return CacheLookup(
            build_key=build_key,
            hit=True,
            entry=CacheEntry(
                build_key=build_key,
                document_hash=stored_document,
                directory=str(directory),
                manifest=manifest,
                render_model=render,
            ),
        )

    def fetch(
        self,
        build_key: str,
        *,
        document_hash: Optional[str] = None,
        required_kinds: Iterable[ArtifactKind] = (),
    ) -> Optional[CacheEntry]:
        """The validated entry for ``build_key``, or ``None`` on a miss."""
        return self.lookup(
            build_key,
            document_hash=document_hash,
            required_kinds=required_kinds,
        ).entry

    def contains(
        self,
        build_key: str,
        *,
        document_hash: Optional[str] = None,
        required_kinds: Iterable[ArtifactKind] = (),
    ) -> bool:
        """Whether a **valid** entry exists. A corrupt entry is not present."""
        return self.lookup(
            build_key,
            document_hash=document_hash,
            required_kinds=required_kinds,
        ).hit

    # --- publication ------------------------------------------------------

    def publish(self, result: BuildResult) -> CacheEntry:
        """Copy a successful build's artifacts into the cache and publish them.

        The entry is assembled under ``staging/`` and becomes visible under
        ``entries/`` through a single directory rename, taken only after every
        payload has been copied, re-checksummed and found to match, and the
        manifest has been written in full. A partially written entry is
        therefore never visible as a hit.

        If another writer published the same build key first, that entry wins:
        this call discards its staging directory, re-validates the published
        entry and returns it. If the entry that is already there is *corrupt*,
        it is moved aside and replaced by this complete one.

        Raises:
            CachePublicationError: if the result did not succeed, if an
                artifact's payload is missing, or if a copied payload's bytes
                disagree with the artifact's recorded checksum. Nothing is
                published in that case.
        """
        if not isinstance(result, BuildResult):
            raise CachePublicationError(
                f"expected a BuildResult; got {type(result).__name__}"
            )
        if result.status is not BuildStatus.SUCCEEDED or result.error is not None:
            raise CachePublicationError(
                "only a successful build is cached; this result is "
                f"{result.status.value}"
            )
        if not result.artifacts:
            raise CachePublicationError("a cached build must have artifacts")

        build_key = result.build_key
        _require_key(build_key)
        staging = self._new_staging_directory(build_key)
        try:
            records = self._stage(staging, result)
            payload = _canonical_json(
                {
                    "cache_schema_version": CACHE_SCHEMA_VERSION,
                    "document_hash": result.document_hash,
                    "build_key": build_key,
                    "artifacts": records,
                }
            )
            (staging / MANIFEST_FILENAME).write_bytes(payload)
            self._install(staging, build_key)
        finally:
            _remove_tree(staging)

        lookup = self.lookup(build_key, document_hash=result.document_hash)
        if not lookup.hit or lookup.entry is None:
            raise CachePublicationError(
                "the published entry did not validate "
                f"({lookup.reason.value if lookup.reason else 'unknown'})"
            )
        return lookup.entry

    # --- internals --------------------------------------------------------

    def _miss(
        self, build_key: str, reason: CacheMissReason, detail: str
    ) -> CacheLookup:
        return CacheLookup(
            build_key=build_key, hit=False, reason=reason, detail=detail
        )

    def _restore(
        self,
        *,
        build_key: str,
        document_hash: str,
        directory: Path,
        record: Any,
    ):
        """Rebuild one artifact from a manifest record and the cached bytes.

        Returns the artifact (and a render model, when this record is the
        render artifact), or a :class:`CacheLookup` MISS.
        """
        if not isinstance(record, dict) or set(record) != set(
            _ARTIFACT_RECORD_FIELDS
        ):
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                "an artifact record's fields are not the ones this cache writes",
            )
        try:
            kind = ArtifactKind(record["kind"])
        except ValueError:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"unknown artifact kind {record['kind']!r}",
            )
        details = record["details"]
        if not isinstance(details, dict):
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"the {kind.value} record's details are not an object",
            )
        # Identity is the artifact layer's rule, applied here rather than
        # restated: a record whose logical id is not this build's is a MISS,
        # which is what catches a manifest naming another cache entry.
        if record["logical_id"] != artifact_logical_id(build_key, kind):
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"the {kind.value} record names another build's artifact",
            )
        if record["format"] != KIND_FORMATS[kind]:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"the {kind.value} record's format is not {KIND_FORMATS[kind]!r}",
            )
        if record["storage"] != KIND_STORAGE[kind].value:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"the {kind.value} record's storage kind is wrong",
            )

        if kind in METADATA_ONLY_KINDS:
            return self._restore_metadata_only(
                build_key=build_key,
                document_hash=document_hash,
                kind=kind,
                record=record,
                details=details,
            )

        located = _payload_path(directory, record["payload_path"])
        if located is None:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"the {kind.value} record's payload path is not inside the entry",
            )
        if not located.is_file():
            return self._miss(
                build_key,
                CacheMissReason.PAYLOAD_MISSING,
                f"the cached {kind.value} payload is missing",
            )
        if record["checksum_algorithm"] != CHECKSUM_ALGORITHM:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"the {kind.value} record's checksum algorithm is not "
                f"{CHECKSUM_ALGORITHM}",
            )
        size = located.stat().st_size
        if size != record["size_bytes"]:
            return self._miss(
                build_key,
                CacheMissReason.SIZE_MISMATCH,
                f"the cached {kind.value} payload is {size} bytes, not "
                f"{record['size_bytes']}",
            )
        if file_checksum(located) != record["checksum"]:
            return self._miss(
                build_key,
                CacheMissReason.CHECKSUM_MISMATCH,
                f"the cached {kind.value} payload's bytes do not match its "
                "recorded checksum",
            )

        if kind is ArtifactKind.RENDER:
            return self._restore_render(
                build_key=build_key,
                document_hash=document_hash,
                path=located,
                record=record,
                details=details,
            )
        return self._restore_file(
            build_key=build_key,
            document_hash=document_hash,
            kind=kind,
            path=located,
            record=record,
            details=details,
        )

    def _restore_metadata_only(
        self,
        *,
        build_key: str,
        document_hash: str,
        kind: ArtifactKind,
        record: Mapping[str, Any],
        details: Mapping[str, Any],
    ):
        """Restore an artifact that never had a payload representation.

        ``geometry`` is the only such kind: no B-rep is serialized, so the
        record must claim no payload, no size and no checksum -- exactly what a
        fresh build's geometry artifact carries.
        """
        for absent in ("payload_path", "size_bytes", "checksum", "checksum_algorithm", "file_extension"):
            if record[absent] is not None:
                return self._miss(
                    build_key,
                    CacheMissReason.MANIFEST_INCONSISTENT,
                    f"the {kind.value} record claims a {absent}, but "
                    f"{kind.value} has no cached payload",
                )
        artifact = Artifact(
            kind=kind,
            format=KIND_FORMATS[kind],
            document_hash=document_hash,
            build_key=build_key,
            storage=ArtifactStorage.IN_MEMORY,
            path=None,
            file_extension=None,
            size_bytes=None,
            checksum=None,
            details=dict(details),
        )
        return artifact, None

    def _restore_file(
        self,
        *,
        build_key: str,
        document_hash: str,
        kind: ArtifactKind,
        path: Path,
        record: Mapping[str, Any],
        details: Mapping[str, Any],
    ):
        """Re-publish a cached file through the artifact layer's own gate."""
        try:
            artifact = publish_file_artifact(
                document_hash=document_hash,
                build_key=build_key,
                kind=kind,
                path=path,
                details=dict(details),
            )
        except ArtifactError as exc:
            return self._miss(
                build_key,
                CacheMissReason.PAYLOAD_MISSING,
                f"the cached {kind.value} payload was refused ({exc})",
            )
        if artifact.file_extension != record["file_extension"]:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"the cached {kind.value} payload's extension is not the "
                "recorded one",
            )
        if artifact.checksum != record["checksum"]:
            return self._miss(
                build_key,
                CacheMissReason.CHECKSUM_MISMATCH,
                f"the cached {kind.value} payload's bytes do not match its "
                "recorded checksum",
            )
        return artifact, None

    def _restore_render(
        self,
        *,
        build_key: str,
        document_hash: str,
        path: Path,
        record: Mapping[str, Any],
        details: Mapping[str, Any],
    ):
        """Reconstruct the render model and re-publish its artifact.

        The payload is exactly ``canonical_render_bytes`` of the model the
        build produced, so the artifact is rebuilt *from the model* rather than
        transcribed: its checksum and size are recomputed and then compared
        with the recorded ones.
        """
        try:
            model = render_model_from_canonical_bytes(path.read_bytes())
        except (ArtifactError, OSError) as exc:
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                f"the cached render payload could not be read ({exc})",
            )
        artifact = publish_render_artifact(
            document_hash=document_hash, build_key=build_key, model=model
        )
        if artifact.checksum != record["checksum"]:
            return self._miss(
                build_key,
                CacheMissReason.CHECKSUM_MISMATCH,
                "the reconstructed render model does not match its recorded "
                "checksum",
            )
        if artifact.size_bytes != record["size_bytes"]:
            return self._miss(
                build_key,
                CacheMissReason.SIZE_MISMATCH,
                "the reconstructed render model's canonical size is not the "
                "recorded one",
            )
        if dict(artifact.details) != dict(details):
            return self._miss(
                build_key,
                CacheMissReason.MANIFEST_INCONSISTENT,
                "the reconstructed render model's metadata is not the "
                "recorded one",
            )
        return artifact, model

    def _new_staging_directory(self, build_key: str) -> Path:
        """A private scratch directory under the cache root.

        Its name carries the build key for legibility and a unique token for
        isolation. The name is never read back and is never an identity.
        """
        staging = self.staging_directory / f"{build_key}.{uuid.uuid4().hex}"
        staging.mkdir(parents=True)
        (staging / PAYLOAD_DIRNAME).mkdir()
        return staging

    def _stage(self, staging: Path, result: BuildResult) -> list:
        """Copy every artifact's payload into ``staging`` and return records."""
        records: list = []
        for artifact in result.artifacts:
            kind = artifact.kind
            if kind in METADATA_ONLY_KINDS:
                records.append(_metadata_record(artifact))
                continue
            if kind is ArtifactKind.RENDER:
                if result.render_model is None:
                    raise CachePublicationError(
                        "the build reported a render artifact but holds no "
                        "render model"
                    )
                relative = f"{PAYLOAD_DIRNAME}/{kind.value}{RENDER_PAYLOAD_EXTENSION}"
                destination = staging / relative
                payload = canonical_render_bytes(result.render_model)
                destination.write_bytes(payload)
                written = file_checksum(destination)
                if written != artifact.checksum:
                    raise CachePublicationError(
                        "the cached render payload's bytes do not match the "
                        "artifact's checksum"
                    )
                records.append(_payload_record(artifact, relative))
                continue
            if artifact.path is None:
                raise CachePublicationError(
                    f"the {kind.value} artifact has no path to cache"
                )
            source = Path(artifact.path)
            if not source.is_file():
                raise CachePublicationError(
                    f"the {kind.value} artifact's file is missing"
                )
            extension = artifact.file_extension or ""
            relative = f"{PAYLOAD_DIRNAME}/{kind.value}{extension}"
            destination = staging / relative
            written = _copy(source, destination)
            if written != artifact.checksum:
                raise CachePublicationError(
                    f"the cached {kind.value} payload's bytes do not match the "
                    "artifact's checksum"
                )
            records.append(_payload_record(artifact, relative))
        return records

    def _install(self, staging: Path, build_key: str) -> None:
        """Make a fully staged entry visible with a single rename.

        A directory rename is atomic within one filesystem, and staging lives
        under the same cache root, so no partially written entry is ever
        visible under ``entries/``. Renaming onto an existing non-empty
        directory fails (measured: ``ENOTEMPTY`` on POSIX), which is how a
        concurrent publication is detected: the first writer wins.
        """
        final = self.entry_directory(build_key)
        self.entries_directory.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(staging, final)
            return
        except OSError:
            pass
        # Something is already there. A valid entry wins -- this build's
        # artifacts are byte-equivalent in identity, so there is nothing to
        # gain by replacing it. A corrupt one is moved aside and replaced.
        if self.lookup(build_key).hit:
            return
        superseded = staging.parent / f"{staging.name}.superseded"
        try:
            os.rename(final, superseded)
        except OSError:
            return  # another writer is mid-publication; that one wins
        try:
            os.rename(staging, final)
        except OSError:
            pass
        _remove_tree(superseded)


def get_or_build(
    request: BuildRequest,
    cache: LocalBuildCache,
    *,
    output_directory: Optional[PathLike] = None,
) -> BuildJob:
    """Return a terminal job for ``request``, from the cache when possible.

    1. take the request's existing build key;
    2. ask the cache for a **valid** entry for that key;
    3. on a hit, return a ``SUCCEEDED`` job whose result carries
       ``cache_hit=True``, the restored manifest and paths inside the cache
       root -- **no geometry is built and no exporter runs**;
    4. on a miss, run the ordinary build and, if it succeeds, publish its
       artifacts to the cache.

    A cache hit is a new invocation, so the job gets a fresh
    :attr:`~cad_core.build_job.BuildJob.execution_id`. That is deliberately
    *not* part of any identity: the build key and the artifacts' logical ids
    are the same as the build that filled the cache.

    ``output_directory`` is needed only for a miss, on the same terms as
    :func:`~cad_core.build_job.run_job`. On a hit nothing is written anywhere.

    A cache hit does **not** restore the in-memory B-rep: ``result.geometry``
    is ``None`` even when ``GEOMETRY`` was requested, because no B-rep is
    serialized. The geometry artifact's measurements are restored, which is
    all that artifact ever held.
    """
    if not isinstance(request, BuildRequest):
        raise CacheError(f"expected a BuildRequest; got {type(request).__name__}")
    if not isinstance(cache, LocalBuildCache):
        raise CacheError(f"expected a LocalBuildCache; got {type(cache).__name__}")

    lookup = cache.lookup(
        request.build_key,
        document_hash=request.document_hash,
        required_kinds=request.options.requested,
    )
    if lookup.hit and lookup.entry is not None:
        return _hit_job(request, lookup.entry)

    job = execute_build(request, output_directory=output_directory)
    if job.status is BuildStatus.SUCCEEDED and job.result is not None:
        cache.publish(job.result)
    return job


def _hit_job(request: BuildRequest, entry: CacheEntry) -> BuildJob:
    """A terminal ``SUCCEEDED`` job carrying a cache hit.

    No new status is introduced: a cache hit is a successful build whose
    artifacts came from the cache, and ``cache_hit`` says so separately.
    """
    job = BuildJob(request)
    job.start()
    job.succeed(
        BuildResult(
            build_key=entry.build_key,
            document_hash=entry.document_hash,
            execution_id=job.execution_id,
            status=BuildStatus.SUCCEEDED,
            artifacts=entry.manifest.artifacts,
            error=None,
            geometry=None,
            render_model=entry.render_model,
            cache_hit=True,
        )
    )
    return job


# --- internals --------------------------------------------------------------


def _require_key(build_key: str) -> None:
    """A build key names a directory, so it must be a plain single segment."""
    if not isinstance(build_key, str) or not build_key:
        raise CacheError("a cache key must be a non-empty build key")
    if build_key != Path(build_key).name or build_key in (".", ".."):
        raise CacheError(f"{build_key!r} is not usable as a cache entry name")


def _payload_record(artifact: Artifact, relative: str) -> Dict[str, Any]:
    """The persisted record for an artifact with a cached payload.

    Holds no absolute path, no execution id, no traceback and no kernel
    object -- the payload's location is relative to the entry directory.
    """
    return {
        "kind": artifact.kind.value,
        "format": artifact.format,
        "storage": artifact.storage.value,
        "logical_id": artifact.logical_id,
        "file_extension": artifact.file_extension,
        "size_bytes": artifact.size_bytes,
        "checksum": artifact.checksum,
        "checksum_algorithm": CHECKSUM_ALGORITHM,
        "payload_path": relative,
        "details": dict(artifact.details),
    }


def _metadata_record(artifact: Artifact) -> Dict[str, Any]:
    """The persisted record for an artifact with no payload representation."""
    return {
        "kind": artifact.kind.value,
        "format": artifact.format,
        "storage": artifact.storage.value,
        "logical_id": artifact.logical_id,
        "file_extension": None,
        "size_bytes": None,
        "checksum": None,
        "checksum_algorithm": None,
        "payload_path": None,
        "details": dict(artifact.details),
    }


def _payload_path(directory: Path, relative: Any) -> Optional[Path]:
    """Resolve a recorded relative payload path inside ``directory``.

    Refuses anything absolute, empty, or containing ``..`` -- a manifest must
    not be able to point at another cache entry or anywhere else on the disk.
    """
    if not isinstance(relative, str) or not relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    located = directory / candidate
    try:
        located.relative_to(directory)
    except ValueError:  # pragma: no cover - guarded by the checks above
        return None
    return located


def _copy(source: Path, destination: Path) -> str:
    """Copy ``source`` to ``destination`` and return the checksum written.

    The checksum is taken from the bytes actually written into the cache, not
    copied from the source artifact's record, so a bad copy cannot be
    published with a checksum that looks right.
    """
    digest = hashlib.new(CHECKSUM_ALGORITHM)
    with open(source, "rb") as reader, open(destination, "wb") as writer:
        for chunk in iter(lambda: reader.read(_COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
            writer.write(chunk)
    return digest.hexdigest()


def _remove_tree(path: Path) -> None:
    """Best-effort removal of a scratch directory."""
    shutil.rmtree(path, ignore_errors=True)


def _canonical_json(structure: Mapping[str, Any]) -> bytes:
    """The manifest's bytes, with the canonical document's own conventions."""
    return json.dumps(
        structure,
        separators=CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    ).encode(CANONICAL_ENCODING)


__all__ = [
    "CACHED_PAYLOAD_KINDS",
    "CACHE_SCHEMA_VERSION",
    "ENTRIES_DIRNAME",
    "MANIFEST_FILENAME",
    "METADATA_ONLY_KINDS",
    "PAYLOAD_DIRNAME",
    "RENDER_PAYLOAD_EXTENSION",
    "STAGING_DIRNAME",
    "CacheEntry",
    "CacheError",
    "CacheLookup",
    "CacheMissReason",
    "CachePublicationError",
    "LocalBuildCache",
    "get_or_build",
]
