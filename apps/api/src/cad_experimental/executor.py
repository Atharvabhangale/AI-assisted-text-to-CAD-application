"""Execute a plan against a CAD backend, in the graph's order.

Stage 46 derived a deterministic topological order and nothing consumed it:
execution went through the V1 adapter, which walks the operation list. This
module is what consumes it.

```
OperationPlan
  -> feature_graph        deterministic order, cycles
  -> plan_history         which body each operation touches, what it consumes
  -> THIS MODULE          walks the order, holds one shape per live body
  -> CadBackend           create / cut / subtract / blend, and describe edges
  -> CAD kernel
```

Why a second path exists at all
-------------------------------
Not because the adapter was wrong. The adapter's job is to write a **V1 CAD
document**, and a V1 document can carry exactly two edge selectors -- `all`
and `axis_parallel` (Section C.7). Stage 47's semantic selectors cannot be
written into one without changing the V1 contract, which is the stable
branch's to change and not this experiment's.

So the two paths answer two different questions, and neither is a fallback
for the other:

* :func:`cad_experimental.build.build_plan` -- "what CAD document is this
  plan, and what does the existing service make of it". Unchanged, still the
  path the comparison instrument uses.
* this module -- "what solid is this plan", for a plan whose selectors are
  richer than a V1 document can express.

:func:`cad_experimental.build.build_plan` chooses between them on one
explicit question -- whether every selector in the plan is expressible in V1
-- and records which it took. There is no silent fallback: a plan that asks
for a semantic selector always executes here, and a plan that does not always
goes through the adapter.

What this module does NOT do
----------------------------
**No geometry, no kernel, no CadQuery.** Every geometric act is a method call
on :class:`~cad_experimental.cad_backend.CadBackend`, and every decision about
*which edges* is :mod:`cad_experimental.edge_semantics` working on plain
numbers. Swapping the backend swaps the engine and nothing else.

**No second history.** Which body an operation touches, what it declares and
what it consumes all come from :func:`cad_experimental.history.plan_history`.
This module adds exactly one thing history cannot hold: the backend's shape
for each live body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .cad_backend import BackendError, CadBackend, resolve_backend
from .edge_semantics import Resolution, resolve
from .graph import feature_graph
from .history import plan_history
from .pattern import PatternError, instance_positions
from .plan import (
    BOX,
    CHAMFER,
    CYLINDER,
    DEFAULT_AXIS,
    EXECUTABLE_TYPES,
    EXECUTOR_ONLY_TYPES,
    FILLET,
    PATTERN,
    SUBTRACT,
    UNION,
    THROUGH_HOLE,
    OperationPlan,
    PlanStatus,
    operation_type,
)

#: Why an execution stopped. Machine-readable, and deliberately distinct from
#: the plan's P-codes: a plan can be perfectly well formed and still meet a
#: solid that cannot honour it.
UNSUPPORTED = "unsupported"        # the backend has no path for an operation
CYCLE = "cycle"                    # the graph cannot be ordered
NOT_GENERATED = "not_generated"    # a refusal has no geometry
MISSING_BODY = "missing_body"      # a reference resolved to no live shape
BACKEND = "backend"                # the engine refused (E1-E5 territory)
PLACEMENT = "placement"            # a pattern's instances cannot be placed
MULTIPLE_SOLIDS = "multiple_solids"  # the plan left more than one live body

#: Resolution codes (R1-R3) are passed through from
#: :mod:`cad_experimental.edge_semantics` unchanged, so a caller sees the
#: same code the resolver produced rather than a re-coded one.


@dataclass(frozen=True)
class ExecutionFailure:
    """One reason an execution stopped, and where."""

    code: str
    message: str
    operation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "operation": self.operation,
        }


@dataclass(frozen=True)
class ExecutedBody:
    """One body that survived, and what the backend measured of it."""

    id: str
    features: Tuple[str, ...]
    measurement: Any

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "features": list(self.features),
            "measurement": (
                self.measurement.to_dict() if self.measurement else None
            ),
        }


@dataclass(frozen=True)
class ExecutionResult:
    """What executing a plan produced.

    ``shapes`` holds backend objects and is deliberately absent from
    :meth:`to_dict`: a kernel shape never crosses a transport boundary, which
    is the same rule the stable path keeps.
    """

    order: Tuple[str, ...] = ()
    bodies: Tuple[ExecutedBody, ...] = ()
    failure: Optional[ExecutionFailure] = None
    shapes: Mapping[str, Any] = field(default_factory=dict)
    backend: str = ""

    #: Every selector this run resolved, by operation id -- what it
    #: considered, what it chose, and what it refused. The diagnostic an
    #: agent needs to see why an edge operation did what it did.
    selections: Mapping[str, Resolution] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.failure is None

    @property
    def part(self) -> Optional[str]:
        """The single live body, when the plan left exactly one."""
        return self.bodies[0].id if len(self.bodies) == 1 else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "succeeded": self.succeeded,
            "backend": self.backend,
            "order": list(self.order),
            "bodies": [body.to_dict() for body in self.bodies],
            "failure": self.failure.to_dict() if self.failure else None,
            "selections": {
                name: resolution.to_dict()
                for name, resolution in self.selections.items()
            },
        }


def execute_plan(
    plan: OperationPlan,
    *,
    backend: Optional[CadBackend] = None,
    part_name: str = "experimental-part",
) -> ExecutionResult:
    """Build ``plan`` on ``backend``, walking the graph's own order.

    Never raises for a plan's fault or a kernel's: both are reported as a
    :class:`ExecutionFailure`, because an executor that threw would make the
    caller's error handling depend on which layer failed.
    """
    engine = backend if backend is not None else resolve_backend()
    state = _State(engine)

    if plan.status is not PlanStatus.GENERATED:
        return state.stopped(
            NOT_GENERATED,
            f"a `{plan.status.value}` plan has no geometry to build",
        )
    if not plan.operations:
        return state.stopped(
            NOT_GENERATED, "a generated plan with no operations has no geometry"
        )

    unexecutable = tuple(dict.fromkeys(
        getattr(operation, "TYPE", None)
        for operation in plan.operations
        if getattr(operation, "TYPE", None) not in EXECUTABLE_TYPES
    ))
    if unexecutable:
        return state.stopped(
            UNSUPPORTED,
            "this backend has no execution path for: "
            + ", ".join(str(kind) for kind in unexecutable),
        )

    graph = feature_graph(plan)
    order = graph.topological_order()
    if len(order) != len(graph.indices()):
        groups = "; ".join(", ".join(group) for group in graph.cycles())
        return state.stopped(
            CYCLE, f"the dependency graph cannot be ordered: {groups}"
        )
    state.order = order

    history = plan_history(plan)
    by_id = {operation.id: operation for operation in plan.operations}

    for identifier in order:
        operation = by_id.get(identifier)
        step = history.step(identifier)
        if operation is None or step is None:
            return state.stopped(
                MISSING_BODY, f"{identifier!r} is not an operation", identifier
            )
        failure = _apply(state, operation, step, by_id)
        if failure is not None:
            return state.stopped(
                failure.code, failure.message, failure.operation
            )

    return state.finished(history, part_name)


class _State:
    """The shapes, and the bookkeeping that turns them into a result."""

    def __init__(self, backend: CadBackend) -> None:
        self.backend = backend
        self.shapes: Dict[str, Any] = {}
        self.order: Tuple[str, ...] = ()
        self.selections: Dict[str, Resolution] = {}

    def stopped(
        self, code: str, message: str, operation: Optional[str] = None
    ) -> ExecutionResult:
        return ExecutionResult(
            order=self.order,
            failure=ExecutionFailure(code, message, operation),
            backend=getattr(self.backend, "name", ""),
            selections=dict(self.selections),
        )

    def finished(self, history: Any, part_name: str) -> ExecutionResult:
        bodies = []
        for body in history.live_bodies:
            shape = self.shapes.get(body.id)
            if shape is None:
                continue
            bodies.append(ExecutedBody(
                id=body.id,
                features=body.features,
                measurement=self.backend.measure(shape),
            ))

        # The executor's own S9. The document path gets the single-solid rule
        # for free -- `plan_to_document` emits a V1 document and the V1
        # validator refuses two solids. This path never builds a document, so
        # until now NOTHING applied the rule here, and Stage 45 said as much:
        # "a plan containing a profile operation is refused by the adapter
        # before a document exists, so S9 never runs and a leftover solid is
        # otherwise invisible."
        #
        # `union` turned that from invisible into wrong. A plan that fuses
        # two boxes and leaves a third standing somewhere else executed with
        # `succeeded=True` and no failure, and the caller then took
        # `bodies[0]` -- so the third solid vanished from the render, the
        # measurement and every export, with nothing anywhere saying so. A
        # part quietly missing a piece is the exact thing this project's "no
        # silent geometry behaviour" rule exists to prevent.
        #
        # Reported, never repaired: the extra body is NOT fused in, and no
        # body is dropped to make the count come out. Both would be guessing
        # at what the author meant. They are named instead.
        if len(bodies) > 1:
            named = ", ".join(repr(body.id) for body in bodies)
            return ExecutionResult(
                order=self.order,
                bodies=tuple(bodies),
                shapes=dict(self.shapes),
                failure=ExecutionFailure(
                    MULTIPLE_SOLIDS,
                    f"the plan leaves {len(bodies)} separate solids "
                    f"({named}); a part is exactly one. Join them with a "
                    f"`union`, or remove the ones that are not part of it",
                    bodies[-1].id,
                ),
                backend=getattr(self.backend, "name", ""),
                selections=dict(self.selections),
            )

        return ExecutionResult(
            order=self.order,
            bodies=tuple(bodies),
            shapes=dict(self.shapes),
            backend=getattr(self.backend, "name", ""),
            selections=dict(self.selections),
        )


def _apply(
    state: _State, operation: Any, step: Any, by_id: Mapping[str, Any]
) -> Optional[ExecutionFailure]:
    """One operation. Returns a failure, or ``None`` and mutates the state."""
    kind = getattr(operation, "TYPE", None)
    try:
        if kind == BOX:
            state.shapes[operation.id] = state.backend.create_box(
                (operation.x, operation.y, operation.z),
                _point(operation.position),
            )
            return None

        if kind == CYLINDER:
            state.shapes[operation.id] = state.backend.create_cylinder(
                operation.diameter,
                operation.height,
                _point(operation.position),
                operation.axis or DEFAULT_AXIS,
            )
            return None

        body = step.modifies
        target = state.shapes.get(body)
        if target is None:
            return ExecutionFailure(
                MISSING_BODY,
                f"{body!r} names no live body at this point",
                operation.id,
            )

        if kind == THROUGH_HOLE:
            state.shapes[body] = state.backend.through_hole(
                target,
                operation.diameter,
                _point(operation.position),
                operation.axis or DEFAULT_AXIS,
            )
            return None

        if kind in (SUBTRACT, UNION):
            tools = []
            for tool in step.consumes:
                shape = state.shapes.get(tool)
                if shape is None:
                    return ExecutionFailure(
                        MISSING_BODY,
                        f"{tool!r} names no live body to combine",
                        operation.id,
                    )
                tools.append(shape)
            state.shapes[body] = (
                state.backend.union(target, tools) if kind == UNION
                else state.backend.subtract(target, tools)
            )
            for tool in step.consumes:
                state.shapes.pop(tool, None)
            return None

        if kind in (FILLET, CHAMFER):
            return _blend(state, operation, kind, body, target)

        if kind == PATTERN:
            return _pattern(state, operation, body, by_id)

    except BackendError as exc:
        return ExecutionFailure(BACKEND, str(exc), operation.id)
    except NotImplementedError as exc:
        # A capability this backend does not implement at all. It is NOT a
        # `BackendError`, so without this clause it escaped `execute_plan`
        # entirely -- breaking the contract three lines of docstring up
        # ("Never raises for a plan's fault or a kernel's") and reaching the
        # caller as a bare Python error with no operation attached.
        #
        # It is reported as UNSUPPORTED rather than BACKEND because the two
        # say different things and the difference is the one this project
        # cares about: BACKEND means the engine tried and the geometry
        # refused; UNSUPPORTED means the engine never had a path to try. The
        # plan is valid in both cases, and neither is a plan error.
        detail = str(exc) or "the backend does not implement this capability"
        return ExecutionFailure(
            UNSUPPORTED,
            f"this backend has no path for {kind!r}: {detail}",
            operation.id,
        )

    return ExecutionFailure(
        UNSUPPORTED, f"no execution path for {kind!r}", operation.id
    )


def _blend(
    state: _State, operation: Any, kind: str, body: str, target: Any
) -> Optional[ExecutionFailure]:
    """A fillet or a chamfer, through the semantic resolver.

    The whole point of the stage, in six lines: the backend says what its
    edges ARE, the resolver says which of them the selector MEANS, and the
    backend acts on exactly those. Nothing picks the first edge the kernel
    happened to return, and nothing silently drops one it cannot use.
    """
    facts = state.backend.describe_edges(target)
    resolution = resolve(operation.edges.semantic(), facts)
    state.selections[operation.id] = resolution
    if not resolution.ok:
        return ExecutionFailure(
            resolution.code, resolution.message, operation.id
        )

    edges = state.backend.edges_at(target, resolution.indices)
    if kind == FILLET:
        state.shapes[body] = state.backend.fillet_edges(
            target, operation.radius, edges
        )
    else:
        state.shapes[body] = state.backend.chamfer_edges(
            target, operation.distance, edges
        )
    return None


def _pattern(
    state: _State, operation: Any, body: str, by_id: Mapping[str, Any]
) -> Optional[ExecutionFailure]:
    """A pattern: the source's own operation, applied at each extra place.

    Instance 0 is the source and was applied when the source ran, so only
    instances 1 onward are applied here -- the same rule the adapter follows,
    from the same :func:`~cad_experimental.pattern.instance_positions`.
    """
    source = by_id.get(operation.source)
    if source is None or getattr(source, "TYPE", None) != THROUGH_HOLE:
        return ExecutionFailure(
            UNSUPPORTED,
            f"{operation.source!r} is not a feature this backend can repeat",
            operation.id,
        )
    try:
        positions = instance_positions(
            operation.placement, source.position, operation.count
        )
    except PatternError as exc:
        return ExecutionFailure(PLACEMENT, str(exc), operation.id)

    for position in positions[1:]:
        target = state.shapes.get(body)
        if target is None:
            return ExecutionFailure(
                MISSING_BODY,
                f"{body!r} names no live body at this point",
                operation.id,
            )
        state.shapes[body] = state.backend.through_hole(
            target,
            source.diameter,
            (position.x, position.y, position.z),
            source.axis or DEFAULT_AXIS,
        )
    return None


def _point(position: Any) -> Tuple[float, float, float]:
    """A position as a plain triple. Absent means the contract's default."""
    if position is None:
        return (0.0, 0.0, 0.0)
    return (position.x, position.y, position.z)


def plan_needs_executor(plan: OperationPlan) -> bool:
    """Whether this plan is beyond what a V1 document can carry.

    The one explicit question :func:`cad_experimental.build.build_plan` asks
    to choose a path. Stated as a property of the plan rather than decided by
    catching a failure, so the choice is visible before anything is built.

    Two ways a plan outruns the document, and they are different facts:

    * a **selector** richer than Section C.7 -- `straight`, `circular`, or a
      rim's `position`. The operation exists in V1; the way it names its
      edges does not.
    * an **operation** with no document form at all, today `union`. V1 has no
      join, so there is nothing to write down rather than a different way of
      writing it.

    Both answer yes here, because the routing question is the same one: can
    a V1 document carry this? Why it cannot is reported elsewhere.
    """
    for operation in plan.operations:
        if operation_type(operation) in EXECUTOR_ONLY_TYPES:
            return True
        selector = getattr(operation, "edges", None)
        if selector is not None and not selector.is_v1:
            return True
    return False


__all__ = [
    "BACKEND",
    "CYCLE",
    "ExecutedBody",
    "ExecutionFailure",
    "ExecutionResult",
    "MISSING_BODY",
    "NOT_GENERATED",
    "PLACEMENT",
    "UNSUPPORTED",
    "execute_plan",
    "plan_needs_executor",
]
