"""The multi-body OBSERVER: what a measurement answered, and what an export wrote.

Stage 75's `evaluate75.observe` records everything that is a property of a
PLAN -- what the model said, whether the plan validated, what the kernel
built. Two of the broader corpus's nine dimensions are not properties of a
plan, and `corpus-design.md` §4 says so:

    A measurement question goes through `questions.answer` and an export
    through `/session/export`, and neither is visible in a
    `PlanGenerationResult`.

This module observes those two. It records FACTS and passes NO judgement:
every verdict lives in :mod:`evaluate76`, which is the only module that may
read :func:`ground_truth76.expected`.

FOUR RULES THIS MODULE IS BUILT AROUND, each of which cost a stage its
headline number somewhere in this project:

1. **Never index a body by position.** Not `bodies[0]`, not "the first one",
   not "the other one". A body is reached by its ID, through the canonical
   `ExecutionResult.part` for the single-body case and by iterating for the
   rest. Stage 62's bug and Stages 71-74's three surviving `bodies[0]` reads
   were all the same mistake, and the last of them was found in the BROWSER
   a stage after the server had been fixed.

2. **Never infer which body an answer is about from its VALUE.** The body an
   answer is about is what the product's own scope decision says it is, read
   from :func:`cad_experimental.questions.scope_for` -- the same call
   `questions.answer` makes. Pairing an answer to a body because the number
   looks right is how a part whose two bodies' numbers are swapped reads as
   correct.

3. **Never infer identity from order.** Every row is keyed by the PROBE's
   name and every body by its id. Reversing either changes nothing, and a
   fixture proves it.

4. **Never let the writer's own verdict be the export's verdict.** The
   observer writes the file, then inspects the ARTEFACT independently --
   counting solids, measuring them, and reading the names out of the file --
   and forms the rung from that. A `verify_assembly` that passed is the
   product agreeing with itself; a file on disk is evidence.

WHAT IT MAY IMPORT. The product, and `ground_truth76.probes_to_ask` -- the
narrowed view that carries a probe's name, kind and text and NOT its
expected outcome. It does not import `expected`, and a test asserts that: an
observer that could read the expectation could record it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_experimental import questions
from cad_experimental.cad_backend import BackendError
from cad_experimental.questions import QuestionRefused

import ground_truth76 as G


#: A number as the product writes one: `Volume 64000.000 mm3.`
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

#: A STEP `PRODUCT` entity's first field -- its name. The lookbehind keeps
#: `PRODUCT_DEFINITION(`, `PRODUCT_CONTEXT(` and
#: `PRODUCT_RELATED_PRODUCT_CATEGORY(` out: only the bare `PRODUCT(` entity
#: carries a body's name.
_PRODUCT = re.compile(r"(?<![A-Z_])PRODUCT\s*\(\s*'([^']*)'")


def step_product_names(text: str) -> Tuple[str, ...]:
    """Every `PRODUCT` name in a STEP file, in the order they appear.

    **This exists because the product's own check is weaker than it looks,
    and a measurement of it is in `README.md`.**
    `cad_backend.verify_assembly` tests each body id with ``name not in
    written_text`` -- a bare substring scan over the whole file, boilerplate
    included. Measured on a real two-body STEP from both engines, that check
    ACCEPTS `'SOLID'`, `'part'`, `'Open'` and `'cub'` as body names: the
    first three occur in STEP boilerplate and the fourth is a prefix of
    `cube`. A body legitimately named `part` would satisfy it in a file that
    never mentioned the body at all.

    An observer that reported what `verify_assembly` reports would inherit
    that, so it reads the file's own product structure instead. Both engines
    write the body id as a `PRODUCT` name -- measured, CadQuery 2.8.0 and
    FreeCAD 1.0.0, same shape both times -- alongside one root product of
    their own (a UUID, and `cad_experimental_assembly`).

    **What this still does NOT prove**, and the reason `identity_binding`
    stays `"unproven"` at every rung: that a PRODUCT named `cube` is the
    product of the SOLID that measures 64000. Binding those needs the
    PRODUCT -> SHAPE_REPRESENTATION -> MANIFOLD_SOLID_BREP chain followed
    per engine, and the two engines' assembly readers differ. This is a
    strictly better name check, not an identity proof.
    """
    return tuple(match.group(1) for match in _PRODUCT.finditer(text or ""))


def _first_number(text: str, label: str = "") -> Optional[float]:
    """The first number in an ANSWER, or ``None``, with its label removed.

    Read out of the text the PERSON is shown rather than out of the
    measurement the answer came from. Taking it from the measurement would
    prove only that a dictionary contains what was put in it; taking it from
    the sentence proves the sentence carries the right number, which is what
    a person acts on. The product formats a volume to three decimals, and
    the tolerance is relative, so the rounding is far inside it.

    **The label comes off first, and this cost a bug.** `_attributed`
    prefixes a scoped answer with `f"{label}: "`, and the AGGREGATE label is
    `all 2 bodies` -- so the first number in *"all 2 bodies: Volume
    73424.778 mm3."* is the body COUNT. Every per-body probe passed, because
    `cube` and `pin` carry no digits, and only the three aggregate totals
    failed: a defect that fires exactly on the multi-body case this observer
    exists for, and on nothing else. It was caught by running the observer
    against a real kernel before any live call, which is the whole reason
    that gate is in the brief.
    """
    sentence = text or ""
    if label and sentence.startswith(f"{label}: "):
        sentence = sentence[len(label) + 2:]
    found = _NUMBER.search(sentence)
    return float(found.group(0)) if found else None


def bodies_by_id(execution: Any) -> Dict[str, Dict[str, Any]]:
    """Every live body's own measurement, keyed by its id.

    Built by ITERATING the executor's body tuple, never by indexing it. The
    resulting mapping is exactly what the product hands `questions.answer`
    (`session.current.bodies`), so the observer asks its questions through
    the same door a person does.
    """
    rows: Dict[str, Dict[str, Any]] = {}
    for body in getattr(execution, "bodies", ()) or ():
        rows[str(body.id)] = body.measurement.to_dict()
    return rows


def part_measurement(execution: Any) -> Dict[str, Any]:
    """The part-level measurement, exactly as the product decides it.

    :attr:`ExecutionResult.part` is "the single live body, or ``None``", and
    Stage 71 deliberately did not widen it to "the first one". So a part with
    several bodies has NO part-level measurement -- `{}` -- which is what the
    session stores and what makes a question about "the part" have to say
    which body it means.
    """
    only = getattr(execution, "part", None)
    if only is None:
        return {}
    return bodies_by_id(execution).get(str(only), {})


def geometry(execution: Any) -> Dict[str, Any]:
    """The GEOMETRY section: what the kernel built, per body.

    An unordered fact reported in the executor's own order. The order is
    recorded because it is what the file writer will use, and graded by
    nothing.
    """
    per_body = bodies_by_id(execution)
    return {
        "backend": getattr(execution, "backend", ""),
        "succeeded": bool(getattr(execution, "succeeded", False)),
        "body_ids": tuple(per_body),
        "declared": tuple(getattr(execution, "declared", ()) or ()),
        "single_live_body": getattr(execution, "part", None),
        "bodies": per_body,
        "failure": (
            execution.failure.to_dict()
            if getattr(execution, "failure", None) else None
        ),
    }


# --------------------------------------------------------- measurement


def measure(
    case_name: str,
    plan: Mapping[str, Any],
    execution: Any,
    *,
    backend_name: str = "the CAD engine",
) -> List[Dict[str, Any]]:
    """Ask this case's probes and record what came back. No judgement.

    The probes come from :func:`ground_truth76.probes_to_ask`, which carries
    a name, a kind and a text and nothing else -- so this function cannot
    read what the right answer is, let alone record it.

    Each probe goes through `questions.answer` with the SAME arguments the
    product passes (`app.py`'s conversation route): the plan, the part-level
    measurement, the backend's name, the text, and the per-body
    measurements. Nothing is reimplemented here; a second copy of "which
    body does this mean" would be a second opinion, and the two would
    disagree the first time an id gained a hyphen.
    """
    return measure_probes(G.probes_to_ask(case_name), plan, execution,
                          backend_name=backend_name)


def measure_probes(
    probes: Sequence[Mapping[str, str]],
    plan: Mapping[str, Any],
    execution: Any,
    *,
    backend_name: str = "the CAD engine",
) -> List[Dict[str, Any]]:
    """The same observation, for probes the CALLER supplies.

    :func:`measure` is this with `ground_truth76`'s own narrowed probe view;
    a later corpus whose bodies the MODEL names cannot use that view,
    because its probe texts are not knowable before the part exists. So the
    door is opened here rather than duplicated there: one implementation of
    "ask the product and record what came back", and the caller decides what
    to ask.

    Each probe is a mapping carrying `name`, `kind` and `text`, and
    **nothing else** -- the same shape `probes_to_ask` returns, so a caller
    cannot smuggle an expectation in through it either.
    """
    per_body = bodies_by_id(execution)
    part = part_measurement(execution)
    rows: List[Dict[str, Any]] = []

    for probe in probes:
        text = probe["text"]
        row: Dict[str, Any] = {
            "probe": probe["name"],
            "kind": probe["kind"],
            "text": text,
            "outcome": None,
            "label": "",
            "about": None,
            "aggregate": False,
            "provenance": None,
            "value": None,
            "said": "",
            "working": None,
            "source": None,
            "label_is_prefix": None,
        }
        try:
            found = questions.answer(dict(plan), part, backend_name, text,
                                     bodies=per_body)
        except QuestionRefused as refusal:
            # RECOGNISED and unanswerable. Kept apart from `None` because the
            # difference is the whole point of Stage 73: `None` falls through
            # to a model, and a model asked "what is the volume?" about a
            # two-body part will answer it from a plan it can read about a
            # part it has never seen.
            row["outcome"] = G.REFUSED
            row["said"] = str(refusal)
            rows.append(row)
            continue

        if found is None:
            row["outcome"] = G.DECLINED
            rows.append(row)
            continue

        # WHICH BODY, from the product's own decision rather than from the
        # answer's number. `scope_for` is the same call `answer` just made,
        # so this is the same answer read twice, never a second opinion --
        # and it cannot raise here, because `answer` got past it.
        scope = questions.scope_for(text.lower(), dict(plan), part, per_body)
        label = scope.label or ""
        row["outcome"] = G.ANSWERED
        row["label"] = label
        row["aggregate"] = bool(scope.aggregate)
        row["about"] = label if (label and not scope.aggregate) else None
        row["provenance"] = found.provenance
        row["value"] = _first_number(found.text, label)
        row["said"] = found.text
        row["working"] = found.working
        row["source"] = found.source
        row["label_is_prefix"] = (
            found.text.startswith(f"{label}: ") if label else None
        )
        rows.append(row)
    return rows


# -------------------------------------------------------------- export


def export(
    case_name: str,
    execution: Any,
    engine: Any,
    folder: Any,
    *,
    filename: str = "part.step",
) -> Dict[str, Any]:
    """Write this part as STEP and INSPECT what landed on disk.

    The writer is chosen the way the product chooses it -- the assembly
    writer when :attr:`ExecutionResult.part` is ``None``, the unchanged
    single-body writer otherwise -- so this observes the real export path
    rather than a convenient one.

    Everything after the write is an inspection of the ARTEFACT: the bytes,
    the solids it reads back as, what each of them measures, and which ids
    appear in it as text. A writer that raised still gets inspected, because
    "the writer refused" and "there is nothing on disk" are different facts
    and a file can be perfectly well-formed and hold nothing.

    Passes no judgement: the rung is computed by :func:`evaluate76.grade`
    from these facts and the truth for ``case_name``.
    """
    per_body = bodies_by_id(execution)
    shapes = getattr(execution, "shapes", {}) or {}
    # Declaration order, from the executor's own body list -- the same
    # ordering `/session/export` uses, so two runs write the same file.
    ordered: List[Tuple[str, Any]] = [
        (str(body.id), shapes.get(str(body.id)))
        for body in (getattr(execution, "bodies", ()) or ())
    ]
    only = getattr(execution, "part", None)
    multi = only is None

    target = Path(folder) / filename
    row: Dict[str, Any] = {
        "case": case_name,
        "engine": getattr(engine, "name", ""),
        "writer": "assembly" if multi else "single",
        "asked_for": [name for name, _ in ordered],
        "body_count": len(ordered),
        "writer_raised": False,
        "error": None,
        "wrote_file": False,
        "bytes": 0,
        "readable": False,
        "solids_read": None,
        "volumes_read": None,
        "names_found": [],
        "names_missing": [name for name, _ in ordered],
        "names_found_by_substring": [],
        "product_names": [],
        "read_error": None,
        # Carried on EVERY export observation, at every rung, so no reader of
        # a recorded run can take the top rung for more than it is.
        "identity_binding": G.IDENTITY_BINDING,
        "identity_binding_note": G.IDENTITY_BINDING_NOTE,
    }

    try:
        if multi:
            engine.export_step_assembly(ordered, target)
        else:
            engine.export_step(shapes.get(str(only)), target)
    except BackendError as exc:
        row["writer_raised"] = True
        row["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        row["writer_raised"] = True
        row["error"] = f"{type(exc).__name__}: {exc}"

    # --- the inspection, which is independent of whether the writer was
    # --- happy. This is the half that makes the observation evidence.
    if target.exists():
        data = target.read_bytes()
        row["wrote_file"] = True
        row["bytes"] = len(data)
        try:
            solids = engine.read_step_solids(target)
            row["readable"] = True
            row["solids_read"] = len(solids)
            row["volumes_read"] = [
                float(engine.measure(solid).volume) for solid in solids
            ]
        except Exception as exc:  # noqa: BLE001
            row["read_error"] = f"{type(exc).__name__}: {exc}"
        # The names, read out of the file's own PRODUCT structure rather
        # than by scanning it for a substring. What this proves is that each
        # id reached the file AS A PRODUCT -- never which solid carries it.
        # `identity_binding` stays "unproven" at every rung for that reason.
        text = target.read_text(errors="ignore")
        products = step_product_names(text)
        row["product_names"] = list(products)
        present = set(products)
        row["names_found"] = [n for n, _ in ordered if n in present]
        row["names_missing"] = [n for n, _ in ordered if n not in present]
        # What the PRODUCT's own check would have seen, recorded beside it
        # and graded by nothing. The gap between these two lists is the
        # weakness described on `step_product_names`, visible in the data
        # rather than only in prose: on a file whose bodies are anonymous
        # this is full and `names_found` is empty.
        row["names_found_by_substring"] = [n for n, _ in ordered if n in text]
    row["measured_bodies"] = per_body
    return row


def cross_read(path: Any, engine: Any) -> Dict[str, Any]:
    """Read a STEP written by one engine with ANOTHER, and measure it.

    A file only one engine can read is not an interchange file. Stage 74
    measured FreeCAD reading the CadQuery-written assembly and returning the
    same two solids at the same volumes; this records that, both ways.
    """
    row: Dict[str, Any] = {"engine": getattr(engine, "name", ""),
                           "readable": False, "solids_read": None,
                           "volumes_read": None, "error": None}
    try:
        solids = engine.read_step_solids(Path(path))
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    row["readable"] = True
    row["solids_read"] = len(solids)
    row["volumes_read"] = [float(engine.measure(s).volume) for s in solids]
    return row


# ------------------------------------------------------ the observation


def observation(
    *,
    case_name: str,
    source: str,
    model_output: Optional[Mapping[str, Any]],
    geometry_section: Mapping[str, Any],
    measurement_section: Sequence[Mapping[str, Any]],
    export_section: Optional[Mapping[str, Any]],
    cross_reads: Sequence[Mapping[str, Any]] = (),
    note: str = "",
) -> Dict[str, Any]:
    """The four sections, kept apart, with where the part came from on top.

    **MODEL OUTPUT** is ``None`` for a deterministic build, and that is not
    the same as an empty one: a fixture part has no model output because no
    model was asked, and recording `{}` would let a reader later mistake it
    for a model that said nothing.

    ``source`` is one of :data:`ground_truth76.SOURCES` and is checked here
    rather than trusted, because everything Stage 76 produces is
    DETERMINISTIC and the single most damaging thing this instrument could
    do is let one of its own fixture runs be read afterwards as evidence
    about a model.
    """
    if source not in G.SOURCES:
        raise ValueError(
            f"unknown source {source!r}; one of {', '.join(G.SOURCES)}"
        )
    if source == G.SOURCE_MODEL_GENERATED and model_output is None:
        raise ValueError(
            "an observation labelled MODEL_GENERATED must carry the model's "
            "output; a build with no model output is DETERMINISTIC, and "
            "labelling it otherwise is the one claim this instrument must "
            "never make"
        )
    if source != G.SOURCE_MODEL_GENERATED and model_output is not None:
        raise ValueError(
            f"an observation labelled {source} carries model output; say "
            "MODEL_GENERATED or drop it -- a fallback and a model result are "
            "different facts"
        )
    return {
        "case": case_name,
        "source": source,
        "is_live_model_result": source == G.SOURCE_MODEL_GENERATED,
        "note": note,
        "model_output": dict(model_output) if model_output is not None else None,
        "geometry": dict(geometry_section),
        "measurement": [dict(row) for row in measurement_section],
        "export": dict(export_section) if export_section is not None else None,
        "cross_reads": [dict(row) for row in cross_reads],
    }


__all__ = [
    "bodies_by_id", "cross_read", "export", "geometry", "measure",
    "measure_probes",
    "observation", "part_measurement", "step_product_names",
]
