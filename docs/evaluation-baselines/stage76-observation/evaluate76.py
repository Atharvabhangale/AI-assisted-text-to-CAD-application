"""Grading a multi-body observation against the immutable truth for its case.

The ONLY module in Stage 76 that reads :func:`ground_truth76.expected`. The
observer cannot: it reaches the probes through
:func:`ground_truth76.probes_to_ask`, which carries a name, a kind and a
text and no expectation at all.

WHAT THIS MODULE REFUSES TO DO, each because a stage paid for it:

* **It never builds an expectation.** `grade` takes one observation and
  fetches its own truth BY NAME. Stage 67 derived its expected plate
  thickness from the model's own plan, and a criterion that grades a part
  against its own answer cannot fail it.
* **It never indexes a body or a probe by position.** Every measurement row
  is keyed by the PROBE's name and every body by its id; a reversed
  observation grades identically, and a fixture proves it.
* **It never accepts a total as proof of a split.** Per-body volumes are
  compared as an unordered MULTISET, body by body. Two disjoint solids
  fused have exactly the total of the two apart, so a grader that added
  them up would pass the one part it exists to fail.
* **It never treats "the file exists" as an export.** The rung ladder makes
  that structural: a file on disk is level B at best, and an empty
  well-formed STEP -- which is what FreeCAD's `Part.export` leaves when
  handed raw shapes -- is level C.
* **It never reports one number for two different quantities.**
  :func:`summarise` keeps measurement and export apart and produces no
  combined rate, exactly as Stage 75's does for creation and refusal.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

import ground_truth76 as G


def _close(measured: Optional[float], truth: float,
           tolerance: float = G.VOLUME_TOLERANCE) -> bool:
    """Relative comparison against a closed form. Never equality."""
    if measured is None:
        return False
    if truth == 0.0:
        return abs(measured) <= tolerance
    return abs(measured - truth) / abs(truth) <= tolerance


def _match_multiset(measured: Optional[Sequence[float]],
                    truth: Sequence[float],
                    tolerance: float = G.VOLUME_TOLERANCE) -> bool:
    """Whether the measured volumes are the expected ones, in ANY order.

    Order is not meaning: which solid a writer emits first says nothing
    about the part. Greedy pairing is exact here because the expected
    volumes are far apart relative to the tolerance.
    """
    if measured is None:
        return False
    if len(measured) != len(truth):
        return False
    remaining = list(measured)
    for want in truth:
        hit = next((i for i, got in enumerate(remaining)
                    if _close(got, want, tolerance)), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def _said(row: Mapping[str, Any]) -> str:
    return str(row.get("said") or "").lower()


# ------------------------------------------------------ measurement


def grade_measurement(observation: Mapping[str, Any]) -> Dict[str, Any]:
    """Score every probe of one case. Keyed by probe NAME, never by position.

    A probe the observation never asked is a FAILURE, not an absence. Five
    correct answers out of six looks like a good run, and the question that
    was not asked is the one that would have failed.
    """
    truth = G.expected(observation["case"])
    tolerance = truth["volume_tolerance"]
    rows = {str(row.get("probe")): row
            for row in observation.get("measurement") or ()}

    probes: Dict[str, Dict[str, Any]] = {}
    for want in truth["probes"]:
        name = want["name"]
        row = rows.get(name)
        checks: Dict[str, Optional[bool]] = {"asked": row is not None}
        if row is None:
            probes[name] = {"kind": want["kind"], "checks": checks,
                            "passed": False, "outcome": None}
            continue

        got = str(row.get("outcome") or "")
        checks["outcome"] = got == want["outcome"]

        if want["outcome"] == G.ANSWERED:
            # WHICH BODY, from the product's own scope decision -- never
            # from the number. A part whose two bodies' values are swapped
            # carries the right multiset of numbers and the wrong answers.
            checks["about"] = row.get("about") == want["about"]
            checks["aggregate"] = bool(row.get("aggregate")) == bool(
                want["aggregate"])
            checks["provenance"] = row.get("provenance") == want["provenance"]
            if want["value"] is not None:
                checks["value"] = _close(row.get("value"), want["value"],
                                         tolerance)
            # An answer about a named body must SAY which body it is about.
            # A right number under no name is not an answer a person can act
            # on, and on a multi-body part it is indistinguishable from an
            # answer about the other body.
            if want["about"] is not None or want["aggregate"]:
                checks["labelled"] = row.get("label_is_prefix") is True
        elif want["outcome"] == G.REFUSED:
            # The refusal must NAME the bodies, and the check reads the
            # model's -- here the product's -- own words. A refusal that
            # names nothing is a shrug.
            said = _said(row)
            checks["named"] = all(
                str(body).lower() in said for body in want["must_name"]
            )

        probes[name] = {
            "kind": want["kind"],
            "checks": checks,
            "outcome": got,
            "passed": all(v is True for v in checks.values()),
        }

    expected_names = {want["name"] for want in truth["probes"]}
    unexpected = sorted(set(rows) - expected_names)
    kinds_covered = sorted({want["kind"] for want in truth["probes"]})
    return {
        "probes": probes,
        "unexpected_probes": unexpected,
        "kinds_covered": kinds_covered,
        "passed": (
            bool(probes)
            and all(p["passed"] for p in probes.values())
            and not unexpected
        ),
    }


# ----------------------------------------------------------- export


def export_level(observation: Mapping[str, Any],
                 truth: Mapping[str, Any]) -> str:
    """The highest rung whose check, and every check below it, passed.

    Strictly increasing, and the order is the one `verify_assembly` uses
    with geometry added on top: written, readable, counted, named, measured.
    """
    row = observation.get("export")
    if not row or not row.get("wrote_file"):
        return G.LEVEL_A
    if not row.get("readable") or row.get("solids_read") is None:
        return G.LEVEL_B
    if int(row["solids_read"]) != int(truth["export_solids"]):
        # 0 is an empty well-formed file, 1 is a fuse the plan never asked
        # for, anything else is a body dropped or invented. All three are
        # the same rung because all three mean the file is not the part.
        return G.LEVEL_C
    found = set(row.get("names_found") or ())
    if any(name not in found for name in truth["export_names"]):
        return G.LEVEL_D
    if row.get("volumes_read") is None:
        # The rung was not ASSESSED, which is not the same as failed. A
        # browser response is a string of bytes: it carries the solids and
        # the names and no measured volume, so nothing in it can answer
        # this. `absent is not zero` -- the same distinction `questions`
        # makes for `None` against `[]` and `_operations_the_model_wrote`
        # makes for a missing raw answer. An empty list, by contrast, means
        # a file that really did read back as no solids, and that IS a
        # failure, caught at rung C above.
        return G.LEVEL_D
    if not _match_multiset(row.get("volumes_read"), truth["export_volumes"],
                           truth["volume_tolerance"]):
        return G.LEVEL_E
    return G.LEVEL_F


def grade_export(observation: Mapping[str, Any]) -> Dict[str, Any]:
    """The rung this export reached, and what it is and is not evidence of."""
    truth = G.expected(observation["case"])
    row = observation.get("export")
    level = export_level(observation, truth)
    reached = G.EXPORT_LEVELS.index(level)
    wanted = G.EXPORT_LEVELS.index(truth["export_level"])

    writer_right: Optional[bool] = None
    if row:
        writer_right = (
            (row.get("writer") == "single") == bool(truth["single_body_writer"])
        )

    # Whether the top rung could be asked about at all. `None` volumes mean
    # the artefact was observed from something that cannot measure -- a
    # browser response -- and a verdict that reported that as a failed rung
    # would be blaming the route for a question it was never asked.
    assessed = bool(row) and row.get("volumes_read") is not None
    return {
        "level": level,
        "level_means": G.EXPORT_LEVEL_MEANING[level],
        "expected_level": truth["export_level"],
        "reached_expected": reached >= wanted,
        "geometry_assessed": assessed,
        "why_geometry_not_assessed": (
            None if assessed else
            "no volume was read back: these bytes carry solids and names "
            "and nothing that measures. Re-read them with a kernel "
            "(`browser76.measure_bytes_with`) to reach the top rung"
        ),
        "writer": (row or {}).get("writer"),
        "writer_right": writer_right,
        # What the top rung is and is not. Carried on EVERY verdict, so no
        # reader of a recorded run can take an F for more than it is -- and
        # so that an F on a single-body export, where the writer puts no
        # body name in the file at all, is never read as the same claim as
        # an F on an assembly.
        "identity_state": truth["export_identity"],
        "identity_note": (
            G.IDENTITY_BINDING_NOTE
            if truth["export_identity"] == G.IDENTITY_UNPROVEN
            else "this writer puts no body name in the file at all -- "
                 "measured on CadQuery 2.8.0 and FreeCAD 1.0.0, which both "
                 "write the translator's own product string -- so nothing "
                 "here is evidence about identity in either direction"
        ),
        "names_checked": tuple(truth["export_names"]),
        "passed": (
            row is not None
            and reached >= wanted
            and writer_right is True
        ),
    }


# ------------------------------------------------------------- geometry


def grade_geometry(observation: Mapping[str, Any]) -> Dict[str, Any]:
    """Per-body volumes as an unordered multiset, and the body set by id."""
    truth = G.expected(observation["case"])
    section = observation.get("geometry") or {}
    per_body = section.get("bodies") or {}

    # By ID, and as a SET: which body the executor happened to build first
    # says nothing about the part.
    got_ids = set(map(str, per_body))
    want_ids = set(map(str, truth["body_ids"]))
    volumes = [float(m.get("volume")) for m in per_body.values()
               if m.get("volume") is not None]

    checks = {
        "succeeded": bool(section.get("succeeded")),
        "body_ids": got_ids == want_ids,
        "body_count": len(per_body) == len(truth["body_ids"]),
        # Per body, never summed. Two disjoint solids fused have exactly the
        # total of the two apart.
        "volumes": _match_multiset(volumes, truth["volumes"],
                                   truth["volume_tolerance"]),
        # A declaration is required exactly when more than one body stands
        # (rule P34), and a single-body plan must declare NONE.
        "declared": (
            set(map(str, section.get("declared") or ())) == want_ids
            if len(truth["body_ids"]) > 1
            else not (section.get("declared") or ())
        ),
        # `part` is the single live body or None. Widening it to "the first
        # one" is the Stage 62 bug by another name.
        # Compared as a one-tuple against the whole tuple rather than by
        # indexing it: `body_ids[0]` would read as "the first body" and is
        # the very shape this instrument forbids, even where a length guard
        # makes it safe. "The only body is the only body" needs no index.
        "single_live_body": (
            (section.get("single_live_body"),) == tuple(truth["body_ids"])
            if len(truth["body_ids"]) == 1
            else section.get("single_live_body") is None
        ),
    }
    return {"checks": checks, "volumes": volumes,
            "passed": all(checks.values())}


# ------------------------------------------------------------ the verdict


def grade(observation: Mapping[str, Any]) -> Dict[str, Any]:
    """Score one observation against the immutable truth for its case.

    The truth is fetched BY NAME from :func:`ground_truth76.expected`. This
    function never constructs, adjusts or infers an expectation.
    """
    truth = G.expected(observation["case"])
    geometry = grade_geometry(observation)
    measurement = grade_measurement(observation)
    export = grade_export(observation)
    source = observation.get("source")
    return {
        "case": observation["case"],
        "group": truth["group"],
        "source": source,
        # Repeated on the VERDICT as well as the observation, because a
        # verdict is what gets quoted and a rate whose provenance has to be
        # looked up elsewhere gets quoted without it.
        "is_live_model_result": source in G.COUNTS_AS_MODEL_EVIDENCE,
        "geometry": geometry,
        "measurement": measurement,
        "export": export,
        # Deliberately an AND of three, and deliberately reported beside its
        # three parts rather than instead of them: a part that builds and
        # measures correctly and exports as one fused solid is not a
        # success, and a single "score" would let it look like 2/3 of one.
        "passed": (geometry["passed"] and measurement["passed"]
                   and export["passed"]),
    }


def summarise(verdicts: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Rates, kept apart. **There is deliberately no combined number.**

    Measurement and export are different quantities measured by different
    means, and so are the multi-body and single-body groups. Stage 75's
    `summarise` refuses to produce a combined rate for creation and refusal
    for the same reason: a rate that mixes two quantities is one a reader
    cannot act on, and it is how an instrument flatters itself.
    """
    def _rate(rows: Sequence[Mapping[str, Any]], section: str) -> Dict[str, Any]:
        total = len(rows)
        passed = sum(1 for r in rows if r[section]["passed"])
        return {"passed": passed, "of": total}

    by_group: Dict[str, List[Mapping[str, Any]]] = {}
    for verdict in verdicts:
        by_group.setdefault(str(verdict["group"]), []).append(verdict)

    live = [v for v in verdicts if v.get("is_live_model_result")]
    return {
        "cases": len(verdicts),
        # Never pooled with each other, and never with the groups below.
        "geometry": _rate(verdicts, "geometry"),
        "measurement": _rate(verdicts, "measurement"),
        "export": _rate(verdicts, "export"),
        "by_group": {
            group: {
                "cases": len(rows),
                "geometry": _rate(rows, "geometry"),
                "measurement": _rate(rows, "measurement"),
                "export": _rate(rows, "export"),
            }
            for group, rows in sorted(by_group.items())
        },
        # A run of deterministic fixtures says a great deal about the
        # observer and nothing about a model. Stated as a count on every
        # summary so a reader never has to go looking for it.
        "live_model_results": len(live),
        "export_levels": {
            level: sum(1 for v in verdicts if v["export"]["level"] == level)
            for level in G.EXPORT_LEVELS
        },
        "combined_rate": None,
        "why_no_combined_rate": (
            "measurement and export are different quantities, and a "
            "multi-body case and a single-body case ask different questions; "
            "a number that mixes them is one nobody can act on"
        ),
    }


__all__ = [
    "export_level", "grade", "grade_export", "grade_geometry",
    "grade_measurement", "summarise",
]
