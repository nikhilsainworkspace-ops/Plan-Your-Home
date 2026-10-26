(() => {
  'use strict';
  const areaInput = document.querySelector('.estimate-assumptions input[readonly]');
  const rateInput = document.getElementById('cost-per-m2');
  const contingencyInput = document.getElementById('contingency-pct');
  const totalNode = document.getElementById('estimate-total');
  const baseNode = document.getElementById('estimate-base');
  const contingencyNode = document.getElementById('estimate-contingency');
  const noteNode = document.getElementById('estimate-total-note');
  const currency = totalNode?.dataset.currency || 'INR';
  if (!areaInput || !rateInput || !contingencyInput || !totalNode) return;
  const symbols = {INR: '₹', USD: '$', GBP: '£', EUR: '€', AUD: 'A$', CAD: 'C$'};
  const formatter = new Intl.NumberFormat(undefined, {maximumFractionDigits: 2});
  const money = (v) => `${symbols[currency] || `${currency} `}${formatter.format(v)}`;
  const calculate = () => {
    const area = Math.max(0, Number(areaInput.value) || 0);
    const rate = Math.max(0, Number(rateInput.value) || 0);
    const pct = Math.max(0, Math.min(100, Number(contingencyInput.value) || 0));
    const base = area * rate;
    const contingency = base * pct / 100;
    totalNode.textContent = money(base + contingency);
    if (baseNode) baseNode.textContent = money(base);
    if (contingencyNode) contingencyNode.textContent = money(contingency);
    if (noteNode) noteNode.textContent = `Based on ${formatter.format(area)} m² and a ${formatter.format(pct)}% contingency.`;
    document.querySelectorAll('.estimate-line').forEach((line) => {
      const pctText = line.querySelector('.estimate-line-label b')?.textContent.replace('%','') || '0';
      const lineAmount = line.querySelector('.estimate-line-amount');
      const percent = Math.max(0, Number(pctText) || 0);
      if (lineAmount) lineAmount.textContent = money(base * percent / 100);
    });
    const form = document.getElementById('estimate-form');
    if (form && form.dataset.preview !== 'ready') {
      form.dataset.preview = 'ready';
      const note = document.createElement('p');
      note.className = 'preview-note';
      note.textContent = 'Live preview uses your unsaved inputs. Save assumptions to persist this estimate.';
      form.append(note);
    }
  };
  rateInput.addEventListener('input', calculate);
  contingencyInput.addEventListener('input', calculate);
  document.getElementById('estimate-form')?.addEventListener('submit', () => {
    const note = document.querySelector('.preview-note');
    if (note) note.textContent = 'Saving estimate assumptions…';
  });
})();
