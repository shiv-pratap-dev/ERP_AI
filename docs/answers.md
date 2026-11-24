# Intent classification and when the chatbot starts

Short answers for the current code. No module uses an LLM, an embedding model, or a learned decoder to pick an intent.

The browser `TextDecoder` in `erp-assistant.js` only turns SSE bytes into text. It does not classify anything.

---

## Shared mechanism

**What classifies intent:** plain Python, in the request that handles the chat POST.

1. Lowercase the message (`casefold`). Student text also runs a few typo regexes in `_normalize_chat_text` (`attendence` → `attendance`, and similar).
2. Test fixed phrases with `_has_phrase`: `re.search(r"\b" + phrase + r"\b", text)`. That is a word-boundary regex, not a model.
3. First matching branch wins and returns a finished answer dict. If every branch returns nothing, the host calls Gemini **to write the reply**, not to choose the module.

There is no chatbot object per question. The UI shell is created when the page loads. The Gemini client is a process-wide singleton created only on the first question that misses the phrase lists.

---

## When things are created

| Thing | When |
|---|---|
| Chat widget (`erp-assistant.js` `boot` → `init` → `mountShell`) | Page load (`DOMContentLoaded`), before any question |
| `POST /api/assistant/chat/stream` | The moment the user sends or taps a chip |
| Phrase routing | Immediately after auth, **before** any model call |
| `ChatGoogleGenerativeAI` (`get_llm` / `_LLM` in `gemini_agent.py`) | Only if routing returns no match, and only the first time in that process. Later misses reuse it |
| Fast-path replies | Never create or call the model. The full markdown string is built, then sliced into 24-character SSE chunks |

So: the widget exists before the question. Classification runs as soon as the request arrives. The model is not created for that question unless classification misses, and even then the client object is reused.

---

## Module A — LMS (student)

**Where:** bottom of `_resolve_rule_based` in `backend/assistant.py`, after creative, examination, and academic have all returned nothing.

**Classifier:** the same word-boundary phrase tests. No LLM.

| Branch | What must match |
|---|---|
| Overdue | `overdue`, `past deadline`, `expired`, … |
| Pending | `pending assignments`, `not submitted`, or `pending` plus `assignment` / `status` |
| Submitted | `submitted assignments`, `my submissions`, or `submitted` plus `assignment` |
| Upload | `upload` / `submit` / `send`, and not the submitted branch |
| Deadline | `deadline`, `due date`, `next`, `due`, … **and** (`assignment` or a deadline phrase) |
| Named assignment | the word `assignment`, and not list/count/status |
| List / count | `list`/`show`/… or `how many`/`count` plus `assignment`, or `semester` plus those words |
| Courses | `course`, `subject`, `teacher`, `faculty`, `enrolled`, … |

Named assignment and upload then narrow rows with `find_assignment_matches`: more regex plus a fixed alias map (`dbms`, `dsa`, `os`, `cn`, …) and an assignment-number regex. Still not a model.

**Order inside LMS:** overdue → pending → submitted → upload → deadline → named item → list/count → courses.

**When the model starts:** it does not, if any branch hits. Gemini runs only if this function returns `None`.

**Extra cost:** `list_current_assignments` runs **before** these phrase tests. The database is hit even when the question is not about assignments and will fall through to Gemini.

---

## Module B — Academic (student)

**Where:** `_resolve_academic`, called only if creative and examination did not match.

**Classifier:** `_normalize_chat_text` plus `_has_any_phrase`. No LLM.

Checked in this order:

1. Current semester — phrases like `which semester`, `my semester`, or (`semester`/`sem` plus `currently`/`which`/`what`/…) as long as the text is not about assignments, results, fees, or courses.
2. Profile — `my profile`, `who am i`, `my rrn`, …
3. Attendance — the word `attendance` (typos already rewritten), **unless** the text also contains `plan`, `suggest`, `schedule`, and the other creative words. On the live path that guard does nothing: those words already returned a focus plan before this function is called.
4. Results — `my results`, `sgpa`, or `result` plus `show`/`my`/…
5. Fees — `my fees`, `outstanding fees`, or `fee(s)` plus `show`/`my`/…, and not `backlog` / `exam fee`.

Attendance then calls `filter_attendance_subjects`, which is another alias/regex filter on the same query string (subject code, name, `dsa`, …).

**When the model starts:** never on a hit. A miss falls through to LMS, not straight to Gemini.

---

## Module C — Examination (student)

**Where:** `_resolve_examination`, after the creative check and before academic. It lowercases the raw message and does **not** run the typo normalizer.

**Classifier:** `_has_any_phrase` only. No LLM.

Order:

1. Admit card — `admit card`, `hall ticket`, `my admit`, …
2. Timetable — `exam timetable`, `exam schedule`, `exam dates`, … or (`timetable` and `exam`/`my`/`show`/…)
3. Backlog write — `backlog` plus `register` / `pay` / … → fixed refusal string, no tool
4. Backlog read — `my backlog`, `arrear`, or `backlog` plus `show`/`list`/…

**When the model starts:** never on a hit.

**Collision:** `exam schedule` also contains `schedule`, and the creative check runs first, so this timetable branch is not reached for that wording. See `CHATBOT_ARCHITECTURES.md` §11.

---

## Module D — Focus / creative plans (student)

**Where:** first lines of `_resolve_rule_based`, before examination, academic, and LMS.

**Classifier:** `_is_creative_ask`. One phrase list: `plan`, `routine`, `schedule`, `suggest`, `prioritize`, `what should i`, `workload`, and others. Word-boundary regex. No LLM.

A hit calls `build_student_focus_plan` and returns. Lower modules do not run.

**When the model starts:** never on a hit. The plan text is built in Python from attendance and assignment rows.

---

## Module E — Gemini path (student)

**This path does not classify intent.** It runs only when A–D all return `None` (`chat` → `invoke_erp_agent`, or `chat_stream` → `stream_erp_agent`).

What happens then, in order:

1. `_student_context` loads live data. Profile, attendance, courses, and assignments are **always** loaded. Results, fees, timetable, admit card, and backlog are added only if `_wants` sees a substring in the question (`"fee" in text`, `"admit" in text`, …). That check is not the module router and is looser than the word-boundary regex (for example `fee` matches inside `feedback`).
2. The question is rewritten by `_rewrite_question` into a “short bullet plan or summary, do not refuse” instruction. Every miss gets this rewrite, not only planning questions.
3. `get_llm()` returns the singleton `ChatGoogleGenerativeAI`, constructing it on first use (`GEMINI_MODEL`, else `gemini-3.5-flash`).
4. `llm.stream` generates the answer. One call. No tool-calling loop. The model is not asked which module this is.

If the API errors or the stream is empty, `_gemini_fallback_answer` returns static help plus a short assignment list. Still no classifier.

---

## Module F — Faculty

**Where:** `resolve_faculty_message` in `backend/faculty_assistant.py`. Same idea: `casefold`, then `_has_phrase` / `_has_any`. A separate copy of the phrase helpers, not the student ones. No LLM on a hit.

Order:

1. Create assignment — a create-word (`create`, `add`, `make`, `new`, …) **and** an assignment-word. Dates and title are pulled with regex (`_parse_dates`, `_extract_title`). Course choice is alias plus score in `_match_course`, not a model.
2. Creative plan — faculty `_is_creative_ask` (`plan`, `schedule`, `workload`, …). Skipped if step 1 matched.
3. Greeting / help — exact `hi` / `hello` / `hey` / `help`, or `what can you do`.
4. Navigation — `where` / `how do i` / `how to` / `open`, then `_guide_for` (`key in text` against the `GUIDE` dict).
5. Payslip — `payslip`, `salary`, `net pay`, …
6. Holidays — `holiday` / `holidays`
7. Leave — `leave balance`, `leave card`, or `leave` plus `balance`/`show`/…
8. Pending checks — `pending checks`, `unchecked`, or `pending` plus `assignment`/`submission`/…
9. My assignments — `my assignments`, `list assignments`, …
10. My courses — `my courses`, `what do i teach`, or (`course` and `my`/`teach`/`show`/…)

**When the model starts:** only if step 10 falls through (`return None`). `stream_faculty_agent` then loads courses, assignments, leave, payslip, and holidays with **no** keyword gate, rewrites the question the same way as the student path, and streams one Gemini reply. `get_llm()` is the same singleton, created here if no earlier miss in the process has created it yet.

Faculty fast-path text is also pre-built and then fake-streamed in 24-character chunks. The model stream is the only real token stream.
