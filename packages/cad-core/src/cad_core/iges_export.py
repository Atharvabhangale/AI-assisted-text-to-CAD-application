"""IGES export for the local CAD backend, with round-trip verification support.

IGES (ASME Y14.26M / IGES 5.3) is the project's second interchange format,
alongside STEP. The two exporters are independent: neither imports the other,
and choosing one never redirects to the other.

```
CAD specification -> local CAD engine -> B-rep solid -> IGES file   (this module)
CAD specification -> local CAD engine -> B-rep solid -> STEP file   (cad_core.step_export)
```

The neutral CAD specification remains the source of truth. IGES is an output
representation generated from the local CAD result, and nothing in the
specification knows IGES exists.

Backend: OpenCascade directly, not CadQuery
-------------------------------------------
**CadQuery cannot write or read IGES.** Its ``exporters.ExportTypes`` offers
AMF, BIN, BREP, DXF, STEP, STL, SVG, THREEMF, TJS, VRML and VTP -- no IGES --
and ``cadquery.importers`` exposes only ``importStep``, ``importDXF``,
``importBrep``, ``importBin`` and ``importShape``. This module therefore drives
OpenCascade directly through ``OCP.IGESControl``:

* write -- ``IGESControl_Writer`` -> ``AddShape`` -> ``ComputeModel`` -> ``Write``
* read  -- ``IGESControl_Reader`` -> ``ReadFile`` -> ``TransferRoots`` -> ``OneShape``

BRep mode is not optional here
------------------------------
``IGESControl_Writer(unit, theModecr)`` takes a mode that decides what the file
actually contains, and the two modes round-trip very differently. Measured on
the reference box:

===========================  ====================  ==========================
Property                     ``theModecr=0``       ``theModecr=1``
                             (faces, the default)  (BRep, used here)
===========================  ====================  ==========================
imported ``ShapeType``       ``Compound``          ``Solid``
solids / shells              0 / 0                 1 / 1
faces / edges / vertices     6 / 24 / 24           6 / 12 / 8
bounding box                 exact                 exact
``Volume()``                 **15200.0**           **60000.0**
===========================  ====================  ==========================

In faces mode the six faces come back unstitched -- 24 edges rather than 12,
because nothing is shared -- so there is no solid, and ``Volume()`` still
returns a number, just a meaningless one. That is precisely the trap this
module avoids: it writes with :data:`IGES_BREP_MODE` so a solid really is
preserved, and the tests assert the topology rather than assuming it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple, Union

from cad_core.local_cad import (
    BoundingBox,
    LocalCadResult,
    shape_bounding_box,
    shape_is_solid,
    shape_solid_count,
    shape_volume,
)

try:
    from cadquery import Shape as _Shape
    from OCP.IFSelect import IFSelect_RetDone as _RET_DONE
    from OCP.IGESControl import IGESControl_Reader, IGESControl_Writer
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "IGES export requires CadQuery and its OpenCascade bindings, an "
        "optional dependency of cad-core. Install it with the 'local-cad' "
        "extra, e.g. `pip install cadquery`."
    ) from exc

#: File extensions this exporter writes. Both produce the same IGES content.
IGES_EXTENSIONS: Tuple[str, ...] = (".igs", ".iges")

#: ``IGESControl_Writer`` mode that emits B-rep entities rather than loose
#: faces. Mode 0, the default, does not survive a round trip as a solid.
IGES_BREP_MODE = 1

#: Unit written into the IGES file. V1 fixes the specification's units as mm.
_UNIT = "MM"

PathLike = Union[str, "os.PathLike[str]"]


class UnsupportedIgesExtensionError(Exception):
    """Raised for an output extension this exporter does not write.

    An IGES export request is never silently redirected to STEP or any other
    format. Parallel to -- but deliberately independent of --
    :class:`cad_core.step_export.UnsupportedExportFormatError`.
    """


class IgesExportError(Exception):
    """Raised when an IGES file could not be written, produced, or read."""


@dataclass(frozen=True)
class ImportedIgesShape:
    """An IGES file read back from disk, for verifying an export.

    Named for a *shape*, not a solid: what IGES returns depends on how it was
    written, and this type does not presume the answer. Ask
    :meth:`shape_type` and :meth:`is_solid` rather than assuming.
    """

    path: Path
    shape: Any

    def shape_type(self) -> str:
        """The kernel's topological type for the imported shape."""
        return self.shape.ShapeType()

    def is_solid(self) -> bool:
        return shape_is_solid(self.shape)

    def solid_count(self) -> int:
        return shape_solid_count(self.shape)

    def face_count(self) -> int:
        return len(self.shape.Faces())

    def edge_count(self) -> int:
        return len(self.shape.Edges())

    def vertex_count(self) -> int:
        return len(self.shape.Vertices())

    def bounding_box(self) -> BoundingBox:
        return shape_bounding_box(self.shape)

    def volume(self) -> float:
        """Kernel-computed volume.

        Meaningful only when the imported shape is a solid; for a compound of
        unstitched faces the kernel still returns a number, and it is not the
        enclosed volume. Check :meth:`is_solid` before trusting this.
        """
        return shape_volume(self.shape)


def export_iges(result: LocalCadResult, path: PathLike) -> Path:
    """Write ``result`` to an IGES file and return the path written.

    Args:
        result: A :class:`~cad_core.local_cad.LocalCadResult` from
            :func:`~cad_core.local_cad.build_part`. Raw dictionaries, bare
            kernel shapes and CadQuery source are not accepted.
        path: Destination, ending in ``.igs`` or ``.iges`` (case-insensitive).

    Returns:
        The :class:`~pathlib.Path` actually written.

    Raises:
        TypeError: if ``result`` is not a ``LocalCadResult``.
        UnsupportedIgesExtensionError: for any other extension. The request is
            never redirected to another format.
        IgesExportError: if the destination cannot be written, if OpenCascade
            refuses the shape or the write, or if the writer returned without
            leaving a non-empty file behind.
    """
    if not isinstance(result, LocalCadResult):
        raise TypeError(
            "export_iges requires a cad_core.local_cad.LocalCadResult, as "
            f"returned by build_part(); got {type(result).__name__}."
        )

    destination = Path(os.fspath(path))
    if destination.suffix.lower() not in IGES_EXTENSIONS:
        supported = ", ".join(repr(extension) for extension in IGES_EXTENSIONS)
        raise UnsupportedIgesExtensionError(
            f"unsupported output extension {destination.suffix!r}; this "
            f"exporter writes IGES only, as {supported}. STEP is a separate "
            "exporter (cad_core.step_export); STL, 3MF and DXF are not "
            "implemented."
        )

    parent = destination.parent
    if not parent.is_dir():
        raise IgesExportError(
            f"cannot write {destination}: the directory {parent} does not exist"
        )

    writer = IGESControl_Writer(_UNIT, IGES_BREP_MODE)
    if not writer.AddShape(result.shape.wrapped):
        raise IgesExportError(
            f"OpenCascade rejected the shape for IGES translation: {destination}"
        )
    writer.ComputeModel()
    try:
        wrote = writer.Write(str(destination))
    except OSError as exc:
        raise IgesExportError(f"cannot write {destination}: {exc}") from exc
    if not wrote:
        raise IgesExportError(f"the IGES writer reported failure for {destination}")

    # Report success only for a file that is actually there and has content.
    if not destination.is_file():
        raise IgesExportError(
            f"the IGES writer did not produce a file at {destination}"
        )
    if destination.stat().st_size == 0:
        raise IgesExportError(f"the IGES writer produced an empty file at {destination}")
    return destination


def read_iges(path: PathLike) -> ImportedIgesShape:
    """Read an IGES file back, for verifying that an export round-trips.

    Uses OpenCascade's ``IGESControl_Reader``. This is verification support,
    not a general importer: IGES is never an input to the pipeline.

    A successful ``ReadFile`` is not sufficient evidence that a file is IGES --
    the reader also returns ``IFSelect_RetDone`` for empty files and for
    arbitrary text, transferring zero roots. Requiring at least one
    transferred root is what actually distinguishes an IGES file here.

    Raises:
        UnsupportedIgesExtensionError: for an extension this module does not read.
        IgesExportError: if the file is missing, the reader rejects it, or it
            yields no transferable entity.
    """
    source = Path(os.fspath(path))
    if source.suffix.lower() not in IGES_EXTENSIONS:
        supported = ", ".join(repr(extension) for extension in IGES_EXTENSIONS)
        raise UnsupportedIgesExtensionError(
            f"unsupported input extension {source.suffix!r}; this module reads "
            f"IGES only, as {supported}."
        )
    if not source.is_file():
        raise IgesExportError(f"no IGES file at {source}")

    reader = IGESControl_Reader()
    status = reader.ReadFile(str(source))
    if status != _RET_DONE:
        raise IgesExportError(
            f"OpenCascade could not read {source} as IGES (status {status})"
        )
    if reader.TransferRoots() < 1:
        raise IgesExportError(
            f"{source} yielded no transferable IGES entity; it is not a usable "
            "IGES file"
        )
    return ImportedIgesShape(path=source, shape=_Shape.cast(reader.OneShape()))
