"""STL mesh export for the local CAD backend, with round-trip verification.

STL is a **mesh** interchange format, not a B-rep CAD format, and this module
is deliberately the end of a one-way street:

```
CAD specification -> local B-rep CAD engine -> B-rep solid -> tessellation -> STL
```

The B-rep solid stays the internal geometry representation. Tessellation
happens here, at the very edge of the system, and the triangles never travel
back inward: no mesh concept appears in the specification, the validator, the
local engine, or the STEP and IGES exporters. STL is one of three independent
output paths from the same `LocalCadResult`, alongside
:mod:`cad_core.step_export` and :mod:`cad_core.iges_export`; none imports
another.

What STL is and is not
----------------------
An STL file is a bag of triangles approximating the boundary of the solid. It
does **not** carry CAD feature history, parametric dimensions, feature ids,
B-rep topology, or design intent. A round trip recovers triangles and their
coordinates -- nothing more. STEP and IGES are the formats for preserving
geometry as geometry; STL is for consumption by mesh tools, and later for
browser visualisation, where triangles are exactly what a renderer wants.

Backend
-------
CadQuery's ``Shape.exportStl``, which tessellates with OpenCascade's
``BRepMesh_IncrementalMesh`` and writes with ``StlAPI_Writer``. Reading back
uses OpenCascade's own mesh reader, ``OCP.RWStl.RWStl.ReadFile_s``, which
returns a ``Poly_Triangulation`` -- the triangles themselves, rather than
converting each one into a B-rep face.

Binary, not ASCII
-----------------
``exportStl``'s ``ascii`` parameter defaults to ``False``, so binary is both
the library default and this module's only mode. Binary STL is compact, has a
strictly specified layout that can be validated structurally, and is what mesh
consumers expect. ASCII STL is not offered.

Tessellation settings
---------------------
All four settings are passed explicitly rather than left to library defaults:

* ``tolerance`` = :data:`DEFAULT_LINEAR_DEFLECTION_MM` (0.01 mm) -- the maximum
  distance between the true surface and its triangulation. That is 1/10000 of
  the reference part's 100 mm largest dimension: visually exact at
  millimetre scale and finer than 3D-printing or visualisation needs, without
  producing needlessly dense meshes.
* ``angularTolerance`` = :data:`DEFAULT_ANGULAR_DEFLECTION_RAD` (0.1 rad, about
  5.7 degrees) -- the limit on the angle between adjacent facets, which is what
  controls smoothness on curved surfaces.
* ``relative=False`` -- deflection is an absolute length in millimetres. The
  library default, ``True``, scales the tolerance by the size of each edge,
  which would make the documented 0.01 mm not actually mean 0.01 mm.
* ``parallel=False`` -- meshing runs single-threaded. Byte-identical output was
  measured under both settings, so this is a conservative choice that removes a
  potential source of ordering nondeterminism at no measured cost. Worth
  revisiting if models ever get large enough for meshing time to matter.

For the current single-box part every one of these settings is immaterial: a
box has only planar faces, which need no refinement, so it tessellates to
exactly 12 triangles (two per face) at any tolerance. The values are chosen and
documented for the curved geometry the engine will eventually build.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, Union

from cad_core.local_cad import BoundingBox, LocalCadResult, Position

try:
    from OCP.OSD import OSD_Path
    from OCP.RWStl import RWStl
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "STL export requires CadQuery and its OpenCascade bindings, an "
        "optional dependency of cad-core. Install it with the 'local-cad' "
        "extra, e.g. `pip install cadquery`."
    ) from exc

#: File extensions this exporter writes.
STL_EXTENSIONS: Tuple[str, ...] = (".stl",)

#: Maximum distance between the true surface and its triangulation, in mm.
DEFAULT_LINEAR_DEFLECTION_MM = 0.01

#: Maximum angle between adjacent facets, in radians (~5.7 degrees).
DEFAULT_ANGULAR_DEFLECTION_RAD = 0.1

#: Binary STL layout: an 80-byte header, a 4-byte little-endian triangle count,
#: then 50 bytes per triangle (a normal and three vertices as 12 floats, plus a
#: 2-byte attribute field).
STL_HEADER_BYTES = 80
STL_COUNT_BYTES = 4
STL_TRIANGLE_BYTES = 50

_ASCII = False  # binary STL only
_RELATIVE = False  # absolute deflection, in millimetres
_PARALLEL = False  # single-threaded meshing

PathLike = Union[str, "os.PathLike[str]"]


class UnsupportedStlExtensionError(Exception):
    """Raised for an output extension this exporter does not write.

    Parallel to -- but deliberately independent of -- the STEP and IGES
    exporters' equivalents, so catching one never silently catches another.
    """


class StlExportError(Exception):
    """Raised when an STL file could not be written, produced, or read."""


@dataclass(frozen=True)
class StlBinaryFacts:
    """Structural facts read straight from a binary STL file's bytes.

    Establishes that a file really is binary STL, rather than trusting its
    extension: the declared triangle count must account for the file's size
    exactly.
    """

    path: Path
    file_size: int
    header: bytes
    declared_triangles: int

    @property
    def expected_size(self) -> int:
        return (
            STL_HEADER_BYTES
            + STL_COUNT_BYTES
            + STL_TRIANGLE_BYTES * self.declared_triangles
        )

    @property
    def is_structurally_consistent(self) -> bool:
        return self.file_size == self.expected_size

    @property
    def looks_ascii(self) -> bool:
        """True if the file opens like an ASCII STL, which begins ``solid``."""
        return self.header.lstrip()[:5].lower() == b"solid"


@dataclass(frozen=True)
class ImportedStlMesh:
    """A binary STL file read back by OpenCascade's mesh reader.

    Holds triangles, not topology. There is no solid here, no faces, no edges
    and no feature identity -- STL does not carry them, so this type does not
    pretend to expose them.
    """

    path: Path
    triangulation: object

    def triangle_count(self) -> int:
        return int(self.triangulation.NbTriangles())

    def node_count(self) -> int:
        """Number of distinct mesh nodes after the reader merges coordinates."""
        return int(self.triangulation.NbNodes())

    def nodes(self) -> Tuple[Tuple[float, float, float], ...]:
        """Every mesh node, as plain coordinate triples."""
        triangulation = self.triangulation
        return tuple(
            (node.X(), node.Y(), node.Z())
            for node in (
                triangulation.Node(index)
                for index in range(1, triangulation.NbNodes() + 1)
            )
        )

    def bounding_box(self) -> BoundingBox:
        """Bounding box of the mesh nodes.

        This is the mesh's envelope, not the B-rep's bounding box. For planar
        geometry they coincide; for a tessellated curved surface the mesh lies
        inside the true surface by up to the linear deflection.
        """
        points = self.nodes()
        if not points:
            raise StlExportError(f"{self.path} contains no mesh nodes")
        xs, ys, zs = zip(*points)
        return BoundingBox(
            minimum=Position(x=min(xs), y=min(ys), z=min(zs)),
            maximum=Position(x=max(xs), y=max(ys), z=max(zs)),
        )


def export_stl(
    result: LocalCadResult,
    path: PathLike,
    *,
    tolerance: float = DEFAULT_LINEAR_DEFLECTION_MM,
    angular_tolerance: float = DEFAULT_ANGULAR_DEFLECTION_RAD,
) -> Path:
    """Tessellate ``result`` and write it as a binary STL file.

    Args:
        result: A :class:`~cad_core.local_cad.LocalCadResult` from
            :func:`~cad_core.local_cad.build_part`. Raw dictionaries, bare
            kernel shapes, loose mesh data and CadQuery source are not accepted.
        path: Destination, ending in ``.stl`` (case-insensitive).
        tolerance: Linear deflection in millimetres. Must be positive.
        angular_tolerance: Angular deflection in radians. Must be positive.

    Returns:
        The :class:`~pathlib.Path` actually written.

    Raises:
        TypeError: if ``result`` is not a ``LocalCadResult``.
        ValueError: if either tolerance is not positive.
        UnsupportedStlExtensionError: for any other extension.
        StlExportError: if the destination cannot be written, if the writer
            reports failure, or if it returns without leaving a non-empty file.
    """
    if not isinstance(result, LocalCadResult):
        raise TypeError(
            "export_stl requires a cad_core.local_cad.LocalCadResult, as "
            f"returned by build_part(); got {type(result).__name__}."
        )
    if not tolerance > 0:
        raise ValueError(f"tolerance must be positive, got {tolerance!r}")
    if not angular_tolerance > 0:
        raise ValueError(
            f"angular_tolerance must be positive, got {angular_tolerance!r}"
        )

    destination = Path(os.fspath(path))
    if destination.suffix.lower() not in STL_EXTENSIONS:
        supported = ", ".join(repr(extension) for extension in STL_EXTENSIONS)
        raise UnsupportedStlExtensionError(
            f"unsupported output extension {destination.suffix!r}; this "
            f"exporter writes binary STL only, as {supported}. STEP and IGES "
            "are separate exporters; 3MF, OBJ and glTF are not implemented."
        )

    parent = destination.parent
    if not parent.is_dir():
        raise StlExportError(
            f"cannot write {destination}: the directory {parent} does not exist"
        )

    try:
        wrote = result.shape.exportStl(
            str(destination),
            tolerance,
            angular_tolerance,
            _ASCII,
            _RELATIVE,
            _PARALLEL,
        )
    except OSError as exc:
        raise StlExportError(f"cannot write {destination}: {exc}") from exc
    if not wrote:
        raise StlExportError(f"the STL writer reported failure for {destination}")

    # Report success only for a file that is actually there and has content.
    if not destination.is_file():
        raise StlExportError(f"the STL writer did not produce a file at {destination}")
    if destination.stat().st_size == 0:
        raise StlExportError(f"the STL writer produced an empty file at {destination}")
    return destination


def binary_stl_facts(path: PathLike) -> StlBinaryFacts:
    """Read a binary STL file's structural header without a geometry kernel.

    Used to establish that a file really is binary STL: the 4-byte triangle
    count must account for the file's length exactly.

    Raises:
        StlExportError: if the file is missing or too short to hold a header
            and a triangle count.
    """
    source = Path(os.fspath(path))
    if not source.is_file():
        raise StlExportError(f"no STL file at {source}")
    data = source.read_bytes()
    minimum = STL_HEADER_BYTES + STL_COUNT_BYTES
    if len(data) < minimum:
        raise StlExportError(
            f"{source} is {len(data)} bytes, too short for a binary STL header "
            f"({minimum} bytes minimum)"
        )
    (declared,) = struct.unpack_from("<I", data, STL_HEADER_BYTES)
    return StlBinaryFacts(
        path=source,
        file_size=len(data),
        header=data[:STL_HEADER_BYTES],
        declared_triangles=declared,
    )


def read_stl(path: PathLike) -> ImportedStlMesh:
    """Read a binary STL file back with OpenCascade's mesh reader.

    Verification support, not a general importer: STL is never an input to the
    pipeline, and a mesh can never become the internal representation.

    Raises:
        UnsupportedStlExtensionError: for an extension this module does not read.
        StlExportError: if the file is missing, structurally inconsistent, or
            the reader will not parse it.
    """
    source = Path(os.fspath(path))
    if source.suffix.lower() not in STL_EXTENSIONS:
        supported = ", ".join(repr(extension) for extension in STL_EXTENSIONS)
        raise UnsupportedStlExtensionError(
            f"unsupported input extension {source.suffix!r}; this module reads "
            f"binary STL only, as {supported}."
        )

    facts = binary_stl_facts(source)
    if not facts.is_structurally_consistent:
        raise StlExportError(
            f"{source} is not a consistent binary STL: it declares "
            f"{facts.declared_triangles} triangles, which needs "
            f"{facts.expected_size} bytes, but the file is {facts.file_size}"
        )

    triangulation = RWStl.ReadFile_s(OSD_Path(str(source)))
    if triangulation is None:
        raise StlExportError(f"OpenCascade could not read {source} as STL")
    return ImportedStlMesh(path=source, triangulation=triangulation)
