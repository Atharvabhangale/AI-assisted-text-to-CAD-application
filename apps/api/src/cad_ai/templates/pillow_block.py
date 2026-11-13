"""Template: a pillow-block bearing housing.

A slotted base, a pedestal with shoulders and a round boss carrying the bore,
two cap-screw heads per shoulder, a bearing ring pressed into the bore and a
grease nipple with its drilled passage. Slots are cut into the base before the
housing is fused on, so nothing else can be cut by them.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.templates._helpers import answer, box, cyl, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    length, width, base_t = 200, 70, 20
    cx, cy_mid, cz = 100, 35, 55
    f = [box("base", (length, width, base_t), (0, 0, 0)), fillet("round_base", "base", 5, "Z")]
    slot_tools = []
    for side, x in (("l", 26), ("r", 162)):
        slot_tools += [
            box(f"slot_{side}", (12, 14, 24), (x, 28, -2)),
            cyl(f"slot_{side}_a", 14, 24, (x, 35, -2)),
            cyl(f"slot_{side}_b", 14, 24, (x + 12, 35, -2)),
        ]
    f += slot_tools + [subtract("cut_slots", "base", [t["id"] for t in slot_tools])]
    f += [
        box("pedestal", (110, width, 35), (45, 0, 10)),
        cyl("boss", 90, width, (cx, 0, cz), "+Y"), fillet("round_boss", "boss", 2),
    ]
    heads = []
    for x in (52, 148):
        for y in (14, 56):
            heads.append(cyl(f"cap_screw_{x}_{y}", 12, 10, (x, y, 43)))
    f += heads + [union("join_housing", "base", ["pedestal", "boss"] + [h["id"] for h in heads])]
    f += [
        cyl("bore_tool", 60, 74, (cx, -2, cz), "+Y"),
        subtract("cut_bore", "base", ["bore_tool"]),
        cyl("bearing", 62, 40, (cx, 15, cz), "+Y"),
        cyl("shaft_tool", 40, 50, (cx, 10, cz), "+Y"),
        subtract("cut_bearing", "bearing", ["shaft_tool"]),
        cyl("nipple", 10, 14, (cx, cy_mid, 92)),
        union("add_bearing", "base", ["bearing", "nipple"]),
        cyl("grease_tool", 4, 32, (cx, cy_mid, 80)),
        subtract("cut_grease", "base", ["grease_tool"]),
    ]
    plan = (
        "Base 200x70x20 at z 0-20, corners r5, two 14 mm slots 12 long at x 26-38 and 162-174 "
        "centred y=35, cut before the housing joins. Pedestal 110x70x35 at x 45-155, z 10-45. "
        "Boss d90 along Y, y 0-70, axis x=100 z=55 (z 10-100), rims r2. Four cap-screw heads "
        "d12 h10 at x 52/148, y 14/56, z 43-53. Bore d60 along Y through the boss. Bearing "
        "ring outer d62 (1 mm into the bore wall), inner d40, y 15-55. Grease nipple d10 at "
        "(100,35) z 92-106, passage d4 from z 80 up through the boss into the bore."
    )
    return {
        "request": "a pillow block bearing housing on a base with two bolt slots, a bore with a bearing and a grease nipple",
        "answer": answer(
            plan=plan,
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a 40 mm shaft bearing in a 60 mm bore, 200 x 70 mm base",
                "Bearing shown as a plain ring fused into the bore; no balls, races or seals",
                "Housing is one piece (no split cap); cap-screw heads are plain cylinders",
                "Bolt openings are straight slots; the grease nipple is a plain stub",
            ],
            name="pillow-block",
            description="Pillow-block bearing housing with slotted base, bearing ring and grease nipple",
            features=f,
            summary="A pillow block: slotted base, pedestal and boss, 60 mm bore holding a 40 mm bearing ring, cap screws and a grease nipple.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "pillow_block",
    "keywords": ("pillow block", "pillow-block", "bearing housing", "plummer block", "bearing block", "bearing mount"),
    **_build(),
}
