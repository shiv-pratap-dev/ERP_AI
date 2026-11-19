/**
 * Universal portal navbar (Home / Academic / Examination / LMS / Pay Online).
 *
 * Usage (same on every page):
 *   <link rel="stylesheet" href="/frontend/shared/navbar.css">
 *   <div id="portal-navbar"></div>
 *   <script src="/frontend/shared/navbar.js"></script>
 *
 * Markup source of truth is also kept at /frontend/shared/navbar.html
 * (kept in sync with NAVBAR_HTML below for static serving / inspection).
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
  const PROFILE_URL = "/Academic/student-detail.html";

  const NAVBAR_HTML = `
<header id="portalNavbar" class="bg-white border-b border-gray-200 sticky top-0 z-40">
  <div class="h-[58px] px-3 flex items-center gap-4">
    <a href="/home" class="flex items-center gap-1 shrink-0">
      <img src="/assests/jecrc-logo.jpeg" alt="JECRC" class="h-9 w-auto"
           onerror="this.style.display='none'">
    </a>

    <nav class="header-nav flex items-center gap-1 sm:gap-4 text-sm text-gray-600 flex-1">
      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">Academic <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/Academic/student-detail.html">Student Related</a>
          <a href="#" data-page="Feedback">Feedback</a>
        </div>
      </div>

      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">Examination <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/Examination/examination.html?view=timetable">Exam Time Table</a>
          <a href="/Examination/examination.html?view=admit">Admit Card for Student</a>
          <a href="/Examination/examination.html?view=backlog">BackLog Exam Reg</a>
        </div>
      </div>

      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">LMS <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/LMS/transaction.html">Transactions</a>
          <a href="/LMS/view.html">View</a>
          <a href="#" data-page="Virtual Notice Board">Virtual Notice Board</a>
        </div>
      </div>

      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">Pay Online <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/PayOnline/payment.html">Online Payment</a>
        </div>
      </div>
    </nav>

    <div class="flex items-center gap-3 shrink-0">
      <button title="Search" class="text-gray-500 text-xl" type="button" data-page="Search">⌕</button>
      <button class="border border-red-200 text-red-500 px-2 py-1 text-xs rounded" type="button" data-page="FAQs">FAQs</button>
      <button title="Applications" class="text-blue-600 text-xl" type="button" data-page="Applications">▦</button>

      <div class="relative">
        <button id="profileButton" class="w-9 h-9 rounded-full overflow-hidden border border-gray-200"
                aria-label="Open profile menu" aria-expanded="false" type="button">
          <img src="/assests/student-profile.jpg" alt="Student profile"
               class="w-full h-full object-cover"
               onerror="this.style.display='none'; this.parentElement.textContent='S'; this.parentElement.classList.add('bg-yellow-400','text-white','font-bold','grid','place-items-center')">
        </button>

        <div id="profileMenu" class="profile-menu rounded-b-lg">
          <div class="p-5 text-center border-b">
            <div class="w-20 h-20 rounded-full bg-yellow-400 mx-auto mb-3 flex items-center justify-center text-white text-3xl font-bold">S</div>
            <p id="welcomeMessage" class="font-medium text-gray-800">Welcome!</p>
          </div>
          <a href="${PROFILE_URL}" class="block px-4 py-3 hover:bg-gray-100 text-sm">👤 &nbsp; User Profile</a>
          <a href="/home" class="block px-4 py-3 hover:bg-gray-100 text-sm">🏠 &nbsp; Home</a>
          <div class="flex justify-around border-t p-4">
            <button id="changePasswordBtn" title="Change Password" class="utility-btn" type="button">🔑</button>
            <button title="Logout" onclick="logout()" class="bg-red-600 text-white rounded-lg w-11 h-11" type="button">⏻</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</header>
`.trim();

  const MODALS_HTML = `
<div id="portalNoticeModal" class="fixed inset-0 bg-black/40 hidden items-center justify-center z-[10000]">
  <div class="bg-white rounded-xl shadow-xl w-[min(420px,92vw)] p-5" role="dialog" aria-modal="true">
    <div class="flex items-start justify-between gap-3 mb-2">
      <h3 id="portalModalTitle" class="text-lg font-semibold text-gray-800"></h3>
      <button type="button" onclick="closeNotice()" class="text-xl text-gray-500 hover:text-black leading-none" aria-label="Close">×</button>
    </div>
    <p id="portalModalText" class="text-sm text-gray-600 mb-4"></p>
    <div class="text-right">
      <button type="button" onclick="closeNotice()" class="px-4 py-2 rounded bg-indigo-600 text-white text-sm">OK</button>
    </div>
  </div>
</div>

<div id="portalPasswordModal" class="fixed inset-0 bg-black/40 hidden items-center justify-center z-[10000]">
  <div class="bg-white rounded-xl shadow-xl w-[min(420px,92vw)] p-5" role="dialog" aria-modal="true" aria-labelledby="passwordModalTitle">
    <div class="flex items-start justify-between gap-3 mb-3">
      <h3 id="passwordModalTitle" class="text-lg font-semibold text-gray-800">Change Password</h3>
      <button type="button" id="passwordModalClose" class="text-xl text-gray-500 hover:text-black leading-none" aria-label="Close">×</button>
    </div>
    <form id="changePasswordForm" class="space-y-3">
      <div>
        <label class="block text-sm text-gray-600 mb-1" for="currentPassword">Current password</label>
        <input id="currentPassword" type="password" required autocomplete="current-password"
               class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
      <div>
        <label class="block text-sm text-gray-600 mb-1" for="newPassword">New password</label>
        <input id="newPassword" type="password" required minlength="6" autocomplete="new-password"
               class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
      <div>
        <label class="block text-sm text-gray-600 mb-1" for="confirmPassword">Confirm new password</label>
        <input id="confirmPassword" type="password" required minlength="6" autocomplete="new-password"
               class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
      <p id="passwordFormError" class="text-sm text-red-600 hidden"></p>
      <p id="passwordFormSuccess" class="text-sm text-green-600 hidden"></p>
      <div class="flex justify-end gap-2 pt-1">
        <button type="button" id="passwordCancelBtn" class="px-4 py-2 rounded border text-sm">Cancel</button>
        <button type="submit" id="passwordSubmitBtn" class="px-4 py-2 rounded bg-indigo-600 text-white text-sm">Update</button>
      </div>
    </form>
  </div>
</div>
`.trim();

  function closeProfileMenu() {
    const profileButton = document.getElementById("profileButton");
    const profileMenu = document.getElementById("profileMenu");
    if (profileMenu) profileMenu.classList.remove("open");
    if (profileButton) profileButton.setAttribute("aria-expanded", "false");
  }

  function showNotice(title) {
    closeProfileMenu();
    ensureModals();
    const modal = document.getElementById("portalNoticeModal");
    const titleEl = document.getElementById("portalModalTitle");
    const textEl = document.getElementById("portalModalText");
    if (!modal || !titleEl || !textEl) {
      window.alert(String(title || "Notice"));
      return;
    }
    titleEl.textContent = title;
    textEl.textContent =
      title === "Assignments"
        ? "Open LMS → Transactions to view and submit assignments."
        : "This section is a UI placeholder and will be implemented next.";
    modal.classList.remove("hidden");
    modal.classList.add("flex");
  }

  function closeNotice() {
    const modal = document.getElementById("portalNoticeModal");
    if (!modal) return;
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  }

  function openPasswordModal() {
    closeProfileMenu();
    ensureModals();
    const modal = document.getElementById("portalPasswordModal");
    const form = document.getElementById("changePasswordForm");
    const err = document.getElementById("passwordFormError");
    const ok = document.getElementById("passwordFormSuccess");
    if (!modal) return;
    if (form) form.reset();
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    if (ok) {
      ok.textContent = "";
      ok.classList.add("hidden");
    }
    modal.classList.remove("hidden");
    modal.classList.add("flex");
    const first = document.getElementById("currentPassword");
    if (first) first.focus();
  }

  function closePasswordModal() {
    const modal = document.getElementById("portalPasswordModal");
    if (!modal) return;
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  }

  async function submitChangePassword(event) {
    event.preventDefault();
    const current = document.getElementById("currentPassword")?.value || "";
    const next = document.getElementById("newPassword")?.value || "";
    const confirm = document.getElementById("confirmPassword")?.value || "";
    const err = document.getElementById("passwordFormError");
    const ok = document.getElementById("passwordFormSuccess");
    const submitBtn = document.getElementById("passwordSubmitBtn");

    function setError(message) {
      if (!err) return;
      err.textContent = message;
      err.classList.remove("hidden");
      if (ok) ok.classList.add("hidden");
    }

    if (next.length < 6) {
      setError("New password must be at least 6 characters.");
      return;
    }
    if (next !== confirm) {
      setError("New password and confirmation do not match.");
      return;
    }
    if (next === current) {
      setError("New password must be different from the current password.");
      return;
    }

    if (submitBtn) submitBtn.disabled = true;
    try {
      const response = await fetch(`${API}/api/change-password`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: current,
          new_password: next,
        }),
      });
      if (response.status === 401) {
        window.location.href = LOGIN_URL;
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(data.detail || "Could not update password.");
        return;
      }
      if (err) err.classList.add("hidden");
      if (ok) {
        ok.textContent = data.message || "Password updated successfully.";
        ok.classList.remove("hidden");
      }
      setTimeout(() => closePasswordModal(), 900);
    } catch (_) {
      setError("Could not reach the server. Try again.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function logout() {
    try {
      await fetch(`${API}/logout`, { method: "POST", credentials: "include" });
    } catch (_) {
      /* ignore */
    }
    try {
      sessionStorage.removeItem("erp_ai_chat_v1");
      sessionStorage.removeItem("erp_ai_chat_v2");
      sessionStorage.clear();
    } catch (_) {}
    window.location.href = LOGIN_URL;
  }

  function ensureModals() {
    if (!document.getElementById("portalNoticeModal") || !document.getElementById("portalPasswordModal")) {
      const wrap = document.createElement("div");
      wrap.innerHTML = MODALS_HTML;
      Array.from(wrap.childNodes).forEach((node) => {
        if (node.nodeType !== 1) return;
        if (node.id && document.getElementById(node.id)) return;
        document.body.appendChild(node);
      });
    }

    // Upgrade older inlined notice modal markup if present without close (X).
    const notice = document.getElementById("portalNoticeModal");
    if (notice) {
      notice.classList.add("z-[10000]");
      notice.style.zIndex = "10000";
    }
    const password = document.getElementById("portalPasswordModal");
    if (password) {
      password.style.zIndex = "10000";
    }
  }

  function patchInlinedNavbar() {
    const profileLink = document.querySelector(
      '#profileMenu a[data-page="User Profile"], #profileMenu a[href="#"]'
    );
    if (profileLink && /user profile/i.test(profileLink.textContent || "")) {
      profileLink.removeAttribute("data-page");
      profileLink.setAttribute("href", PROFILE_URL);
    }

    const oldPwd = document.querySelector(
      '#profileMenu button[title="Change Password"]'
    );
    if (oldPwd && !oldPwd.id) {
      oldPwd.id = "changePasswordBtn";
      oldPwd.removeAttribute("onclick");
    }
  }

  function wireNavbar() {
    ensureModals();
    patchInlinedNavbar();

    const profileButton = document.getElementById("profileButton");
    const profileMenu = document.getElementById("profileMenu");

    if (profileButton && profileMenu) {
      profileButton.addEventListener("click", (event) => {
        event.stopPropagation();
        const open = profileMenu.classList.toggle("open");
        profileButton.setAttribute("aria-expanded", String(open));
      });

      document.addEventListener("click", (event) => {
        if (
          !profileMenu.contains(event.target) &&
          !profileButton.contains(event.target)
        ) {
          closeProfileMenu();
        }
      });
    }

    document.querySelectorAll("#portalNavbar .nav-trigger").forEach((button) => {
      button.addEventListener("click", () => {
        const menu = button.nextElementSibling;
        if (!menu) return;
        const wasOpen = menu.style.display === "block";
        document
          .querySelectorAll("#portalNavbar .nav-dropdown")
          .forEach((other) => {
            other.style.display = "";
          });
        menu.style.display = wasOpen ? "" : "block";
      });
    });

    document
      .querySelectorAll(
        '#portalNavbar a[data-page][href="#"], #portalNavbar button[data-page]'
      )
      .forEach((el) => {
        el.addEventListener("click", (event) => {
          event.preventDefault();
          showNotice(el.dataset.page);
        });
      });

    const changePasswordBtn = document.getElementById("changePasswordBtn");
    if (changePasswordBtn) {
      changePasswordBtn.addEventListener("click", (event) => {
        event.preventDefault();
        openPasswordModal();
      });
    }

    const form = document.getElementById("changePasswordForm");
    if (form && form.dataset.wired !== "1") {
      form.dataset.wired = "1";
      form.addEventListener("submit", submitChangePassword);
    }
    const pwdClose = document.getElementById("passwordModalClose");
    const pwdCancel = document.getElementById("passwordCancelBtn");
    if (pwdClose) pwdClose.addEventListener("click", closePasswordModal);
    if (pwdCancel) pwdCancel.addEventListener("click", closePasswordModal);

    const noticeModal = document.getElementById("portalNoticeModal");
    if (noticeModal && noticeModal.dataset.wired !== "1") {
      noticeModal.dataset.wired = "1";
      noticeModal.addEventListener("click", (event) => {
        if (event.target.id === "portalNoticeModal") closeNotice();
      });
    }
    const passwordModal = document.getElementById("portalPasswordModal");
    if (passwordModal && passwordModal.dataset.wired !== "1") {
      passwordModal.dataset.wired = "1";
      passwordModal.addEventListener("click", (event) => {
        if (event.target.id === "portalPasswordModal") closePasswordModal();
      });
    }

    if (!window.__erpModalEscapeWired) {
      window.__erpModalEscapeWired = true;
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        closeNotice();
        closePasswordModal();
        closeProfileMenu();
        // Also close any page-local notice modal if present.
        const local = document.getElementById("noticeModal");
        if (local) {
          local.classList.add("hidden");
          local.classList.remove("flex");
        }
        const startup = document.getElementById("startupNoticeModal");
        if (startup) {
          startup.classList.add("hidden");
          startup.classList.remove("flex");
        }
      });
    }

    fetch(`${API}/session-user`, { credentials: "include" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        const welcome = document.getElementById("welcomeMessage");
        if (welcome && data && data.user && data.user.email) {
          welcome.textContent = `Welcome ${data.user.email}`;
        }
      })
      .catch(() => {});

    document.dispatchEvent(new CustomEvent("portal-navbar:ready"));
  }

  function mountNavbar() {
    const existing = document.getElementById("portalNavbar");
    if (existing) {
      if (existing.dataset.wired === "1") {
        ensureModals();
        return;
      }
      existing.dataset.wired = "1";
      wireNavbar();
      return;
    }

    const root = document.getElementById("portal-navbar");
    if (!root || root.dataset.mounted === "1") return;
    const wrap = document.createElement("div");
    wrap.innerHTML = NAVBAR_HTML;
    root.replaceWith(...Array.from(wrap.childNodes));
    const header = document.getElementById("portalNavbar");
    if (header) {
      header.dataset.mounted = "1";
      header.dataset.wired = "1";
    }
    wireNavbar();
  }

  window.showNotice = showNotice;
  window.closeNotice = closeNotice;
  window.openPasswordModal = openPasswordModal;
  window.closePasswordModal = closePasswordModal;
  window.logout = logout;

  mountNavbar();
})();
