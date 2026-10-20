'use strict';
const $ = (id) => document.getElementById(id);
const STORE = 'pivx-services-v1';
let state = {session: null, purchases: []};
let catalog;
let refreshing = false;
const busy = new Set();
const offerButtons = new Map();
let sessionPending;
function save() { localStorage.setItem(STORE, JSON.stringify(state)); }
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function node(tag, text, className) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; }
function piv(sat) { return (Number(sat) / 1e8).toLocaleString(undefined, {maximumFractionDigits: 8}); }
function date(timestamp) { return new Date(timestamp * 1000).toLocaleString(undefined, {timeZone: 'UTC'}) + ' UTC'; }
async function api(path, method = 'GET', body, token) {
  if (document.body.dataset.static === 'true') path = './' + path.replace(/^\/+/, '');
  const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})}, ...(body !== undefined ? {body: JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || 'The service is unavailable. Retry using the same purchase.');
  return result;
}
async function session() {
  if (state.session) return state.session;
  if (!sessionPending) sessionPending = api('/v1/sessions', 'POST', {}).then(result => { state.session = result.session_token; save(); return state.session; }).finally(() => { sessionPending = null; });
  return sessionPending;
}
function indicator(simulation) { $('simulation').hidden = !simulation; }
function showAvailability() {
  indicator(catalog.simulation);
  $('status').textContent = catalog.receiver_ready ? 'Receiver ready · shielded payments' : 'Receiver is syncing. Purchases will be available when it is ready.';
  for (const [service, button] of offerButtons) button.disabled = !catalog.receiver_ready || busy.has(service);
}
async function purchase(service, product, button, useCredit = false) {
  if (busy.has(service)) return;
  busy.add(service); button.disabled = true; error('');
  try {
    const token = await session();
    const quote = await api('/v1/quotes', 'POST', {service_id: service, product}, token);
    const pending = {quote_id: quote.quote_id, service, product, use_credit: useCredit, request_id: crypto.randomUUID(), invoice_id: null, call_id: crypto.randomUUID()};
    state.purchases.unshift(pending); save();
    await finishInvoice(pending);
    await refresh();
  } catch (problem) { error(problem.message); renderPurchases(); }
  finally { busy.delete(service); button.disabled = !catalog.receiver_ready; }
}
async function finishInvoice(purchase) {
  const invoice = await api('/v1/invoices', 'POST', {quote_id: purchase.quote_id, request_id: purchase.request_id, use_credit: purchase.use_credit || false}, state.session);
  purchase.invoice_id = invoice.invoice_id; purchase.latest = invoice; save();
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    catalog = await api('/v1/services'); showAvailability();
    for (const purchase of state.purchases) {
      try {
        if (!purchase.invoice_id) await finishInvoice(purchase);
        else purchase.latest = await api('/v1/invoices/' + purchase.invoice_id, 'GET', undefined, state.session);
        purchase.error = null;
      } catch (problem) { purchase.error = problem.message; }
    }
    if (state.session) { const account = await api('/v1/account', 'GET', undefined, state.session); $('credit').textContent = piv(account.available_credit_sat) + ' tPIV available credit'; }
    save(); renderPurchases();
  } catch (problem) { error(problem.message); }
  finally { refreshing = false; }
}
async function consume(purchase, button, newRequest = false) {
  button.disabled = true; error('');
  try {
    if (newRequest) { purchase.call_id = crypto.randomUUID(); save(); }
    const current = await api('/v1/invoices/' + purchase.invoice_id, 'GET', undefined, state.session);
    if (!current.access_token) throw new Error('Access is unavailable. Refresh the invoice to check its status.');
    const result = await api('/v1/services/' + purchase.service + '/call', 'POST', {request_id: purchase.call_id, parameters: {}}, current.access_token);
    purchase.result = result; save(); renderPurchases();
  } catch (problem) { error(problem.message); button.disabled = false; }
}
function renderPurchases() {
  const list = $('invoices'); list.replaceChildren();
  if (!state.purchases.length) { list.append(node('p', 'No invoices in this browser yet.', 'empty')); return; }
  for (const purchase of state.purchases) {
    const section = node('article', undefined, 'invoice');
    const invoice = purchase.latest;
    const title = node('h3', purchase.service === 'analytics' ? 'Chain & governance analytics' : 'Research digest');
    title.append(node('small', invoice?.status?.replaceAll('_', ' ') || 'Creating invoice')); section.append(title);
    if (purchase.error) section.append(node('p', purchase.error, 'error'));
    if (invoice) {
      const details = node('dl');
      const pairs = [['Invoice', invoice.invoice_id], ['Network', 'PIVX testnet6'], ['Amount remaining', piv(invoice.amount_to_send_sat) + ' tPIV'], ['Received', piv(invoice.received_sat) + ' tPIV'], ['Confirmations required', String(invoice.required_confirmations)], ['Pay before', date(invoice.expires_at)]];
      if (!invoice.simulation && !['paid', 'expired', 'review'].includes(invoice.status)) pairs.push(['Shielded address', invoice.address], ['Encrypted memo', invoice.memo]);
      for (const [label, value] of pairs) details.append(node('dt', label), node('dd', value));
      section.append(details);
      if (!invoice.simulation && ['pending', 'partially_paid'].includes(invoice.status)) section.append(node('p', 'Pay this invoice with the dedicated testnet Core wallet tool. Upstream Agent Kit currently uses mainnet.'));
      if (invoice.simulation && ['pending', 'confirming'].includes(invoice.status)) section.append(node('p', 'The local simulator will add a test receipt and advance confirmations.'));
      if (invoice.status === 'paid' && !purchase.result) { const button = node('button', 'Collect JSON result'); button.type = 'button'; button.addEventListener('click', () => consume(purchase, button)); section.append(button); }
      if (purchase.result) { section.append(node('h3', 'Your result'), node('pre', JSON.stringify(purchase.result, null, 2))); }
      if (purchase.result && invoice.product === 'week' && invoice.status === 'paid') { const button = node('button', 'Get latest digest', 'secondary'); button.type = 'button'; button.addEventListener('click', () => consume(purchase, button, true)); section.append(button); }
    }
    list.append(section);
  }
}
async function loadServices() {
  try {
    const raw = localStorage.getItem(STORE);
    if (raw) { const parsed = JSON.parse(raw); if (!Array.isArray(parsed.purchases)) throw new Error('Invalid saved purchases; preserve browser data for recovery.'); state = parsed; }
    catalog = await api('/v1/services'); showAvailability();
    $('policy').textContent = catalog.refund_policy;
    for (const service of catalog.services) {
      const offer = node('section', undefined, 'offer');
      const description = node('div'); description.append(node('h2', service.name), node('p', service.description));
      const controls = node('div', undefined, 'buy'); const select = node('select'); select.setAttribute('aria-label', service.name + ' purchase option');
      for (const product of service.products) { const label = catalog.price_mode === 'fixed_testnet' ? (Number(product.usd) / Number(catalog.reference_rate)).toLocaleString(undefined, {maximumFractionDigits: 8}) + ' tPIV' : '$' + product.usd + ' reference'; const option = node('option', label + (product.id === 'week' ? ' / 7 days' : ' / call')); option.value = product.id; select.append(option); }
      const button = node('button', catalog.simulation ? 'Simulate purchase' : 'Create invoice'); button.type = 'button'; button.disabled = !catalog.receiver_ready;
      const creditLabel = node('label'); const creditInput = node('input'); creditInput.type = 'checkbox'; creditLabel.append(creditInput, node('span', ' Apply available credit'));
      offerButtons.set(service.id, button);
      button.addEventListener('click', () => purchase(service.id, select.value, button, creditInput.checked)); controls.append(select, button, creditLabel); offer.append(description, controls); $('offers').append(offer);
    }
    $('refresh').addEventListener('click', refresh); renderPurchases(); await refresh();
    setInterval(refresh, 10000);
  } catch (problem) { error(problem.message); $('status').textContent = 'Service unavailable. No payment has been requested.'; }
}
async function loadStats() {
  try {
    const data = await api('/stats.json'); indicator(data.simulation);
    $('status').textContent = 'Updated ' + date(data.generated_at) + (data.receiver_ready ? '' : ' · receiver sync paused');
    $('paid-calls').textContent = data.totals.paid_calls.toLocaleString(); $('volume').textContent = piv(data.totals.piv_volume_sat);
    $('payments').textContent = data.totals.payment_count.toLocaleString(); $('integrations').textContent = data.totals.unique_integrations.toLocaleString();
    $('payer-metric').textContent = data.payer_metric; $('volume-metric').textContent = data.volume_metric;
    const days = new Map(data.daily.map(day => [day.day, day]));
    const sequence = [];
    for (let i = 29; i >= 0; i--) { const day = new Date((data.generated_at - i * 86400) * 1000).toISOString().slice(0, 10); sequence.push(days.get(day) || {day, paid_calls: 0, piv_volume_sat: 0, integrations: 0}); }
    const max = Math.max(1, ...sequence.map(day => day.paid_calls));
    $('chart').replaceChildren();
    for (const day of sequence) { const bar = node('div', undefined, 'bar'); bar.style.height = Math.max(1, day.paid_calls / max * 100) + '%'; bar.title = day.day + ': ' + day.paid_calls + ' paid calls'; $('chart').append(bar); }
    $('chart-from').textContent = sequence[0].day; $('chart-to').textContent = sequence.at(-1).day;
    $('daily').replaceChildren();
    for (const day of [...data.daily].reverse()) { const row = node('tr'); for (const value of [day.day, day.paid_calls, piv(day.piv_volume_sat), day.integrations]) row.append(node('td', String(value))); $('daily').append(row); }
    if (!data.daily.length) { const row = node('tr'); const cell = node('td', 'No paid activity recorded yet.'); cell.colSpan = 4; row.append(cell); $('daily').append(row); }
    error('');
  } catch (problem) { error(problem.message); $('status').textContent = 'Statistics unavailable. Totals have not been substituted.'; }
}
if (document.body.dataset.page === 'services') loadServices();
if (document.body.dataset.page === 'stats') { loadStats(); setInterval(loadStats, 60000); }
