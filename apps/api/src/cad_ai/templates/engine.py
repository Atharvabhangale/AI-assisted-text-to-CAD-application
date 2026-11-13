"""Template: a cutaway inline-four engine.

Block, crankcase, oil pan and head; intake and exhaust ports; spark plug
wells; valves as spring/retainer/stem stacks; pistons, connecting rods and a
crankshaft (journals through the bulkheads, a pin and two webs per throw) in a
cutaway; overhead camshafts on bearing towers with a lobe per valve; a timing
belt of cam pulleys, crank pulley and straight runs; flywheel, oil filter,
alternator and a mount with a drilled bolt hole. Built and checked on the
real engine: one solid.

Since schema ``1.2.0`` the parts that repeat identically at the 60 mm
cylinder pitch -- bores, crank bays, ports, plug wells and the valve stacks
-- are one tool each, copied four times by a row pattern along X. The parts
that differ per cylinder (pins, webs, rods and pistons alternate up and down;
journals differ in length) and the camshaft lobes and towers stay explicit.
The solid is the same one the 114-feature ``1.1.1`` engine built.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.features import answer, box, cyl, drill, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    """The reference cutaway engine.

    Built from parameters rather than written out, so its features stay
    consistent with one another; ``tests/fixtures/cutaway_engine.json`` holds
    the same features and a test checks the two agree.
    """

    xs = (40, 100, 160, 220)
    pitch = 60
    x0 = xs[0] - pitch  # a row tool sits one pitch before cylinder 1
    yc, zc, stroke, rod = 70, 35, 40, 100
    up = (True, False, False, True)
    row = dict(count=4, axis="X", step=pitch)
    f: list = [
        box("block", (260, 140, 170), (0, 0, 60)), fillet("round_block", "block", 4, "Z"),
        box("crankcase", (280, 170, 70), (-10, -15, 0)), fillet("round_crankcase", "crankcase", 6, "Z"),
        box("oil_pan", (240, 130, 45), (10, 5, -40)), fillet("round_oil_pan", "oil_pan", 8),
        box("head", (250, 130, 60), (5, 5, 229)), fillet("round_head", "head", 3, "X"),
        union("join_main", "block", ["crankcase", "oil_pan", "head"]),
        cyl("bore", 50, 190, (x0, yc, 40)), box("crank_bay", (48, 110, 70), (x0 - 24, yc - 55, 0)),
        subtract("cut_bores", "block", ["bore", "crank_bay"], **row),
        box("section", (340, 90, 270), (-40, yc - 90, -42)), subtract("cutaway", "block", ["section"]),
        box("intake_port", (24, 50, 20), (x0 - 12, 0, 248)), cyl("exhaust_port", 22, 50, (x0, 140, 258), "-Y"),
        subtract("cut_ports", "block", ["intake_port", "exhaust_port"], **row),
        cyl("plug_well", 16, 30, (x0, 70, 265)),
        subtract("cut_plug_wells", "block", ["plug_well"], **row),
    ]
    valves: list = []
    for side, y, dx in (("in", 40, -14), ("ex", 100, 14)):
        valves += [
            cyl(f"{side}_spring", 22, 30, (x0 + dx, y, 288)),
            cyl(f"{side}_retainer", 26, 5, (x0 + dx, y, 317)),
            cyl(f"{side}_stem", 8, 14, (x0 + dx, y, 321)),
        ]
    f += valves + [union("add_valves", "block", [v["id"] for v in valves], **row)]
    edges = [-34, xs[0] - 20] + [e for i in range(3) for e in (xs[i] + 20, xs[i + 1] - 20)] + [xs[3] + 20, 294]
    train: list = [
        cyl(f"journal_{k // 2 + 1}", 36, edges[k + 1] - edges[k], (edges[k], yc, zc), "+X")
        for k in range(0, len(edges), 2)
    ]
    for n, x in enumerate(xs, 1):
        pz = zc + (stroke / 2 if up[n - 1] else -stroke / 2)
        lo, hi = min(zc, pz) - 16, max(zc, pz) + 16
        train += [
            cyl(f"crank_pin_{n}", 24, 34, (x - 17, yc, pz), "+X"),
            box(f"web_a_{n}", (8, 40, hi - lo), (x - 21, yc - 20, lo)),
            box(f"web_b_{n}", (8, 40, hi - lo), (x + 13, yc - 20, lo)),
        ]
    for n, x in enumerate(xs, 1):
        pz = zc + (stroke / 2 if up[n - 1] else -stroke / 2)
        train += [box(f"rod_{n}", (12, 16, rod), (x - 6, yc - 8, pz)), cyl(f"piston_{n}", 48, 40, (x, yc, pz + rod - 10))]
    f += train + [union("add_crank_train", "block", [t["id"] for t in train])]
    cam: list = [box(f"cam_tower_{k}", (8, 84, 64), (tx, 28, 288)) for k, tx in enumerate((6, 66, 126, 186, 246), 1)]
    for side, y in (("intake", 40), ("exhaust", 100)):
        cam.append(cyl(f"{side}_camshaft", 24, 304, (-4, y, 350), "+X"))
        for n, x in enumerate(xs, 1):
            vx = x - 14 if side == "intake" else x + 14
            cam.append(cyl(f"{side}_lobe_{n}", 32, 12, (vx - 6, y, 345), "+X"))
    f += cam + [union("add_camshafts", "block", [c["id"] for c in cam])]
    belt = [
        cyl("intake_cam_pulley", 70, 20, (290, 40, 350), "+X"),
        cyl("exhaust_cam_pulley", 70, 20, (290, 100, 350), "+X"),
        box("belt_left_run", (18, 4, 315), (291, 23, 35)),
        box("belt_right_run", (18, 4, 315), (291, 113, 35)),
        box("belt_top_run", (18, 60, 4), (291, 40, 383)),
    ]
    f += belt + [union("add_timing_drive", "block", [b["id"] for b in belt])]
    f += [
        cyl("flywheel", 220, 25, (-58, yc, zc), "+X"), cyl("crank_pulley", 90, 20, (290, yc, zc), "+X"),
        cyl("oil_filter", 60, 50, (200, 138, 100), "+Y"), cyl("alternator", 80, 60, (60, 138, 140), "+Y"),
        box("mount_right", (40, 30, 40), (110, 138, 90)),
        union("add_accessories", "block", ["flywheel", "crank_pulley", "oil_filter", "alternator", "mount_right"]),
    ]
    f += drill("mount_bolt_right", "block", 10, (130, 160, 88), 44, "+Z")
    return {
        "request": "a detailed 4 cylinder engine with pistons, a crankshaft, camshafts, a timing belt, valves and ports",
        "answer": answer(
            plan=(
                "Cylinders at x=40,100,160,220 (pitch 60), centre line y=70, crank "
                "axis y=70 z=35, stroke 40, rod 100. Block 260x140x170 at z 60-230; "
                "crankcase 280x170x70 at z 0-70; pan z -40..5; head 250x130x60 at "
                "z 229-289. Parts repeated per cylinder are one tool placed one "
                "pitch before cylinder 1 (at x=-20) with a row pattern count 4, "
                "axis X, step 60: copies at -20+60k = 40,100,160,220 for k=1..4. "
                "Bores d50 from z=40 up to z=230 (top of block, never into the "
                "head); crank bay 48 wide per cylinder, leaving 12 mm bulkheads. "
                "Cutaway box removes y<70 below z=229, before the internals. Ports "
                "into the head's two long sides; plug wells into the deck between "
                "the valves. Valves (intake x-14 y=40, exhaust x+14 y=100): spring "
                "z 288-318, retainer 317-322, stem 321-335, the same row pattern. "
                "Crank: journal segments through every bulkhead and end wall, pins "
                "at z=55 (cyl 1,4) and z=15 (cyl 2,3), webs either side of each "
                "pin; these alternate, so they are written per cylinder. Rods 100 "
                "tall on the pins, pistons d48 h40 on the rods inside the bores. "
                "Camshafts d24 along x at y=40 and y=100, z=350, on five towers "
                "standing on the deck between the valves; a lobe per valve dropping "
                "onto its stem. Timing drive at x=290: cam pulleys d70 on the "
                "camshaft ends, the crank pulley on the crank, and three belt runs "
                "overlapping the pulleys' rims. Flywheel on the crank at the other "
                "end; filter, alternator and mount 2 mm into the back face; bolt "
                "hole d10 drilled down through the mount (z 88-132, 2 mm past both "
                "faces) at x=130 y=160."
            ),
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose 50 mm bore, 40 mm stroke, 60 mm pitch, 100 mm rods",
                "Front half cut away below the head so the pistons, rods and crankshaft show",
                "Valve cover and timing cover left off so the camshafts, valves and belt show",
                "Timing belt approximated as three straight runs around the pulleys",
                "Moving parts fused in place: crankshaft held by its journals, cylinders 1 and 4 at top",
            ],
            name="inline-four-engine-cutaway",
            description="Inline-four engine, front half cut away to show pistons, connecting rods and crankshaft",
            features=f,
            summary="A cutaway inline-four engine with pistons, crankshaft, camshafts, timing belt, valves and ports.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "engine",
    "keywords": ("engine", "piston", "pistons", "crankshaft", "camshaft", "camshafts", "cylinder head", "timing belt", "inline-four", "inline four", "v8", "v6"),
    "excludes": ("rocket", "jet", "jet engine", "steam engine", "engine mount", "fire engine"),
    **_build(),
}
