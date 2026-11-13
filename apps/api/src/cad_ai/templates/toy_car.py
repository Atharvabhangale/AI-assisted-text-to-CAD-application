"""Template: a toy sports car.

A rounded lower body with wheel arches, a cabin whose windscreen and rear
window are raked by cutting it with boxes turned about Y, shallow side
windows, front and rear bumpers (the front with a row of grille slots),
headlights, tail lights and spoiler posts as rows of two, a rear wing and a
stripe. One five-spoke wheel (dished tyre, hub, hexagonal nut; one spoke
patterned five times) is built on the Y axis, paired across the axle by a
180-degree turn, moved forward by a step, and the axle set turned 180 degrees
to make the rear axle. The axles are at z 0; the tyres stand on z -24. One
solid.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from cad_ai.features import answer, box, cyl, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    f: List[Dict[str, Any]] = [
        # Wheel, built on the Y axis (x 0, z 0) so its spokes pattern about Y.
        cyl("tyre", 48, 12, (0, 30, 0), "+Y"),
        fillet("round_tyre", "tyre", 3),
        cyl("dish", 34, 6, (0, 38, 0), "+Y"),
        subtract("cut_dish", "tyre", ["dish"]),
        cyl("hub", 12, 4, (0, 36, 0), "+Y"),
        union("fit_hub", "tyre", ["hub"]),
        box("spoke", (3, 4, 13), (-1.5, 36, 5)),
        union("spokes", "tyre", ["spoke"], count=5, axis="Y", angle=72),
        cyl("wheel_nut", 8, 3, (0, 39, 0), "+Y", sides=6),
        union("fit_nut", "tyre", ["wheel_nut"]),
        # Axle y -42..42; the wheel turned 180 about Z lands on the other end, facing out.
        cyl("axle", 8, 84, (0, -42, 0), "+Y"),
        union("wheel_pair", "axle", ["tyre"], count=2, angle=180),
        # Axle carrier at x 42..58; the axle set moved one step of 50 along X onto it.
        box("axle_block", (16, 50, 10), (42, -25, -5)),
        union("place_axle", "axle_block", ["axle"], count=1, axis="X", step=50),
        # Body and wheel arches.
        box("body", (180, 70, 26), (-90, -35, -12)),
        fillet("round_body", "body", 8, "Z"),
        cyl("arch_l", 54, 8, (50, 29, 0), "+Y"),
        cyl("arch_r", 54, 8, (50, -29, 0), "-Y"),
        subtract("cut_arches", "body", ["arch_l", "arch_r"], count=2, angle=180),
        # Front axle set turned 180 about Z: the rear axle at x -50, and itself at x 50.
        union("fit_axles", "body", ["axle_block"], count=2, angle=180),
        # Cabin with a raked windscreen and rear window.
        box("cabin", (80, 60, 20), (-40, -30, 12)),
        box("windscreen_cut", (60, 80, 140), (39.5, -40, -80)),
        subtract("rake_front", "cabin", ["windscreen_cut"], count=1, axis="Y", angle=-50),
        box("rear_window_cut", (60, 80, 140), (-99.33, -40, -80)),
        subtract("rake_rear", "cabin", ["rear_window_cut"], count=1, axis="Y", angle=55),
        fillet("round_cabin", "cabin", 4),
        box("window_l1", (20, 6, 9), (-22, 27, 19)),
        box("window_l2", (20, 6, 9), (2, 27, 19)),
        box("window_r1", (20, 6, 9), (-22, -33, 19)),
        box("window_r2", (20, 6, 9), (2, -33, 19)),
        subtract("cut_windows", "cabin", ["window_l1", "window_l2", "window_r1", "window_r2"]),
        box("front_bumper", (10, 74, 10), (86, -37, -14)),
        fillet("round_front_bumper", "front_bumper", 3),
        box("grille_slot", (6, 40, 2), (93, -20, -16)),
        subtract("cut_grille", "front_bumper", ["grille_slot"], count=2, axis="Z", step=4),
        box("rear_bumper", (10, 74, 10), (-96, -37, -14)),
        fillet("round_rear_bumper", "rear_bumper", 3),
        # Pairs as rows of 2 along Y, step 44, each tool placed one step before the right-hand one.
        cyl("headlight", 12, 6, (88, -66, 4), "+X"),
        box("taillight", (4, 14, 8), (-92, -73, 0)),
        box("spoiler_post", (6, 6, 18), (-80, -69, 12)),
        union("fit_pairs", "body", ["headlight", "taillight", "spoiler_post"], count=2, axis="Y", step=44),
        box("wing", (24, 64, 4), (-86, -32, 28)),
        fillet("round_wing", "wing", 1.5),
        box("stripe", (180, 10, 4), (-90, -5, 12)),
        union("assemble", "body", ["cabin", "front_bumper", "rear_bumper", "wing", "stripe"]),
    ]
    return {
        "request": "a toy car with four wheels, a cabin with windows, bumpers, headlights and a rear spoiler",
        "answer": answer(
            plan=(
                "Axles at z 0, tyres down to z -24. Wheel on the Y axis: tyre d48 y 30..42, "
                "dish d34 cut 4 deep (y 38..42), hub d12 y 36..40, spoke 3x4x13 at z 5..18 "
                "count 5, angle 72 about Y (360/5), hex nut d8 y 39..42. Axle d8 y -42..42; "
                "wheel count 2, angle 180 about Z -> a mirror-facing wheel at y -30..-42. "
                "Carrier 16x50x10 at x 42..58; axle set count 1, step 50 along X -> x 50. "
                "Body 180x70x26 x -90..90, z -12..14, Z corners r8; arches d54 y 29..37 at x 50 "
                "both sides, count 2, angle 180 about Z -> all four. Axle set count 2, angle "
                "180 about Z -> x -50 and 50. Cabin 80x60x20 x -40..40, z 12..32; windscreen: "
                "box from x 39.5 (= 40 cos50 + 18 sin50) turned -50 about Y, cutting the plane "
                "through (x 40, z 18) and (x 23.3, z 32); rear: box ending at x -39.33 (= 40 "
                "cos55 + 20 sin55) turned +55 about Y, plane through (x -40, z 20) and (x "
                "-22.9, z 32). Side windows 20x9 at x -22..-2 and 2..22, z 19..28, 3 deep. "
                "Bumpers 10x74x10 z -14..-4 at x 86..96 and -96..-86; grille slot 6x40x2 from "
                "z -16, count 2, step 4 along Z -> z -12, -8. Headlights d12 x 88..94 at z 4, "
                "tail lights 4x14x8 z 0..8, spoiler posts 6x6x18 z 12..30: count 2, step 44 "
                "along Y from tools at y -66/-73/-69 -> y -22, 22 / -29, 15 / -25, 19. Wing "
                "24x64x4 z 28..32, stripe 180x10x4 z 12..16."
            ),
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a 180 mm long toy-scale car with 48 mm five-spoke wheels",
                "Windows modelled as shallow cuts into the cabin, not glass",
                "Windscreen raked at 40 degrees and rear window at 35 degrees from vertical",
                "Wheels are tyres without tread, joined to the body through the axles",
            ],
            name="toy-car",
            description="Toy sports car with raked cabin, windows, bumpers, lights, spoiler and four spoked wheels",
            features=f,
            summary=(
                "A toy car with a rounded body and wheel arches, a cabin with raked windscreen and side windows, "
                "bumpers, lights, a spoiler, and four five-spoke wheels on two axles."
            ),
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "toy_car",
    "keywords": ("toy car", "race car", "racing car", "sports car", "sedan", "coupe", "hatchback", "toy vehicle", "model car"),
    **_build(),
}
