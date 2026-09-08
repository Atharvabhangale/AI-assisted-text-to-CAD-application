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

from cad_api.artifacts import DeliveryReason

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

#: Statuses this application can return, for documentation and tests.
STATUSES: Tuple[int, ...] = (
    OK_STATUS,
    BAD_REQUEST_STATUS,
    NOT_FOUND_STATUS,
    UNPROCESSABLE_STATUS,
    INTERNAL_STATUS,
    UNAVAILABLE_STATUS,
)


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
