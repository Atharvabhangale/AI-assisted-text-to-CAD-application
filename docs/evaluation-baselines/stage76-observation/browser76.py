"""The same observation, read off what the BROWSER is actually sent.

Stage 76's observer calls `questions.answer` and the backend's exporter
directly, which is the right level for proving the instrument. It is not
the level a person uses. Between the two sit the HTTP routes, the session,
and a payload that has had three different per-body shapes in it at once --
and the last `bodies[0]` of the whole multi-body slice was found in the
BROWSER (`main.ts:201`), a stage after the server had been fixed, showing
one body's numbers under a heading that said *measurements*.

So this module maps the payload a person's browser receives onto the SAME
observation object, and the same `evaluate76.grade` scores it. One truth,
one grader, two routes into it.

**It duplicates no business logic.** Which body a question is about is
decided by the server, through `body_reference.resolve_body`, exactly once;
the browser sends only the raw sentence and never a body field. This module
reads the ANSWER the server gave and the body it SAID the answer was about.
It does not re-derive either, and a test asserts it imports no reader, no
resolver and no validator.

TWO THINGS IT REFUSES TO READ AS EVIDENCE:

* **`x-cad-bodies`.** The header is built from the executor's own body list
  BEFORE the file is read back (`app.py`), so it says what was asked for,
  never what landed. A caller that checked it would be asking the writer
  whether the writer succeeded.
* **the file's name, and the STEP header's originating-system string.**
  Neither is a body. `e2e:surfaces` today counts `MANIFOLD_SOLID_BREP` and
  substring-matches each id, which is the same honest limit the server's
  own check has; this module counts the same solids and reads the PRODUCT
  names instead, which is strictly stronger and is why its `names_found`
  can disagree with that script's.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

import ground_truth76 as G

#: `MANIFOLD_SOLID_BREP` is one entity per solid, and it is what the browser
#: e2e counts. Counted here too, so the two instruments read the same file
#: the same way.
_SOLID = re.compile(r"MANIFOLD_SOLID_BREP")

#: Repeated from `observe76` deliberately: this module is importable without
#: the product, so a captured payload can be graded anywhere -- including
#: from a JSON file a browser run wrote.
_PRODUCT = re.compile(r"(?<![A-Z_])PRODUCT\s*\(\s*'([^']*)'")


def geometry_from_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """The GEOMETRY section, from a `/session/message` reply.

    The per-body rows come from the payload's own `bodies` list, whose key
    is `body_id`. `execution["bodies"]` carries the same bodies under `id`
    and without the declared flag, and `session["current"]["bodies"]` is a
    third shape again -- so the one the browser draws from is the one read,
    and the others are recorded for comparison rather than merged.
    """
    rows = list(payload.get("bodies") or ())
    bodies: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        body_id = str(row.get("body_id"))
        measured = row.get("measurement") or {}
        bodies[body_id] = dict(measured)

    execution = payload.get("execution") or {}
    declared = tuple(map(str, payload.get("declared_bodies")
                         or execution.get("declared") or ()))
    # `measurement` is `{}` for a multi-body part, by construction: `_facts`
    # reads `ExecutionResult.part`, which is the single live body or None.
    # So an EMPTY part-level measurement is what says "several bodies", and
    # a populated one names the only body there is.
    part_level = payload.get("measurement") or {}
    single = None
    if part_level and len(bodies) == 1:
        single = next(iter(bodies))
    return {
        "backend": payload.get("backend", ""),
        "succeeded": payload.get("status") == "built"
        or bool(execution.get("succeeded")),
        "body_ids": tuple(bodies),
        "declared": declared,
        "single_live_body": single,
        "bodies": bodies,
        "failure": execution.get("failure"),
        # Recorded so a divergence between the three per-body shapes is
        # visible rather than silently resolved by whichever one was read.
        "execution_body_ids": tuple(
            str(row.get("id")) for row in execution.get("bodies") or ()
        ),
        "session_body_ids": tuple(
            ((payload.get("session") or {}).get("current") or {})
            .get("bodies") or ()
        ),
        "part_level_measurement": dict(part_level),
        "render_present": payload.get("render") is not None,
    }


def measurement_row(probe: Mapping[str, str],
                    reply: Mapping[str, Any],
                    bodies: Sequence[str] = ()) -> Dict[str, Any]:
    """One probe's row, from the `/session/message` reply that answered it.

    The route reports `status: "answered"` with an `evidence` object, or
    `status: "refused"` with the reason as the reply. Those are the two the
    server distinguishes, and this keeps them apart for the same reason
    `questions` does: a refusal reaches the person, and anything else falls
    through to a model.
    """
    status = str(reply.get("status") or "")
    evidence = reply.get("evidence") or {}
    said = str(reply.get("reply") or "")
    row: Dict[str, Any] = {
        "probe": probe["name"],
        "kind": probe["kind"],
        "text": probe["text"],
        "outcome": G.DECLINED,
        "label": "",
        "about": None,
        "aggregate": False,
        "provenance": evidence.get("provenance"),
        "value": None,
        "said": said,
        "working": evidence.get("working"),
        "source": evidence.get("source"),
        "label_is_prefix": None,
        "http_status": status,
    }
    if status == "refused":
        row["outcome"] = G.REFUSED
        row["provenance"] = None
        return row
    if status != "answered":
        return row

    row["outcome"] = G.ANSWERED
    # The body the SERVER said the answer is about, read off the prefix it
    # wrote -- never re-derived here, and never guessed from the number.
    # The route's own bodies list is the vocabulary it is matched against,
    # so an answer prefixed with something that is not a body of this part
    # is recorded as no body at all rather than as a new one.
    label, _, rest = said.partition(": ")
    known = _labels(bodies or reply.get("bodies") or ())
    if label and label in known:
        row["label"] = label
        row["label_is_prefix"] = True
        row["aggregate"] = label.startswith("all ")
        row["about"] = None if row["aggregate"] else label
        row["value"] = _first_number(rest)
    else:
        row["value"] = _first_number(said)
    return row


def _labels(bodies: Sequence[str]) -> frozenset:
    """Every label the server could legitimately have prefixed an answer
    with: one of the part's bodies, or the aggregate label.

    A closed vocabulary on purpose. Treating whatever precedes the first
    colon as a body id would let a reworded answer invent one, and an
    observation that invents a body is worse than one that finds none.
    """
    names = [str(name) for name in bodies]
    return frozenset(names + [f"all {len(names)} bodies"])


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _first_number(text: str) -> Optional[float]:
    found = _NUMBER.search(text or "")
    return float(found.group(0)) if found else None


def export_from_bytes(
    case_name: str,
    body: str,
    *,
    asked_for: Sequence[str],
    status: int,
    headers: Mapping[str, str],
    writer: str,
) -> Dict[str, Any]:
    """The EXPORT section, from the bytes `/session/export` returned.

    The solids are counted the way `e2e:surfaces` counts them, and the names
    are read out of the PRODUCT structure rather than by scanning the text,
    which is where this is stronger than that script.

    `headers` is recorded and is NOT evidence: `x-cad-bodies` is built from
    the executor's body list before the file is read back.
    """
    asked = [str(name) for name in asked_for]
    products = [match.group(1) for match in _PRODUCT.finditer(body or "")]
    present = set(products)
    wrote = status == 200 and bool(body)
    return {
        "case": case_name,
        "engine": headers.get("x-cad-backend", ""),
        "writer": writer,
        "asked_for": asked,
        "body_count": len(asked),
        "writer_raised": status != 200,
        "error": None if status == 200 else f"HTTP {status}",
        "wrote_file": wrote,
        "bytes": len(body or ""),
        "readable": wrote and bool(products),
        "solids_read": len(_SOLID.findall(body or "")) if wrote else None,
        # The bytes carry no measured volume, so the geometry rung cannot be
        # reached from a browser response alone. Reported as `None`, never as
        # an empty list: absent is not zero, and a rung this route cannot
        # reach must not read as one it failed.
        "volumes_read": None,
        "names_found": [name for name in asked if name in present],
        "names_missing": [name for name in asked if name not in present],
        "names_found_by_substring": [name for name in asked
                                     if name in (body or "")],
        "product_names": products,
        "read_error": None,
        "identity_binding": G.IDENTITY_BINDING,
        "identity_binding_note": G.IDENTITY_BINDING_NOTE,
        # Recorded, and deliberately not consulted by any check.
        "header_x_cad_bodies": headers.get("x-cad-bodies"),
        "header_is_not_evidence": (
            "x-cad-bodies is built from the executor's body list before the "
            "file is read back, so it says what was asked for and never "
            "what landed"
        ),
    }


def probe_rows(case_name: str,
               replies: Mapping[str, Mapping[str, Any]],
               bodies: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """Every probe of a case, from the replies the route gave, by probe NAME.

    Keyed by name rather than zipped by position: `measure` and this must
    agree about which answer belongs to which question, and a zip would make
    them agree only while both lists happened to be in the same order.
    """
    return [measurement_row(probe, replies.get(probe["name"], {}), bodies)
            for probe in G.probes_to_ask(case_name)]


def measure_bytes_with(engine: Any, body: str, row: Dict[str, Any],
                       folder: Any) -> Dict[str, Any]:
    """Fill in the geometry half of a browser export, with a real kernel.

    A browser response is a string of bytes. It carries the solids and the
    names, and it carries no MEASURED VOLUME -- nothing in it says a solid
    is 64000 mm3, and no amount of reading the text will produce one. So an
    export observed from a browser alone stops at the names rung, and the
    grader says `geometry_assessed: False` rather than failing a rung it
    never asked about.

    This is the honest way past that: write the bytes the browser received
    to a file and read THEM back with a kernel. The artefact under test is
    still the one the person downloaded; only the instrument reading it is
    the kernel. `volumes_measured_by` records which engine did it, because
    that is a different provenance from the rest of the row and must not be
    read as the browser having measured anything.
    """
    from pathlib import Path as _Path

    filled = dict(row)
    target = _Path(folder) / f"{row['case']}-from-browser.step"
    target.write_text(body or "")
    try:
        solids = engine.read_step_solids(target)
    except Exception as exc:  # noqa: BLE001
        filled["read_error"] = f"{type(exc).__name__}: {exc}"
        return filled
    filled["readable"] = True
    filled["solids_read"] = len(solids)
    filled["volumes_read"] = [float(engine.measure(s).volume) for s in solids]
    filled["volumes_measured_by"] = getattr(engine, "name", "")
    return filled


__all__ = ["export_from_bytes", "geometry_from_payload", "measure_bytes_with",
           "measurement_row", "probe_rows"]
