(() => {
  const form = document.getElementById('stashGuideForm');
  if (!form) return;
  const update = () => {
    const selected = [...form.querySelectorAll('[name="flavour"]:checked')];
    let total = 0;
    for (const item of form.querySelectorAll('[name="flavour"]')) {
      const input = form.elements.namedItem(`pct_${item.value}`);
      input.required = item.checked;
      if (item.checked) total += Number(input.value) || 0;
    }
    document.getElementById('guideTotal').textContent = `${selected.length} ingredients selected · ${total.toFixed(2)}% total flavour. Review the balance before continuing.`;
    form.querySelector('button').disabled = selected.length < 1 || selected.length > 4 || total <= 0 || total >= 100;
  };
  form.addEventListener('input', update);
  update();
})();
