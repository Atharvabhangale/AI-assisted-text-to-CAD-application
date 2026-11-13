"""Template: a model rocket.

A body tube with a coupler band, a nose cone of five stepped cylinders, four
fins (a root box and a swept tip box each) along +X, -X, +Y and -Y, an engine
nozzle with a hollow bell, and a launch lug with a bore. The rocket stands on
the Z axis. Every attached piece starts 2 mm inside the face it joins.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.templates._helpers import answer, box, chamfer, cyl, fillet, subtract, union


def _build() -> Mapping[str, Any]:
    f: list = []
    z0, tube_h = 60, 400
    f += [cyl("body", 60, tube_h, (0, 0, z0)), fillet("round_body", "body", 2)]
    f += [cyl("band", 64, 10, (0, 0, 300)), chamfer("chamfer_band", "band", 2)]
    cone = [(54, 40, 458), (44, 30, 496), (32, 22, 524), (18, 16, 544), (8, 12, 558)]
    nose = []
    for n, (d, h, z) in enumerate(cone, 1):
        f += [cyl(f"nose_{n}", d, h, (0, 0, z)), fillet(f"round_nose_{n}", f"nose_{n}", 1.5 if d > 10 else 1)]
        nose.append(f"nose_{n}")
    f += [cyl("nozzle_throat", 28, 14, (0, 0, 48)), cyl("nozzle_bell", 40, 12, (0, 0, 38)), chamfer("chamfer_bell", "nozzle_bell", 2)]
    fins = []
    for n, (sx, sy, tag) in enumerate(((1, 0, "px"), (-1, 0, "nx"), (0, 1, "py"), (0, -1, "ny")), 1):
        root_x = (28, -58, -1.5, -1.5)[n - 1]
        tip_x = (56, -80, -1.5, -1.5)[n - 1]
        if sx:
            f += [box(f"fin_root_{tag}", (30, 3, 80), (root_x, -1.5, 60)), box(f"fin_tip_{tag}", (24, 3, 50), (tip_x, -1.5, 60))]
        else:
            root_y = 28 if sy > 0 else -58
            tip_y = 56 if sy > 0 else -80
            f += [box(f"fin_root_{tag}", (3, 30, 80), (-1.5, root_y, 60)), box(f"fin_tip_{tag}", (3, 24, 50), (-1.5, tip_y, 60))]
        fins += [f"fin_root_{tag}", f"fin_tip_{tag}"]
    f += [cyl("lug", 8, 30, (32, 0, 255))]
    f += [cyl("bell_cavity", 22, 16, (0, 0, 36)), cyl("lug_bore", 4, 34, (32, 0, 253))]
    f += [union("add_nose", "body", ["band"] + nose)]
    f += [union("add_tail", "body", ["nozzle_throat", "nozzle_bell"] + fins + ["lug"])]
    f += [subtract("cut_details", "body", ["bell_cavity", "lug_bore"])]
    return {
        "request": "a model rocket with a nose cone, four fins, an exhaust nozzle and a launch lug",
        "answer": answer(
            plan=(
                "Axis Z, centre (0,0). Body tube d60 z60-460, coupler band d64 z300-310. "
                "Nose cone as five steps starting 2 mm inside the one below: d54 z458-498, "
                "d44 z496-526, d32 z524-546, d18 z544-560, d8 z558-570. Nozzle throat d28 "
                "z48-62 and bell d40 z38-50; cavity d22 z36-52 cut from below. Fins: four "
                "3 mm plates at +X,-X,+Y,-Y, root box 30x3x80 from r28 (2 mm inside the tube) "
                "plus swept tip box 24x3x50 at r56-80, z60 up. Launch lug d8 z255-285 at "
                "x=32 (2 mm inside the tube), bore d4 cut as a finite tool so it cannot "
                "touch the fins."
            ),
            assumptions=[
                "Nose cone stepped as five cylinders, since V1 has no cones or ogives.",
                "Fins are flat plates along the X and Y axes (four at 90 degrees), with a stepped swept tip.",
                "Body tube is solid, not a hollow tube.",
                "Sizes are typical for a 60 mm hobby rocket, about 51 cm tall.",
            ],
            name="Model rocket",
            description="Model rocket: body tube, stepped nose cone, four fins, engine nozzle and launch lug.",
            features=f,
            summary="A 60 mm model rocket with a five-step nose cone, four swept fins, a hollow engine nozzle and a bored launch lug.",
        ),
    }


TEMPLATE = {
    "name": "rocket",
    "keywords": ("rocket", "model rocket", "missile"),
    **_build(),
}
