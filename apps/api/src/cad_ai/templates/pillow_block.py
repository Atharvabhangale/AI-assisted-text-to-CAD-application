"""Template: a pillow-block bearing housing.

The bore axis is the Y axis through the origin, and the block is centred on X
and Y, so both of its symmetries are turns about an axis through the origin:
half of the base, slots, hold-down bolts, cap screws and seal covers is
written once and turned 180 degrees about Z; the four cover bolts are one bolt
turned 90 degrees at a time about Y. A slotted base, a pedestal and a round
boss carrying the bore, a bearing ring pressed into the bore, hex-head
hold-down bolts on washers over the slots, hex cap screws on the shoulders,
seal covers on both bore ends with a bolt circle each, and a grease nipple
(hex, neck and cone) with its drilled passage. Slots are cut into the base
before anything is fused onto it.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.features import answer, box, cyl, drill, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    length, width, base_t = 200, 70, 20
    base_z = -55  # the base sits 55 below the bore axis
    top = base_z + base_t  # -35: top of the base
    f = [box("base", (length, width, base_t), (-length / 2, -width / 2, base_z)), fillet("round_base", "base", 5, "Z")]
    # One slot (x 67..93, 14 wide, round ends); its 180-degree copy is the other slot.
    f += [
        box("slot_mid", (12, 14, base_t + 4), (74, -7, base_z - 2)),
        cyl("slot_end_a", 14, base_t + 4, (74, 0, base_z - 2)),
        cyl("slot_end_b", 14, base_t + 4, (86, 0, base_z - 2)),
        subtract("cut_slots", "base", ["slot_mid", "slot_end_a", "slot_end_b"], count=2, axis="Z", angle=180),
    ]
    f += [
        box("pedestal", (130, width, 35), (-65, -width / 2, top - 10)),
        cyl("boss", 90, width, (0, -width / 2, 0), "+Y"), fillet("round_boss", "boss", 2),
        union("join_housing", "base", ["pedestal", "boss"]),
        *drill("cut_bore", "base", 60, (0, -width / 2 - 2, 0), width + 4, "+Y"),
        cyl("bearing", 62, 40, (0, -20, 0), "+Y"),
        *drill("cut_bearing", "bearing", 40, (0, -22, 0), 44, "+Y"),
        union("press_bearing", "base", ["bearing"]),
    ]
    # Seal cover on the -Y end of the bore, its 180-degree copy on the +Y end.
    f += [
        cyl("cover", 72, 6, (0, -width / 2 + 1, 0), "-Y"),
        *drill("cut_cover", "cover", 46, (0, -width / 2 + 3, 0), 10, "-Y"),
    ]
    # Fasteners for the +X half; the 180-degree copies are the -X half.
    half = [
        cyl("washer", 28, 2.5, (80, 0, top - 0.5)),
        cyl("bolt_head", 22, 9, (80, 0, top + 1.5), sides=6),
        cyl("cap_screw_a", 14, 8, (56, 20, -11), sides=6),
        cyl("cap_screw_b", 14, 8, (56, -20, -11), sides=6),
    ]
    f += half + [union("join_half", "base", ["cover"] + [h["id"] for h in half], count=2, axis="Z", angle=180)]
    # Cover bolts: one per cover at 45 degrees on a 30 mm radius, four at 90 degrees about Y.
    r45 = 21.21
    f += [
        cyl("cover_bolt_front", 8, 5, (r45, -width / 2 - 4.5, r45), "-Y", sides=6),
        cyl("cover_bolt_back", 8, 5, (r45, width / 2 + 4.5, r45), "+Y", sides=6),
        union("join_cover_bolts", "base", ["cover_bolt_front", "cover_bolt_back"], count=4, axis="Y", angle=90),
    ]
    # Grease nipple on the crown of the boss: hex, neck, cone head, and its passage.
    f += [
        cyl("nipple_hex", 12, 6, (0, 0, 44), sides=6),
        cyl("nipple_neck", 6, 5, (0, 0, 49)),
        cyl("nipple_head", 9, 6, (0, 0, 53), top=4),
        union("join_nipple", "base", ["nipple_hex", "nipple_neck", "nipple_head"]),
        *drill("cut_grease", "base", 3, (0, 0, 15), 46),
    ]
    plan = (
        "Put the bore axis on the Y axis through the origin and centre the block on X and Y, so every "
        "symmetry is a turn about an axis through the origin. Base 200x70x20 at x -100..100, y -35..35, "
        "z -55..-35, Z corners r5. Slot: a 12x14 box at x 74-86 with d14 round ends at x 74 and 86 "
        "(x 67-93, centred y=0), z -57..-33, cut with count 2, axis Z, angle 180 -> the copies at 180 "
        "(x -93..-67) and 360 (its own place) are the two slots. Pedestal 130x70x35 at x -65..65, "
        "z -45..-10. Boss d90 along +Y from y -35, axis on the Y axis (z -45..45), edges r2. Bore d60 "
        "drilled along +Y from y -37, length 74. Bearing ring d62 (1 mm into the bore wall) y -20..20, "
        "its d40 bore drilled before it is pressed in. Seal cover d72 along -Y from y -34 (1 mm into the "
        "boss face) to y -40, d46 centre hole. Hold-down bolt: washer d28 z -35.5..-33 and hex head d22 "
        "(19.1 across flats) z -33.5..-24.5 at (80,0) over the slot; hex cap screws d14 h8 at (56,+-20), "
        "z -11..-3 on the shoulder. Cover, washer, bolt head and both cap screws are fused with count 2, "
        "axis Z, angle 180: the copy at 180 puts a cover on the +Y end (y 34..40), a bolt over the -X "
        "slot and cap screws at (-56,-+20); the copy at 360 is each one's own place. Cover bolts: hex d8 "
        "h5 at radius 30 and 45 degrees, (21.21, z 21.21), one on the front cover face (y -39.5 along -Y) "
        "and one on the back (y 39.5 along +Y), fused with count 4, axis Y, angle 90 -> 45, 135, 225, 315 "
        "degrees, four per cover. Grease nipple at the crown (boss top z 45): hex d12 z 44-50, neck d6 "
        "z 49-54, head d9 tapering to d4 z 53-59; passage d3 drilled up from z 15 (inside the bearing "
        "bore) to z 61, through the bearing ring, the boss and the nipple."
    )
    return {
        "request": "a pillow block bearing housing on a base with two bolt slots, hex bolts, a bore with a bearing, seal covers and a grease nipple",
        "answer": answer(
            plan=plan,
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a 40 mm shaft bearing in a 60 mm bore, 200 x 70 mm base",
                "Bearing shown as a plain ring fused into the bore; no balls, races or seals",
                "Housing is one piece (no split cap); bolts are hex heads on washers with no shanks or threads",
                "Seal covers are plain rings with four hex bolts each; the grease nipple is a hex, a neck and a cone",
            ],
            name="pillow-block",
            description="Pillow-block bearing housing with slotted base, hex bolts, seal covers, bearing ring and grease nipple",
            features=f,
            summary="A pillow block: slotted base with hex hold-down bolts, pedestal and boss, 60 mm bore holding a 40 mm bearing ring, hex cap screws, bolted seal covers and a grease nipple.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "pillow_block",
    "keywords": ("pillow block", "pillow-block", "bearing housing", "plummer block", "bearing block", "bearing mount"),
    **_build(),
}
