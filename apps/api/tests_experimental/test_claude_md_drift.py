"""CLAUDE.md must not disagree with the code it describes.

CLAUDE.md is this project's persistent engineering memory: a fresh session
reads it instead of the chat history. That makes a stale sentence in it
worse than a missing one -- it is confidently wrong, and it was confidently
wrong for several stages at a time. The Stage 63 audit found the document
claiming ten operation types, a two-type vocabulary, a stage range ending
twelve stages early, and a credential rule that was no longer the intent.

So the load-bearing numbers are **derived from the code here** and looked up
in the document, rather than typed into both places and hoped to agree.

Scope, deliberately narrow: this guards the CURRENT-STATE facts. It says
nothing about the per-stage historical subsections, which are a record and
are supposed to keep saying what was true at the time.
"""

from __future__ import annotations

import pathlib
import re
import unittest

from cad_experimental import generation, plan, schema_ladder
from cad_experimental.config import CREDENTIAL_PRECEDENCE, DEFAULT_MODEL
from cad_experimental.prompt import PROMPT_VERSION, prompt_fingerprint

REPO = pathlib.Path(__file__).resolve().parents[3]

_WORDS = ("zero one two three four five six seven eight nine ten eleven "
          "twelve thirteen fourteen").split()


def _normalise(markdown: str) -> str:
    """Collapse whitespace and drop emphasis, so rewrapping is not a failure."""
    return re.sub(r"\s+", " ", markdown.replace("**", "").replace("*", ""))


class ClaudeMdStatesTheMeasuredFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.flat = _normalise(
            (REPO / "CLAUDE.md").read_text(encoding="utf-8")).lower()

    def _says(self, phrase: str, why: str) -> None:
        wanted = re.sub(r"\s+", " ", str(phrase).replace("**", "")).lower()
        self.assertIn(wanted, self.flat, f"CLAUDE.md omits {phrase!r}: {why}")

    def _does_not_say(self, phrase: str, why: str) -> None:
        unwanted = re.sub(r"\s+", " ", str(phrase).replace("**", "")).lower()
        self.assertNotIn(unwanted, self.flat,
                         f"CLAUDE.md still says {phrase!r}: {why}")

    # --- vocabulary -----------------------------------------------------

    def test_the_operation_count_is_current(self) -> None:
        count = len(plan.OPERATION_TYPES)
        self._says(f"{_WORDS[count]} operation types",
                   "the vocabulary size is the single most-repeated fact in "
                   "the document and has gone stale twice")
        for kind in plan.OPERATION_TYPES:
            self._says(kind, "every operation the language has must be named")

    def test_no_superseded_operation_count_is_stated_as_current(self) -> None:
        current = len(plan.OPERATION_TYPES)
        for count in range(2, current):
            self._does_not_say(
                f"the vocabulary is still {_WORDS[count]} types",
                "a superseded count must read as history, not as the "
                "present tense",
            )

    # --- the live route -------------------------------------------------

    def test_the_live_model_and_encoding_are_current(self) -> None:
        self._says(DEFAULT_MODEL, "the exact model must be named")
        self._says(generation.PLAN_SCHEMA_NAME,
                   "which encoding the live route sends")
        self._says(generation.PLAN_SCHEMA_INLINED,
                   "its measured inlined size")
        self._says(generation.PLAN_SCHEMA_FINGERPRINT[:32],
                   "its fingerprint, so a reader can tell whether a recorded "
                   "result came from this encoding")

    def test_the_prompt_identity_is_current(self) -> None:
        self._says(PROMPT_VERSION, "the prompt version")
        self._says(prompt_fingerprint()[:16], "the prompt fingerprint")

    def test_the_measured_ceiling_is_current(self) -> None:
        self._says(schema_ladder.KNOWN_ACCEPTED_INLINED,
                   "the largest grammar measured ACCEPTED live")
        self._says(schema_ladder.KNOWN_REFUSED_INLINED,
                   "the smallest measured REFUSED live")

    def test_the_frozen_stage_43_fingerprint_is_named(self) -> None:
        metrics = schema_ladder.grammar_metrics(plan.executable_schema())
        self._says(metrics["fingerprint"][:16],
                   "Stage 43's instrument must stay identifiable")

    # --- credentials ----------------------------------------------------

    def test_both_credential_names_and_the_precedence_are_documented(
        self,
    ) -> None:
        for name in CREDENTIAL_PRECEDENCE:
            self._says(name, "names may be documented; values never")
        self._says("CREDENTIAL_PRECEDENCE",
                   "policy 9: the precedence is written down, not implicit")

    def test_no_credential_value_is_present(self) -> None:
        """The one thing that must never be in this file.

        Checked by shape rather than by comparing against the live
        environment, so it holds whether or not a key is set here.
        """
        raw = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"sk-ant-[A-Za-z0-9_\-]{8,}", raw),
            "an Anthropic key shape appears in CLAUDE.md",
        )


if __name__ == "__main__":
    unittest.main()
