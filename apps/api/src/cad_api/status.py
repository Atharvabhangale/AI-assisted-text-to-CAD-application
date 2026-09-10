"""The HTTP status mapping, in one place.

The line this map draws is **whose fault** a failure is, because that is what
a status code is for:

* **4xx** -- the request cannot be processed as sent. The client can fix it.
* **5xx** -- the request was fine and the server could not do it.

Nothing maps to a status because of a Python exception class, and nothing
collapses into a blanket 500.
"""

from __future__ import annotations

from typing import Mapping, Tuple

from cad_core.application_service import ServiceFailure

from cad_ai.generation import GenerationOutcome

from cad_api.artifacts import DeliveryReason
from cad_api.builds import RetrievalReason

#: 200. A successful operation, and also a *completed* validation whose answer
#: happens to be "no" -- see :mod:`cad_api.app`.
OK_STATUS = 200

#: 422 Unprocessable Content. The body was well-formed HTTP and JSON but the
#: request cannot be processed: a bad envelope, an unbuildable document.
UNPROCESSABLE_STATUS = 422

#: 500. The server failed at something that was not the client's fault.
INTERNAL_STATUS = 500

#: 503. Execution or the cache failed; the same request may well work later.
UNAVAILABLE_STATUS = 503

#: The status for a request FastAPI itself rejected -- malformed JSON, a
#: missing field, an unknown field, a wrong type. FastAPI's own default, kept.
TRANSPORT_STATUS = UNPROCESSABLE_STATUS

#: One status per application failure. Complete by construction: a test
#: asserts every :class:`ServiceFailure` member appears.
FAILURE_STATUS: Mapping[ServiceFailure, int] = {
    # the client's request cannot be processed as sent
    ServiceFailure.MALFORMED_DOCUMENT: UNPROCESSABLE_STATUS,
    ServiceFailure.INVALID_DOCUMENT: UNPROCESSABLE_STATUS,
    ServiceFailure.INVALID_REQUEST: UNPROCESSABLE_STATUS,
    # statically valid, but this geometry cannot be built -- still the input
    ServiceFailure.GEOMETRY_FAILED: UNPROCESSABLE_STATUS,
    # the geometry was fine and the server could not produce the output
    ServiceFailure.OUTPUT_FAILED: INTERNAL_STATUS,
    # the build could not be executed at all; retrying may succeed
    ServiceFailure.EXECUTION_FAILED: UNAVAILABLE_STATUS,
    ServiceFailure.INTERNAL_ERROR: INTERNAL_STATUS,
}

#: 502 Bad Gateway. An upstream service answered, and its answer was not
#: usable. Used for one thing only: the model produced something that is not a
#: valid CAD document. That is not the client's fault -- the same description
#: may well work on the next attempt -- and it is not this server failing
#: either, so it is neither 4xx nor 500.
BAD_GATEWAY_STATUS = 502

#: 400 Bad Request. The request's own identifier is malformed.
BAD_REQUEST_STATUS = 400

#: 404 Not Found.
NOT_FOUND_STATUS = 404

#: One status per artifact-delivery refusal. Complete by construction: a test
#: asserts every :class:`DeliveryReason` member appears.
#:
#: ``ARTIFACT_NOT_FOUND`` and ``ARTIFACT_NOT_DOWNLOADABLE`` share a status and
#: differ by ``reason``, which is what a client branches on. They are both
#: 404 because neither names a document that can be fetched -- one does not
#: exist, and the other has no bytes to have.
DELIVERY_STATUS: Mapping[DeliveryReason, int] = {
    DeliveryReason.ARTIFACT_ID_INVALID: BAD_REQUEST_STATUS,
    DeliveryReason.ARTIFACT_NOT_FOUND: NOT_FOUND_STATUS,
    DeliveryReason.ARTIFACT_NOT_DOWNLOADABLE: NOT_FOUND_STATUS,
    DeliveryReason.DELIVERY_FAILED: INTERNAL_STATUS,
}

#: One status per build-retrieval refusal. Complete by construction: a test
#: asserts every :class:`RetrievalReason` member appears.
#:
#: A malformed key is separated from an unknown one because the syntax is
#: public; an unknown key and an unavailable entry share one status *and* one
#: reason, so a client cannot learn what the cache holds.
RETRIEVAL_STATUS: Mapping[RetrievalReason, int] = {
    RetrievalReason.BUILD_KEY_INVALID: BAD_REQUEST_STATUS,
    RetrievalReason.BUILD_NOT_FOUND: NOT_FOUND_STATUS,
    RetrievalReason.RENDER_NOT_AVAILABLE: NOT_FOUND_STATUS,
    RetrievalReason.RETRIEVAL_FAILED: INTERNAL_STATUS,
}

#: One status per AI outcome. Complete by construction: a test asserts every
#: :class:`GenerationOutcome` member appears.
#:
#: The first three are **200 because the question was asked and answered**,
#: which is the same rule ``POST /validate`` follows when its answer is
#: ``"valid": false``. A clarification and a refusal are results, not
#: failures: the service did exactly what it exists to do and reported what it
#: found. Collapsing them into a 4xx would tell a client its request was
#: malformed when it was merely under-specified or out of scope.
#:
#: The last two are the two genuine failures, and they are separated because a
#: client can act on the difference: a model that answered badly may answer
#: well next time (502), while a provider that could not be reached at all is
#: an availability problem (503).
GENERATION_STATUS: Mapping[GenerationOutcome, int] = {
    GenerationOutcome.GENERATED: OK_STATUS,
    GenerationOutcome.NEEDS_CLARIFICATION: OK_STATUS,
    GenerationOutcome.UNSUPPORTED: OK_STATUS,
    GenerationOutcome.INVALID_MODEL_OUTPUT: BAD_GATEWAY_STATUS,
    GenerationOutcome.MODEL_ERROR: UNAVAILABLE_STATUS,
}

#: Statuses this application can return, for documentation and tests.
STATUSES: Tuple[int, ...] = (
    OK_STATUS,
    BAD_REQUEST_STATUS,
    NOT_FOUND_STATUS,
    UNPROCESSABLE_STATUS,
    INTERNAL_STATUS,
    BAD_GATEWAY_STATUS,
    UNAVAILABLE_STATUS,
)


def status_for_outcome(outcome: GenerationOutcome) -> int:
    """The HTTP status for an AI generation outcome.

    An unrecognised outcome is a 500 rather than a guess: the taxonomy is
    closed, so an unknown member means this map is out of date, which is a
    server problem and not the client's.
    """
    return GENERATION_STATUS.get(outcome, INTERNAL_STATUS)


def status_for_retrieval(reason: RetrievalReason) -> int:
    """The HTTP status for a build-retrieval refusal."""
    return RETRIEVAL_STATUS.get(reason, INTERNAL_STATUS)


def status_for_delivery(reason: DeliveryReason) -> int:
    """The HTTP status for an artifact-delivery refusal."""
    return DELIVERY_STATUS.get(reason, INTERNAL_STATUS)


def status_for_failure(failure: str) -> int:
    """The HTTP status for a transport-contract ``failure`` value.

    An unrecognised value is a 500 rather than a guess: the contract's
    taxonomy is closed, so an unknown one means this map is out of date, which
    is a server problem and not the client's.
    """
    for member, status in FAILURE_STATUS.items():
        if member.value == failure:
            return status
    return INTERNAL_STATUS
