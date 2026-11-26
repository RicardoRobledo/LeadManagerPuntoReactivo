// Ejecuta el editor real con un DOM y una red controlados.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const listing = fs.readFileSync(path.join(root,
    'leadmanager/apps/prospects/templates/prospects/agents.html'), 'utf8');
const html = fs.readFileSync(path.join(root,
    'leadmanager/apps/prospects/templates/prospects/agent_configuration.html'), 'utf8');
const source = fs.readFileSync(path.join(root,
    'leadmanager/apps/prospects/templates/static/agents/agent-prompts.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const configuration = {
    name: 'Sofía', enterprise_name: 'Empresa', enterprise_information: 'Información',
    role_information: 'Debes identificarte por tu nombre como asistente comercial.',
    trato: 'tu', estilo_comercial: 'directo_cordial',
    evitar_tecnicismos: true, usar_nombre_prospecto: true, responder_breve: true,
    objective_conversation: 'Comprender la necesidad del prospecto.',
    agrupar_preguntas: true,
    tone: 'Cordial', flujo_conversacion: 'Preguntar', reglas_escalamiento: 'Escalar',
};
const unit = { id: 7, name: 'CONSULTORÍA', description: 'Servicio de consultoría' };
const defaultFlows = [
    { service: 'Otros servicios', questions: ['¿Qué necesita?', '¿En qué plazo?'] },
    { service: 'Kioskos digitales', questions: ['¿Cuántos kioscos?', '¿Qué componentes?'] },
];
const data = (overrides = {}) => ({ name: 'Sofía', prompt: {
    ...configuration, business_units: [unit], service_flows: defaultFlows, ...overrides,
} });
const response = (value, status = 200) => ({
    ok: status < 400, status, redirected: false,
    headers: { get: () => 'application/json' }, json: async () => value,
});

function harness(replies) {
    const elements = {};
    let focused;
    class Element {
        constructor() {
            this.listeners = {}; this.attrs = {}; this.dataset = {};
            this.value = ''; this.children = [];
        }
        addEventListener(event, callback) { this.listeners[event] = callback; }
        fire(event, extra = {}) {
            const e = { preventDefault() { this.defaultPrevented = true; }, ...extra };
            return this.listeners[event]?.(e) || e;
        }
        setAttribute(name, value) { this.attrs[name] = value; }
        getAttribute(name) { return this.attrs[name]; }
        removeAttribute(name) { delete this.attrs[name]; }
        focus() { focused = this; }
        appendChild(child) { this.children.push(child); child.parent = this; }
        remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
        set innerHTML(value) { this.children = []; }
        querySelectorAll() { return this.children; }
    }
    for (const match of html.matchAll(/<\w+\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
        const el = elements[match[2]] = new Element();
        el.hidden = /\bhidden\b/.test(match[1]);
        el.disabled = /\bdisabled\b/.test(match[1]);
    }
    const inputs = {};
    const errors = {};
    for (const key of [...Object.keys(configuration), 'business_units', 'service_flows']) {
        errors[key] = elements['error_' + key] || new Element();
        errors[key].hidden = true;
        if (key !== 'business_units' && key !== 'service_flows') {
            inputs[key] = new Element();
            inputs[key].dataset.promptField = key;
            if (typeof configuration[key] === 'boolean') inputs[key].type = 'checkbox';
        }
    }
    elements.agentBusinessUnitTemplate.content = { firstElementChild: { cloneNode() {
        const row = new Element();
        row.parts = {
            '[data-unit-name]': new Element(),
            '[data-unit-label]': new Element(),
            '[data-unit-description]': new Element(),
            '[data-remove-business-unit]': new Element(),
        };
        row.querySelector = selector => row.parts[selector];
        return row;
    } } };
    elements.agentServiceFlowTemplate.content = { firstElementChild: { cloneNode() {
        const row = new Element();
        row.parts = {
            '[data-service-name]': new Element(),
            '[data-remove-service-flow]': new Element(),
            '[data-add-service-question]': new Element(),
            '[data-service-questions]': new Element(),
        };
        row.querySelector = selector => row.parts[selector];
        return row;
    } } };
    elements.agentServiceQuestionTemplate.content = { firstElementChild: { cloneNode() {
        const row = new Element();
        row.parts = {
            '[data-service-question]': new Element(),
            '[data-remove-service-question]': new Element(),
        };
        row.querySelector = selector => row.parts[selector];
        return row;
    } } };
    const editor = elements.agentPromptEditor;
    editor.dataset.promptUrl = '/agents/prospector/prompts/';
    const tabs = [elements.promptConfigurationTab, elements.promptBusinessUnitsTab];
    tabs.forEach((tab, index) => {
        tab.dataset.promptTab = index ? 'business_units' : 'configuration';
        tab.attrs['aria-controls'] = index ? 'promptBusinessUnitsPanel' : 'promptConfigurationPanel';
    });
    editor.querySelectorAll = selector => ({
        '[data-prompt-tab]': tabs,
        '[data-prompt-field]': Object.values(inputs),
        '[data-prompt-error]': Object.values(errors),
    }[selector]);
    editor.querySelector = selector => errors[selector.match(/="([^"]+)"/)[1]];
    const calls = [];
    const timers = new Map();
    let timerId = 0;
    class FormData extends Map {
        constructor() {
            super();
            this.set('csrfmiddlewaretoken', 'csrf-test');
            if (!elements.agentPromptFields.disabled) {
                for (const [name, input] of Object.entries(inputs)) {
                    if (input.type === 'checkbox') {
                        if (input.checked) this.set(name, 'on');
                    } else this.set(name, input.value);
                }
            }
        }
    }
    const document = { getElementById: id => elements[id], title: 'Configuraciones de Sofía | Lead Manager' };
    vm.runInNewContext(source, {
        document, FormData,
        fetch: (url, options) => {
            calls.push({ url, ...options });
            const reply = replies.shift();
            assert.ok(reply, 'Petición inesperada');
            return typeof reply === 'function' ? reply() : Promise.resolve(reply);
        },
        setTimeout: fn => { timers.set(++timerId, fn); return timerId; },
        clearTimeout: id => timers.delete(id),
    });
    return { elements, editor, document, tabs, calls, inputs, errors,
        submit() { return elements.agentPromptForm.fire('submit'); },
        rows: () => elements.agentBusinessUnits.children,
        flows: () => elements.agentServiceFlows.children,
        focused: () => focused,
    };
}

test('carga los campos y las unidades conservando cambios al alternar pestañas', async () => {
    const app = harness([response(data())]);
    await flush();
    assert.equal(app.calls[0].url, '/agents/prospector/prompts/');
    assert.equal(app.elements.agentPromptSave.disabled, false);
    assert.equal(app.inputs.enterprise_name.value, 'Empresa');
    assert.equal(app.inputs.trato.value, 'tu');
    assert.equal(app.inputs.estilo_comercial.value, 'directo_cordial');
    assert.equal(app.inputs.objective_conversation.value, configuration.objective_conversation);
    assert.equal(app.flows().length, 2);
    assert.equal(app.flows()[0].querySelector('[data-service-name]').value, 'Otros servicios');
    assert.equal(app.flows()[1].querySelector('[data-service-questions]').children[0].querySelector('[data-service-question]').value, '¿Cuántos kioscos?');
    assert.equal(app.inputs.agrupar_preguntas.checked, true);
    for (const key of ['evitar_tecnicismos', 'usar_nombre_prospecto', 'responder_breve']) {
        assert.equal(app.inputs[key].checked, true);
    }
    assert.equal(app.rows()[0].querySelector('[data-unit-name]').value, unit.name);
    app.inputs.tone.value = 'Edición sin guardar';
    app.tabs[1].fire('click');
    assert.equal(app.elements.promptConfigurationPanel.hidden, true);
    assert.equal(app.elements.promptBusinessUnitsPanel.hidden, false);
    app.tabs[0].fire('keydown', { key: 'ArrowRight' });
    assert.equal(app.focused(), app.tabs[1]);
    app.tabs[0].fire('click');
    assert.equal(app.inputs.tone.value, 'Edición sin guardar');
});

test('carga una preferencia desactivada sin marcar su casilla', async () => {
    const app = harness([response(data({ responder_breve: false, tone: '' }))]);
    await flush();
    assert.equal(app.inputs.responder_breve.checked, false);
    assert.equal(app.inputs.evitar_tecnicismos.checked, true);
    assert.equal(app.inputs.tone.value, '');
});

test('agrega y quita unidades, normaliza nombres y envía la configuración con CSRF', async () => {
    let complete;
    const app = harness([response(data()), () => new Promise(resolve => { complete = resolve; })]);
    await flush();
    app.rows()[0].querySelector('[data-remove-business-unit]').fire('click');
    assert.equal(app.elements.agentBusinessUnitsEmpty.hidden, false);
    app.elements.agentBusinessUnitAdd.fire('click');
    const row = app.rows()[0];
    const name = row.querySelector('[data-unit-name]');
    name.value = '  mi servicio  ';
    name.fire('blur');
    assert.equal(name.value, 'MI SERVICIO');
    row.querySelector('[data-unit-description]').value = 'Descripción propia';
    app.inputs.name.value = 'Andrea';
    app.inputs.trato.value = 'usted';
    app.inputs.estilo_comercial.value = 'formal';
    app.inputs.evitar_tecnicismos.checked = false;
    app.inputs.agrupar_preguntas.checked = false;
    const save = app.submit();
    assert.equal(app.elements.agentPromptSave.disabled, true);
    await app.submit();
    assert.equal(app.calls.length, 2);
    const body = app.calls[1].body;
    assert.deepEqual(JSON.parse(body.get('business_units')), [
        { id: null, name: 'MI SERVICIO', description: 'Descripción propia' },
    ]);
    assert.deepEqual(JSON.parse(body.get('service_flows')), defaultFlows);
    for (const key of Object.keys(configuration)) {
        if (typeof configuration[key] === 'boolean') {
            assert.equal(body.has(key), app.inputs[key].checked);
        } else assert.equal(body.get(key), app.inputs[key].value);
    }
    assert.equal(body.get('csrfmiddlewaretoken'), 'csrf-test');
    assert.equal(app.calls[1].credentials, 'same-origin');
    complete(response({ name: 'Andrea', message: 'Cambios guardados', prompt: data({
        name: 'Andrea', business_units: [{ id: 19, name: 'MI SERVICIO', description: 'Descripción propia' }],
    }).prompt }));
    await save;
    assert.equal(app.elements.agentPromptToast.hidden, false);
    assert.equal(app.elements.agentPromptTitle.textContent, 'Configuraciones · Andrea');
    assert.equal(app.document.title, 'Configuraciones de Andrea | Lead Manager');
    assert.equal(app.rows()[0].dataset.unitId, '19');
});

test('agrega y quita servicios y preguntas y envía el flujo resultante', async () => {
    const app = harness([response(data()), response({ message: 'Guardado', prompt: data().prompt })]);
    await flush();
    app.flows()[1].querySelector('[data-remove-service-flow]').fire('click');
    assert.equal(app.flows().length, 1);
    const first = app.flows()[0];
    first.querySelector('[data-add-service-question]').fire('click');
    const questions = first.querySelector('[data-service-questions]').children;
    questions[2].querySelector('[data-service-question]').value = '¿Cuál es el presupuesto?';
    questions[0].querySelector('[data-remove-service-question]').fire('click');
    app.elements.agentServiceFlowAdd.fire('click');
    const added = app.flows()[1];
    added.querySelector('[data-service-name]').value = 'Odoo';
    added.querySelector('[data-service-questions]').children[0].querySelector('[data-service-question]').value = '¿Qué módulos necesitan?';
    await app.submit();
    assert.deepEqual(JSON.parse(app.calls[1].body.get('service_flows')), [
        { service: 'Otros servicios', questions: ['¿En qué plazo?', '¿Cuál es el presupuesto?'] },
        { service: 'Odoo', questions: ['¿Qué módulos necesitan?'] },
    ]);
});

test('un servicio incompleto muestra un error y enfoca el campo pendiente', async () => {
    const app = harness([response(data())]);
    await flush();
    app.elements.agentServiceFlowAdd.fire('click');
    const row = app.flows()[2];
    await app.submit();
    assert.equal(app.calls.length, 1);
    assert.equal(app.errors.service_flows.hidden, false);
    assert.equal(app.focused(), row.querySelector('[data-service-name]'));
    row.querySelector('[data-service-name]').value = 'Odoo';
    await app.submit();
    assert.equal(app.focused(), row.querySelector('[data-service-questions]').children[0].querySelector('[data-service-question]'));
});

test('limita la lista a cincuenta servicios y veinte preguntas por servicio', async () => {
    const app = harness([response(data())]);
    await flush();
    for (let i = 0; i < 55; i++) app.elements.agentServiceFlowAdd.fire('click');
    assert.equal(app.flows().length, 50);
    assert.equal(app.elements.agentServiceFlowAdd.disabled, true);
    const row = app.flows()[0];
    for (let i = 0; i < 25; i++) row.querySelector('[data-add-service-question]').fire('click');
    assert.equal(row.querySelector('[data-service-questions]').children.length, 20);
    assert.equal(row.querySelector('[data-add-service-question]').disabled, true);
});

test('mantiene identificadores al editar unidades existentes y limita filas a cincuenta', async () => {
    const app = harness([response(data()), response({ message: 'Guardado', prompt: data().prompt })]);
    await flush();
    app.rows()[0].querySelector('[data-unit-description]').value = 'Editada';
    for (let i = 0; i < 55; i++) app.elements.agentBusinessUnitAdd.fire('click');
    assert.equal(app.rows().length, 50);
    assert.equal(app.elements.agentBusinessUnitAdd.disabled, true);
    await app.submit();
    const saved = JSON.parse(app.calls[1].body.get('business_units'));
    assert.deepEqual(saved[0], { id: 7, name: unit.name, description: 'Editada' });
});

test('la unidad predeterminada es de solo lectura y no se puede quitar', async () => {
    const defaultUnit = { id: 8, name: 'NO_DETERMINADO', description: 'Sin unidad identificada' };
    const app = harness([response(data({ business_units: [unit, defaultUnit] })), response({
        message: 'Guardado', prompt: data({ business_units: [unit, defaultUnit] }).prompt,
    })]);
    await flush();
    const row = app.rows()[1];
    assert.equal(row.querySelector('[data-unit-name]').readOnly, true);
    assert.equal(row.querySelector('[data-unit-label]').textContent, 'Unidad predeterminada · Solo lectura');
    assert.equal(row.querySelector('[data-unit-description]').readOnly, true);
    assert.equal(row.querySelector('[data-remove-business-unit]').hidden, true);
    row.querySelector('[data-remove-business-unit]').fire('click');
    assert.equal(app.rows().length, 2);
    for (let i = 0; i < 55; i++) app.elements.agentBusinessUnitAdd.fire('click');
    assert.equal(app.rows().length, 51);
    await app.submit();
    const saved = JSON.parse(app.calls[1].body.get('business_units'));
    assert.deepEqual(saved[1], defaultUnit);
});

test('un error abre la pestaña correspondiente y conserva los campos para corregirlos', async () => {
    for (const field of ['tone', 'service_flows', 'business_units']) {
        const app = harness([response(data()), response({ errors: {
            [field]: [{ message: 'Revisa este dato.' }],
        } }, 400)]);
        await flush();
        app.inputs.tone.value = 'Texto a corregir';
        await app.submit();
        assert.equal(app.elements.agentPromptSave.disabled, false);
        assert.equal(app.errors[field].hidden, false);
        assert.equal(app.errors[field].textContent, 'Revisa este dato.');
        assert.equal(app.elements.agentPromptError.hidden, false);
        assert.equal(app.inputs.tone.value, 'Texto a corregir');
        assert.equal(app.elements.agentPromptSave.disabled, false);
        assert.equal(app.focused(), app.inputs[field] || app.elements[field === 'service_flows' ? 'agentServiceFlowAdd' : 'agentBusinessUnitAdd']);
        assert.equal(app.elements[field === 'business_units' ? 'promptBusinessUnitsPanel' : 'promptConfigurationPanel'].hidden, false);
    }
});

test('un fallo al cargar mantiene el guardado deshabilitado', async () => {
    const app = harness([response({}, 500)]);
    await flush();
    assert.equal(app.elements.agentPromptSave.disabled, true);
    assert.equal(app.elements.agentPromptFields.disabled, true);
    assert.equal(app.elements.agentPromptError.hidden, false);
    await app.submit();
    assert.equal(app.calls.length, 1);
});

test('el formulario permanece deshabilitado mientras se carga la página', async () => {
    let complete;
    const app = harness([() => new Promise(resolve => { complete = resolve; })]);
    assert.equal(app.elements.agentPromptFields.disabled, true);
    assert.equal(app.elements.agentPromptSave.disabled, true);
    complete(response(data()));
    await flush();
    assert.equal(app.inputs.name.value, 'Sofía');
    assert.equal(app.elements.agentPromptFields.disabled, false);
    assert.equal(app.elements.agentPromptSave.disabled, false);
});

test('los avisos usan hidden como atributo para que se puedan mostrar', () => {
    for (const id of ['agentPromptToast', 'agentPromptError', 'error_business_units']) {
        const tag = html.match(new RegExp('<[^>]*id="' + id + '"[^>]*>'))[0];
        const classes = tag.match(/class="([^"]*)"/)[1].split(/\s+/);
        assert.ok(!classes.includes('hidden'), id);
    }
});

test('la tarjeta abre la pantalla de configuración con sidebar', () => {
    assert.match(listing, /app_prospects:agent_configuration/);
    assert.doesNotMatch(listing, /agentPromptModal|data-edit-agent-prompt/);
    assert.match(html, /{% extends 'prospects\/base_dashboard.html' %}/);
    assert.match(html, /id="agentPromptEditor"/);
    assert.doesNotMatch(html, /<dialog/);
});
