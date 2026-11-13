"""Template: a four-legged table with apron rails.

A rounded-corner top centred on the origin, on four round tapered legs (wide
under the top, slim at the floor) with a small glide under each, joined under
the top by an apron of rails that run leg to leg and rise into the underside
of the top, and by a low stretcher at each end. The table is symmetric about
the Z axis, so one leg per side, one rail per direction and one stretcher are
written once and placed by a pattern of 2 turned 180 degrees about Z. Every
attached piece starts inside the face it joins.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.features import answer, box, cyl, fillet, union


def _build() -> Mapping[str, Any]:
    length, width, top_t, height = 1200, 700, 30, 750
    top_z = height - top_t  # 720: underside of the top
    lx, ly = 530, 280  # leg centres at (+-530, +-280)
    leg_top, leg_foot = 60, 36  # leg diameter under the top / at the floor
    glide_h = 12
    leg_z0 = glide_h - 2  # the leg starts 2 mm inside its glide
    leg_h = top_z + 2 - leg_z0  # and ends 2 mm inside the top
    rail_t, rail_h = 20, 110
    rail_z = top_z + 2 - rail_h  # 612: rails rise 2 mm into the top

    f = [
        box("top", (length, width, top_t), (-length / 2, -width / 2, top_z)),
        fillet("round_top_corners", "top", 25, "Z"),
        fillet("round_top_edges", "top", 3),
    ]
    legs = []
    for side, y in (("a", ly), ("b", -ly)):
        leg, glide = f"leg_{side}", f"glide_{side}"
        f += [
            cyl(leg, leg_foot, leg_h, (lx, y, leg_z0), top=leg_top),
            cyl(glide, 30, glide_h, (lx, y, 0), top=34),
            fillet(f"round_{glide}", glide, 2),
            union(f"fit_{glide}", leg, [glide]),
        ]
        legs.append(leg)
    rails = [
        # Long rail on the +Y leg line, leg centre to leg centre; the 180-degree copy is the -Y rail.
        box("rail_long", (2 * lx, rail_t, rail_h), (-lx, ly - rail_t / 2, rail_z)),
        # Short rail on the +X leg line; the 180-degree copy is the -X rail.
        box("rail_short", (rail_t, 2 * ly, rail_h), (lx - rail_t / 2, -ly, rail_z)),
        # Low stretcher between the two +X legs; the 180-degree copy ties the -X legs.
        box("stretcher", (rail_t, 2 * ly, 40), (lx - rail_t / 2, -ly, 150)),
    ]
    f += rails + [union("join_table", "top", legs + [r["id"] for r in rails], count=2, axis="Z", angle=180)]

    plan = (
        "Centre the table on the Z axis so its symmetry is a turn about Z. Top 1200x700x30 at "
        "x -600..600, y -350..350, z 720-750; corners r25, then every top edge r3. Leg centres at "
        "(+-530, +-280). Each leg is a round taper along +Z from z 10 (d36 at the floor end) to z 722 "
        "(d60 under the top, 2 mm into it), height 712. Under each leg a glide d30 tapering to d34, "
        "z 0-12, edges r2, fused into its leg first. Apron rails 20 thick, 110 tall, z 612-722, "
        "centred on the leg lines: rail_long x -530..530 on y=280, rail_short y -280..280 on x=530; "
        "each ends at two leg centres, so it is buried in both legs. A low stretcher 20x560x40 at "
        "z 150-190 on x=530 ties the two end legs the same way. Legs, rails and stretcher are written for "
        "one half only (legs at (530,280) and (530,-280)) and fused into the top with a pattern: "
        "count 2, axis Z, angle 180 -> copies at 180 degrees (the opposite corners, rails and "
        "stretcher) and 360 degrees (their own places): 4 legs, 4 rails, 2 stretchers. A square table would "
        "use one leg and one rail with count 4, angle 90."
    )
    return {
        "request": "a four-legged table with a rounded top, tapered legs and apron rails under the top",
        "answer": answer(
            plan=plan,
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a 1200 x 700 mm dining-desk top, 750 mm tall overall",
                "Legs are round and taper from 60 mm under the top to 36 mm at the floor, with a small glide",
                "Apron is four plain rails centred on the legs, plus a low stretcher at each end; no drawers or joinery",
                "The table is a rectangle, so the legs are placed by two copies turned 180 degrees, not four at 90",
            ],
            name="table",
            description="Four-legged table with a rounded top, tapered legs, apron rails and glides",
            features=f,
            summary="A 1200 x 700 x 750 mm table: rounded top, four tapered round legs with glides, an apron of four rails and two end stretchers, placed by a 2 x 180 degree pattern.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "table",
    "keywords": ("table", "desk", "dining table", "coffee table", "side table", "workbench"),
    "excludes": ("lamp", "table saw", "timetable", "turntable"),
    **_build(),
}
