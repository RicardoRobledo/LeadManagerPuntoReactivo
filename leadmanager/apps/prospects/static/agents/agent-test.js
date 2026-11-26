// Conversación aislada para probar la configuración guardada del agente.
(function () {
    'use strict';

    var root = document.getElementById('agentTest');
    if (!root) return;

    var cases = JSON.parse(document.getElementById('agent-test-cases').textContent);
    var caseButtons = Array.from(root.querySelectorAll('[data-agent-test-case]'));
    var messagesEl = document.getElementById('agentTestMessages');
    var empty = document.getElementById('agentTestEmpty');
    var input = document.getElementById('agentTestInput');
    var sendButton = document.getElementById('agentTestSend');
    var exampleButton = document.getElementById('agentTestExample');
    var silenceButton = document.getElementById('agentTestSilence');
    var resetButton = document.getElementById('agentTestReset');
    var status = document.getElementById('agentTestStatus');
    var interestValue = document.getElementById('agentTestInterestValue');
    var interestDetail = document.getElementById('agentTestInterestDetail');
    var escalationValue = document.getElementById('agentTestEscalationValue');
    var escalationDetail = document.getElementById('agentTestEscalationDetail');
    var trafficValue = document.getElementById('agentTestTrafficValue');
    var csrf = document.querySelector('#agentPromptForm input[name="csrfmiddlewaretoken"]');
    var history = [];
    var selectedCase = null;
    var busy = false;
    var silenceAfter = -1;
    var silencePreview = '';
    var reminderEmail = null;

    function setStatus(message, isError) {
        status.textContent = message;
        status.classList.toggle('text-red-700', !!isError);
        status.classList.toggle('text-amber-800', !isError);
    }

    function setCard(valueNode, detailNode, value, detail, tone) {
        valueNode.textContent = value;
        valueNode.dataset.stateTone = tone || 'neutral';
        detailNode.textContent = detail;
    }

    function resetStatusCards() {
        setCard(interestValue, interestDetail, 'Pendiente', 'Aún no hay respuesta del agente.', 'neutral');
        setCard(escalationValue, escalationDetail, 'Pendiente', 'El agente aún no ha evaluado el caso.', 'neutral');
        trafficValue.textContent = 'Pendiente';
        trafficValue.dataset.stateTone = 'neutral';
    }

    function updateStatusCards(data) {
        var hasInterest = data.interest_detected === true;
        setCard(interestValue, interestDetail, hasInterest ? 'Sí' : 'No', data.interest_detail || '',
            hasInterest ? 'positive' : 'negative');
        var escalated = data.handoff_suggested === true;
        setCard(escalationValue, escalationDetail, escalated ? 'Sí' : 'No',
            escalated ? (data.handoff_reason || 'Pase sugerido a ventas; sin asignación real.')
                : 'El agente recomienda continuar la conversación.',
            escalated ? 'warning' : 'neutral');
        var traffic = {
            verde: ['Verde', 'positive'],
            amarillo: ['Amarillo', 'warning'],
            rojo: ['Rojo', 'negative'],
            gris: ['Gris', 'neutral'],
        }[data.semaforo] || ['Sin clasificar', 'neutral'];
        trafficValue.textContent = traffic[0];
        trafficValue.dataset.stateTone = traffic[1];
    }

    function setBusy(value) {
        busy = value;
        caseButtons.forEach(function (button) { button.disabled = value; });
        [input, sendButton, exampleButton, silenceButton, resetButton].forEach(function (control) {
            control.disabled = value;
        });
        sendButton.textContent = value ? 'Probando…' : 'Probar respuesta ↗';
    }

    function appendMessage(role, content) {
        empty.hidden = true;
        var row = document.createElement('div');
        row.className = role === 'user' ? 'ml-auto max-w-[90%]' : 'mr-auto max-w-[90%]';
        var label = document.createElement('p');
        label.className = 'mb-1 text-xs font-medium text-slate-500';
        label.textContent = role === 'user' ? 'Prospecto' : 'Asistente';
        var bubble = document.createElement('div');
        bubble.className = role === 'user'
            ? 'whitespace-pre-wrap rounded-2xl bg-emerald-50 px-3 py-2.5 text-sm leading-relaxed text-slate-800'
            : 'whitespace-pre-wrap rounded-2xl bg-slate-100 px-3 py-2.5 text-sm leading-relaxed text-slate-800';
        bubble.textContent = content;
        row.appendChild(label);
        row.appendChild(bubble);
        messagesEl.appendChild(row);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return row;
    }

    function resetConversation() {
        history = [];
        silenceAfter = -1;
        silencePreview = '';
        reminderEmail = null;
        resetStatusCards();
        messagesEl.replaceChildren(empty);
        empty.hidden = false;
        input.value = '';
        setStatus('Esta prueba no crea prospectos, no envía mensajes a clientes ni realiza asignaciones.', false);
    }

    async function sendMessage(value) {
        var message = value.trim();
        if (busy) return;
        if (!message) {
            setStatus('Escribe un mensaje o elige un caso para probar.', true);
            input.focus();
            return;
        }
        if (history.length >= 12) {
            setStatus('La prueba llegó al límite de mensajes. Reiníciala para continuar.', true);
            return;
        }
        var next = history.concat({ role: 'user', content: message });
        var row = appendMessage('user', message);
        setBusy(true);
        setStatus('Probando la respuesta del agente…', false);
        try {
            var response = await fetch(root.dataset.testUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf.value,
                    'Accept': 'application/json',
                },
                credentials: 'same-origin',
                body: JSON.stringify({ messages: next }),
            });
            if (response.redirected) throw new Error('La sesión venció. Vuelve a iniciar sesión.');
            var data = await response.json();
            if (!response.ok) throw new Error(data.error || 'No se pudo obtener una respuesta.');
            history = next.concat({ role: 'assistant', content: data.assistant_message });
            appendMessage('assistant', data.reply);
            updateStatusCards(data);
            input.value = '';
            silenceAfter = -1;
            silencePreview = data.silence_preview || '';
            reminderEmail = {
                subject: data.reminder_subject || '¿Te ayudamos a dar seguimiento a tu consulta?',
                body: data.reminder_body || '',
            };
            setStatus(data.handoff_suggested
                ? 'Pase sugerido a ventas. Esta prueba no realiza la asignación.'
                : 'Sin pase a ventas por ahora. Esta prueba no realiza acciones reales.', false);
        } catch (failure) {
            row.remove();
            empty.hidden = messagesEl.children.length > 1;
            input.value = message;
            setStatus(failure.message || 'No se pudo obtener una respuesta.', true);
        } finally {
            setBusy(false);
        }
    }

    caseButtons.forEach(function (button) {
        button.addEventListener('click', function () {
            var testCase = cases.find(function (item) { return item.key === button.dataset.agentTestCase; });
            if (!testCase || busy) return;
            resetConversation();
            selectedCase = testCase;
            caseButtons.forEach(function (item) {
                item.setAttribute('aria-pressed', String(item === button));
            });
            sendMessage(testCase.message);
        });
    });

    sendButton.addEventListener('click', function () { sendMessage(input.value); });
    input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            sendMessage(input.value);
        }
    });
    exampleButton.addEventListener('click', function () {
        input.value = selectedCase ? selectedCase.follow_up :
            'Hola. Buscamos kioscos interactivos para reducir filas. Cantidad: 5; contacto: ana@empresa-ejemplo.com; plazo: este trimestre; presupuesto: por definir; rol: coordino la evaluación; interés: evaluar propuesta; capacidad: podemos revisar inversión.';
        input.focus();
    });
    silenceButton.addEventListener('click', function () {
        if (!history.length || history[history.length - 1].role !== 'assistant') {
            setStatus('Prueba una respuesta antes de simular silencio.', true);
            return;
        }
        if (silenceAfter === history.length) return;
        silenceAfter = history.length;
        var note = document.createElement('p');
        note.className = 'text-center text-xs italic text-slate-500';
        note.textContent = 'El prospecto no respondió.';
        messagesEl.appendChild(note);
        var email = document.createElement('article');
        email.className = 'mr-auto max-w-[95%] rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm leading-relaxed text-slate-800';
        var heading = document.createElement('p');
        heading.className = 'mb-2 text-xs font-semibold uppercase tracking-wide text-amber-800';
        heading.textContent = 'Correo de recordatorio · Simulación inmediata';
        var subject = document.createElement('p');
        subject.className = 'mb-2 font-semibold';
        subject.textContent = 'Asunto: ' + (reminderEmail && reminderEmail.subject || 'Recordatorio');
        var body = document.createElement('p');
        body.className = 'whitespace-pre-wrap';
        body.textContent = reminderEmail && reminderEmail.body || 'No se pudo cargar el texto del recordatorio.';
        email.appendChild(heading);
        email.appendChild(subject);
        email.appendChild(body);
        messagesEl.appendChild(email);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        setStatus('Correo mostrado al instante como simulación. En producción se enviaría tras 48 horas si cumple las condiciones. No se envía correo real. ' + silencePreview, false);
    });
    resetButton.addEventListener('click', function () {
        resetConversation();
        selectedCase = null;
        caseButtons.forEach(function (button) { button.setAttribute('aria-pressed', 'false'); });
        input.focus();
    });
})();
