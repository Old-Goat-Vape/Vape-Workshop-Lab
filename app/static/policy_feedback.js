(function () {
  function toNode(container) {
    if (!container) return null;
    if (typeof container === "string") return document.querySelector(container);
    return container;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function classify(policy, options) {
    var p = policy || {};
    var warnings = Array.isArray(p.warnings) ? p.warnings.filter(Boolean) : [];
    var hardStops = Array.isArray(p.hard_stops) ? p.hard_stops.filter(Boolean) : [];
    var blocked = hardStops.length > 0 || p.allowed === false || options.forceBlocked === true;
    return { warnings: warnings, hardStops: hardStops, blocked: blocked };
  }

  function applyTone(node, tone, blocked) {
    if (!node) return;
    node.classList.remove("d-none", "alert-warning", "alert-danger", "alert-info");
    if (tone === "lab") {
      node.classList.add("alert", blocked ? "alert-danger" : "alert-warning");
      return;
    }
    node.classList.add("alert", blocked ? "alert-danger" : "alert-warning");
  }

  function clear(container) {
    var node = toNode(container);
    if (!node) return;
    node.classList.add("d-none");
    node.textContent = "";
    node.innerHTML = "";
  }

  function render(container, policy, options) {
    var node = toNode(container);
    if (!node) return { blocked: false, warnings: [], hardStops: [] };
    var opts = options || {};
    var tone = opts.tone || "tvw";
    var cls = classify(policy, opts);
    if (!cls.warnings.length && !cls.hardStops.length) {
      clear(node);
      return cls;
    }
    applyTone(node, tone, cls.blocked);
    var lead;
    if (cls.blocked) {
      lead = opts.blockLead || (tone === "lab"
        ? "Policy hard stop: fix these values before continuing."
        : "Policy blocked: fix these values before continuing.");
    } else {
      lead = opts.warnLead || (tone === "lab"
        ? "Policy warning: review before production."
        : "Policy notice: you can continue, but review warnings.");
    }
    var lines = [lead]
      .concat(cls.warnings.map(function (w) { return "- " + esc(w); }))
      .concat(cls.hardStops.map(function (h) { return "- " + esc(h); }));
    node.innerHTML = lines.join("<br>");
    return cls;
  }

  function notice(container, message, options) {
    var node = toNode(container);
    if (!node) return;
    var opts = options || {};
    var tone = opts.tone || "tvw";
    var blocked = opts.severity === "danger";
    applyTone(node, tone, blocked);
    node.innerHTML = esc(message || "");
  }

  window.PolicyFeedback = { render: render, notice: notice, clear: clear };
})();

