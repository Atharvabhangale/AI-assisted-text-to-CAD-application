"""An experimental CAD **operation plan** path, parallel to the stable one.

The stable path (``cad_ai``) asks a model for a canonical V1 CAD document and
validates it with ``cad_core``'s validator. This package asks for something
smaller -- a list of typed *operations* -- and adapts that into the very same
canonical document, so the existing validator, engine, cache and exporters do
all the real work exactly as before.

Nothing here replaces or imports its way into the stable path. ``cad_ai`` and
``cad_api`` do not import this package, and this package adds no rule, no
geometry and no schema to ``cad_core``.

The question it exists to answer is narrow and empirical: **is an operation
plan easier for a model to produce correctly than the full V1 document?**

It began with two operations -- ``box`` and ``cylinder`` -- as enough to find
out. The vocabulary is now **eleven** types: ``box``, ``cylinder``,
``through_hole``, ``subtract``, ``union``, ``fillet``, ``chamfer``,
``pattern``, ``sketch``, ``extrude`` and ``revolve``. Eight are executable;
``sketch``, ``extrude`` and ``revolve`` are represented and validated, then
refused at the execution boundary rather than approximated.
:data:`cad_experimental.plan.OPERATION_TYPES` is the authority.

(This docstring still claimed two until Stage 62's audit -- the first thing
any reader of the package saw, nine operations out of date.)
"""

EXPERIMENT_NAME = "cad-operation-graph"

#: Bumped whenever the plan shape changes, so a saved measurement says which
#: representation it measured.
PLAN_SCHEMA_VERSION = "0.1.0"

__all__ = ["EXPERIMENT_NAME", "PLAN_SCHEMA_VERSION"]
