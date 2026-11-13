"""Template: a spoked wheel with tyre.

The wheel lies on the Y axis, centred on it, so every repeated part is ONE
tool patterned about Y: 24 tread grooves cut round the tyre, six tapered
round spokes, five bolt holes on a bolt circle and five hex wheel nuts. A
tyre ring (rounded edges, bored out) holds a rim ring that overlaps it by
10 mm; the spokes join the rim to a chamfered hub with a boss and an axle
bore.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.features import answer, box, chamfer, cyl, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    f: list = []
    f += [cyl("tyre", 600, 180, (0, 0, 0), "+Y"), fillet("round_tyre", "tyre", 25)]
    f += [cyl("tyre_bore", 440, 184, (0, -2, 0), "+Y"), subtract("cut_tyre_bore", "tyre", ["tyre_bore"])]
    # Tread: one 8 mm groove across the top of the tyre, cut 24 times round Y.
    f += [box("tread_groove", (8, 184, 14), (-4, -2, 290))]
    f += [subtract("cut_tread", "tyre", ["tread_groove"], count=24, axis="Y", angle=15)]
    f += [cyl("rim", 460, 150, (0, 15, 0), "+Y"), cyl("rim_cavity", 420, 154, (0, 13, 0), "+Y")]
    f += [subtract("hollow_rim", "rim", ["rim_cavity"])]
    f += [cyl("hub", 120, 120, (0, 30, 0), "+Y"), chamfer("chamfer_hub", "hub", 4)]
    f += [cyl("hub_boss", 90, 40, (0, 148, 0), "+Y")]
    # One tapered round spoke along +X, from inside the hub to inside the rim.
    f += [cyl("spoke", 40, 172, (50, 90, 0), "+X", top=28)]
    f += [union("add_boss", "hub", ["hub_boss"])]
    f += [union("add_spokes", "hub", ["spoke"], count=6, axis="Y", angle=60)]
    f += [union("join_rim", "tyre", ["rim", "hub"])]
    f += [cyl("axle_bore_tool", 40, 164, (0, 27, 0), "+Y"), subtract("axle_bore", "tyre", ["axle_bore_tool"])]
    # Bolt circle r32: one hole tool at +X, cut 5 times round Y.
    f += [cyl("bolt_hole_tool", 12, 164, (32, 27, 0), "+Y")]
    f += [subtract("bolt_holes", "tyre", ["bolt_hole_tool"], count=5, axis="Y", angle=72)]
    # Hex wheel nuts on the boss face over the holes, also 5 round Y.
    f += [cyl("wheel_nut", 20, 12, (32, 186, 0), "+Y", sides=6), chamfer("chamfer_nut", "wheel_nut", 1)]
    f += [union("add_nuts", "tyre", ["wheel_nut"], count=5, axis="Y", angle=72)]
    return {
        "request": "a spoked wheel with a tyre, rim, hub, six spokes, an axle bore, bolt holes and wheel nuts",
        "answer": answer(
            plan=(
                "Axis Y, centred on x=z=0 so patterns about Y go round the wheel. Tyre d600 y0-180, "
                "edges filleted r25, bored out to d440. Tread: groove box 8x184x14 at x-4..4, "
                "z290-304 (10 mm deep at the crown), cut with count 24, angle 360/24 = 15 about Y. "
                "Rim d460 y15-165 hollowed to d420 (a ring r210-230), overlapping the tyre r220-230. "
                "Hub d120 y30-150, chamfer 4, plus boss d90 y148-188 (2 mm inside). Spoke: tapered "
                "cylinder along +X from (50,90,0), d40 -> top 28, 172 long, so x50-222: 10 mm inside "
                "the r60 hub and 12 mm into the r210-230 rim ring. Union with count 6, angle 360/6 = "
                "60 about Y into the hub -> spokes at 60..360, the last on +X; the hub reaches the rim only through the spokes, so they are fused into the hub first and the hub then joins the tyre with the rim. Axle bore d40 y27-191 (through hub "
                "and boss, 3 mm past both). Bolt circle r32: hole d12 at (32,27,0) along +Y, 164 "
                "long, cut with count 5, angle 72 (r26-38 stays inside the r45 boss and clear of "
                "the r20 bore). Hex nuts: sides 6, corners on d20 (across flats 20*cos30 = 17.3), "
                "y186-198 over each hole (2 mm into the boss), joined with count 5, angle 72, so "
                "each nut caps its hole on the outer face."
            ),
            assumptions=[
                "Six round spokes tapering from 40 to 28 mm, patterned evenly round the axle.",
                "Tyre is a ring with rounded edges and 24 straight tread grooves; no sidewall detail.",
                "Five-stud bolt circle of 64 mm with hex wheel nuts; studs are not modelled.",
                "Tyre, rim and hub are one solid, since a part is a single body.",
                "Sizes are typical for a small cart wheel, about 600 mm across.",
            ],
            name="Spoked wheel",
            description=(
                "Wheel with a treaded rounded tyre, rim ring, hub, six tapered spokes, axle bore, "
                "five bolt holes and five hex wheel nuts."
            ),
            features=f,
            summary=(
                "A 600 mm spoked wheel: a rounded tyre with 24 tread grooves, a rim ring, six tapered "
                "round spokes, a hub with a 40 mm axle bore, and five bolt holes capped by hex wheel nuts."
            ),
        ),
    }


TEMPLATE = {
    "name": "wheel",
    "keywords": ("wheel", "spoked wheel", "tyre", "tire"),
    **_build(),
}
