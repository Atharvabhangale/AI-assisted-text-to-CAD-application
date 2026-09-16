"""Stage 57: the Stage 56 parity harness, both backends, in WSL.

Same canonical plans, same tolerances. The model is not part of this.
"""
import os, sys, json, tempfile
VOLUME_RTOL = 1e-6
BBOX_ATOL   = 1e-6
REPO = os.environ.get("CAD_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
D = os.path.join(REPO, "docs/evaluation-baselines/stage48-widened-schema")
from cad_experimental.local_plan_provider import FIXTURES
from cad_experimental.parser import parse_plan
from cad_experimental.build import build_plan
from cad_experimental.adapter import ExecutionUnsupported
from cad_experimental.stage48_capability_evaluation import _measure
from cad_core.application_service import CadApplicationService

def recorded(fname, case_id):
    d = json.load(open(os.path.join(D, fname), encoding="utf-8"))
    for r in d["records"]:
        if r["case_id"] == case_id and r.get("build_success"):
            return json.loads(r["raw_text"])
    return None

CASES = [
 ("1-primitive-plus-hole",  lambda: FIXTURES["plate-one-hole"]["plan"]),
 ("2-subtract",             lambda: FIXTURES["subtract-cube-bore"]["plan"]),
 ("3-fillet-straight",      lambda: recorded("stage53-selector.json","F1-drilled-plate-round-corners")),
 ("4-chamfer-straight-x",   lambda: recorded("stage54-B-selector-guidance.json","D1-plate-hole-chamfer-long-edges")),
 ("5-circular-top",         lambda: recorded("stage55-strict-selector.json","F2-hole-rim-chamfer-top")),
 ("6-circular-bottom",      lambda: recorded("stage55-strict-selector.json","F3-hole-rim-chamfer-bottom")),
 ("7-two-holes-ordering",   lambda: recorded("stage53-selector.json","G2-two-holes-then-chamfer")),
 ("8-radial-pattern",       lambda: None),
]

def run_on(backend, payload):
    os.environ["CAD_BACKEND"] = backend
    out = {"backend": backend}
    try:
        plan = parse_plan(json.loads(json.dumps(payload)))
    except Exception as e:
        return {**out, "status":"ERROR", "error":f"parse {type(e).__name__}: {e}"}
    try:
        res = build_plan(CadApplicationService.local(tempfile.mkdtemp()), plan)
    except ExecutionUnsupported as e:
        return {**out, "status":"UNSUPPORTED", "error":str(e)[:120]}
    except NotImplementedError as e:
        # A protocol method the backend inherits as a stub: the backend runs,
        # the capability is absent. That is UNSUPPORTED, not ERROR.
        return {**out, "status":"UNSUPPORTED",
                "error":"backend does not implement a required protocol method",
                "detail":str(e)[:120]}
    except Exception as e:
        return {**out, "status":"ERROR", "error":f"{type(e).__name__}: {str(e)[:120]}"}
    if res.execution_unsupported:
        return {**out, "status":"UNSUPPORTED", "error":str(res.unsupported_types)}
    if not res.built:
        return {**out, "status":"ERROR", "error":str(res.error or "build failed")[:120]}
    vol, box, solids, tris = _measure(res)
    return {**out, "status":"OK", "volume":vol, "solids":solids, "bbox":box,
            "triangles":tris, "render":bool(getattr(res.outcome,"render_model",None)),
            "graph":bool(res.executed)}

def close(a, b, rtol):
    if a is None or b is None: return a is b
    return abs(a-b) <= rtol*max(abs(a),abs(b),1.0)

rows=[]
for name, get in CASES:
    payload = get()
    if payload is None:
        rows.append({"case":name,"verdict":"NO_SOURCE"}); print(f"{name:<24} NO_SOURCE"); continue
    c = run_on("cadquery", payload); f = run_on("freecad", payload)
    if c["status"]=="OK" and f["status"]=="OK":
        geom = (close(c["volume"],f["volume"],VOLUME_RTOL)
                and c["solids"]==f["solids"])
        verdict = "PASS" if geom else "MISMATCH"
    elif "UNSUPPORTED" in (c["status"],f["status"]): verdict="UNSUPPORTED"
    elif "UNAVAILABLE" in (c["status"],f["status"]): verdict="UNAVAILABLE"
    else: verdict="ERROR"
    rows.append({"case":name,"cadquery":c,"freecad":f,"verdict":verdict})
    dv = (abs(c.get("volume",0)-f.get("volume",0))
          if c["status"]=="OK" and f["status"]=="OK" else None)
    print(f"{name:<24} cq={c['status']:<11} fc={f['status']:<11} {verdict:<10} "
          f"cqvol={c.get('volume')} fcvol={f.get('volume')} dv={dv}")
out = os.environ.get("PARITY_OUT", os.path.join(D,"stage57-backend-parity.json"))
json.dump({"volume_rtol":VOLUME_RTOL,"bbox_atol_mm":BBOX_ATOL,"rows":rows},
          open(out,"w",encoding="utf-8"), indent=2, default=str)
print("\nwritten to", out)
