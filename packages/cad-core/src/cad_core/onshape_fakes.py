"""Adapter implementations that exist today. Neither one contacts Onshape.

* :class:`UnconfiguredOnshapeAdapter` -- the application's default: no adapter
  has been configured, so every operation reports ``NOT_CONFIGURED``.
* :class:`RecordingOnshapeAdapter` -- a deterministic in-memory double for unit
  tests. It records what was asked of it and returns predictable results.

Both are honest about what they are: every result they produce has
``reached_onshape=False``, and the handles the recording adapter mints are
plainly synthetic. Neither models Onshape geometry, and neither fabricates a
model summary -- the point is to exercise application flow, not to simulate a
CAD kernel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from cad_core.onshape_adapter import (
    AdapterResult,
    GeneratedFeatureScript,
    Handle,
    OperationStatus,
)

#: Prefix on every handle token the recording adapter mints, so a synthetic
#: reference can never be mistaken for a real Onshape identifier.
FAKE_TOKEN_PREFIX = "fake"

_NO_CONTACT = "no Onshape service was contacted"


class UnconfiguredOnshapeAdapter:
    """The adapter in force when nothing has been configured.

    This is the honest default state of the application: there is no Onshape
    integration, so every operation says so rather than failing obscurely.
    """

    _DETAIL = (
        "no Onshape adapter is configured; FeatureScript delivery is not "
        "available in this build"
    )

    def _unconfigured(self, operation: str) -> AdapterResult:
        return AdapterResult(
            operation=operation,
            status=OperationStatus.NOT_CONFIGURED,
            detail=self._DETAIL,
            reached_onshape=False,
        )

    def open_target(self, name: str) -> AdapterResult:
        return self._unconfigured("open_target")

    def submit_feature_studio_source(
        self, target: Handle, script: GeneratedFeatureScript
    ) -> AdapterResult:
        return self._unconfigured("submit_feature_studio_source")

    def commit_feature_studio(self, feature_studio: Handle) -> AdapterResult:
        return self._unconfigured("commit_feature_studio")

    def instantiate_feature(self, feature_studio: Handle) -> AdapterResult:
        return self._unconfigured("instantiate_feature")

    def request_model_summary(self, part_studio: Handle) -> AdapterResult:
        return self._unconfigured("request_model_summary")


@dataclass(frozen=True)
class RecordedOperation:
    """One operation the recording adapter was asked to perform."""

    operation: str
    name: Optional[str] = None
    handle: Optional[Handle] = None
    script: Optional[GeneratedFeatureScript] = None


class RecordingOnshapeAdapter:
    """Deterministic in-memory adapter for tests. Contacts nothing.

    Handle tokens are minted from a per-instance counter, so the same sequence
    of calls always produces the same tokens. Results are predictable and
    always carry ``reached_onshape=False``.

    Args:
        fail_at: Name of an operation that should report a failure instead of
            succeeding, letting tests exercise the failure statuses. ``None``
            means every operation succeeds.
        failure_status: Which status ``fail_at`` reports. Defaults to
            ``REMOTE_FAILED``.
    """

    def __init__(
        self,
        *,
        fail_at: Optional[str] = None,
        failure_status: OperationStatus = OperationStatus.REMOTE_FAILED,
    ) -> None:
        if failure_status is OperationStatus.SUCCEEDED:
            raise ValueError("failure_status must describe a failure, not success")
        self._fail_at = fail_at
        self._failure_status = failure_status
        self._records: List[RecordedOperation] = []
        self._counter = 0

    # --- what the test can inspect -----------------------------------------

    @property
    def recorded_operations(self) -> Tuple[RecordedOperation, ...]:
        """Every operation requested, in order."""
        return tuple(self._records)

    @property
    def operation_names(self) -> Tuple[str, ...]:
        """Just the operation names, in order."""
        return tuple(record.operation for record in self._records)

    @property
    def submitted_sources(self) -> Tuple[str, ...]:
        """The exact FeatureScript source text handed to this adapter."""
        return tuple(
            record.script.source
            for record in self._records
            if record.script is not None
        )

    # --- internals ----------------------------------------------------------

    def _mint(self, kind: str) -> Handle:
        self._counter += 1
        return Handle(kind=kind, token=f"{FAKE_TOKEN_PREFIX}-{kind}-{self._counter}")

    def _result(self, operation: str, *, kind: Optional[str] = None) -> AdapterResult:
        if operation == self._fail_at:
            return AdapterResult(
                operation=operation,
                status=self._failure_status,
                detail=(
                    f"recording adapter was configured to report "
                    f"{self._failure_status.value!r} for {operation!r}; {_NO_CONTACT}"
                ),
                reached_onshape=False,
            )
        return AdapterResult(
            operation=operation,
            status=OperationStatus.SUCCEEDED,
            detail=f"recorded {operation!r} in memory; {_NO_CONTACT}",
            reached_onshape=False,
            handle=self._mint(kind) if kind is not None else None,
        )

    # --- the boundary -------------------------------------------------------

    def open_target(self, name: str) -> AdapterResult:
        self._records.append(RecordedOperation("open_target", name=name))
        return self._result("open_target", kind="target")

    def submit_feature_studio_source(
        self, target: Handle, script: GeneratedFeatureScript
    ) -> AdapterResult:
        self._records.append(
            RecordedOperation(
                "submit_feature_studio_source", handle=target, script=script
            )
        )
        return self._result("submit_feature_studio_source", kind="feature_studio")

    def commit_feature_studio(self, feature_studio: Handle) -> AdapterResult:
        self._records.append(
            RecordedOperation("commit_feature_studio", handle=feature_studio)
        )
        return self._result("commit_feature_studio")

    def instantiate_feature(self, feature_studio: Handle) -> AdapterResult:
        self._records.append(
            RecordedOperation("instantiate_feature", handle=feature_studio)
        )
        return self._result("instantiate_feature", kind="part_studio")

    def request_model_summary(self, part_studio: Handle) -> AdapterResult:
        self._records.append(
            RecordedOperation("request_model_summary", handle=part_studio)
        )
        result = self._result("request_model_summary")
        if not result.succeeded:
            return result
        # Deliberately no geometry: this adapter does not model Onshape, so it
        # reports that no model information exists rather than inventing any.
        return AdapterResult(
            operation=result.operation,
            status=OperationStatus.SUCCEEDED,
            detail=(
                "no model information is available: this adapter records "
                f"operations only and does not model Onshape geometry; {_NO_CONTACT}"
            ),
            reached_onshape=False,
            data={},
        )
