"""The AI interpretation layer: natural language to a V1 CAD document.

```
text -> TextToCadModel -> JSON -> cad_core validator -> AiGenerationResult
```

**The LLM is an interpreter, not the CAD engine.** It emits a CAD *data*
document and nothing else -- no Python, no CadQuery, no OpenCascade, no
FeatureScript, no STL, no STEP, no mesh, no code in any language -- and
nothing it returns is executed. The existing validator is the authority on
whether the document it produced is valid, and the existing build pipeline is
the only thing that turns a document into geometry.

This package lives beside the HTTP transport rather than inside ``cad_core``
because it needs a vendor SDK, and ``cad_core`` has none: no module in
``cad_core`` imports this package, this package's provider, or the SDK, and a
test asserts it.

Each provider's SDK import is lazy -- inside
:mod:`cad_ai.anthropic_provider` and :mod:`cad_ai.gemini_provider`
respectively -- so importing this package needs no provider SDK installed, and
no module outside a provider imports one.
"""

from __future__ import annotations

from cad_ai.config import (
    PROVIDER_NAMES,
    AiConfig,
    AiConfigurationError,
    config_from_environment,
    credential_available,
    resolve_provider,
)
from cad_ai.generation import (
    AiGenerationResult,
    GenerationMetadata,
    GenerationOutcome,
    TextToCadService,
)
from cad_ai.prompt import PROMPT_VERSION, prompt_fingerprint, system_prompt
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderNotConfigured,
    TextToCadModel,
)
from cad_ai.specification import document_schema, response_schema

__all__ = [
    "PROMPT_VERSION",
    "PROVIDER_NAMES",
    "AiConfig",
    "AiConfigurationError",
    "AiGenerationResult",
    "GenerationMetadata",
    "GenerationOutcome",
    "ModelRequest",
    "ModelResponse",
    "ProviderError",
    "ProviderNotConfigured",
    "TextToCadModel",
    "TextToCadService",
    "config_from_environment",
    "credential_available",
    "resolve_provider",
    "document_schema",
    "prompt_fingerprint",
    "response_schema",
    "system_prompt",
]
