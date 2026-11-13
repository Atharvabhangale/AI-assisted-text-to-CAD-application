"""Template: a toy sports car.

A rounded lower body and cabin with shallow window cuts, front and rear
bumpers (the front with grille slots), headlights and tail lights, a rear
spoiler, a stripe, and four wheels with hubs on two axles along Y. One solid.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from cad_ai.templates._helpers import answer, box, cyl, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    f: List[Dict[str, Any]] = [
        box("body", (170, 70, 22), (-85, -35, 12)),
        fillet("round_body", "body", 8, "Z"),
        box("cabin", (80, 60, 22), (-35, -30, 32)),
        fillet("round_cabin", "cabin", 6),
        box("window_left", (28, 6, 12), (-30, 26, 38)),
        box("window_left_2", (28, 6, 12), (4, 26, 38)),
        box("window_right", (28, 6, 12), (-30, -32, 38)),
        box("window_right_2", (28, 6, 12), (4, -32, 38)),
        box("windscreen", (6, 48, 10), (41, -24, 40)),
        box("rear_window", (6, 48, 10), (-37, -24, 40)),
        subtract("cut_windows", "cabin", ["window_left", "window_left_2", "window_right", "window_right_2", "windscreen", "rear_window"]),
        box("front_bumper", (12, 76, 12), (83, -38, 12)),
        fillet("round_front_bumper", "front_bumper", 3),
        box("grille_a", (6, 40, 2), (91, -20, 14)),
        box("grille_b", (6, 40, 2), (91, -20, 18)),
        subtract("cut_grille", "front_bumper", ["grille_a", "grille_b"]),
        box("rear_bumper", (12, 76, 12), (-95, -38, 12)),
        fillet("round_rear_bumper", "rear_bumper", 3),
        cyl("headlight_l", 12, 6, (83, 22, 28), "+X"),
        cyl("headlight_r", 12, 6, (83, -22, 28), "+X"),
        box("taillight_l", (4, 14, 8), (-87, 15, 26)),
        box("taillight_r", (4, 14, 8), (-87, -29, 26)),
        box("post_l", (6, 6, 18), (-73, 19, 32)),
        box("post_r", (6, 6, 18), (-73, -25, 32)),
        box("wing", (24, 64, 4), (-82, -32, 48)),
        fillet("round_wing", "wing", 1.5),
        box("stripe", (170, 10, 4), (-85, -5, 32)),
    ]
    tools = [
        "cabin", "front_bumper", "rear_bumper", "headlight_l", "headlight_r", "taillight_l",
        "taillight_r", "post_l", "post_r", "wing", "stripe",
    ]
    for tag, x in (("front", 52), ("rear", -52)):
        f += [
            cyl(f"axle_{tag}", 8, 90, (x, -45, 28), "+Y"),
            cyl(f"wheel_{tag}_l", 56, 14, (x, 33, 28), "+Y"),
            fillet(f"round_wheel_{tag}_l", f"wheel_{tag}_l", 4),
            cyl(f"wheel_{tag}_r", 56, 14, (x, -33, 28), "-Y"),
            fillet(f"round_wheel_{tag}_r", f"wheel_{tag}_r", 4),
            cyl(f"hub_{tag}_l", 28, 4, (x, 45, 28), "+Y"),
            cyl(f"hub_{tag}_r", 28, 4, (x, -45, 28), "-Y"),
            cyl(f"cap_{tag}_l", 10, 3, (x, 48, 28), "+Y"),
            cyl(f"cap_{tag}_r", 10, 3, (x, -48, 28), "-Y"),
        ]
        tools += [
            f"axle_{tag}", f"wheel_{tag}_l", f"wheel_{tag}_r", f"hub_{tag}_l", f"hub_{tag}_r",
            f"cap_{tag}_l", f"cap_{tag}_r",
        ]
    f.append(union("assemble", "body", tools))
    return {
        "request": "a toy car with four wheels, a cabin with windows, bumpers, headlights and a rear spoiler",
        "answer": answer(
            plan=(
                "Body 170x70x22 at x -85..85, y +-35, z 12..34, corners rounded about Z. "
                "Cabin 80x60x22 at x -35..45, z 32..54, with 4 shallow side windows "
                "(y +-26..32, z 38..50), windscreen and rear window cut 4 deep. Bumpers "
                "12x76x12 at x 83..95 and -95..-83, z 12..24; front has two grille "
                "slots. Headlights d12 along X from x=83 at y=+-22,z=28; tail lights "
                "4x14x8 at x -87..-83. Spoiler posts z 32..50 and wing 24x64x4 at z "
                "48..52. Wheels d56 x 14 wide centred z=28 at x=+-52, y 33..47 and "
                "-33..-47; axles d8 along Y through y -45..45; hubs d28 and caps d10 "
                "stacked outwards."
            ),
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a 170 mm long toy-scale car with 56 mm wheels",
                "Windows modelled as shallow cuts into the cabin, not glass",
                "Cabin is a rounded box; no sloped windscreen (V1 has no angled cuts)",
                "Wheels are plain discs with a hub and cap, no tread",
            ],
            name="toy-car",
            description="Toy sports car with cabin, windows, bumpers, lights, spoiler and four wheels",
            features=f,
            summary="A toy car with a rounded body, windowed cabin, bumpers, lights, spoiler and four wheels on axles.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "toy_car",
    "keywords": ("toy car", "race car", "racing car", "sports car", "sedan", "coupe", "hatchback", "toy vehicle", "model car"),
    **_build(),
}
