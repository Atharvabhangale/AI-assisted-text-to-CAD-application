"""Stage 42: a CAD backend boundary, so two engines can be compared.

Until now there was one engine: CadQuery over OpenCascade, reached through
``cad_core.local_cad``. This module introduces the smallest interface that
lets a second engine sit beside it, so the project can eventually answer
*which engine is better for the mechanical work we actually want to do* with
evidence rather than preference.

What this is not
----------------
It is **not** a replacement for ``cad_core``. The CadQuery backend delegates
to the existing engine's own functions -- its selector, its RenderModel, its
STEP exporter -- rather than reimplementing them, so the baseline stays
exactly what it has always been.

It is **not** a general plugin system. There are two named implementations,
chosen by configuration; there is no discovery, no entry point and no dynamic
import.

It is **not** a fallback chain. If the configured backend cannot run, that is
an error. A FreeCAD failure must never quietly become a CadQuery result --
that would make every comparison meaningless and could ship geometry from an
engine nobody selected.

Scope
-----
Six operations, matching exactly the six the V1 contract can build: box,
cylinder, through_hole, subtract, fillet and chamfer. Sketch, extrude and
revolve are deliberately absent -- no engine here executes them, and adding
interface methods for features that do not exist yet is how an abstraction
becomes a liability.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

#: The environment variable that selects a backend, following the project's
#: existing ``CAD_``/``CAD_EXPERIMENTAL_`` convention.
BACKEND_VARIABLE = "CAD_BACKEND"

#: Where an extracted FreeCAD distribution lives, when one is not on the
#: default library path. Read only as a filesystem location; never executed.
FREECAD_HOME_VARIABLE = "CAD_FREECAD_HOME"

CADQUERY = "cadquery"
FREECAD = "freecad"

#: Every backend this application implements, in preference order.
BACKEND_NAMES: Tuple[str, ...] = (CADQUERY, FREECAD)

#: **The default, and it stays the default.** FreeCAD is experimental in this
#: stage and must never be selected implicitly.
DEFAULT_BACKEND = CADQUERY

#: The operations a backend must implement -- the six V1 features, and no
#: more. Named here so a test can assert both backends cover the same set.
SUPPORTED_OPERATIONS: Tuple[str, ...] = (
    "box", "cylinder", "through_hole", "subtract", "fillet", "chamfer",
)


class BackendError(Exception):
    """Base class for every backend failure."""


class BackendUnavailable(BackendError):
    """The selected backend cannot run here.

    Raised instead of substituting another engine. A caller that wanted
    FreeCAD and silently received CadQuery geometry would have no way to know
    which engine produced the part it is about to manufacture.
    """


class BackendOperationError(BackendError):
    """A geometric operation failed on otherwise supported input.

    The counterpart of :class:`cad_core.local_cad.GeometryOperationError`, and
    deliberately a separate class: the two backends are independent execution
    paths, and neither imports the other's errors.
    """


class UnsupportedSelector(BackendError):
    """An edge selector this backend cannot map safely.

    Raised rather than selecting *something*. The contract has two selectors
    and no nearest-edge rule; guessing at a third would silently change which
    edges a part was filleted on.
    """


@dataclass(frozen=True)
class Measurement:
    """What a backend reports about a shape, in plain numbers.

    Every field is asked of the kernel. Nothing here is inferred from a shape
    merely existing, and nothing is a backend-specific object -- which is what
    makes two backends comparable at all.
    """

    is_valid: bool
    solid_count: int
    volume: float
    face_count: int
    edge_count: int
    minimum: Tuple[float, float, float]
    maximum: Tuple[float, float, float]

    @property
    def size(self) -> Tuple[float, float, float]:
        return (
            self.maximum[0] - self.minimum[0],
            self.maximum[1] - self.minimum[1],
            self.maximum[2] - self.minimum[2],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "solid_count": self.solid_count,
            "volume": self.volume,
            "face_count": self.face_count,
            "edge_count": self.edge_count,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
            "size": list(self.size),
        }


@dataclass(frozen=True)
class Selector:
    """The V1 edge selector, as plain data.

    Section C.7 exactly: ``select`` is ``"all"`` or ``"axis_parallel"``, and
    ``axis`` is an **unsigned** letter present exactly when the mode is
    ``axis_parallel``. This is the same selector both backends receive; each
    translates it to its own kernel, and neither may reinterpret it.
    """

    select: str
    axis: Optional[str] = None

    def validate(self) -> None:
        if self.select == "all":
            if self.axis is not None:
                raise UnsupportedSelector(
                    "an `all` selector carries no axis (rule S18)"
                )
            return
        if self.select != "axis_parallel":
            raise UnsupportedSelector(
                f"unknown selector mode {self.select!r}; the contract defines "
                "only 'all' and 'axis_parallel'"
            )
        if self.axis not in ("X", "Y", "Z"):
            raise UnsupportedSelector(
                f"an `axis_parallel` selector needs an unsigned axis of "
                f"X, Y or Z; got {self.axis!r}"
            )


class CadBackend:
    """The interface both engines implement.

    A backend owns its own shape objects; callers pass them back in and never
    inspect them directly. Everything a caller needs to know about a shape
    comes through :meth:`measure`, :meth:`render_model` and
    :meth:`export_step`, all of which speak in neutral types.
    """

    #: The backend's name, one of :data:`BACKEND_NAMES`.
    name: str = ""

    def available(self) -> bool:
        raise NotImplementedError

    def version(self) -> str:
        raise NotImplementedError

    # --- construction ----------------------------------------------------

    def create_box(
        self, size: Tuple[float, float, float],
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> Any:
        """Section C.1: ``position`` is the **minimum corner**."""
        raise NotImplementedError

    def create_cylinder(
        self, diameter: float, height: float,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        axis: str = "+Z",
    ) -> Any:
        """Section C.2: ``position`` is the **base circle centre**."""
        raise NotImplementedError

    # --- modification ----------------------------------------------------

    def through_hole(
        self, target: Any, diameter: float,
        position: Tuple[float, float, float], axis: str = "+Z",
    ) -> Any:
        """Section C.3. The centreline is infinite, so the sign is dropped."""
        raise NotImplementedError

    def union(self, target: Any, tools: Sequence[Any]) -> Any:
        """Fuse every solid in ``tools`` into ``target``.

        The counterpart of :meth:`subtract`, held to the same standard: the
        result must be ONE connected solid. Fusing solids that do not touch
        leaves a disconnected body, and rule E3 refuses that here rather than
        letting a scattering of fragments reach a caller as a part.
        """
        raise NotImplementedError

    def subtract(self, target: Any, tools: Sequence[Any]) -> Any:
        """Section C.4. The target survives; the tools are consumed."""
        raise NotImplementedError

    def fillet(self, target: Any, radius: float, selector: Selector) -> Any:
        """Section C.5. Every selected edge must be blended, or none is."""
        raise NotImplementedError

    def chamfer(self, target: Any, distance: float, selector: Selector) -> Any:
        """Section C.6. An equal setback on both adjoining faces."""
        raise NotImplementedError

    # --- semantic edge selection (Stage 47) ------------------------------
    #
    # The division of labour, and the reason the graph can stay
    # backend-neutral: a backend DISCOVERS facts about edges and ACTS on
    # edges it is handed back. It never decides which edges a selector
    # means -- `cad_experimental.edge_semantics` does that, on plain numbers,
    # with no kernel anywhere near it.

    def describe_edges(self, shape: Any) -> Tuple[Any, ...]:
        """Every edge of ``shape`` as :class:`~edge_semantics.EdgeFacts`.

        Plain numbers and strings: curve type, direction or centre and
        normal, radius, length, whether the edge is a **parameterisation
        seam**, and the neutral names of the surfaces meeting there. No
        kernel object crosses this boundary.

        ``EdgeFacts.index`` is this backend's own handle for the edge and is
        opaque to everything above: the resolver orders by geometry and uses
        the index only to settle edges that are geometrically identical.
        :meth:`edges_at` turns handles back into whatever the backend
        actually operates on.
        """
        raise NotImplementedError

    def edges_at(self, shape: Any, indices: Sequence[int]) -> Tuple[Any, ...]:
        """The backend's own edge objects for these handles, in this order."""
        raise NotImplementedError

    def fillet_edges(
        self, target: Any, radius: float, edges: Sequence[Any]
    ) -> Any:
        """Blend exactly these edges. All of them, or none.

        Separate from :meth:`fillet` because the selection has already been
        made: this takes edges, not a selector, so the same kernel call
        serves a Section C.7 selector and a semantic one without the backend
        learning a second selector vocabulary.
        """
        raise NotImplementedError

    def chamfer_edges(
        self, target: Any, distance: float, edges: Sequence[Any]
    ) -> Any:
        """Bevel exactly these edges. All of them, or none."""
        raise NotImplementedError

    # --- reading out -----------------------------------------------------

    def measure(self, shape: Any) -> Measurement:
        raise NotImplementedError

    def select_edges(self, shape: Any, selector: Selector) -> Tuple[Any, ...]:
        """The edges a selector names, in the backend's own stable order."""
        raise NotImplementedError

    def export_step(self, shape: Any, path: Any) -> Any:
        raise NotImplementedError

    def export_stl(self, shape: Any, path: Any) -> Any:
        """Write a binary/ASCII STL of ``shape``, using the engine's own writer.

        At the abstraction boundary rather than in a caller, for the same
        reason every other output is: a mesh derived from the RenderModel in
        the frontend would be a second tessellation of the same solid, and
        the two would drift. Each engine already has a tessellator and a
        writer; this asks for them.

        Like :meth:`export_step`, an implementation is expected to VERIFY the
        file it wrote rather than trust that writing succeeded.
        """
        raise NotImplementedError

    def export_step_assembly(
        self, bodies: Sequence[Tuple[str, Any]], path: Any
    ) -> Any:
        """Write ONE STEP file holding every body, each under its own id.

        ``bodies`` is an ordered sequence of ``(body_id, shape)``. The order
        is the caller's -- declaration order -- and is preserved, so two runs
        of the same plan write the same file structure rather than whatever
        order a dictionary happened to iterate in.

        **This is not :meth:`export_step` in a loop, and it is not a fuse.**
        A multi-body part written as a single fused solid would assert a join
        the plan never asked for; written as only its first body it would
        silently drop the rest. Both are lies about the geometry, and STEP
        represents several solids natively, so neither is necessary.

        An implementation must VERIFY what it wrote by reading the file back
        and counting solids, exactly as :meth:`export_step` is expected to --
        and here the standard matters more, because a writer given a list can
        fail by writing a *well-formed file with nothing in it*. FreeCAD's
        ``Part.export`` does precisely that: handed raw shapes it leaves a
        1.6 kB STEP that reads back as **zero** solids, and a caller checking
        only that the file exists would report a successful export of an
        empty file.

        A backend that cannot preserve these semantics must raise rather than
        write something approximate.
        """
        raise NotImplementedError

    def read_step_solids(self, path: Any) -> Tuple[Any, ...]:
        """Every solid in a STEP file, separately, in the file's own order.

        :meth:`read_step` answers "what shape is in this file", which is the
        right question for a single-solid export and the wrong one for an
        assembly: it returns one shape, and a caller cannot tell a compound
        of two solids from one solid by looking at it. This answers "how many
        bodies came back, and what does each measure", which is the only way
        to check that an assembly export did not drop or fuse anything.
        """
        raise NotImplementedError

    def read_step(self, path: Any) -> Any:
        """Read a STEP file back, so an export can be verified and not assumed."""
        raise NotImplementedError

    def project_edges(
        self, shape: Any, direction: Sequence[float]
    ) -> Tuple[Tuple[Tuple[float, float], ...], ...]:
        """The shape's visible outline seen along ``direction``, as polylines.

        Plain 2D points and nothing else -- no kernel object, no curve type,
        no engine enumeration. A drawing is then a matter of arranging
        polylines on a sheet, which needs no CAD knowledge and cannot drift
        from the geometry it came from.

        Curves are discretised rather than described: a polyline is the one
        form every engine can produce and every renderer can draw, and a
        drawing view is a picture, not a model.
        """
        raise NotImplementedError

    def render_model(self, shape: Any, *, part_name: str, feature_id: str) -> Any:
        """Build the **existing** :class:`cad_core.render_model.RenderModel`.

        Both backends return that one type. The frontend must never be able to
        tell which engine produced a mesh, and a second render format would
        make that impossible.
        """
        raise NotImplementedError


# --- assembly export helpers, shared by both backends -----------------------
#
# Here rather than in each backend because they are not engine knowledge:
# "the ids must be unique" and "the file must read back as the bodies you put
# in" are properties of the EXPORT, and two copies could disagree about them.
# This module imports no kernel, so the FreeCAD backend can use them without
# reaching through the CadQuery one -- which on a machine with only FreeCAD
# installed would not import at all.
#
# The verification is deliberately the SAME standard on both engines, so a
# STEP written by one and a STEP written by the other are held to it equally.


def ordered_bodies(bodies) -> Tuple[Tuple[str, Any], ...]:
    """Validate the (body_id, shape) pairs and keep the caller's order."""
    ordered = tuple((str(name), shape) for name, shape in bodies)
    if not ordered:
        raise BackendOperationError(
            "a STEP assembly needs at least one body; nothing was given"
        )
    names = [name for name, _ in ordered]
    if len(set(names)) != len(names):
        duplicated = sorted({n for n in names if names.count(n) > 1})
        raise BackendOperationError(
            "two bodies cannot share an id in one STEP assembly "
            f"({', '.join(repr(n) for n in duplicated)}); a body's id is its "
            "identity and the file would name two different solids the same"
        )
    for name, shape in ordered:
        if shape is None:
            raise BackendOperationError(
                f"body {name!r} has no shape to export"
            )
    return ordered


def verify_assembly(backend, destination, ordered) -> None:
    """Read the written file back and prove no body was dropped or fused.

    The count is the whole point. Writing N bodies and reading back 1 means
    they were fused; reading back 0 means the writer produced a well-formed
    file with nothing in it -- which is not hypothetical, it is exactly what
    FreeCAD's ``Part.export`` does when handed raw shapes. Neither failure
    makes the file unreadable, so neither is visible without counting.
    """
    try:
        written = backend.read_step_solids(destination)
    except BackendOperationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BackendOperationError(
            f"the STEP assembly could not be read back to verify it: {exc}"
        ) from exc
    if len(written) != len(ordered):
        raise BackendOperationError(
            f"the STEP assembly was written with {len(ordered)} bodies "
            f"({', '.join(repr(n) for n, _ in ordered)}) but reads back as "
            f"{len(written)} solid{'' if len(written) == 1 else 's'}; the "
            "export would have dropped or fused a body, so it is refused "
            "rather than delivered"
        )

    # AND the ids must have survived. The count above cannot see this: handed
    # a compound, BOTH engines write a geometrically perfect two-solid STEP in
    # which the bodies are called `Open CASCADE STEP translator 7.9 1.1` and
    # `1.2`. Every count, every volume and every face total matches, and the
    # identity this whole slice is about is gone. So the names are checked
    # too, and an export that lost them is refused rather than delivered as an
    # assembly whose bodies cannot be told apart.
    #
    # STEP is a text format and the ids are written into it literally, so this
    # is read as text. What it proves is exactly that each id REACHED the
    # file -- not which solid carries it, which would need a per-engine
    # assembly reader. That is the honest limit of this check, and it is
    # enough to catch the failure it exists for.
    try:
        written_text = Path(destination).read_text(errors="ignore")
    except OSError as exc:
        raise BackendOperationError(
            f"the STEP assembly could not be re-read to check its body "
            f"names: {exc}"
        ) from exc
    missing = [name for name, _ in ordered if name not in written_text]
    if missing:
        raise BackendOperationError(
            "the STEP assembly was written but "
            f"{', '.join(repr(n) for n in missing)} did not reach the file "
            "under that name; an assembly whose bodies cannot be told apart "
            "is not the export that was asked for"
        )


# --- axes, shared by both backends -----------------------------------------

#: The six signed principal directions of Section A.4.
AXIS_DIRECTIONS: Dict[str, Tuple[float, float, float]] = {
    "+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0),
}


def axis_direction(axis: str) -> Tuple[float, float, float]:
    if axis not in AXIS_DIRECTIONS:
        permitted = ", ".join(repr(name) for name in AXIS_DIRECTIONS)
        raise BackendOperationError(
            f"unsupported axis {axis!r}; the specification permits only "
            f"{permitted}"
        )
    return AXIS_DIRECTIONS[axis]


def axis_index(axis: str) -> int:
    """The 0/1/2 index of a signed or unsigned axis. The sign is dropped."""
    letter = axis[-1].upper()
    if letter not in "XYZ":
        raise BackendOperationError(f"unsupported axis {axis!r}")
    return "XYZ".index(letter)


# --- selection -------------------------------------------------------------


def resolve_backend(name: Optional[str] = None) -> CadBackend:
    """Return the named backend, or raise. **Never falls back.**

    Args:
        name: A backend name, or ``None`` to read :data:`BACKEND_VARIABLE`
            from the environment, defaulting to :data:`DEFAULT_BACKEND`.

    Raises:
        BackendUnavailable: if the name is unknown, or if the backend it
            names cannot run here. A caller that asked for FreeCAD and got
            CadQuery would have no way to know which engine built its part,
            so this is an error and not a degradation.
    """
    chosen = (
        name if name is not None
        else os.environ.get(BACKEND_VARIABLE, "").strip() or DEFAULT_BACKEND
    )
    if chosen not in BACKEND_NAMES:
        raise BackendUnavailable(
            f"unknown CAD backend {chosen!r}; this application implements "
            f"{', '.join(BACKEND_NAMES)}"
        )

    if chosen == CADQUERY:
        from .cadquery_backend import CadQueryBackend

        backend: CadBackend = CadQueryBackend()
    else:
        from .freecad_backend import FreeCadBackend

        backend = FreeCadBackend()

    if not backend.available():
        raise BackendUnavailable(
            f"the {chosen!r} backend is not available in this environment. "
            "Nothing was substituted: a part must come from the engine that "
            "was asked for."
        )
    return backend


def backend_report() -> Dict[str, Any]:
    """What each backend reports about itself. Calls no geometry."""
    report: Dict[str, Any] = {
        "default": DEFAULT_BACKEND,
        "selected": os.environ.get(BACKEND_VARIABLE, "").strip() or DEFAULT_BACKEND,
        "backends": {},
    }
    for name in BACKEND_NAMES:
        try:
            if name == CADQUERY:
                from .cadquery_backend import CadQueryBackend

                backend: CadBackend = CadQueryBackend()
            else:
                from .freecad_backend import FreeCadBackend

                backend = FreeCadBackend()
            available = backend.available()
            report["backends"][name] = {
                "available": available,
                "version": backend.version() if available else None,
            }
        except Exception as exc:  # a backend that cannot even be constructed
            report["backends"][name] = {
                "available": False,
                "version": None,
                "detail": f"{type(exc).__name__}: {exc}",
            }
    return report


__all__ = [
    "AXIS_DIRECTIONS",
    "BACKEND_NAMES",
    "BACKEND_VARIABLE",
    "CADQUERY",
    "DEFAULT_BACKEND",
    "FREECAD",
    "FREECAD_HOME_VARIABLE",
    "SUPPORTED_OPERATIONS",
    "BackendError",
    "BackendOperationError",
    "BackendUnavailable",
    "CadBackend",
    "Measurement",
    "Selector",
    "UnsupportedSelector",
    "axis_direction",
    "axis_index",
    "backend_report",
    "ordered_bodies",
    "resolve_backend",
    "verify_assembly",
]
