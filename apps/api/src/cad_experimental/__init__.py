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
Two operations -- ``box`` and ``cylinder`` -- are enough to find out, and are
deliberately all that is implemented.
"""

EXPERIMENT_NAME = "cad-operation-graph"

#: Bumped whenever the plan shape changes, so a saved measurement says which
#: representation it measured.
PLAN_SCHEMA_VERSION = "0.1.0"

__all__ = ["EXPERIMENT_NAME", "PLAN_SCHEMA_VERSION"]
