/**
 * Guided Mix scale reading input helpers (parse, sanity check, focus behaviour).
 * Loaded by tvw_guided_mix_session.html; also require()-able in Node for tests.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.GuidedScaleInput = factory();
  }
})(typeof self !== "undefined" ? self : this, function guidedScaleInputFactory() {
  "use strict";

  var SCALE_SANITY_MESSAGE = "Scale reading looks too high for this step. Check the decimal point.";

  function normalizeScaleRaw(raw) {
    return String(raw == null ? "" : raw).trim().replace(/\s+/g, "").replace(/,/g, ".");
  }

  function isPartialScaleEntry(raw) {
    var trimmed = String(raw == null ? "" : raw).trim();
    if (trimmed === "" || trimmed === "." || trimmed === "," || trimmed === "-") {
      return true;
    }
    if (/^\d+[.,]$/.test(trimmed)) {
      return true;
    }
    if (/^[.,]\d*$/.test(trimmed)) {
      return true;
    }
    return false;
  }

  function parseScaleGrams(raw) {
    if (isPartialScaleEntry(raw)) {
      return null;
    }
    var normalized = normalizeScaleRaw(raw);
    if (normalized === "" || normalized === "-") {
      return null;
    }
    if (!/^-?\d+(\.\d+)?$/.test(normalized) && !/^-?\.\d+$/.test(normalized)) {
      return null;
    }
    var n = Number(normalized);
    return Number.isFinite(n) ? n : null;
  }

  /** Parse for live readout while typing; does not require a finished decimal. */
  function parseScaleGramsForReadout(raw) {
    var normalized = normalizeScaleRaw(raw);
    if (normalized === "" || normalized === "." || normalized === "-") {
      return null;
    }
    if (!/^-?(\d+\.?\d*|\.\d*)$/.test(normalized)) {
      return null;
    }
    if (isPartialScaleEntry(raw)) {
      if (normalized === "." || /^\d+\.$/.test(normalized)) {
        var whole = normalized.replace(/\.$/, "");
        if (whole === "" || whole === "-") {
          return null;
        }
        var w = Number(whole);
        return Number.isFinite(w) ? w : null;
      }
    }
    var n = Number(normalized);
    return Number.isFinite(n) ? n : null;
  }

  function formatScaleGrams(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return "0.00";
    return n.toFixed(2);
  }

  function isSeedScaleValue(raw) {
    var normalized = normalizeScaleRaw(raw);
    return normalized === "" || normalized === "0" || /^0\.0*$/.test(normalized);
  }

  /**
   * Flag likely decimal-point mistakes (e.g. 780 g when target is 0.78 g).
   * Skips partial in-progress entries like "0." or "0,3".
   */
  function isScaleReadingSuspicious(parsedGrams, targetGrams, raw) {
    if (raw != null && isPartialScaleEntry(raw)) {
      return false;
    }
    var parsed = Number(parsedGrams);
    var target = Number(targetGrams);
    if (!Number.isFinite(parsed) || !Number.isFinite(target) || target <= 0) {
      return false;
    }
    if (parsed <= 0) return false;
    if (parsed / target >= 50) return true;
    if (target < 5 && parsed >= 10 && parsed / target >= 10) return true;
    return false;
  }

  function createScaleInputState() {
    return {
      focused: false,
      dirty: false,
      selectedOnce: false,
    };
  }

  function selectAllOrEnd(input) {
    if (!input) return;
    try {
      input.focus({ preventScroll: true });
    } catch (_err) {
      input.focus();
    }
    var len = String(input.value || "").length;
    try {
      input.select();
    } catch (_selectErr) {
      /* ignore */
    }
    if (len > 0 && input.selectionStart === input.selectionEnd) {
      try {
        input.setSelectionRange(len, len);
      } catch (_rangeErr) {
        /* ignore */
      }
    }
  }

  /**
   * @param {HTMLInputElement} input
   * @param {{ onValueChange?: function(number|null, string, object): void, state?: object, autoFocus?: boolean }} opts
   * @returns {{ getState: function(): object, resetState: function(): void }}
   */
  function attachScaleInput(input, opts) {
    if (!input) {
      return {
        getState: function () {
          return createScaleInputState();
        },
        resetState: function () {},
      };
    }
    var options = opts || {};
    var onValueChange = typeof options.onValueChange === "function" ? options.onValueChange : function () {};
    var state = options.state && typeof options.state === "object" ? options.state : createScaleInputState();

    function emit() {
      onValueChange(parseScaleGramsForReadout(input.value), input.value, state);
    }

    input.addEventListener("focus", function () {
      state.focused = true;
      if (!state.selectedOnce && !state.dirty) {
        requestAnimationFrame(function () {
          if (document.activeElement !== input || state.dirty) {
            return;
          }
          selectAllOrEnd(input);
          state.selectedOnce = true;
        });
      }
    });

    input.addEventListener("blur", function () {
      state.focused = false;
      state.dirty = false;
      state.selectedOnce = false;
      var parsed = parseScaleGrams(input.value);
      if (parsed !== null && !isPartialScaleEntry(input.value)) {
        input.value = formatScaleGrams(parsed);
      }
      emit();
    });

    input.addEventListener("input", function () {
      state.dirty = true;
      state.selectedOnce = true;
      emit();
    });

    if (options.autoFocus !== false) {
      requestAnimationFrame(function () {
        if (document.activeElement === input) {
          return;
        }
        input.focus();
      });
    }

    emit();

    return {
      getState: function () {
        return state;
      },
      resetState: function () {
        state.focused = false;
        state.dirty = false;
        state.selectedOnce = false;
      },
    };
  }

  /**
   * Simulate typing after a single select-all on focus (for regression tests).
   * Re-selecting on later keystrokes would produce wrong values like "3" for "0." + "3".
   */
  function simulateTypingAfterSelectAll(initialValue, keystrokes) {
    var value = String(initialValue == null ? "" : initialValue);
    var selectedAll = true;
    var keys = keystrokes || [];
    for (var i = 0; i < keys.length; i += 1) {
      var ch = String(keys[i]);
      if (selectedAll) {
        value = ch;
        selectedAll = false;
      } else {
        value += ch;
      }
    }
    return value;
  }

  return {
    SCALE_SANITY_MESSAGE: SCALE_SANITY_MESSAGE,
    normalizeScaleRaw: normalizeScaleRaw,
    isPartialScaleEntry: isPartialScaleEntry,
    parseScaleGrams: parseScaleGrams,
    parseScaleGramsForReadout: parseScaleGramsForReadout,
    formatScaleGrams: formatScaleGrams,
    isSeedScaleValue: isSeedScaleValue,
    isScaleReadingSuspicious: isScaleReadingSuspicious,
    createScaleInputState: createScaleInputState,
    selectAllOrEnd: selectAllOrEnd,
    attachScaleInput: attachScaleInput,
    simulateTypingAfterSelectAll: simulateTypingAfterSelectAll,
  };
});
