"""Answering questions about the current part from evidence, not from a model.

A model cannot see the part. Asked "how many holes are there?", it can only
re-read the plan it was shown and count -- which is what this module does,
without the round trip and without the chance of a different answer each time.
Asked "what is the volume?", a model can only guess, because the volume is
something the CAD kernel measured and nobody told the model.

So: every question this module answers is answered from one of three places,
and **which one is always stated**.

``MEASURED``
    The CAD kernel measured it on the build that actually succeeded. Volume,
    bounding box, face and edge counts, solid count.

``DECLARED``
    The Operation Plan says so. A hole's diameter is declared -- it is what
    was asked for, and the kernel was told it rather than asked.

``CALCULATED``
    Arithmetic on the two, with the working shown. Material removed, mass at
    a given density, the centre of the bounding box.

The distinction is not pedantry. A declared 8 mm hole and a measured 8 mm bore
are different claims: the first says what was requested, the second says what
exists. Reporting either as the other is how a drawing ends up asserting
something nobody checked.

**Nothing here is ever a guess.** A question this module cannot answer from
evidence returns ``None``, and the ordinary path -- the model -- takes it.
Returning ``None`` is the safe direction: the worst case is a model call that
was not strictly needed, where the worst case of the opposite is a fabricated
dimension delivered with the authority of a measurement.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .body_reference import resolve_body
from .history import plan_history
from .parser import PlanParseError, parse_plan

#: Where a number came from. Never omitted, never guessed at.
MEASURED = "measured"
DECLARED = "declared"
CALCULATED = "calculated"

#: The part is stated to be something nobody measured and the plan does not
#: say. There is exactly one of these, and it exists because the honest
#: alternative was worse: the "overall size" of a part made of SEVERAL
#: separate bodies is the box that contains them all, and that box also
#: contains the empty space between them. No kernel measured that box and no
#: operation declared it. Reporting it as MEASURED would assert that the part
#: fills it, which is exactly the class of claim this module exists to keep
#: apart from the ones that are true.
#:
#: It is deliberately NOT a general licence to assume. Every other answer
#: here still comes from the kernel, the plan, or arithmetic on the two.
ASSUMED = "assumed"

PROVENANCE = (MEASURED, DECLARED, CALCULATED, ASSUMED)


@dataclass(frozen=True)
class Answer:
    """One answer, what it is worth, and where it came from."""

    text: str
    provenance: str
    #: The arithmetic, when there was any. Shown so a calculated number can be
    #: checked rather than believed.
    working: Optional[str] = None
    #: What the answer was read out of, named: the backend that measured it,
    #: or the operations that declared it.
    source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"text": self.text,
                                   "provenance": self.provenance}
        if self.working:
            payload["working"] = self.working
        if self.source:
            payload["source"] = self.source
        return payload


# --- reading the evidence ----------------------------------------------------


def _operations(plan: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [op for op in (plan.get("operations") or []) if isinstance(op, Mapping)]


def _of_type(plan: Mapping[str, Any], *kinds: str) -> List[Mapping[str, Any]]:
    wanted = set(kinds)
    return [op for op in _operations(plan) if op.get("type") in wanted]


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def hole_count(plan: Mapping[str, Any]) -> Tuple[int, str]:
    """How many bores the plan declares, and the working.

    A pattern multiplies its source rather than adding to it: ``count``
    INCLUDES the source, so four patterned holes is four holes and not five.
    Getting that wrong by one is the kind of error a drawing carries to the
    shop floor.
    """
    bores = _of_type(plan, "through_hole")
    by_id = {str(op.get("id")): op for op in bores}
    patterns = _of_type(plan, "pattern")
    counted = len(bores)
    notes: List[str] = [f"{len(bores)} through_hole operation"
                        f"{'' if len(bores) == 1 else 's'}"]
    for pattern in patterns:
        source = str(pattern.get("source"))
        if source not in by_id:
            continue
        count = _number((pattern.get("parameters") or {}).get("count")) or 1
        extra = int(count) - 1
        counted += extra
        notes.append(f"{pattern.get('id')} repeats {source} to {int(count)} "
                     f"(+{extra})")
    return counted, "; ".join(notes)


def hole_diameters(plan: Mapping[str, Any]) -> List[float]:
    """Every distinct declared bore diameter, smallest first."""
    found = set()
    for op in _of_type(plan, "through_hole"):
        diameter = _number((op.get("parameters") or {}).get("diameter"))
        if diameter is not None:
            found.add(round(diameter, 6))
    return sorted(found)


def removed_volume(plan: Mapping[str, Any]) -> Tuple[Optional[float], str]:
    """The material the bores take out, as a closed form.

    CALCULATED, and honest about its limit: it is exact only where the bores
    do not meet each other or leave the stock. Where a plan has an edge
    treatment the figure ignores it, and says so rather than pretending to a
    completeness it has not got.
    """
    solids = _of_type(plan, "box", "cylinder")
    if not solids:
        return None, ""
    depth = _stock_depth(plan)
    if depth is None:
        return None, ""
    total = 0.0
    terms: List[str] = []
    counted, _ = hole_count(plan)
    diameters = hole_diameters(plan)
    if not diameters or counted == 0:
        return None, ""
    per_hole = {}
    for op in _of_type(plan, "through_hole"):
        diameter = _number((op.get("parameters") or {}).get("diameter"))
        if diameter is None:
            continue
        per_hole[str(op.get("id"))] = diameter
    multiplicity = {name: 1 for name in per_hole}
    for pattern in _of_type(plan, "pattern"):
        source = str(pattern.get("source"))
        if source in multiplicity:
            count = _number((pattern.get("parameters") or {}).get("count")) or 1
            multiplicity[source] = int(count)
    for name, diameter in per_hole.items():
        n = multiplicity[name]
        volume = n * math.pi * (diameter / 2.0) ** 2 * depth
        total += volume
        terms.append(f"{n} x pi x ({diameter:g}/2)^2 x {depth:g}")
    return total, " + ".join(terms)


def _stock_depth(plan: Mapping[str, Any]) -> Optional[float]:
    """How deep a +Z bore has to travel. Only certain for a single box."""
    boxes = _of_type(plan, "box")
    if len(boxes) != 1:
        return None
    return _number((boxes[0].get("parameters") or {}).get("z"))


# --- the questions -----------------------------------------------------------

_ASKS = re.compile(
    r"\?|^\s*(?:what|how|which|where|is|are|does|do|tell|show|give|list)\b",
    re.I)


def _size(measurement: Mapping[str, Any]) -> Optional[Sequence[float]]:
    size = measurement.get("size")
    if isinstance(size, (list, tuple)) and len(size) == 3 and all(
        isinstance(v, (int, float)) for v in size
    ):
        return list(size)
    return None


def _answer_size(plan, measurement, backend, text):
    # "how long" is a length only when it is about the PART. "How long will
    # it take to machine" is a question about time that happens to share four
    # letters, and answering it with a bounding box would be absurd.
    if not re.search(r"\b(?:size|dimension|dimensions|how\s+big|how\s+large"
                     r"|bounding\s+box|envelope|overall"
                     r"|how\s+(?:wide|tall|long|thick)\b(?!\s+(?:will|would"
                     r"|does|did|do|to|until|before|ago)\b))\b",
                     text):
        return None
    size = _size(measurement)
    if size is None:
        return None
    return Answer(
        text=(f"Overall {size[0]:g} x {size[1]:g} x {size[2]:g} mm "
              f"(X x Y x Z)."),
        provenance=MEASURED, source=f"{backend} measured the current build")


def _answer_volume(plan, measurement, backend, text):
    if not re.search(r"\bvolume\b|\bhow\s+much\s+material\b", text):
        return None
    if re.search(r"\bremoved\b|\btaken\s+out\b|\bcut\s+away\b", text):
        return None
    volume = _number(measurement.get("volume"))
    if volume is None:
        return None
    return Answer(text=f"Volume {volume:.3f} mm3.", provenance=MEASURED,
                  source=f"{backend} measured the current build")


def _answer_removed(plan, measurement, backend, text):
    if not re.search(r"\b(?:removed|taken\s+out|cut\s+away|waste|swarf)\b",
                     text):
        return None
    removed, working = removed_volume(plan)
    if removed is None:
        return None
    caveat = ""
    if _of_type(plan, "fillet", "chamfer"):
        caveat = (" This counts the bores only; the edge treatments on this "
                  "part remove a little more.")
    return Answer(
        text=f"About {removed:.3f} mm3 of material is removed by the holes."
             + caveat,
        provenance=CALCULATED, working=working,
        source="the bore diameters the plan declares, through the stock depth")


def _answer_hole_count(plan, measurement, backend, text):
    if not re.search(r"\bholes?\b|\bbores?\b", text):
        return None
    if not re.search(r"\bhow\s+many\b|\bcount\b|\bnumber\s+of\b", text):
        return None
    count, working = hole_count(plan)
    if count == 0 and not _of_type(plan, "through_hole"):
        return Answer(text="There are no holes in this part.",
                      provenance=DECLARED, source="the operation plan")
    return Answer(
        text=f"There {'is' if count == 1 else 'are'} {count} hole"
             f"{'' if count == 1 else 's'}.",
        provenance=DECLARED, working=working,
        source="the operation plan")


def _answer_hole_size(plan, measurement, backend, text):
    if not re.search(r"\bholes?\b|\bbores?\b", text):
        return None
    if not re.search(r"\b(?:diameter|size|big|wide|how\s+large)\b", text):
        return None
    diameters = hole_diameters(plan)
    if not diameters:
        return None
    if len(diameters) == 1:
        body = f"The holes are {diameters[0]:g} mm in diameter."
    else:
        body = ("The holes are "
                + ", ".join(f"{d:g} mm" for d in diameters[:-1])
                + f" and {diameters[-1]:g} mm in diameter.")
    return Answer(text=body + " That is what the plan asks for; the kernel "
                             "was told it rather than asked.",
                  provenance=DECLARED, source="the operation plan")


def _answer_how_made(plan, measurement, backend, text):
    if not re.search(r"\b(?:operations?|steps?|how\s+(?:was|is)\s+(?:it|this)"
                     r"\s+(?:made|built)|feature\s+tree|history)\b", text):
        return None
    operations = _operations(plan)
    if not operations:
        return None
    described = ", ".join(
        f"{op.get('id')} ({op.get('type')})" for op in operations)
    return Answer(text=f"{len(operations)} operations, in order: {described}.",
                  provenance=DECLARED, source="the operation plan")


def _answer_which_operation(plan, measurement, backend, text):
    if not re.search(r"\bwhich\s+(?:operation|step|feature)\b"
                     r"|\bwhat\s+(?:made|created|cut|added)\b", text):
        return None
    wanted = None
    for pattern, kinds in (
        (r"\bholes?\b|\bbores?\b", ("through_hole", "pattern")),
        (r"\bfillets?\b|\brounds?\b", ("fillet",)),
        (r"\bchamfers?\b|\bbevels?\b", ("chamfer",)),
    ):
        if re.search(pattern, text):
            wanted = kinds
            break
    if wanted is None:
        return None
    found = _of_type(plan, *wanted)
    if not found:
        return Answer(text="Nothing in this plan creates that.",
                      provenance=DECLARED, source="the operation plan")
    named = ", ".join(f"{op.get('id')} ({op.get('type')})" for op in found)
    return Answer(text=f"That comes from {named}.", provenance=DECLARED,
                  source="the operation plan")


def _answer_counts(plan, measurement, backend, text):
    if not re.search(r"\bfaces?\b|\bedges?\b|\bsolids?\b|\bbodies\b", text):
        return None
    parts: List[str] = []
    for key, singular in (("face_count", "face"), ("edge_count", "edge"),
                          ("solid_count", "solid")):
        value = measurement.get(key)
        if isinstance(value, int):
            parts.append(f"{value} {singular}{'' if value == 1 else 's'}")
    if not parts:
        return None
    return Answer(text="The solid has " + ", ".join(parts) + ".",
                  provenance=MEASURED,
                  source=f"{backend} measured the current build")


_DENSITY = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:g\s*/\s*cm3|g/cc|grams?\s+per\s+(?:cubic\s+)?cm)",
    re.I)

#: Densities in g/cm3, for the common shop materials. Used ONLY when the
#: question names one; nothing here assumes a material, because a part's
#: material is not something geometry can tell you.
_DENSITIES = {
    "aluminium": 2.70, "aluminum": 2.70, "steel": 7.85,
    "stainless": 8.00, "brass": 8.50, "copper": 8.96, "titanium": 4.51,
    "abs": 1.04, "pla": 1.24, "nylon": 1.14, "acrylic": 1.18,
}


def _answer_mass(plan, measurement, backend, text):
    if not re.search(r"\b(?:mass|weigh|weight|heavy)\b", text):
        return None
    volume = _number(measurement.get("volume"))
    if volume is None:
        return None
    density = None
    named = None
    stated = _DENSITY.search(text)
    if stated:
        density = float(stated.group(1))
        named = f"{density:g} g/cm3 as given"
    else:
        for material, value in _DENSITIES.items():
            if re.search(rf"\b{material}\b", text):
                density, named = value, f"{material} at {value:g} g/cm3"
                break
    if density is None:
        # A mass without a material is not a number this can produce, and
        # inventing a density would be inventing the answer.
        return Answer(
            text=("Mass needs a material. Say which -- \"in aluminium\", or "
                  "give a density -- and it can be worked out from the "
                  "measured volume."),
            provenance=CALCULATED,
            source="no density was given, so no mass was calculated")
    grams = volume / 1000.0 * density
    return Answer(
        text=f"About {grams:.1f} g ({grams / 1000.0:.3f} kg) in {named}.",
        provenance=CALCULATED,
        working=f"{volume:.3f} mm3 / 1000 x {density:g} g/cm3",
        source="the measured volume and the density you named")


def _answer_centre(plan, measurement, backend, text):
    if not re.search(r"\bcent(?:re|er)\b", text):
        return None
    if not re.search(r"\bwhere\b|\bwhat\b|\bcoordinate", text):
        return None
    size = _size(measurement)
    if size is None:
        return None
    middle = [v / 2.0 for v in size]
    return Answer(
        text=(f"The centre of the bounding box is at "
              f"({middle[0]:g}, {middle[1]:g}, {middle[2]:g}) mm, measured "
              f"from the origin corner."),
        provenance=CALCULATED,
        working=f"half of {size[0]:g} x {size[1]:g} x {size[2]:g}",
        source="the measured bounding box")


def _answer_backend(plan, measurement, backend, text):
    if not re.search(r"\b(?:backend|engine|kernel|freecad|cadquery)\b", text):
        return None
    return Answer(text=f"This part was built by {backend}.",
                  provenance=MEASURED, source="the build that succeeded")


# --- which body the question is about ----------------------------------------
#
# Stage 73. A part with one body has one volume, one envelope and one set of
# holes, and every answerer above was written for that part. A part with two
# bodies has two of each and no single anything -- so before any answerer
# runs, this decides WHICH body the question is about, and refuses when the
# question does not say.
#
# The decision reuses `body_reference.resolve_body`, the one place that
# answers "which body does this request mean". A second implementation here
# would be a second opinion about what "the cube" means, and the two would
# disagree the first time an id gained a hyphen.


class QuestionRefused(Exception):
    """This IS a question about the part, and it has no honest single answer.

    Kept distinct from returning ``None``, and the distinction is the whole
    point. ``None`` means *"not a question this module answers"*, and the
    request falls through to the model, which is safe. A question that this
    module recognises but cannot answer WITHOUT GUESSING must not fall
    through the same way: the model would answer it, from a plan it can read
    but a part it cannot see, and "the volume is 64000" about a part with two
    bodies is wrong in a way nobody downstream can detect.

    So the two outcomes are carried differently, exactly as
    `normalize` already separates a reader declining a sentence from a reader
    refusing one: `_Decline` falls through, `ReadingError` reaches the person.
    """


#: A question about the part AS A WHOLE rather than about one of its bodies.
#: Deliberately narrow: these are the words that say "add them up", and
#: nothing is treated as an aggregate merely because it named no body.
_AGGREGATE = re.compile(
    r"\btotal\b|\baltogether\b|\bcombined\b|\ball\s+told\b|\bin\s+all\b"
    r"|\ball\s+(?:of\s+)?the\s+bodies\b|\bboth\s+bodies\b|\bevery\s+body\b"
    r"|\bwhole\s+part\b|\bentire\s+part\b|\bthe\s+part\s+as\s+a\s+whole\b",
    re.I)


@dataclass(frozen=True)
class Scope:
    """What one question is about: one body, every body, or the only body."""

    #: The plan the answerers should read -- narrowed to the body's own
    #: operations when the question is about one body.
    plan: Mapping[str, Any]
    #: The measurement they should read.
    measurement: Mapping[str, Any]
    #: How to name it in the answer. Empty for a single-body part, so its
    #: wording is byte-for-byte what it has always been.
    label: str = ""
    #: True when the numbers are sums across bodies rather than one body's.
    aggregate: bool = False


def _body_operations(plan: Mapping[str, Any], body: str) -> Mapping[str, Any]:
    """The plan narrowed to the operations that shaped ``body``.

    Read out of `history.Body.features` -- the per-body feature list the
    solid-set walk already produces -- rather than by filtering on `target`,
    which would miss the constructive operation that created the body and
    would have to re-derive ownership that the walk has already decided.
    """
    try:
        history = plan_history(parse_plan(dict(plan)))
    except PlanParseError:
        return plan
    owned = history.body(body)
    if owned is None:
        return plan
    keep = set(owned.features)
    operations = [op for op in _operations(plan) if str(op.get("id")) in keep]
    narrowed = dict(plan)
    narrowed["operations"] = operations
    return narrowed


def _aggregate_measurement(
    bodies: Mapping[str, Mapping[str, Any]]
) -> Dict[str, Any]:
    """The part's totals, and its containing box.

    Volume, faces, edges and solids ADD: each body contributes its own and
    nothing is shared, because separate bodies do not touch. The envelope
    does not add -- it is the box containing every body's box, which is why
    the caller marks it ASSUMED rather than measured.
    """
    volume = 0.0
    solids = faces = edges = 0
    lows: List[Sequence[float]] = []
    highs: List[Sequence[float]] = []
    for measured in bodies.values():
        volume += _number(measured.get("volume")) or 0.0
        solids += int(measured.get("solid_count") or 0)
        faces += int(measured.get("face_count") or 0)
        edges += int(measured.get("edge_count") or 0)
        low, high = measured.get("minimum"), measured.get("maximum")
        if isinstance(low, (list, tuple)) and isinstance(high, (list, tuple)):
            lows.append(low)
            highs.append(high)
    total: Dict[str, Any] = {
        "is_valid": all(bool(m.get("is_valid")) for m in bodies.values()),
        "solid_count": solids,
        "volume": volume,
        "face_count": faces,
        "edge_count": edges,
    }
    if len(lows) == len(bodies) and lows:
        minimum = [min(float(low[i]) for low in lows) for i in range(3)]
        maximum = [max(float(high[i]) for high in highs) for i in range(3)]
        total["minimum"] = minimum
        total["maximum"] = maximum
        total["size"] = [maximum[i] - minimum[i] for i in range(3)]
    return total


def scope_of_body(
    plan: Optional[Mapping[str, Any]],
    bodies: Optional[Mapping[str, Mapping[str, Any]]],
    body: str,
) -> Scope:
    """The scope for a body the caller has ALREADY chosen.

    For a caller that is not reading a sentence at all -- reporting every
    body in turn, say. Going through :func:`scope_for` with the body's own id
    as the text would work by coincidence and break on a body called
    `total`, which the aggregate words would claim.
    """
    per_body = {str(k): dict(v) for k, v in (bodies or {}).items()}
    return Scope(plan=_body_operations(plan or {}, body),
                 measurement=per_body.get(body, {}),
                 label=body)


def scope_for(
    text: str,
    plan: Optional[Mapping[str, Any]],
    measurement: Optional[Mapping[str, Any]],
    bodies: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Scope:
    """Which body this question is about. Refuses rather than choosing one.

    The single-body case is FIRST and returns the caller's own arguments
    untouched, so a part with one body takes no new path, reads no new
    measurement and produces the same words it always has.
    """
    measured = dict(measurement or {})
    per_body = {str(k): dict(v) for k, v in (bodies or {}).items()}

    if len(per_body) <= 1:
        # One body, or a caller that never passed any: unchanged, entirely.
        return Scope(plan=plan or {}, measurement=measured)

    if _AGGREGATE.search(text):
        return Scope(plan=plan or {},
                     measurement=_aggregate_measurement(per_body),
                     label=f"all {len(per_body)} bodies", aggregate=True)

    # "measure", not the resolver's default "change": an edit and a question
    # refuse for the same reason and must say so differently. Someone who
    # asked what the volume is would read "say which one to change" as a
    # refusal to answer rather than as a request to name a body.
    choice = resolve_body(text, plan, verb="measure")
    if choice.reason is not None:
        raise QuestionRefused(choice.reason)
    if choice.body is None or choice.body not in per_body:
        # Resolved to a body with no measurement of its own. Nothing here can
        # answer that honestly, so it is refused rather than answered from
        # some other body's numbers.
        raise QuestionRefused(
            f"there is no measurement for {choice.body!r} on this build"
        )
    return Scope(plan=_body_operations(plan or {}, choice.body),
                 measurement=per_body[choice.body],
                 label=choice.body)


#: Every question, in the order they are offered. Narrow before broad: "how
#: many holes" must not be taken by the size answer merely because it says
#: "how".
ANSWERERS = (
    ("hole_count", _answer_hole_count),
    ("hole_size", _answer_hole_size),
    ("removed", _answer_removed),
    ("mass", _answer_mass),
    ("which_operation", _answer_which_operation),
    ("how_made", _answer_how_made),
    ("centre", _answer_centre),
    ("size", _answer_size),
    ("volume", _answer_volume),
    ("counts", _answer_counts),
    ("backend", _answer_backend),
)


def answer(plan: Optional[Mapping[str, Any]],
           measurement: Optional[Mapping[str, Any]],
           backend: str = "the CAD engine",
           text: str = "",
           bodies: Optional[Mapping[str, Mapping[str, Any]]] = None
           ) -> Optional[Answer]:
    """Answer a question about the current part, or ``None``.

    ``None`` means "not a question this can answer from evidence", and is the
    common and safe outcome.

    ``bodies`` maps each live body's id to that body's own measurement. It is
    optional and defaults to none, so every existing caller keeps its exact
    behaviour; a part with one body takes the same path whether it is passed
    or not. With SEVERAL bodies it is what makes a per-body answer possible
    at all, and a question that does not say which body raises
    :class:`QuestionRefused` rather than being answered about one of them.
    """
    if not plan or not text or not text.strip():
        return None
    lowered = text.lower().strip()
    if not _ASKS.search(lowered):
        return None
    # Scope BEFORE the `_ASKS` gate would be wrong: "make the cube wider" is
    # not a question, and refusing it here would take it away from the edit
    # readers that do handle it. Scope after, so only things already
    # recognised as questions can be refused as questions.
    scope = scope_for(lowered, plan, measurement, bodies)
    for name, answerer in ANSWERERS:
        try:
            found = answerer(scope.plan, scope.measurement,
                             backend or "the CAD engine", lowered)
        except (TypeError, ValueError, KeyError):
            # A malformed plan is not a reason to raise at a person asking a
            # question. Fall through and let the model take it.
            continue
        if found is not None:
            return _attributed(found, scope, name)
    return None


#: The answers that are read off the ENVELOPE rather than summed from the
#: bodies. For an aggregate these are the ones nothing measured: the box
#: containing every body, and the centre of that box -- which for two bodies
#: standing apart lies in the air between them. Named here, so the downgrade
#: below keys on WHICH ANSWERER produced the answer rather than on matching
#: words in its text, which would quietly stop working the day one is
#: reworded.
_FROM_ENVELOPE = ("size", "centre")


def _attributed(found: Answer, scope: Scope, name: str = "") -> Answer:
    """Say which body an answer is about, when there is a choice of body.

    Untouched for a single-body part -- `scope.label` is empty there, so the
    wording is byte-for-byte what it was before this stage and no existing
    test or transcript moves.

    The ASSUMED downgrade is here rather than in the answerer because the
    answerer is right about its own number: `_answer_size` reports the size
    of the measurement it was handed, and that measurement is MEASURED for
    one body. It is only the AGGREGATE envelope that nothing measured, so
    only that case is relabelled, and only for the size.
    """
    if not scope.label:
        return found
    text, provenance = found.text, found.provenance
    source, working = found.source, found.working
    if scope.aggregate:
        if name in _FROM_ENVELOPE:
            provenance = ASSUMED
            source = ("the box containing every body -- no kernel measured "
                      "it, and it includes the space between them")
        elif provenance == MEASURED:
            provenance = CALCULATED
            source = "summed over every body's own measurement"
        # "The solid has 9 faces" is wrong in front of two bodies, and the
        # numbers being right makes it worse rather than better.
        text = text.replace("The solid has ", "The bodies have ")
    elif provenance == MEASURED:
        source = f"{source} of {scope.label}" if source else None
    return Answer(text=f"{scope.label}: {text}", provenance=provenance,
                  working=working, source=source)


__all__ = [
    "ANSWERERS",
    "ASSUMED",
    "CALCULATED",
    "DECLARED",
    "MEASURED",
    "PROVENANCE",
    "Answer",
    "QuestionRefused",
    "Scope",
    "answer",
    "hole_count",
    "hole_diameters",
    "removed_volume",
    "scope_for",
    "scope_of_body",
]
