"""Provider-neutral canonical intent, and its lowering to an Operation Plan.

Where this sits
---------------
```
natural language
  -> AI provider            (Anthropic today; a local model later)
  -> ProviderResult         provider-independent
  -> canonical intent       THIS MODULE
  -> Operation Plan         THIS MODULE lowers to it
  -> validation -> graph -> backend -> FreeCAD / CadQuery
```

**Nothing in this module knows which model produced anything.** There is no
provider name, no model name, no prompt, no schema and no vendor field
anywhere in it, and no branch on any of those. That is the whole point: the
CAD semantics live here, below the provider boundary, so replacing Anthropic
with a local model changes the layer above and nothing else.

Two ways in, one way out
------------------------
A provider may return a usable plan, in which case none of this runs. When it
does not, :func:`extract_intent` reads the request directly against a narrow,
explicit grammar. Both routes produce the *same* canonical intent, and the
intent is then lowered, validated and executed by the ordinary pipeline --
deterministic extraction is not a shortcut around validation, it is another
way of arriving at the same IR.

What it deliberately does not do
--------------------------------
It does not build geometry, call a kernel, or emit anything a backend
understands. It emits an Operation Plan, which is the contract everything
downstream already speaks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: The one assembly shape this module understands today. Named in the intent
#: so a second one can be added without changing what this one means.
PLATE_ASSEMBLY = "plate_assembly"
HOLLOW_BOX = "hollow_rectangular_box"

CENTER = "center"

#: A rectangular box has six faces. A constant because it is the reason a
#: plate count is checked at all, not an arbitrary limit.
BOX_FACES = 6

MAX_PLATES = 24
MAX_DIMENSION_MM = 5_000.0


class IntentError(ValueError):
    """A request that cannot become a valid intent, and precisely why."""


@dataclass(frozen=True)
class PlateGroup:
    """``count`` plates of one size. ``thickness`` may be inherited."""

    width: float
    height: float
    count: int
    thickness: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"width": self.width, "height": self.height,
                                   "count": self.count}
        if self.thickness is not None:
            payload["thickness"] = self.thickness
        return payload


@dataclass(frozen=True)
class HoleSpec:
    """One hole per plate, at the plate's centre, all the way through."""

    diameter: float
    position: str = CENTER
    count_per_plate: int = 1
    through: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"diameter": self.diameter, "position": self.position,
                "count_per_plate": self.count_per_plate,
                "through": self.through}


@dataclass(frozen=True)
class PlateAssemblyIntent:
    """A hollow enclosure built from plates. Provider-neutral by construction.

    Every field is a number, a count or a fixed word from this module's own
    vocabulary. There is nothing here that only one model could have produced,
    and nothing downstream needs to know where it came from.
    """

    kind: str = PLATE_ASSEMBLY
    container: str = HOLLOW_BOX
    plate_groups: Tuple[PlateGroup, ...] = ()
    hole: Optional[HoleSpec] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "kind": self.kind,
            "container": self.container,
            "plate_groups": [g.to_dict() for g in self.plate_groups],
        }
        if self.hole is not None:
            payload["hole"] = self.hole.to_dict()
        return payload

    @property
    def total_plates(self) -> int:
        return sum(group.count for group in self.plate_groups)


# --- reading a request ------------------------------------------------------

#: "40*20*5", "40 x 20 x 5", "40x20x5" -- three numbers, any common separator.
_TRIPLE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)\s*[*x×]\s*"
    r"(\d+(?:\.\d+)?)", re.I)
#: "20*20", "20 x 20" -- two numbers, thickness left to inherit.
_PAIR = re.compile(
    r"(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)", re.I)
#: "(4)plates", "(4) plates", "4 plates"
_COUNT = re.compile(r"\((\d+)\)|\b(\d+)\s*(?:no\.?\s*)?plates?\b", re.I)
#: "8mm diameter", "8 mm holes", "8 mm dia", a leading diameter sign, or
#: "diameter 8". Every common way a drawing states a bore.
_DIAMETER = re.compile(
    r"[ø⌀]\s*(\d+(?:\.\d+)?)"
    r"|\bdia(?:meter)?\.?\s*(?:of\s*)?(\d+(?:\.\d+)?)"
    r"|(\d+(?:\.\d+)?)\s*mm\s*(?:dia(?:meter)?|(?:through\s+)?holes?)",
    re.I)

#: A request has to say it is an enclosure before this grammar will read it.
_ASSEMBLY_WORDS = ("hollow", "box", "enclosure", "casing", "housing")


def looks_like_plate_assembly(text: str) -> bool:
    """Whether the narrow grammar below is worth trying at all.

    Deliberately strict. A request this does not recognise is left to the
    provider rather than forced through a grammar that was not written for
    it -- guessing here would be worse than not answering.
    """
    lowered = (text or "").lower()
    if "plate" not in lowered:
        return False
    if not any(word in lowered for word in _ASSEMBLY_WORDS):
        return False
    return bool(_PAIR.search(lowered))


def _diameter(text: str) -> Optional[float]:
    match = _DIAMETER.search(text)
    if not match:
        return None
    for group in match.groups():
        if group:
            return float(group)
    return None


def _count_after(text: str, position: int) -> int:
    """How many plates the size just read applies to. Defaults to one."""
    window = text[position:position + 40]
    match = _COUNT.search(window)
    if match:
        return int(match.group(1) or match.group(2))
    return 1


def extract_intent(text: str) -> PlateAssemblyIntent:
    """Read a plate-assembly request into canonical intent.

    Deterministic, local, and not a model call. It exists so this shape of
    request survives a provider that answered badly -- today's hosted model
    or tomorrow's local one, without distinction.
    """
    if not looks_like_plate_assembly(text):
        raise IntentError("this request is not a recognisable plate assembly")

    lowered = text.lower()
    groups: List[PlateGroup] = []
    inherited: Optional[float] = None
    consumed: List[Tuple[int, int]] = []

    # Sized groups first: "40*20*5" fixes a thickness the later pairs inherit.
    for match in _TRIPLE.finditer(lowered):
        width, height, thickness = (float(g) for g in match.groups())
        if inherited is None:
            inherited = thickness
        groups.append(PlateGroup(width=width, height=height,
                                 count=_count_after(lowered, match.end()),
                                 thickness=thickness))
        consumed.append((match.start(), match.end()))

    # Then the pairs that were not part of a triple.
    for match in _PAIR.finditer(lowered):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        width, height = (float(g) for g in match.groups())
        groups.append(PlateGroup(width=width, height=height,
                                 count=_count_after(lowered, match.end()),
                                 thickness=None))

    if not groups:
        raise IntentError("no plate sizes were given")

    # An omitted thickness inherits the one that WAS stated. Inherited, not
    # defaulted: a plate group with no thickness and nothing to inherit from
    # is refused rather than given a number this module made up.
    resolved: List[PlateGroup] = []
    for group in groups:
        thickness = group.thickness if group.thickness is not None else inherited
        if thickness is None:
            raise IntentError(
                f"the {group.width:g} x {group.height:g} plates have no "
                "thickness, and no other plate states one to inherit")
        resolved.append(PlateGroup(width=group.width, height=group.height,
                                   count=group.count, thickness=thickness))

    diameter = _diameter(lowered)
    intent = PlateAssemblyIntent(
        plate_groups=tuple(resolved),
        hole=HoleSpec(diameter=diameter) if diameter else None)
    validate_intent(intent)
    return intent


def validate_intent(intent: PlateAssemblyIntent) -> None:
    """Refuse an intent that could not become a sensible part.

    Checked here, before any plan exists, so a bad request fails with a
    sentence about plates rather than a rule code about operations.
    """
    if intent.kind != PLATE_ASSEMBLY:
        raise IntentError(f"unknown intent kind {intent.kind!r}")
    if intent.container != HOLLOW_BOX:
        raise IntentError(f"unknown container {intent.container!r}")
    if not intent.plate_groups:
        raise IntentError("a plate assembly needs at least one plate group")

    for group in intent.plate_groups:
        for label, value in (("width", group.width), ("height", group.height),
                             ("thickness", group.thickness)):
            if value is None or value <= 0:
                raise IntentError(f"a plate {label} must be positive")
            if value > MAX_DIMENSION_MM:
                raise IntentError(
                    f"a plate {label} of {value:g} mm is beyond what this "
                    "supports")
        if group.count <= 0:
            raise IntentError("a plate group needs a positive count")

    total = intent.total_plates
    if total > MAX_PLATES:
        raise IntentError(
            f"{total} plates is more than this supports ({MAX_PLATES})")
    if total != BOX_FACES:
        raise IntentError(
            f"a hollow rectangular box is closed by {BOX_FACES} plates; this "
            f"describes {total}")

    if intent.hole is not None:
        if intent.hole.diameter <= 0:
            raise IntentError("a hole diameter must be positive")
        if intent.hole.count_per_plate != 1:
            raise IntentError(
                "this supports one hole per plate; "
                f"{intent.hole.count_per_plate} were asked for")
        if intent.hole.position != CENTER:
            raise IntentError(
                f"a hole position of {intent.hole.position!r} is not "
                "supported; only 'center' is")
        smallest = min(min(g.width, g.height) for g in intent.plate_groups)
        if intent.hole.diameter >= smallest:
            raise IntentError(
                f"a {intent.hole.diameter:g} mm hole does not fit in a "
                f"{smallest:g} mm plate")


# --- lowering to the canonical Operation Plan -------------------------------


@dataclass(frozen=True)
class Panel:
    """One plate, placed. An intermediate: never stored, never exported."""

    name: str
    size: Tuple[float, float, float]
    position: Tuple[float, float, float]


@dataclass(frozen=True)
class Bore:
    """One through-hole, and the plates its centreline passes through.

    A hole belongs to an *axis*, not to a plate, and that is geometry rather
    than a shortcut: the bottom and the top plate sit on one centreline, so a
    single through-hole drills both. Asking for two would put the second on a
    line the first had already emptied, and a hole that meets no material is
    an error here (rule E1), not a quiet no-op.
    """

    name: str
    axis: str
    at: Tuple[float, float, float]
    plates: Tuple[str, ...]


#: Which coordinate each axis runs along, for the containment check below.
_AXIS_INDEX = {"+X": 0, "+Y": 1, "+Z": 2}


def _enclosure(intent: PlateAssemblyIntent) -> Tuple[List[Panel], List[Bore]]:
    """Place the plates as a closed rectangular shell, and site the holes.

    The plate sizes fix the enclosure. The larger group is the pair of faces
    whose outline is ``width x height``; the smaller group closes the ends,
    and one of its two dimensions must equal the enclosure's depth -- which is
    what makes the third dimension readable rather than guessed.

    Plates meet by overlapping at the corners, so the fuse leaves one solid.
    """
    groups = sorted(intent.plate_groups,
                    key=lambda g: g.width * g.height, reverse=True)
    large = groups[0]
    t = float(large.thickness or 0.0)

    x = float(large.width)
    y = float(large.height)

    if len(groups) == 1:
        # Six identical plates can only close a cube.
        if abs(x - y) > 1e-9:
            raise IntentError(
                f"six identical {x:g} x {y:g} mm plates cannot close a box; "
                "one plate size only works when it is square")
        z = x
    else:
        small = groups[-1]
        if abs(float(small.width) - y) <= 1e-9:
            z = float(small.height)
        elif abs(float(small.height) - y) <= 1e-9:
            z = float(small.width)
        else:
            raise IntentError(
                f"the {small.width:g} x {small.height:g} mm end plates do not "
                f"match the {y:g} mm depth of the {x:g} x {y:g} mm plates, so "
                "they cannot close that box")

    if 2 * t >= min(x, y, z):
        raise IntentError(
            f"{t:g} mm plates are too thick to leave a cavity in a "
            f"{x:g} x {y:g} x {z:g} mm box")

    panels = [
        # bottom and top -- the full footprint, normal to Z
        Panel("bottom", (x, y, t), (0.0, 0.0, 0.0)),
        Panel("top", (x, y, t), (0.0, 0.0, z - t)),
        # front and back -- full width, normal to Y
        Panel("front", (x, t, z), (0.0, 0.0, 0.0)),
        Panel("back", (x, t, z), (0.0, y - t, 0.0)),
        # left and right -- the ends, normal to X
        Panel("left", (t, y, z), (0.0, 0.0, 0.0)),
        Panel("right", (t, y, z), (x - t, 0.0, 0.0)),
    ]

    # Each centreline is centred in the two plates it pierces and clear of the
    # other four: the Z line sits at y = y/2, well inside the front and back
    # plates, and correspondingly for the other two.
    bores = [
        Bore("hole_z", "+Z", (x / 2, y / 2, 0.0), ("bottom", "top")),
        Bore("hole_y", "+Y", (x / 2, 0.0, z / 2), ("front", "back")),
        Bore("hole_x", "+X", (0.0, y / 2, z / 2), ("left", "right")),
    ]
    return panels, bores


def _holes_fit(intent: PlateAssemblyIntent, panels: Sequence[Panel],
               bores: Sequence[Bore]) -> None:
    """Refuse a hole that would break out of the plate it is centred in.

    Checked against the *placed* plates rather than the stated sizes, because
    the enclosure's third dimension only exists once they are placed.
    """
    if intent.hole is None:
        return
    radius = intent.hole.diameter / 2.0
    by_name = {panel.name: panel for panel in panels}
    for bore in bores:
        drilled = _AXIS_INDEX[bore.axis]
        for name in bore.plates:
            panel = by_name[name]
            for index in range(3):
                if index == drilled:
                    continue  # the hole runs through this one by design
                low = panel.position[index]
                high = low + panel.size[index]
                centre = bore.at[index]
                if centre - radius < low - 1e-9 or centre + radius > high + 1e-9:
                    raise IntentError(
                        f"a {intent.hole.diameter:g} mm hole centred in the "
                        f"{name} plate would break out of it")


def lower_to_plan(intent: PlateAssemblyIntent, *,
                  summary: Optional[str] = None) -> Dict[str, Any]:
    """Turn canonical intent into a canonical Operation Plan.

    The plan uses operations the language already has, plus the one ``union``
    a multi-plate body requires. Every plate is a ``box``; they are fused into
    a single solid; the holes are then drilled through that solid, which is
    why they come after the union rather than into loose plates.
    """
    validate_intent(intent)
    panels, bores = _enclosure(intent)
    _holes_fit(intent, panels, bores)

    operations: List[Dict[str, Any]] = [
        {
            "id": panel.name,
            "type": "box",
            "parameters": {
                "x": panel.size[0], "y": panel.size[1], "z": panel.size[2],
                "position": {"x": panel.position[0], "y": panel.position[1],
                             "z": panel.position[2]},
            },
        }
        for panel in panels
    ]

    # The first plate's id survives the fuse and names the finished body, so
    # everything after this point targets it.
    body = panels[0].name
    operations.append({
        "id": "shell",
        "type": "union",
        "target": body,
        "tools": [panel.name for panel in panels[1:]],
    })

    if intent.hole is not None:
        for bore in bores:
            operations.append({
                "id": bore.name,
                "type": "through_hole",
                "target": body,
                "parameters": {
                    "diameter": intent.hole.diameter,
                    "position": {"x": bore.at[0], "y": bore.at[1],
                                 "z": bore.at[2]},
                    "axis": bore.axis,
                },
            })

    sizes = ", ".join(
        f"{g.count} x {g.width:g}x{g.height:g}x{g.thickness:g} mm"
        for g in intent.plate_groups)
    holes = ""
    if intent.hole is not None:
        holes = (f", each with a {intent.hole.diameter:g} mm through hole at "
                 "its centre")
    return {
        "status": "generated",
        "summary": summary or f"a hollow plate enclosure ({sizes}){holes}",
        "operations": operations,
    }


def plan_from_request(text: str) -> Tuple[PlateAssemblyIntent, Dict[str, Any]]:
    """The whole deterministic route: request -> intent -> plan.

    Returns both, because a caller that shows only the plan cannot say what it
    understood, and the intent is the explanation.
    """
    intent = extract_intent(text)
    return intent, lower_to_plan(intent)


def intent_from_dict(payload: Mapping[str, Any]) -> PlateAssemblyIntent:
    """Rebuild an intent from its own serialized form.

    Exists so a provider -- any provider -- can return canonical intent
    directly instead of natural language and reach the same lowering.
    """
    groups = tuple(
        PlateGroup(width=float(g["width"]), height=float(g["height"]),
                   count=int(g.get("count", 1)),
                   thickness=(float(g["thickness"])
                              if g.get("thickness") is not None else None))
        for g in (payload.get("plate_groups") or [])
    )
    inherited = next((g.thickness for g in groups if g.thickness is not None),
                     None)
    groups = tuple(
        PlateGroup(width=g.width, height=g.height, count=g.count,
                   thickness=(g.thickness if g.thickness is not None
                              else inherited))
        for g in groups
    )
    raw_hole = payload.get("hole")
    hole = None
    if raw_hole:
        hole = HoleSpec(
            diameter=float(raw_hole["diameter"]),
            position=str(raw_hole.get("position", CENTER)),
            count_per_plate=int(raw_hole.get("count_per_plate", 1)),
            through=bool(raw_hole.get("through", True)),
        )
    intent = PlateAssemblyIntent(
        kind=str(payload.get("kind", PLATE_ASSEMBLY)),
        container=str(payload.get("container", HOLLOW_BOX)),
        plate_groups=groups, hole=hole)
    validate_intent(intent)
    return intent


__all__ = ["BOX_FACES", "CENTER", "HOLLOW_BOX", "PLATE_ASSEMBLY", "Bore",
           "HoleSpec", "IntentError", "Panel", "PlateAssemblyIntent",
           "PlateGroup", "extract_intent", "intent_from_dict",
           "looks_like_plate_assembly", "lower_to_plan", "plan_from_request",
           "validate_intent"]
