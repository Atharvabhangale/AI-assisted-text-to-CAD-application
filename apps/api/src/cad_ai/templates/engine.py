"""Template: a cutaway inline-four engine.

Block, crankcase, oil pan and head; intake and exhaust ports; spark plug
wells; valves as spring/retainer/stem stacks; pistons, connecting rods and a
crankshaft (journals through the bulkheads, a pin and two webs per throw) in a
cutaway; overhead camshafts on bearing towers with a lobe per valve; a timing
belt of cam pulleys, crank pulley and straight runs; flywheel, oil filter,
alternator and a mount. Built and checked on the real engine: one solid.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from cad_core.model import SCHEMA_VERSION

from cad_ai.templates._helpers import p as _p


def _build() -> Mapping[str, Any]:
    """The reference cutaway engine.

    Built from parameters rather than written out, so its 114 features stay
    consistent with one another; ``tests/fixtures/cutaway_engine.json`` holds
    the same features and a test checks the two agree.
    """

    def box(i: str, s: Tuple[float, float, float], p: Tuple[float, float, float]) -> Dict[str, Any]:
        return {"id": i, "type": "box", "size": _p(*s), "position": _p(*p)}

    def cyl(i: str, d: float, h: float, p: Tuple[float, float, float], axis: str = "+Z") -> Dict[str, Any]:
        return {"id": i, "type": "cylinder", "diameter": d, "height": h, "position": _p(*p), "axis": axis}

    def op(i: str, kind: str, target: str, tools: Sequence[str]) -> Dict[str, Any]:
        return {"id": i, "type": kind, "target": target, "tools": list(tools)}

    def fillet(i: str, target: str, radius: float, axis: Optional[str] = None) -> Dict[str, Any]:
        edges: Dict[str, Any] = {"select": "axis_parallel", "axis": axis} if axis else {"select": "all"}
        return {"id": i, "type": "fillet", "target": target, "radius": radius, "edges": edges}

    xs = (40, 100, 160, 220)
    yc, zc, stroke, rod = 70, 35, 40, 100
    up = (True, False, False, True)
    f: list = [
        box("block", (260, 140, 170), (0, 0, 60)), fillet("round_block", "block", 4, "Z"),
        box("crankcase", (280, 170, 70), (-10, -15, 0)), fillet("round_crankcase", "crankcase", 6, "Z"),
        box("oil_pan", (240, 130, 45), (10, 5, -40)), fillet("round_oil_pan", "oil_pan", 8),
        box("head", (250, 130, 60), (5, 5, 229)), fillet("round_head", "head", 3, "X"),
        op("join_main", "union", "block", ["crankcase", "oil_pan", "head"]),
    ]
    for n, x in enumerate(xs, 1):
        f += [cyl(f"bore_{n}", 50, 190, (x, yc, 40)), box(f"crank_bay_{n}", (48, 110, 70), (x - 24, yc - 55, 0))]
    f.append(op("cut_bores", "subtract", "block", [f"bore_{n}" for n in range(1, 5)] + [f"crank_bay_{n}" for n in range(1, 5)]))
    f += [box("section", (340, 90, 270), (-40, yc - 90, -42)), op("cutaway", "subtract", "block", ["section"])]
    for n, x in enumerate(xs, 1):
        f += [box(f"intake_port_{n}", (24, 50, 20), (x - 12, 0, 248)), cyl(f"exhaust_port_{n}", 22, 50, (x, 140, 258), "-Y")]
    f.append(op("cut_ports", "subtract", "block", [f"intake_port_{n}" for n in range(1, 5)] + [f"exhaust_port_{n}" for n in range(1, 5)]))
    f += [cyl(f"plug_well_{n}", 16, 30, (x, 70, 265)) for n, x in enumerate(xs, 1)]
    f.append(op("cut_plug_wells", "subtract", "block", [f"plug_well_{n}" for n in range(1, 5)]))
    valves: list = []
    for n, x in enumerate(xs, 1):
        for side, y, dx in (("in", 40, -14), ("ex", 100, 14)):
            valves += [
                cyl(f"{side}_spring_{n}", 22, 30, (x + dx, y, 288)),
                cyl(f"{side}_retainer_{n}", 26, 5, (x + dx, y, 317)),
                cyl(f"{side}_stem_{n}", 8, 14, (x + dx, y, 321)),
            ]
    f += valves + [op("add_valves", "union", "block", [v["id"] for v in valves])]
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
    f += train + [op("add_crank_train", "union", "block", [t["id"] for t in train])]
    cam: list = [box(f"cam_tower_{k}", (8, 84, 64), (tx, 28, 288)) for k, tx in enumerate((6, 66, 126, 186, 246), 1)]
    for side, y in (("intake", 40), ("exhaust", 100)):
        cam.append(cyl(f"{side}_camshaft", 24, 304, (-4, y, 350), "+X"))
        for n, x in enumerate(xs, 1):
            vx = x - 14 if side == "intake" else x + 14
            cam.append(cyl(f"{side}_lobe_{n}", 32, 12, (vx - 6, y, 345), "+X"))
    f += cam + [op("add_camshafts", "union", "block", [c["id"] for c in cam])]
    belt = [
        cyl("intake_cam_pulley", 70, 20, (290, 40, 350), "+X"),
        cyl("exhaust_cam_pulley", 70, 20, (290, 100, 350), "+X"),
        box("belt_left_run", (18, 4, 315), (291, 23, 35)),
        box("belt_right_run", (18, 4, 315), (291, 113, 35)),
        box("belt_top_run", (18, 60, 4), (291, 40, 383)),
    ]
    f += belt + [op("add_timing_drive", "union", "block", [b["id"] for b in belt])]
    f += [
        cyl("flywheel", 220, 25, (-58, yc, zc), "+X"), cyl("crank_pulley", 90, 20, (290, yc, zc), "+X"),
        cyl("oil_filter", 60, 50, (200, 138, 100), "+Y"), cyl("alternator", 80, 60, (60, 138, 140), "+Y"),
        box("mount_right", (40, 30, 40), (110, 138, 90)),
        op("add_accessories", "union", "block", ["flywheel", "crank_pulley", "oil_filter", "alternator", "mount_right"]),
        {"id": "mount_bolt_right", "type": "through_hole", "target": "block", "diameter": 10, "position": _p(130, 160, 0), "axis": "+Z"},
    ]
    return {
        "request": "a detailed 4 cylinder engine with pistons, a crankshaft, camshafts, a timing belt, valves and ports",
        "answer": {
            "status": "document",
            "plan": (
                "Cylinders at x=40,100,160,220 (pitch 60), centre line y=70, crank "
                "axis y=70 z=35, stroke 40, rod 100. Block 260x140x170 at z 60-230; "
                "crankcase 280x170x70 at z 0-70; pan z -40..5; head 250x130x60 at "
                "z 229-289. Bores d50 from z=40 up to z=230 (top of block, never into "
                "the head); crank bay 48 wide per cylinder, leaving 12 mm bulkheads. "
                "Cutaway box removes y<70 below z=229, before the internals. Ports "
                "into the head's two long sides; plug wells into the deck between "
                "the valves. Valves: spring z 288-318, retainer 317-322, stem "
                "321-335. Crank: journal segments through every bulkhead and end "
                "wall, pins at z=55 (cyl 1,4) and z=15 (cyl 2,3), webs either side "
                "of each pin. Rods 100 tall on the pins, pistons d48 h40 on the "
                "rods inside the bores. Camshafts d24 along x at y=40 and y=100, "
                "z=350, on five towers standing on the deck between the valves; a "
                "lobe per valve dropping onto its stem. Timing drive at x=290: "
                "cam pulleys d70 on the camshaft ends, the crank pulley on the "
                "crank, and three belt runs overlapping the pulleys' rims. "
                "Flywheel on the crank at the other end; filter, alternator and "
                "mount 2 mm into the back face."
            ),
            "assumptions": [
                "No unit given; assumed millimetres",
                "Chose 50 mm bore, 40 mm stroke, 60 mm pitch, 100 mm rods",
                "Front half cut away below the head so the pistons, rods and crankshaft show",
                "Valve cover and timing cover left off so the camshafts, valves and belt show",
                "Timing belt approximated as three straight runs around the pulleys",
                "Moving parts fused in place: crankshaft held by its journals, cylinders 1 and 4 at top",
            ],
            "document": {
                "schema_version": SCHEMA_VERSION,
                "units": "mm",
                "name": "inline-four-engine-cutaway",
                "description": "Inline-four engine, front half cut away to show pistons, connecting rods and crankshaft",
                "features": f,
            },
            "summary": "A cutaway inline-four engine with pistons, crankshaft, camshafts, timing belt, valves and ports.",
            "questions": [],
            "issues": [],
        },
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "engine",
    "keywords": ("engine", "piston", "pistons", "crankshaft", "camshaft", "camshafts", "cylinder head", "timing belt", "inline-four", "inline four", "v8", "v6"),
    "excludes": ("rocket", "jet", "jet engine", "steam engine", "engine mount", "fire engine"),
    **_build(),
}
