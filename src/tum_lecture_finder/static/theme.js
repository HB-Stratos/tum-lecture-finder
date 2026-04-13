/**
 * Theme toggle for TUM Lecture Finder.
 *
 * This script is loaded in <head> (render-blocking) so the saved theme
 * is applied before the first paint, preventing a flash of the wrong mode.
 * The toggle button wiring runs after DOMContentLoaded.
 */
(function () {
  "use strict";

  // ── Early detection (runs immediately, before first paint) ──────────
  var saved = localStorage.getItem("theme");
  if (saved) {
    document.documentElement.setAttribute("data-theme", saved);
  }

  // ── Toggle wiring (runs after DOM is ready) ─────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("theme-toggle");
    if (!toggle) return;

    var iconSun = toggle.querySelector(".icon-sun");
    var iconMoon = toggle.querySelector(".icon-moon");

    function updateIcons() {
      var isDark =
        document.documentElement.getAttribute("data-theme") === "dark" ||
        (!document.documentElement.getAttribute("data-theme") &&
          window.matchMedia("(prefers-color-scheme: dark)").matches);
      iconSun.style.display = isDark ? "none" : "block";
      iconMoon.style.display = isDark ? "block" : "none";
    }

    toggle.addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-theme");
      var isDarkSystem = window.matchMedia(
        "(prefers-color-scheme: dark)"
      ).matches;
      var next;

      if (!current) {
        next = isDarkSystem ? "light" : "dark";
      } else if (current === "dark") {
        next = "light";
      } else {
        next = "dark";
      }

      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("theme", next);
      updateIcons();
    });

    updateIcons();
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", updateIcons);
  });
})();
