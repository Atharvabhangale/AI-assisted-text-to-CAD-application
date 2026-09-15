"""Where a pattern's instances go. Arithmetic, and nothing else.

Separate from the adapter on purpose. Placing an instance is a fact about
the **representation** -- a rule the plan states and anyone reading the plan
can reproduce -- not a fact about CadQuery, FreeCAD or any other engine. Put
in the adapter it would be one backend's idea of where a hole goes; put here
it is the plan's, and every backend gets the same answer.

Nothing in this module imports a kernel, ``cad_core`` or a backend, and
nothing in it decides whether the result is *good* geometry. Whether a
pattern's fourth hole still meets material is E1's question and the engine's,
exactly as it is for a single hole.

Determinism
-----------
The same plan must always give the same document, so nothing here is
order-dependent, randomised or accumulated: instance ``k`` is computed from
the source and ``k`` alone, never from instance ``k - 1``. Accumulating a
rotation would let floating-point error grow along the pattern and would make
the eighth hole depend on how the first seven were computed.
"""

from __future__ import annotations

import math
from typing import Tuple

from .plan import (
    FULL_TURN,
    LinearPlacement,
    Point,
    RadialPlacement,
)

#: The unit vector of each signed principal direction. A table, so the six
#: names are spelled once and a translation never reads a component by a
#: computed attribute name.
AXIS_VECTOR = {
    "+X": (1.0, 0.0, 0.0),
    "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0),
    "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0),
    "-Z": (0.0, 0.0, -1.0),
}


class PatternError(Exception):
    """A placement that cannot be turned into positions.

    Only reachable from a plan the validator has not seen: P27-P30 catch
    every case a parsed plan can present. Raised rather than defaulted
    because inventing a position is exactly the silent approximation this
    project forbids.
    """


def instance_positions(
    placement: object, source: Point, count: int
) -> Tuple[Point, ...]:
    """Where each instance of a pattern sits, instance 0 first.

    The returned tuple has ``count`` entries and its first is ``source``
    itself, unmoved: a pattern **adds** repeats beside the feature it
    repeats, and instance 0 is that feature. A caller that writes a feature
    per entry would write the source twice, so the adapter writes the source
    once and then entries 1 onward.
    """
    if count < 1:
        raise PatternError(f"a pattern needs at least one instance, got {count}")
    if isinstance(placement, LinearPlacement):
        return _linear(placement, source, count)
    if isinstance(placement, RadialPlacement):
        return _radial(placement, source, count)
    raise PatternError(f"unknown placement {type(placement).__name__}")


def _linear(
    placement: LinearPlacement, source: Point, count: int
) -> Tuple[Point, ...]:
    """Instance ``k`` displaced ``k * spacing`` along the signed axis."""
    vector = AXIS_VECTOR.get(placement.axis)
    if vector is None:
        raise PatternError(f"unknown axis {placement.axis!r}")
    dx, dy, dz = vector
    return tuple(
        Point(
            x=source.x + dx * placement.spacing * index,
            y=source.y + dy * placement.spacing * index,
            z=source.z + dz * placement.spacing * index,
        )
        for index in range(count)
    )


def _radial(
    placement: RadialPlacement, source: Point, count: int
) -> Tuple[Point, ...]:
    """Instance ``k`` turned ``k * step`` about the axis through the centre.

    Right-handed about the **signed** direction, so ``+Z`` and ``-Z`` put the
    instances in mirrored places. The coordinate along the axis is untouched:
    a radial pattern turns, it does not lift.
    """
    if placement.axis not in AXIS_VECTOR:
        raise PatternError(f"unknown axis {placement.axis!r}")
    letter = placement.axis[1]
    sign = 1.0 if placement.axis[0] == "+" else -1.0
    step = placement.step(count)
    centre = placement.centre

    points = []
    for index in range(count):
        # From the source every time, never from the previous instance: a
        # rotation composed step by step would accumulate error along the
        # pattern.
        radians = math.radians(sign * step * index)
        cos = math.cos(radians)
        sin = math.sin(radians)
        if letter == "Z":
            # Right-handed about +Z carries X toward Y.
            u, v = source.x - centre.x, source.y - centre.y
            points.append(Point(
                x=centre.x + u * cos - v * sin,
                y=centre.y + u * sin + v * cos,
                z=source.z,
            ))
        elif letter == "X":
            # About +X, Y toward Z.
            u, v = source.y - centre.y, source.z - centre.z
            points.append(Point(
                x=source.x,
                y=centre.y + u * cos - v * sin,
                z=centre.z + u * sin + v * cos,
            ))
        else:
            # About +Y, Z toward X.
            u, v = source.z - centre.z, source.x - centre.x
            points.append(Point(
                x=centre.x + u * sin + v * cos,
                y=source.y,
                z=centre.z + u * cos - v * sin,
            ))
    return tuple(points)


__all__ = ["AXIS_VECTOR", "FULL_TURN", "PatternError", "instance_positions"]
