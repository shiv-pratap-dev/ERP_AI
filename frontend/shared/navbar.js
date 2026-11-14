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
    "http://127.0.0.1:8000";
  const LOGIN_URL =
    window.ERP_LOGIN_URL ||
    window.LOGIN_URL ||
    "http://127.0.0.1:5500/frontend/index.html";

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
      <button title="Search" class="text-gray-500 text-xl" type="button">⌕</button>
      <button class="border border-red-200 text-red-500 px-2 py-1 text-xs rounded" type="button">FAQs</button>
      <button title="Applications" class="text-blue-600 text-xl" type="button">▦</button>

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
          <a href="#" data-page="User Profile" class="block px-4 py-3 hover:bg-gray-100 text-sm">👤 &nbsp; User Profile</a>
          <a href="/home" class="block px-4 py-3 hover:bg-gray-100 text-sm">🏠 &nbsp; Home</a>
          <div class="flex justify-around border-t p-4">
            <button title="Change Password" onclick="showNotice('Change Password')" class="utility-btn" type="button">🔑</button>
            <button title="Logout" onclick="logout()" class="bg-red-600 text-white rounded-lg w-11 h-11" type="button">⏻</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</header>

<div id="portalNoticeModal" class="fixed inset-0 bg-black/40 hidden items-center justify-center z-[80]">
  <div class="bg-white rounded-xl shadow-xl w-[min(420px,92vw)] p-5">
    <h3 id="portalModalTitle" class="text-lg font-semibold text-gray-800 mb-2"></h3>
    <p id="portalModalText" class="text-sm text-gray-600 mb-4"></p>
    <div class="text-right">
      <button type="button" onclick="closeNotice()" class="px-4 py-2 rounded bg-indigo-600 text-white text-sm">OK</button>
    </div>
  </div>
</div>
`.trim();

  function showNotice(title) {
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

  async function logout() {
    try {
      await fetch(`${API}/logout`, { method: "POST", credentials: "include" });
    } catch (_) {
      /* ignore */
    }
    sessionStorage.clear();
    window.location.href = LOGIN_URL;
  }

  function wireNavbar() {
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
          profileMenu.classList.remove("open");
          profileButton.setAttribute("aria-expanded", "false");
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

    // Only stub links (href="#") — never block real module navigation.
    document
      .querySelectorAll(
        '#portalNavbar a[data-page][href="#"], #profileMenu a[data-page][href="#"]'
      )
      .forEach((link) => {
        link.addEventListener("click", (event) => {
          event.preventDefault();
          showNotice(link.dataset.page);
        });
      });

    const modal = document.getElementById("portalNoticeModal");
    if (modal) {
      modal.addEventListener("click", (event) => {
        if (event.target.id === "portalNoticeModal") closeNotice();
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
      if (existing.dataset.wired === "1") return;
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
  window.logout = logout;

  mountNavbar();
})();
