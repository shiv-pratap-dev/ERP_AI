/**
 * Faculty Establishment transaction sidebar.
 */
(function () {
  const ITEMS = [
    ["Employee Service Book", "/faculty/Establishment/service-book.html"],
    ["Employee Leave Card", "/faculty/Establishment/leave-card.html"],
    ["OD Application", "/faculty/Establishment/od-application.html"],
    ["Comp-Off Apply", "/faculty/Establishment/comp-off.html"],
  ];

  function favorites() {
    try {
      return new Set(JSON.parse(localStorage.getItem("estFavorites") || "[]"));
    } catch (_) {
      return new Set();
    }
  }

  function saveFavorites(set) {
    localStorage.setItem("estFavorites", JSON.stringify(Array.from(set)));
  }

  const mount = document.getElementById("est-sidebar");
  if (!mount) return;
  const active = mount.dataset.active || "";
  const favs = favorites();
  const aside = document.createElement("aside");
  aside.className = "est-side w-60 bg-white border-r hidden md:flex md:flex-col shrink-0";
  aside.innerHTML = `
    <div class="px-4 py-3 text-sm font-semibold text-gray-700 border-b flex items-center justify-between">
      <span>Transactions</span>
      <button type="button" id="estSideClose" class="text-gray-400 text-lg leading-none" aria-label="Close">×</button>
    </div>
    <nav class="overflow-y-auto flex-1">
      ${ITEMS.map(([label, href]) => `
        <a class="side-link${label === active ? " active" : ""}" href="${href}">
          <span>${label}</span>
          <button type="button" class="star${favs.has(label) ? " on" : ""}" data-fav="${label}" title="Favorite">${favs.has(label) ? "★" : "☆"}</button>
        </a>
      `).join("")}
    </nav>
  `;
  mount.replaceWith(aside);

  aside.querySelector("#estSideClose")?.addEventListener("click", () => {
    aside.style.display = "none";
  });
  aside.querySelectorAll("button[data-fav]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const next = favorites();
      const label = button.dataset.fav;
      if (next.has(label)) {
        next.delete(label);
        button.textContent = "☆";
        button.classList.remove("on");
      } else {
        next.add(label);
        button.textContent = "★";
        button.classList.add("on");
      }
      saveFavorites(next);
    });
  });
})();
