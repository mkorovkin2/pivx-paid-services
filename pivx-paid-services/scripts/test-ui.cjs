'use strict';
// Behavior tests without a browser or TCP listener. Browser rendering remains a separate check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../paid_services/static/app.js'), 'utf8');

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.style = {}; this.dataset = {}; this.hidden = false; this.value = ''; this.textContent = ''; }
  append(...children) { this.children.push(...children); if (this.tagName === 'select' && !this.value && children[0]) this.value = children[0].value; }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(key, value) { this[key] = value; }
}
const services = {simulation: true, receiver_ready: true, refund_policy: 'Test policy', services: [
  {id: 'analytics', name: 'Analytics', description: 'Network stats', products: [{id: 'single', usd: '0.05'}]},
  {id: 'digest', name: 'Digest', description: 'Sources', products: [{id: 'single', usd: '0.25'}, {id: 'week', usd: '2.00'}]},
]};
async function harness(page, responder, saved) {
  const elements = new Map(); const storage = new Map(saved ? [['pivx-services-v1', JSON.stringify(saved)]] : []);
  const calls = [];
  const context = vm.createContext({
    document: {body: {dataset: {page}}, getElementById(id) { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); }, createElement: tag => new Element(tag)},
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    crypto: {randomUUID: (() => { let i = 0; return () => 'request-' + (++i); })()},
    setInterval() {}, console,
    fetch: async (url, options) => { calls.push({url, options}); const result = await responder(url, options); return {ok: !result.error, json: async () => result}; },
  });
  vm.runInContext(source, context);
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(setImmediate); };
  await flush();
  return {context, elements, storage, calls, flush, run: expression => vm.runInContext(expression, context)};
}

test('catalog renders two services and simulation indicator', async () => {
  const h = await harness('services', async () => services);
  assert.equal(h.elements.get('offers').children.length, 2);
  assert.equal(h.elements.get('simulation').hidden, false);
  assert.match(h.elements.get('status').textContent, /Receiver ready/);
});
test('receiver readiness recovers after initial sync', async () => {
  let ready = false;
  const h = await harness('services', async () => ({...services, receiver_ready: ready}));
  const button = h.elements.get('offers').children[0].children[1].children[1];
  assert.equal(button.disabled, true);
  ready = true;
  await h.run('refresh()');
  assert.equal(button.disabled, false);
});
test('concurrent session creation produces one private session', async () => {
  const h = await harness('services', async url => url === '/v1/sessions' ? {session_token: 'PRIVATE'} : services);
  await Promise.all([h.run('session()'), h.run('session()')]);
  assert.equal(h.calls.filter(call => call.url === '/v1/sessions').length, 1);
  assert.equal(JSON.parse(h.storage.get('pivx-services-v1')).session, 'PRIVATE');
});
test('lost invoice response retries with the same quote and request ID', async () => {
  let lost = true;
  const h = await harness('services', async (url, options) => {
    if (url === '/v1/services') return services;
    if (url === '/v1/sessions') return {session_token: 'PRIVATE'};
    if (url === '/v1/quotes') return {quote_id: 'QUOTE'};
    if (url === '/v1/account') return {available_credit_sat: 0};
    if (url === '/v1/invoices') {
      if (lost) { lost = false; return {error: {message: 'Interrupted response'}}; }
      return {invoice_id: 'INVOICE', status: 'pending', simulation: true, amount_to_send_sat: 5, received_sat: 0, required_confirmations: 1, expires_at: 1800000000};
    }
    throw new Error('Unexpected route ' + url);
  });
  await h.run('purchase("analytics", "single", offerButtons.get("analytics"))');
  assert.match(h.elements.get('error').textContent, /Interrupted/);
  await h.run('refresh()');
  const posts = h.calls.filter(call => call.url === '/v1/invoices');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].options.body, posts[1].options.body);
  assert.equal(h.calls.filter(call => call.url === '/v1/quotes').length, 1);
  assert.equal(JSON.parse(h.storage.get('pivx-services-v1')).purchases[0].invoice_id, 'INVOICE');
});
test('source text is inserted as text, without HTML execution', async () => {
  const h = await harness('services', async () => ({...services, services: [{...services.services[0], name: '<img onerror=attack()>', description: '<script>attack()</script>'}]}));
  const description = h.elements.get('offers').children[0].children[0];
  assert.equal(description.children[0].textContent, '<img onerror=attack()>');
  assert.equal(description.children[1].textContent, '<script>attack()</script>');
  assert.equal(description.children[0].children.length, 0);
});
test('stats render genuine zero totals and a 30-day chart', async () => {
  const h = await harness('stats', async () => ({simulation: true, receiver_ready: true, generated_at: 1800000000,
    totals: {paid_calls: 0, payment_count: 0, unique_integrations: 0, piv_volume_sat: 0}, daily: [], payer_metric: 'Sessions, not wallets.', volume_metric: 'Confirmed receipts.'}));
  assert.equal(h.elements.get('paid-calls').textContent, '0');
  assert.equal(h.elements.get('chart').children.length, 30);
  assert.match(h.elements.get('daily').children[0].children[0].textContent, /No paid activity/);
});
test('failed statistics do not substitute fake totals', async () => {
  const h = await harness('stats', async () => { throw new Error('Offline'); });
  assert.match(h.elements.get('error').textContent, /Offline/);
  assert.equal(h.elements.has('paid-calls'), false);
});
test('GitHub Pages export fetches stats relative to its project directory', async () => {
  const stats = {simulation: true, receiver_ready: false, generated_at: 1800000000,
    totals: {paid_calls: 0, payment_count: 0, unique_integrations: 0, piv_volume_sat: 0}, daily: [], payer_metric: 'Sessions.', volume_metric: 'Receipts.'};
  const h = await harness('stats', async () => stats);
  h.context.document.body.dataset.static = 'true';
  await h.run('loadStats()');
  assert.equal(h.calls.at(-1).url, './stats.json');
});
test('fixed testnet catalog displays tPIV prices rather than dollar amounts', async () => {
  const h = await harness('services', async () => ({...services, price_mode: 'fixed_testnet', reference_rate: '1'}));
  const select = h.elements.get('offers').children[0].children[1].children[0];
  assert.equal(select.children[0].textContent, '0.05 tPIV / call');
});
