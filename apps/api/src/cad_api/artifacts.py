"""Safe resolution of a logical artifact id into deliverable bytes.

An application-level service, not HTTP: nothing here imports FastAPI, and it
is exercised directly in the tests as well as through the route.

```
artifact logical id            <build key>:<kind>, from the client
        |   whitelisted, never string-to-path
build key + artifact kind
        |   cad_core.local_build_cache -- the existing Stage 18 validation
validated cache entry          manifest parsed, sizes and checksums recomputed
        |
manifest record                the authority on what exists and where
        |   containment + symlink gate, below
verified regular file inside the entry
        |   read once, then size and checksum checked against those bytes
deliverable bytes
```

The client supplies **only** a logical id. It never supplies a path, a
filename, an extension, a directory or a cache root, and no part of the
request reaches the filesystem as text: a build key must match
``[0-9a-f]{64}`` and a kind must be an existing
:class:`~cad_core.artifact_registry.ArtifactKind` value, and the parsed pair
is round-tripped through
:func:`~cad_core.artifact_registry.artifact_logical_id` and compared with what
arrived. Anything else is refused before the cache is touched.

Nothing here recomputes what a lower layer already owns
-------------------------------------------------------
Logical ids, artifact formats, extensions, checksum algorithm and **cache
entry validation** all come from ``cad_core``. The cache decides whether an
entry is valid; this layer decides whether a file inside a valid entry is safe
to hand to a client, which is a different question and the one thing this
layer adds:

* **symlink refusal.** Measured on this platform: a payload replaced by a
  symlink pointing outside its entry still satisfies ``Path.is_file()``, and
  the cache's own containment check runs on the *unresolved* path, so the link
  passes it. ``os.path.realpath`` escapes the entry. So a symlinked payload,
  a symlinked parent directory, or anything whose real path leaves the entry
  is refused here.
* **containment by real path**, with :func:`os.path.commonpath` on resolved
  paths -- never a prefix string comparison.
* **regular files only**, from ``lstat`` rather than a call that follows
  links.
* **integrity against the bytes actually served.** The payload is read once,
  and its length and SHA-256 are checked against the manifest record *after*
  the read. What is delivered is exactly what was verified; there is no window
  between the check and the send.

In-memory artifacts are never delivered
---------------------------------------
``geometry`` and ``render`` have no file, and this endpoint invents neither a
B-rep serialization nor a second render format. They are refused as
**not downloadable**, decided from the id alone before any cache access, so
the refusal reveals nothing about what the cache holds.

Nothing about the cache is revealed
-----------------------------------
An unknown build key, an invalid entry, a corrupt payload, an unsafe path and
a missing kind all produce one message: *the artifact is not available*. No
path, cache root, manifest filename, ``errno``, checksum comparison or Python
exception text ever reaches a client.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional, Tuple, Union

from cad_core.build_job import BUILD_KEY_PATTERN
from cad_core.artifact_registry import (
    CHECKSUM_ALGORITHM,
    FILE_KINDS,
    KIND_EXTENSIONS,
    KIND_FORMATS,
    ArtifactKind,
    ArtifactStorage,
    artifact_logical_id,
)
from cad_core.local_build_cache import CacheError, LocalBuildCache

#: A logical artifact id: a build key, a colon, and an artifact kind. A
#: whitelist, not a sanitiser -- nothing that is not exactly this shape gets
#: any further.
#:
#: The build-key half is :data:`~cad_core.build_job.BUILD_KEY_PATTERN`, the
#: public syntax defined where the key itself is, so the two cannot drift.
ARTIFACT_ID_PATTERN = re.compile(
    r"\A(?P<build_key>%s):(?P<kind>[a-z_]{1,16})\Z"
    % BUILD_KEY_PATTERN.pattern.lstrip("\\A").rstrip("\\Z")
)

#: The kinds this endpoint can deliver: the file-backed ones, from the
#: artifact registry itself rather than a list repeated here.
DOWNLOADABLE_KINDS: Tuple[ArtifactKind, ...] = FILE_KINDS

#: The media type for every delivered artifact.
#:
#: Measured rather than guessed: Python's own IANA table does offer more
#: specific types (``model/step``, ``model/iges``, ``model/stl``). They are
#: deliberately not used. These responses are opaque bytes for a client to
#: save -- ``Content-Disposition: attachment`` already says so -- and
#: ``application/octet-stream`` is exactly that, with no chance of a browser
#: deciding to render a CAD file. No custom type is invented either way.
DOWNLOAD_CONTENT_TYPE = "application/octet-stream"

#: Characters that must never appear in a download filename. A build key is
#: hexadecimal and an extension comes from the registry, so this cannot
#: trigger; it is asserted anyway, because a header-injecting filename is the
#: kind of thing that must be impossible rather than merely unlikely.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")

#: Bytes read per chunk is deliberately absent: a payload is read whole, so
#: the bytes hashed are the bytes served. See the module docstring.
_MAX_ID_LENGTH = 128


class DeliveryReason(Enum):
    """Why an artifact could not be delivered. A client can branch on these."""

    #: The id is not a logical artifact id at all.
    ARTIFACT_ID_INVALID = "artifact_id_invalid"

    #: There is no deliverable artifact with that id. Deliberately one reason
    #: for an unknown build key, an invalid or corrupt cache entry, a missing
    #: kind and an unsafe payload -- distinguishing them would report on the
    #: cache's contents.
    ARTIFACT_NOT_FOUND = "artifact_not_found"

    #: The id names a real artifact kind that has no bytes: ``geometry`` or
    #: ``render``.
    ARTIFACT_NOT_DOWNLOADABLE = "artifact_not_downloadable"

    #: The delivery layer failed unexpectedly.
    DELIVERY_FAILED = "delivery_failed"


#: The one message used for every unavailable artifact.
NOT_FOUND_MESSAGE = "the artifact is not available"


@dataclass(frozen=True)
class DeliveryProblem:
    """A refusal, with a stable reason and a message safe to show a client."""

    reason: DeliveryReason
    message: str

    def to_payload(self) -> Mapping[str, object]:
        return {"reason": self.reason.value, "message": self.message}


@dataclass(frozen=True)
class DeliveredArtifact:
    """A verified artifact, with the bytes that were verified.

    :attr:`content` is what was hashed. :attr:`size_bytes` is its real length
    and :attr:`checksum` its real SHA-256, both agreeing with the manifest
    record. No path is carried: there is nothing left to open.
    """

    artifact_id: str
    build_key: str
    kind: ArtifactKind
    format: str
    content: bytes
    size_bytes: int
    checksum: str
    checksum_algorithm: str
    filename: str
    content_type: str

    @property
    def etag(self) -> str:
        """The HTTP entity tag: the artifact's own SHA-256, quoted.

        Not a second hash -- the same checksum the artifact layer computed
        from these bytes.
        """
        return f'"{self.checksum}"'


class ArtifactResolver:
    """Turns a logical artifact id into verified bytes, or a refusal.

    Holds the one cache the application was configured with. A resolver built
    without a cache delivers nothing: every id is simply not available.
    """

    def __init__(self, cache: Optional[LocalBuildCache]) -> None:
        if cache is not None and not isinstance(cache, LocalBuildCache):
            raise TypeError(
                f"expected a LocalBuildCache; got {type(cache).__name__}"
            )
        self._cache = cache

    @property
    def cache(self) -> Optional[LocalBuildCache]:
        return self._cache

    def resolve(
        self, artifact_id: str
    ) -> Union[DeliveredArtifact, DeliveryProblem]:
        """Resolve ``artifact_id``, verifying everything on the way.

        Returns a :class:`DeliveredArtifact` or a :class:`DeliveryProblem`;
        ordinary refusals are results, not exceptions.
        """
        parsed = _parse_artifact_id(artifact_id)
        if isinstance(parsed, DeliveryProblem):
            return parsed
        build_key, kind = parsed

        if kind not in DOWNLOADABLE_KINDS:
            # Decided from the id alone: no cache access, so this says nothing
            # about what has been built.
            return DeliveryProblem(
                reason=DeliveryReason.ARTIFACT_NOT_DOWNLOADABLE,
                message=(
                    f"the {kind.value} artifact is held in memory and is not "
                    "downloadable; the build response describes it"
                ),
            )

        if self._cache is None:
            return _not_found()

        try:
            lookup = self._cache.lookup(build_key, required_kinds=(kind,))
        except CacheError:
            # A key the cache itself refuses. Nothing is disclosed.
            return _not_found()
        if not lookup.hit or lookup.entry is None:
            return _not_found()

        artifact = lookup.entry.artifact(kind)
        if artifact is None or artifact.storage is not ArtifactStorage.FILE:
            return _not_found()
        if artifact.path is None or artifact.checksum is None:
            return _not_found()

        content = _verified_bytes(lookup.entry.directory, artifact)
        if content is None:
            return _not_found()

        filename = _download_filename(build_key, kind, artifact.file_extension)
        if filename is None:
            return _not_found()

        return DeliveredArtifact(
            artifact_id=artifact.logical_id,
            build_key=build_key,
            kind=kind,
            format=artifact.format,
            content=content,
            size_bytes=len(content),
            checksum=artifact.checksum,
            checksum_algorithm=CHECKSUM_ALGORITHM,
            filename=filename,
            content_type=DOWNLOAD_CONTENT_TYPE,
        )


# --- internals -------------------------------------------------------------


def _not_found() -> DeliveryProblem:
    return DeliveryProblem(
        reason=DeliveryReason.ARTIFACT_NOT_FOUND, message=NOT_FOUND_MESSAGE
    )


def _parse_artifact_id(
    artifact_id: object,
) -> Union[Tuple[str, ArtifactKind], DeliveryProblem]:
    """Whitelist an artifact id, or refuse it.

    No path is constructed from client text anywhere: this returns a build key
    that is 64 hexadecimal characters and an :class:`ArtifactKind` member, and
    proves the pair round-trips to the id that arrived.
    """
    invalid = DeliveryProblem(
        reason=DeliveryReason.ARTIFACT_ID_INVALID,
        message="the artifact id is not a <build key>:<kind> identifier",
    )
    if not isinstance(artifact_id, str) or not artifact_id:
        return invalid
    if len(artifact_id) > _MAX_ID_LENGTH:
        return invalid
    # Refused explicitly as well as by the pattern, so the intent is on the
    # record: nothing that could name a path is entertained.
    for forbidden in ("\x00", "/", "\\", "..", os.sep, os.altsep or "/", "\r", "\n"):
        if forbidden and forbidden in artifact_id:
            return invalid
    match = ARTIFACT_ID_PATTERN.match(artifact_id)
    if match is None:
        return invalid
    build_key = match.group("build_key")
    try:
        kind = ArtifactKind(match.group("kind"))
    except ValueError:
        return invalid
    if artifact_logical_id(build_key, kind) != artifact_id:
        return invalid  # pragma: no cover - the pattern makes this impossible
    return build_key, kind


def _verified_bytes(entry_directory: str, artifact: object) -> Optional[bytes]:
    """Read a payload only if it is safe, then verify what was read.

    In order: the entry and the payload resolve to real paths; the payload's
    real path is **inside** the entry's, by :func:`os.path.commonpath` rather
    than a prefix comparison; nothing on the way is a symlink; the payload is
    a regular file by ``lstat``; the extension is one the kind uses; the bytes
    are read; their length matches; their SHA-256 matches.

    ``None`` means refuse. No reason is returned, because no reason is
    reported.
    """
    path = getattr(artifact, "path", None)
    checksum = getattr(artifact, "checksum", None)
    recorded_size = getattr(artifact, "size_bytes", None)
    kind = getattr(artifact, "kind", None)
    if not isinstance(path, str) or not isinstance(checksum, str):
        return None
    if not isinstance(recorded_size, int) or recorded_size <= 0:
        return None
    if not isinstance(kind, ArtifactKind):
        return None

    location = Path(path)
    if location.suffix.lower() not in KIND_EXTENSIONS.get(kind, ()):
        return None
    if artifact.format != KIND_FORMATS[kind]:
        return None

    # A symlink anywhere -- the payload itself or a directory above it inside
    # the entry -- is refused. The real-path containment below catches a link
    # that escapes; this catches one that does not, because a cache entry has
    # no legitimate use for a link at all.
    try:
        if os.path.islink(path):
            return None
        entry_real = os.path.realpath(entry_directory)
        payload_real = os.path.realpath(path)
        if not _contains(entry_real, payload_real):
            return None
        if os.path.islink(entry_directory):
            return None
        if _has_symlinked_parent(entry_real, path):
            return None
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size != recorded_size:
            return None
        with open(path, "rb") as handle:
            content = handle.read(recorded_size + 1)
    except OSError:
        # A filesystem error is never reported to a client, and never turns
        # into a partial download.
        return None

    if len(content) != recorded_size:
        return None
    if hashlib.new(CHECKSUM_ALGORITHM, content).hexdigest() != checksum:
        return None
    return content


def _contains(directory: str, candidate: str) -> bool:
    """Whether ``candidate`` is inside ``directory``, both real paths.

    Uses :func:`os.path.commonpath`, so ``/cache/entry-2`` is not inside
    ``/cache/entry`` -- which a prefix comparison would get wrong.
    """
    if candidate == directory:
        return False
    try:
        return os.path.commonpath([directory, candidate]) == directory
    except ValueError:  # different drives, or a mix of absolute and relative
        return False


def _has_symlinked_parent(entry_real: str, path: str) -> bool:
    """Whether any directory between the entry and the payload is a symlink."""
    current = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.islink(current):
            return True
        if os.path.realpath(current) == entry_real:
            return False
        parent = os.path.dirname(current)
        if parent == current:
            return True  # walked to the root without meeting the entry
        current = parent


def _download_filename(
    build_key: str, kind: ArtifactKind, extension: Optional[str]
) -> Optional[str]:
    """``<build key><extension>``, from trusted metadata only.

    The extension comes from the validated manifest record and must be one the
    registry lists for that kind -- never from the URL, never from the
    original build directory's filename. The result is checked against a
    conservative character class, so a header-injecting name is impossible
    rather than merely unlikely.
    """
    if not isinstance(extension, str) or extension not in KIND_EXTENSIONS.get(
        kind, ()
    ):
        return None
    filename = f"{build_key}{extension}"
    if _UNSAFE_FILENAME.search(filename):
        return None  # pragma: no cover - a hex key and a known extension
    return filename


__all__ = [
    "ARTIFACT_ID_PATTERN",
    "DOWNLOADABLE_KINDS",
    "DOWNLOAD_CONTENT_TYPE",
    "NOT_FOUND_MESSAGE",
    "ArtifactResolver",
    "DeliveredArtifact",
    "DeliveryProblem",
    "DeliveryReason",
]
