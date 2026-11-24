"""Stage 3 async tool-calling orchestrator.

Flow for one turn:
1. Classify intents (Stage 1, already parallel and DB-free).
2. Resolve the scoped tool list (Stage 2). Greeting, out-of-scope, and
   forbidden-write turns return a static reply with no synthesis call.
3. Otherwise bind only those tools, let the model choose calls (max 2 hops),
   run the calls concurrently, and stream the final answer token by token.
4. Stop when the client disconnects. Model failures are logged per attempt
   and surface as one short retry sentence — never a speculative ERP dump.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from chat_activity_log import (
    chat_turn,
    get_turn_log,
    reset_parallel_batch,
    set_parallel_batch,
)
from erp_ai.chat.cache import (
    RateLimitExceededError,
    acquire_user_turn_slot,
    get_session_slots,
    update_session_slots,
)
from erp_ai.chat.classifier import classify_intents
from erp_ai.chat.prompts import (
    AI_BUSY_MESSAGE,
    EMPTY_SYNTHESIS_MESSAGE,
    forbidden_write_reply,
    greeting_reply,
    out_of_scope_reply,
    role_title,
    system_prompt_for_role,
)
from erp_ai.chat.schemas import (
    ACTIONABLE_ERP_INTENTS,
    AuthContext,
    ChatResponse,
    IntentCategory,
    TurnSideEffects,
    format_sse,
)
from erp_ai.chat.tool_registry import get_tools_for_intents
from gemini_agent import get_llm, model_attempt_order

MAX_TOOL_HOPS = 2

_TurnEvent = tuple[str, dict[str, Any]]


class ClientDisconnected(Exception):
    """The SSE client closed the connection (Stop, navigation, or network drop)."""


def _fast_exit_text(role: str, intents: list[IntentCategory]) -> str | None:
    """Static reply when no ERP tool category is present. None means 'call the model'."""
    if any(intent in ACTIONABLE_ERP_INTENTS for intent in intents):
        return None
    if IntentCategory.FORBIDDEN_WRITE in intents:
        return forbidden_write_reply(role)
    if IntentCategory.OUT_OF_SCOPE in intents or not intents:
        return out_of_scope_reply(role)
    if IntentCategory.GREETING_HELP in intents:
        return greeting_reply(role)
    return out_of_scope_reply(role)


def _user_message_for_failure(exc: BaseException | None) -> str:
    text = f"{type(exc).__name__}: {exc}" if exc else ""
    lowered = text.casefold()
    if "gemini_api_key" in lowered or "llm_api_key" in lowered or "api key" in lowered:
        return (
            "The AI assistant is not configured yet. "
            "Add your LLM API key in .env and retry."
        )
    return AI_BUSY_MESSAGE


def _split_content(content: Any) -> tuple[str, str]:
    """Split a model chunk into (visible answer text, thought text)."""
    if content is None:
        return "", ""
    if isinstance(content, str):
        return content, ""
    if isinstance(content, list):
        answer: list[str] = []
        thoughts: list[str] = []
        for item in content:
            if isinstance(item, str):
                answer.append(item)
                continue
            item_type = ""
            text = ""
            if isinstance(item, dict):
                item_type = str(item.get("type") or item.get("kind") or "").casefold()
                text = str(
                    item.get("text")
                    or item.get("thought")
                    or item.get("thinking")
                    or ""
                )
            else:
                item_type = str(getattr(item, "type", "") or "").casefold()
                text = str(
                    getattr(item, "text", None)
                    or getattr(item, "thought", None)
                    or getattr(item, "thinking", None)
                    or ""
                )
            if item_type in {"thinking", "thought", "thoughts", "reasoning"}:
                if text:
                    thoughts.append(text)
            elif text:
                answer.append(text)
        return "".join(answer), "".join(thoughts)
    return str(content), ""


def _chunk_signals_tool(chunk: Any) -> bool:
    if getattr(chunk, "tool_call_chunks", None):
        return True
    if getattr(chunk, "tool_calls", None):
        return True
    additional = getattr(chunk, "additional_kwargs", None) or {}
    return bool(additional.get("tool_calls") or additional.get("function_call"))


def _merge_chunks(previous: Any, chunk: Any) -> Any:
    if previous is None:
        return chunk
    try:
        return previous + chunk
    except Exception:
        return chunk


def _normalize_tool_call(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and isinstance(raw.get("function"), dict):
        function = raw["function"]
        raw = {
            "name": function.get("name"),
            "args": function.get("arguments") or function.get("args"),
            "id": raw.get("id"),
        }
    if isinstance(raw, dict):
        name = str(raw.get("name") or "").strip()
        args = raw.get("args") if "args" in raw else raw.get("arguments")
        call_id = str(raw.get("id") or "").strip()
    else:
        name = str(getattr(raw, "name", "") or "").strip()
        args = getattr(raw, "args", None)
        if args is None:
            args = getattr(raw, "arguments", None)
        call_id = str(getattr(raw, "id", "") or "").strip()
    if isinstance(args, str):
        try:
            parsed = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        args = parsed
    if not isinstance(args, dict):
        args = {}
    if not name:
        return None
    if not call_id:
        call_id = f"call_{uuid.uuid4().hex[:12]}"
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _extract_tool_calls(message: Any) -> list[dict[str, Any]]:
    if message is None:
        return []
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        normalized = _normalize_tool_call(raw)
        if normalized is None:
            return
        key = normalized["id"] or f"{normalized['name']}:{len(found)}"
        if key in seen:
            return
        seen.add(key)
        found.append(normalized)

    for raw in list(getattr(message, "tool_calls", None) or []):
        _add(raw)
    if found:
        return found
    for raw in list(getattr(message, "invalid_tool_calls", None) or []):
        _add(raw)
    if found:
        return found
    additional = getattr(message, "additional_kwargs", None) or {}
    for raw in additional.get("tool_calls") or []:
        _add(raw)
    function_call = additional.get("function_call")
    if function_call:
        _add(function_call)
    return found


def _ai_message(assembled: Any, tool_calls: list[dict[str, Any]]) -> AIMessage:
    content, _thought = _split_content(getattr(assembled, "content", "") if assembled else "")
    return AIMessage(content=content or "", tool_calls=tool_calls)


def _tool_result_text(value: Any) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except TypeError:
        return json.dumps({"ok": False, "error": "Tool result was not JSON serializable"})


async def _client_gone(request: Any) -> bool:
    if request is None:
        return False
    checker = getattr(request, "is_disconnected", None)
    if checker is None:
        return False
    try:
        return bool(await checker())
    except Exception:
        return False


async def _invoke_tool(tool: Any, args: dict[str, Any]) -> Any:
    def _run() -> Any:
        return tool.invoke(args)

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _run_tools_parallel(
    tools_by_name: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> list[Any]:
    batch = len(tool_calls)
    token = set_parallel_batch(batch if batch > 1 else None)

    async def _one(call: dict[str, Any]) -> Any:
        tool = tools_by_name.get(call["name"])
        if tool is None:
            return {
                "ok": False,
                "error": f"Tool {call['name']} is not available for this turn.",
            }
        return await _invoke_tool(tool, call["args"])

    try:
        log = get_turn_log()
        if log and batch:
            log.thinking(
                f"Executing {batch} tool call(s)"
                + (" in parallel." if batch > 1 else ".")
            )
        return list(await asyncio.gather(*(_one(call) for call in tool_calls)))
    finally:
        reset_parallel_batch(token)


async def _stream_model(
    messages: list[Any],
    tools: list[Any],
    request: Any,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream one model hop. Yields ('token', text) then ('outcome', dict) or ('failed', exc)."""
    log = get_turn_log()
    models = model_attempt_order()
    last_exc: BaseException | None = None
    allow_tools = bool(tools)

    for attempt, model_name in enumerate(models, start=1):
        if log:
            log.thinking(
                f"Calling Gemini (model={model_name}, attempt={attempt}, "
                f"bound_tools={len(tools)})."
            )
        yielded = False
        try:
            llm = get_llm(
                model_name,
                temperature=0.1,
                max_output_tokens=900,
                timeout=30,
                max_retries=1,
            )
            bound = llm.bind_tools(tools) if allow_tools else llm
            assembled: Any = None
            saw_tool = False
            async for chunk in bound.astream(messages):
                if await _client_gone(request):
                    raise ClientDisconnected()
                assembled = _merge_chunks(assembled, chunk)
                if _chunk_signals_tool(chunk):
                    saw_tool = True
                answer, thought = _split_content(getattr(chunk, "content", None))
                if thought and log:
                    log.model_thought(thought)
                if saw_tool or not answer:
                    continue
                yielded = True
                yield ("token", answer)
            calls = _extract_tool_calls(assembled)
            if calls:
                saw_tool = True
            yield (
                "outcome",
                {
                    "assembled": assembled,
                    "tool_calls": calls if saw_tool else [],
                    "model": model_name,
                },
            )
            return
        except ClientDisconnected:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            if log:
                log.model_error(model_name, exc, attempt=attempt)
            if yielded:
                yield ("failed", exc)
                return
            continue

    if log:
        log.thinking(
            "Gemini is unavailable for this turn. Returning the short retry message."
        )
    yield ("failed", last_exc if last_exc is not None else RuntimeError("No Gemini model is configured"))


async def _drive_turn(
    query: str,
    auth_ctx: AuthContext,
    request: Any = None,
) -> AsyncIterator[_TurnEvent]:
    """Yield internal events: meta, token, action. Caller appends the SSE done frame."""
    log = get_turn_log()
    collected: list[str] = []
    meta_title = role_title(auth_ctx.role)
    meta_kind = "help"
    source = "orchestrator"
    quiet_exit = False

    try:
        prior_slots = get_session_slots(auth_ctx.cache_user_key)
        classification = await classify_intents(
            query,
            auth_ctx.role,
            session_slots=prior_slots,
        )
        update_session_slots(
            auth_ctx.cache_user_key,
            intents=classification.all_classified_intents,
        )

        side_effects = TurnSideEffects()
        tools = get_tools_for_intents(
            auth_ctx.role,
            classification.all_classified_intents,
            auth_ctx,
            side_effects,
        )
        fast_text = _fast_exit_text(auth_ctx.role, classification.all_classified_intents)
        streaming = fast_text is None

        def _meta() -> dict[str, Any]:
            return {
                "kind": meta_kind,
                "title": meta_title,
                "intents": classification.intent_values,
                "streaming": streaming,
            }

        yield ("meta", _meta())

        if fast_text is not None:
            if log:
                log.thinking(
                    "Fast exit — static reply, no synthesis LLM call and no ERP queries."
                )
            collected.append(fast_text)
            yield ("token", {"text": fast_text})
            source = "fast_exit"
        elif not tools:
            if log:
                log.thinking(
                    "No tools resolved for the classified intents. "
                    "Returning a static reply without a synthesis call."
                )
            text = out_of_scope_reply(auth_ctx.role)
            collected.append(text)
            yield ("token", {"text": text})
            source = "fast_exit"
        else:
            messages: list[Any] = [SystemMessage(content=system_prompt_for_role(auth_ctx.role))]
            slot_summary = prior_slots.to_prompt_summary()
            if slot_summary:
                messages.append(SystemMessage(content=slot_summary))
            messages.append(HumanMessage(content=query))
            tools_by_name = {tool.name: tool for tool in tools}
            tool_rounds = 0
            source = "gemini"

            while tool_rounds <= MAX_TOOL_HOPS:
                allow_tools = tool_rounds < MAX_TOOL_HOPS
                outcome: dict[str, Any] | None = None
                failed: BaseException | None = None
                hop_sent = False
                async for kind, payload in _stream_model(
                    messages,
                    tools if allow_tools else [],
                    request,
                ):
                    if kind == "token":
                        hop_sent = True
                        collected.append(payload)
                        yield ("token", {"text": payload})
                    elif kind == "outcome":
                        outcome = payload
                    elif kind == "failed":
                        failed = payload if isinstance(payload, BaseException) else RuntimeError(str(payload))
                if failed is not None and not hop_sent:
                    text = _user_message_for_failure(failed)
                    collected.append(text)
                    yield ("token", {"text": text})
                    source = "gemini_unavailable"
                    break
                if failed is not None:
                    source = "gemini_partial"
                    break
                calls = list((outcome or {}).get("tool_calls") or [])
                if calls and allow_tools:
                    messages.append(
                        _ai_message((outcome or {}).get("assembled"), calls)
                    )
                    results = await _run_tools_parallel(tools_by_name, calls)
                    for call, result in zip(calls, results):
                        messages.append(
                            ToolMessage(
                                content=_tool_result_text(result),
                                tool_call_id=call["id"],
                                name=call["name"],
                            )
                        )
                    tool_rounds += 1
                    if side_effects.title_override:
                        meta_title = side_effects.title_override
                    if side_effects.kind_override:
                        meta_kind = side_effects.kind_override
                    if side_effects.title_override or side_effects.kind_override:
                        yield ("meta", _meta())
                    continue
                if not hop_sent:
                    collected.append(EMPTY_SYNTHESIS_MESSAGE)
                    yield ("token", {"text": EMPTY_SYNTHESIS_MESSAGE})
                break

        if side_effects.action:
            yield ("action", side_effects.action)

        if log:
            log.final_response(
                "".join(collected).strip(),
                kind=meta_kind,
                title=meta_title,
                source=source,
            )
    except (asyncio.CancelledError, ClientDisconnected):
        quiet_exit = True
        raise
    finally:
        if quiet_exit and log is not None and not log._printed:
            partial = "".join(collected).strip()
            log.final_response(
                partial or "Generation stopped.",
                kind="stopped",
                title=meta_title,
                source="client_disconnect",
            )


async def _turn_events(
    query: str,
    auth_ctx: AuthContext,
    request: Any = None,
) -> AsyncIterator[_TurnEvent]:
    async with acquire_user_turn_slot(auth_ctx.cache_user_key):
        async with chat_turn(
            query=query,
            role=auth_ctx.role,
            user_label=auth_ctx.user_label,
            student_id=auth_ctx.student_id,
            teacher_id=auth_ctx.teacher_id,
            semester=auth_ctx.semester,
        ):
            try:
                async for event in _drive_turn(query, auth_ctx, request):
                    yield event
            except ClientDisconnected:
                return


async def run_chat_turn(
    query: str,
    auth_ctx: AuthContext,
    request: Any = None,
) -> ChatResponse:
    """Run one turn and return the JSON body used by `POST /api/assistant/chat`."""
    parts: list[str] = []
    meta: dict[str, Any] = {
        "kind": "help",
        "title": role_title(auth_ctx.role),
        "intents": [],
    }
    action: dict[str, Any] | None = None
    async for kind, payload in _turn_events(query, auth_ctx, request):
        if kind == "meta":
            meta = payload
        elif kind == "token":
            parts.append(str(payload.get("text") or ""))
        elif kind == "action":
            action = payload
        elif kind == "error":
            parts.append(str(payload.get("detail") or ""))
    return ChatResponse(
        message="".join(parts).strip(),
        kind=str(meta.get("kind") or "help"),
        title=str(meta.get("title") or role_title(auth_ctx.role)),
        intents=list(meta.get("intents") or []),
        action=action,
    )


async def stream_chat_turn(
    query: str,
    auth_ctx: AuthContext,
    request: Any = None,
) -> AsyncIterator[str]:
    """SSE frames for `POST /api/assistant/chat/stream`."""
    sent_token = False
    try:
        async for kind, payload in _turn_events(query, auth_ctx, request):
            if kind == "token":
                sent_token = True
            elif kind == "error" and payload.get("detail"):
                sent_token = True
            yield format_sse(kind, payload)
        yield format_sse("done", {})
    except RateLimitExceededError as exc:
        yield format_sse("error", {"detail": str(exc)})
        yield format_sse("done", {})
    except ClientDisconnected:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        if not sent_token:
            yield format_sse("token", {"text": AI_BUSY_MESSAGE})
        else:
            yield format_sse(
                "token",
                {"text": "\n\n(The live model interrupted. You can retry this question.)"},
            )
        yield format_sse("done", {})
