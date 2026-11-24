"""End-to-end automated verification suite for the 8 Acceptance Matrix scenarios.

Tests all 4 stages (Classifier -> Deterministic Tool Registry -> Async Orchestrator
+ Parallel Tool Execution -> SSE Contract) plus ASGI streaming ContextVar safety,
DOMPurify/rAF frontend wiring, and Gemini 503 outage resilience.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from contextlib import redirect_stdout
from langchain_core.messages import AIMessageChunk

import database
from chat_activity_log import get_turn_log
from erp_ai.chat.cache import slot_store, tool_cache
from erp_ai.chat.classifier import classify_intents
from erp_ai.chat.orchestrator import run_chat_turn, stream_chat_turn
from erp_ai.chat.prompts import AI_BUSY_MESSAGE
from erp_ai.chat.schemas import (
    AuthContext,
    IntentCategory,
    StudentClassificationOutput,
)
from erp_ai.chat.tool_registry import get_tool_names_for_intents
import gemini_agent


def _parse_sse_frames(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    raw = "".join(chunks)
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        ev = "message"
        data_obj: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data_obj = json.loads(line[5:].strip())
        frames.append((ev, data_obj))
    return frames


def _load_real_auth_contexts() -> tuple[AuthContext, AuthContext]:
    with database.get_connection() as conn:
        s_row = conn.execute(
            """
            SELECT u.id, u.email, s.id, s.semester
            FROM users u
            JOIN students s ON s.user_id = u.id
            WHERE u.email = 'student1@erp.test'
            """
        ).fetchone()
        if not s_row:
            s_row = conn.execute(
                """
                SELECT u.id, u.email, s.id, s.semester
                FROM users u
                JOIN students s ON s.user_id = u.id
                LIMIT 1
                """
            ).fetchone()
        assert s_row, "No student account found in DB"

        t_row = conn.execute(
            """
            SELECT u.id, u.email, t.id, t.employee_id, t.full_name, t.department
            FROM users u
            JOIN teachers t ON t.user_id = u.id
            WHERE u.email = 'teacher1@erp.test'
            """
        ).fetchone()
        if not t_row:
            t_row = conn.execute(
                """
                SELECT u.id, u.email, t.id, t.employee_id, t.full_name, t.department
                FROM users u
                JOIN teachers t ON t.user_id = u.id
                LIMIT 1
                """
            ).fetchone()
        assert t_row, "No teacher account found in DB"

    student_ctx = AuthContext(
        user_id=str(s_row[0]),
        role="student",
        email=s_row[1],
        student_id=str(s_row[2]),
        semester=int(s_row[3]),
    )
    teacher_ctx = AuthContext(
        user_id=str(t_row[0]),
        role="teacher",
        email=t_row[1],
        teacher_id=str(t_row[2]),
        teacher={
            "id": str(t_row[2]),
            "employee_id": t_row[3],
            "full_name": t_row[4],
            "department": t_row[5],
            "email": t_row[1],
        },
    )
    return student_ctx, teacher_ctx


class _ScriptedBoundLLM:
    """Deterministic async streaming tool-calling LLM stub when testing tool plumbing or outages."""

    def __init__(self, hops: list[Any], bound_tools: list[Any] | None = None) -> None:
        self._hops = hops
        self.bound_tools = bound_tools or []
        self.seen_messages: list[list[Any]] = []
        self._call_idx = 0

    def bind_tools(self, tools: list[Any]) -> "_ScriptedBoundLLM":
        self.bound_tools = list(tools)
        return self

    def with_structured_output(self, schema_cls: Any, **_kwargs: Any) -> "_ScriptedStructuredLLM":
        return _ScriptedStructuredLLM(self._hops[0])

    async def astream(self, messages: list[Any]):
        self.seen_messages.append(list(messages))
        idx = min(self._call_idx, len(self._hops) - 1)
        self._call_idx += 1
        step = self._hops[idx]
        if isinstance(step, BaseException):
            raise step
        for chunk in step:
            if isinstance(chunk, BaseException):
                raise chunk
            await asyncio.sleep(0)
            yield chunk


class _ScriptedStructuredLLM:
    def __init__(self, output: Any) -> None:
        self._output = output

    async def ainvoke(self, _messages: list[Any]) -> Any:
        if isinstance(self._output, BaseException):
            raise self._output
        return self._output


async def test_1_greeting_zero_db_zero_llm(student_ctx: AuthContext) -> None:
    tool_cache.clear()
    slot_store.clear()
    db_calls = 0
    llm_calls = 0
    orig_get_conn = database.get_connection

    def counting_conn():
        nonlocal db_calls
        db_calls += 1
        return orig_get_conn()

    def counting_llm(*args, **kwargs):
        nonlocal llm_calls
        llm_calls += 1
        raise AssertionError("LLM must NOT be invoked on greeting_help!")

    buf = io.StringIO()
    with patch.object(database, "get_connection", side_effect=counting_conn), patch(
        "erp_ai.chat.orchestrator.get_llm", side_effect=counting_llm
    ), patch("erp_ai.chat.classifier.get_llm", side_effect=counting_llm), redirect_stdout(buf):
        t0 = time.perf_counter()
        chunks = [c async for c in stream_chat_turn("hey there", student_ctx)]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

    frames = _parse_sse_frames(chunks)
    log_output = buf.getvalue()
    assert db_calls == 0, f"Expected 0 DB queries on greeting, got {db_calls}"
    assert llm_calls == 0, f"Expected 0 LLM calls on greeting, got {llm_calls}"
    assert elapsed_ms < 50.0, f"Expected fast exit (<50ms, target <20ms), took {elapsed_ms:.2f}ms"
    assert "ContextVar" not in log_output and "different Context" not in log_output
    assert "all_classified_intents=['greeting_help']" in log_output

    meta_frames = [p for ev, p in frames if ev == "meta"]
    token_frames = [p for ev, p in frames if ev == "token"]
    assert meta_frames and meta_frames[0]["intents"] == ["greeting_help"]
    assert meta_frames[0]["streaming"] is False
    assert len(token_frames) == 1, "Static greeting must arrive in 1 chunk (no 24-char slicing)"
    assert frames[-1][0] == "done"
    print(f"[PASS] Case 1 — Greeting ('hey there'): 0 DB, 0 LLM, {elapsed_ms:.2f} ms, 1 SSE chunk")


async def test_2_out_of_scope_and_forbidden_write(student_ctx: AuthContext) -> None:
    tool_cache.clear()
    slot_store.clear()
    db_calls = 0
    llm_calls = 0

    def fail_db():
        nonlocal db_calls
        db_calls += 1
        raise AssertionError("DB must NOT be queried for out_of_scope / forbidden_write")

    def fail_llm(*args, **kwargs):
        nonlocal llm_calls
        llm_calls += 1
        raise AssertionError("LLM must NOT be called for lexical out_of_scope / forbidden_write")

    buf = io.StringIO()
    with patch.object(database, "get_connection", side_effect=fail_db), patch(
        "erp_ai.chat.orchestrator.get_llm", side_effect=fail_llm
    ), patch("erp_ai.chat.classifier.get_llm", side_effect=fail_llm), redirect_stdout(buf):
        oos_resp = await run_chat_turn("write binary search in C++", student_ctx)
        fw_resp = await run_chat_turn("pay my backlog fees", student_ctx)

    assert db_calls == 0 and llm_calls == 0
    assert oos_resp.intents == ["out_of_scope"], f"Got {oos_resp.intents}"
    assert fw_resp.intents == ["forbidden_write"], f"Got {fw_resp.intents}"
    assert "only help with" in oos_resp.message.lower()
    assert "can't pay fees" in fw_resp.message.lower()
    print("[PASS] Case 2 — Out-of-Scope & Forbidden Write: 0 DB, 0 LLM, clean refusals")


async def test_3_collision_checks() -> None:
    slot_store.clear()
    exam_res = await classify_intents("exam schedule", "student", allow_llm_fallback=False)
    assert exam_res.all_classified_intents == [IntentCategory.EXAMINATION], (
        f"Collision failure on 'exam schedule': {exam_res.all_classified_intents}"
    )

    nav_res = await classify_intents("where is teaching plan", "teacher", allow_llm_fallback=False)
    assert nav_res.all_classified_intents == [IntentCategory.FACULTY_NAVIGATION], (
        f"Collision failure on 'where is teaching plan': {nav_res.all_classified_intents}"
    )

    # Also verify greeting stripping when paired with ERP module
    greet_combo = await classify_intents(
        "hey there, what is my attendance?", "student", allow_llm_fallback=False
    )
    assert greet_combo.all_classified_intents == [IntentCategory.ACADEMIC], (
        f"Expected greeting stripped to ['academic'], got {greet_combo.all_classified_intents}"
    )
    print("[PASS] Case 3 — Collision Immunity ('exam schedule' -> examination, 'where is teaching plan' -> faculty_navigation)")


async def test_4_multi_module_parallel_student(student_ctx: AuthContext) -> None:
    tool_cache.clear()
    slot_store.clear()
    query = "check my DSA attendance, exam timetable, and pending assignments"

    cls = await classify_intents(query, "student", allow_llm_fallback=False)
    assert cls.intent_values == ["lms", "academic", "examination"], f"Got {cls.intent_values}"

    resolved_names = get_tool_names_for_intents("student", cls.all_classified_intents)
    for expected in ("get_attendance", "get_exam_timetable", "list_assignments"):
        assert expected in resolved_names
    assert "get_student_profile_info" not in resolved_names

    hop1 = [
        AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "name": "get_attendance",
                    "args": {"subject_hint": "DSA"},
                    "id": "call_att",
                    "type": "tool_call",
                },
                {
                    "name": "get_exam_timetable",
                    "args": {},
                    "id": "call_tt",
                    "type": "tool_call",
                },
                {
                    "name": "list_assignments",
                    "args": {"status_filter": "pending"},
                    "id": "call_asg",
                    "type": "tool_call",
                },
            ],
        )
    ]
    hop2 = [
        AIMessageChunk(content="Here is your **DSA attendance**, "),
        AIMessageChunk(content="**exam timetable**, and "),
        AIMessageChunk(content="**pending assignments** summary."),
    ]
    scripted = _ScriptedBoundLLM([hop1, hop2])
    buf = io.StringIO()
    with patch("erp_ai.chat.orchestrator.get_llm", return_value=scripted), redirect_stdout(buf):
        chunks = [c async for c in stream_chat_turn(query, student_ctx)]

    frames = _parse_sse_frames(chunks)
    log_text = buf.getvalue()
    assert "[parallel x3]" in log_text, "Expected all 3 tools to execute in a parallel x3 batch!"
    # Verify real DB JSON was fed back into hop 2 ToolMessages
    hop2_msgs = scripted.seen_messages[1]
    tool_msgs = [m for m in hop2_msgs if getattr(m, "type", "") == "tool"]
    assert len(tool_msgs) == 3
    att_json = json.loads(tool_msgs[0].content)
    tt_json = json.loads(tool_msgs[1].content)
    asg_json = json.loads(tool_msgs[2].content)
    assert "subjects" in att_json and att_json.get("filter_matched") is True
    assert "rows" in tt_json
    assert "assignments" in asg_json and "overall_counts" in asg_json

    token_texts = [p["text"] for ev, p in frames if ev == "token"]
    assert len(token_texts) == 3, f"Expected true token streaming chunks, got {token_texts}"
    print("[PASS] Case 4 — Student Multi-Module Query: ['lms','academic','examination'] + [parallel x3] real DB tools + true streaming")


async def test_5_paraphrase_query_and_llm_fallback(student_ctx: AuthContext) -> None:
    tool_cache.clear()
    slot_store.clear()

    # 5a. Exact spec paraphrase query
    q1 = "how many classes have I missed so far and when is homework due?"
    res1 = await classify_intents(q1, "student", allow_llm_fallback=False)
    assert set(res1.intent_values) == {"lms", "academic"}, f"Got {res1.intent_values}"

    # 5b. Pure 0-lexical-match paraphrase exercising the zero-ERP-JSON structured LLM classifier fallback
    q2 = "am i falling behind on classroom presence or take-home tasks?"
    db_calls_during_classify = 0
    orig_conn = database.get_connection

    def guard_no_db():
        nonlocal db_calls_during_classify
        db_calls_during_classify += 1
        return orig_conn()

    fallback_stub = _ScriptedBoundLLM(
        [StudentClassificationOutput(intents=["academic", "lms"])]
    )
    with patch.object(database, "get_connection", side_effect=guard_no_db), patch(
        "erp_ai.chat.classifier.get_llm", return_value=fallback_stub
    ):
        res2 = await classify_intents(q2, "student", allow_llm_fallback=True)

    assert db_calls_during_classify == 0, "LLM classifier fallback MUST perform 0 DB queries!"
    assert res2.source == "llm_fallback"
    assert res2.intent_values == ["lms", "academic"], f"Got {res2.intent_values}"
    print("[PASS] Case 5 — Paraphrase Query & Zero-DB Structured LLM Classifier Fallback -> ['lms', 'academic']")


async def test_6_guided_upload(student_ctx: AuthContext) -> None:
    tool_cache.clear()
    slot_store.clear()

    # Find an enrolled theory course with 1 assignment in DB for this student
    from erp_ai.tools.lms_tools.assignment_tools import list_current_assignments

    enrolled_asgs = list_current_assignments(student_ctx.student_id, student_ctx.semester)
    assert enrolled_asgs, "Expected enrolled assignments in seeded DB"
    target = enrolled_asgs[0]
    target_code = target["course_code"]
    query = f"upload assignment 1 for {target_code}"

    hop1 = [
        AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "name": "prepare_assignment_upload",
                    "args": {"course_hint": target_code, "assignment_number": 1},
                    "id": "call_up",
                    "type": "tool_call",
                }
            ],
        )
    ]
    hop2 = [
        AIMessageChunk(
            content=f"Choose your PDF or DOCX file (max 3 MB) for **{target_code}** below and click **Confirm upload**."
        )
    ]
    scripted = _ScriptedBoundLLM([hop1, hop2])
    buf = io.StringIO()
    with patch("erp_ai.chat.orchestrator.get_llm", return_value=scripted), redirect_stdout(buf):
        chunks = [c async for c in stream_chat_turn(query, student_ctx)]

    frames = _parse_sse_frames(chunks)
    action_frames = [p for ev, p in frames if ev == "action"]
    assert len(action_frames) == 1, f"Expected SSE action event, got {frames}"
    action = action_frames[0]
    assert action["type"] == "upload_confirmation"
    assert action["assignment"]["id"] == str(target["id"])
    assert action["assignment"]["course_code"] == target_code
    print(f"[PASS] Case 6 — Guided Upload ('{query}'): SSE emits action=upload_confirmation for {target_code}")


async def test_7_faculty_multi_intent_no_payslip_leak(teacher_ctx: AuthContext) -> None:
    tool_cache.clear()
    slot_store.clear()
    query = "show my pending checks and leave balance"

    cls = await classify_intents(query, "teacher", allow_llm_fallback=False)
    assert cls.intent_values == ["faculty_lms_read", "faculty_hr_payroll"], (
        f"Got {cls.intent_values}"
    )

    hop1 = [
        AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "name": "get_teacher_assignments",
                    "args": {"unchecked_only": True},
                    "id": "call_t_asg",
                    "type": "tool_call",
                },
                {
                    "name": "get_teacher_leave_balance",
                    "args": {},
                    "id": "call_t_leave",
                    "type": "tool_call",
                },
            ],
        )
    ]
    hop2 = [
        AIMessageChunk(
            content="Here are your **unchecked submissions** and **leave card balances**."
        )
    ]
    scripted = _ScriptedBoundLLM([hop1, hop2])
    payslip_called = False

    def spy_payslip(*args, **kwargs):
        nonlocal payslip_called
        payslip_called = True
        raise AssertionError("teacher_payslip MUST NOT be called when not requested!")

    buf = io.StringIO()
    with patch("erp_ai.chat.orchestrator.get_llm", return_value=scripted), patch(
        "erp_ai.chat.tool_registry.teacher_payslip", side_effect=spy_payslip
    ), redirect_stdout(buf):
        chunks = [c async for c in stream_chat_turn(query, teacher_ctx)]

    assert not payslip_called
    log_text = buf.getvalue()
    assert "[parallel x2]" in log_text
    hop2_prompt_dump = json.dumps([str(m.content) for m in scripted.seen_messages[1]])
    assert "total_deduction" not in hop2_prompt_dump and "gross" not in hop2_prompt_dump
    frames = _parse_sse_frames(chunks)
    assert frames[-1][0] == "done"
    print("[PASS] Case 7 — Faculty Multi-Intent: ['faculty_lms_read','faculty_hr_payroll'] parallel x2, zero payslip data in prompt")


async def test_8_gemini_503_outage_simulation(student_ctx: AuthContext) -> None:
    tool_cache.clear()
    slot_store.clear()
    gemini_agent.clear_llm_cache()

    class ServiceUnavailable503Error(RuntimeError):
        status_code = 503

    outage_exc = ServiceUnavailable503Error(
        "503 UNAVAILABLE: The model is overloaded. Please try again later."
    )
    failing_llm = _ScriptedBoundLLM([outage_exc])
    assignment_queries = 0

    def spy_assignments(*args, **kwargs):
        nonlocal assignment_queries
        assignment_queries += 1
        return []

    buf = io.StringIO()
    with patch.dict(
        os.environ,
        {
            "GEMINI_MODEL": "gemini-3.5-flash",
            "GEMINI_FALLBACK_MODEL": "gemini-2.0-flash",  # Retired model must be ignored!
        },
        clear=False,
    ), patch("erp_ai.chat.orchestrator.get_llm", return_value=failing_llm), patch(
        "erp_ai.chat.tool_registry.list_current_assignments", side_effect=spy_assignments
    ), redirect_stdout(buf):
        models = gemini_agent.model_attempt_order()
        assert models == ["gemini-3.5-flash"], f"Retired 2.0-flash was not blocked: {models}"
        chunks = [c async for c in stream_chat_turn("list my pending assignments", student_ctx)]

    frames = _parse_sse_frames(chunks)
    log_text = buf.getvalue()
    assert assignment_queries == 0, f"Expected 0 speculative assignment queries, got {assignment_queries}"
    assert "gemini-2.0-flash" not in [
        line for line in log_text.splitlines() if "[model error]" in line
    ]
    assert "[model error] model=gemini-3.5-flash attempt=1 status=503" in log_text
    token_texts = [p["text"] for ev, p in frames if ev == "token"]
    assert token_texts == [AI_BUSY_MESSAGE], f"Expected clean 1-line retry, got {token_texts}"
    print("[PASS] Case 8 — Gemini 503 Outage: logged status=503, ignored retired gemini-2.0-flash, 0 assignment queries, clean 1-line retry")


async def test_frontend_stage4_contract() -> None:
    js_path = Path(__file__).resolve().parent.parent / "frontend" / "shared" / "erp-assistant.js"
    js_text = js_path.read_text(encoding="utf-8")
    assert "DOMPurify" in js_text and "DOMPurify.sanitize" in js_text
    assert "requestAnimationFrame" in js_text and "rafScheduled" in js_text
    assert "flushStreamMarkdown" in js_text
    assert "upload_confirmation" in js_text
    print("[PASS] Stage 4 Frontend Contract — DOMPurify + requestAnimationFrame batching + SSE flush verified")


async def main() -> None:
    student_ctx, teacher_ctx = _load_real_auth_contexts()
    await test_1_greeting_zero_db_zero_llm(student_ctx)
    await test_2_out_of_scope_and_forbidden_write(student_ctx)
    await test_3_collision_checks()
    await test_4_multi_module_parallel_student(student_ctx)
    await test_5_paraphrase_query_and_llm_fallback(student_ctx)
    await test_6_guided_upload(student_ctx)
    await test_7_faculty_multi_intent_no_payslip_leak(teacher_ctx)
    await test_8_gemini_503_outage_simulation(student_ctx)
    await test_frontend_stage4_contract()
    database.close_pool()
    print("\nALL 8 ACCEPTANCE MATRIX SCENARIOS + STAGE 4 FRONTEND CHECKS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
