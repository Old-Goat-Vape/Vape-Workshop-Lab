/* Exact-ingredient usage suggestions. Isolated so async input behaviour can be tested. */
((root) => {
  class FlavourUsage {
    static steepPlan(ingredients) {
      const selected = ingredients.filter(i => Number(i.percentage) > 0);
      const supported = selected.map(i => (i.knowledge?.steep_sources || []).filter(
        s => s.kind === 'steep_days' && Number.isFinite(s.high) && s.high >= 0 && s.high <= 365));
      const sources = supported.flat();
      return {days:sources.length ? Math.ceil(Math.max(...sources.map(s => s.high))) : null,
        covered:supported.filter(s => s.length).length, total:selected.length};
    }
    constructor(row, fetchUsage, onChange, onKnowledge = () => {}) {
      this.row = row;
      this.pct = row.querySelector('.ingredient-pct');
      this.id = row.querySelector('.ingredient-id');
      this.label = row.querySelector('.row-usage-text');
      this.button = row.querySelector('.row-use-average');
      this.fetchUsage = fetchUsage;
      this.onChange = onChange;
      this.onKnowledge = onKnowledge;
      this.sequence = 0;
      this.editRevision = 0;
      this.selectedId = 0;
      this.automaticValue = null;
      this.suggestion = null;
      this.pct.addEventListener('input', () => {
        this.editRevision++;
        this.automaticValue = null;
      });
      this.button.addEventListener('click', () => {
        if (this.suggestion == null || Number(this.id.value) !== this.selectedId) return;
        this.pct.value = this.suggestion;
        this.automaticValue = this.pct.value;
        this.editRevision++;
        this.onChange();
      });
    }

    async select(ingredientId, {autofill = true} = {}) {
      const id = Number(ingredientId || 0);
      if (id === this.selectedId) return;
      const sequence = ++this.sequence;
      this.selectedId = id;
      this.onKnowledge(this.row, null);
      // Remove only a value supplied by the previous flavour's suggestion.
      if (this.automaticValue !== null && this.pct.value === this.automaticValue) {
        this.pct.value = '';
        this.onChange();
      }
      this.automaticValue = null;
      this.suggestion = null;
      this.button.hidden = true;
      this.label.textContent = id ? 'Checking average usage…' : '';
      if (!id) return;
      const revision = this.editRevision;
      try {
        const usage = await this.fetchUsage(id);
        if (sequence !== this.sequence || !this.row.isConnected || Number(this.id.value) !== id) return;
        this.onKnowledge(this.row, usage.knowledge || null);
        const average = usage.average;
        const count = usage.count;
        if (!Number.isFinite(average) || average <= 0 || average > 100 || !Number.isInteger(count) || count <= 0) {
          this.label.textContent = 'No recipe usage data yet.';
          return;
        }
        // Ordinary percentages use two decimals; retain tiny positive values instead of rounding to zero.
        const value = String(average >= 0.01 ? Number(average.toFixed(2)) : Number(average.toPrecision(2)));
        this.suggestion = value;
        this.label.textContent = `Average usage: ${value}% across ${count} recipe${count === 1 ? '' : 's'}.${count < 5 ? ' Small sample.' : ''}`;
        this.button.textContent = `Use ${value}%`;
        this.button.hidden = false;
        // Never replace saved values or something typed while the request was in flight.
        if (autofill && this.pct.value.trim() === '' && revision === this.editRevision) {
          this.pct.value = value;
          this.automaticValue = this.pct.value;
          this.onChange();
        }
      } catch (_) {
        if (sequence === this.sequence && this.row.isConnected && Number(this.id.value) === id) {
          this.label.textContent = 'Average unavailable. Enter your own percentage.';
        }
      }
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = FlavourUsage;
  else root.TVWFlavourUsage = FlavourUsage;
})(typeof window !== 'undefined' ? window : {});
