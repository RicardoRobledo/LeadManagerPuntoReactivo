const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Ejecuta el controlador real del interruptor con botones y diálogo simulados.
const source = fs.readFileSync(path.join(__dirname,
    '../leadmanager/apps/prospects/templates/static/agents/agent-prompts.js'), 'utf8');
const start = source.indexOf('    function syncMode()');
const end = source.indexOf('    function syncQualificationMethodology()');
assert.ok(start >= 0 && end > start);

function harness(mode, blockReason = '') {
    function button() {
        return {
            attrs: {}, listeners: {}, dataset: {},
            setAttribute(name, value) { this.attrs[name] = value; },
            addEventListener(name, callback) { this.listeners[name] = callback; },
            click() { this.listeners.click(); },
        };
    }
    const context = {
        inputs: { mode: { value: mode } },
        modeToggle: button(), modeConfirm: button(), modeCancel: button(),
        modeLabel: { textContent: '' },
        modeDescription: { textContent: '' },
        modeDialog: {
            open: false,
            showModal() { this.open = true; },
            close() { this.open = false; },
        },
    };
    context.modeToggle.dataset.modeBlockReason = blockReason;
    vm.runInNewContext(source.slice(start, end) + '\nsyncMode();', context);
    return context;
}

test('encender solicita confirmación y cancelar conserva producción', () => {
    const ui = harness('production');
    ui.modeToggle.click();
    assert.equal(ui.modeDialog.open, true);
    assert.equal(ui.inputs.mode.value, 'production');
    ui.modeCancel.click();
    assert.equal(ui.modeDialog.open, false);
    assert.equal(ui.inputs.mode.value, 'production');
    assert.equal(ui.modeToggle.attrs['aria-checked'], 'false');
});

test('confirmar selecciona pruebas y apagar vuelve a producción', () => {
    const ui = harness('production');
    ui.modeToggle.click();
    ui.modeConfirm.click();
    assert.equal(ui.modeDialog.open, false);
    assert.equal(ui.inputs.mode.value, 'shadow_testing');
    assert.equal(ui.modeToggle.attrs['aria-checked'], 'true');
    ui.modeToggle.click();
    assert.equal(ui.inputs.mode.value, 'production');
    assert.equal(ui.modeToggle.attrs['aria-checked'], 'false');
});

test('cargar un agente de pruebas muestra el interruptor encendido', () => {
    const ui = harness('shadow_testing');
    assert.equal(ui.modeToggle.attrs['aria-checked'], 'true');
    assert.equal(ui.modeLabel.textContent, 'Está en pruebas');
    assert.equal(ui.modeDialog.open, false);
});

test('un agente protegido mantiene el modo y no abre el modal', () => {
    const ui = harness('production', 'Este agente atiende en producción.');
    assert.equal(ui.modeToggle.disabled, true);
    assert.equal(ui.modeDescription.textContent, 'Este agente atiende en producción.');
    ui.modeToggle.click();
    ui.modeConfirm.click();
    assert.equal(ui.modeDialog.open, false);
    assert.equal(ui.inputs.mode.value, 'production');
});
