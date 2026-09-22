"""The prompt's identity, read before and after a run.

Stage 69 Phase 1 changes no prompt. This is the check that says so in the
record rather than in prose.
"""
from __future__ import annotations

from cad_experimental import prompt as prompt_module


def identity() -> dict:
    text = prompt_module.system_prompt()
    return {
        "version": prompt_module.PROMPT_VERSION,
        "fingerprint": prompt_module.prompt_fingerprint(),
        "characters": len(text),
    }
