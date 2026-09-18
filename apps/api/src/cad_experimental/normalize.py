"""Provider-independent readings of unambiguous mechanical English.

Why this exists
---------------
``intent.py`` reads exactly one shape of request -- a counted plate assembly --
and everything else goes to a model. That is the right default for genuinely
ambiguous language, and the wrong one for the large class of mechanical
sentences that have only one possible meaning:

    "make a 100 x 50 x 10 mm plate"
    "put a 10 mm hole through the centre"
    "round the outside vertical edges to 2 mm"
    "make it 20 mm taller"

A model is not needed to understand those, and asking one costs a network
round trip, a token bill and a chance of a wrong answer, to arrive at a
reading a regular expression can be certain of.

```
request (+ the current plan, for an edit)
   -> a reader that either understands it COMPLETELY or declines
   -> canonical Operation Plan
   -> parser -> validator -> graph -> backend
```

The plan goes out through the ordinary parser, so a deterministic reading
meets exactly the checks a model's plan meets. This is not a shortcut around
validation; it is another way of arriving at the same IR.

What a reader may not do
------------------------
**Decline rather than guess.** Every reader is written so that a partial
understanding produces nothing at all. A reader that half-understood
"chamfer the top edges" and emitted a chamfer of the *vertical* edges would
build a confidently wrong part, which is worse than not answering -- the model
is there for exactly the sentences these readers refuse.

**Invent no number.** A dimension that is not in the request is not supplied
from a default here. Where the Operation Plan itself has a default (a box's
position, a hole's axis) the reader simply omits the field and lets the
contract mean what it means.

**Say which reading was taken.** Where English is conventional rather than
certain -- "wider" meaning X -- the reading is reported in words so the person
can see the convention that was applied and correct it. A silent convention is
a guess wearing a uniform.

What is deliberately absent
---------------------------
No provider name, no model name, no prompt, no schema, and no branch on any of
those. Swapping Anthropic for a local model changes nothing in this file. It
imports ``re``, the standard library, and this package's own plan vocabulary.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .intent import looks_like_plate_assembly
from .plan import (
    BOX,
    CHAMFER,
    CYLINDER,
    FILLET,
    PATTERN,
    SELECT_CIRCULAR,
    SELECT_STRAIGHT,
    THROUGH_HOLE,
)

#: A reading that is certain, and the sentence that says what was understood.
#: The summary is shown to the person, so it names any convention applied.
@dataclass(frozen=True)
class Reading:
    """One complete understanding of a request.

    ``plan`` is a whole Operation Plan in its wire form -- for an edit, the
    current plan with the change applied -- because that is what the parser,
    the validator and the session all already speak. Returning a delta would
    add a second representation for no gain.
    """

    plan: Dict[str, Any]
    summary: str
    reader: str
    #: Conventions applied where English was not precise. Shown to the person
    #: so a wrong guess is visible and correctable, never silent.
    assumptions: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"summary": self.summary, "reader": self.reader,
                "assumptions": list(self.assumptions)}


class ReadingError(ValueError):
    """The grammar matched but the request cannot be honoured, and why.

    Distinct from declining. Declining means "not my sentence"; this means
    "your sentence, and it asks for something impossible" -- a hole wider than
    the stock, an edge treatment on a part with no such edges. The difference
    matters because a decline should fall through to the model and this should
    not.
    """


# --- shared number grammar --------------------------------------------------

#: A number, with an optional unit that must be millimetres if stated. Other
#: units are not converted here: the Operation Plan is millimetres throughout,
#: and quietly reinterpreting "2 inches" would be the worst kind of helpful.
_N = r"(\d+(?:\.\d+)?)"
_MM = r"(?:\s*(?:mm|millimet(?:re|er)s?))?"
_SEP = r"\s*(?:[*x×]|\bby\b)\s*"

_TRIPLE = re.compile(rf"{_N}{_MM}{_SEP}{_N}{_MM}{_SEP}{_N}{_MM}", re.I)
_DIAMETER = re.compile(
    rf"[ø⌀]\s*{_N}"
    rf"|\bdia(?:meter)?\.?\s*(?:of\s*)?{_N}"
    rf"|{_N}\s*mm\s*(?:dia(?:meter)?|(?:through\s+)?holes?|bores?)"
    rf"|{_N}\s*mm\s*(?=(?:\w+\s+){{0,2}}holes?\b)",
    re.I,
)

#: Units this system does not speak. Named so a request in them is refused
#: with a sentence about units rather than silently read as millimetres.
_FOREIGN_UNITS = re.compile(
    r"\b(?:inch(?:es)?|in\.|feet|foot|ft\.?|cm|centimet(?:re|er)s?|"
    r"m\b|met(?:re|er)s?|thou|mil)\b", re.I)


def _first_number(match: "re.Match[str]") -> Optional[float]:
    for group in match.groups():
        if group:
            return float(group)
    return None


def _positive(value: Optional[float], what: str) -> float:
    if value is None:
        raise ReadingError(f"no {what} was given")
    if not math.isfinite(value) or value <= 0:
        raise ReadingError(f"a {what} must be a positive number")
    if value > MAX_MM:
        raise ReadingError(
            f"a {what} of {value:g} mm is beyond what this supports")
    return value


#: The largest dimension any reader will accept. Not a kernel limit -- a
#: sanity bound, so a typo becomes a refusal rather than a tessellation that
#: never finishes.
MAX_MM = 5_000.0

#: The most operations a single deterministic reading may add. Bounded on
#: purpose: nothing here should be able to turn one sentence into an
#: unbounded plan.
MAX_ADDED_OPERATIONS = 64


def mentions_foreign_units(text: str) -> bool:
    """Whether the request states a length this system cannot accept."""
    return bool(_FOREIGN_UNITS.search(text or ""))


# --- reading the current plan -----------------------------------------------
#
# An edit reader needs to know what is already there. These read the plan's
# wire form directly -- it is the same shape the parser accepts and the
# session stores, so there is nothing to convert.


def _operations(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return list(plan.get("operations") or [])


def _of_type(plan: Mapping[str, Any], *kinds: str) -> List[Dict[str, Any]]:
    wanted = set(kinds)
    return [op for op in _operations(plan) if op.get("type") in wanted]


def _ids(plan: Mapping[str, Any]) -> List[str]:
    return [str(op.get("id")) for op in _operations(plan) if op.get("id")]


def _body_id(plan: Mapping[str, Any]) -> Optional[str]:
    """The id of the solid a further modifier should target.

    A modifier keeps its TARGET's id, so the body is whatever the first
    constructive operation declared -- not the id of the last operation, which
    for a modifier names nothing at all. Getting this wrong is the classic way
    to write a plan that references a feature as if it were a solid.
    """
    for op in _operations(plan):
        if op.get("type") in (BOX, CYLINDER):
            return str(op.get("id"))
    return None


def _envelope(plan: Mapping[str, Any]) -> Optional[Tuple[List[float], List[float]]]:
    """The bounding box of every constructive solid, from the plan alone.

    Derived from the plan rather than from a measurement so a reader can place
    a hole before anything is built. It is exact for the axis-aligned boxes and
    cylinders this vocabulary has; a reader that needs more than that should
    decline instead.
    """
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for op in _of_type(plan, BOX, CYLINDER):
        parameters = op.get("parameters") or {}
        position = parameters.get("position") or {}
        origin = [float(position.get(k, 0.0)) for k in ("x", "y", "z")]
        if op.get("type") == BOX:
            span = [float(parameters.get(k, 0.0)) for k in ("x", "y", "z")]
            corner = origin
        else:
            diameter = float(parameters.get("diameter", 0.0))
            height = float(parameters.get("height", 0.0))
            axis = str(parameters.get("axis", "+Z"))
            index = {"X": 0, "Y": 1, "Z": 2}[axis[-1]]
            span = [diameter, diameter, diameter]
            span[index] = height
            # A cylinder's position is the CENTRE of its base circle, so the
            # box around it starts half a diameter back on the two radial axes.
            corner = list(origin)
            for other in range(3):
                if other != index:
                    corner[other] -= diameter / 2.0
            if axis.startswith("-"):
                corner[index] -= height
        for k in range(3):
            lo[k] = min(lo[k], corner[k])
            hi[k] = max(hi[k], corner[k] + span[k])
    if any(not math.isfinite(v) for v in lo + hi):
        return None
    return lo, hi


def _unique(plan: Mapping[str, Any], stem: str) -> str:
    """An id like ``stem`` that no operation in ``plan`` already uses."""
    taken = set(_ids(plan))
    if stem not in taken:
        return stem
    for n in range(2, 1000):
        candidate = f"{stem}{n}"
        if candidate not in taken:
            return candidate
    raise ReadingError("cannot name a new operation; the plan is full of them")


def _plan_with(plan: Mapping[str, Any], *added: Mapping[str, Any],
               summary: Optional[str] = None) -> Dict[str, Any]:
    """The current plan with operations appended. The original is untouched."""
    out = copy.deepcopy(dict(plan))
    operations = list(out.get("operations") or [])
    if len(added) > MAX_ADDED_OPERATIONS:
        raise ReadingError(
            f"that would add {len(added)} operations; "
            f"{MAX_ADDED_OPERATIONS} is the most one request may add")
    operations.extend(copy.deepcopy(dict(op)) for op in added)
    out["operations"] = operations
    out["status"] = "generated"
    if summary:
        out["summary"] = summary
    return out


def _fresh_plan(summary: str, *operations: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "status": "generated",
        "summary": summary,
        "operations": [copy.deepcopy(dict(op)) for op in operations],
    }


# --- creation: a primitive solid --------------------------------------------

_BOX_WORDS = ("plate", "block", "box", "slab", "bar", "cuboid", "panel")
_ROUND_WORDS = ("cylinder", "rod", "shaft", "disc", "disk", "pin", "peg")

_TALL = re.compile(rf"{_N}{_MM}\s*(?:tall|high|long|in\s+length|length)", re.I)
_HEIGHT_FIRST = re.compile(rf"(?:height|length|tall)\s*(?:of\s*)?{_N}{_MM}", re.I)


def read_box(text: str, plan: Optional[Mapping[str, Any]]) -> Optional[Reading]:
    """"a 100 x 50 x 10 mm plate" -- three dimensions and a solid noun.

    Only fires when the request has no current part, or plainly asks for a new
    one. Three numbers in an existing conversation usually modify something
    rather than replace it, and replacing a part the person is working on is
    not a mistake worth risking on a regex.
    """
    lowered = (text or "").lower()
    if not any(word in lowered for word in _BOX_WORDS):
        return None
    if any(word in lowered for word in _ROUND_WORDS):
        return None            # "a plate and a rod" is not this reader's
    if looks_like_plate_assembly(text):
        # A counted plate assembly says "plate" and carries three numbers, so
        # this reader matches it and would build ONE of its six plates. The
        # assembly grammar owns that sentence; deferring here is not politeness
        # but the difference between an enclosure and a single plate.
        return None
    triple = _TRIPLE.search(lowered)
    if triple is None:
        return None
    if plan is not None and not _asks_for_a_new_part(lowered):
        return None
    x, y, z = (_positive(float(g), "dimension") for g in triple.groups())
    noun = next(w for w in _BOX_WORDS if w in lowered)
    summary = f"a {x:g} x {y:g} x {z:g} mm {noun}"
    return Reading(
        plan=_fresh_plan(summary, {
            "id": "body", "type": BOX,
            "parameters": {"x": x, "y": y, "z": z},
        }),
        summary=summary,
        reader="box",
        assumptions=(
            "read the three dimensions as X, Y then Z, in the order given",
        ),
    )


def read_cylinder(text: str,
                  plan: Optional[Mapping[str, Any]]) -> Optional[Reading]:
    """"a 20 mm diameter cylinder 50 mm tall" -- a diameter and a length."""
    lowered = (text or "").lower()
    if not any(word in lowered for word in _ROUND_WORDS):
        return None
    if plan is not None and not _asks_for_a_new_part(lowered):
        return None
    diameter_match = _DIAMETER.search(lowered)
    if diameter_match is None:
        return None
    height_match = _TALL.search(lowered) or _HEIGHT_FIRST.search(lowered)
    if height_match is None:
        return None
    diameter = _positive(_first_number(diameter_match), "diameter")
    height = _positive(_first_number(height_match), "height")
    noun = next(w for w in _ROUND_WORDS if w in lowered)
    summary = f"a {diameter:g} mm diameter {noun} {height:g} mm long"
    return Reading(
        plan=_fresh_plan(summary, {
            "id": "body", "type": CYLINDER,
            "parameters": {"diameter": diameter, "height": height},
        }),
        summary=summary,
        reader="cylinder",
        assumptions=("the axis was not stated, so the default +Z is used",),
    )


_NEW_PART = re.compile(
    r"\b(?:start over|new part|instead\s+make|replace\s+(?:it|this|that)\s+with"
    r"|scrap\s+(?:it|this|that))\b", re.I)


def _asks_for_a_new_part(lowered: str) -> bool:
    return bool(_NEW_PART.search(lowered))


# --- edit: a hole through the centre ----------------------------------------

_CENTRE = re.compile(
    r"\b(?:centre|center|middle)\b|\bcentred\b|\bcentered\b", re.I)
_HOLE_WORD = re.compile(r"\b(?:holes?|bores?|drill(?:ed|s)?|through)\b", re.I)
_ADD_WORD = re.compile(r"\b(?:add|put|make|drill|bore|cut|create|place)\b", re.I)


def read_centre_hole(text: str,
                     plan: Optional[Mapping[str, Any]]) -> Optional[Reading]:
    """"put a 10 mm hole through the centre" -- one bore on the part's axis.

    Declines without a part to drill, and declines when the request names more
    than one hole: "four holes in the centre" is not a sentence this reader
    can be certain of, and the corner reader below handles the counted case.
    """
    if plan is None:
        return None
    lowered = (text or "").lower()
    if not _HOLE_WORD.search(lowered) or not _CENTRE.search(lowered):
        return None
    if not _ADD_WORD.search(lowered):
        return None
    if _count_word(lowered) not in (None, 1):
        return None
    body = _body_id(plan)
    envelope = _envelope(plan)
    if body is None or envelope is None:
        return None
    diameter_match = _DIAMETER.search(lowered)
    if diameter_match is None:
        raise ReadingError(
            "a hole needs a diameter, and the request does not give one")
    diameter = _positive(_first_number(diameter_match), "diameter")

    lo, hi = envelope
    axis, index = _axis_for_hole(lowered)
    centre = [(lo[k] + hi[k]) / 2.0 for k in range(3)]
    centre[index] = lo[index]
    across = [hi[k] - lo[k] for k in range(3) if k != index]
    if diameter >= min(across):
        raise ReadingError(
            f"a {diameter:g} mm hole does not fit through a "
            f"{min(across):g} mm section")

    identifier = _unique(plan, "bore")
    summary = f"{plan.get('summary') or 'the part'}, with a {diameter:g} mm hole through the centre"
    return Reading(
        plan=_plan_with(plan, {
            "id": identifier, "type": THROUGH_HOLE, "target": body,
            "parameters": {
                "diameter": diameter,
                "position": {"x": centre[0], "y": centre[1], "z": centre[2]},
                "axis": axis,
            },
        }, summary=summary),
        summary=summary,
        reader="centre_hole",
        assumptions=(
            f"the hole runs along {axis}, through the middle of the part",
        ),
    )


_AXIS_PHRASES = (
    (re.compile(r"\balong\s+(?:the\s+)?([xyz])\b|\bin\s+(?:the\s+)?([xyz])\s*(?:axis|direction)", re.I), None),
)


def _axis_for_hole(lowered: str) -> Tuple[str, int]:
    """Which way a bore runs. ``+Z`` unless the request says otherwise."""
    for pattern, _ in _AXIS_PHRASES:
        found = pattern.search(lowered)
        if found:
            letter = (found.group(1) or found.group(2) or "z").upper()
            return f"+{letter}", {"X": 0, "Y": 1, "Z": 2}[letter]
    if re.search(r"\bfrom\s+the\s+(?:front|back)\b|\balong\s+y\b", lowered):
        return "+Y", 1
    if re.search(r"\bfrom\s+the\s+(?:left|right|side)\b|\balong\s+x\b", lowered):
        return "+X", 0
    return "+Z", 2


_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "twelve": 12}
#: "4 holes", "4 x 6mm holes", "four 6 mm holes".
#:
#: The negative lookahead is the whole difficulty: in "a 10 mm hole" the 10 is
#: a DIAMETER, and a pattern that allowed a unit between the number and the
#: noun would read it as a count of ten. A count is a bare number.
#:
#: The trailing word boundary on the digits matters too: without it the
#: engine backtracks "10" to "1", finds no unit after the "1", and
#: reports a count of one. A partially consumed number is never a count.
_COUNT = re.compile(
    r"\b(\d+)\b(?!\s*(?:mm|millimet|[ø⌀]))\s*(?:x\s+)?"
    r"(?=(?:[\w.]+\s+){0,3}holes?\b)"
    r"|\b(" + "|".join(_COUNT_WORDS) + r")\s+(?:[\w.]+\s+){0,3}?holes?\b",
    re.I)


def _count_word(lowered: str) -> Optional[int]:
    found = _COUNT.search(lowered)
    if not found:
        return None
    if found.group(1):
        return int(found.group(1))
    return _COUNT_WORDS[found.group(2).lower()]


# --- edit: holes at the corners ---------------------------------------------

_CORNERS = re.compile(r"\bcorners?\b", re.I)
_INSET = re.compile(
    rf"{_N}{_MM}\s*(?:in\s+)?(?:from\s+(?:the\s+)?edges?|inset|margin|in\s+from)",
    re.I)

#: How far a corner hole sits from each edge when the request does not say.
#: A convention, reported as one -- not a silent default, because a bolt
#: pattern whose inset nobody chose is a bolt pattern nobody can drill.
DEFAULT_INSET_MM = 10.0


def read_corner_holes(text: str,
                      plan: Optional[Mapping[str, Any]]) -> Optional[Reading]:
    """"add four holes at the corners" -- one bore near each corner in plan.

    Four, and only four: "corners" of a rectangular face means four, and a
    request for three or five corner holes is not a sentence with one meaning.
    """
    if plan is None:
        return None
    lowered = (text or "").lower()
    if not _CORNERS.search(lowered) or not _HOLE_WORD.search(lowered):
        return None
    count = _count_word(lowered)
    if count not in (None, 4):
        raise ReadingError(
            f"{count} holes do not go at the corners of a rectangle; four do")
    body = _body_id(plan)
    envelope = _envelope(plan)
    if body is None or envelope is None:
        return None
    diameter_match = _DIAMETER.search(lowered)
    if diameter_match is None:
        raise ReadingError(
            "corner holes need a diameter, and the request does not give one")
    diameter = _positive(_first_number(diameter_match), "diameter")

    inset_match = _INSET.search(lowered)
    inset = (_positive(_first_number(inset_match), "inset")
             if inset_match else DEFAULT_INSET_MM)
    lo, hi = envelope
    span_x, span_y = hi[0] - lo[0], hi[1] - lo[1]
    if 2 * inset + diameter >= min(span_x, span_y):
        raise ReadingError(
            f"four {diameter:g} mm holes inset {inset:g} mm do not fit on a "
            f"{span_x:g} x {span_y:g} mm face")

    corners = [
        (lo[0] + inset, lo[1] + inset), (hi[0] - inset, lo[1] + inset),
        (lo[0] + inset, hi[1] - inset), (hi[0] - inset, hi[1] - inset),
    ]
    added = []
    for index, (x, y) in enumerate(corners, start=1):
        added.append({
            "id": _unique({"operations": _operations(plan) + added},
                          f"corner{index}"),
            "type": THROUGH_HOLE, "target": body,
            "parameters": {
                "diameter": diameter,
                "position": {"x": x, "y": y, "z": lo[2]},
                "axis": "+Z",
            },
        })
    summary = (f"{plan.get('summary') or 'the part'}, with four {diameter:g} mm "
               f"corner holes")
    assumptions = [f"the holes run along +Z, {inset:g} mm in from each edge"]
    if inset_match is None:
        assumptions.append(
            f"no inset was given, so {DEFAULT_INSET_MM:g} mm was used")
    return Reading(
        plan=_plan_with(plan, *added, summary=summary),
        summary=summary, reader="corner_holes",
        assumptions=tuple(assumptions),
    )


# --- edit: an edge treatment -------------------------------------------------

_FILLET_WORD = re.compile(r"\b(?:fillet|round(?:ed|ing)?|radius)\b", re.I)
_CHAMFER_WORD = re.compile(r"\b(?:chamfer|bevel|break)\b", re.I)

#: The edge families this reader is willing to name, and the selector each
#: means. Everything else declines -- notably "the top edges" of a plate, whose
#: four edges run along TWO axes and cannot be one selector at all. Emitting
#: an approximation there would round the wrong metal.
_EDGE_FAMILIES = (
    (re.compile(r"\b(?:outside\s+)?(?:vertical|upright)\s+edges?\b"
                r"|\b(?:outer|outside)\s+corners?\b|\bcorners?\s+of\s+the\s+"
                r"(?:plate|block|box|part)\b", re.I),
     {"select": SELECT_STRAIGHT, "axis": "Z"},
     "the four vertical corner edges"),
    (re.compile(r"\b(?:the\s+)?(?:long|lengthwise)\s+edges?\b", re.I),
     {"select": SELECT_STRAIGHT, "axis": "X"},
     "the edges running along X"),
    # These three are described as what the SELECTOR does, not as what the
    # sentence said. `circular Z top` means "every circular edge about Z at
    # the top end" -- on a plate that has been filleted, that is the fillet
    # arcs as well as the bores, and a summary promising "the bore" would be
    # a false receipt for a part that also had its corner arcs broken.
    (re.compile(r"\b(?:top\s+(?:of\s+the\s+)?(?:bore|hole)|top\s+rim)\b", re.I),
     {"select": SELECT_CIRCULAR, "axis": "Z", "position": "top"},
     "every circular edge about Z at the top"),
    (re.compile(r"\b(?:bottom\s+(?:of\s+the\s+)?(?:bore|hole)|bottom\s+rim)\b",
                re.I),
     {"select": SELECT_CIRCULAR, "axis": "Z", "position": "bottom"},
     "every circular edge about Z at the bottom"),
    (re.compile(r"\b(?:edges?\s+of\s+the\s+(?:holes?|bores?)|hole\s+edges?"
                r"|rims?)\b", re.I),
     {"select": SELECT_CIRCULAR, "axis": "Z"},
     "every circular edge about Z, both ends"),
)

_AMOUNT = re.compile(
    rf"(?:\bto\b|\bby\b|\bof\b|\bwith\b)?\s*{_N}{_MM}\s*"
    rf"(?:radius|chamfer|bevel|fillet)?", re.I)


def read_edge_treatment(text: str,
                        plan: Optional[Mapping[str, Any]]) -> Optional[Reading]:
    """"round the outside vertical edges to 2 mm" -- a fillet or a chamfer.

    Only the edge families above, and only when the request names one. A
    treatment whose edges this reader cannot name is left to the model, which
    can ask what was meant.
    """
    if plan is None:
        return None
    lowered = (text or "").lower()
    wants_fillet = bool(_FILLET_WORD.search(lowered))
    wants_chamfer = bool(_CHAMFER_WORD.search(lowered))
    if wants_fillet == wants_chamfer:
        return None            # neither, or an ambiguous "round and bevel"
    family = next(((selector, describe) for pattern, selector, describe
                   in _EDGE_FAMILIES if pattern.search(lowered)), None)
    if family is None:
        return None
    selector, describe = family
    body = _body_id(plan)
    if body is None:
        return None
    amount_match = _AMOUNT.search(lowered)
    if amount_match is None:
        raise ReadingError(
            "an edge treatment needs a size, and the request does not give one")
    amount = _positive(_first_number(amount_match), "size")

    circular = selector.get("select") == SELECT_CIRCULAR
    named_a_bore = bool(re.search(r"\b(?:bore|hole)s?\b", lowered))
    rounded_already = any(op.get("type") in (FILLET, CHAMFER)
                          for op in _operations(plan))

    kind = FILLET if wants_fillet else CHAMFER
    key = "radius" if wants_fillet else "distance"
    identifier = _unique(plan, "round" if wants_fillet else "break")
    verb = "rounded" if wants_fillet else "chamfered"
    summary = (f"{plan.get('summary') or 'the part'}, with {describe} "
               f"{verb} {amount:g} mm")
    return Reading(
        plan=_plan_with(plan, {
            "id": identifier, "type": kind, "target": body,
            "parameters": {key: amount, "edges": dict(selector)},
        }, summary=summary),
        summary=summary, reader="edge_treatment",
        assumptions=tuple(
            [f"read \"{describe}\" as the selector "
             f"{selector.get('select')}"
             + (f" about {selector['axis']}" if "axis" in selector else "")
             + (f", {selector['position']} end"
                if "position" in selector else "")]
            + ([
                "this part already has rounded or chamfered edges, and a "
                "circular selector takes their arcs too -- not only the bores"
            ] if circular and named_a_bore and rounded_already else [])
        ),
    )


# --- edit: resize the body ---------------------------------------------------

#: Words for a direction, and the axis each conventionally means.
#:
#: This is a CONVENTION, not a certainty: "wider" is X on almost every
#: drawing and is not guaranteed to be. Every reading made here is reported in
#: the summary so the person can see which axis moved and say otherwise.
_GROW_WORDS = (
    (re.compile(r"\b(?:wider|narrower|width)\b", re.I), 0, "X"),
    (re.compile(r"\b(?:deeper|shallower|longer|shorter|depth|length)\b", re.I),
     1, "Y"),
    (re.compile(r"\b(?:taller|thicker|thinner|higher|height|thickness)\b",
                re.I), 2, "Z"),
    (re.compile(r"\bin\s+(?:the\s+)?x\b|\balong\s+x\b", re.I), 0, "X"),
    (re.compile(r"\bin\s+(?:the\s+)?y\b|\balong\s+y\b", re.I), 1, "Y"),
    (re.compile(r"\bin\s+(?:the\s+)?z\b|\balong\s+z\b", re.I), 2, "Z"),
)
_SHRINKS = re.compile(
    r"\b(?:narrower|shallower|shorter|thinner|smaller|less|reduce)\b", re.I)
_RESIZE_VERB = re.compile(
    r"\b(?:make|grow|shrink|increase|decrease|reduce|extend|set)\b", re.I)
_ABSOLUTE = re.compile(rf"\b(?:to|exactly)\s+{_N}{_MM}", re.I)


def read_resize(text: str,
                plan: Optional[Mapping[str, Any]]) -> Optional[Reading]:
    """"make it 20 mm taller" -- change one dimension of the body.

    Relative ("20 mm taller") and absolute ("set the height to 30 mm") both
    read; anything that names two directions at once declines, because
    "wider and taller by 10" does not say whether that is one number or two.
    """
    if plan is None:
        return None
    lowered = (text or "").lower()
    if not _RESIZE_VERB.search(lowered):
        return None
    hits = [(index, letter) for pattern, index, letter in _GROW_WORDS
            if pattern.search(lowered)]
    distinct = {index for index, _ in hits}
    if len(distinct) != 1:
        return None
    index, letter = hits[0]

    body = _body_id(plan)
    if body is None:
        return None
    operation = next((op for op in _operations(plan)
                      if op.get("id") == body), None)
    if operation is None or operation.get("type") != BOX:
        return None            # a cylinder's "width" is its diameter; later
    parameters = dict(operation.get("parameters") or {})
    key = ("x", "y", "z")[index]
    current = float(parameters.get(key, 0.0))

    absolute = _ABSOLUTE.search(lowered)
    if absolute:
        target = _positive(float(absolute.group(1)), "dimension")
        how = f"set {letter} to {target:g} mm (was {current:g} mm)"
    else:
        amount_match = re.search(rf"{_N}{_MM}", lowered)
        if amount_match is None:
            raise ReadingError(
                "a resize needs an amount, and the request does not give one")
        delta = _positive(float(amount_match.group(1)), "amount")
        if _SHRINKS.search(lowered):
            delta = -delta
        target = current + delta
        if target <= 0:
            raise ReadingError(
                f"that would leave {letter} at {target:g} mm, which is not a "
                "size")
        how = f"{'grew' if delta > 0 else 'shrank'} {letter} to {target:g} mm"

    out = copy.deepcopy(dict(plan))
    for op in out["operations"]:
        if op.get("id") == body:
            op["parameters"] = {**parameters, key: target}
    summary = f"{plan.get('summary') or 'the part'} -- {how}"
    out["summary"] = summary
    return Reading(plan=out, summary=summary, reader="resize",
                   assumptions=(f"read the direction as {letter}", how))


# --- edit: remove the last feature -------------------------------------------

_REMOVE = re.compile(
    r"\b(?:remove|delete|drop|get\s+rid\s+of|take\s+(?:out|off|away))\b", re.I)
_LAST = re.compile(r"\b(?:last|latest|most\s+recent|that)\b", re.I)

#: Which noun names which operation type, for a removal.
_REMOVABLE = (
    (re.compile(r"\bfillets?\b|\brounds?\b", re.I), (FILLET,), "fillet"),
    (re.compile(r"\bchamfers?\b|\bbevels?\b", re.I), (CHAMFER,), "chamfer"),
    (re.compile(r"\bholes?\b|\bbores?\b", re.I), (THROUGH_HOLE,), "hole"),
    (re.compile(r"\bpatterns?\b", re.I), (PATTERN,), "pattern"),
)


def read_remove(text: str,
                plan: Optional[Mapping[str, Any]]) -> Optional[Reading]:
    """"remove the last fillet" -- drop one named feature from the plan.

    Removes exactly one operation, the LAST of the named kind, because that is
    the only one a bare "the last fillet" can mean. "Remove all the holes"
    declines: dropping several operations at once on a regex's say-so is how a
    part loses a feature nobody noticed.
    """
    if plan is None:
        return None
    lowered = (text or "").lower()
    if not _REMOVE.search(lowered):
        return None
    if re.search(r"\ball\b|\bevery\b|\bboth\b", lowered):
        return None
    family = next(((kinds, noun) for pattern, kinds, noun in _REMOVABLE
                   if pattern.search(lowered)), None)
    if family is None:
        return None
    kinds, noun = family
    candidates = [op for op in _operations(plan) if op.get("type") in kinds]
    if not candidates:
        raise ReadingError(f"there is no {noun} on this part to remove")
    if len(candidates) > 1 and not _LAST.search(lowered):
        raise ReadingError(
            f"there are {len(candidates)} {noun}s; say which one, or "
            f"\"the last {noun}\"")
    doomed = candidates[-1]
    doomed_id = str(doomed.get("id"))

    # A feature other operations depend on cannot simply be dropped. The
    # plan's own references are the authority on that, not a guess.
    depending = [op for op in _operations(plan)
                 if op is not doomed
                 and (op.get("target") == doomed_id
                      or op.get("source") == doomed_id
                      or doomed_id in (op.get("tools") or []))]
    if depending:
        raise ReadingError(
            f"{doomed_id} cannot be removed: "
            f"{', '.join(str(op.get('id')) for op in depending)} "
            f"still refer{'s' if len(depending) == 1 else ''} to it")

    out = copy.deepcopy(dict(plan))
    out["operations"] = [op for op in out["operations"] if op is not doomed
                         and op.get("id") != doomed_id]
    if not out["operations"]:
        raise ReadingError("removing that would leave no part at all")
    summary = f"{plan.get('summary') or 'the part'}, without {doomed_id}"
    out["summary"] = summary
    return Reading(plan=out, summary=summary, reader="remove",
                   assumptions=(f"removed the last {noun}, {doomed_id!r}",))


# --- edit: repeat a feature (Phase 4, deterministic patterning) --------------
#
# The point of reading these locally is schema economics. Teaching a provider
# to emit a `pattern` correctly costs grammar budget the schema does not have
# to spare -- the ceiling is eight operation branches and the vocabulary
# already fills it. A sentence like "four holes on a 40 mm bolt circle" has
# exactly one meaning, so it does not need the model's judgement at all, and
# reading it here leaves the provider schema untouched.

_REPEAT = re.compile(
    r"\b(?:repeat|copies|copy|pattern|array|every|spaced|apart|pitch"
    r"|bolt\s+circle|around)\b", re.I)
_SPACING = re.compile(
    rf"{_N}{_MM}\s*(?:apart|spacing|pitch|between)"
    rf"|(?:spaced|apart|pitch|every)\s*(?:by\s*)?{_N}{_MM}", re.I)
_TIMES = re.compile(
    r"\b(\d+)\s*(?:times|copies|instances|off)\b"
    r"|\b(" + "|".join(_COUNT_WORDS) + r")\s+(?:times|copies|instances)\b",
    re.I)
_BOLT_CIRCLE = re.compile(
    rf"(?:on|around)\s+(?:a\s+)?{_N}{_MM}\s*(?:diameter\s+)?"
    rf"(?:bolt\s+circle|pcd|pitch\s+circle)"
    rf"|bolt\s+circle\s+(?:of\s+)?{_N}{_MM}", re.I)
_RADIAL = re.compile(
    r"\b(?:around|about|radial(?:ly)?|bolt\s+circle|pcd|circle)\b", re.I)
_LINEAR_AXIS = re.compile(
    r"\balong\s+(?:the\s+)?([xyz])\b|\bin\s+(?:a\s+)?(?:row|line)\b", re.I)

#: The most instances one sentence may ask for. The plan's own P28 caps a
#: pattern at 64; this is the same number said here so the refusal is about
#: the request rather than about a rule code.
MAX_INSTANCES = 64


def read_pattern(text: str,
                 plan: Optional[Mapping[str, Any]]) -> Optional[Reading]:
    """"four holes on a 40 mm bolt circle" / "3 holes 20 mm apart".

    Repeats the plan's LAST through-hole, which is the only feature this
    vocabulary can pattern today. The source is instance zero and stays where
    it is -- so "four holes" means the one that exists plus three more, and a
    count of four produces four holes rather than five.
    """
    if plan is None:
        return None
    lowered = (text or "").lower()
    if not _REPEAT.search(lowered):
        return None

    sources = _of_type(plan, THROUGH_HOLE)
    if not sources:
        return None            # nothing to repeat; let the model read it
    source = sources[-1]
    source_id = str(source.get("id"))

    count = _count_word(lowered)
    if count is None:
        times = _TIMES.search(lowered)
        if times:
            count = (int(times.group(1)) if times.group(1)
                     else _COUNT_WORDS[times.group(2).lower()])
    if count is None:
        return None
    if not 2 <= count <= MAX_INSTANCES:
        raise ReadingError(
            f"a pattern repeats between 2 and {MAX_INSTANCES} times; "
            f"{count} was asked for")

    circle = _BOLT_CIRCLE.search(lowered)
    if circle is not None or (_RADIAL.search(lowered)
                              and _SPACING.search(lowered) is None):
        return _radial_pattern(plan, source, source_id, count, circle, lowered)
    return _linear_pattern(plan, source, source_id, count, lowered)


def _radial_pattern(plan, source, source_id, count, circle, lowered):
    """Instances spread evenly about the part's centre."""
    envelope = _envelope(plan)
    if envelope is None:
        return None
    lo, hi = envelope
    centre = {"x": (lo[0] + hi[0]) / 2.0, "y": (lo[1] + hi[1]) / 2.0,
              "z": lo[2]}
    parameters = dict(source.get("parameters") or {})
    axis = str(parameters.get("axis", "+Z"))

    assumptions = [f"spread evenly about {axis} through the part's centre"]
    plan_out = plan
    if circle is not None:
        # A bolt circle states the DIAMETER the holes sit on, so the source
        # must first be moved onto that circle. Moving it is part of the
        # reading, not a separate request, and is reported as such.
        diameter = _positive(_first_number(circle), "bolt circle diameter")
        radius = diameter / 2.0
        span = min(hi[0] - lo[0], hi[1] - lo[1])
        bore = float(parameters.get("diameter", 0.0))
        if diameter + bore >= span:
            raise ReadingError(
                f"a {diameter:g} mm bolt circle of {bore:g} mm holes does not "
                f"fit on a {span:g} mm face")
        plan_out = copy.deepcopy(dict(plan))
        for op in plan_out["operations"]:
            if op.get("id") == source_id:
                moved = dict(op.get("parameters") or {})
                position = dict(moved.get("position") or {})
                position["x"] = centre["x"] + radius
                position["y"] = centre["y"]
                moved["position"] = position
                op["parameters"] = moved
        assumptions.append(
            f"moved {source_id!r} onto the {diameter:g} mm circle first")

    identifier = _unique(plan_out, f"{source_id}s")
    summary = (f"{plan.get('summary') or 'the part'}, with {source_id!r} "
               f"repeated {count} times about the centre")
    return Reading(
        plan=_plan_with(plan_out, {
            "id": identifier, "type": PATTERN, "source": source_id,
            "parameters": {
                "count": count,
                "placement": {"kind": "radial", "axis": axis,
                              "centre": centre},
            },
        }, summary=summary),
        summary=summary, reader="pattern_radial",
        assumptions=tuple(assumptions + [
            f"the count includes {source_id!r} itself, so there are {count} "
            f"holes in total"]),
    )


def _linear_pattern(plan, source, source_id, count, lowered):
    """Instances in a row at a fixed pitch."""
    spacing_match = _SPACING.search(lowered)
    if spacing_match is None:
        raise ReadingError(
            "a row of holes needs a spacing, and the request does not give one")
    spacing = _positive(_first_number(spacing_match), "spacing")

    letter = "X"
    found = _LINEAR_AXIS.search(lowered)
    if found and found.group(1):
        letter = found.group(1).upper()
    axis = f"+{letter}"
    index = {"X": 0, "Y": 1, "Z": 2}[letter]

    # The last instance must still land on the part. The plan says where the
    # source is and how big the stock is, so this is checkable before anything
    # is built rather than as a kernel failure afterwards.
    envelope = _envelope(plan)
    parameters = dict(source.get("parameters") or {})
    position = dict(parameters.get("position") or {})
    if envelope is not None and position:
        lo, hi = envelope
        start = float(position.get(("x", "y", "z")[index], 0.0))
        end = start + spacing * (count - 1)
        bore = float(parameters.get("diameter", 0.0)) / 2.0
        if end + bore > hi[index] or end - bore < lo[index]:
            raise ReadingError(
                f"{count} holes {spacing:g} mm apart run to {end:g} mm, off "
                f"the end of a part that spans "
                f"{lo[index]:g} to {hi[index]:g} mm")

    identifier = _unique(plan, f"{source_id}s")
    summary = (f"{plan.get('summary') or 'the part'}, with {source_id!r} "
               f"repeated {count} times {spacing:g} mm apart along {axis}")
    return Reading(
        plan=_plan_with(plan, {
            "id": identifier, "type": PATTERN, "source": source_id,
            "parameters": {
                "count": count,
                "placement": {"kind": "linear", "axis": axis,
                              "spacing": spacing},
            },
        }, summary=summary),
        summary=summary, reader="pattern_linear",
        assumptions=(
            f"read the direction as {axis}",
            f"the count includes {source_id!r} itself, so there are {count} "
            f"holes in total",
        ),
    )


# --- the registry ------------------------------------------------------------

#: Every reader, in the order they are offered a request.
#:
#: Order is deliberate and narrow-first. A sentence that creates a part is
#: tried before one that edits it, and a specific edit before a general one --
#: "four holes at the corners" must not be read as "a hole" by a reader that
#: only noticed the word "hole". Each reader is written to decline what is not
#: its sentence, so the order is a tie-break rather than a dispatch table, but
#: relying on that alone would be relying on every reader being perfect.
READERS: Tuple[Tuple[str, Callable[..., Optional[Reading]]], ...] = (
    ("box", read_box),
    ("cylinder", read_cylinder),
    ("corner_holes", read_corner_holes),
    ("pattern", read_pattern),
    ("centre_hole", read_centre_hole),
    ("edge_treatment", read_edge_treatment),
    ("remove", read_remove),
    ("resize", read_resize),
)


@dataclass(frozen=True)
class Refusal:
    """The grammar recognised the request and cannot honour it.

    Carried separately from "no reader understood this" because the two want
    opposite handling: a refusal is an answer to give the person, and a
    non-understanding should go to the model.
    """

    reason: str
    reader: str


def read_request(
    text: str, plan: Optional[Mapping[str, Any]] = None,
) -> Optional[Any]:
    """Read a request deterministically, or answer ``None``.

    Returns a :class:`Reading` when one reader understood the request
    completely, a :class:`Refusal` when a reader recognised it and it cannot
    be done, and ``None`` when no reader claimed it -- which is the signal to
    ask a model.

    ``None`` is the safe direction and the common one. The worst case of
    returning ``None`` is a model call that was not strictly needed; the worst
    case of the opposite is a part built from a sentence nobody understood.
    """
    if not text or not text.strip():
        return None
    if mentions_foreign_units(text):
        return Refusal(
            reason=("this system works in millimetres only, and the request "
                    "states a length in another unit; give the dimensions in "
                    "mm and it will be built"),
            reader="units",
        )
    for name, reader in READERS:
        try:
            reading = reader(text, plan)
        except ReadingError as exc:
            return Refusal(reason=str(exc), reader=name)
        if reading is not None:
            return reading
    return None


def describe_readers() -> List[Dict[str, str]]:
    """What the deterministic layer can read, for the health surface.

    Published so the product can say what it understands without a model,
    rather than leaving a person to discover it by trial.
    """
    return [
        {"reader": name,
         "reads": (fn.__doc__ or "").strip().split("\n")[0]}
        for name, fn in READERS
    ]


__all__ = [
    "DEFAULT_INSET_MM",
    "MAX_ADDED_OPERATIONS",
    "MAX_INSTANCES",
    "MAX_MM",
    "READERS",
    "Reading",
    "ReadingError",
    "Refusal",
    "describe_readers",
    "mentions_foreign_units",
    "read_box",
    "read_centre_hole",
    "read_corner_holes",
    "read_cylinder",
    "read_edge_treatment",
    "read_pattern",
    "read_remove",
    "read_request",
    "read_resize",
]
