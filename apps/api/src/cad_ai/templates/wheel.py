"""Template: a spoked wheel with tyre.

The wheel lies on the Y axis. A tyre ring (rounded rims, bored out) holds a
rim ring that overlaps it by 10 mm; four spokes along +X, -X, +Z and -Z join
the rim to a hub with an axle bore and four bolt holes at 45 degrees.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.templates._helpers import answer, chamfer, cyl, fillet, hole, subtract, union, box


def _build() -> Mapping[str, Any]:
    f: list = []
    f += [cyl("tyre", 600, 180, (0, 0, 0), "+Y"), fillet("round_tyre", "tyre", 25)]
    f += [cyl("tyre_bore", 440, 184, (0, -2, 0), "+Y"), subtract("cut_tyre_bore", "tyre", ["tyre_bore"])]
    f += [cyl("rim", 460, 150, (0, 15, 0), "+Y"), cyl("rim_cavity", 420, 154, (0, 13, 0), "+Y")]
    f += [subtract("hollow_rim", "rim", ["rim_cavity"])]
    f += [cyl("hub", 120, 120, (0, 30, 0), "+Y"), chamfer("chamfer_hub", "hub", 4)]
    f += [cyl("hub_boss", 90, 40, (0, 150, 0), "+Y")]
    f += [
        box("spoke_px", (212, 36, 36), (10, 72, -18)),
        box("spoke_nx", (212, 36, 36), (-222, 72, -18)),
        box("spoke_pz", (36, 36, 212), (-18, 72, 10)),
        box("spoke_nz", (36, 36, 212), (-18, 72, -222)),
    ]
    f += [union("join_rim", "tyre", ["rim"])]
    f += [union("join_hub", "tyre", ["hub", "hub_boss", "spoke_px", "spoke_nx", "spoke_pz", "spoke_nz"])]
    f += [hole("axle_bore", "tyre", 40, (0, 0, 0), "+Y")]
    for n, (x, z) in enumerate(((32, 32), (-32, 32), (-32, -32), (32, -32)), 1):
        f += [hole(f"bolt_hole_{n}", "tyre", 12, (x, 0, z), "+Y")]
    return {
        "request": "a spoked wheel with a tyre, rim, hub, four spokes, an axle bore and bolt holes",
        "answer": answer(
            plan=(
                "Axis Y, centre x=0 z=0. Tyre d600 y0-180, edges filleted r25, bored out to d440. "
                "Rim d460 y15-165 hollowed to d420 (a 20 mm ring r210-230), overlapping the tyre "
                "r220-230. Hub d120 y30-150 plus boss d90 y148-188 (2 mm inside). Four spokes "
                "36x36, y72-108, running x or z from 10 to 222 (inside the hub and the rim ring). "
                "Axle bore d40 along Y; bolt holes d12 at (+-32,+-32), clear of the spokes."
            ),
            assumptions=[
                "Four spokes only along the X and Z axes (V1 has no rotation).",
                "Tyre is a plain ring with rounded edges and no tread pattern.",
                "Tyre, rim and hub are one solid, since V1 allows a single body.",
                "Sizes are typical for a small cart wheel, about 600 mm across.",
            ],
            name="Spoked wheel",
            description="Wheel with a rounded tyre, rim ring, hub, four spokes, axle bore and four bolt holes.",
            features=f,
            summary="A 600 mm spoked wheel with a rounded tyre, rim, four square spokes, a hub with an axle bore and four bolt holes.",
        ),
    }


TEMPLATE = {
    "name": "wheel",
    "keywords": ("wheel", "spoked wheel", "tyre", "tire", "rim"),
    **_build(),
}
