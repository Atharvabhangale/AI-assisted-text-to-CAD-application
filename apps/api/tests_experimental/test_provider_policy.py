"""The AI provider usage policy, as guards rather than as prose.

CLAUDE.md used to say "Nothing in Claude Code Web may use or request
[the credential]". That restriction was retired: when a credential is
available, a real provider is to be used, and live calls are encouraged for
schema experiments, semantic-quality measurement and product verification.

What did NOT change is the architecture: the CORE stays vendor-neutral.
"Provider-independent" means the canonical pipeline does not depend on a
vendor -- never that real vendor calls are forbidden. These tests pin both
halves, because the previous wording drifted for want of anything checking
it.
"""

from __future__ import annotations

import pathlib
import unittest

from cad_experimental import config as experimental_config


REPO = pathlib.Path(__file__).resolve().parents[3]


class CredentialPrecedenceTests(unittest.TestCase):
    """Policy 9: the precedence is documented, not implicit."""

    def test_both_names_are_recognised(self) -> None:
        self.assertEqual(
            experimental_config.CREDENTIAL_PRECEDENCE,
            ("CAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        )

    def test_the_operator_supplied_name_wins(self) -> None:
        """`CAD_ANTHROPIC_API_KEY` is set deliberately for this project;
        `ANTHROPIC_API_KEY` may be ambient. The deliberate one must win, so
        that "the key I exported for this run" is the key used."""
        both = {"CAD_ANTHROPIC_API_KEY": "operator",
                "ANTHROPIC_API_KEY": "ambient"}
        self.assertEqual(experimental_config.credential_variable(both),
                         "CAD_ANTHROPIC_API_KEY")
        bridged = dict(both)
        came_from = experimental_config.bridge_credential(bridged)
        self.assertEqual(came_from, "CAD_ANTHROPIC_API_KEY")
        self.assertEqual(bridged["ANTHROPIC_API_KEY"], "operator")

    def test_either_name_alone_counts_as_available(self) -> None:
        for name in experimental_config.CREDENTIAL_PRECEDENCE:
            with self.subTest(name):
                self.assertTrue(
                    experimental_config.credential_available({name: "x"}))
        self.assertFalse(experimental_config.credential_available({}))
        # Whitespace is not a credential.
        self.assertFalse(
            experimental_config.credential_available(
                {"CAD_ANTHROPIC_API_KEY": "   "}))

    def test_no_credential_bridges_to_nothing(self) -> None:
        empty: dict = {}
        self.assertIsNone(experimental_config.bridge_credential(empty))
        self.assertEqual(empty, {})

    def test_the_bridge_returns_a_name_and_never_a_value(self) -> None:
        """A name is safe to log, put in a report, or show in a browser.

        The whole credential discipline rests on the value never leaving, so
        this asserts the return type is the VARIABLE NAME -- which means a
        caller that prints what it got cannot print a secret.
        """
        environment = {"CAD_ANTHROPIC_API_KEY": "s3cret-not-a-real-key"}
        came_from = experimental_config.bridge_credential(environment)
        self.assertIn(came_from, experimental_config.CREDENTIAL_PRECEDENCE)
        self.assertNotIn("s3cret", str(came_from))


class TheCoreStaysVendorNeutralTests(unittest.TestCase):
    """The half of the policy that did NOT change.

    Live vendor calls are encouraged. Vendor code in the canonical pipeline
    is still forbidden. Loosening the first must not loosen the second, so
    this is asserted rather than trusted -- it is the guard that lets the
    policy be permissive about calls without becoming permissive about
    coupling.
    """

    #: Every module a request passes through from canonical intent to a
    #: RenderModel. None may import a vendor SDK or the provider layer.
    CANONICAL_PIPELINE = (
        "interpretation", "normalize", "intent", "parser", "validation",
        "graph", "history", "executor", "adapter", "plan", "pattern",
        "edge_semantics", "cad_backend", "cadquery_backend",
        "freecad_backend", "build", "session", "questions",
        "local_intent_provider",
    )

    #: Names whose import into the canonical pipeline would be the coupling
    #: the policy forbids.
    FORBIDDEN_IMPORTS = ("anthropic", "openai", "cad_ai")

    def _source(self, module: str) -> str:
        path = (REPO / "apps" / "api" / "src" / "cad_experimental"
                / f"{module}.py")
        self.assertTrue(path.exists(), path)
        return path.read_text(encoding="utf-8")

    def test_no_canonical_module_imports_a_vendor_sdk(self) -> None:
        import ast

        for module in self.CANONICAL_PIPELINE:
            with self.subTest(module):
                tree = ast.parse(self._source(module))
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(a.name.split(".")[0]
                                        for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.add(node.module.split(".")[0])
                for forbidden in self.FORBIDDEN_IMPORTS:
                    self.assertNotIn(forbidden, imported, module)

    def test_edge_semantics_still_imports_only_the_standard_library(
        self,
    ) -> None:
        """Stage 47's strongest isolation claim, still true."""
        import ast

        tree = ast.parse(self._source("edge_semantics"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
        self.assertLessEqual(modules, {"__future__", "dataclasses", "typing"})


class OutcomeVocabularyTests(unittest.TestCase):
    """Policy 10: never claim live success without a live call.

    The five words a result may be described by must exist as something
    checkable, so a report cannot blur "the model did it" into "a reader
    did it".
    """

    def test_the_five_outcomes_are_distinguishable(self) -> None:
        from cad_experimental.generation import PlanOutcome
        from cad_experimental.interpretation import (
            SOURCE_DETERMINISTIC, SOURCE_PROVIDER,
        )

        # MODEL_GENERATED vs DETERMINISTIC: the interpretation layer records
        # which route produced a plan, on every answer.
        self.assertNotEqual(SOURCE_PROVIDER, SOURCE_DETERMINISTIC)

        # REFUSED, PROVIDER_ERROR and the rest are provider outcomes, and
        # each is its own value rather than a shared "failed".
        values = {outcome.value for outcome in PlanOutcome}
        self.assertIn("generated", values)
        self.assertIn("unsupported", values)
        self.assertIn("needs_clarification", values)
        self.assertTrue(
            {"model_error", "invalid_model_output"} <= values, values)

    def test_a_deterministic_answer_is_labelled_as_one(self) -> None:
        """FALLBACK is not silent: the note says which route ran, and there
        are two different true sentences for the two different reasons."""
        from cad_experimental.interpretation import (
            DETERMINISTIC_NOTE, NO_MODEL_NOTE,
        )

        self.assertNotEqual(DETERMINISTIC_NOTE, NO_MODEL_NOTE)
        # One says the model fell short; the other says none was configured.
        # Saying the first when the second happened misdescribes the user's
        # own setup.
        self.assertIn("model", DETERMINISTIC_NOTE.lower())
        self.assertIn("no interpretation model", NO_MODEL_NOTE.lower())


if __name__ == "__main__":
    unittest.main()
