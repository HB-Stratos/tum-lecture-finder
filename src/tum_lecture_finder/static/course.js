/**
 * TUM Lecture Finder — Course detail page interactions.
 *
 * Handles back-to-search navigation and schedule loading.
 */

(function () {
  "use strict";

  // i18n helper — provided by i18n.js loaded before this script
  var t = window.t || function (k) { return k; };
  var escapeHtml = window.escapeHtml;

  // Back to search — preserve search state
  var backLink = document.getElementById("back-to-search");
  if (backLink) {
    backLink.addEventListener("click", function (e) {
      e.preventDefault();
      if (
        document.referrer &&
        document.referrer.indexOf(location.origin) === 0
      ) {
        history.back();
      } else {
        location.href = "/";
      }
    });
  }

  // Load schedule from API
  var scheduleSection = document.getElementById("schedule-section");
  var scheduleContent = document.getElementById("schedule-content");
  var courseIdMeta = document.querySelector('.course-detail[data-course-id]');

  if (scheduleSection && scheduleContent && courseIdMeta) {
    var courseId = courseIdMeta.getAttribute("data-course-id");

    fetch("/api/course/" + courseId + "/schedule")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.appointments || data.appointments.length === 0) {
          scheduleSection.classList.remove("hidden");
          scheduleContent.innerHTML =
            '<p class="schedule-empty">' + t("schedule.no_data") + '</p>';
          return;
        }
        scheduleSection.classList.remove("hidden");
        var html =
          '<table class="schedule-table" aria-label="' + t("a11y.course_schedule") + '"><thead><tr>' +
          "<th>" + t("schedule.day") + "</th><th>" + t("schedule.time") + "</th><th>" + t("schedule.room") + "</th></tr></thead><tbody>";
        data.appointments.forEach(function (a) {
          var roomCell = a.room_link
            ? '<a href="' +
              escapeHtml(a.room_link) +
              '" target="_blank" rel="noopener noreferrer" class="room-link">' +
              escapeHtml(a.room) +
              " ↗</a>"
            : escapeHtml(a.room);
          html +=
            "<tr><td>" +
            escapeHtml(a.weekday) +
            "</td><td>" +
            escapeHtml(a.time) +
            "</td><td>" +
            roomCell +
            "</td></tr>";
        });
        html += "</tbody></table>";
        scheduleContent.innerHTML = html;
      })
      .catch(function () {
        scheduleSection.classList.remove("hidden");
        scheduleContent.innerHTML =
          '<p class="schedule-empty">' + t("schedule.load_error") + '</p>';
      });
  }
})();
