"""Stage 70: where every post-union target points, and what the union is called.

Stage 69 left P11 as the dominant residual on the EXPLICIT golden request and
recorded it as "every P11 attempt targets the product noun `enclosure`". That
is true as a token and misleading as a description, which this module exists
to make impossible to repeat: `enclosure` is **the union operation's own id**,
not a noun invented from nowhere. The model names the union after the product
and then targets the name it just wrote.

So the classification here is relational, never a word list. A target is read
against the plan's own structure -- the union's target, the union's own id,
the union's consumed tools, the rest of the plan -- so it says WHICH ROLE the
model named, and the same code works whatever nouns a future model picks.

Adds to Stage 68's evaluator and Stage 69's axis analysis; replaces neither.
Imports no kernel and no vendor SDK.
"""
from __future__ import annotations

from collections import Counter
from typing import Final

#: The union id the prompt mandates, read from the prompt rather than typed
#: here, so this module cannot disagree with what the model was told.
def mandated_union_id(prompt_text: str) -> str | None:
    marker = "Name the union itself `"
    if marker not in prompt_text:
        return None
    rest = prompt_text.split(marker, 1)[1]
    return rest.split("`", 1)[0] or None


#: The roles a post-union target can name. Relational, not lexical.
SURVIVING_BODY: Final[str] = "surviving_body"        # correct
UNION_OWN_ID: Final[str] = "union_own_id"            # P11
CONSUMED_TOOL: Final[str] = "consumed_tool"          # P12-shaped
OTHER_EXISTING: Final[str] = "other_existing_id"
ABSENT_FROM_PLAN: Final[str] = "absent_from_plan"

ROLES: Final[tuple[str, ...]] = (
    SURVIVING_BODY, UNION_OWN_ID, CONSUMED_TOOL, OTHER_EXISTING,
    ABSENT_FROM_PLAN,
)


def classify_target(target: str | None, union: dict, ids: set[str]) -> str | None:
    """Which ROLE in the plan this target names. No noun is special."""
    if target is None:
        return None
    if target == union["target"]:
        return SURVIVING_BODY
    if target == union["id"]:
        return UNION_OWN_ID
    if target in (union["tools"] or ()):
        return CONSUMED_TOOL
    if target in ids:
        return OTHER_EXISTING
    return ABSENT_FROM_PLAN


def analyse(operations: list[dict], prompt_text: str | None = None) -> dict:
    """`operations` is Stage 68's arena row: id / type / target / tools."""
    ids = {o["id"] for o in operations}
    unions = [o for o in operations if o.get("type") == "union"]
    if not unions:
        return {"has_union": False, "union": None, "post_union": [],
                "roles": {}, "union_id_is_mandated": None}
    union = unions[0]
    index = operations.index(union)
    post = []
    for op in operations[index + 1:]:
        role = classify_target(op.get("target"), union, ids)
        if role is None:
            continue
        post.append({"id": op["id"], "type": op.get("type"),
                     "target": op.get("target"), "role": role})
    mandated = mandated_union_id(prompt_text) if prompt_text else None
    return {
        "has_union": True,
        "union": {"id": union["id"], "target": union["target"],
                  "tools": list(union["tools"] or ()),
                  "tool_count": len(union["tools"] or ())},
        "unions_in_plan": len(unions),
        "post_union": post,
        "roles": dict(Counter(p["role"] for p in post)),
        "all_targets_correct": bool(post) and all(
            p["role"] == SURVIVING_BODY for p in post),
        "mandated_union_id": mandated,
        "union_id_is_mandated": None if mandated is None
                                else union["id"] == mandated,
    }


def summarise(analyses: list[dict]) -> dict:
    """The Phase 2 distribution, over a run's attempts."""
    withu = [a for a in analyses if a["has_union"]]
    roles = Counter()
    for a in withu:
        roles.update(a["roles"])
    obeyed = [a for a in withu if a["union_id_is_mandated"]]
    broke = [a for a in withu if a["union_id_is_mandated"] is False]
    def wrong(group):
        return sum(1 for a in group if not a["all_targets_correct"])
    return {
        "attempts": len(analyses),
        "with_a_union": len(withu),
        "union_ids": dict(Counter(a["union"]["id"] for a in withu)),
        "union_targets": dict(Counter(a["union"]["target"] for a in withu)),
        "target_roles": dict(roles),
        "all_targets_correct": sum(1 for a in withu if a["all_targets_correct"]),
        "union_id_mandated": len(obeyed),
        "union_id_not_mandated": len(broke),
        # The contingency Stage 70 exists to measure. Reported as counts,
        # never as a cause.
        "wrong_target_given_mandated_id": [wrong(obeyed), len(obeyed)],
        "wrong_target_given_other_id": [wrong(broke), len(broke)],
    }
