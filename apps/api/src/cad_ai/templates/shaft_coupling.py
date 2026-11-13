"""Template: a flange shaft coupling.

The shaft axis is X. Two hubs (d70) carry bores of different sizes with
keyways; two d140 flanges are bolted by a ring of six bolt holes with bolt
heads and nuts as stepped cylinders; each hub has two set-screw holes.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from cad_ai.templates._helpers import answer, box, chamfer, cyl, fillet, hole, subtract, union


def _build() -> Mapping[str, Any]:
    f: list = []
    f += [cyl("hub_a", 70, 50, (0, 0, 0), "+X"), fillet("round_hub_a", "hub_a", 2)]
    f += [cyl("flange_a", 140, 20, (48, 0, 0), "+X"), chamfer("chamfer_flange_a", "flange_a", 3)]
    f += [cyl("flange_b", 140, 20, (67, 0, 0), "+X"), chamfer("chamfer_flange_b", "flange_b", 3)]
    f += [cyl("hub_b", 70, 50, (85, 0, 0), "+X"), fillet("round_hub_b", "hub_b", 2)]
    angles = [math.radians(30 + 60 * k) for k in range(6)]
    pts = [(round(50 * math.cos(a), 3), round(50 * math.sin(a), 3)) for a in angles]
    heads, nuts = [], []
    for n, (y, z) in enumerate(pts, 1):
        f += [cyl(f"bolt_head_{n}", 16, 6, (42, y, z), "+X"), chamfer(f"chamfer_head_{n}", f"bolt_head_{n}", 1)]
        heads.append(f"bolt_head_{n}")
    for n, (y, z) in enumerate(pts, 1):
        f += [cyl(f"nut_{n}", 16, 6, (85, y, z), "+X"), chamfer(f"chamfer_nut_{n}", f"nut_{n}", 1)]
        nuts.append(f"nut_{n}")
    f += [union("join_coupling", "hub_a", ["flange_a", "flange_b", "hub_b"] + heads + nuts)]
    f += [
        cyl("bore_a", 24, 71, (-2, 0, 0), "+X"),
        cyl("bore_b", 28, 71, (67, 0, 0), "+X"),
        box("keyway_a", (50, 8, 6), (-2, -4, 10)),
        box("keyway_b", (50, 8, 6), (88, -4, 12)),
    ]
    for n, (x, y0, ax) in enumerate(((25, 8, "+Y"), (25, -8, "-Y"), (111, 8, "+Y"), (111, -8, "-Y")), 1):
        f += [cyl(f"set_screw_{n}", 8, 30, (x, y0, 0), ax)]
    f += [subtract("cut_bores", "hub_a", ["bore_a", "bore_b", "keyway_a", "keyway_b"] + [f"set_screw_{n}" for n in range(1, 5)])]
    for n, (y, z) in enumerate(pts, 1):
        f += [hole(f"bolt_hole_{n}", "hub_a", 9, (0, y, z), "+X")]
    return {
        "request": "a flange shaft coupling with two hubs, bores and keyways, bolted flanges and set screws",
        "answer": answer(
            plan=(
                "Shaft axis X, centre y=z=0. Hub A d70 x0-50, flange A d140 x48-68, flange B d140 "
                "x67-87 (1 mm overlap), hub B d70 x85-135. Bolt circle r50, six points at 30+60k "
                "degrees; bolt heads d16 x42-48 on flange A's hub side, nuts d16 x85-91. Bore A d24 "
                "x-2..69, bore B d28 x67..138 (overlap, one bore). Keyways 8 wide cut up from the "
                "bore: A z10-16 x-2..48, B z12-18 x88..138. Set screws d8 on +-Y at x=25 and "
                "x=111, finite cuts from the bore wall out. Six through-holes d9 on the bolt circle "
                "cut after joining, so they also open the heads and nuts."
            ),
            assumptions=[
                "Bolt heads and nuts are plain cylinders, not hexagons.",
                "The two flanges overlap by 1 mm so they join as one solid.",
                "Hub bores are 24 mm and 28 mm with 8 mm keyways, typical for a mid-size coupling.",
                "Bolts themselves are not modelled; only their holes, heads and nuts.",
            ],
            name="Flange shaft coupling",
            description="Flange coupling: two bored and keyed hubs, two bolted flanges, and set-screw holes.",
            features=f,
            summary="A flange shaft coupling with two hubs, a 24 mm and a 28 mm bore with keyways, six bolt holes with heads and nuts, and four set screws.",
        ),
    }


TEMPLATE = {
    "name": "shaft_coupling",
    "keywords": ("shaft coupling", "flange coupling", "coupling", "coupler"),
    **_build(),
}
