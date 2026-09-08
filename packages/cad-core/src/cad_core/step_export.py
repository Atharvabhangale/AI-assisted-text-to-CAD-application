"""STEP export for the local CAD backend, with round-trip verification support.

STEP (ISO 10303) is the project's first **interoperable** CAD output: unlike a
FeatureScript document, which only Onshape understands, a STEP file can be
opened by essentially any mechanical CAD system.

Position in the pipeline
------------------------
```
CAD specification -> local CAD engine -> B-rep solid -> STEP file
```

The dependency direction is one-way and stays that way. This module imports
:mod:`cad_core.local_cad`; nothing imports it. The specification, the
validator, the FeatureScript generator and the Onshape adapter know nothing
about STEP, and STEP concepts never travel back up into the specification.

**The neutral CAD specification remains the source of truth. STEP is an output
representation generated from the local CAD result.**

Backend
-------
CadQuery's ``exporters.export`` with ``exportType="STEP"`` (OpenCascade's STEP
writer underneath), and ``importers.importStep`` for reading back, which uses
OpenCascade's ``STEPControl_Reader``.

``exportType`` is always passed explicitly rather than inferred from the file
name. CadQuery infers the format by upper-casing the extension and looking it
up in its ``ExportTypes``: ``.step`` resolves to ``STEP``, but ``.stp`` becomes
``STP``, which is not a member, and inference raises
``ValueError("Unknown extensions, specify export type explicitly")``. Passing
the type explicitly makes both extensions behave identically and removes the
dependence on that lookup entirely.

Units
-----
V1 fixes the specification's unit system as millimetres, so ``unit="MM"`` is
passed explicitly and the STEP file declares millimetres. No conversion happens
anywhere.
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
    from cadquery import exporters as _exporters
    from cadquery import importers as _importers
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "STEP export requires CadQuery, which is an optional dependency of "
        "cad-core. Install it with the 'local-cad' extra, e.g. "
        "`pip install cadquery`."
    ) from exc

#: File extensions this exporter writes. Both produce the same STEP content;
#: the two spellings are conventional aliases for the same format.
STEP_EXTENSIONS: Tuple[str, ...] = (".step", ".stp")

#: CadQuery export type and unit passed explicitly on every export.
_EXPORT_TYPE = "STEP"
_UNIT = "MM"

PathLike = Union[str, "os.PathLike[str]"]


class UnsupportedExportFormatError(Exception):
    """Raised for an output extension this exporter does not write.

    The format is never silently switched: a path this exporter cannot honour
    is rejected rather than written in some other format.
    """


class StepExportError(Exception):
    """Raised when a STEP file could not be written, or was not produced."""


@dataclass(frozen=True)
class ImportedStepSolid:
    """A STEP file read back from disk, for verifying an export.

    Measurement helpers are the same ones the local engine uses, so an exported
    shape and a re-imported one are measured identically.
    """

    path: Path
    shape: Any

    def is_solid(self) -> bool:
        return shape_is_solid(self.shape)

    def solid_count(self) -> int:
        return shape_solid_count(self.shape)

    def bounding_box(self) -> BoundingBox:
        return shape_bounding_box(self.shape)

    def volume(self) -> float:
        return shape_volume(self.shape)


def export_step(result: LocalCadResult, path: PathLike) -> Path:
    """Write ``result`` to a STEP file and return the path written.

    Args:
        result: A :class:`~cad_core.local_cad.LocalCadResult` from
            :func:`~cad_core.local_cad.build_part`. Raw dictionaries, bare
            kernel shapes and CadQuery source are not accepted.
        path: Destination, ending in ``.step`` or ``.stp`` (case-insensitive).

    Returns:
        The resolved :class:`~pathlib.Path` actually written.

    Raises:
        TypeError: if ``result`` is not a ``LocalCadResult``.
        UnsupportedExportFormatError: for any other extension. The format is
            never silently switched.
        StepExportError: if the destination cannot be written, or if the
            exporter returned without leaving a non-empty file behind.
    """
    if not isinstance(result, LocalCadResult):
        raise TypeError(
            "export_step requires a cad_core.local_cad.LocalCadResult, as "
            f"returned by build_part(); got {type(result).__name__}."
        )

    destination = Path(os.fspath(path))
    suffix = destination.suffix.lower()
    if suffix not in STEP_EXTENSIONS:
        supported = ", ".join(repr(extension) for extension in STEP_EXTENSIONS)
        raise UnsupportedExportFormatError(
            f"unsupported output extension {destination.suffix!r}; this "
            f"exporter writes STEP only, as {supported}. IGES, STL and other "
            "formats are not implemented."
        )

    parent = destination.parent
    if not parent.is_dir():
        raise StepExportError(
            f"cannot write {destination}: the directory {parent} does not exist"
        )

    try:
        _exporters.export(
            result.shape, str(destination), exportType=_EXPORT_TYPE, unit=_UNIT
        )
    except OSError as exc:
        raise StepExportError(f"cannot write {destination}: {exc}") from exc

    # Report success only for a file that is actually there and has content.
    if not destination.is_file():
        raise StepExportError(
            f"the STEP exporter did not produce a file at {destination}"
        )
    if destination.stat().st_size == 0:
        raise StepExportError(f"the STEP exporter produced an empty file at {destination}")
    return destination


def read_step(path: PathLike) -> ImportedStepSolid:
    """Read a STEP file back, for verifying that an export round-trips.

    Uses OpenCascade's STEP reader through ``cadquery.importers.importStep``.
    This is verification support, not a general-purpose importer: the neutral
    specification remains the source of truth, and nothing in the pipeline
    consumes STEP as an input.

    Raises:
        UnsupportedExportFormatError: for an extension this module does not
            handle.
        StepExportError: if the file does not exist or the reader rejects it.
    """
    source = Path(os.fspath(path))
    if source.suffix.lower() not in STEP_EXTENSIONS:
        supported = ", ".join(repr(extension) for extension in STEP_EXTENSIONS)
        raise UnsupportedExportFormatError(
            f"unsupported input extension {source.suffix!r}; this module reads "
            f"STEP only, as {supported}."
        )
    if not source.is_file():
        raise StepExportError(f"no STEP file at {source}")

    try:
        workplane = _importers.importStep(str(source))
    except (ValueError, OSError) as exc:
        raise StepExportError(f"cannot read {source}: {exc}") from exc
    return ImportedStepSolid(path=source, shape=workplane.val())
