"""Template: a four-legged table with apron rails.

A rounded-corner top on four square legs, each with a small levelling foot,
joined under the top by an apron of four rails that run leg to leg and rise
into the underside of the top. Every leg and foot is blended before it is
fused, and every attached piece starts inside the face it joins.
"""

from __future__ import annotations

from typing import Any, Mapping

from cad_ai.templates._helpers import answer, box, cyl, fillet, union


def _build() -> Mapping[str, Any]:
    length, width, top_t, height = 1200, 700, 30, 750
    top_z = height - top_t
    leg, inset = 60, 40
    rail_t, rail_h, rail_in = 20, 110, 10
    leg_h = top_z + 2
    xs = (inset, length - inset - leg)
    ys = (inset, width - inset - leg)

    f = [box("top", (length, width, top_t), (0, 0, top_z)), fillet("round_top", "top", 25, "Z")]
    tools = []
    corners = [(a, i, b, j) for a, i in zip(xs, (1, 2)) for b, j in zip(ys, (1, 2))]
    for x, i, y, j in corners:
        n = f"{i}{j}"
        f += [box(f"leg_{n}", (leg, leg, leg_h), (x, y, 0)), fillet(f"round_leg_{n}", f"leg_{n}", 6, "Z")]
        f += [cyl(f"foot_{n}", 44, 12, (x + leg / 2, y + leg / 2, -10)), fillet(f"round_foot_{n}", f"foot_{n}", 3)]
        tools += [f"leg_{n}", f"foot_{n}"]
    rail_z = top_z + 2 - rail_h
    rail_len_x = xs[1] + leg - xs[0] - 2 * rail_in
    rail_len_y = ys[1] + leg - ys[0] - 2 * rail_in
    rails = [
        box("rail_front", (rail_len_x, rail_t, rail_h), (xs[0] + rail_in, ys[0], rail_z)),
        box("rail_back", (rail_len_x, rail_t, rail_h), (xs[0] + rail_in, ys[1] + leg - rail_t, rail_z)),
        box("rail_left", (rail_t, rail_len_y, rail_h), (xs[0], ys[0] + rail_in, rail_z)),
        box("rail_right", (rail_t, rail_len_y, rail_h), (xs[1] + leg - rail_t, ys[0] + rail_in, rail_z)),
    ]
    f += rails + [union("join_table", "top", tools + [r["id"] for r in rails])]

    plan = (
        "Top 1200x700x30 at z 720-750, corners rounded r25. Legs 60x60 at x 40-100 and "
        "1100-1160, y 40-100 and 600-660, z 0-722 (2 mm into the top). Feet d44 h12 at "
        "z -10..2 under each leg centre. Apron rails 20 thick, 110 tall, z 612-722: front/back "
        "x 90-1110 at y 40-60 and 640-660; left/right y 90-610 at x 40-60 and 1140-1160. "
        "Each rail overlaps two legs by 10 mm and the top by 2 mm."
    )
    return {
        "request": "a four-legged table with a rounded top, square legs and apron rails under the top",
        "answer": answer(
            plan=plan,
            assumptions=[
                "No unit given; assumed millimetres",
                "Chose a 1200 x 700 mm dining-desk top, 750 mm tall overall",
                "Legs are square and straight, with a small round levelling foot",
                "Apron is four plain rails; no drawers, joinery or taper",
            ],
            name="table",
            description="Four-legged table with a rounded top, apron rails and levelling feet",
            features=f,
            summary="A 1200 x 700 x 750 mm table: rounded top, four square legs with feet, and an apron of four rails.",
        ),
    }


TEMPLATE: Mapping[str, Any] = {
    "name": "table",
    "keywords": ("table", "desk", "dining table", "coffee table", "side table", "workbench"),
    "excludes": ("lamp", "table saw", "timetable", "turntable"),
    **_build(),
}
