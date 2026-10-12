(function () {
  "use strict";

  var root = document.querySelector("[data-tvw-discover-tabs]");
  if (!root) return;

  var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
  var panels = Array.prototype.slice.call(root.querySelectorAll('[role="tabpanel"]'));
  if (!tabs.length || !panels.length) return;

  function panelForTab(tab) {
    var panelId = tab.getAttribute("aria-controls");
    return panelId ? document.getElementById(panelId) : null;
  }

  function activateTab(tab) {
    tabs.forEach(function (item) {
      var selected = item === tab;
      item.classList.toggle("is-active", selected);
      item.setAttribute("aria-selected", selected ? "true" : "false");
      item.tabIndex = selected ? 0 : -1;
    });
    panels.forEach(function (panel) {
      var linked = panelForTab(tab);
      var active = panel === linked;
      panel.classList.toggle("is-active", active);
      if (active) {
        panel.removeAttribute("hidden");
      } else {
        panel.setAttribute("hidden", "");
      }
    });
    tab.focus();
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      activateTab(tab);
    });
    tab.addEventListener("keydown", function (event) {
      var index = tabs.indexOf(tab);
      var next = -1;
      if (event.key === "ArrowRight") {
        next = (index + 1) % tabs.length;
      } else if (event.key === "ArrowLeft") {
        next = (index - 1 + tabs.length) % tabs.length;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = tabs.length - 1;
      }
      if (next >= 0) {
        event.preventDefault();
        activateTab(tabs[next]);
      }
    });
  });
})();
