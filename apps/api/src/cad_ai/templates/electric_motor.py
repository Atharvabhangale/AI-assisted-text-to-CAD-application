"""Template: a finned electric motor on a flange.

A cylindrical housing along X with a front flange (four bolt holes) and pilot
boss, a keyed and chamfered output shaft, a rear cap with a stub shaft, axis-
parallel cooling ribs, a terminal box with a cover and cable gland, and two
mounting feet with slotted bolt holes. One solid.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from cad_ai.templates._helpers import answer, box, chamfer, cyl, fillet, hole, subtract, union


def _build() -> Mapping[str, Any]:
    f: List[Dict[str, Any]] = [
        cyl("housing", 80, 120, (0, 0, 0), "+X"),
        fillet("round_housing", "housing", 3),
        box("flange", (10, 100, 100), (-8, -50, -50)),
        fillet("round_flange", "flange", 4, "X"),
    ]
    for n, (y, z) in enumerate(((-38, -38), (38, -38), (-38, 38), (38, 38)), 1):
        f.append(hole(f"flange_hole_{n}", "flange", 9, (0, y, z), "+X"))
    f += [
        cyl("pilot_boss", 50, 10, (-16, 0, 0), "+X"),
        fillet("round_boss", "pilot_boss", 1.5),
        cyl("shaft", 10, 46, (-46, 0, 0), "+X"),
        chamfer("chamfer_shaft", "shaft", 1),
        box("keyway", (25, 4, 3), (-44, -2, 4)),
        subtract("cut_keyway", "shaft", ["keyway"]),
        cyl("rear_cap", 78, 15, (118, 0, 0), "+X"),
        fillet("round_rear_cap", "rear_cap", 4),
        cyl("rear_stub", 8, 18, (130, 0, 0), "+X"),
    ]
    ribs: List[str] = []
    for n, y in enumerate((-24, -12, 0, 12, 24), 1):
        f.append(box(f"rib_top_{n}", (100, 4, 18), (10, y - 2, 28)))
        f.append(box(f"rib_bottom_{n}", (100, 4, 18), (10, y - 2, -46)))
        ribs += [f"rib_top_{n}", f"rib_bottom_{n}"]
    for n, z in enumerate((-20, 0, 20), 1):
        f.append(box(f"rib_left_{n}", (100, 18, 4), (10, 28, z - 2)))
        f.append(box(f"rib_right_{n}", (100, 18, 4), (10, -46, z - 2)))
        ribs += [f"rib_left_{n}", f"rib_right_{n}"]
    f += [
        box("terminal_box", (40, 34, 36), (40, -17, 30)),
        fillet("round_terminal_box", "terminal_box", 3),
        box("terminal_cover", (36, 30, 6), (42, -15, 62)),
        fillet("round_terminal_cover", "terminal_cover", 2),
        cyl("cable_gland", 14, 14, (78, 0, 48), "+X"),
        fillet("round_gland", "cable_gland", 2),
    ]
    feet: List[str] = []
    for n, x in enumerate((20, 80), 1):
        f.append(box(f"foot_{n}", (30, 90, 8), (x, -45, -46)))
        f.append(hole(f"foot_{n}_hole_a", f"foot_{n}", 8, (x + 15, -35, 0), "+Z"))
        f.append(hole(f"foot_{n}_hole_b", f"foot_{n}", 8, (x + 15, 35, 0), "+Z"))
        feet.append(f"foot_{n}")
    f.append(
        union(
            "assemble",
            "housing",
            ["flange", "pilot_boss", "shaft", "rear_cap", "rear_stub"] + ribs
            + ["terminal_box", "terminal_cover", "cable_gland"] + feet,
        )
    )
    return {
        "request": "an electric motor with a mounting flange, a shaft, cooling fins, a terminal box and mounting feet",
        "answer": answer(
            plan=(
                "Axis along X through y=0,z=0. Housing d80 x 0..120. Front flange "
                "10x100x100 at x -8..2, bolt holes d9 at y,z=+-38; pilot boss d50 x "
                "-16..-6; shaft d10 x -46..0 with a 25 mm keyway cut at z 4..7. Rear "
                "cap d78 x 118..133, stub d8 x 130..148. Ribs 100 long on x 10..110: "
                "five on top (z 28..46) and bottom (z -46..-28), three per side (y "
                "28..46 and -46..-28), each starting inside the housing. Terminal box "
                "40x34x36 at z 30..66 with a cover z 62..68 and a gland d14 along X "
                "from x=78. Feet 30x90x8 at z -46..-38, x 20..50 and 80..110, slotted "
                "by d8 holes at y=+-35. Everything joined into the housing."
            ),
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a frame of about 80 mm diameter and 120 mm length",
                "Cooling fins modelled as straight axis-parallel ribs",
                "Terminal box and cover left as plain boxes with a round gland",
                "Foot bolt holes are round, not slots",
            ],
            name="electric-motor",
            description="Finned electric motor with flange, keyed shaft, terminal box and feet",
            features=f,
            summary="An electric motor with a flange, keyed shaft, cooling ribs, terminal box and mounting feet.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "electric_motor",
    "keywords": ("electric motor", "motor", "dc motor", "ac motor", "induction motor", "servo motor", "servo", "stepper", "stepper motor", "bldc"),
    **_build(),
}
