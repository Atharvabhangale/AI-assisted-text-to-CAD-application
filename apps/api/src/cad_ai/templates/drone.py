"""Template: a quadcopter.

A rounded central body with a top deck (vent holes, GPS puck, antenna), four
arms in a + layout along X and Y, a motor pod, shaft and two-blade propeller
on each arm tip, a battery under the body, a front camera, and landing legs on
two skids. One solid.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from cad_ai.templates._helpers import answer, box, cyl, fillet, hole, union


def _build() -> Mapping[str, Any]:
    f: List[Dict[str, Any]] = [
        box("body", (80, 80, 24), (-40, -40, 0)),
        fillet("round_body", "body", 6),
        box("deck", (60, 60, 6), (-30, -30, 22)),
        fillet("round_deck", "deck", 2),
    ]
    for n, (x, y) in enumerate(((-18, -18), (18, -18), (-18, 18), (18, 18)), 1):
        f.append(hole(f"vent_{n}", "deck", 8, (x, y, 0), "+Z"))
    tools: List[str] = ["deck"]
    arms = (
        ("px", (112, 14, 8), (38, -7, 8), (138, 0), "x"),
        ("nx", (112, 14, 8), (-150, -7, 8), (-138, 0), "x"),
        ("py", (14, 112, 8), (-7, 38, 8), (0, 138), "y"),
        ("ny", (14, 112, 8), (-7, -150, 8), (0, -138), "y"),
    )
    for tag, size, pos, (cx, cy), run in arms:
        blade = (100, 10, 2) if run == "x" else (10, 100, 2)
        bpos = (cx - blade[0] / 2, cy - blade[1] / 2, 30)
        f += [
            box(f"arm_{tag}", size, pos),
            fillet(f"round_arm_{tag}", f"arm_{tag}", 2),
            cyl(f"pod_{tag}", 30, 20, (cx, cy, 4)),
            fillet(f"round_pod_{tag}", f"pod_{tag}", 2),
            cyl(f"shaft_{tag}", 5, 10, (cx, cy, 22)),
            box(f"blade_{tag}", blade, bpos),
            cyl(f"hub_{tag}", 12, 4, (cx, cy, 31)),
        ]
        tools += [f"arm_{tag}", f"pod_{tag}", f"shaft_{tag}", f"blade_{tag}", f"hub_{tag}"]
    f += [
        box("battery", (50, 30, 14), (-25, -15, -12)),
        fillet("round_battery", "battery", 2),
        box("camera_box", (24, 16, 20), (-12, -54, 2)),
        fillet("round_camera_box", "camera_box", 2),
        cyl("lens", 14, 10, (0, -52, 12), "-Y"),
        cyl("gps", 20, 5, (0, 0, 26)),
        cyl("antenna", 3, 35, (-20, 34, 20)),
    ]
    tools += ["battery", "camera_box", "lens", "gps", "antenna"]
    for n, (x, y) in enumerate(((-28, -28), (28, -28), (-28, 28), (28, 28)), 1):
        f.append(cyl(f"leg_{n}", 8, 30, (x, y, -26)))
        tools.append(f"leg_{n}")
    for n, x in enumerate((-28, 28), 1):
        f += [box(f"skid_{n}", (10, 110, 8), (x - 5, -55, -32)), fillet(f"round_skid_{n}", f"skid_{n}", 2)]
        tools.append(f"skid_{n}")
    f.append(union("assemble", "body", tools))
    return {
        "request": "a quadcopter drone with four arms, motors, propellers, landing legs and a camera",
        "answer": answer(
            plan=(
                "Body 80x80x24 at z 0..24 centred on the origin, deck 60x60x6 at z "
                "22..28 with four d8 vent holes at +-18. Arms 112x14x8 at z 8..16 "
                "starting at |x| or |y| = 38 (2 mm inside the body) reaching 150. Pods "
                "d30 x 20 tall at z 4..24 centred 138 from the origin on each arm; "
                "shafts d5 z 22..32; blades 100x10x2 at z 30..32 (aligned with the arm); "
                "hubs d12 z 31..35. Battery z -12..2. Camera box y -54..-38 with a "
                "d14 lens along -Y to y=-62. GPS puck d20 z 26..31, antenna d3 z "
                "20..55. Legs d8 z -26..4 at (+-28,+-28) on two skids 10x110x8 at z "
                "-32..-24."
            ),
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a 300 mm motor-to-motor frame with 100 mm propellers",
                "Propellers modelled as flat two-blade boxes, not curved blades",
                "Arms restricted to the X and Y axes (a + layout)",
                "Landing gear simplified to four legs and two skids",
            ],
            name="quadcopter",
            description="Quadcopter with + layout arms, motors, propellers, camera and landing skids",
            features=f,
            summary="A quadcopter with four arms, motor pods, propellers, a front camera, battery and landing skids.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "drone",
    "keywords": ("drone", "quadcopter", "quad copter", "quadrocopter", "multirotor", "multicopter", "uav", "quadrotor"),
    **_build(),
}
