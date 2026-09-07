"""Application-side boundary for eventually delivering FeatureScript to Onshape.

**Nothing in this module contacts Onshape.** There is no network code, no
authentication, no MCP client, and no Onshape document handling. What exists
here is the *shape* of the conversation the application will eventually need,
expressed so that the rest of ``cad_core`` depends on this internal abstraction
rather than on any particular remote implementation:

```
FeatureScript generator (cad_core.featurescript)
        |
        v
OnshapeAdapter  <- this module
        |
        v
future MCP implementation (does not exist yet)
```

Why the indirection exists
--------------------------
At the time of writing there is no Onshape MCP tool in this environment and no
Onshape connector in the MCP registry, so the wire-level vocabulary of any such
service -- its tool names, request and response shapes, authentication model and
document operations -- is unknown. Rather than guess at them, the operations
below are named in *this project's* vocabulary and describe what the
one-box workflow needs. A future client implements this protocol and translates
into whatever the real service turns out to require; nothing outside that future
client has to change.

Consequently this module deliberately encodes **no** MCP tool name, endpoint,
URL, header, credential or payload format. :class:`Handle` is opaque for the
same reason: it is *not* modelled on Onshape's document/workspace/element
identifiers, whose required shape is not established here.

Pipeline position
-----------------
The neutral CAD specification stays upstream. The sanctioned entry point is
:func:`deliver_part`, which takes a validated :class:`~cad_core.model.Part`,
generates its FeatureScript, and only then talks to an adapter. Raw
FeatureScript text is not a normal input to the application: an adapter accepts
a :class:`GeneratedFeatureScript`, and the supported way to obtain one is
:func:`featurescript_for`, which runs the Stage 3A generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple

try:  # pragma: no cover - Protocol is stdlib from 3.8; the fallback keeps 3.7 importable
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment]

    def runtime_checkable(cls):  # type: ignore[misc]
        return cls

from cad_core.featurescript import generate_featurescript
from cad_core.model import Part


class OperationStatus(Enum):
    """Outcome of one boundary operation.

    These describe the *boundary*, never the specification. A specification
    that fails validation (rules S1-S20) or falls outside the generator's
    supported subset never reaches an adapter and therefore never produces one
    of these values -- see :func:`deliver_part`.
    """

    #: The operation did what this adapter promises. For every adapter that
    #: exists today that means "recorded locally", never "Onshape accepted it".
    SUCCEEDED = "succeeded"

    #: No adapter has been configured for this application. The default state.
    NOT_CONFIGURED = "not_configured"

    #: An adapter is configured but the remote service cannot be reached.
    UNAVAILABLE = "unavailable"

    #: The remote service requires credentials that are not present.
    AUTHENTICATION_REQUIRED = "authentication_required"

    #: The remote service was reached and rejected or failed the operation.
    REMOTE_FAILED = "remote_failed"


#: The operations the one-box workflow needs, in the order it needs them.
#: These are this project's names for the steps, chosen so that no MCP tool
#: name is baked into the application.
OPERATIONS: Tuple[str, ...] = (
    "open_target",
    "submit_feature_studio_source",
    "commit_feature_studio",
    "instantiate_feature",
    "request_model_summary",
)

#: Kinds of thing an adapter can hand back a reference to.
HANDLE_KINDS: Tuple[str, ...] = ("target", "feature_studio", "part_studio")


@dataclass(frozen=True)
class Handle:
    """An opaque reference minted by an adapter and passed back to it.

    The ``token`` is meaningful only to the adapter that produced it. This is
    deliberately *not* a model of Onshape's document, workspace or element
    identifiers: their required shape has not been established, so nothing here
    pretends to know it.
    """

    kind: str
    token: str


@dataclass(frozen=True)
class GeneratedFeatureScript:
    """FeatureScript that cad-core generated from a validated part.

    Carrying this type rather than a bare ``str`` keeps the pipeline honest:
    an adapter accepts generated source, and :func:`featurescript_for` is the
    supported way to produce it. Arbitrary hand-written FeatureScript entering
    the application is not the normal workflow.
    """

    part_name: str
    feature_id: str
    source: str


@dataclass(frozen=True)
class AdapterResult:
    """What one boundary operation reports back."""

    operation: str
    status: OperationStatus
    detail: str

    #: True only if a real Onshape service was actually contacted. **Every
    #: implementation in this package sets this False**, so no result can be
    #: mistaken for evidence that Onshape accepted anything.
    reached_onshape: bool = False

    #: Reference to whatever the operation opened or created, when applicable.
    handle: Optional[Handle] = None

    #: Adapter-defined and opaque. Empty for every implementation here; no
    #: response shape is guessed, and no geometry is ever fabricated.
    data: Mapping[str, object] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status is OperationStatus.SUCCEEDED


@runtime_checkable
class OnshapeAdapter(Protocol):
    """What the application needs from Onshape, in the application's own terms.

    A future MCP-backed client implements this. Nothing that implements it
    today reaches Onshape.
    """

    def open_target(self, name: str) -> AdapterResult:
        """Create or open the place the work will live. Returns a ``target`` handle."""
        ...

    def submit_feature_studio_source(
        self, target: Handle, script: GeneratedFeatureScript
    ) -> AdapterResult:
        """Put generated FeatureScript into a Feature Studio in ``target``.

        Returns a ``feature_studio`` handle.
        """
        ...

    def commit_feature_studio(self, feature_studio: Handle) -> AdapterResult:
        """Commit the Feature Studio so its feature becomes usable."""
        ...

    def instantiate_feature(self, feature_studio: Handle) -> AdapterResult:
        """Use the generated custom feature to build the part.

        Returns a ``part_studio`` handle.
        """
        ...

    def request_model_summary(self, part_studio: Handle) -> AdapterResult:
        """Ask for information about the resulting model, for verification."""
        ...


def featurescript_for(part: Part) -> GeneratedFeatureScript:
    """Generate FeatureScript for ``part`` and wrap it for the boundary.

    Raises whatever :func:`cad_core.featurescript.generate_featurescript`
    raises: :class:`TypeError` for a non-``Part``, and
    :class:`~cad_core.featurescript.UnsupportedPartError` for a part outside the
    supported subset. Those are upstream failures and are never reported as an
    :class:`OperationStatus`.
    """
    source = generate_featurescript(part)
    return GeneratedFeatureScript(
        part_name=part.name,
        feature_id=part.features[0].id,
        source=source,
    )


@dataclass(frozen=True)
class DeliveryReport:
    """The ordered outcome of running the workflow against one adapter."""

    script: GeneratedFeatureScript
    results: Tuple[AdapterResult, ...]

    @property
    def completed(self) -> bool:
        """True if every operation in :data:`OPERATIONS` succeeded."""
        return len(self.results) == len(OPERATIONS) and all(
            result.succeeded for result in self.results
        )

    @property
    def reached_onshape(self) -> bool:
        """True only if some operation actually contacted Onshape."""
        return any(result.reached_onshape for result in self.results)

    @property
    def stopped_at(self) -> Optional[AdapterResult]:
        """The first non-successful result, or ``None`` if all succeeded."""
        for result in self.results:
            if not result.succeeded:
                return result
        return None


def deliver_part(
    part: Part, adapter: OnshapeAdapter, *, target_name: Optional[str] = None
) -> DeliveryReport:
    """Run the one-box workflow for ``part`` against ``adapter``.

    Generation happens first, so a part that fails validation or falls outside
    the supported subset raises before any adapter operation is attempted --
    upstream failures never reach the boundary and never masquerade as remote
    failures.

    The operations run in :data:`OPERATIONS` order and stop at the first
    non-successful result.
    """
    script = featurescript_for(part)
    name = target_name if target_name is not None else script.part_name
    results = []

    opened = adapter.open_target(name)
    results.append(opened)
    if not opened.succeeded or opened.handle is None:
        return DeliveryReport(script=script, results=tuple(results))

    submitted = adapter.submit_feature_studio_source(opened.handle, script)
    results.append(submitted)
    if not submitted.succeeded or submitted.handle is None:
        return DeliveryReport(script=script, results=tuple(results))

    committed = adapter.commit_feature_studio(submitted.handle)
    results.append(committed)
    if not committed.succeeded:
        return DeliveryReport(script=script, results=tuple(results))

    built = adapter.instantiate_feature(submitted.handle)
    results.append(built)
    if not built.succeeded or built.handle is None:
        return DeliveryReport(script=script, results=tuple(results))

    results.append(adapter.request_model_summary(built.handle))
    return DeliveryReport(script=script, results=tuple(results))
