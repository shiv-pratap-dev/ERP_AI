/**
 * Faculty LMS transaction sidebar, matching the recorded menu.
 */
(function () {
  const ITEMS = [
    ["Select Course", "/faculty/LMS/select-course.html"],
    ["Create Assignment", "/faculty/LMS/create-assignment.html"],
    ["Check Student Assignment", "/faculty/LMS/check-assignment.html"],
    ["Create Question Bank", ""],
    ["Test Creation", ""],
    ["Create Announcement", ""],
    ["Create E-Library", ""],
    ["Create Syllabus", ""],
    ["Create LectureNotes", ""],
    ["Create Teaching Plan", ""],
    ["Create Forum", ""],
    ["Bulk Student Login Creation", ""],
    ["Discussion Forum", ""],
    ["Personal Calendar", ""],
    ["Import Question Bank", ""],
    ["Export Question Bank", ""],
    ["Allow Retest", ""],
    ["Reply To FAQ", ""],
    ["Mark Entry for Descriptive Test", ""],
    ["Request For ReTest", ""],
  ];

  function favorites() {
    try {
      return new Set(JSON.parse(localStorage.getItem("lmsFavorites") || "[]"));
    } catch (_) {
      return new Set();
    }
  }

  function saveFavorites(set) {
    localStorage.setItem("lmsFavorites", JSON.stringify(Array.from(set)));
  }

  const mount = document.getElementById("lms-sidebar");
  if (!mount) return;
  const active = mount.dataset.active || "";
  const favs = favorites();
  const aside = document.createElement("aside");
  aside.className = "lms-side";
  aside.innerHTML = `
    <div class="lms-side-head">
      <span>Transactions</span>
      <button type="button" id="lmsSideClose" class="text-gray-400 text-lg leading-none" aria-label="Close">×</button>
    </div>
    <nav class="overflow-y-auto flex-1">
      ${ITEMS.map(([label, href]) => `
        <a class="lms-link${label === active ? " active" : ""}" href="${href || "#"}" ${href ? "" : `data-page="${label}"`}>
          <span>${label}</span>
          <button type="button" class="star${favs.has(label) ? " on" : ""}" data-fav="${label}" title="Favorite">☆</button>
        </a>
      `).join("")}
    </nav>
  `;
  mount.replaceWith(aside);

  aside.querySelector("#lmsSideClose")?.addEventListener("click", () => {
    aside.style.display = "none";
  });
  aside.querySelectorAll(".star").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const next = favorites();
      const label = button.dataset.fav;
      if (next.has(label)) {
        next.delete(label);
        button.classList.remove("on");
        button.textContent = "☆";
      } else {
        next.add(label);
        button.classList.add("on");
        button.textContent = "★";
      }
      saveFavorites(next);
    });
    if (button.classList.contains("on")) button.textContent = "★";
  });
  aside.querySelectorAll("a[data-page]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      if (window.showNotice) window.showNotice(link.dataset.page);
    });
  });
})();
