"""Template: a model rocket.

A body tube with a coupler band, a two-stage nose cone (a frustum shoulder
and a pointed cone), a tapered boat tail, a flared engine nozzle with a
conical cavity, four swept fins and a launch lug. The rocket stands on the Z
axis, centred on it, so the fins are ONE fin patterned four times around Z.

The fin's swept leading edge is a cut by a box turned 40 degrees about Y with
a single-copy pattern (count 1): the box is placed at the point that the turn
carries onto the leading edge. The launch lug is bored, then turned 45 degrees
about Z the same way so it sits between two fins. Every attached piece starts
inside the face it joins.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.features import answer, box, chamfer, cyl, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    f: list = []
    f += [cyl("body", 60, 400, (0, 0, 60)), fillet("round_body", "body", 2)]
    f += [cyl("band", 64, 10, (0, 0, 300)), chamfer("chamfer_band", "band", 2)]
    # Nose: a frustum shoulder d60 -> d48, then a pointed cone d48 -> 0.
    f += [cyl("nose_shoulder", 60, 50, (0, 0, 458), top=48)]
    f += [cyl("nose_cone", 48, 90, (0, 0, 506), top=0)]
    # Tail: a boat tail narrowing downwards, then a nozzle flaring downwards.
    f += [cyl("boat_tail", 60, 14, (0, 0, 62), "-Z", top=46)]
    f += [cyl("nozzle", 28, 24, (0, 0, 50), "-Z", top=40)]
    f += [cyl("nozzle_cavity", 38, 20, (0, 0, 24), top=14)]
    # One fin on +X: a 62 x 3 x 90 plate, its leading edge swept by a cutter
    # box turned 40 degrees about Y (count 1). The box's lower corner sits at
    # (30, 150) turned by -40 degrees, so the turn lays its bottom face on the
    # line from (30, 150) down at 40 degrees.
    f += [box("fin", (62, 3, 90), (28, -1.5, 60))]
    f += [box("fin_sweep", (140, 6, 100), (-93.437, -3, 134.19))]
    f += [subtract("sweep_fin", "fin", ["fin_sweep"], count=1, axis="Y", angle=40)]
    f += [fillet("round_fin", "fin", 1)]
    # Launch lug on +X, bored, later turned 45 degrees about Z.
    f += [cyl("lug", 8, 30, (33, 0, 255)), cyl("lug_bore", 4, 34, (33, 0, 253))]
    f += [subtract("bore_lug", "lug", ["lug_bore"])]
    f += [union("add_nose", "body", ["band", "nose_shoulder", "nose_cone"])]
    f += [union("add_tail", "body", ["boat_tail", "nozzle"])]
    f += [subtract("hollow_nozzle", "body", ["nozzle_cavity"])]
    f += [union("add_fins", "body", ["fin"], count=4, axis="Z", angle=90)]
    f += [union("add_lug", "body", ["lug"], count=1, axis="Z", angle=45)]
    return {
        "request": "a model rocket with a nose cone, four swept fins, an exhaust nozzle and a launch lug",
        "answer": answer(
            plan=(
                "Axis Z, centred on x=y=0 so patterns about Z go round the rocket. Body tube d60 "
                "z60-460, coupler band d64 z300-310. Nose: shoulder frustum d60 -> top 48, z458-508 "
                "(2 mm inside the tube), then pointed cone d48 -> top 0, z506-596. Tail: boat tail "
                "from z62 down along -Z, d60 -> top 46, 14 high (z48-62); nozzle from z50 down, d28 "
                "-> top 40, 24 high (z26-50); conical cavity from z24 up, d38 -> top 14, 20 high, so "
                "the wall is 2.2 mm at the exit (outer d40 vs cavity 35.6 at z26). Fin on +X: box "
                "62x3x90 at x28-90 (2 mm inside the r30 tube), z60-150. Sweep: cutter box 140x6x100 "
                "turned 40 deg about Y with count 1; its corner is placed at (30,150) turned by -40 "
                "deg = (30cos40-150sin40, 30sin40+150cos40) = (-73.437, 134.19), so after the turn "
                "its bottom face runs from (30,150) down at 40 deg, leaving the tip chord about "
                "z60-84 at x=90. Fins: union the one fin with count 4, angle 360/4 = 90 about Z -> "
                "fins at 90, 180, 270 and 360 = its own place (+X). Lug d8 z255-285 at x=33 (1 mm "
                "inside the tube), bore d4 cut before joining so it cannot reach the tube; joined "
                "with count 1, angle 45 about Z, so it sits between the +X and +Y fins."
            ),
            assumptions=[
                "Nose cone is a frustum shoulder plus a pointed cone, approximating an ogive.",
                "Four fins at 90 degrees, 3 mm thick, with a 40 degree swept leading edge and a straight trailing edge.",
                "Body tube is solid, not a hollow tube; only the nozzle is hollow.",
                "Sizes are typical for a 60 mm hobby rocket, about 57 cm tall.",
            ],
            name="Model rocket",
            description="Model rocket: body tube, cone nose, boat tail, flared hollow nozzle, four swept fins and a launch lug.",
            features=f,
            summary=(
                "A 60 mm model rocket with a pointed two-stage nose cone, a tapered boat tail and "
                "flared hollow nozzle, four swept fins patterned around the body, and a bored "
                "launch lug between two fins."
            ),
        ),
    }


TEMPLATE = {
    "name": "rocket",
    "keywords": ("rocket", "model rocket", "missile"),
    **_build(),
}
