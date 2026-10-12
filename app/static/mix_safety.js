(() => {
  let pendingFormId = null;
  let modalInstance = null;

  function getModalEl() {
    return document.getElementById("globalSafetyModal");
  }

  function getConfirmBtn() {
    return document.getElementById("ogSafetyConfirmBtn");
  }

  function getChecks() {
    return Array.from(document.querySelectorAll(".og-safety-check"));
  }

  function resetModal() {
    getChecks().forEach((c) => (c.checked = false));
    const btn = getConfirmBtn();
    if (btn) btn.disabled = true;
  }

  function gatherSafetyPayload() {
    const payload = {};
    getChecks().forEach((c) => {
      payload[c.dataset.safe] = !!c.checked;
    });
    // Backward/forward compatibility: if the checkbox isn't present in the DOM
    // (stale template cache), default this flag to true since stock is validated
    // again server-side during /mix/init.
    if (payload.ingredients_in_stock === undefined) payload.ingredients_in_stock = true;
    return payload;
  }

  async function postSafety(payload) {
    const res = await fetch("/mix/safety-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return res;
  }

  function resolveFormId(arg, event) {
    if (typeof arg === "string" && arg.length) {
      if (arg.startsWith("mix-form-") || arg === "view-mix-form") return arg;
      return `mix-form-${arg}`;
    }
    if (event && event.target && event.target.id) return event.target.id;
    return null;
  }

  // Exposed for templates that use onclick
  window.openSafetyModal = (formId) => {
    pendingFormId = formId || pendingFormId;

    const el = getModalEl();
    if (!el || !window.bootstrap) {
      alert("Safety modal is unavailable on this page.");
      return;
    }

    if (!modalInstance) modalInstance = new window.bootstrap.Modal(el);
    resetModal();
    modalInstance.show();
  };

  // Exposed for forms: onsubmit="return handleMixSubmit(event, '{{ r.id }}')"
  window.handleMixSubmit = (event, formIdOrRecipeId) => {
    if (event) event.preventDefault();
    pendingFormId = resolveFormId(formIdOrRecipeId, event);
    if (!pendingFormId) return false;
    window.openSafetyModal(pendingFormId);
    return false;
  };

  // Exposed for legacy pages that call confirmSafety() directly
  window.confirmSafety = async () => {
    const formId = pendingFormId;
    if (!formId) {
      alert("No mix form selected.");
      return;
    }

    const payload = gatherSafetyPayload();

    try {
      const res = await postSafety(payload);
      if (res.ok) {
        const form = document.getElementById(formId);
        if (!form) {
          alert("Mix form not found on page.");
          return;
        }
        if (modalInstance) modalInstance.hide();
        form.submit();
      } else {
        const data = await res.json().catch(() => null);
        alert((data && data.message) || "Safety protocols failed.");
      }
    } catch (e) {
      console.error(e);
      alert("Network error confirming safety.");
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    const checks = getChecks();
    const btn = getConfirmBtn();
    if (!checks.length || !btn) return;

    checks.forEach((c) => {
      c.addEventListener("change", () => {
        const allChecked = checks.every((x) => x.checked);
        btn.disabled = !allChecked;
      });
    });

    btn.addEventListener("click", () => {
      window.confirmSafety();
    });

    const el = getModalEl();
    if (el) {
      el.addEventListener("hidden.bs.modal", () => {
        pendingFormId = null;
        resetModal();
      });
    }
  });
})();

