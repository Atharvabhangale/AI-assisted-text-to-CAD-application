"""Read-only retrieval of a published build, by its build key.

An application-level adapter, not HTTP: nothing here imports FastAPI, and it
is exercised directly as well as through the route.

```
build key                      from the client, and nothing else
     │   cad_core.build_job.is_build_key -- the public syntax, reused
application service            find_build_by_key
     │   the existing cache validation
published build result
```

**This retrieves successfully published derived results. It is not a
job-status service and not a document database.** The cache is the only place
a completed build is looked up: no database, no build index, no job registry
and no persistence of documents or job state exists, and none is implied.

What this layer adds over the service is one thing: the difference between a
key that is *not a key* and a key that names nothing. The syntax of a build
key is public -- it is in the transport contract, and every build response
returns one -- so telling a client its key is malformed reveals nothing and
saves it a guess. Everything else is one answer: *not available*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Union

from cad_core.application_service import BuildOutcome, CadApplicationService
from cad_core.build_job import BUILD_KEY_LENGTH, is_build_key

#: How long a build key is, from the layer that defines it.
BUILD_KEY_CHARACTERS = BUILD_KEY_LENGTH

#: The one message used for every build that cannot be retrieved.
NOT_FOUND_MESSAGE = "the build is not available"


class RetrievalReason(Enum):
    """Why a build could not be retrieved. A client can branch on these."""

    #: The path parameter is not a build key at all.
    BUILD_KEY_INVALID = "build_key_invalid"

    #: No published build has that key. Deliberately one reason for a key
    #: nothing was built under and for an entry that does not validate --
    #: distinguishing them would report on the cache's contents.
    BUILD_NOT_FOUND = "build_not_found"

    #: The retrieval layer failed unexpectedly.
    RETRIEVAL_FAILED = "retrieval_failed"


@dataclass(frozen=True)
class RetrievalProblem:
    """A refusal, with a stable reason and a message safe to show a client."""

    reason: RetrievalReason
    message: str

    def to_payload(self) -> Mapping[str, object]:
        return {"reason": self.reason.value, "message": self.message}


class BuildRetriever:
    """Turns a build key into a published build result, or a refusal.

    Holds the application service the application was configured with, and
    calls one read-only method on it. It reads no file, parses no manifest,
    touches no path and computes no checksum -- the service and the cache
    below it own all of that.
    """

    def __init__(self, service: CadApplicationService) -> None:
        if not isinstance(service, CadApplicationService):
            raise TypeError(
                f"expected a CadApplicationService; got {type(service).__name__}"
            )
        self._service = service

    @property
    def service(self) -> CadApplicationService:
        return self._service

    def retrieve(
        self, build_key: str
    ) -> Union[BuildOutcome, RetrievalProblem]:
        """Retrieve the build published under ``build_key``.

        Returns a :class:`~cad_core.application_service.BuildOutcome` or a
        :class:`RetrievalProblem`; ordinary refusals are results, not
        exceptions. **Nothing is built and nothing is written.**
        """
        if not is_build_key(build_key):
            return RetrievalProblem(
                reason=RetrievalReason.BUILD_KEY_INVALID,
                message=(
                    "a build key is "
                    f"{BUILD_KEY_CHARACTERS} lowercase hexadecimal characters"
                ),
            )
        outcome = self._service.find_build_by_key(build_key)
        if outcome is None:
            return RetrievalProblem(
                reason=RetrievalReason.BUILD_NOT_FOUND,
                message=NOT_FOUND_MESSAGE,
            )
        return outcome


__all__ = [
    "BUILD_KEY_CHARACTERS",
    "NOT_FOUND_MESSAGE",
    "BuildRetriever",
    "RetrievalProblem",
    "RetrievalReason",
]
