/**
 * Global ERP AI assistant widget.
 * Mounts on every portal page and persists chat + open state in sessionStorage.
 */
(function () {
  const API =
    window.ERP_API_BASE ||
    window.API ||
    (window.location.port === "8000" ? "" : "http://127.0.0.1:8000");
  const LOGIN_URL =
    window.ERP_LOGIN_URL ||
    window.LOGIN_URL ||
    "http://127.0.0.1:8000/signin";
  const FACULTY = window.location.pathname.toLowerCase().includes("/faculty");
  // Separate history so a faculty session does not reuse a student chat.
  const STORAGE_KEY = FACULTY ? "erp_ai_chat_faculty_v1" : "erp_ai_chat_v2";

  const WELCOME_MD = FACULTY
    ? "Hi! I can look up **your courses**, **assignment checks**, **leave**, and **payslip**. Try a chip below or type a question."
    : "Hi! I can help with your **courses**, **assignments**, deadlines, and guided uploads. Try a quick chip below or type a question.";

  const CHIPS = FACULTY
    ? [
        ["my courses", "My courses"],
        ["pending checks", "Pending checks"],
        ["leave balance", "Leave balance"],
        ["my payslip", "Payslip"],
        ["where do I enter question wise marks", "Question-wise marks"],
      ]
    : [
        ["list my assignments", "List assignments"],
        ["pending assignments", "Pending"],
        ["show my courses", "My courses"],
        ["what is my next assignment deadline", "Next deadline"],
        ["upload assignment 1 for DBMS", "Upload DBMS A1"],
      ];
  const SUBTITLE = FACULTY
    ? "Courses · Checks · Leave · Payroll"
    : "Courses · Assignments · Uploads";
  const PLACEHOLDER = FACULTY
    ? "Ask about your courses, checks, or leave... (Shift+Enter for new line)"
    : "Ask about courses or assignments... (Shift+Enter for new line)";

  function loadState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return { open: false, messages: [] };
      const parsed = JSON.parse(raw);
      return {
        open: Boolean(parsed.open),
        messages: Array.isArray(parsed.messages) ? parsed.messages : [],
      };
    } catch (_) {
      return { open: false, messages: [] };
    }
  }

  function saveState(partial) {
    const current = loadState();
    const next = { ...current, ...partial };
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch (_) {
      /* ignore quota */
    }
    return next;
  }

  function ensureMarked(callback) {
    if (window.marked && typeof window.marked.parse === "function") {
      callback();
      return;
    }
    const existing = document.querySelector("script[data-erp-marked]");
    if (existing) {
      existing.addEventListener("load", callback, { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/npm/marked/marked.min.js";
    script.dataset.erpMarked = "1";
    script.onload = callback;
    document.head.appendChild(script);
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function compactMarkdownText(text) {
    return String(text || "")
      .replace(/\r\n/g, "\n")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function renderMarkdown(text) {
    const compact = compactMarkdownText(text);
    if (!compact) return "";
    if (window.marked && typeof marked.parse === "function") {
      return `<div class="chat-md">${marked.parse(compact)}</div>`;
    }
    return `<div class="chat-md">${escapeHtml(compact).replace(/\n/g, "<br>")}</div>`;
  }

  function formatChatDate(value) {
    try {
      return new Date(value).toLocaleString();
    } catch (_) {
      return value || "-";
    }
  }

  function mountShell() {
    if (document.getElementById("chatFab")) return;

    const chipsHtml = CHIPS.map(
      ([prompt, label]) =>
        `<button type="button" class="chat-chip" data-chat="${escapeHtml(prompt)}">${escapeHtml(label)}</button>`
    ).join("");
    const wrap = document.createElement("div");
    wrap.id = "erpAssistantRoot";
    wrap.innerHTML = `
      <button id="chatFab" class="chat-fab" type="button" title="ERP Assistant" aria-label="Open ERP Assistant">
        <span aria-hidden="true">💬</span>
        <span>ERP AI</span>
      </button>
      <section id="chatPanel" class="chat-panel" aria-label="ERP Assistant chat">
        <div class="chat-header">
          <div>
            <p class="font-semibold text-sm" style="margin:0;font-weight:600;font-size:14px;">ERP Assistant</p>
            <p class="text-xs text-blue-100" style="margin:0;font-size:12px;opacity:.9;">${SUBTITLE}</p>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <button id="chatClear" type="button" class="text-xs" style="font-size:11px;opacity:.9;" title="Clear chat">Clear</button>
            <button id="chatClose" type="button" class="text-xl leading-none" aria-label="Close chat" style="font-size:22px;line-height:1;">×</button>
          </div>
        </div>
        <div id="chatMessages" class="chat-messages"></div>
        <div class="chat-chips" id="chatChips">
          ${chipsHtml}
        </div>
        <form id="chatForm" class="chat-input-row">
          <div class="chat-composer">
            <textarea id="chatInput" rows="1" placeholder="${PLACEHOLDER}" autocomplete="off"></textarea>
            <button id="chatSend" type="submit" class="chat-action-btn" aria-label="Send message">
              <span class="chat-icon chat-icon-send" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M4 12h14"/>
                  <path d="M12 5l7 7-7 7"/>
                </svg>
              </span>
              <span class="chat-icon chat-icon-stop" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8">
                  <circle cx="12" cy="12" r="9"/>
                  <rect x="8.5" y="8.5" width="7" height="7" rx="1" fill="currentColor" stroke="none"/>
                </svg>
              </span>
            </button>
          </div>
        </form>
      </section>
    `;
    document.body.appendChild(wrap);
  }

  function persistMessagesFromDom() {
    const chatMessages = document.getElementById("chatMessages");
    if (!chatMessages) return;
    const messages = [];
    chatMessages.querySelectorAll(".chat-bubble").forEach((bubble) => {
      const role = bubble.classList.contains("user") ? "user" : "bot";
      const meta = bubble.querySelector(".chat-meta");
      // Prefer raw markdown source — innerText strips **bold** / newlines and
      // breaks formatting when chat is restored on another page.
      let text = bubble.dataset.mdSource || "";
      if (!text) {
        const md = bubble.querySelector(".chat-md, .js-md");
        if (md) text = md.innerText || md.textContent || "";
        else text = bubble.innerText || bubble.textContent || "";
      }
      messages.push({
        role,
        text: String(text).trim(),
        title: meta ? meta.textContent : role === "bot" ? "ERP Assistant" : "",
        format: role === "bot" ? "markdown" : "text",
      });
    });
    saveState({ messages });
  }

  function init() {
    mountShell();

    const chatFab = document.getElementById("chatFab");
    const chatPanel = document.getElementById("chatPanel");
    const chatMessages = document.getElementById("chatMessages");
    const chatForm = document.getElementById("chatForm");
    const chatInput = document.getElementById("chatInput");
    const chatSend = document.getElementById("chatSend");
    let chatBusy = false;
    let activeController = null;
    let stopRequested = false;

    if (window.marked) {
      marked.setOptions({ gfm: true, breaks: true });
    }

    function autosizeInput() {
      chatInput.style.height = "auto";
      chatInput.style.height = `${Math.min(chatInput.scrollHeight, 120)}px`;
    }

    function setSendMode() {
      chatSend.type = "submit";
      chatSend.classList.remove("chat-stop");
      chatSend.disabled = false;
      chatSend.title = "Send";
      chatSend.setAttribute("aria-label", "Send message");
    }

    function setStopMode() {
      chatSend.type = "button";
      chatSend.classList.add("chat-stop");
      chatSend.disabled = false;
      chatSend.title = "Stop";
      chatSend.setAttribute("aria-label", "Stop generating");
    }

    function stopGeneration() {
      if (!chatBusy) return;
      stopRequested = true;
      if (activeController) {
        try {
          activeController.abort();
        } catch (_) {}
      }
    }

    function appendChatBubble(role, html, skipPersist, mdSource) {
      const bubble = document.createElement("div");
      bubble.className = `chat-bubble ${role}`;
      bubble.innerHTML = html;
      if (mdSource != null) {
        bubble.dataset.mdSource = String(mdSource);
      }
      chatMessages.appendChild(bubble);
      chatMessages.scrollTop = chatMessages.scrollHeight;
      if (!skipPersist) persistMessagesFromDom();
      return bubble;
    }

    function restoreMessages(messages) {
      chatMessages.innerHTML = "";
      if (!messages.length) {
        appendChatBubble(
          "bot",
          `<div class="chat-meta">ERP Assistant</div>${renderMarkdown(WELCOME_MD)}`,
          true,
          WELCOME_MD
        );
        saveState({
          messages: [
            {
              role: "bot",
              text: WELCOME_MD,
              title: "ERP Assistant",
              format: "markdown",
            },
          ],
        });
        return;
      }
      messages.forEach((msg) => {
        if (msg.role === "user") {
          appendChatBubble("user", escapeHtml(msg.text), true, msg.text);
        } else {
          const source = msg.text || "";
          const bubble = appendChatBubble(
            "bot",
            `<div class="chat-meta">${escapeHtml(msg.title || "ERP Assistant")}</div>${renderMarkdown(source)}`,
            true,
            source
          );
          // If older v1 plain-text was saved, still keep source for next persist.
          bubble.dataset.mdSource = source;
        }
      });
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function statusBadge(item) {
      const raw = String(item.status || "pending").toLowerCase();
      const due = item.due_date ? new Date(item.due_date) : null;
      const dueValid = due && !Number.isNaN(due.getTime());
      const now = new Date();
      const oneDayMs = 24 * 60 * 60 * 1000;

      if (raw === "submitted") {
        return { label: "submitted", tone: "submitted" };
      }

      // Deadline already passed → red expired (includes overdue / late).
      if (
        raw === "overdue" ||
        raw === "late" ||
        raw === "expired" ||
        (dueValid && due <= now && raw !== "submitted")
      ) {
        return { label: "expired", tone: "expired" };
      }

      // Still open: yellow if <= 1 day left, else green pending.
      if (dueValid && due - now <= oneDayMs) {
        return { label: "pending", tone: "soon" };
      }
      return { label: "pending", tone: "ok" };
    }

    function renderAssignmentList(assignments) {
      if (!assignments || !assignments.length) return "";
      const items = assignments
        .map((item) => {
          const badge = statusBadge(item);
          return `
        <div class="chat-list-item">
          <div><strong>${escapeHtml(item.course_code || "")}</strong> · ${escapeHtml(item.title || "")}</div>
          <div class="chat-list-meta">Due: ${escapeHtml(formatChatDate(item.due_date))} · Status: <span class="status-badge status-${badge.tone}">${escapeHtml(badge.label)}</span></div>
        </div>`;
        })
        .join("");
      return `<div class="chat-list">${items}</div>`;
    }

    function renderCourseList(courses) {
      if (!courses || !courses.length) return "";
      const items = courses
        .map(
          (item) => `
        <div class="chat-list-item">
          <div><strong>${escapeHtml(item.course_code || "")}</strong> · ${escapeHtml(item.course_name || "")}</div>
          <div class="text-gray-500">Teacher: ${escapeHtml(item.teacher || "N/A")}</div>
        </div>`
        )
        .join("");
      return `<div class="chat-list">${items}</div>`;
    }

    function renderUploadAction(action, hostBubble) {
      if (!action || action.type !== "upload_confirmation" || !action.assignment) return;
      const assignment = action.assignment;
      const box = document.createElement("div");
      box.className = "chat-upload-box";
      box.innerHTML = `
        <div><strong>Upload ready</strong></div>
        <div class="text-xs text-gray-600 mt-1">${escapeHtml(assignment.course_code)} · ${escapeHtml(assignment.title)}</div>
        <input type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" />
        <button type="button">Confirm upload</button>
        <div class="chat-status"></div>
      `;
      const fileInput = box.querySelector('input[type="file"]');
      const uploadBtn = box.querySelector("button");
      const status = box.querySelector(".chat-status");

      uploadBtn.addEventListener("click", async () => {
        const file = fileInput.files && fileInput.files[0];
        if (!file) {
          status.textContent = "Choose a PDF or DOCX file first.";
          status.style.color = "#b91c1c";
          return;
        }
        const ext = file.name.toLowerCase().slice(file.name.lastIndexOf("."));
        if (![".pdf", ".docx"].includes(ext)) {
          status.textContent = "Only PDF and DOCX files are allowed.";
          status.style.color = "#b91c1c";
          return;
        }
        if (file.size > 3 * 1024 * 1024) {
          status.textContent = "File exceeds the 3 MB limit.";
          status.style.color = "#b91c1c";
          return;
        }
        uploadBtn.disabled = true;
        status.textContent = "Uploading...";
        status.style.color = "#64748b";
        try {
          const formData = new FormData();
          formData.append("file", file);
          const response = await fetch(
            `${API}/api/assignments/${encodeURIComponent(assignment.id)}/upload`,
            { method: "POST", body: formData, credentials: "include" }
          );
          if (response.status === 401) {
            window.location.href = LOGIN_URL;
            return;
          }
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || "Upload failed.");
          status.textContent = data.message || "Upload successful.";
          status.style.color = "#15803d";
          fileInput.value = "";
        } catch (error) {
          status.textContent = error.message || "Upload failed.";
          status.style.color = "#b91c1c";
        } finally {
          uploadBtn.disabled = false;
        }
      });

      hostBubble.appendChild(box);
    }

    function createStreamingBotBubble() {
      // Keep markup compact — whitespace between tags becomes blank lines with pre-wrap.
      const bubble = appendChatBubble(
        "bot",
        `<div class="chat-meta">ERP Assistant</div><div class="js-typing"><div class="typing-indicator" aria-label="Assistant is typing"><span></span><span></span><span></span></div></div><div class="js-md" style="display:none;"></div><div class="js-extra"></div>`,
        true
      );
      return {
        bubble,
        metaEl: bubble.querySelector(".chat-meta"),
        typingEl: bubble.querySelector(".js-typing"),
        mdEl: bubble.querySelector(".js-md"),
        extraEl: bubble.querySelector(".js-extra"),
        rawText: "",
        started: false,
      };
    }

    function applyStreamMarkdown(state) {
      state.mdEl.style.display = "block";
      state.mdEl.innerHTML = renderMarkdown(state.rawText);
      state.bubble.dataset.mdSource = state.rawText;
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function appendStreamToken(state, token) {
      if (!token) return;
      if (!state.started) {
        state.started = true;
        if (state.typingEl) state.typingEl.remove();
      }
      state.rawText += token;
      applyStreamMarkdown(state);
    }

    async function consumeSseResponse(response, state) {
      if (!response.body) throw new Error("Streaming is not supported in this browser.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventName = "message";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";

        for (const line of parts) {
          if (line.startsWith("event:")) {
            eventName = line.slice(6).trim();
            continue;
          }
          if (line.startsWith("data:")) {
            const raw = line.slice(5).trim();
            let payload = {};
            try {
              payload = raw ? JSON.parse(raw) : {};
            } catch (_) {
              payload = { text: raw };
            }

            if (eventName === "meta") {
              if (payload.title) state.metaEl.textContent = payload.title;
            } else if (eventName === "token") {
              appendStreamToken(state, payload.text || "");
            } else if (eventName === "assignments") {
              state.extraEl.insertAdjacentHTML("beforeend", renderAssignmentList(payload));
            } else if (eventName === "courses") {
              state.extraEl.insertAdjacentHTML("beforeend", renderCourseList(payload));
            } else if (eventName === "action") {
              renderUploadAction(payload, state.bubble);
            } else if (eventName === "error") {
              appendStreamToken(state, payload.detail || "Assistant stream error.");
            }
            eventName = "message";
            continue;
          }
          if (!line.trim()) eventName = "message";
        }
      }
    }

    async function sendChatMessage(rawText) {
      const text = (rawText || "").trim();
      if (!text || chatBusy) return;
      chatBusy = true;
      stopRequested = false;
      activeController = new AbortController();
      setStopMode();
      appendChatBubble("user", escapeHtml(text), false, text);
      chatInput.value = "";
      autosizeInput();

      const state = createStreamingBotBubble();

      try {
        const response = await fetch(`${API}/api/assistant/chat/stream`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
          signal: activeController.signal,
        });
        if (response.status === 401) {
          window.location.href = LOGIN_URL;
          return;
        }
        if (!response.ok) {
          let detail = "Assistant request failed.";
          try {
            const err = await response.json();
            detail = err.detail || detail;
          } catch (_) {}
          throw new Error(detail);
        }

        await consumeSseResponse(response, state);

        if (stopRequested) {
          if (state.typingEl) state.typingEl.remove();
          if (!state.started) {
            state.rawText = "Generation stopped.";
            applyStreamMarkdown(state);
          } else {
            const note = document.createElement("div");
            note.className = "chat-stopped";
            note.textContent = "Generation stopped.";
            state.bubble.appendChild(note);
          }
        } else if (!state.started) {
          if (state.typingEl) state.typingEl.remove();
          state.rawText = "No response received. Please try again.";
          applyStreamMarkdown(state);
        }
      } catch (error) {
        if (state.typingEl) state.typingEl.remove();
        const aborted =
          stopRequested ||
          error.name === "AbortError" ||
          /aborted/i.test(String(error.message || ""));
        if (aborted) {
          if (!state.started) {
            state.rawText = "Generation stopped.";
            applyStreamMarkdown(state);
          } else if (!state.bubble.querySelector(".chat-stopped")) {
            const note = document.createElement("div");
            note.className = "chat-stopped";
            note.textContent = "Generation stopped.";
            state.bubble.appendChild(note);
          }
        } else {
          state.rawText = error.message || "Could not reach the assistant.";
          applyStreamMarkdown(state);
        }
      } finally {
        persistMessagesFromDom();
        chatBusy = false;
        stopRequested = false;
        activeController = null;
        setSendMode();
        chatInput.focus();
      }
    }

    function openChat() {
      chatPanel.classList.add("open");
      saveState({ open: true });
      chatInput.focus();
    }

    function closeChat() {
      chatPanel.classList.remove("open");
      saveState({ open: false });
    }

    chatFab.addEventListener("click", () => {
      if (chatPanel.classList.contains("open")) closeChat();
      else openChat();
    });
    document.getElementById("chatClose").addEventListener("click", closeChat);
    document.getElementById("chatClear").addEventListener("click", () => {
      if (chatBusy) stopGeneration();
      saveState({ messages: [] });
      restoreMessages([]);
    });
    chatForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (chatBusy) return;
      sendChatMessage(chatInput.value);
    });
    chatSend.addEventListener("click", (event) => {
      if (!chatBusy) return;
      event.preventDefault();
      stopGeneration();
    });
    chatInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      if (event.shiftKey) {
        // Allow newline in textarea
        return;
      }
      event.preventDefault();
      if (chatBusy) return;
      sendChatMessage(chatInput.value);
    });
    chatInput.addEventListener("input", autosizeInput);
    document.querySelectorAll("#chatChips .chat-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        if (chatBusy) return;
        sendChatMessage(chip.dataset.chat);
      });
    });
    setSendMode();
    autosizeInput();

    const state = loadState();
    restoreMessages(state.messages);
    if (state.open) openChat();

    window.ERPAssistant = {
      open: openChat,
      close: closeChat,
      clear() {
        saveState({ messages: [], open: false });
        restoreMessages([]);
        closeChat();
      },
    };
  }

  function boot() {
    try {
      // Drop v1 cache (plain innerText) so old broken history is not restored.
      sessionStorage.removeItem("erp_ai_chat_v1");
    } catch (_) {}
    ensureMarked(() => {
      if (window.marked) marked.setOptions({ gfm: true, breaks: true });
      init();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
