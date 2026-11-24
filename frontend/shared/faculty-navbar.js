/**
 * Faculty portal navbar (Academic / Examination / LMS / Payroll / Establishment / OBE).
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

  const NAVBAR_HTML = `
<header id="facultyNavbar" class="bg-white border-b border-gray-200 sticky top-0 z-40">
  <div class="h-[58px] px-3 flex items-center gap-4">
    <a href="/faculty/home" class="flex items-center gap-1 shrink-0">
      <img src="/assests/jecrc-logo.jpeg" alt="JECRC" class="h-9 w-auto"
           onerror="this.style.display='none'">
    </a>

    <nav class="header-nav flex items-center gap-1 sm:gap-3 text-sm text-gray-600 flex-1">
      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">Academic <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/faculty/Academic/teaching-plan.html">Teaching Plan Entry</a>
          <a href="/faculty/Academic/attendance.html">Attendance By Faculty</a>
          <a href="/faculty/Academic/extra-class.html">Extra Class Time Table</a>
          <a href="/faculty/Academic/attendance-register.html">Attendance Register Report</a>
        </div>
      </div>

      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">Examination <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/faculty/Examination/mark-entry.html">Mark Entry</a>
        </div>
      </div>

      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">LMS <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/faculty/LMS/select-course.html">Transactions</a>
          <a href="#" data-page="LMS Reports">Reports</a>
        </div>
      </div>

      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">Payroll <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="#" data-page="Payroll Transactions">Transactions</a>
          <a href="/faculty/Payroll/employee-payslip.html">Reports</a>
        </div>
      </div>

      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">Establishment <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/faculty/Establishment/leave-card.html">Transactions</a>
          <a href="#" data-page="Establishment Reports">Reports</a>
        </div>
      </div>

      <div class="nav-item relative">
        <button class="nav-trigger py-4 hover:text-blue-600" type="button">OBE <span>⌄</span></button>
        <div class="nav-dropdown">
          <a href="/faculty/OBE/question-wise.html">Question Wise Mark Entry</a>
          <a href="/faculty/OBE/question-paper.html">Exam Question Paper</a>
          <a href="/faculty/OBE/portal.html">Go to OBE Portal</a>
          <a href="/faculty/OBE/mark-report.html">Mark Entry Report</a>
        </div>
      </div>
    </nav>

    <div class="flex items-center gap-3 shrink-0">
      <button title="Search" class="text-gray-500 text-xl" type="button" data-page="Search">⌕</button>
      <button class="border border-red-200 text-red-500 px-2 py-1 text-xs rounded" type="button" data-page="FAQs">FAQs</button>
      <button title="Applications" class="text-blue-600 text-xl" type="button" data-page="Applications">▦</button>

      <div class="relative">
        <button id="profileButton" class="w-9 h-9 rounded-full overflow-hidden border border-gray-200 bg-indigo-500 text-white font-bold grid place-items-center"
                aria-label="Open profile menu" aria-expanded="false" type="button">T</button>
        <div id="profileMenu" class="profile-menu rounded-b-lg">
          <div class="p-5 text-center border-b">
            <div class="w-20 h-20 rounded-full bg-indigo-500 mx-auto mb-3 flex items-center justify-center text-white text-3xl font-bold">T</div>
            <p id="welcomeMessage" class="font-medium text-gray-800">Welcome!</p>
          </div>
          <a href="/faculty/home" class="block px-4 py-3 hover:bg-gray-100 text-sm">🏠 &nbsp; Home</a>
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
  <div class="bg-white rounded-xl shadow-xl w-[min(420px,92vw)] p-5">
    <div class="flex items-start justify-between gap-3 mb-2">
      <h3 id="portalModalTitle" class="text-lg font-semibold text-gray-800"></h3>
      <button type="button" onclick="closeNotice()" class="text-xl text-gray-500 hover:text-black leading-none">×</button>
    </div>
    <p id="portalModalText" class="text-sm text-gray-600 mb-4"></p>
    <div class="text-right">
      <button type="button" onclick="closeNotice()" class="px-4 py-2 rounded bg-indigo-600 text-white text-sm">OK</button>
    </div>
  </div>
</div>

<div id="portalPasswordModal" class="fixed inset-0 bg-black/40 hidden items-center justify-center z-[10000]">
  <div class="bg-white rounded-xl shadow-xl w-[min(420px,92vw)] p-5">
    <div class="flex items-start justify-between gap-3 mb-3">
      <h3 class="text-lg font-semibold text-gray-800">Change Password</h3>
      <button type="button" id="passwordModalClose" class="text-xl text-gray-500 hover:text-black leading-none">×</button>
    </div>
    <form id="changePasswordForm" class="space-y-3">
      <div>
        <label class="block text-sm text-gray-600 mb-1" for="currentPassword">Current password</label>
        <input id="currentPassword" type="password" required class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
      <div>
        <label class="block text-sm text-gray-600 mb-1" for="newPassword">New password</label>
        <input id="newPassword" type="password" required minlength="6" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
      <div>
        <label class="block text-sm text-gray-600 mb-1" for="confirmPassword">Confirm new password</label>
        <input id="confirmPassword" type="password" required minlength="6" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
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
    document.getElementById("portalModalTitle").textContent = title;
    document.getElementById("portalModalText").textContent =
      "This faculty module will be implemented next.";
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
    if (form) form.reset();
    document.getElementById("passwordFormError")?.classList.add("hidden");
    document.getElementById("passwordFormSuccess")?.classList.add("hidden");
    modal.classList.remove("hidden");
    modal.classList.add("flex");
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
      ok?.classList.add("hidden");
    }

    if (next.length < 6) return setError("New password must be at least 6 characters.");
    if (next !== confirm) return setError("New password and confirmation do not match.");
    if (next === current) return setError("New password must be different from the current password.");

    if (submitBtn) submitBtn.disabled = true;
    try {
      const response = await fetch(`${API}/api/change-password`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      if (response.status === 401) {
        window.location.href = LOGIN_URL;
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return setError(data.detail || "Could not update password.");
      err?.classList.add("hidden");
      if (ok) {
        ok.textContent = data.message || "Password updated successfully.";
        ok.classList.remove("hidden");
      }
      setTimeout(closePasswordModal, 900);
    } catch (_) {
      setError("Could not reach the server. Try again.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function logout() {
    try {
      await fetch(`${API}/logout`, { method: "POST", credentials: "include" });
    } catch (_) {}
    try {
      sessionStorage.clear();
      localStorage.removeItem("facultySelectedCourse");
    } catch (_) {}
    window.location.href = LOGIN_URL;
  }

  function ensureModals() {
    if (!document.getElementById("portalNoticeModal") || !document.getElementById("portalPasswordModal")) {
      const wrap = document.createElement("div");
      wrap.innerHTML = MODALS_HTML;
      Array.from(wrap.childNodes).forEach((node) => {
        if (node.nodeType === 1 && !(node.id && document.getElementById(node.id))) {
          document.body.appendChild(node);
        }
      });
    }
  }

  function wireNavbar() {
    ensureModals();
    const profileButton = document.getElementById("profileButton");
    const profileMenu = document.getElementById("profileMenu");

    if (profileButton && profileMenu) {
      profileButton.addEventListener("click", (event) => {
        event.stopPropagation();
        const open = profileMenu.classList.toggle("open");
        profileButton.setAttribute("aria-expanded", String(open));
      });
      document.addEventListener("click", (event) => {
        if (!profileMenu.contains(event.target) && !profileButton.contains(event.target)) {
          closeProfileMenu();
        }
      });
    }

    document.querySelectorAll("#facultyNavbar .nav-trigger").forEach((button) => {
      button.addEventListener("click", () => {
        const menu = button.nextElementSibling;
        if (!menu) return;
        const wasOpen = menu.style.display === "block";
        document.querySelectorAll("#facultyNavbar .nav-dropdown").forEach((other) => {
          other.style.display = "";
        });
        menu.style.display = wasOpen ? "" : "block";
      });
    });

    document
      .querySelectorAll('#facultyNavbar a[data-page][href="#"], #facultyNavbar button[data-page]')
      .forEach((el) => {
        el.addEventListener("click", (event) => {
          event.preventDefault();
          showNotice(el.dataset.page);
        });
      });

    document.getElementById("changePasswordBtn")?.addEventListener("click", openPasswordModal);
    const form = document.getElementById("changePasswordForm");
    if (form && form.dataset.wired !== "1") {
      form.dataset.wired = "1";
      form.addEventListener("submit", submitChangePassword);
    }
    document.getElementById("passwordModalClose")?.addEventListener("click", closePasswordModal);
    document.getElementById("passwordCancelBtn")?.addEventListener("click", closePasswordModal);
    document.getElementById("portalNoticeModal")?.addEventListener("click", (event) => {
      if (event.target.id === "portalNoticeModal") closeNotice();
    });
    document.getElementById("portalPasswordModal")?.addEventListener("click", (event) => {
      if (event.target.id === "portalPasswordModal") closePasswordModal();
    });

    fetch(`${API}/api/faculty/me`, { credentials: "include" })
      .then((response) => {
        if (response.status === 401 || response.status === 403) {
          window.location.href = LOGIN_URL;
          return null;
        }
        return response.ok ? response.json() : null;
      })
      .then((data) => {
        const welcome = document.getElementById("welcomeMessage");
        if (welcome && data?.teacher) {
          welcome.textContent = `Welcome ${data.teacher.full_name || data.teacher.email}`;
          const initial = (data.teacher.full_name || "T").trim().charAt(0).toUpperCase();
          profileButton.textContent = initial;
          const avatar = profileMenu.querySelector(".rounded-full");
          if (avatar) avatar.textContent = initial;
        }
      })
      .catch(() => {});
  }

  function mountNavbar() {
    const existing = document.getElementById("facultyNavbar");
    if (existing) {
      if (existing.dataset.wired === "1") return;
      existing.dataset.wired = "1";
      wireNavbar();
      return;
    }
    const root = document.getElementById("faculty-navbar");
    if (!root || root.dataset.mounted === "1") return;
    const wrap = document.createElement("div");
    wrap.innerHTML = NAVBAR_HTML;
    root.replaceWith(...Array.from(wrap.childNodes));
    const header = document.getElementById("facultyNavbar");
    if (header) {
      header.dataset.mounted = "1";
      header.dataset.wired = "1";
    }
    wireNavbar();
  }

  function mountAssistant() {
    if (document.getElementById("chatFab") || document.querySelector("script[data-erp-assistant]")) return;
    if (!document.querySelector('link[href*="erp-assistant.css"]')) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/frontend/shared/erp-assistant.css?v=8";
      document.head.appendChild(link);
    }
    const script = document.createElement("script");
    script.src = "/frontend/shared/erp-assistant.js?v=9";
    script.dataset.erpAssistant = "1";
    document.body.appendChild(script);
  }

  window.showNotice = showNotice;
  window.closeNotice = closeNotice;
  window.logout = logout;
  mountNavbar();
  mountAssistant();
})();
