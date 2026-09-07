"""Local deterministic CAD engine: builds real B-rep geometry from a V1 part.

This is an **execution backend**, not a replacement for the specification. The
neutral CAD specification stays the source of truth, and two backends consume
it independently:

```
CAD specification -> local CAD engine (this module)      -> B-rep solid
CAD specification -> FeatureScript generator             -> Onshape
```

Nothing in the specification knows this module exists, and nothing here feeds
back into it: no CadQuery concept appears in :mod:`cad_core.model`.

Backend
-------
CadQuery (over OpenCascade, via ``cadquery-ocp``). The geometry produced is a
genuine B-rep solid from the kernel -- not a mesh, not triangles, not a
bounding-box stand-in.

Supported subset for this stage
-------------------------------
Exactly one feature, of type ``box``, in millimetres. Anything else raises
:class:`UnsupportedGeometryError`. Cylinders, through-holes, boolean
subtraction, fillets and chamfers are **not implemented**: they are rejected,
never partially built and never silently skipped.

Coordinate semantics (specification Section C.1)
------------------------------------------------
``position`` is the box's **minimum corner** and the box occupies
``[position, position + size]`` on each axis, axis-aligned with no rotation.
``cadquery.Solid.makeBox`` is used with ``pnt=position``, which places the
minimum corner directly -- no centred-box default is involved, so there is no
centring transform to get wrong.

Units
-----
The kernel is unitless; every number handed to it is in the part's declared
unit, which V1 fixes as millimetres. A part declaring anything else is
rejected rather than converted.

Availability
------------
CadQuery is an **optional** dependency (extra ``local-cad``), so importing
:mod:`cad_core` does not require it: the specification, the validator and the
FeatureScript generator stay dependency-free. This module is therefore not
re-exported from the package root -- import it explicitly:

```python
from cad_core.local_cad import build_part
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

from cad_core.model import Box, Part, Position, Size

try:
    import cadquery as _cq
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "the local CAD engine requires CadQuery, which is an optional "
        "dependency of cad-core. Install it with the 'local-cad' extra, e.g. "
        "`pip install cadquery`."
    ) from exc

#: Name of the geometry backend this module drives.
BACKEND_NAME = "cadquery"

#: Version of that backend, read from the installed package.
BACKEND_VERSION = _cq.__version__

#: Unit systems this engine accepts (specification Section A.3).
SUPPORTED_UNITS: Tuple[str, ...] = ("mm",)


class UnsupportedGeometryError(Exception):
    """Raised when a part is outside the subset this engine can build.

    Deliberately distinct from
    :class:`cad_core.featurescript.UnsupportedPartError`: the two backends are
    independent execution paths and their supported subsets are free to
    diverge, so neither imports the other's error.
    """


@dataclass(frozen=True)
class BoundingBox:
    """Extents of a built shape, measured by the kernel.

    Expressed with the specification's own :class:`~cad_core.model.Position`
    and :class:`~cad_core.model.Size` types, so callers never need a CadQuery
    type to read a measurement.
    """

    minimum: Position
    maximum: Position

    @property
    def size(self) -> Size:
        return Size(
            x=self.maximum.x - self.minimum.x,
            y=self.maximum.y - self.minimum.y,
            z=self.maximum.z - self.minimum.z,
        )


@dataclass(frozen=True)
class LocalCadResult:
    """One built part: the B-rep shape plus the identity it came from.

    ``shape`` is the kernel object (a ``cadquery.Shape``), exposed because
    later stages -- export, visualisation -- need it. The measurement helpers
    exist so ordinary callers can inspect the result without touching CadQuery
    directly.
    """

    part_name: str
    feature_id: str
    shape: Any

    def is_solid(self) -> bool:
        """True if the kernel reports a topologically valid solid.

        Both checks come from OpenCascade: ``ShapeType`` is the shape's real
        topological type, and ``isValid`` runs the kernel's own validity
        analysis. Existence of an object is never taken as evidence.
        """
        return self.shape.ShapeType() == "Solid" and bool(self.shape.isValid())

    def solid_count(self) -> int:
        """Number of solids in the result, as enumerated by the kernel."""
        return len(self.shape.Solids())

    def bounding_box(self) -> BoundingBox:
        """Kernel-measured bounding box of the result."""
        box = self.shape.BoundingBox()
        return BoundingBox(
            minimum=Position(x=box.xmin, y=box.ymin, z=box.zmin),
            maximum=Position(x=box.xmax, y=box.ymax, z=box.zmax),
        )

    def volume(self) -> float:
        """Kernel-computed volume, in the cube of the part's declared unit."""
        return float(self.shape.Volume())


def build_part(part: Part) -> LocalCadResult:
    """Build real CAD geometry for a single-box part.

    Args:
        part: A validated :class:`~cad_core.model.Part`, as returned in
            :attr:`~cad_core.errors.ValidationResult.part`. The part is assumed
            to have passed static validation; this function does not re-run
            rules S1-S20 and will not repair or reinterpret it.

    Returns:
        A :class:`LocalCadResult` wrapping the B-rep solid.

    Raises:
        TypeError: if ``part`` is not a :class:`~cad_core.model.Part`. A raw
            specification document is not accepted.
        UnsupportedGeometryError: if the part is outside the supported subset
            -- units other than millimetres, a feature count other than one, or
            a feature that is not a box. Nothing partial is built.
    """
    box = _require_supported_box(part)
    shape = _cq.Solid.makeBox(
        box.size.x,
        box.size.y,
        box.size.z,
        pnt=_cq.Vector(box.position.x, box.position.y, box.position.z),
    )
    return LocalCadResult(part_name=part.name, feature_id=box.id, shape=shape)


def _require_supported_box(part: Any) -> Box:
    """Return the part's single box, or fail explicitly."""
    if not isinstance(part, Part):
        raise TypeError(
            "build_part requires a typed cad_core.model.Part, such as the "
            "'part' of a successful validate() result; got "
            f"{type(part).__name__}. Raw specification documents are not accepted."
        )

    if part.units not in SUPPORTED_UNITS:
        supported = ", ".join(repr(unit) for unit in SUPPORTED_UNITS)
        raise UnsupportedGeometryError(
            f"unsupported units {part.units!r}; this engine builds {supported} "
            "geometry and does not convert other unit systems"
        )

    if len(part.features) != 1:
        raise UnsupportedGeometryError(
            "this engine builds a part with exactly one feature; got "
            f"{len(part.features)}. Multi-feature histories are not supported yet."
        )

    feature = part.features[0]
    if not isinstance(feature, Box):
        raise UnsupportedGeometryError(
            f"unsupported feature type {feature.TYPE!r}; this engine builds "
            "only 'box'. Cylinders, through-holes, boolean subtraction, "
            "fillets and chamfers are not implemented."
        )
    return feature
