"""A local development provider. **NOT a model, and not a Claude result.**

The real Anthropic credential is unavailable in this environment, so the one
thing that cannot be exercised is the model call itself. Everything *after*
it can be, and this module exists to do exactly that: it hands a
pre-supplied operation plan to the same :class:`~cad_ai.provider.TextToCadModel`
boundary a real provider satisfies, so the plan travels the whole real path --

    fixture (or a caller's plan)
      -> cad_ai.provider.TextToCadModel boundary
      -> cad_experimental.generation.OperationPlanService
      -> parser            (the same allow-list parser)
      -> plan validation   (the same P1-P7 rules)
      -> V1 adapter        (the same translation)
      -> cad_core.validator (the existing, authoritative validator)
      -> cad_core CAD engine (the existing CadQuery/OpenCascade engine)
      -> RenderModel        (the existing tessellation)

-- with not one step stubbed, mocked or skipped except the network call.

**What this is not.** It is not a language model, it does not interpret
natural language, and its output is not a Claude result of any kind. It
returns text a developer supplied. Every result it produces is stamped
:data:`SOURCE_LABEL` (``"LOCAL_DEVELOPMENT_PLAN"``) and reports
``is_live_model_result: false``, and a test asserts that a caller cannot
mistake one for a model answer.

**What it deliberately cannot do.** It reads no credential and no
environment variable, opens no socket, imports no SDK, touches no file, and
executes nothing. It ignores the prompt it is given, because it does not
reason -- a fixture is chosen by name or supplied whole.

The real live harness (:mod:`cad_experimental.harness`) is untouched by this
module and cannot reach it: ``--live`` still requires ``ANTHROPIC_API_KEY``
and still builds the real Anthropic provider. When a credential exists, the
genuine comparison runs there, unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

from cad_ai.provider import ModelRequest, ModelResponse, ProviderError

#: Stamped on every result this module produces. The one string a caller
#: should look for before believing a number came from a model: it did not.
SOURCE_LABEL = "LOCAL_DEVELOPMENT_PLAN"

#: The provider name that appears in generation metadata. Deliberately not a
#: vendor name and deliberately not a model id.
PROVIDER_NAME = "local-development"

#: Stands where a model id would. Says what it is.
MODEL_NAME = "local-plan-fixture (not a model)"


def _box(identifier: str, x: float, y: float, z: float, **extra: Any) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"x": x, "y": y, "z": z}
    parameters.update(extra)
    return {"id": identifier, "type": "box", "parameters": parameters}


def _cylinder(
    identifier: str, diameter: float, height: float, **extra: Any
) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"diameter": diameter, "height": height}
    parameters.update(extra)
    return {"id": identifier, "type": "cylinder", "parameters": parameters}


#: The development fixtures. Hand-written plans, with the geometry each is
#: expected to produce recorded beside it so a build can be checked rather
#: than merely observed.
FIXTURES: Mapping[str, Dict[str, Any]] = {
    "box-100x60x10": {
        "description": "a rectangular plate, 100 x 60 x 10 mm",
        "expected_bounding_box": {"x": 100.0, "y": 60.0, "z": 10.0},
        "expected_volume_mm3": 100.0 * 60.0 * 10.0,
        "plan": {
            "status": "generated",
            "summary": "a rectangular plate 100 x 60 x 10 mm",
            "operations": [_box("body", 100, 60, 10)],
        },
    },
    "cylinder-d20-h50-z": {
        "description": "a cylinder, diameter 20 mm, height 50 mm, along +Z",
        "expected_bounding_box": {"x": 20.0, "y": 20.0, "z": 50.0},
        "expected_volume_mm3": None,  # computed from the diameter below
        "plan": {
            "status": "generated",
            "summary": "a cylinder 20 mm across and 50 mm tall along +Z",
            "operations": [
                _cylinder(
                    "body",
                    20,
                    50,
                    position={"x": 0, "y": 0, "z": 0},
                    axis="+Z",
                )
            ],
        },
    },
    "cylinder-d16-h30-x": {
        "description": "a cylinder, diameter 16 mm, height 30 mm, along +X",
        "expected_bounding_box": {"x": 30.0, "y": 16.0, "z": 16.0},
        "expected_volume_mm3": None,
        "plan": {
            "status": "generated",
            "summary": "a cylinder 16 mm across and 30 mm long along +X",
            "operations": [_cylinder("body", 16, 30, axis="+X")],
        },
    },
    "cylinder-d80-h100-z": {
        "description": "the plan from the stage brief, verbatim",
        "expected_bounding_box": {"x": 80.0, "y": 80.0, "z": 100.0},
        "expected_volume_mm3": None,
        "plan": {
            "status": "generated",
            "summary": "a cylinder 80 mm across and 100 mm tall along +Z",
            "operations": [
                _cylinder(
                    "body",
                    80,
                    100,
                    position={"x": 0, "y": 0, "z": 0},
                    axis="+Z",
                )
            ],
        },
    },
}

#: Fixture names, in a stable order.
FIXTURE_NAMES: Tuple[str, ...] = tuple(FIXTURES)


def _expected_volume(name: str) -> float:
    """The volume a fixture should build to, computed from its own numbers."""
    import math

    entry = FIXTURES[name]
    recorded = entry.get("expected_volume_mm3")
    if recorded is not None:
        return float(recorded)
    parameters = entry["plan"]["operations"][0]["parameters"]
    radius = float(parameters["diameter"]) / 2.0
    return math.pi * radius**2 * float(parameters["height"])


def fixture(name: str) -> Dict[str, Any]:
    """One fixture, by name. Raises :class:`KeyError` for an unknown name."""
    if name not in FIXTURES:
        raise KeyError(
            f"unknown fixture {name!r}; available: {', '.join(FIXTURE_NAMES)}"
        )
    entry = dict(FIXTURES[name])
    entry["expected_volume_mm3"] = _expected_volume(name)
    return entry


def fixture_plan(name: str) -> Dict[str, Any]:
    """The plan payload of one fixture."""
    return json.loads(json.dumps(fixture(name)["plan"]))


def describe_fixtures() -> List[Dict[str, Any]]:
    """Every fixture, for a development endpoint or a CLI listing."""
    return [
        {
            "name": name,
            "description": FIXTURES[name]["description"],
            "expected_bounding_box": FIXTURES[name]["expected_bounding_box"],
            "expected_volume_mm3": _expected_volume(name),
            "source": SOURCE_LABEL,
        }
        for name in FIXTURE_NAMES
    ]


class LocalPlanProvider:
    """Returns a developer-supplied plan at the provider boundary.

    Satisfies :class:`~cad_ai.provider.TextToCadModel` structurally, so
    :class:`~cad_experimental.generation.OperationPlanService` treats it
    exactly as it treats a real provider -- which is the point: the parser,
    the validator and the outcome mapping all run for real.

    It does **not** read :attr:`ModelRequest.user_text`. There is no
    interpretation here, and pretending otherwise by matching on the text
    would make this look like a model.
    """

    #: The name that reaches generation metadata.
    name = PROVIDER_NAME

    #: True on this class and absent from every real provider, so a caller
    #: can tell a development result from a model result by type, not by
    #: reading a string.
    is_local_development = True

    def __init__(
        self,
        *,
        plan: Optional[Mapping[str, Any]] = None,
        fixture_name: Optional[str] = None,
        raw_text: Optional[str] = None,
    ) -> None:
        """Give exactly one of ``plan``, ``fixture_name`` or ``raw_text``.

        ``raw_text`` exists so a developer can feed deliberately malformed
        text through the real parser and watch it be rejected -- the failure
        paths deserve exercising too.
        """
        supplied = [
            value is not None for value in (plan, fixture_name, raw_text)
        ]
        if sum(supplied) != 1:
            raise ValueError(
                "give exactly one of plan, fixture_name or raw_text"
            )
        if fixture_name is not None:
            self._text = json.dumps(fixture_plan(fixture_name))
            self._fixture = fixture_name
        elif plan is not None:
            self._text = json.dumps(plan)
            self._fixture = None
        else:
            self._text = raw_text or ""
            self._fixture = None
        self.requests: List[ModelRequest] = []

    @property
    def fixture_name(self) -> Optional[str]:
        return self._fixture

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Hand back the supplied text. No network, no reasoning, no retry."""
        self.requests.append(request)
        return ModelResponse(
            text=self._text,
            provider=PROVIDER_NAME,
            model=MODEL_NAME,
            # No schema was enforced by anything: nothing constrained this
            # text, so claiming structured output would be a lie.
            structured_output=False,
            stop_reason="local_fixture",
            usage={},
        )


class UnavailableProvider:
    """Raises, so "no credential" can be exercised as the outcome it is.

    Not part of the development path; used by tests to show a provider
    failure stays a provider failure and never becomes a wrong answer.
    """

    name = PROVIDER_NAME
    is_local_development = True

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise ProviderError(
            "no interpretation model is configured",
            detail="local development provider: nothing to call",
        )


def stamp(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Mark a payload as local development output. Called on every result.

    Two fields rather than one: a human reads ``source``, and a program
    checks ``is_live_model_result``. Neither can be omitted by accident,
    because everything goes through here.
    """
    payload["source"] = SOURCE_LABEL
    payload["is_live_model_result"] = False
    payload["note"] = (
        "Local development plan. NOT a Claude/Anthropic result: no model "
        "was called and no credential was used. The real comparison runs "
        "through cad_experimental.harness --live."
    )
    return payload


# --- the development CLI ---------------------------------------------------


def run_fixture(name: str, *, build: bool = True) -> Dict[str, Any]:
    """Push one fixture through the entire real path and report every step."""
    # Imported here so the module's own import stays free of the CAD kernel.
    from cad_core.application_service import CadApplicationService

    from .build import build_plan
    from .config import ExperimentalConfig
    from .generation import OperationPlanService

    entry = fixture(name)
    provider = LocalPlanProvider(fixture_name=name)
    service = OperationPlanService(
        provider, ExperimentalConfig(model=MODEL_NAME, provider=PROVIDER_NAME)
    )

    generation = service.generate(entry["description"])
    result: Dict[str, Any] = {
        "fixture": name,
        "description": entry["description"],
        "generation_outcome": generation.outcome.value,
        "parsed": generation.plan is not None,
        "plan_valid": bool(
            generation.plan_validation is not None
            and generation.plan_validation.valid
        ),
        "plan": (
            generation.plan.to_dict() if generation.plan is not None else None
        ),
        "provider": generation.metadata.provider,
        "model": generation.metadata.model,
    }

    if generation.plan is None:
        result["error"] = generation.error
        return stamp(result)

    from .adapter import plan_to_document

    document = plan_to_document(generation.plan, name=name)
    result["v1_document"] = document
    result["v1_conversion"] = "ok"

    if not build:
        return stamp(result)

    import tempfile

    cad = CadApplicationService.local(tempfile.mkdtemp())

    # The existing validator's verdict, recorded on its own so it is never
    # confused with the plan validator's.
    validation = cad.validate_document(document)
    result["existing_validator_valid"] = validation.valid
    result["document_hash"] = validation.document_hash

    built = build_plan(cad, generation.plan, name=name)
    outcome = built.outcome
    result["built"] = built.built
    if outcome is None or not built.built:
        result["build_error"] = (
            built.error
            if outcome is None
            else (outcome.error.message if outcome.error else "build failed")
        )
        return stamp(result)

    geometry = outcome.artifact("geometry")
    details = geometry.details if geometry is not None else {}
    box = (details.get("bounding_box") or {}).get("size")
    volume = details.get("volume_mm3")
    expected_volume = entry["expected_volume_mm3"]

    result.update(
        {
            "build_key": outcome.build_key,
            "solid_count": details.get("solid_count"),
            "is_solid": details.get("is_solid"),
            "face_count": details.get("face_count"),
            "edge_count": details.get("edge_count"),
            "volume_mm3": volume,
            "expected_volume_mm3": expected_volume,
            "bounding_box": box,
            "expected_bounding_box": entry["expected_bounding_box"],
        }
    )

    import math

    result["volume_matches"] = (
        volume is not None
        and math.isclose(volume, expected_volume, rel_tol=1e-6)
    )
    result["bounding_box_matches"] = box is not None and all(
        math.isclose(
            float(box[axis]),
            float(entry["expected_bounding_box"][axis]),
            rel_tol=1e-6,
        )
        for axis in ("x", "y", "z")
    )

    render = outcome.render_model
    if render is None:
        result["render_model"] = None
    else:
        result["render_model"] = {
            "triangles": render.triangle_count(),
            "vertices": len(render.vertices),
            "units": render.units,
            "coordinate_system": render.coordinate_system,
            "bounds": {
                "minimum": list(render.bounds.minimum),
                "maximum": list(render.bounds.maximum),
            },
        }
    return stamp(result)


def format_report(results: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("=" * 74)
    lines.append("LOCAL DEVELOPMENT PLAN -- architecture check")
    lines.append("=" * 74)
    lines.append("NOT a Claude/Anthropic result. No model was called.")
    lines.append("Plans are developer-supplied fixtures; everything after the")
    lines.append("provider boundary is the real implementation.")
    lines.append("")
    for entry in results:
        lines.append("-" * 74)
        lines.append(f"{entry['fixture']}  --  {entry['description']}")
        lines.append(
            f"  generation {entry['generation_outcome']}   parsed "
            f"{entry['parsed']}   plan_valid {entry['plan_valid']}"
        )
        if "v1_conversion" in entry:
            lines.append(
                f"  v1 conversion {entry['v1_conversion']}   existing "
                f"validator {entry.get('existing_validator_valid')}"
            )
        if "built" in entry:
            lines.append(
                f"  built {entry['built']}   solids "
                f"{entry.get('solid_count')}   faces "
                f"{entry.get('face_count')}"
            )
            lines.append(
                f"  volume {entry.get('volume_mm3')}  expected "
                f"{entry.get('expected_volume_mm3')}  match "
                f"{entry.get('volume_matches')}"
            )
            lines.append(
                f"  bbox   {entry.get('bounding_box')}  expected "
                f"{entry.get('expected_bounding_box')}  match "
                f"{entry.get('bounding_box_matches')}"
            )
            render = entry.get("render_model")
            lines.append(
                f"  render {render['triangles']} triangles, "
                f"{render['vertices']} vertices, {render['units']}"
                if render
                else "  render NONE"
            )
        if entry.get("build_error"):
            lines.append(f"  build error {entry['build_error']}")
        if entry.get("error"):
            lines.append(f"  error {entry['error']}")
    lines.append("=" * 74)
    built = sum(1 for e in results if e.get("built"))
    ok = sum(
        1
        for e in results
        if e.get("volume_matches") and e.get("bounding_box_matches")
    )
    lines.append(f"fixtures            {len(results)}")
    lines.append(f"built               {built}")
    lines.append(f"geometry as expected {ok}")
    lines.append("=" * 74)
    lines.append(
        "REAL HAIKU COMPARISON: STILL PENDING -- needs ANTHROPIC_API_KEY and "
        "cad_experimental.harness --live."
    )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cad_experimental.local_plan_provider",
        description=(
            "Push a developer-supplied operation plan through the real "
            "parser, validator, adapter, CAD engine and RenderModel. "
            "NOT a model call and NOT a Claude result."
        ),
    )
    parser.add_argument(
        "--list", action="store_true", help="list the fixtures and exit"
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=[],
        help=f"fixture to run (default: all). One of: {', '.join(FIXTURE_NAMES)}",
    )
    parser.add_argument(
        "--plan-file",
        default=None,
        help="a JSON file holding a plan to run instead of a fixture",
    )
    parser.add_argument(
        "--no-build", action="store_true", help="stop before the CAD build"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    arguments = parser.parse_args(argv)

    if arguments.list:
        for entry in describe_fixtures():
            print(f"{entry['name']:22s} {entry['description']}")
        return 0

    if arguments.plan_file is not None:
        with open(arguments.plan_file, encoding="utf-8") as handle:
            supplied = json.load(handle)
        results = [_run_supplied(supplied, build=not arguments.no_build)]
    else:
        names = arguments.fixture or list(FIXTURE_NAMES)
        for name in names:
            if name not in FIXTURES:
                print(f"unknown fixture {name!r}", file=sys.stderr)
                print(f"available: {', '.join(FIXTURE_NAMES)}", file=sys.stderr)
                return 2
        results = [
            run_fixture(name, build=not arguments.no_build) for name in names
        ]

    if arguments.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print(format_report(results))
    return 0


def _run_supplied(plan: Mapping[str, Any], *, build: bool) -> Dict[str, Any]:
    """Run a caller's own plan through the same path as a fixture."""
    from cad_core.application_service import CadApplicationService

    from .adapter import AdapterError, plan_to_document
    from .build import build_plan
    from .config import ExperimentalConfig
    from .generation import OperationPlanService

    provider = LocalPlanProvider(plan=plan)
    service = OperationPlanService(
        provider, ExperimentalConfig(model=MODEL_NAME, provider=PROVIDER_NAME)
    )
    generation = service.generate("a plan supplied by a developer")
    result: Dict[str, Any] = {
        "fixture": "(supplied)",
        "description": "a plan supplied on the command line",
        "generation_outcome": generation.outcome.value,
        "parsed": generation.plan is not None,
        "plan_valid": bool(
            generation.plan_validation is not None
            and generation.plan_validation.valid
        ),
        "plan": (
            generation.plan.to_dict() if generation.plan is not None else None
        ),
        "provider": generation.metadata.provider,
        "model": generation.metadata.model,
    }
    if generation.plan is None:
        result["error"] = generation.error
        return stamp(result)
    try:
        result["v1_document"] = plan_to_document(generation.plan)
        result["v1_conversion"] = "ok"
    except AdapterError as exc:
        result["v1_conversion"] = f"refused: {exc}"
        return stamp(result)
    if not build:
        return stamp(result)

    import tempfile

    cad = CadApplicationService.local(tempfile.mkdtemp())
    validation = cad.validate_document(result["v1_document"])
    result["existing_validator_valid"] = validation.valid
    built = build_plan(cad, generation.plan)
    result["built"] = built.built
    outcome = built.outcome
    if outcome is not None and built.built:
        geometry = outcome.artifact("geometry")
        details = geometry.details if geometry is not None else {}
        result.update(
            {
                "solid_count": details.get("solid_count"),
                "face_count": details.get("face_count"),
                "volume_mm3": details.get("volume_mm3"),
                "bounding_box": (details.get("bounding_box") or {}).get("size"),
            }
        )
        render = outcome.render_model
        result["render_model"] = (
            None
            if render is None
            else {
                "triangles": render.triangle_count(),
                "vertices": len(render.vertices),
                "units": render.units,
            }
        )
    elif outcome is not None:
        result["build_error"] = (
            outcome.error.message if outcome.error else "build failed"
        )
    else:
        result["build_error"] = built.error
    return stamp(result)


__all__ = [
    "FIXTURES",
    "FIXTURE_NAMES",
    "MODEL_NAME",
    "PROVIDER_NAME",
    "SOURCE_LABEL",
    "LocalPlanProvider",
    "UnavailableProvider",
    "describe_fixtures",
    "fixture",
    "fixture_plan",
    "format_report",
    "main",
    "run_fixture",
    "stamp",
]


if __name__ == "__main__":
    sys.exit(main())
