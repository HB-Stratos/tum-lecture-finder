/**
 * Internationalisation (i18n) for TUM Lecture Finder.
 *
 * This script is loaded in <head> (render-blocking) so the language is
 * resolved before any other script calls window.t().  Translation strings
 * are fetched asynchronously; until the fetch completes, t() returns the
 * raw key as a safe fallback.  The language toggle wiring runs after
 * DOMContentLoaded.
 */
(function () {
  "use strict";

  // ── Early detection (runs immediately, before first paint) ──────────
  var lang =
    localStorage.getItem("lang") ||
    document.documentElement.getAttribute("lang") ||
    "en";

  window.__TLF_LANG = lang;
  window.__TLF_TRANSLATIONS = {};

  /**
   * Look up a translation key.  Returns the translated string if available,
   * otherwise returns the key itself so the UI is never blank.
   */
  window.t = function (key) {
    var val = window.__TLF_TRANSLATIONS[key];
    return val !== undefined ? val : key;
  };

  // ── Fetch translations (async, fire-and-forget) ─────────────────────
  fetch("/static/i18n/" + lang + ".json")
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (data) {
      window.__TLF_TRANSLATIONS = data;
    })
    .catch(function () {
      // Silently fall back to raw keys — the UI remains usable.
    });

  // ── Toggle wiring (runs after DOM is ready) ─────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("lang-toggle");
    if (!toggle) return;

    var label = toggle.querySelector(".lang-label");
    if (label) {
      label.textContent = lang.toUpperCase();
    }

    toggle.addEventListener("click", function () {
      var newLang = lang === "en" ? "de" : "en";
      localStorage.setItem("lang", newLang);
      document.cookie =
        "lang=" + newLang + ";path=/;max-age=31536000;SameSite=Lax";
      location.reload();
    });
  });
})();
