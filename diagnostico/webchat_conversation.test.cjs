// Ejecuta el JavaScript real del iframe con DOM, red y reloj controlados.
// No conecta con Celery, Odoo ni la base de datos.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname,
    '../leadmanager/apps/pages/templates/webchat/chat.html'), 'utf8');
const source = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).find(script => script.includes('var API_URL'));

function harness({ stored = {}, replies = [], search = '?utm_source=google', configuration } = {}) {
    class Element {
        constructor() {
            this.children = [];
            this.listeners = {};
            this.attrs = {};
            this.style = {};
            this.value = '';
            this.hidden = false;
            this.className = '';
            this.classList = { add() {}, toggle() {} };
        }
        appendChild(child) { child.parent = this; this.children.push(child); }
        addEventListener(event, callback) { this.listeners[event] = callback; }
        fire(event) { this.listeners[event]?.({ preventDefault() {} }); }
        setAttribute(key, value) { this.attrs[key] = value; }
        getAttribute(key) { return this.attrs[key]; }
        removeAttribute(key) { delete this.attrs[key]; }
        focus() {}
        scrollTo() {}
        remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
        querySelectorAll() { return this.children; }
        querySelector() { return this.children[0]; }
        set innerHTML(value) { this.html = value; this.children = []; }
        get innerHTML() { return this.html || ''; }
    }
    const elements = {};
    for (const match of html.matchAll(/<\w+\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
        elements[match[2]] = new Element();
        elements[match[2]].hidden = /\bhidden\b/.test(match[1]);
    }
    const footer = new Element();
    if (configuration) {
        elements['webchat-configuration'] = new Element();
        elements['webchat-configuration'].textContent = JSON.stringify(configuration);
    }
    for (const channel of ['whatsapp', 'correo']) {
        const chip = new Element();
        chip.setAttribute('data-channel', channel);
        elements.channelGate.appendChild(chip);
    }
    const storage = new Map(Object.entries(stored));
    const calls = [];
    const timers = new Map();
    let nextTimer = 0;
    const window = {
        self: {}, top: {},
        parent: { postMessage() {} },
        location: { search, pathname: '/chat/' },
        history: { replaceState() {} },
        localStorage: {
            getItem: key => storage.get(key),
            setItem: (key, value) => storage.set(key, value),
            removeItem: key => storage.delete(key),
        },
        addEventListener() {},
    };
    const document = {
        body: new Element(), title: 'Chat',
        getElementById(id) {
            if (id === 'typing-indicator') {
                return elements.messages.children.find(child => child.id === id);
            }
            return elements[id];
        },
        querySelector: () => footer,
        createElement: () => new Element(),
    };
    vm.runInNewContext(source, {
        window, document, URLSearchParams, console,
        requestAnimationFrame: callback => callback(),
        setTimeout: (callback, delay) => {
            const id = ++nextTimer;
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout: id => timers.delete(id),
        fetch: (url, options = {}) => {
            calls.push({ url, ...options });
            const reply = replies.shift();
            assert.ok(reply, `Petición inesperada: ${url}`);
            if (typeof reply === 'function') return reply();
            return Promise.resolve({ ok: true, json: () => Promise.resolve(reply) });
        },
    });
    return {
        elements, footer, calls, timers, storage,
        tick() {
            assert.equal(timers.size, 1, 'Debe existir un solo temporizador');
            const [id, timer] = [...timers][0];
            assert.equal(timer.delay, 7000, 'El polling debe esperar siete segundos');
            timers.delete(id);
            timer.callback();
        },
        texts() {
            return elements.messages.children.filter(child => child.id !== 'typing-indicator')
                .map(child => child.children[0].children[0])
                .map(bubble => bubble.textContent || bubble.innerHTML);
        },
        register(channel = 'correo') {
            elements.unitOptions.children.find(child => child.textContent === 'Desarrollo Web').fire('click');
            elements.userBrief.value = 'Quiero una tienda';
            elements.briefGate.fire('submit');
            elements.userName.value = 'Ana';
            elements.userEmail.value = 'ana@example.com';
            elements.userCompany.value = 'Acme';
            elements.infoGate.fire('submit');
            elements.channelGate.children[channel === 'correo' ? 1 : 0].fire('click');
            if (channel === 'whatsapp') {
                elements.userPhone.value = '3312345678';
                elements.phoneGate.fire('submit');
            }
        },
        send(text) {
            elements.userInput.value = text;
            elements.chatForm.fire('submit');
        },
    };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const message = (id, role, content) => ({ id, role, content, created_at: '2026-10-16T18:00:00Z' });

test('el menú muestra solo las opciones de este chat aunque el agente tenga otras unidades', async () => {
    const app = harness({ configuration: {
        name: 'Andrea', enterprise_name: 'Mi empresa', business_units: ['CONSULTORÍA', 'SOPORTE', 'NO_DETERMINADO'],
    }, replies: [{ reply: 'Cuéntame más' }] });
    assert.deepEqual(app.elements.unitOptions.children.map(child => child.textContent), [
        'Branding e Identidad de Marca', 'Rediseño de Branding Evolutivo',
        'Arquitectura de Marca', 'Employer Branding y Comunicación', 'Desarrollo Web',
        'Publicidad Digital', 'Gestión de Redes Sociales', 'Email Marketing',
        'Gestión de Marketplaces', 'AEO/GEO Y SEO', 'Consultoría en Marketing',
        'Auditoría de Marca y Comunicación', 'Consultoría de Innovación y Diseño',
        'No estoy seguro',
    ]);
    app.register();
    await flush();
    const payload = JSON.parse(app.calls[0].body);
    assert.equal(payload.business_unit, 'Desarrollo Web');
    assert.ok(app.texts().some(text => text.includes('Andrea')));
    assert.ok(!app.texts().some(text => text.includes('Sofía')));
});

test('las ligas del widget reconocen las unidades del frontend y sus alias', () => {
    for (const [unit, label] of [
        [' BRANDING E IDENTIDAD DE MARCA ', 'Branding e Identidad de Marca'],
        ['REDISEÑO_DE_BRANDING_EVOLUTIVO', 'Rediseño de Branding Evolutivo'],
        ['ARQUITECTURA DE MARCA', 'Arquitectura de Marca'],
        ['EMPLOYER BRANDING Y COMUNICACIÓN', 'Employer Branding y Comunicación'],
        ['DESARROLLO WEB', 'Desarrollo Web'],
        ['ECOMMERCE', 'Desarrollo Web'],
        ['Sitio web/e-commerce', 'Desarrollo Web'],
        ['PUBLICIDAD DIGITAL', 'Publicidad Digital'],
        ['GESTIÓN DE REDES SOCIALES', 'Gestión de Redes Sociales'],
        ['EMAIL MARKETING', 'Email Marketing'],
        ['GESTIÓN_DE_MARKETPLACES', 'Gestión de Marketplaces'],
        ['AEO/GEO Y SEO', 'AEO/GEO Y SEO'],
        ['SEO', 'AEO/GEO Y SEO'],
        ['GEO', 'AEO/GEO Y SEO'],
        ['CONSULTORÍA EN MARKETING', 'Consultoría en Marketing'],
        ['AUDITORÍA DE MARCA Y COMUNICACIÓN', 'Auditoría de Marca y Comunicación'],
        ['CONSULTORÍA DE INNOVACIÓN Y DISEÑO', 'Consultoría de Innovación y Diseño'],
    ]) {
        const app = harness({ search: '?unit=' + encodeURIComponent(unit) });
        assert.equal(app.elements.unitGate.hidden, true, unit);
        assert.equal(app.elements.briefGate.hidden, false, unit);
        assert.equal(app.storage.get('estrasol_chat_unit'), label, unit);
    }
});
test('permite escribir tras cada respuesta aunque el CRM marque la conversación cerrada', async () => {
    const app = harness({ replies: [
        { session_id: 'session-test', after_id: 0 },
        { status: 'processing', messages: [], last_id: 0 },
        { status: 'ready', messages: [message(1, 'user', 'Quiero una tienda'),
            message(2, 'assistant', '¿Cuántos productos tienes?')], last_id: 2,
        conversation_closed: true },
        { session_id: 'session-test', after_id: 2 },
        { status: 'ready', messages: [message(3, 'user', 'Tenemos cien productos'),
            message(4, 'assistant', '¿Para cuándo la necesitas?')], last_id: 4,
        conversation_closed: true },
    ] });
    app.register();
    await flush();
    assert.equal(app.elements.chatForm.hidden, false);
    assert.equal(app.elements.userInput.disabled, true);
    assert.equal(app.calls.length, 1);
    app.tick();
    await flush();
    assert.equal(app.elements.userInput.disabled, true);
    app.tick();
    await flush();
    assert.equal(app.elements.userInput.disabled, false);
    assert.equal(app.elements.chatForm.hidden, false);
    assert.equal(app.footer.hidden, false);
    assert.equal(app.texts().filter(text => text === 'Quiero una tienda').length, 1);
    assert.equal(app.texts().filter(text => text === '¿Cuántos productos tienes?').length, 1);
    app.send('Tenemos cien productos');
    await flush();
    const payloads = app.calls.filter(call => call.method === 'POST')
        .map(call => JSON.parse(call.body));
    assert.equal(payloads[1].session_id, payloads[0].session_id);
    assert.equal(payloads[1].preferred_contact_method, 'email');
    assert.equal(payloads[1].utm_source, 'google');
    app.tick();
    await flush();
    assert.ok(app.calls.at(-1).url.endsWith('&after_id=2'));
    assert.equal(app.texts().filter(text => text === 'Tenemos cien productos').length, 1);
    assert.ok(app.texts().includes('¿Para cuándo la necesitas?'));
    assert.equal(app.footer.hidden, false);
    assert.equal(app.timers.size, 0);
});

test('WhatsApp conserva teléfono y preferencia en los mensajes siguientes', async () => {
    const app = harness({ replies: [
        { reply: '¿Cuántos necesitas?' }, { reply: '¿Para cuándo?' },
    ] });
    app.register('whatsapp');
    await flush();
    app.send('Cinco');
    await flush();
    const payload = JSON.parse(app.calls.at(-1).body);
    assert.equal(payload.phone, '3312345678');
    assert.equal(payload.preferred_contact_method, 'phone');
});

test('recargar reconstruye el historial y espera el turno pendiente sin reenviarlo', async () => {
    const app = harness({ stored: {
        estrasol_chat_session: 'session-test',
        estrasol_chat_email: 'ana@example.com',
        estrasol_chat_name: 'Ana',
        estrasol_chat_company: 'Acme',
        estrasol_chat_unit: 'Sitio web/e-commerce',
        estrasol_chat_brief: 'Quiero una tienda',
        estrasol_chat_channel: 'correo',
        estrasol_chat_sent: '1',
        estrasol_chat_finished: '1',
    }, replies: [
        { status: 'processing', messages: [message(1, 'user', 'Quiero una tienda'),
            message(2, 'assistant', '¿Cuántos productos?')], last_id: 2 },
        { status: 'ready', messages: [message(3, 'user', 'Cien productos'),
            message(4, 'assistant', 'Un asesor te contactará.')], last_id: 4,
        conversation_closed: true },
    ] });
    await flush();
    assert.equal(app.calls.length, 1);
    assert.equal(app.calls[0].method, undefined);
    app.tick();
    await flush();
    assert.equal(app.texts().length, 4);
    assert.ok(app.texts().includes('Cien productos'));
    assert.equal(app.footer.hidden, false);
    assert.equal(app.elements.chatForm.hidden, false);
    assert.equal(app.elements.userInput.disabled, false);
    assert.equal(app.timers.size, 0);
});

test('borrar durante el envío descarta respuestas tardías y cancela el polling', async () => {
    let complete;
    const app = harness({ replies: [() => new Promise(resolve => { complete = resolve; })] });
    app.register();
    app.elements.resetBtn.fire('click');
    complete({ ok: true, json: () => Promise.resolve({ session_id: 'old', after_id: 0 }) });
    await flush();
    assert.equal(app.timers.size, 0);
    assert.equal(app.elements.unitGate.hidden, false);
    assert.equal(app.texts().length, 1);
});

test('una consulta fallida se reintenta a los 7 s sin volver a enviar el mensaje', async () => {
    const app = harness({ replies: [
        { session_id: 'session-test', after_id: 0 },
        () => Promise.reject(new Error('Sin conexión temporalmente')),
        { status: 'ready', messages: [message(1, 'assistant', 'Ya estoy aquí')], last_id: 1 },
    ] });
    app.register();
    await flush();
    app.tick();
    await flush();
    assert.equal(app.elements.userInput.disabled, true);
    app.send('No debe enviarse mientras espera');
    app.tick();
    await flush();
    assert.equal(app.calls.filter(call => call.method === 'POST').length, 1);
    assert.equal(app.elements.userInput.disabled, false);
    assert.ok(app.texts().includes('Ya estoy aquí'));
});

const registeredSession = {
    estrasol_chat_session: 'session-test',
    estrasol_chat_email: 'ana@example.com',
    estrasol_chat_name: 'Ana',
    estrasol_chat_company: 'Acme',
    estrasol_chat_unit: 'Sitio web/e-commerce',
    estrasol_chat_brief: 'Quiero una tienda',
    estrasol_chat_channel: 'correo',
    estrasol_chat_sent: '1',
};

test('el noveno envío espera la respuesta final y después muestra el límite sin enviar un décimo', async () => {
    const app = harness({ stored: { ...registeredSession, estrasol_chat_message_count: '8' }, replies: [
        { status: 'idle', messages: [], message_count: 8, message_limit: 9 },
        { status: 'accepted', session_id: 'session-test', after_id: 0,
            message_count: 9, message_limit: 9, message_limit_reached: true },
        { status: 'processing', messages: [], last_id: 0,
            message_count: 9, message_limit_reached: true },
        { status: 'ready', messages: [message(9, 'assistant', 'Esta es la última respuesta')],
            last_id: 9, message_count: 9, message_limit_reached: true },
    ] });
    await flush();
    assert.equal(app.elements.userInput.disabled, false);
    assert.equal(app.elements.chatForm.hidden, false);
    assert.equal(app.elements.messageCounter.textContent, 'Mensajes enviados: 8/9');
    app.send('Mi noveno mensaje');
    await flush();
    assert.equal(app.elements.messageLimitNotice.hidden, true);
    assert.equal(app.elements.chatForm.hidden, false);
    assert.equal(app.elements.userInput.disabled, true);
    app.tick();
    await flush();
    assert.equal(app.elements.messageLimitNotice.hidden, true);
    app.tick();
    await flush();
    assert.ok(app.texts().includes('Esta es la última respuesta'));
    assert.equal(app.elements.userInput.disabled, true);
    assert.equal(app.elements.sendBtn.disabled, true);
    assert.equal(app.elements.chatForm.hidden, true);
    assert.equal(app.elements.messageLimitNotice.hidden, false);
    assert.equal(app.elements.messageCounter.textContent, 'Has alcanzado el límite de mensajes permitidos.');
    assert.equal(app.storage.get('estrasol_chat_message_count'), '9');
    assert.equal(app.timers.size, 0);
    app.elements.userInput.disabled = false;
    app.elements.sendBtn.disabled = false;
    app.send('Intento de mensaje diez');
    assert.equal(app.calls.filter(call => call.method === 'POST').length, 1);
    assert.equal(app.elements.userInput.disabled, true);
});

test('recargar recupera la última respuesta y el bloqueo aunque se haya borrado el contador local', async () => {
    const app = harness({ stored: registeredSession, replies: [
        { status: 'ready', messages: [message(9, 'user', 'Noveno mensaje'),
            message(10, 'assistant', 'Respuesta final')],
            message_count: 9, message_limit: 9, message_limit_reached: true },
    ] });
    await flush();
    assert.equal(app.elements.userInput.disabled, true);
    assert.equal(app.elements.sendBtn.disabled, true);
    assert.equal(app.elements.chatForm.hidden, true);
    assert.equal(app.elements.messageLimitNotice.hidden, false);
    assert.equal(app.elements.messageCounter.textContent, 'Has alcanzado el límite de mensajes permitidos.');
    assert.equal(app.timers.size, 0);
    assert.ok(app.texts().includes('Respuesta final'));
    app.send('No debe salir');
    assert.equal(app.calls.length, 1);
});

test('recargar con nueve mensajes sigue esperando al bot antes de mostrar el aviso', async () => {
    const app = harness({ stored: { ...registeredSession, estrasol_chat_message_count: '9' }, replies: [
        { status: 'processing', messages: [], last_id: 0,
            message_count: 9, message_limit_reached: true },
        { status: 'ready', messages: [message(10, 'assistant', 'Terminó la respuesta pendiente')],
            last_id: 10, message_count: 9, message_limit_reached: true },
    ] });
    await flush();
    assert.equal(app.elements.messageLimitNotice.hidden, true);
    assert.equal(app.elements.userInput.disabled, true);
    app.tick();
    await flush();
    assert.ok(app.texts().includes('Terminó la respuesta pendiente'));
    assert.equal(app.elements.messageLimitNotice.hidden, false);
    assert.equal(app.elements.chatForm.hidden, true);
    assert.equal(app.calls.filter(call => call.method === 'POST').length, 0);
});

test('una respuesta directa al noveno mensaje también se muestra antes del bloqueo', async () => {
    const app = harness({ stored: registeredSession, replies: [
        { status: 'idle', messages: [], message_count: 8, message_limit: 9 },
        { reply: 'Respuesta final directa', message_count: 9, message_limit_reached: true },
    ] });
    await flush();
    app.send('Noveno mensaje');
    await flush();
    assert.ok(app.texts().includes('Respuesta final directa'));
    assert.equal(app.elements.messageLimitNotice.hidden, false);
    assert.equal(app.elements.chatForm.hidden, true);
    assert.equal(app.timers.size, 0);
});

test('si falla la última respuesta se muestra el error y termina la espera', async () => {
    const app = harness({ stored: { ...registeredSession, estrasol_chat_message_count: '9' }, replies: [
        { status: 'failed', messages: [], message_count: 9, message_limit_reached: true },
    ] });
    await flush();
    assert.ok(app.texts().some(text => text.includes('No pude')));
    assert.equal(app.elements.messageLimitNotice.hidden, false);
    assert.equal(app.elements.userInput.disabled, true);
    assert.equal(app.timers.size, 0);
});

test('reiniciar desde el aviso crea otra sesión y permite el primer mensaje', async () => {
    const app = harness({ stored: { ...registeredSession, estrasol_chat_message_count: '9' }, replies: [
        { status: 'idle', messages: [], message_count: 9, message_limit_reached: true },
        { reply: 'Bienvenido de nuevo', message_count: 1, message_limit_reached: false },
    ] });
    await flush();
    app.elements.restartChatBtn.fire('click');
    assert.equal(app.elements.messageLimitNotice.hidden, true);
    assert.equal(app.elements.userInput.disabled, false);
    assert.equal(app.elements.sendBtn.disabled, false);
    assert.notEqual(app.storage.get('estrasol_chat_session'), 'session-test');
    assert.equal(app.storage.has('estrasol_chat_message_count'), false);
    app.register();
    await flush();
    const payload = JSON.parse(app.calls.at(-1).body);
    assert.notEqual(payload.session_id, 'session-test');
    assert.equal(app.elements.userInput.disabled, false);
    assert.equal(app.elements.chatForm.hidden, false);
    assert.equal(app.elements.messageCounter.textContent, 'Mensajes enviados: 1/9');
});

test('un rechazo del backend bloquea otra pestaña con un contador local atrasado', async () => {
    const app = harness({ stored: registeredSession, replies: [
        { status: 'idle', messages: [], message_count: 8, message_limit: 9 },
        () => Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({
            code: 'message_limit_reached', message_count: 9, message_limit_reached: true,
        }) }),
    ] });
    await flush();
    app.send('Ya se agotó en otra pestaña');
    await flush();
    assert.equal(app.elements.userInput.disabled, true);
    assert.equal(app.elements.sendBtn.disabled, true);
    assert.equal(app.elements.messageLimitNotice.hidden, false);
    assert.equal(app.timers.size, 0);
});

test('el polling detecta el límite alcanzado desde otra pestaña y no desbloquea el chat', async () => {
    const app = harness({ replies: [
        { session_id: 'session-test', after_id: 0, message_count: 1 },
        { status: 'idle', messages: [], message_count: 9, message_limit_reached: true },
    ] });
    app.register();
    await flush();
    app.tick();
    await flush();
    assert.equal(app.elements.userInput.disabled, true);
    assert.equal(app.elements.sendBtn.disabled, true);
    assert.equal(app.elements.messageLimitNotice.hidden, false);
    assert.equal(app.timers.size, 0);
});
