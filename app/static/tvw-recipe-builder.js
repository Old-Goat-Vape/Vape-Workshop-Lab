(() => {
  const cfg = window.TVW_RECIPE_BUILDER || {};
  const rowsWrap = document.getElementById("ingredientRows");
  const rowTemplate = document.getElementById("ingredientRowTemplate");
  const form = document.getElementById("recipeBuilderForm");
  const saveBtn = document.getElementById("saveRecipeBtn");
  const saveMeta = document.getElementById("saveMeta");
  const calcStatus = document.getElementById("calcStatus");
  const policyBox = document.getElementById("policyBox");
  const ratioField = document.getElementById("targetRatioField");
  const byLabel = new Map();
  const byId = new Map();
  let activeAcWrap = null;
  let searchTimer = null;
  let searchSeq = 0;
  let calcTimer = null;
  let lastHardStops = [];
  let submitting = false;

  const mkLabel = (f) => f.display_label || `${f.brand || "Unknown"} - ${f.name || `Flavor #${f.id}`} (#${f.id})`;
  const registerFlavor = (f) => {
    if (!f || !f.id) return;
    const id = Number(f.id);
    byId.set(id, f);
    byLabel.set(mkLabel(f).toLowerCase(), id);
  };
  for (const f of cfg.flavourCatalog || []) registerFlavor(f);

  const num = (v, d = 0) => {
    const x = Number(v);
    return Number.isFinite(x) ? x : d;
  };
  const fmt = (v) => num(v, 0).toFixed(2);

  const syncHiddenCalc = () => {
    document.getElementById("calcTargetMlHidden").value = document.getElementById("calcTargetMl").value;
    document.getElementById("calcTargetNicHidden").value = document.getElementById("calcTargetNic").value;
    document.getElementById("calcPgRatioHidden").value = document.getElementById("calcPgRatio").value;
    document.getElementById("calcVgRatioHidden").value = document.getElementById("calcVgRatio").value;
    document.getElementById("calcNicBaseHidden").value = document.getElementById("calcNicBase").value;
    document.getElementById("calcNicCarrierHidden").value = document.getElementById("calcNicCarrier").value;
  };

  const queueCalc = () => {
    if (calcTimer) clearTimeout(calcTimer);
    calcTimer = setTimeout(runCalc, 120);
  };

  const parseFlavorId = (raw) => {
    const txt = String(raw || "").trim();
    if (!txt) return 0;
    const m = txt.match(/\(#(\d+)\)\s*$/);
    if (m) return Number(m[1]);
    return Number(byLabel.get(txt.toLowerCase()) || 0);
  };

  const closeFlavorMenu = (wrap) => {
    if (!wrap) return;
    const menu = wrap.querySelector(".tvw-flavor-ac-menu");
    const input = wrap.querySelector(".flavor-input");
    if (menu) {
      menu.classList.add("d-none");
      menu.innerHTML = "";
    }
    if (input) input.setAttribute("aria-expanded", "false");
    if (activeAcWrap === wrap) activeAcWrap = null;
  };

  const updateRowBrand = (row, flavour) => {
    const badge = row.querySelector(".row-brand");
    const nameEl = row.querySelector(".ingredient-name");
    const brandEl = row.querySelector(".ingredient-brand");
    const brand = flavour?.brand || "—";
    badge.textContent = brand || "—";
    if (nameEl) nameEl.value = flavour?.name || "";
    if (brandEl) brandEl.value = flavour?.brand || "";
  };

  const selectFlavor = (wrap, flavour) => {
    const row = wrap.closest(".ingredient-row");
    const input = wrap.querySelector(".flavor-input");
    const idEl = row.querySelector(".ingredient-id");
    registerFlavor(flavour);
    input.value = mkLabel(flavour);
    idEl.value = String(flavour.id);
    updateRowBrand(row, flavour);
    closeFlavorMenu(wrap);
    queueCalc();
  };

  const renderFlavorMenu = (wrap, query, items) => {
    const menu = wrap.querySelector(".tvw-flavor-ac-menu");
    const input = wrap.querySelector(".flavor-input");
    menu.innerHTML = "";
    if (!items.length) {
      menu.innerHTML = `<div class="tvw-flavor-ac-empty">No flavours found for '${String(query).replace(/</g, "&lt;")}'.</div>`;
      menu.classList.remove("d-none");
      input.setAttribute("aria-expanded", "true");
      activeAcWrap = wrap;
      return;
    }
    for (const f of items) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tvw-flavor-ac-item";
      btn.setAttribute("role", "option");
      btn.dataset.id = String(f.id);
      btn.textContent = mkLabel(f);
      btn.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        selectFlavor(wrap, f);
      });
      menu.appendChild(btn);
    }
    menu.classList.remove("d-none");
    input.setAttribute("aria-expanded", "true");
    activeAcWrap = wrap;
  };

  const searchFlavours = async (wrap, query) => {
    const q = String(query || "").trim();
    if (q.length < 1) {
      closeFlavorMenu(wrap);
      return;
    }
    const seq = ++searchSeq;
    try {
      const res = await fetch(`/api/recipes/flavour-search?q=${encodeURIComponent(q)}&limit=100`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const body = await res.json();
      if (seq !== searchSeq) return;
      if (!res.ok) throw new Error(body.message || "Search failed");
      const items = body.items || [];
      for (const f of items) registerFlavor(f);
      renderFlavorMenu(wrap, q, items);
    } catch (e) {
      if (seq !== searchSeq) return;
      const menu = wrap.querySelector(".tvw-flavor-ac-menu");
      menu.innerHTML = `<div class="tvw-flavor-ac-empty">${e.message || "Search failed"}</div>`;
      menu.classList.remove("d-none");
    }
  };

  const onFlavorInput = (wrap, input) => {
    const row = wrap.closest(".ingredient-row");
    const idEl = row.querySelector(".ingredient-id");
    const id = parseFlavorId(input.value);
    idEl.value = id > 0 ? String(id) : "";
    if (id <= 0) updateRowBrand(row, null);
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      activeAcWrap = wrap;
      searchFlavours(wrap, input.value);
    }, 120);
    queueCalc();
  };

  const wireFlavorAutocomplete = (row) => {
    const wrap = row.querySelector(".tvw-flavor-ac");
    const input = row.querySelector(".flavor-input");
    if (!wrap || !input) return;
    input.addEventListener("input", () => onFlavorInput(wrap, input));
    input.addEventListener("focus", () => {
      const q = String(input.value || "").trim();
      if (q.length >= 1) searchFlavours(wrap, q);
    });
    input.addEventListener("blur", () => {
      setTimeout(() => {
        if (!wrap.contains(document.activeElement)) closeFlavorMenu(wrap);
      }, 150);
    });
    input.addEventListener("keydown", (ev) => {
      const menu = wrap.querySelector(".tvw-flavor-ac-menu");
      const items = Array.from(menu.querySelectorAll(".tvw-flavor-ac-item"));
      if (!items.length) return;
      const active = menu.querySelector(".tvw-flavor-ac-item.is-active");
      let idx = active ? items.indexOf(active) : -1;
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        idx = Math.min(items.length - 1, idx + 1);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        idx = Math.max(0, idx - 1);
      } else if (ev.key === "Enter" && idx >= 0) {
        ev.preventDefault();
        const id = Number(items[idx].dataset.id || 0);
        if (id > 0 && byId.has(id)) selectFlavor(wrap, byId.get(id));
        return;
      } else if (ev.key === "Escape") {
        closeFlavorMenu(wrap);
        return;
      } else {
        return;
      }
      items.forEach((el) => el.classList.remove("is-active"));
      if (idx >= 0) items[idx].classList.add("is-active");
    });
  };

  const setRatioFromCalcInputs = () => {
    const pg = Math.max(0, num(document.getElementById("calcPgRatio").value, 30));
    const vg = Math.max(0, num(document.getElementById("calcVgRatio").value, 70));
    const sum = pg + vg;
    const pgNorm = sum > 0 ? Math.round((pg / sum) * 100) : 30;
    const vgNorm = sum > 0 ? Math.max(0, 100 - pgNorm) : 70;
    ratioField.value = `${vgNorm}/${pgNorm}`;
    syncHiddenCalc();
  };

  const initCalcInputsFromSavedRatio = () => {
    const raw = String(cfg.savedRatio || ratioField.value || "").trim();
    const m = raw.match(/^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)$/);
    if (!m) return;
    const vg = Math.max(0, num(m[1], 70));
    const pg = Math.max(0, num(m[2], 30));
    document.getElementById("calcPgRatio").value = String(pg);
    document.getElementById("calcVgRatio").value = String(vg);
    syncHiddenCalc();
  };

  const buildRow = ({ ingredient_id = "", percent = "", name = "", brand = "" } = {}) => {
    rowsWrap.insertAdjacentHTML("beforeend", rowTemplate.innerHTML);
    const row = rowsWrap.lastElementChild;
    const input = row.querySelector(".flavor-input");
    const idEl = row.querySelector(".ingredient-id");
    const pctEl = row.querySelector(".ingredient-pct");
    const idNum = Number(ingredient_id || 0);
    if (idNum > 0 && byId.has(idNum)) {
      const f = byId.get(idNum);
      input.value = mkLabel(f);
      idEl.value = String(idNum);
      updateRowBrand(row, f);
    } else if (name) {
      input.value = name;
      updateRowBrand(row, { name, brand });
    }
    if (percent !== "" && percent != null) pctEl.value = String(percent);
    wireFlavorAutocomplete(row);
    return row;
  };

  const allRows = () => Array.from(rowsWrap.querySelectorAll(".ingredient-row"));
  const collectRows = () =>
    allRows().map((row) => ({
      ingredient_id: num(row.querySelector(".ingredient-id").value, 0),
      percent: num(row.querySelector(".ingredient-pct").value, 0),
      row,
    }));

  const applyRowOutputs = (rows, lineItems) => {
    let idx = 0;
    for (const r of rows) {
      const mlEl = r.row.querySelector(".row-out-ml");
      const gEl = r.row.querySelector(".row-out-g");
      const valid = r.ingredient_id > 0 && r.percent > 0;
      if (!valid) {
        mlEl.textContent = "0.00";
        gEl.textContent = "0.00";
        continue;
      }
      const li = lineItems[idx++];
      mlEl.textContent = fmt(li?.ml);
      gEl.textContent = fmt(li?.grams);
    }
  };

  const setCompositionBar = (pct) => {
    const pg = Math.max(0, num(pct.pg, 0));
    const vg = Math.max(0, num(pct.vg, 0));
    const flv = Math.max(0, num(pct.flavor, 0));
    const nic = Math.max(0, num(pct.nicotine, 0));
    const barPg = document.getElementById("barPg");
    const barVg = document.getElementById("barVg");
    const barFlv = document.getElementById("barFlv");
    const barNic = document.getElementById("barNic");
    barPg.style.width = `${pg}%`;
    barVg.style.width = `${vg}%`;
    barFlv.style.width = `${flv}%`;
    barNic.style.width = `${nic}%`;
    barPg.textContent = pg >= 8 ? `PG ${fmt(pg)}%` : "PG";
    barVg.textContent = vg >= 8 ? `VG ${fmt(vg)}%` : "VG";
    barFlv.textContent = flv >= 8 ? `Flavour ${fmt(flv)}%` : "Flavour";
    barNic.textContent = nic >= 8 ? `Nicotine ${fmt(nic)}%` : "Nicotine";
  };

  const updateSaveState = (hardStops, validRows) => {
    lastHardStops = hardStops || [];
    const pg = num(document.getElementById("calcPgRatio").value, 0);
    const vg = num(document.getElementById("calcVgRatio").value, 0);
    const ratioBad = Math.abs(pg + vg - 100) > 0.01;
    const blocked = lastHardStops.length > 0 || !validRows || ratioBad;
    if (submitting) return;
    saveBtn.disabled = blocked;
    if (blocked) {
      saveBtn.setAttribute("aria-disabled", "true");
      if (lastHardStops.length) {
        saveMeta.textContent = lastHardStops[0];
      } else if (!validRows) {
        saveMeta.textContent = "Add at least one flavour with a percentage.";
      } else if (ratioBad) {
        saveMeta.textContent = "PG and VG must add up to 100%.";
      } else {
        saveMeta.textContent = "Fix validation issues before saving.";
      }
    } else {
      saveBtn.removeAttribute("aria-disabled");
      saveMeta.textContent = "Ready to save.";
    }
  };

  const setTotals = (res) => {
    const t = res?.totals || {};
    document.getElementById("summaryTotalPct").textContent = `${fmt(t.total_flavor_percent)}%`;
    document.getElementById("summaryFlvMl").textContent = fmt(t.flavor_ml);
    document.getElementById("summaryPgMl").textContent = `${fmt(t.pg_ml)} ml`;
    document.getElementById("summaryVgMl").textContent = `${fmt(t.vg_ml)} ml`;
    document.getElementById("summaryTotalMl").textContent = `${fmt(t.total_mix_ml)} ml`;
    setCompositionBar(res?.composition_pct || {});
  };

  const runCalc = async () => {
    const rows = collectRows();
    const validRows = rows.some((r) => r.ingredient_id > 0 && r.percent > 0);
    syncHiddenCalc();
    setRatioFromCalcInputs();
    const payload = {
      target_ml: num(document.getElementById("calcTargetMl").value, 60),
      target_nic_mg_ml: num(document.getElementById("calcTargetNic").value, 3),
      nic_base_mg_ml: num(document.getElementById("calcNicBase").value, 100),
      pg_ratio: num(document.getElementById("calcPgRatio").value, 30),
      vg_ratio: num(document.getElementById("calcVgRatio").value, 70),
      nicotine_carrier: document.getElementById("calcNicCarrier").value || "PG",
      rows: rows.map((r) => ({ ingredient_id: r.ingredient_id, percent: r.percent })),
    };
    calcStatus.textContent = "Calculating…";
    try {
      const response = await fetch("/api/recipes/live-calc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.message || "Calculation failed");
      const result = body.result || {};
      setTotals(result);
      applyRowOutputs(rows, result.line_items || []);
      const hardStops = (result.policy || {}).hard_stops || [];
      window.PolicyFeedback.render(policyBox, result.policy || {}, {
        tone: "tvw",
        warnLead: "Review before saving:",
      });
      calcStatus.textContent = "Recipe summary updated";
      updateSaveState(hardStops, validRows);
    } catch (e) {
      window.PolicyFeedback.clear(policyBox);
      calcStatus.textContent = e.message || "Calculation error";
      updateSaveState([e.message || "Calculation error"], validRows);
    }
  };

  const addFlavour = () => {
    buildRow();
    queueCalc();
  };

  document.getElementById("addFlavourBtn").addEventListener("click", addFlavour);
  document.getElementById("addFlavourBtnBottom").addEventListener("click", addFlavour);

  rowsWrap.addEventListener("click", (e) => {
    if (e.target.classList.contains("remove-row")) {
      const row = e.target.closest(".ingredient-row");
      if (row) row.remove();
      if (!allRows().length) buildRow();
      queueCalc();
    }
  });

  rowsWrap.addEventListener("input", (e) => {
    if (e.target.classList.contains("ingredient-pct")) queueCalc();
  });

  rowsWrap.addEventListener("blur", (e) => {
    const row = e.target.closest(".ingredient-row");
    if (!row || !e.target.classList.contains("flavor-input")) return;
    const id = parseFlavorId(e.target.value);
    row.querySelector(".ingredient-id").value = id > 0 ? String(id) : "";
    if (id > 0 && byId.has(id)) {
      const f = byId.get(id);
      e.target.value = mkLabel(f);
      updateRowBrand(row, f);
    }
    queueCalc();
  }, true);

  ["calcTargetMl", "calcTargetNic", "calcPgRatio", "calcVgRatio", "calcNicBase", "calcNicCarrier"].forEach((id) => {
    const el = document.getElementById(id);
    el.addEventListener("input", queueCalc);
    el.addEventListener("change", queueCalc);
  });

  form.addEventListener("submit", (e) => {
    syncHiddenCalc();
    if (saveBtn.disabled || lastHardStops.length) {
      e.preventDefault();
      return;
    }
    if (submitting) {
      e.preventDefault();
      return;
    }
    submitting = true;
    saveBtn.disabled = true;
    saveBtn.textContent = cfg.mode === "edit" ? "Saving…" : "Creating recipe…";
  });

  const initialRows = cfg.initialRows || [];
  if (initialRows.length) {
    initialRows.forEach((x) => buildRow(x));
  } else {
    buildRow();
  }
  initCalcInputsFromSavedRatio();
  setRatioFromCalcInputs();
  queueCalc();

  if (cfg.hasErrors) {
    const summary = document.getElementById("formErrorSummary");
    if (summary) summary.focus();
  }
})();
