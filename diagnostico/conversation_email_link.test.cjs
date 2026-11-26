// Comprueba el manejador real del modal sin navegador, red ni base de datos.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname,
  '../leadmanager/apps/prospects/templates/prospects/conversations.html'), 'utf8');
const start = template.indexOf('$(document).ready(function() {');
const source = template.slice(start, template.indexOf('</script>', start))
  .replace("{% url 'app_prospects:conversation' 0 %}", '/leadmanager-admin/conversations/conversation/0/');

function harness(search = '') {
  const calls = [];
  const handlers = {};
  const document = { body: { style: { overflow: 'auto' } }, getElementById: () => ({ focus() {}, scrollTop: 0 }) };
  const chain = new Proxy({}, { get: (_, name) => name === 'hasClass' ? () => true : () => chain });
  const context = {
    document, window: { location: { search } }, URLSearchParams, console,
    conversationModalTrigger: null, conversationBodyOverflow: '',
    setConversationModalView() {},
    fetch(url) { calls.push(url); return new Promise(() => {}); },
    $(selector) {
      if (selector === document) return { ready: callback => callback() };
      if (selector === '.open-email-history-modal') return { on: (_, callback) => { handlers.click = callback; } };
      if (typeof selector === 'object') return { data: () => selector.id };
      return chain;
    },
  };
  vm.runInNewContext(source, context);
  return { calls, context, handlers };
}

test('el enlace abre el modal sin necesitar una fila en la página actual', () => {
  const { calls, context } = harness('?open_conversation=281');
  assert.deepEqual(calls, ['/leadmanager-admin/conversations/conversation/281/']);
  assert.equal(context.document.body.style.overflow, 'hidden');
});

test('un identificador inválido no inicia ninguna consulta', () => {
  for (const id of ['0', '-1', 'abc', '<script>', '1/../../', '']) {
    assert.deepEqual(harness('?open_conversation=' + encodeURIComponent(id)).calls, []);
  }
});

test('el botón de una fila conserva su comportamiento y su referencia de foco', () => {
  const { calls, context, handlers } = harness();
  const button = { id: 286 };
  handlers.click.call(button, { preventDefault() {}, stopPropagation() {} });
  assert.deepEqual(calls, ['/leadmanager-admin/conversations/conversation/286/']);
  assert.equal(context.conversationModalTrigger, button);
});
