"""Template: a flange shaft coupling.

The shaft axis is X, centred on it, so every repeated part is ONE tool
patterned about X. Two hubs (d70) carry bores of different sizes with
keyways and a pair of set-screw holes each; two d140 flanges are drilled with
a ring of six bolt holes, and one complete bolt -- hex head, shank and hex
nut, with the tip standing out of the nut -- is built once and patterned
round the same bolt circle.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.features import answer, box, chamfer, cyl, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    f: list = []
    f += [cyl("hub_a", 70, 50, (0, 0, 0), "+X"), fillet("round_hub_a", "hub_a", 2)]
    f += [cyl("flange_a", 140, 20, (48, 0, 0), "+X"), chamfer("chamfer_flange_a", "flange_a", 3)]
    f += [cyl("flange_b", 140, 20, (67, 0, 0), "+X"), chamfer("chamfer_flange_b", "flange_b", 3)]
    f += [cyl("hub_b", 70, 50, (85, 0, 0), "+X"), fillet("round_hub_b", "hub_b", 2)]
    f += [union("join_coupling", "hub_a", ["flange_a", "flange_b", "hub_b"])]
    f += [
        cyl("bore_a", 24, 71, (-2, 0, 0), "+X"),
        cyl("bore_b", 28, 71, (67, 0, 0), "+X"),
        box("keyway_a", (50, 8, 6), (-2, -4, 10)),
        box("keyway_b", (50, 8, 6), (88, -4, 12)),
    ]
    f += [subtract("cut_bores", "hub_a", ["bore_a", "bore_b", "keyway_a", "keyway_b"])]
    # Set screws: one radial tool per hub on +Y, cut twice round X (180 deg -> -Y, 360 -> +Y).
    f += [cyl("set_screw_a", 8, 30, (25, 8, 0), "+Y"), cyl("set_screw_b", 8, 30, (111, 8, 0), "+Y")]
    f += [subtract("cut_set_screws", "hub_a", ["set_screw_a", "set_screw_b"], count=2, axis="X", angle=180)]
    # Bolt circle r50: one clearance hole at +Z through both flanges, cut 6 times round X.
    f += [cyl("bolt_hole_tool", 10, 43, (46, 0, 50), "+X")]
    f += [subtract("bolt_holes", "hub_a", ["bolt_hole_tool"], count=6, axis="X", angle=60)]
    # One bolt at +Z: hex head, d8 shank in the d10 hole, hex nut; then patterned.
    f += [cyl("bolt_head", 17, 7, (42, 0, 50), "+X", sides=6), chamfer("chamfer_head", "bolt_head", 1)]
    f += [cyl("bolt_shank", 8, 53, (45, 0, 50), "+X"), chamfer("chamfer_shank", "bolt_shank", 0.8)]
    f += [cyl("nut", 17, 8, (86, 0, 50), "+X", sides=6), chamfer("chamfer_nut", "nut", 1)]
    f += [union("assemble_bolt", "bolt_head", ["bolt_shank", "nut"])]
    f += [union("add_bolts", "hub_a", ["bolt_head"], count=6, axis="X", angle=60)]
    return {
        "request": "a flange shaft coupling with two hubs, bores and keyways, bolted flanges and set screws",
        "answer": answer(
            plan=(
                "Shaft axis X, centred on y=z=0 so patterns about X go round the shaft. Hub A d70 "
                "x0-50, flange A d140 x48-68, flange B d140 x67-87 (1 mm overlap), hub B d70 x85-135. "
                "Bore A d24 x-2..69, bore B d28 x67..138 (overlap, one bore). Keyways 8 wide cut up "
                "from the bore: A z10-16 x-2..48, B z12-18 x88..138. Set screws: d8 tools from the "
                "bore (y=8) out along +Y to y38 at x=25 and x=111, cut with count 2, angle 180 about "
                "X -> copies on -Y (180) and back on +Y (360). Bolt circle r50, six bolts: the tool "
                "is at +Z (0,50); count 6, angle 360/6 = 60 about X puts copies at 90+60k deg, i.e. "
                "30, 90, 150, 210, 270, 330. Clearance hole d10 from x46, 43 long (x46-89, 2 mm past "
                "both flange faces). Bolt built once at +Z: hex head sides 6, corners on d17 (across "
                "flats 17*cos30 = 14.7), x42-49 (1 mm into flange A); shank d8 x45-98, clear of the "
                "d10 hole; hex nut d17 x86-94 (1 mm into flange B), so the shank tip stands 4 mm "
                "out of the nut. Head, shank and nut fused into one bolt, then joined with count 6, "
                "angle 60 about X; it reaches the flanges only through head and nut."
            ),
            assumptions=[
                "Six M8 bolts in 10 mm clearance holes on a 100 mm bolt circle, with hex heads and hex nuts.",
                "The two flanges overlap by 1 mm so they join as one solid; bolts are part of the same solid.",
                "Hub bores are 24 mm and 28 mm with 8 mm keyways, typical for a mid-size coupling.",
                "Two set-screw holes per hub, opposite each other on the Y axis; threads are not modelled.",
            ],
            name="Flange shaft coupling",
            description=(
                "Flange coupling: two bored and keyed hubs with set-screw holes, two flanges joined "
                "by six hex bolts and nuts on a bolt circle."
            ),
            features=f,
            summary=(
                "A flange shaft coupling with a 24 mm and a 28 mm keyed bore, four set-screw holes, "
                "and six hex-head bolts with hex nuts patterned round a 100 mm bolt circle."
            ),
        ),
    }


TEMPLATE = {
    "name": "shaft_coupling",
    "keywords": ("shaft coupling", "flange coupling", "coupling", "coupler"),
    **_build(),
}
