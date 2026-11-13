"""Template: a finned electric motor on feet and a flange.

A cylindrical housing along X (on the X axis) with 24 radial cooling fins (one
fin patterned around X), a square front flange with four bolt holes (one hole
patterned around X), a pilot boss, a keyed and chamfered output shaft, a
tapered rear fan cowl with eight vent holes and a stub shaft, a terminal box
with a cover and a hexagonal cable gland, and two drilled mounting feet (one
foot, its two holes as a row, then the foot repeated as a row along X). One
solid.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from cad_ai.features import answer, box, chamfer, cyl, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    f: List[Dict[str, Any]] = [
        cyl("housing", 80, 120, (0, 0, 0), "+X"),
        fillet("round_housing", "housing", 3),
        # One radial fin on top (z 37..48), turned 15, 30 ... 360 degrees about X: 24 fins.
        box("fin", (100, 3, 11), (10, -1.5, 37)),
        union("fins", "housing", ["fin"], count=24, axis="X", angle=15),
        box("flange", (10, 100, 100), (-8, -50, -50)),
        fillet("round_flange", "flange", 4, "X"),
        # One bolt hole at (y 38, z 38), turned 90/180/270/360 degrees about X: four corners.
        cyl("flange_bolt", 9, 14, (-10, 38, 38), "+X"),
        subtract("flange_holes", "flange", ["flange_bolt"], count=4, axis="X", angle=90),
        cyl("pilot_boss", 50, 10, (-16, 0, 0), "+X"),
        fillet("round_boss", "pilot_boss", 1.5),
        cyl("shaft", 10, 46, (-46, 0, 0), "+X"),
        chamfer("chamfer_shaft", "shaft", 1),
        box("keyway", (25, 4, 3), (-44, -2, 4)),
        subtract("cut_keyway", "shaft", ["keyway"]),
        cyl("fan_cowl", 78, 15, (118, 0, 0), "+X", top=70),
        fillet("round_fan_cowl", "fan_cowl", 3),
        # One shallow vent at (z 22) in the cowl's end face, turned every 45 degrees about X: eight vents.
        cyl("cowl_vent", 6, 4, (131, 0, 22), "+X"),
        subtract("cowl_vents", "fan_cowl", ["cowl_vent"], count=8, axis="X", angle=45),
        cyl("rear_stub", 8, 18, (130, 0, 0), "+X"),
        box("terminal_box", (40, 34, 36), (40, -17, 30)),
        fillet("round_terminal_box", "terminal_box", 3),
        box("terminal_cover", (36, 30, 6), (42, -15, 62)),
        fillet("round_terminal_cover", "terminal_cover", 2),
        cyl("gland_nut", 18, 6, (78, 0, 48), "+X", sides=6),
        cyl("gland_nose", 12, 10, (82, 0, 48), "+X", top=9),
        union(
            "assemble",
            "housing",
            ["flange", "pilot_boss", "shaft", "fan_cowl", "rear_stub", "terminal_box", "terminal_cover", "gland_nut", "gland_nose"],
        ),
        # One foot, built one step (60) before the first foot's place, x -40..-10.
        box("foot", (30, 110, 14), (-40, -55, -52)),
        fillet("round_foot", "foot", 4, "Z"),
        # Its holes: a row of 2 along Y, step 90, from a tool one step before (y -135) -> y -45 and 45.
        cyl("foot_bolt", 9, 18, (-25, -135, -54), "+Z"),
        subtract("foot_holes", "foot", ["foot_bolt"], count=2, axis="Y", step=90),
        # The drilled foot as a row of 2 along X, step 60 -> x 20..50 and 80..110.
        union("fit_feet", "housing", ["foot"], count=2, axis="X", step=60),
    ]
    return {
        "request": "an electric motor with a mounting flange, a shaft, cooling fins, a terminal box and mounting feet",
        "answer": answer(
            plan=(
                "Motor on the X axis (y=0, z=0) so patterns about X go round it. Housing d80 "
                "x 0..120. Fin 100x3x11 at x 10..110, z 37..48 (3 mm inside r40), count 24, "
                "angle 15 about X (360/24). Flange 10x100x100 at x -8..2; bolt tool d9 x "
                "-10..4 at (y 38, z 38), subtract count 4, angle 90 about X -> (+-38, +-38). "
                "Pilot boss d50 x -16..-6; shaft d10 x -46..0, keyway 25x4x3 at z 4..7. Fan "
                "cowl d78->70 x 118..133 with a d6 vent 2 deep at (z 22) on its end face, "
                "count 8, angle 45 about X; stub d8 x 130..148. Terminal box 40x34x36 z "
                "30..66 over the top fins, cover z 62..68, hex gland nut d18 (across flats "
                "15.6) x 78..84 at z 48 and a tapered nose d12->9 x 82..92. Foot 30x110x14 z "
                "-52..-38 (reaching r40's chord) built at x -40..-10; hole tool d9 at y -135, "
                "count 2, step 90 along Y -> y -45, 45; foot count 2, step 60 along X -> x "
                "20..50 and 80..110."
            ),
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a frame of about 80 mm diameter and 120 mm length, foot and flange mounted",
                "Cooling fins modelled as 24 straight radial fins along the frame",
                "Fan cowl vents are shallow round recesses, not a slotted grille",
                "Terminal box and cover left as plain boxes with a hexagonal cable gland",
                "Foot bolt holes are round, not slots",
            ],
            name="electric-motor",
            description="Finned electric motor with flange, keyed shaft, fan cowl, terminal box and feet",
            features=f,
            summary=(
                "An electric motor with 24 patterned cooling fins, a four-bolt flange, keyed shaft, tapered vented "
                "fan cowl, terminal box with hex gland, and two drilled feet."
            ),
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "electric_motor",
    "keywords": ("electric motor", "motor", "dc motor", "ac motor", "induction motor", "servo motor", "servo", "stepper", "stepper motor", "bldc"),
    "excludes": ("mount", "bracket", "motor mount"),
    **_build(),
}
