const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname,
    '../leadmanager/apps/prospects/templates/prospects/conversations.html'), 'utf8');
const source = html.slice(html.indexOf('function getProspectingSummary('),
    html.indexOf('function parseInitialMessage('));
const context = vm.createContext({});
vm.runInContext(source, context);
const summarize = crm => context.getProspectingSummary(crm);

test('muestra el motivo real de escalamiento y prioriza la empresa reconocida sobre el correo personal', () => {
    const reason = 'Cuenta de alto valor: empresa reconocida, aunque usa correo personal.';
    const result = summarize({
        escalar_a_humano: true,
        semaforo: 'gris',
        motivo_escalamiento: reason,
        motivo_semaforo: 'Correo no corporativo.',
    });
    assert.equal(result.status, 'Escalado');
    assert.equal(result.reason, reason);
    assert.equal(result.tone, 'bg-green-500');
});

test('amarillo describe una prospección en curso y conserva su explicación', () => {
    const result = summarize({
        escalar_a_humano: false, semaforo: 'amarillo',
        motivo_semaforo: 'Pregunta por precios sin compartir datos de su proyecto.',
    });
    assert.equal(result.status, 'En proceso de prospección');
    assert.equal(result.reason, 'Pregunta por precios sin compartir datos de su proyecto.');
});

test('rojo explica los datos insuficientes o contradictorios sin escalar por una puntuación alta', () => {
    const result = summarize({semaforo: 'rojo', score_comercial: 100});
    assert.equal(result.status, 'No escalado');
    assert.match(result.reason, /informaci.*contradictorios/);
});

test('gris explica que terminó con correo personal', () => {
    const result = summarize({semaforo: 'gris', escalar_a_humano: 'false'});
    assert.equal(result.status, 'No escalado');
    assert.match(result.reason, /correo personal o no corporativo/);
});

test('verde no afirma que hubo un escalamiento si no está registrado', () => {
    const result = summarize({semaforo: 'verde', escalar_a_humano: false});
    assert.equal(result.status, 'Pendiente de escalamiento');
    assert.match(result.reason, /no se ha confirmado/);
});

test('reconoce flags históricos y escalamientos existentes', () => {
    assert.equal(summarize({escalar_a_humano: ' true '}).status, 'Escalado');
    assert.equal(summarize({is_qualified: true, escalar_a_humano: false}).status, 'Escalado');
});

test('distingue el final del flujo, la prospección activa y los registros sin evaluación', () => {
    assert.equal(summarize({conversation_state: 'enrutamiento'}).status, 'No escalado');
    assert.equal(summarize({conversation_state: 'calificacion'}).status, 'En proceso de prospección');
    assert.equal(summarize({}).status, 'Evaluación pendiente');
});

test('normaliza el color, ignora motivos vacíos y no modifica el análisis guardado', () => {
    const crm = {semaforo: ' AMARILLO ', motivo_semaforo: '   '};
    const original = JSON.stringify(crm);
    const result = summarize(crm);
    assert.equal(result.status, 'En proceso de prospección');
    assert.ok(result.reason.trim());
    assert.equal(JSON.stringify(crm), original);
});

test('la explicación comercial aparece debajo de la puntuación y antes del contacto', () => {
    assert.ok(html.indexOf('id="crmScore"') < html.indexOf('id="crmProspectingStatus"'));
    assert.ok(html.indexOf('id="crmProspectingReason"') < html.indexOf('id="crmContactoNombre"'));
    assert.ok(!html.includes('id="crmSemaforo"'));
    assert.ok(!html.includes('Motivo del semáforo'));
});
