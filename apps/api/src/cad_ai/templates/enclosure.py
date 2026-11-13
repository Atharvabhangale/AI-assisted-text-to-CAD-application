"""Template: an electronics enclosure.

A rounded hollow box centred on the origin, open on top, with four corner
screw posts, four PCB standoffs, four mounting ears, a row of rounded
ventilation slots in the back wall and a cable gland (hex body and tapered cap)
on one end wall. The box is symmetric about the Z axis, so posts, standoffs,
ears, their pilot holes and the ear holes are each written for one half and
placed by a pattern of 2 turned 180 degrees about Z; the vent slot is written
once and repeated by a linear pattern along X. Everything attached starts
inside the face it joins; all cuts are made after the pieces are fused.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.features import answer, box, cyl, drill, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    L, W, H, t = 120, 80, 40, 3
    hx, hy = L / 2, W / 2  # 60, 40
    f = [
        box("shell", (L, W, H), (-hx, -hy, 0)), fillet("round_shell", "shell", 6, "Z"),
        box("cavity", (L - 2 * t, W - 2 * t, H), (-hx + t, -hy + t, t)), fillet("round_cavity", "cavity", 3, "Z"),
        subtract("hollow", "shell", ["cavity"]),
    ]
    # One half of the symmetric parts: the +X posts, standoffs and ears.
    half = [
        cyl("post_a", 8, 37, (54, 34, 1)), cyl("post_b", 8, 37, (54, -34, 1)),
        cyl("standoff_a", 7, 10, (40, 25, 1)), cyl("standoff_b", 7, 10, (40, -25, 1)),
        box("ear_a", (20, 20, 4), (hx - 2, 10, 0)), fillet("round_ear_a", "ear_a", 1.5, "Z"),
        box("ear_b", (20, 20, 4), (hx - 2, -30, 0)), fillet("round_ear_b", "ear_b", 1.5, "Z"),
    ]
    half_ids = [h["id"] for h in half if h["type"] != "fillet"]
    f += half + [union("join_halves", "shell", half_ids, count=2, axis="Z", angle=180)]
    # Cable gland on the +X end wall: a hex body and a tapered cap.
    f += [
        cyl("gland_hex", 26, 8, (hx - 1, 0, 22), "+X", sides=6),
        cyl("gland_cap", 22, 10, (hx + 6, 0, 22), "+X", top=16),
        union("join_gland", "shell", ["gland_hex", "gland_cap"]),
        *drill("cut_cable", "shell", 10, (hx - 6, 0, 22), 26, "+X"),
    ]
    # Vent slot: a 4 x 14 box with round ends, one step (10 mm) before the first slot at x=-35.
    x0, step, n = -45, 10, 8
    f += [
        box("vent_mid", (4, 9, 14), (x0 - 2, hy - 6, 16)),
        cyl("vent_low", 4, 9, (x0, hy - 6, 16), "+Y"),
        cyl("vent_high", 4, 9, (x0, hy - 6, 30), "+Y"),
        subtract("cut_vents", "shell", ["vent_mid", "vent_low", "vent_high"], count=n, axis="X", step=step),
    ]
    # Pilot holes: one post and one standoff per half, turned 180 degrees for the other half.
    f += [
        cyl("pilot_post_a", 3, 16, (54, 34, 24)), cyl("pilot_post_b", 3, 16, (54, -34, 24)),
        cyl("pilot_so_a", 2.5, 8, (40, 25, 5)), cyl("pilot_so_b", 2.5, 8, (40, -25, 5)),
        subtract("cut_pilots", "shell", ["pilot_post_a", "pilot_post_b", "pilot_so_a", "pilot_so_b"], count=2, axis="Z", angle=180),
        *drill("ear_hole_a", "shell", 5, (hx + 8, 20, -2), 8, count=2, angle=180),
        *drill("ear_hole_b", "shell", 5, (hx + 8, -20, -2), 8, count=2, angle=180),
    ]
    plan = (
        "Centre the box on the Z axis so its symmetry is a turn about Z. Shell 120x80x40 at x -60..60, "
        "y -40..40, z 0-40, vertical edges r6; cavity 114x74 from z 3 up through the open top, r3 corners: "
        "walls 3, floor 3. Corner posts d8 at (54,+-34) z 1-38, PCB standoffs d7 at (40,+-25) z 1-11, ears "
        "20x20x4 at x 58-78, y 10-30 and -30..-10 (2 mm into the end wall), Z edges r1.5: written for the +X "
        "half only and fused with count 2, axis Z, angle 180 -> copies at 180 degrees (the -X half) and 360 "
        "(their own places): 4 posts, 4 standoffs, 4 ears. Cable gland on the x=60 end wall at y=0 z=22: hex "
        "body d26 (corners on d26, 22.5 across flats) x 59-67, cap d22 tapering to d16 x 66-76; a d10 cable "
        "bore from x 54 to 80 along +X through wall, hex and cap. Vent slots in the back wall y 37-40: a 4x14 "
        "box z 16-30 with d4 round ends along +Y from y 34 to 43, placed at x=-45, one step before the first "
        "slot, and cut with count 8, axis X, step 10 -> slots at x -35, -25, ... 35. Pilots d3 in the posts "
        "from z 24 and d2.5 in the standoffs from z 5, written for (54,+-34) and (40,+-25), cut with count 2, "
        "angle 180. Ear holes d5 at (68,+-20) from z -2, length 8, each drilled with count 2, angle 180 -> "
        "the -X ears get theirs at (-68,-+20)."
    )
    return {
        "request": "an electronics enclosure with a wall thickness, PCB standoffs inside, ventilation slots, a cable gland and mounting ears with holes",
        "answer": answer(
            plan=plan,
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose 120 x 80 x 40 mm outside with 3 mm walls",
                "Open on top; the lid is left off so the standoffs and posts show",
                "Cable gland is a hex body and a tapered cap with a round cable bore; no thread or seal",
                "Standoffs hold a roughly 80 x 50 mm PCB; pilot holes stand in for threads",
                "Eight round-ended vent slots, 10 mm apart, in the back wall only",
            ],
            name="electronics-enclosure",
            description="Electronics enclosure with PCB standoffs, vent slots, cable gland and mounting ears",
            features=f,
            summary="A 120 x 80 x 40 mm open-top enclosure, 3 mm walls, with corner posts, four PCB standoffs and four ears (a 2 x 180 degree pattern), eight vent slots (a linear pattern) and a hex cable gland.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "enclosure",
    "keywords": ("enclosure", "electronics box", "electronics enclosure", "project box", "junction box", "housing for a pcb", "pcb housing", "pcb enclosure"),
    **_build(),
}
