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

    def subtract(self, target: Any, tools: Sequence[Any]) -> Any:
        """Section C.4. The target survives; the tools are consumed."""
        raise NotImplementedError

    def fillet(self, target: Any, radius: float, selector: Selector) -> Any:
        """Section C.5. Every selected edge must be blended, or none is."""
        raise NotImplementedError

    def chamfer(self, target: Any, distance: float, selector: Selector) -> Any:
        """Section C.6. An equal setback on both adjoining faces."""
        raise NotImplementedError

    # --- reading out -----------------------------------------------------

    def measure(self, shape: Any) -> Measurement:
        raise NotImplementedError

    def select_edges(self, shape: Any, selector: Selector) -> Tuple[Any, ...]:
        """The edges a selector names, in the backend's own stable order."""
        raise NotImplementedError

    def export_step(self, shape: Any, path: Any) -> Any:
        raise NotImplementedError

    def read_step(self, path: Any) -> Any:
        """Read a STEP file back, so an export can be verified and not assumed."""
        raise NotImplementedError

    def render_model(self, shape: Any, *, part_name: str, feature_id: str) -> Any:
        """Build the **existing** :class:`cad_core.render_model.RenderModel`.

        Both backends return that one type. The frontend must never be able to
        tell which engine produced a mesh, and a second render format would
        make that impossible.
        """
        raise NotImplementedError


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
    "resolve_backend",
]
