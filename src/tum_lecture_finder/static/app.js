/**
 * TUM Lecture Finder — Client-side search interaction.
 *
 * Handles AJAX search, filter population, URL state, and keyboard navigation.
 * Search executes on form submit (Enter key) only — no debounced auto-search.
 */

(function () {
  "use strict";

  // i18n helper — provided by i18n.js loaded before this script
  var t = window.t || function (k) { return k; };
  var escapeHtml = window.escapeHtml;

  // ── DOM references ────────────────────────────────────────────────
  var searchForm = document.getElementById("search-form");
  var searchInput = document.getElementById("search-input");
  var modeSelect = document.getElementById("filter-mode");
  var semesterSelect = document.getElementById("filter-semester");
  var campusSelect = document.getElementById("filter-campus");
  var typeSelect = document.getElementById("filter-type");
  var resultsStatus = document.getElementById("results-status");
  var resultsList = document.getElementById("results-list");
  var loadingIndicator = document.getElementById("loading-indicator");
  var emptyState = document.getElementById("empty-state");
  var loadMoreContainer = document.getElementById("load-more-container");
  var loadMoreBtn = document.getElementById("load-more-btn");

  // ── State ─────────────────────────────────────────────────────────
  var currentController = null;
  var currentOffset = 0;
  var currentTotalCount = 0;
  var PAGE_SIZE = 20;

  // ── Initialize ────────────────────────────────────────────────────
  function init() {
    loadFilters();
    restoreFromURL();
    bindEvents();
  }

  // ── Load filter options from API ──────────────────────────────────
  function loadFilters() {
    fetch("/api/filters")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        populateCampusFilter(data.campuses || []);
        populateTypeFilter(data.course_types || []);
        restoreFromURL();
      })
      .catch(function () {
        // Filters fail gracefully
      });
  }

  function populateCampusFilter(campuses) {
    var savedValue = campusSelect.value;
    campusSelect.innerHTML = '<option value="">' + t("filter.all_campuses") + '</option>';
    campuses.forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = c.campus;
      opt.textContent =
        (c.display || capitalize(c.campus)) +
        " (" +
        c.count.toLocaleString() +
        ")";
      campusSelect.appendChild(opt);
    });
    if (savedValue) campusSelect.value = savedValue;
  }

  function populateTypeFilter(types) {
    var savedValue = typeSelect.value;
    typeSelect.innerHTML = '<option value="">' + t("filter.all_types") + '</option>';
    types.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t.type;
      opt.textContent = t.type + " (" + t.count.toLocaleString() + ")";
      typeSelect.appendChild(opt);
    });
    if (savedValue) typeSelect.value = savedValue;
  }

  // ── Event binding ─────────────────────────────────────────────────
  function bindEvents() {
    // Search only on form submit (Enter key)
    searchForm.addEventListener("submit", function (e) {
      e.preventDefault();
      currentOffset = 0;
      doSearch(false);
    });

    // Keyboard shortcut: / to focus search
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== searchInput) {
        e.preventDefault();
        searchInput.focus();
        searchInput.select();
      }
      if (e.key === "Escape" && document.activeElement === searchInput) {
        searchInput.blur();
      }
    });

    // Load more button
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener("click", function () {
        currentOffset += PAGE_SIZE;
        doSearch(true);
      });
    }
  }

  // ── Search execution ──────────────────────────────────────────────
  function doSearch(append) {
    var query = searchInput.value.trim();
    if (!query) {
      clearResults();
      showEmptyState();
      return;
    }

    if (currentController) {
      currentController.abort();
    }
    currentController = new AbortController();

    var params = new URLSearchParams();
    params.set("q", query);
    params.set("mode", modeSelect.value);
    if (semesterSelect && semesterSelect.value)
      params.set("semester", semesterSelect.value);
    if (campusSelect.value) params.set("campus", campusSelect.value);
    if (typeSelect.value) params.set("type", typeSelect.value);
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(currentOffset));

    // Update URL (without offset for clean URLs)
    var urlParams = new URLSearchParams();
    urlParams.set("q", query);
    urlParams.set("mode", modeSelect.value);
    if (semesterSelect && semesterSelect.value)
      urlParams.set("semester", semesterSelect.value);
    if (campusSelect.value) urlParams.set("campus", campusSelect.value);
    if (typeSelect.value) urlParams.set("type", typeSelect.value);
    history.replaceState(null, "", "/?" + urlParams.toString());

    if (!append) {
      showLoading();
    } else {
      loadMoreBtn.disabled = true;
      loadMoreBtn.textContent = t("results.loading_more");
    }

    var startTime = performance.now();

    fetch("/api/search?" + params.toString(), {
      signal: currentController.signal,
    })
      .then(function (r) {
        if (!r.ok) {
          if (r.status === 429) {
            throw new Error("Rate limit exceeded. Please wait a moment.");
          }
          throw new Error("Search failed (HTTP " + r.status + ")");
        }
        return r.json();
      })
      .then(function (data) {
        var elapsed = Math.round(performance.now() - startTime);
        currentTotalCount = data.total_count || data.count;
        renderResults(data, elapsed, append);
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        hideLoading();
        hideLoadMore();
        showError(err.message);
      });
  }

  // ── Rendering ─────────────────────────────────────────────────────
  function renderResults(data, elapsedMs, append) {
    hideLoading();
    hideEmptyState();

    if (!append) {
      clearResults();
    }

    if (data.results.length === 0 && !append) {
      resultsStatus.classList.remove("hidden");
      resultsStatus.innerHTML =
        '<span>' + t("results.no_results") + ' <strong>"' +
        escapeHtml(data.query) +
        '"</strong></span>';
      hideLoadMore();
      return;
    }

    // Status bar
    resultsStatus.classList.remove("hidden");
    var showing = currentOffset + data.results.length;
    resultsStatus.innerHTML =
      '<span>' + t("results.showing")
        .replace("{shown}", showing)
        .replace("{total}", currentTotalCount) +
      ' <strong>"' +
      escapeHtml(data.query) +
      '"</strong></span>' +
      '<span class="time">' +
      t("results.time_and_mode").replace("{ms}", elapsedMs).replace("{mode}", data.mode) +
      "</span>";

    // Result cards
    var fragment = document.createDocumentFragment();
    data.results.forEach(function (r) {
      fragment.appendChild(createResultCard(r));
    });
    resultsList.appendChild(fragment);

    // Load more button
    if (data.has_more) {
      showLoadMore();
    } else {
      hideLoadMore();
    }
  }

  function createResultCard(r) {
    var card = document.createElement("a");
    card.className = "result-card";
    card.href = "/course/" + r.course_id;

    var title = r.title_en || r.title_de;
    var subtitle =
      r.title_en && r.title_de && r.title_en !== r.title_de ? r.title_de : "";

    var html = '<div class="result-header">';
    html += '<div><div class="result-title">' + escapeHtml(title) + "</div>";
    if (subtitle) {
      html += '<div class="result-subtitle">' + escapeHtml(subtitle) + "</div>";
    }
    html += "</div>";
    html += "</div>";

    // Meta badges
    html += '<div class="result-meta">';
    if (r.course_number) {
      html +=
        '<span class="badge badge-code">' +
        escapeHtml(r.course_number) +
        "</span>";
    }
    if (r.course_type) {
      html +=
        '<span class="badge badge-type">' +
        escapeHtml(r.course_type) +
        "</span>";
    }
    if (r.semester_display) {
      html +=
        '<span class="badge badge-semester">' +
        escapeHtml(r.semester_display) +
        "</span>";
    }
    if (r.campus_display) {
      html +=
        '<span class="badge badge-campus">' +
        escapeHtml(r.campus_display) +
        "</span>";
    }
    if (r.offering_frequency) {
      html +=
        '<span class="badge badge-frequency">' +
        escapeHtml(r.offering_frequency) +
        "</span>";
    }
    if (r.organisation) {
      html +=
        '<span class="result-org">' +
        escapeHtml(truncate(r.organisation, 60)) +
        "</span>";
    }
    html += "</div>";

    if (r.snippet) {
      html +=
        '<div class="result-snippet">…' + escapeHtml(r.snippet) + "…</div>";
    }

    card.innerHTML = html;
    return card;
  }

  // ── URL state management ──────────────────────────────────────────
  function setSelectIfValid(selectEl, value) {
    if (!selectEl) return;
    for (var i = 0; i < selectEl.options.length; i++) {
      if (selectEl.options[i].value === value) {
        selectEl.value = value;
        return;
      }
    }
  }

  function restoreFromURL() {
    var params = new URLSearchParams(window.location.search);
    if (params.has("q")) searchInput.value = params.get("q");
    if (params.has("mode")) setSelectIfValid(modeSelect, params.get("mode"));
    if (params.has("semester"))
      setSelectIfValid(semesterSelect, params.get("semester"));
    if (params.has("campus"))
      setSelectIfValid(campusSelect, params.get("campus"));
    if (params.has("type")) setSelectIfValid(typeSelect, params.get("type"));

    if (params.has("q") && params.get("q").trim()) {
      currentOffset = 0;
      doSearch(false);
    }
  }

  // ── UI helpers ────────────────────────────────────────────────────
  function showLoading() {
    document.getElementById("results-container").setAttribute("aria-busy", "true");
    loadingIndicator.classList.remove("hidden");
    emptyState.classList.add("hidden");
    resultsStatus.classList.add("hidden");
    resultsList.innerHTML = "";
    hideLoadMore();
  }

  function hideLoading() {
    document.getElementById("results-container").setAttribute("aria-busy", "false");
    loadingIndicator.classList.add("hidden");
  }

  function showEmptyState() {
    emptyState.classList.remove("hidden");
    resultsStatus.classList.add("hidden");
    hideLoadMore();
  }

  function hideEmptyState() {
    emptyState.classList.add("hidden");
  }

  function clearResults() {
    resultsList.innerHTML = "";
    resultsStatus.classList.add("hidden");
    hideLoadMore();
  }

  function showLoadMore() {
    if (loadMoreContainer) {
      loadMoreContainer.classList.remove("hidden");
      loadMoreBtn.disabled = false;
      loadMoreBtn.textContent = t("results.load_more");
    }
  }

  function hideLoadMore() {
    if (loadMoreContainer) {
      loadMoreContainer.classList.add("hidden");
    }
  }

  function showError(message) {
    resultsStatus.classList.remove("hidden");
    resultsStatus.innerHTML =
      '<span class="error-message" style="width:100%">' +
      escapeHtml(message) +
      "</span>";
  }

  // ── Utility ───────────────────────────────────────────────────────

  function capitalize(s) {
    if (!s) return "";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function truncate(s, len) {
    if (!s || s.length <= len) return s;
    return s.substring(0, len) + "…";
  }

  // ── Boot ──────────────────────────────────────────────────────────
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
