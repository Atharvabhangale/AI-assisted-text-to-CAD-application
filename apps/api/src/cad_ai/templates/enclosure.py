"""Template: an electronics enclosure.

A rounded hollow box with a wall, open on top, with four corner screw posts,
four PCB standoffs with pilot holes, a row of ventilation slots in the back
wall, a cable gland on one end wall and four mounting ears with holes.
Everything attached starts inside the face it joins; all cuts are made after
the pieces are fused.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.templates._helpers import answer, box, cyl, fillet, hole, subtract, union


def _build() -> Mapping[str, Any]:
    L, W, H, t = 120, 80, 40, 3
    f = [
        box("shell", (L, W, H), (0, 0, 0)), fillet("round_shell", "shell", 6, "Z"),
        box("cavity", (L - 2 * t, W - 2 * t, H), (t, t, t)), fillet("round_cavity", "cavity", 3, "Z"),
        subtract("hollow", "shell", ["cavity"]),
    ]
    adds = []
    for x, y in ((6, 6), (L - 6, 6), (6, W - 6), (L - 6, W - 6)):
        adds.append(cyl(f"post_{int(x)}_{int(y)}", 8, 37, (x, y, 1)))
    for x, y in ((20, 15), (100, 15), (20, 65), (100, 65)):
        adds.append(cyl(f"standoff_{x}_{y}", 7, 10, (x, y, 1)))
    adds.append(cyl("gland", 28, 12, (L - 2, W / 2, 22), "+X"))
    ears = []
    for side, x in (("l", -18), ("r", L - 2)):
        for y in (10, 50):
            e = f"ear_{side}_{y}"
            ears += [box(e, (20, 20, 4), (x, y, 0)), fillet(f"round_{e}", e, 1.5, "Z")]
    adds_ids = [a["id"] for a in adds] + [e["id"] for e in ears if e["type"] == "box"]
    f += adds + ears + [union("join_parts", "shell", adds_ids)]
    vents = [box(f"vent_{k}", (4, 8, 18), (30 + 12 * k, W - 5, 14)) for k in range(6)]
    pilots = [cyl(f"pilot_{a['id']}", 3 if a["id"].startswith("post") else 2.5, 16, (a["position"]["x"], a["position"]["y"], 24 if a["id"].startswith("post") else 5))
              for a in adds if a["id"].startswith(("post", "standoff"))]
    f += vents + [subtract("cut_vents", "shell", [v["id"] for v in vents])]
    f += [cyl("cable_tool", 16, 24, (L - 8, W / 2, 22), "+X"), subtract("cut_cable", "shell", ["cable_tool"])]
    f += pilots + [subtract("cut_pilots", "shell", [x["id"] for x in pilots])]
    for e in [e for e in ears if e["type"] == "box"]:
        pos = e["position"]
        f.append(hole(f"hole_{e['id']}", "shell", 5, (pos["x"] + 10, pos["y"] + 10, 0)))
    plan = (
        "Shell 120x80x40, vertical edges r6; cavity 114x74 from z 3 up through the open top, r3 corners, "
        "walls 3 and floor 3. Corner posts d8 at (6,6),(114,6),(6,74),(114,74) z 1-38 with d3 pilots from z 24. "
        "PCB standoffs d7 at (20,15),(100,15),(20,65),(100,65) z 1-11 with d2.5 pilots from z 5. Cable gland d28 "
        "on the x=120 end wall at y=40 z=22, x 118-130, bore d16 through the wall. Six vent slots 4x8x18 at "
        "x 30+12k in the back wall y 77-80, z 14-32. Four ears 20x20x4 at x -18..2 and 118..138, y 10-30 and 50-70, "
        "each with a d5 hole through its centre."
    )
    return {
        "request": "an electronics enclosure with a wall thickness, PCB standoffs inside, ventilation slots, a cable gland hole and mounting ears with holes",
        "answer": answer(
            plan=plan,
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose 120 x 80 x 40 mm outside with 3 mm walls",
                "Open on top; the lid is left off so the standoffs and posts show",
                "Cable gland is a plain collar with a round bore, no thread or nut",
                "Standoffs hold a roughly 80 x 50 mm PCB; pilot holes stand in for threads",
            ],
            name="electronics-enclosure",
            description="Electronics enclosure with PCB standoffs, vents, cable gland and mounting ears",
            features=f,
            summary="A 120 x 80 x 40 mm open-top enclosure, 3 mm walls, with corner posts, four PCB standoffs, six vent slots, a cable gland and four mounting ears.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "enclosure",
    "keywords": ("enclosure", "electronics box", "electronics enclosure", "project box", "junction box", "housing for a pcb", "pcb housing", "pcb enclosure"),
    **_build(),
}
