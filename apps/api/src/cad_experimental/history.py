"""The plan's dependency and history graph. Derived, never authoritative.

An operation plan has always *been* a history: Section B.4's solid set is
walked in order, a constructive operation adds a solid named by its own id, a
modifier replaces its target in place and keeps the target's id, and a
subtract consumes its tools so nothing may name them again. Rules P9-P14 and
P23 judge references against exactly that walk.

What did not exist was anywhere to *see* it. The walk lived inside
:func:`cad_experimental.validation.validate_plan` as four local dictionaries,
so a plan could be judged as a chain and then only ever reported as a flat
list. This module gives the walk a name, a type and one implementation.

**One simulation, not two.** :mod:`cad_experimental.validation` consumes
:func:`walk` rather than keeping its own copy. That is the whole reason this
module is worth having: a second walk would be a second opinion about what
"consumed" means, and the two would drift the first time an operation type
was added.

What this module is not
-----------------------
It computes **no geometry** and makes **no judgement**. Every field here is a
restatement of what the plan says, in the order the plan says it. A history
can be computed for an invalid plan and will simply describe the invalid
thing -- :func:`cad_experimental.validation.validate_plan` decides validity,
and the V1 validator decides whether the resulting document is valid CAD.

In particular this module does **not** enforce the single-solid rule. A plan
that leaves two solids gets a :attr:`PlanHistory.terminal_solids` of length
two, reported as the fact it is. S9 remains the V1 validator's, on the
converted document; see :attr:`PlanHistory.terminal_solids` for the one case
that never reaches it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from .graph import modified_body, references_of
from .plan import (
    CONSUMING_TYPES,
    PART,
    PROFILE_TYPES,
    SOLID_DECLARING_TYPES,
    OperationPlan,
    tools_of,
)


def _kind(operation: object) -> Optional[str]:
    """The operation's type, defensively.

    ``plan.operation_type`` would raise on an object without ``TYPE``. This
    module never raises: an unrecognised operation simply contributes nothing
    to the solid set, and :mod:`cad_experimental.validation` reports it as
    P3. A walk that refused to continue would hide every later problem
    behind the first.
    """
    return getattr(operation, "TYPE", None)


@dataclass(frozen=True)
class HistoryState:
    """The solid set and what has become of it, at one point in the plan.

    Every mapping is ``id -> the index of the operation that put it there``,
    because a useful message says *where*, not merely *that*.

    :attr:`declared` covers the **whole** plan and never changes: knowing
    that an id appears later is what tells a forward reference (P10) apart
    from an id that does not exist at all (P9). The other three evolve.

    The mappings belong to the walk and are handed out read-only. A caller
    that mutated one would be editing history rather than reading it.
    """

    #: Every operation id in the plan, mapped to where it FIRST appears.
    declared: Mapping[str, int]

    #: Ids that name a solid right now. A modifier's own id is never here:
    #: its result keeps the target's id, which is already present.
    solids: Mapping[str, int]

    #: Ids that name a profile. A profile is not a solid and never becomes
    #: one -- an extrude makes a new solid and leaves the profile alone.
    profiles: Mapping[str, int]

    #: Ids a subtract has consumed, mapped to the subtract that took them.
    consumed: Mapping[str, int]


@dataclass(frozen=True)
class OperationStep:
    """One operation's place in the chain: what it used and what it left."""

    index: int
    id: str
    type: str

    #: The ids this operation names directly -- its target, and a
    #: tool-taking operation's tools in list order. The edges of the
    #: dependency graph.
    depends_on: Tuple[str, ...] = ()

    #: Ids this operation removed from the solid set. Every type in
    #: :data:`CONSUMING_TYPES` does this -- ``subtract`` and, since Stage 61,
    #: ``union`` -- and only for tools that were actually live: consuming an
    #: unresolved reference would invent a second, misleading problem.
    #:
    #: Stated as "only a subtract does this" until Stage 62's audit, in the
    #: one module that exists so the walk and the graph cannot disagree
    #: about what "consumed" means.
    consumes: Tuple[str, ...] = ()

    #: The solid this operation brought into being, named by its own id.
    declares_solid: Optional[str] = None

    #: The profile this operation brought into being. Never a solid.
    declares_profile: Optional[str] = None

    #: The solid this operation replaced in place. The result keeps THIS id,
    #: not the operation's own, which is why a modifier declares nothing.
    modifies: Optional[str] = None

    solids_before: Tuple[str, ...] = ()
    solids_after: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "id": self.id,
            "type": self.type,
            "depends_on": list(self.depends_on),
            "consumes": list(self.consumes),
            "declares_solid": self.declares_solid,
            "declares_profile": self.declares_profile,
            "modifies": self.modifies,
            "solids_before": list(self.solids_before),
            "solids_after": list(self.solids_after),
        }


@dataclass(frozen=True)
class Body:
    """One solid, and the features that shaped it.

    Groundwork rather than assembly support. Nothing in this project builds
    more than one body today -- rule S9 requires a plan to end with exactly
    one -- but nothing here *assumes* one either: bodies are a tuple, a
    feature names the body it belongs to, and "the part" is a query rather
    than a fact. When assemblies arrive, the question they ask first is
    which body a feature belongs to, and that is answered here.

    The body's :attr:`id` is the id of the operation that created it, and
    stays that id for life: every modifier keeps its target's id, so a body
    that has been drilled, cut and filleted is still the same body with the
    same name. That is why :attr:`origin` and :attr:`id` agree today and are
    still recorded separately -- a later stage that copies or splits a body
    would need them to differ.
    """

    id: str
    origin: str
    origin_index: int

    #: The operations that shaped this body, in order, starting with the one
    #: that created it. Feature ownership, explicitly.
    features: Tuple[str, ...] = ()

    #: Whether the body still exists at the end of the plan. A tool consumed
    #: by a subtract does not.
    live: bool = True

    #: The subtract that consumed it, when it was.
    consumed_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "origin": self.origin,
            "origin_index": self.origin_index,
            "features": list(self.features),
            "live": self.live,
            "consumed_by": self.consumed_by,
        }


@dataclass(frozen=True)
class PlanHistory:
    """The whole chain: every step, and what the plan ended up holding."""

    steps: Tuple[OperationStep, ...] = ()

    #: The solids still standing after the last operation, in the order they
    #: were declared.
    #:
    #: A finished part has exactly one. Two means a leftover -- most often a
    #: cutter that was made and never subtracted. This module reports that
    #: and does not rule on it: S9 does, on the converted document.
    #:
    #: Except in one case, and it is the reason this attribute is worth
    #: having. A plan containing a profile operation is refused by the
    #: adapter before any document exists, so the V1 validator never runs and
    #: S9 never fires. For those plans this is the only place a leftover
    #: solid is visible at all.
    terminal_solids: Tuple[str, ...] = ()

    profiles: Tuple[str, ...] = ()
    consumed: Tuple[str, ...] = ()

    #: Every body the plan ever created, in creation order, live or not.
    bodies: Tuple[Body, ...] = ()

    #: The ids a ``part`` declaration names, in the order they are declared,
    #: without duplicates.
    #:
    #: **Reported, never judged** -- exactly like :attr:`terminal_solids`.
    #: Whether each one is live, unique, or complete is the validator's
    #: answer (P33-P35), and whether more than one live body is permitted is
    #: the executor's. This says only what the plan declared, so a malformed
    #: plan is described rather than refused, which is the whole contract of
    #: this module.
    declared_bodies: Tuple[str, ...] = ()

    @property
    def declares_bodies(self) -> bool:
        """Whether the plan declares its bodies at all.

        A plan that does not means exactly what every plan meant before
        declarations existed: one body, and a second live solid is a
        leftover. That is why this is a question worth asking rather than a
        count -- the absence of a declaration is itself the statement.
        """
        return bool(self.declared_bodies)

    def is_declared(self, body_id: str) -> bool:
        return body_id in self.declared_bodies

    @property
    def live_bodies(self) -> Tuple[Body, ...]:
        """The bodies still standing. Length one for a finished V1 part."""
        return tuple(body for body in self.bodies if body.live)

    def body(self, body_id: str) -> Optional[Body]:
        for body in self.bodies:
            if body.id == body_id:
                return body
        return None

    def owner_of(self, operation_id: str) -> Optional[str]:
        """Which body this operation belongs to, if any.

        A constructive operation owns the body it created; a modifier belongs
        to the body it changed; a pattern belongs to the body its source
        belongs to. A sketch belongs to none -- a profile is not a body.
        """
        for body in self.bodies:
            if operation_id in body.features:
                return body.id
        return None

    def step(self, operation_id: str) -> Optional[OperationStep]:
        """The step for one operation id, or ``None``.

        The first, if a malformed plan repeats an id -- the same one P2
        reports as the original.
        """
        for step in self.steps:
            if step.id == operation_id:
                return step
        return None

    def dependents(self, operation_id: str) -> Tuple[str, ...]:
        """The ids that name ``operation_id`` directly, in plan order."""
        return tuple(
            step.id for step in self.steps
            if operation_id in step.depends_on
        )

    def producers(self, solid_id: str) -> Tuple[int, ...]:
        """Indices of the steps that declared or changed ``solid_id``.

        A solid is produced once and then changed by each modifier that
        targets it, so this is its edit history in order.
        """
        return tuple(
            step.index for step in self.steps
            if solid_id in (step.declares_solid, step.declares_profile,
                            step.modifies)
        )

    def derivation(self, solid_id: str) -> Tuple[str, ...]:
        """Every operation whose effect is present in ``solid_id``, in order.

        The transitive closure backwards through the graph: the operation
        that declared the solid, every modifier that has since changed it,
        and -- through those modifiers' own references -- the operations that
        made the tools they consumed and the profiles they swept.

        This is the answer to "what is this solid made of", and it is why the
        plan is a history rather than a list. For a base box, a cutter, the
        subtract that removes it and a fillet, the derivation of the base is
        all four, in plan order: the cutter is part of the base's history
        even though the cutter itself no longer exists.

        Empty for an id that never named anything.
        """
        wanted = set(self.producers(solid_id))
        if not wanted:
            return ()
        by_index = {step.index: step for step in self.steps}
        frontier = list(wanted)
        while frontier:
            step = by_index[frontier.pop()]
            for reference in step.depends_on:
                for producer in self.producers(reference):
                    # Strictly earlier only. References always are on a valid
                    # plan; on an invalid one this keeps the walk finite
                    # rather than trusting P10 to have run.
                    if producer < step.index and producer not in wanted:
                        wanted.add(producer)
                        frontier.append(producer)
        return tuple(
            step.id for step in self.steps if step.index in wanted
        )

    @property
    def depth(self) -> int:
        """The longest chain of operations any one result rests on.

        One for a single box; three for a box, a subtract and a fillet. A
        measure of how chained a plan actually is, which a count of
        operations is not -- four unrelated boxes have depth one.
        """
        longest: Dict[int, int] = {}
        for step in self.steps:
            best = 0
            for reference in step.depends_on:
                for producer in self.producers(reference):
                    if producer < step.index:
                        best = max(best, longest.get(producer, 1))
            longest[step.index] = best + 1
        return max(longest.values(), default=0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "terminal_solids": list(self.terminal_solids),
            "profiles": list(self.profiles),
            "consumed": list(self.consumed),
            "depth": self.depth,
            "bodies": [body.to_dict() for body in self.bodies],
            "declared_bodies": list(self.declared_bodies),
        }


def _ordered(mapping: Mapping[str, int]) -> Tuple[str, ...]:
    """Ids in the order the operations that produced them appear."""
    return tuple(
        name for name, _ in sorted(mapping.items(), key=lambda pair: pair[1])
    )


def _references(operation: object, kind: Optional[str]) -> Tuple[str, ...]:
    """The ids one operation names, in edge order, without duplicates.

    Delegated to :func:`cad_experimental.graph.references_of` rather than
    re-derived: which key on which operation type is a reference is the
    graph's table to own, and a second answer here would be a second answer.
    """
    return tuple(dict.fromkeys(
        reference.id for reference in references_of(operation, kind)
    ))


def walk(
    operations: Tuple[object, ...]
) -> Iterator[Tuple[int, object, HistoryState]]:
    """Step through a plan, yielding the state **before** each operation.

    The one implementation of Section B.4's solid set, shared by this module
    and by :func:`cad_experimental.validation.validate_plan`. A reference is
    judged against the state yielded with it; the state is then advanced by
    that operation and the next is yielded.

    Never raises and never judges. An operation naming something that does
    not exist simply does not change the set -- saying so is the validator's
    job, and a walk that refused to continue would hide every later problem
    behind the first.
    """
    declared: Dict[str, int] = {}
    for index, operation in enumerate(operations):
        declared.setdefault(operation.id, index)

    solids: Dict[str, int] = {}
    profiles: Dict[str, int] = {}
    consumed: Dict[str, int] = {}

    for index, operation in enumerate(operations):
        yield index, operation, HistoryState(
            declared=declared,
            solids=solids,
            profiles=profiles,
            consumed=consumed,
        )
        _advance(index, operation, solids, profiles, consumed)


def _consumed_by(
    operation: object, kind: Optional[str], solids: Mapping[str, int]
) -> Tuple[str, ...]:
    """The ids ``operation`` takes out of the solid set, in list order.

    The consumption rule in one place, so the walk and the graph cannot come
    to disagree about what a subtract took. Only tools that were actually
    live are consumed: consuming an unresolved reference would invent a
    second, misleading problem downstream, and a tool that is also the
    target is P14's to report, not a consumption.
    """
    if kind not in CONSUMING_TYPES:
        return ()
    target = getattr(operation, "target", None)
    return tuple(
        tool for tool in tools_of(operation)
        if tool in solids and tool != target
    )


def _advance(
    index: int,
    operation: object,
    solids: Dict[str, int],
    profiles: Dict[str, int],
    consumed: Dict[str, int],
) -> None:
    """Apply one operation's effect on the solid set. Section B.4, in code."""
    kind = _kind(operation)
    if kind in SOLID_DECLARING_TYPES:
        # A box or cylinder makes a solid from nothing; an extrude or
        # revolve makes one from a profile. Either way the new solid is
        # named by the operation's OWN id, so a later fillet can target it
        # -- and the profile it came from is untouched and still a profile.
        solids.setdefault(operation.id, index)
    elif kind in PROFILE_TYPES:
        # A profile is not a solid: nothing may fillet it, subtract it or
        # count it toward the single-solid rule.
        profiles.setdefault(operation.id, index)
    else:
        # The history step. Every tool a subtract legitimately used is gone
        # from here on: a later operation naming it gets P12, not a second
        # chance.
        for tool in _consumed_by(operation, kind, solids):
            solids.pop(tool, None)
            consumed[tool] = index


def plan_history(plan: OperationPlan) -> PlanHistory:
    """The dependency and history graph of one plan.

    Pure derivation: computed from the plan alone, with no geometry, no
    kernel and no verdict. Safe on an invalid plan, which it describes
    rather than refuses.
    """
    records: List[Dict[str, Any]] = []
    last_state: Optional[HistoryState] = None
    by_id = {operation.id: operation for operation in plan.operations}
    bodies: List[Dict[str, Any]] = []
    body_index: Dict[str, int] = {}
    declared: Dict[str, int] = {}

    for index, operation, state in walk(plan.operations):
        last_state = state
        kind = _kind(operation)
        changed = modified_body(operation, kind, by_id)
        before = _ordered(state.solids)
        # Each step's outcome is the NEXT step's starting point, so the
        # "after" view is filled in on the following turn -- and after the
        # loop for the last one, from the final state.
        if records:
            records[-1]["solids_after"] = before
        records.append({
            "index": index,
            "id": operation.id,
            "type": kind,
            "depends_on": _references(operation, kind),
            "consumes": _consumed_by(operation, kind, state.solids),
            "declares_solid": (
                operation.id if kind in SOLID_DECLARING_TYPES else None
            ),
            "declares_profile": (
                operation.id if kind in PROFILE_TYPES else None
            ),
            "modifies": changed,
            "solids_before": before,
            "solids_after": (),
        })

        # Body bookkeeping, in the same single pass. A constructive or
        # profile-solid operation starts a body; anything that changes one
        # joins its feature list; a consumed body stops being live and
        # remembers what took it.
        if kind in SOLID_DECLARING_TYPES and operation.id not in body_index:
            body_index[operation.id] = len(bodies)
            bodies.append({
                "id": operation.id,
                "origin": operation.id,
                "origin_index": index,
                "features": [operation.id],
                "live": True,
                "consumed_by": None,
            })
        elif changed is not None and changed in body_index:
            bodies[body_index[changed]]["features"].append(operation.id)
        # A declaration, in the same single pass. It creates no body, joins
        # no body's feature list and consumes nothing -- it records an
        # intention about one. `setdefault` keeps the FIRST declaration's
        # position, so a plan that declares the same body twice is described
        # here once and reported by P33 there.
        if kind == PART:
            target = getattr(operation, "target", None)
            if isinstance(target, str) and target:
                declared.setdefault(target, index)

        for taken in _consumed_by(operation, kind, state.solids):
            if taken in body_index:
                record = bodies[body_index[taken]]
                record["live"] = False
                record["consumed_by"] = operation.id

    # The state is yielded BEFORE each operation, and the mappings inside it
    # belong to the walk. By the time the loop ends the walk has advanced
    # past the last operation, so re-reading the final state -- rather than
    # a snapshot taken inside the loop -- is what includes that operation's
    # own effect.
    if last_state is None:
        return PlanHistory()
    final_solids = _ordered(last_state.solids)
    records[-1]["solids_after"] = final_solids

    return PlanHistory(
        steps=tuple(OperationStep(**record) for record in records),
        terminal_solids=final_solids,
        profiles=_ordered(last_state.profiles),
        consumed=_ordered(last_state.consumed),
        bodies=tuple(
            Body(
                id=record["id"],
                origin=record["origin"],
                origin_index=record["origin_index"],
                features=tuple(record["features"]),
                live=record["live"],
                consumed_by=record["consumed_by"],
            )
            for record in bodies
        ),
        declared_bodies=_ordered(declared),
    )


__all__ = [
    "Body",
    "HistoryState",
    "OperationStep",
    "PlanHistory",
    "plan_history",
    "walk",
]
