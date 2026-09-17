"""The multi-turn copilot session: context, commit, undo, and the failure rule.

The rule most of these tests exist for: **a failed edit must never replace the
model that still builds.** A refusal, a clarification, an invalid plan and a
kernel failure are all different reasons, and every one of them has to leave
the previous part exactly where it was.

No model is called. A stub planner returns whatever outcome a test needs, so
what is measured is the session's own behaviour rather than Claude's.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient

from cad_core.application_service import CadApplicationService

from cad_experimental.app import (
    SESSION_MESSAGE_PATH,
    SESSION_RESET_PATH,
    SESSION_STATE_PATH,
    SESSION_UNDO_PATH,
    create_app,
)
from cad_experimental.config import ExperimentalConfig
from cad_experimental.generation import (
    PlanGenerationMetadata,
    PlanGenerationResult,
    PlanOutcome,
)
from cad_experimental.parser import parse_plan
from cad_experimental.session import (
    CadSession,
    Revision,
    SessionStore,
    describe_model,
    revision_context,
)
from cad_experimental.validation import PlanValidation

CONFIG = ExperimentalConfig()


def plate(width: float = 100.0, hole: bool = True) -> Dict[str, Any]:
    operations: List[Dict[str, Any]] = [
        {"id": "plate", "type": "box",
         "parameters": {"x": width, "y": 60.0, "z": 10.0}},
    ]
    if hole:
        operations.append(
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 8.0,
                            "position": {"x": 10.0, "y": 10.0, "z": 0.0}}})
    return {"status": "generated", "summary": f"a {width:g} mm plate",
            "operations": operations}


class StubPlanner:
    """A planner that returns scripted outcomes and records what it was asked."""

    def __init__(self, outcomes: List[PlanGenerationResult]) -> None:
        self._outcomes = list(outcomes)
        self.requests: List[Dict[str, Any]] = []

    @property
    def config(self) -> ExperimentalConfig:
        return CONFIG

    def generate(self, description, *, context=None, max_output_tokens=None):
        self.requests.append({
            "description": description,
            "context": context,
            "max_output_tokens": max_output_tokens,
        })
        return self._outcomes.pop(0)


def generated(payload: Dict[str, Any]) -> PlanGenerationResult:
    return PlanGenerationResult(
        outcome=PlanOutcome.GENERATED,
        metadata=PlanGenerationMetadata(provider="stub", model="stub"),
        plan=parse_plan(payload),
        plan_validation=PlanValidation(valid=True),
    )


def refused(outcome: PlanOutcome, **kwargs: Any) -> PlanGenerationResult:
    payload = {"status": outcome.value, "summary": "", **kwargs}
    return PlanGenerationResult(
        outcome=outcome,
        metadata=PlanGenerationMetadata(provider="stub", model="stub"),
        plan=parse_plan(payload),
        plan_validation=PlanValidation(valid=True),
    )


# --- the session object itself ---------------------------------------------


class SessionStateTests(unittest.TestCase):

    def revision(self, width: float = 100.0) -> Revision:
        return Revision(plan=plate(width), summary=f"{width:g} plate",
                        request="make it", measurement={"size": [width, 60, 10]})

    def test_a_new_session_has_no_model(self) -> None:
        session = CadSession(session_id="s")
        self.assertFalse(session.has_model)
        self.assertIsNone(session.undo())

    def test_commit_moves_the_current_model_and_stacks_the_old_one(self) -> None:
        session = CadSession(session_id="s")
        session.commit(self.revision(100))
        session.commit(self.revision(120))
        self.assertEqual(session.current.plan["operations"][0]["parameters"]["x"], 120)
        self.assertEqual(len(session.history), 1)

    def test_undo_restores_the_previous_plan(self) -> None:
        session = CadSession(session_id="s")
        session.commit(self.revision(100))
        session.commit(self.revision(120))
        restored = session.undo()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.plan["operations"][0]["parameters"]["x"], 100)
        self.assertFalse(session.history)

    def test_undo_past_the_beginning_returns_nothing_rather_than_raising(self):
        session = CadSession(session_id="s")
        session.commit(self.revision(100))
        self.assertIsNone(session.undo())

    def test_the_revision_stack_is_bounded(self) -> None:
        from cad_experimental.session import MAX_REVISIONS

        session = CadSession(session_id="s")
        for width in range(1, MAX_REVISIONS + 12):
            session.commit(self.revision(float(width)))
        self.assertLessEqual(len(session.history), MAX_REVISIONS)

    def test_the_conversation_is_bounded(self) -> None:
        session = CadSession(session_id="s")
        for index in range(400):
            session.said("user", f"turn {index}")
        self.assertLess(len(session.conversation), 400)

    def test_reset_clears_the_model_but_keeps_the_thread(self) -> None:
        session = CadSession(session_id="s")
        session.said("user", "build a plate")
        session.commit(self.revision(100))
        session.reset()
        self.assertFalse(session.has_model)
        self.assertFalse(session.history)
        self.assertTrue(session.conversation)

    def test_the_store_is_bounded_and_evicts_the_least_recently_used(self):
        from cad_experimental.session import MAX_SESSIONS

        store = SessionStore()
        for index in range(MAX_SESSIONS + 8):
            store.get(f"session-{index}")
        self.assertLessEqual(len(store), MAX_SESSIONS)

    def test_the_same_id_returns_the_same_session(self) -> None:
        store = SessionStore()
        self.assertIs(store.get("abc"), store.get("abc"))


class RevisionContextTests(unittest.TestCase):
    """What the model is shown when it is asked to modify a part."""

    def context(self) -> str:
        session = CadSession(session_id="s")
        session.said("user", "make a plate")
        session.said("assistant", "Created the plate.")
        session.commit(Revision(plan=plate(), summary="a plate",
                                request="make a plate",
                                measurement={"size": [100, 60, 10]}))
        return revision_context(session, "make it 120 mm wide")

    def test_it_carries_the_current_plan_verbatim(self) -> None:
        text = self.context()
        self.assertIn('"plate"', text)
        self.assertIn('"through_hole"', text)
        self.assertIn('"diameter"', text)

    def test_it_carries_the_recent_conversation(self) -> None:
        self.assertIn("Created the plate.", self.context())

    def test_it_carries_the_new_request(self) -> None:
        self.assertIn("make it 120 mm wide", self.context())

    def test_it_asks_for_a_complete_plan_not_a_patch(self) -> None:
        """The canonical IR has no patch form, and inventing one would be a
        second representation of the part."""
        self.assertIn("COMPLETE", self.context())

    def test_it_tells_the_model_to_ask_rather_than_guess(self) -> None:
        self.assertIn("ambiguous", self.context())

    def test_it_does_not_dump_execution_internals(self) -> None:
        """Edge indices, backends and render models help the model not at all
        and would make every request bigger."""
        text = self.context()
        for noise in ("render", "triangles", "freecad", "cadquery",
                      "execution_path", "candidates"):
            with self.subTest(noise=noise):
                self.assertNotIn(noise, text.lower())

    def test_the_summary_describes_the_part_in_one_line(self) -> None:
        revision = Revision(plan=plate(), summary="a plate", request="r",
                            measurement={"size": [100, 60, 10]})
        described = describe_model(revision)
        self.assertIn("box", described)
        self.assertIn("through_hole", described)
        self.assertIn("100", described)


# --- the route ---------------------------------------------------------------


class SessionRouteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def client(self, outcomes: List[PlanGenerationResult]) -> Any:
        planner = StubPlanner(outcomes)
        app = create_app(config=CONFIG, service=self.service, planner=planner)
        return TestClient(app), planner

    def message(self, client, text: str, session_id: str = "s") -> Dict[str, Any]:
        response = client.post(SESSION_MESSAGE_PATH,
                               json={"session_id": session_id, "text": text})
        return response.json()

    def test_a_first_request_builds_and_carries_no_context(self) -> None:
        client, planner = self.client([generated(plate())])
        body = self.message(client, "make a plate")
        self.assertEqual(body["status"], "built")
        self.assertFalse(body["editing"])
        self.assertIsNone(planner.requests[0]["context"])
        self.assertTrue(body["session"]["has_model"])

    def test_a_second_request_is_given_the_current_plan(self) -> None:
        client, planner = self.client([generated(plate()), generated(plate(120))])
        self.message(client, "make a plate")
        body = self.message(client, "make it 120 mm wide")
        self.assertTrue(body["editing"])
        context = planner.requests[1]["context"]
        self.assertIsNotNone(context)
        self.assertIn('"plate"', context)
        self.assertIn("make it 120 mm wide", context)
        self.assertEqual(body["status"], "built")

    def test_a_revision_gets_a_larger_reply_budget(self) -> None:
        """It must return the whole part, not one operation."""
        client, planner = self.client([generated(plate()), generated(plate(120))])
        self.message(client, "make a plate")
        self.message(client, "wider")
        self.assertIsNone(planner.requests[0]["max_output_tokens"])
        self.assertIsNotNone(planner.requests[1]["max_output_tokens"])

    def test_the_viewport_payload_carries_a_render_model(self) -> None:
        client, _ = self.client([generated(plate())])
        body = self.message(client, "make a plate")
        self.assertIn("render", body)
        self.assertTrue(body["render"]["triangles"])

    # --- the failure rule ------------------------------------------------

    def test_an_unsupported_edit_leaves_the_model_untouched(self) -> None:
        client, _ = self.client([
            generated(plate()),
            refused(PlanOutcome.UNSUPPORTED, reason="no gears here"),
        ])
        self.message(client, "make a plate")
        body = self.message(client, "make it an involute gear")
        self.assertEqual(body["status"], "unsupported")
        self.assertTrue(body["session"]["has_model"])
        self.assertIn("plate", json.dumps(body["session"]["current"]))
        # and nothing was drawn over the top of the good model
        self.assertNotIn("render", body)

    def test_a_clarification_builds_nothing_and_keeps_the_model(self) -> None:
        client, _ = self.client([
            generated(plate()),
            refused(PlanOutcome.NEEDS_CLARIFICATION,
                    questions=["Which hole, and where should it move?"]),
        ])
        self.message(client, "make a plate")
        body = self.message(client, "move the hole")
        self.assertEqual(body["status"], "needs_clarification")
        self.assertIn("Which hole", body["reply"])
        self.assertTrue(body["session"]["has_model"])
        self.assertNotIn("render", body)

    def test_a_plan_that_fails_to_build_keeps_the_previous_part(self) -> None:
        """A 500 mm fillet on a 10 mm plate: valid plan, impossible geometry."""
        impossible = {
            "status": "generated", "summary": "over-filleted",
            "operations": plate()["operations"] + [
                {"id": "edges", "type": "fillet", "target": "plate",
                 "parameters": {"radius": 500.0,
                                "edges": {"select": "straight", "axis": "Z"}}}],
        }
        client, _ = self.client([generated(plate()), generated(impossible)])
        self.message(client, "make a plate")
        body = self.message(client, "fillet it enormously")
        self.assertEqual(body["status"], "build_failed")
        self.assertTrue(body["session"]["has_model"])
        current = json.dumps(body["session"]["current"])
        self.assertIn("plate", current)
        self.assertNotIn("500", current)

    def test_a_failed_edit_does_not_grow_the_undo_stack(self) -> None:
        client, _ = self.client([
            generated(plate()),
            refused(PlanOutcome.UNSUPPORTED, reason="no"),
        ])
        self.message(client, "make a plate")
        body = self.message(client, "make it a gear")
        self.assertEqual(body["session"]["revisions"], 0)

    # --- undo and reset ----------------------------------------------------

    def test_undo_restores_and_rebuilds_the_previous_plan(self) -> None:
        client, _ = self.client([generated(plate()), generated(plate(120))])
        self.message(client, "make a plate")
        self.message(client, "make it 120 wide")
        body = client.post(SESSION_UNDO_PATH, json={"session_id": "s"}).json()
        self.assertEqual(body["status"], "built")
        self.assertEqual(body["plan"]["operations"][0]["parameters"]["x"], 100.0)
        # rebuilt, not merely remembered
        self.assertIn("render", body)

    def test_undo_with_nothing_behind_it_says_so(self) -> None:
        client, _ = self.client([generated(plate())])
        self.message(client, "make a plate")
        body = client.post(SESSION_UNDO_PATH, json={"session_id": "s"}).json()
        self.assertEqual(body["status"], "nothing_to_undo")
        self.assertTrue(body["session"]["has_model"])

    def test_reset_clears_the_part_but_keeps_the_thread(self) -> None:
        client, _ = self.client([generated(plate())])
        self.message(client, "make a plate")
        body = client.post(SESSION_RESET_PATH, json={"session_id": "s"}).json()
        self.assertFalse(body["session"]["has_model"])
        self.assertTrue(body["session"]["conversation"])

    def test_after_reset_the_next_request_is_a_first_request_again(self) -> None:
        client, planner = self.client([generated(plate()), generated(plate())])
        self.message(client, "make a plate")
        client.post(SESSION_RESET_PATH, json={"session_id": "s"})
        body = self.message(client, "make another plate")
        self.assertFalse(body["editing"])
        self.assertIsNone(planner.requests[1]["context"])

    # --- sessions are separate --------------------------------------------

    def test_two_sessions_do_not_share_a_model(self) -> None:
        client, _ = self.client([generated(plate()), generated(plate(120))])
        self.message(client, "make a plate", session_id="one")
        body = self.message(client, "make a plate", session_id="two")
        self.assertFalse(body["editing"])

    def test_state_reports_without_changing_anything(self) -> None:
        client, _ = self.client([generated(plate())])
        self.message(client, "make a plate")
        first = client.post(SESSION_STATE_PATH, json={"session_id": "s"}).json()
        second = client.post(SESSION_STATE_PATH, json={"session_id": "s"}).json()
        self.assertTrue(first["session"]["has_model"])
        self.assertEqual(first["session"]["revisions"],
                         second["session"]["revisions"])

    def test_the_conversation_records_what_actually_happened(self) -> None:
        client, _ = self.client([generated(plate()), generated(plate(120))])
        self.message(client, "make a plate")
        body = self.message(client, "make it 120 mm wide")
        roles = [turn["role"] for turn in body["session"]["conversation"]]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])
        self.assertIn("Updated", body["session"]["conversation"][-1]["text"])


class ParametricEditingTests(unittest.TestCase):
    """An edit must change what was asked for and nothing else."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def client(self, outcomes):
        planner = StubPlanner(outcomes)
        return TestClient(create_app(config=CONFIG, service=self.service,
                                     planner=planner)), planner

    def send(self, client, text):
        return client.post(SESSION_MESSAGE_PATH,
                           json={"session_id": "p", "text": text}).json()

    def test_changing_the_width_leaves_the_hole_alone(self) -> None:
        client, _ = self.client([generated(plate(100)), generated(plate(120))])
        self.send(client, "make a plate with a hole")
        body = self.send(client, "make it 120 mm wide")
        operations = body["plan"]["operations"]
        self.assertEqual(operations[0]["parameters"]["x"], 120.0)
        hole = next(o for o in operations if o["type"] == "through_hole")
        self.assertEqual(hole["parameters"]["diameter"], 8.0)
        self.assertEqual(hole["parameters"]["position"]["x"], 10.0)

    def test_operation_ids_survive_an_edit(self) -> None:
        """Ids are how a later request refers to a feature, so losing them
        loses the conversation's grip on the part."""
        client, _ = self.client([generated(plate(100)), generated(plate(120))])
        first = self.send(client, "make a plate")
        second = self.send(client, "make it wider")
        self.assertEqual(
            [o["id"] for o in first["plan"]["operations"]],
            [o["id"] for o in second["plan"]["operations"]],
        )

    def test_the_revision_context_tells_the_model_to_change_only_what_was_asked(
        self,
    ) -> None:
        session = CadSession(session_id="s")
        session.commit(Revision(plan=plate(), summary="a plate", request="r",
                                measurement={"size": [100, 60, 10]}))
        context = revision_context(session, "make it wider")
        self.assertIn("change only what the request asks for", context)
        self.assertIn("Keep the ids", context)


class MeasurementAnswerTests(unittest.TestCase):
    """Measurements come from the executor, never from the model."""

    def session(self) -> CadSession:
        session = CadSession(session_id="m")
        session.commit(Revision(
            plan=plate(), summary="a plate", request="r", backend="freecad",
            measurement={"size": [100, 60, 10], "volume": 59497.345,
                         "face_count": 7, "edge_count": 15, "solid_count": 1}))
        return session

    def test_it_answers_a_volume_question_from_evidence(self) -> None:
        from cad_experimental.session import measurement_answer

        answer = measurement_answer(self.session(), "What's the volume?")
        self.assertIsNotNone(answer)
        self.assertIn("59497.345", answer)
        self.assertIn("freecad", answer)

    def test_it_answers_a_dimensions_question(self) -> None:
        from cad_experimental.session import measurement_answer

        answer = measurement_answer(self.session(),
                                    "What are the overall dimensions?")
        self.assertIn("100 x 60 x 10", answer)

    def test_it_declines_anything_that_is_not_a_measurement_question(self):
        """Declining is the safe direction: the cost of returning None is one
        model call, the cost of the opposite is a fabricated dimension."""
        from cad_experimental.session import measurement_answer

        for request in ("make it 120 mm wide", "add a hole",
                        "fillet the edges", "undo that"):
            with self.subTest(request=request):
                self.assertIsNone(measurement_answer(self.session(), request))

    def test_it_declines_when_there_is_no_model(self) -> None:
        from cad_experimental.session import measurement_answer

        self.assertIsNone(
            measurement_answer(CadSession(session_id="x"), "what is the volume?"))


if __name__ == "__main__":
    unittest.main()
