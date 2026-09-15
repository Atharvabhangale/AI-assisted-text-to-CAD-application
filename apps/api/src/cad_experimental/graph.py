"""The feature graph: nodes, typed edges, and a deterministic order.

The operation plan has always *been* a graph. What it lacked was a place
where that was written down. References were re-derived in three modules --
the validator's category checks, the history walk's edge list, the adapter's
traversal -- each re-deciding for itself which key on which operation type
points at what. That is how a fourth operation type comes to disagree with
the other three.

This module is that place. It holds structure only:

* a **node** per operation, carrying its identity, its index, its type and
  what it produces;
* an **edge** per reference, carrying the id it names and the **role** it
  names it in -- a target, a tool, or a pattern's source. The role is the
  part that was implicit before, and it is what lets one rule decide whether
  a reference is admissible instead of one branch per operation type;
* a deterministic **topological order**, derived from the edges rather than
  assumed from the list;
* the queries an agent needs to diagnose a broken plan: what depends on
  this, what does this depend on, where is the cycle.

What is deliberately NOT here
-----------------------------
**No state, no geometry, no verdict.** The solid set -- what is live, what
was consumed, which body owns which feature -- is
:mod:`cad_experimental.history`, because that is a fold over this graph and
not a property of it. Validity is
:mod:`cad_experimental.validation`. Geometry is the engine's, two layers
down.

The split matters: structure is decidable from the plan text alone, so this
module can describe a plan that is nonsense and let the validator say so.
Every function here is total -- no exceptions, no partial results -- because
a graph that refused to describe a broken plan would be useless for exactly
the plans a diagnostic is wanted for.

Backend independence
--------------------
Nothing here imports a kernel, ``cad_core``, or any backend. A graph is the
same graph whether it is eventually built by CadQuery, by FreeCAD or by
nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from .plan import (
    CONSUMING_TYPES,
    MODIFIER_TYPES,
    PATTERN,
    PATTERNABLE_TYPES,
    PROFILE_SOLID_TYPES,
    PROFILE_TYPES,
    SOLID_DECLARING_TYPES,
    TARGETED_TYPES,
    OperationPlan,
    tools_of,
)

# --- what a reference is FOR ------------------------------------------------

#: The solid or profile an operation acts on. One per operation that has one.
TARGET = "target"

#: A solid a subtract removes and consumes. Ordered: Section C.4 removes them
#: in list order, so the edges are a sequence and not a set.
TOOL = "tool"

#: The feature a pattern repeats. Unlike the other two this names an
#: **operation**, not a body -- which is precisely why the role has to be
#: part of the edge rather than inferred from the key's name.
SOURCE = "source"

ROLES: Tuple[str, ...] = (TARGET, TOOL, SOURCE)


# --- what a node PRODUCES ---------------------------------------------------

#: The node adds a solid to the model, named by its own id.
SOLID = "solid"

#: The node adds a profile. A profile is not a solid and never becomes one:
#: sweeping it makes a new solid and leaves the profile alone.
PROFILE = "profile"

#: The node changes an existing solid in place. The result keeps the
#: TARGET's id, so the node's own id names nothing afterwards. This is the
#: single fact that makes a modifier-targeting-a-modifier plan invalid.
NOTHING = "nothing"

CATEGORIES: Tuple[str, ...] = (SOLID, PROFILE, NOTHING)


def produces(kind: Optional[str]) -> str:
    """What an operation of this type leaves behind.

    Read from :data:`~cad_experimental.plan.SOLID_DECLARING_TYPES` and
    :data:`~cad_experimental.plan.PROFILE_TYPES` -- the same tables the
    parser and the validator read -- so the graph cannot come to disagree
    with them about what a type is.
    """
    if kind in SOLID_DECLARING_TYPES:
        return SOLID
    if kind in PROFILE_TYPES:
        return PROFILE
    return NOTHING


# --- what a reference must POINT AT -----------------------------------------

#: A reference must name a node whose own id currently denotes a solid.
EXPECT_SOLID = "solid"

#: A reference must name a sketch.
EXPECT_PROFILE = "profile"

#: A reference must name a **feature** that can be repeated -- an operation,
#: not a body. The third expectation exists because a pattern is the first
#: operation in this language whose input is another operation.
EXPECT_FEATURE = "feature"

EXPECTATIONS: Tuple[str, ...] = (EXPECT_SOLID, EXPECT_PROFILE, EXPECT_FEATURE)


def expectation(kind: Optional[str], role: str) -> Optional[str]:
    """What a ``role`` reference on a ``kind`` operation must point at.

    One table in place of a branch per operation type. Adding an operation
    means answering this question once, here, rather than editing the
    validator, the history walk and the adapter and hoping they agree.

    ``None`` means the combination does not occur -- a box has no target, a
    fillet has no tools.
    """
    if role == TARGET:
        if kind in PROFILE_SOLID_TYPES:
            # The only reference in the language that names a sketch.
            return EXPECT_PROFILE
        if kind in TARGETED_TYPES:
            return EXPECT_SOLID
        return None
    if role == TOOL:
        return EXPECT_SOLID if kind in CONSUMING_TYPES else None
    if role == SOURCE:
        return EXPECT_FEATURE if kind == PATTERN else None
    return None


#: Which node types may stand at the far end of an ``EXPECT_FEATURE`` edge.
#: A table rather than a predicate so that widening it later -- to repeat a
#: constructive primitive, say -- is a one-line change with one place to
#: audit.
REPEATABLE: FrozenSet[str] = frozenset(PATTERNABLE_TYPES)


# --- the graph --------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """One edge: the id a node names, and what it names it as."""

    id: str
    role: str

    #: Position within its role. A subtract's tools are ordered, so an error
    #: has to be able to say *which* tool.
    position: int = 0

    def path(self) -> str:
        """The JSON path fragment this edge came from."""
        if self.role == TOOL:
            return f"tools[{self.position}]"
        return self.role

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "role": self.role, "position": self.position}


@dataclass(frozen=True)
class FeatureNode:
    """One operation, as a node."""

    index: int
    id: str
    type: Optional[str]
    inputs: Tuple[Reference, ...] = ()

    #: :data:`SOLID`, :data:`PROFILE` or :data:`NOTHING`.
    produces: str = NOTHING

    #: Operations this one must follow although it does not name them:
    #: whatever last changed the same body. **Derived, not declared.**
    #:
    #: A modifier depends on its target's STATE, not merely on its identity,
    #: and the declared edges cannot say so -- a chamfer and a hole both
    #: name the plate, and "chamfer the plate" means chamfer it *as it is
    #: now*. Without this edge a topological sort may legitimately float the
    #: chamfer ahead of the holes, and chamfering before drilling is a
    #: different part.
    #:
    #: Only the immediate predecessor is recorded; the rest follows by
    #: transitivity. This is the per-body feature history that every CAD
    #: feature tree has, made explicit.
    after: Tuple[str, ...] = ()

    #: The body this node changes in place, if any.
    body: Optional[str] = None

    def dependencies(self) -> Tuple[str, ...]:
        """The ids this node **names**, in edge order, without duplicates.

        Declared references only. For everything that orders this node, see
        :meth:`predecessors`.
        """
        return tuple(dict.fromkeys(reference.id for reference in self.inputs))

    def predecessors(self) -> Tuple[str, ...]:
        """Everything this node must follow: what it names, and what it
        follows on its body."""
        return tuple(dict.fromkeys(self.dependencies() + self.after))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "id": self.id,
            "type": self.type,
            "produces": self.produces,
            "body": self.body,
            "inputs": [reference.to_dict() for reference in self.inputs],
            "after": list(self.after),
        }


@dataclass(frozen=True)
class FeatureGraph:
    """The plan's nodes and edges, and the questions worth asking of them."""

    nodes: Tuple[FeatureNode, ...] = ()

    # -- lookup ------------------------------------------------------------

    def node(self, identifier: str) -> Optional[FeatureNode]:
        """The node with this id, or ``None``.

        The **first**, if a malformed plan repeats an id -- the same one P2
        reports as the original, so every layer blames the same node.
        """
        for node in self.nodes:
            if node.id == identifier:
                return node
        return None

    def indices(self) -> Mapping[str, int]:
        """Each id mapped to the index where it first appears."""
        found: Dict[str, int] = {}
        for node in self.nodes:
            found.setdefault(node.id, node.index)
        return found

    # -- edges -------------------------------------------------------------

    def dependencies(self, identifier: str) -> Tuple[str, ...]:
        """What this node **names** directly. Declared edges only."""
        node = self.node(identifier)
        return node.dependencies() if node is not None else ()

    def dependents(self, identifier: str) -> Tuple[str, ...]:
        """What names this node directly, in plan order. Declared edges only."""
        return tuple(
            node.id for node in self.nodes
            if identifier in node.dependencies()
        )

    def predecessors(self, identifier: str) -> Tuple[str, ...]:
        """Everything this node must follow -- declared and derived."""
        node = self.node(identifier)
        return node.predecessors() if node is not None else ()

    def successors(self, identifier: str) -> Tuple[str, ...]:
        """Everything that must follow this node, in plan order."""
        return tuple(
            node.id for node in self.nodes
            if identifier in node.predecessors()
        )

    def ancestors(self, identifier: str) -> Tuple[str, ...]:
        """Everything this node rests on, transitively, in plan order.

        Over :meth:`predecessors`, not :meth:`dependencies`: "what does this
        feature depend on" means everything whose effect it inherits, and a
        chamfer inherits every hole drilled before it.
        """
        return self._reach(identifier, self.predecessors)

    def descendants(self, identifier: str) -> Tuple[str, ...]:
        """Everything affected by this node, transitively, in plan order."""
        return self._reach(identifier, self.successors)

    def _reach(self, identifier: str, step: Any) -> Tuple[str, ...]:
        seen: set = set()
        frontier = [identifier]
        while frontier:
            for name in step(frontier.pop()):
                if name not in seen:
                    seen.add(name)
                    frontier.append(name)
        seen.discard(identifier)
        return tuple(node.id for node in self.nodes if node.id in seen)

    # -- structure ---------------------------------------------------------

    def unknown_references(self) -> Tuple[Tuple[str, Reference], ...]:
        """Edges naming an id no node declares: ``(node id, edge)`` pairs."""
        known = set(self.indices())
        return tuple(
            (node.id, reference)
            for node in self.nodes
            for reference in node.inputs
            if reference.id not in known
        )

    def forward_references(self) -> Tuple[Tuple[str, Reference], ...]:
        """Edges naming a node that does not appear strictly earlier.

        A self-reference counts: it names a node that is not *earlier*, and
        it is the shortest possible cycle -- :meth:`cycles` reports it as a
        group of one.
        """
        first = self.indices()
        return tuple(
            (node.id, reference)
            for node in self.nodes
            for reference in node.inputs
            if reference.id in first and first[reference.id] >= node.index
        )

    def topological_order(self) -> Tuple[str, ...]:
        """A deterministic execution order, derived rather than assumed.

        Kahn's algorithm, with ties broken by plan index, so one plan always
        gives one order -- there is no "a valid topological order", there is
        *the* order this plan executes in.

        Because rule P10 requires every reference to appear strictly earlier,
        a valid plan's list order is already a topological order and this
        returns it unchanged. That is the point rather than a coincidence:
        the order is now **checked** against the edges instead of trusted,
        and :meth:`is_list_order` says whether the two agree.

        Nodes in a cycle cannot be ordered and are omitted; the returned
        tuple is shorter than :attr:`nodes` exactly when :meth:`cycles` is
        non-empty. Unknown references are ignored -- an edge to nothing
        constrains nothing, and P9 reports it.
        """
        known = set(self.indices())
        first = self.indices()
        remaining: Dict[str, int] = {}
        for node in self.nodes:
            if node.id in remaining:
                # A duplicate id is P2's to report. Here the first node
                # wins, so the graph stays single-valued.
                continue
            # A node that names itself counts itself as a predecessor and so
            # can never be released: a self-loop is the shortest cycle and
            # `cycles` must be able to say so. Rule P10 reports it with
            # better advice, and P31 states the structural fact alongside.
            remaining[node.id] = len(
                {name for name in node.predecessors() if name in known}
            )

        order: List[str] = []
        while True:
            ready = sorted(
                (name for name, count in remaining.items() if count == 0),
                key=lambda name: first[name],
            )
            if not ready:
                break
            for name in ready:
                order.append(name)
                del remaining[name]
                for dependent in self.successors(name):
                    if dependent in remaining:
                        remaining[dependent] -= 1
        return tuple(order)

    def is_list_order(self) -> bool:
        """Whether executing the list in order *is* the topological order."""
        ordered = self.topological_order()
        listed = tuple(dict.fromkeys(node.id for node in self.nodes))
        return ordered == listed

    def cycles(self) -> Tuple[Tuple[str, ...], ...]:
        """The cyclic groups, each in plan order, the whole thing sorted.

        Every node that could not be ordered is in one: a node is unorderable
        exactly when it is in a cycle or downstream of one. Reported as
        groups rather than as a single flat set so a caller can tell two
        independent cycles apart.

        Rule P10 makes a cycle unreachable through the parser -- a cycle
        needs a reference that is not strictly earlier, which is P10 itself.
        This exists because a plan can also be built in code, because the
        graph must not depend on the validator having run, and because P10
        is a rule that a later stage may want to relax.
        """
        unordered = set(self.indices()) - set(self.topological_order())
        if not unordered:
            return ()

        groups: List[Tuple[str, ...]] = []
        assigned: set = set()
        for node in self.nodes:
            if node.id not in unordered or node.id in assigned:
                continue
            # Everything mutually reachable with this node.
            forward = set(self.descendants(node.id))
            backward = set(self.ancestors(node.id))
            group = (forward & backward) | {node.id}
            group &= unordered
            assigned |= group
            groups.append(
                tuple(n.id for n in self.nodes if n.id in group)
            )
        return tuple(groups)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "order": list(self.topological_order()),
            "list_order": self.is_list_order(),
            "cycles": [list(group) for group in self.cycles()],
        }


def modified_body(
    operation: object, kind: Optional[str], by_id: Mapping[str, object]
) -> Optional[str]:
    """The body this operation changes in place, if any.

    A modifier names it directly: its ``target`` IS the body, and the result
    keeps that id. A **pattern** does not -- its reference is to the feature
    it repeats, so the body is one hop further on, whatever that feature
    modifies. Everything else changes no body: a constructive operation
    creates one, a sketch creates a profile.

    Structural, so it lives here rather than in the state walk: which body an
    operation touches is decidable from the plan's references alone.
    """
    if kind in MODIFIER_TYPES:
        target = getattr(operation, "target", None)
        return target if isinstance(target, str) else None
    if kind == PATTERN:
        source = by_id.get(getattr(operation, "source", None))
        if source is None:
            return None
        target = getattr(source, "target", None)
        return target if isinstance(target, str) else None
    return None


def references_of(operation: object, kind: Optional[str]) -> Tuple[Reference, ...]:
    """Every edge one operation contributes, in a stable order.

    Target first, then tools in list order, then a source. Derived from
    :func:`expectation`, so an operation type with no entry in that table
    contributes no edges and a type that gains one gains them automatically.
    """
    edges: List[Reference] = []
    if expectation(kind, TARGET) is not None:
        target = getattr(operation, "target", None)
        if isinstance(target, str) and target:
            edges.append(Reference(target, TARGET))
    if expectation(kind, TOOL) is not None:
        for position, tool in enumerate(tools_of(operation)):
            edges.append(Reference(tool, TOOL, position))
    if expectation(kind, SOURCE) is not None:
        source = getattr(operation, "source", None)
        if isinstance(source, str) and source:
            edges.append(Reference(source, SOURCE))
    return tuple(edges)


def feature_graph(plan: OperationPlan) -> FeatureGraph:
    """The graph of one plan. Total: never raises, never judges.

    Builds both edge sets in one pass: the references the plan declares, and
    the per-body sequencing each modifier inherits. The second is what makes
    the derived order agree with the order the plan means -- see
    :attr:`FeatureNode.after`.
    """
    by_id: Dict[str, object] = {}
    for operation in plan.operations:
        by_id.setdefault(operation.id, operation)

    nodes: List[FeatureNode] = []
    #: The last operation to have touched each body, as the walk stands.
    latest: Dict[str, str] = {}

    for index, operation in enumerate(plan.operations):
        kind = getattr(operation, "TYPE", None)
        body = modified_body(operation, kind, by_id)
        after: Tuple[str, ...] = ()
        if body is not None and body in latest:
            after = (latest[body],)
        nodes.append(
            FeatureNode(
                index=index,
                id=operation.id,
                type=kind,
                inputs=references_of(operation, kind),
                produces=produces(kind),
                after=after,
                body=body,
            )
        )
        if produces(kind) is SOLID:
            latest.setdefault(operation.id, operation.id)
            latest[operation.id] = operation.id
        if body is not None:
            latest[body] = operation.id

    return FeatureGraph(nodes=tuple(nodes))


__all__ = [
    "CATEGORIES",
    "EXPECTATIONS",
    "EXPECT_FEATURE",
    "EXPECT_PROFILE",
    "EXPECT_SOLID",
    "FeatureGraph",
    "FeatureNode",
    "NOTHING",
    "PROFILE",
    "REPEATABLE",
    "ROLES",
    "Reference",
    "SOLID",
    "SOURCE",
    "TARGET",
    "TOOL",
    "expectation",
    "feature_graph",
    "modified_body",
    "produces",
    "references_of",
]
