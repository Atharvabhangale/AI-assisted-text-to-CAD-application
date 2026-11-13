"""Template: an X-frame quadcopter.

A rounded square body with a top deck (four vent holes, one drilled hole
patterned four times), GPS puck and tapered whip antenna, a battery under the
body and a front camera. One arm is built once along +X -- arm, motor pod,
tapered motor bell, shaft, prop hub, a two-blade propeller, a hexagonal prop
nut and a tapered landing leg with a foot -- then turned 45 degrees and
patterned four times around Z, so the arms leave the body's corners in an X.
One solid.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from cad_ai.features import answer, box, cyl, drill, fillet, union


def _build() -> Mapping[str, Any]:
    r = 150  # motor centre distance from the Z axis
    f: List[Dict[str, Any]] = [
        box("body", (80, 80, 24), (-40, -40, 0)),
        fillet("round_body", "body", 6),
        box("deck", (60, 60, 6), (-30, -30, 22)),
        fillet("round_deck", "deck", 2),
        # One vent at (18, 18), turned 90/180/270/360 degrees about Z: four vents.
        *drill("vents", "deck", 8, (18, 18, 20), 10, "+Z", count=4, angle=90),
        # The arm, built once along +X (centreline y=0), every piece overlapping the arm or the pod.
        cyl("arm_root", 30, 12, (0, 0, 6)),
        box("arm", (140, 14, 8), (10, -7, 8)),
        fillet("round_arm", "arm", 2),
        cyl("pod", 30, 14, (r, 0, 4)),
        fillet("round_pod", "pod", 2),
        cyl("bell", 28, 10, (r, 0, 17), top=24),
        cyl("shaft", 5, 8, (r, 0, 26)),
        cyl("prop_hub", 12, 4, (r, 0, 30)),
        box("blade", (10, 127, 2), (r - 5, -63.5, 31)),
        cyl("prop_nut", 8, 4, (r, 0, 33), sides=6),
        cyl("leg", 10, 46, (r, 0, 6), "-Z", top=6),
        cyl("foot", 16, 4, (r, 0, -42)),
        fillet("round_foot", "foot", 1.5),
        union("arm_parts", "arm", ["pod", "bell", "shaft", "prop_hub", "blade", "prop_nut", "leg", "foot"]),
        # Turn the finished arm once by 45 degrees about Z, onto the hidden root disc.
        union("turn_arm", "arm_root", ["arm"], count=1, angle=45),
        # Root + diagonal arm, four times around Z: arms at 135, 225, 315 and 45 degrees.
        union("arms", "body", ["arm_root"], count=4, angle=90),
        box("battery", (50, 30, 14), (-25, -15, -12)),
        fillet("round_battery", "battery", 2),
        box("camera_box", (24, 16, 20), (-12, -54, 2)),
        fillet("round_camera_box", "camera_box", 2),
        cyl("lens", 14, 10, (0, -52, 12), "-Y"),
        cyl("gps", 20, 5, (0, 0, 26)),
        fillet("round_gps", "gps", 1.5),
        cyl("antenna", 3, 35, (-20, 34, 20), top=1.5),
        union("assemble", "body", ["deck", "battery", "camera_box", "lens", "gps", "antenna"]),
    ]
    return {
        "request": "a quadcopter drone with four arms, motors, propellers, landing legs and a camera",
        "answer": answer(
            plan=(
                "Centre on the Z axis. Body 80x80x24 at z 0..24, deck 60x60x6 at z 22..28; "
                "vents: one d8 hole at (18,18) drilled z 20..30, count 4, angle 90 about Z "
                "(360/4) -> (+-18,+-18). Arm built once along +X: hidden root disc d30 z "
                "6..18 at the origin (inside the body); arm 140x14x8 x 10..150, z 8..16; "
                "pod d30 z 4..18 at x=150; bell d28->24 z 17..27; shaft d5 z 26..34; hub "
                "d12 z 30..34; blade 10x127x2 z 31..33 across the arm (5-inch prop); hex nut "
                "d8 z 33..37; leg d10->6 from z 6 down to z -40; foot d16 z -42..-38. Arm "
                "pieces fused into the arm, the arm turned once (count 1, angle 45) and fused "
                "to the root: motor at (106,106), 150 from the axis. Root + arm patterned "
                "count 4, angle 90 about Z -> arms at 135, 225, 315, 45 degrees, leaving the "
                "body's corners; adjacent motors 212 apart > 127 prop. Battery z -12..2, "
                "camera box y -54..-38 on the front (-Y) face between two arms, lens d14 "
                "along -Y to y -62, GPS d20 z 26..31, antenna d3->1.5 z 20..55."
            ),
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose an X frame: 300 mm motor to motor across the diagonal, 127 mm (5-inch) propellers",
                "Propellers modelled as flat two-blade bars, not twisted blades",
                "Landing gear is a tapered leg under each motor instead of skids",
                "The arms meet on a hidden root disc inside the body so one arm can be turned and patterned",
            ],
            name="quadcopter",
            description="X-frame quadcopter with four patterned arms, motors, propellers, legs, camera and GPS",
            features=f,
            summary=(
                "An X-frame quadcopter: one arm with motor, propeller and leg built once, turned 45 degrees and "
                "patterned four times, on a body with deck vents, camera, battery, GPS and antenna."
            ),
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "drone",
    "keywords": ("drone", "quadcopter", "quad copter", "quadrocopter", "multirotor", "multicopter", "uav", "quadrotor"),
    **_build(),
}
