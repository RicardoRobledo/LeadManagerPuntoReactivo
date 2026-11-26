// Editor de configuraciones en la página del agente.
(function () {
    'use strict';

    var editor = document.getElementById('agentPromptEditor');
    if (!editor) return;

    var form = document.getElementById('agentPromptForm');
    var fields = document.getElementById('agentPromptFields');
    var saveButton = document.getElementById('agentPromptSave');
    var loading = document.getElementById('agentPromptLoading');
    var error = document.getElementById('agentPromptError');
    var toast = document.getElementById('agentPromptToast');
    var steps = Array.from(editor.querySelectorAll('[data-prompt-step]'));
    var inputs = {};
    editor.querySelectorAll('[data-prompt-field]').forEach(function (input) {
        inputs[input.dataset.promptField] = input;
    });
    var methodologyData = document.getElementById('agentQualificationMethodologies');
    var methodologies = methodologyData ? JSON.parse(methodologyData.textContent) : {};
    var methodologyOrderBlock = document.getElementById('agentQualificationOrderBlock');
    var methodologyOrder = document.getElementById('agentQualificationOrder');
    var methodologyDescription = document.getElementById('agentQualificationDescription');
    var customMethodology = document.getElementById('agentCustomMethodology');
    var sliders = Array.from(editor.querySelectorAll('[data-prompt-slider]'));
    var unitList = document.getElementById('agentBusinessUnits');
    var unitTemplate = document.getElementById('agentBusinessUnitTemplate');
    var addUnitButton = document.getElementById('agentBusinessUnitAdd');
    var unitsEmpty = document.getElementById('agentBusinessUnitsEmpty');
    var serviceList = document.getElementById('agentServiceFlows');
    var serviceTemplate = document.getElementById('agentServiceFlowTemplate');
    var questionTemplate = document.getElementById('agentServiceQuestionTemplate');
    var addServiceButton = document.getElementById('agentServiceFlowAdd');
    var servicesEmpty = document.getElementById('agentServiceFlowsEmpty');
    var escalationRuleList = document.getElementById('agentEscalationRules');
    var addEscalationRuleButton = document.getElementById('agentEscalationRuleAdd');
    var reminderList = editor.querySelector('[data-reminder-rows]');
    var modeToggle = document.getElementById('agentModeToggle');
    var modeLabel = document.getElementById('agentModeLabel');
    var modeDialog = document.getElementById('agentModeDialog');
    var modeConfirm = document.getElementById('agentModeConfirm');
    var modeCancel = document.getElementById('agentModeCancel');
    var modeDescription = document.getElementById('agentModeDescription');
    var saving = false;
    var toastTimer = null;
    form.action = editor.dataset.promptUrl;

    function syncMode() {
        if (!modeToggle || !inputs.mode) return;
        var shadow = inputs.mode.value === 'shadow_testing';
        modeToggle.setAttribute('aria-checked', String(shadow));
        modeToggle.disabled = !!modeToggle.dataset.modeBlockReason;
        modeDescription.textContent = modeToggle.dataset.modeBlockReason || 'Recibe una copia de los mensajes de sus canales y genera respuestas internas sin interrumpir la atención a los clientes.';
        modeLabel.textContent = shadow ? 'Está en pruebas' : 'No está en pruebas';
    }

    if (modeToggle && inputs.mode && modeDialog) {
        modeToggle.addEventListener('click', function () {
            if (modeToggle.disabled) return;
            if (inputs.mode.value === 'shadow_testing') {
                inputs.mode.value = 'production';
                syncMode();
            } else {
                modeDialog.showModal();
            }
        });
        modeConfirm.addEventListener('click', function () {
            if (modeToggle.disabled) return;
            inputs.mode.value = 'shadow_testing';
            syncMode();
            modeDialog.close();
        });
        modeCancel.addEventListener('click', function () { modeDialog.close(); });
    }

    function syncQualificationMethodology() {
        if (!inputs.qualification_method || !methodologyDescription) return;
        var selected = inputs.qualification_method.value;
        var methodology = methodologies[selected] || methodologies.basic;
        if (!methodology) return;
        methodologyOrder.textContent = methodology.order || '';
        methodologyOrderBlock.hidden = !methodology.order;
        methodologyDescription.textContent = methodology.description;
        customMethodology.hidden = selected !== 'custom';
        if (inputs.custom_methodology) inputs.custom_methodology.required = selected === 'custom';
    }

    function updateUnitList() {
        var rows = Array.from(unitList.querySelectorAll('[data-business-unit-row]'));
        var count = rows.filter(function (row) { return row.dataset.defaultUnit !== 'true'; }).length;
        unitsEmpty.hidden = rows.length > 0;
        addUnitButton.disabled = saving || count >= 50;
    }

    function addUnit(unit) {
        var row = unitTemplate.content.firstElementChild.cloneNode(true);
        row.dataset.unitId = unit.id == null ? '' : String(unit.id);
        var isDefault = unit.name === 'NO_DETERMINADO';
        row.dataset.defaultUnit = String(isDefault);
        var name = row.querySelector('[data-unit-name]');
        name.value = unit.name || '';
        name.readOnly = isDefault;
        row.querySelector('[data-unit-label]').textContent = isDefault ? 'Unidad predeterminada · Solo lectura' : 'Unidad de negocio';
        var description = row.querySelector('[data-unit-description]');
        description.value = unit.description || '';
        description.readOnly = isDefault;
        name.addEventListener('blur', function () { name.value = name.value.trim().toUpperCase(); });
        var remove = row.querySelector('[data-remove-business-unit]');
        remove.hidden = isDefault;
        remove.addEventListener('click', function () {
            if (isDefault) return;
            row.remove();
            updateUnitList();
        });
        unitList.appendChild(row);
        updateUnitList();
        return name;
    }

    function businessUnits() {
        return Array.from(unitList.querySelectorAll('[data-business-unit-row]')).map(function (row) {
            return {
                id: row.dataset.unitId ? Number(row.dataset.unitId) : null,
                name: row.querySelector('[data-unit-name]').value.trim().toUpperCase(),
                description: row.querySelector('[data-unit-description]').value,
            };
        });
    }

    function updateServiceFlows() {
        var rows = Array.from(serviceList.querySelectorAll('[data-service-flow-row]'));
        servicesEmpty.hidden = rows.length > 0;
        addServiceButton.disabled = saving || rows.length >= 50;
        rows.forEach(function (row) {
            var questions = Array.from(row.querySelector('[data-service-questions]').querySelectorAll('[data-service-question-row]'));
            row.querySelector('[data-add-service-question]').disabled = saving || questions.length >= 20;
            questions.forEach(function (question) {
                question.querySelector('[data-remove-service-question]').hidden = questions.length <= 1;
            });
        });
    }

    function addServiceQuestion(row, value) {
        var question = questionTemplate.content.firstElementChild.cloneNode(true);
        var input = question.querySelector('[data-service-question]');
        input.value = value || '';
        question.querySelector('[data-remove-service-question]').addEventListener('click', function () {
            var list = row.querySelector('[data-service-questions]');
            if (list.querySelectorAll('[data-service-question-row]').length <= 1) return;
            question.remove();
            updateServiceFlows();
        });
        row.querySelector('[data-service-questions]').appendChild(question);
        updateServiceFlows();
        return input;
    }

    function addServiceFlow(flow) {
        var row = serviceTemplate.content.firstElementChild.cloneNode(true);
        var name = row.querySelector('[data-service-name]');
        name.value = flow.service || '';
        row.querySelector('[data-remove-service-flow]').addEventListener('click', function () {
            row.remove();
            updateServiceFlows();
        });
        row.querySelector('[data-add-service-question]').addEventListener('click', function () {
            if (row.querySelector('[data-service-questions]').querySelectorAll('[data-service-question-row]').length < 20) {
                addServiceQuestion(row, '').focus();
            }
        });
        serviceList.appendChild(row);
        (flow.questions && flow.questions.length ? flow.questions : ['']).forEach(function (question) {
            addServiceQuestion(row, question);
        });
        updateServiceFlows();
        return name;
    }

    function serviceFlows() {
        return Array.from(serviceList.querySelectorAll('[data-service-flow-row]')).map(function (row) {
            return {
                service: row.querySelector('[data-service-name]').value.trim(),
                questions: Array.from(row.querySelector('[data-service-questions]').querySelectorAll('[data-service-question-row]'))
                    .map(function (question) { return question.querySelector('[data-service-question]').value.trim(); }),
            };
        });
    }

    function updateEscalationRules() {
        addEscalationRuleButton.disabled = saving
            || escalationRuleList.querySelectorAll('[data-escalation-rule-row]').length >= 30;
    }

    function addEscalationRule(value) {
        var row = document.createElement('div');
        row.dataset.escalationRuleRow = '';
        row.className = 'rounded-xl border border-slate-200 bg-slate-50 p-3 sm:p-4';
        var header = document.createElement('div');
        header.className = 'mb-2 flex items-center justify-between gap-3';
        var label = document.createElement('label');
        label.className = 'min-w-0 flex-1 text-sm font-semibold text-slate-700';
        var index = escalationRuleList.querySelectorAll('[data-escalation-rule-row]').length + 1;
        label.appendChild(document.createTextNode('Regla ' + index));
        var textarea = document.createElement('textarea');
        textarea.rows = 3;
        textarea.maxLength = 2000;
        textarea.required = true;
        textarea.value = value || '';
        textarea.dataset.escalationRule = '';
        textarea.className = 'mt-2 block w-full rounded-xl border border-slate-300 bg-white p-3 text-sm leading-relaxed text-slate-800 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200';
        label.appendChild(textarea);
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'shrink-0 rounded-lg px-2 py-1 text-xs font-semibold text-red-600 hover:bg-red-50';
        remove.textContent = 'Quitar regla';
        remove.addEventListener('click', function () {
            row.remove();
            Array.from(escalationRuleList.querySelectorAll('[data-escalation-rule-row] label')).forEach(function (item, itemIndex) {
                item.firstChild.textContent = 'Regla ' + (itemIndex + 1);
            });
            updateEscalationRules();
        });
        header.append(label, remove);
        row.appendChild(header);
        escalationRuleList.appendChild(row);
        updateEscalationRules();
        return textarea;
    }

    function escalationRules() {
        return Array.from(escalationRuleList.querySelectorAll('[data-escalation-rule]'))
            .map(function (rule) { return rule.value.trim(); });
    }

    function syncSlider(slider) {
        var select = inputs[slider.dataset.promptSlider];
        if (!select) return;
        var options = Array.from(select.options);
        slider.max = String(Math.max(0, options.length - 1));
        var index = options.findIndex(function (option) { return option.value === select.value; });
        if (index < 0) index = 0;
        slider.value = String(index);
        var label = options[index] ? options[index].textContent : '';
        slider.setAttribute('aria-valuetext', label);
        var output = document.getElementById(slider.dataset.sliderOutput);
        if (output) output.textContent = label;
    }

    function firstIncompleteServiceField() {
        var rows = Array.from(serviceList.querySelectorAll('[data-service-flow-row]'));
        for (var row of rows) {
            var name = row.querySelector('[data-service-name]');
            if (!name.value.trim()) return name;
            var questions = Array.from(row.querySelector('[data-service-questions]').querySelectorAll('[data-service-question-row]'));
            for (var question of questions) {
                var input = question.querySelector('[data-service-question]');
                if (!input.value.trim()) return input;
            }
        }
        return null;
    }

    function selectStep(name) {
        form.hidden = name === 'annotations';
        steps.forEach(function (step) {
            var selected = step.dataset.promptStep === name;
            step.setAttribute('aria-current', selected ? 'step' : 'false');
            document.getElementById(step.getAttribute('aria-controls')).hidden = !selected;
        });
    }

    function stepForError(name) {
        var label = editor.querySelector('[data-prompt-error="' + name + '"]');
        var panel = label && label.closest('[data-prompt-panel]');
        return panel ? panel.dataset.promptPanel : steps[0].dataset.promptStep;
    }

    function clearErrors() {
        error.hidden = true;
        error.textContent = '';
        editor.querySelectorAll('[data-prompt-error]').forEach(function (label) {
            label.hidden = true;
            label.textContent = '';
        });
        Object.values(inputs).forEach(function (input) {
            input.removeAttribute('aria-invalid');
        });
        sliders.forEach(function (slider) { slider.removeAttribute('aria-invalid'); });
    }

    function showError(message) {
        error.textContent = message;
        error.hidden = false;
    }

    function setSaving(value) {
        saving = value;
        fields.disabled = value;
        saveButton.disabled = value;
        saveButton.textContent = value ? 'Guardando…' : 'Guardar cambios';
        updateUnitList();
        updateServiceFlows();
    }

    async function readResponse(response) {
        if (response.redirected) {
            throw new Error('La sesión venció. Vuelve a iniciar sesión para continuar.');
        }
        if (!(response.headers.get('content-type') || '').includes('application/json')) {
            throw new Error('No se pudo completar la solicitud. Recarga la página e inténtalo de nuevo.');
        }
        return response.json();
    }

    function populateEditor(prompt) {
        Object.keys(inputs).forEach(function (name) {
            if (inputs[name].type === 'checkbox') inputs[name].checked = prompt[name] === true;
            else inputs[name].value = prompt[name] || '';
        });
        if (modeToggle) modeToggle.dataset.modeBlockReason = prompt.mode_block_reason || '';
        syncMode();
        syncQualificationMethodology();
        sliders.forEach(syncSlider);
        unitList.innerHTML = '';
        serviceList.innerHTML = '';
        escalationRuleList.innerHTML = '';
        (prompt.business_units || []).forEach(addUnit);
        (prompt.service_flows || []).forEach(addServiceFlow);
        (prompt.escalation_rules || []).forEach(addEscalationRule);
        renderReminderSchedule(prompt.reminder_schedule || []);
        updateUnitList();
        updateServiceFlows();
        updateEscalationRules();
    }

    var reminderTransitions = [
        ['none', 'first', 'Inicio', 'Primer recordatorio'],
        ['first', 'second', 'Primer recordatorio', 'Segundo recordatorio'],
        ['second', 'third', 'Segundo recordatorio', 'Tercer recordatorio'],
    ];

    function renderReminderSchedule(schedule) {
        if (!reminderList) return;
        reminderList.innerHTML = '';
        schedule.slice(0, 3).forEach(function (item, index) {
            var transition = reminderTransitions[index];
            if (!transition) return;
            var row = document.createElement('div');
            row.className = 'grid gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:grid-cols-[1fr_10rem_8rem_8rem] sm:items-center';
            row.dataset.reminderRow = '';
            var label = document.createElement('div');
            label.className = 'text-sm font-medium text-slate-800';
            label.textContent = transition[2] + ' → ' + transition[3];
            var enabledLabel = document.createElement('label');
            enabledLabel.className = 'flex items-center gap-2 text-sm text-slate-700';
            var enabled = document.createElement('input');
            enabled.type = 'checkbox'; enabled.checked = item.enabled !== false;
            enabled.className = 'h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500';
            enabled.dataset.reminderEnabled = '';
            enabledLabel.append(enabled, document.createTextNode('Habilitado'));
            var amount = document.createElement('input');
            amount.type = 'number'; amount.min = '1'; amount.max = '365'; amount.required = true;
            amount.value = String(item.amount || 48);
            amount.className = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm';
            amount.dataset.reminderAmount = '';
            var unit = document.createElement('select');
            unit.className = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm';
            unit.dataset.reminderUnit = '';
            [['hours', 'Horas'], ['days', 'Días']].forEach(function (optionData) {
                var option = document.createElement('option'); option.value = optionData[0]; option.textContent = optionData[1];
                unit.appendChild(option);
            });
            unit.value = item.unit || 'hours';
            function syncEnabled() {
                amount.disabled = !enabled.checked;
                unit.disabled = !enabled.checked;
                amount.classList.toggle('bg-slate-100', !enabled.checked);
                unit.classList.toggle('bg-slate-100', !enabled.checked);
            }
            enabled.addEventListener('change', syncEnabled);
            enabled.addEventListener('change', syncReminderOrder);
            syncEnabled();
            row.append(label, enabledLabel, amount, unit);
            reminderList.appendChild(row);
        });
        syncReminderOrder();
    }

    function syncReminderOrder() {
        if (!reminderList) return;
        var rows = Array.from(reminderList.querySelectorAll('[data-reminder-row]'));
        rows.forEach(function (row, index) {
            var enabled = row.querySelector('[data-reminder-enabled]');
            var previousEnabled = index === 0 || rows[index - 1].querySelector('[data-reminder-enabled]').checked;
            enabled.disabled = index > 0 && !previousEnabled;
            if (!previousEnabled) enabled.checked = false;
        });
    }

    function reminderSchedule() {
        return Array.from(reminderList.querySelectorAll('[data-reminder-row]')).map(function (row, index) {
            var transition = reminderTransitions[index];
            return { from: transition[0], to: transition[1], amount: Number(row.querySelector('[data-reminder-amount]').value), unit: row.querySelector('[data-reminder-unit]').value, enabled: row.querySelector('[data-reminder-enabled]').checked };
        });
    }

    async function loadEditor() {
        try {
            var response = await fetch(form.action, {
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
                cache: 'no-store',
            });
            var data = await readResponse(response);
            if (!response.ok) throw new Error('No se pudieron cargar las instrucciones del agente.');
            populateEditor(data.prompt);
            fields.disabled = false;
            saveButton.disabled = false;
        } catch (failure) {
            showError(failure.message || 'No se pudieron cargar las instrucciones.');
        } finally {
            loading.hidden = true;
        }
    }

    sliders.forEach(function (slider) {
        slider.addEventListener('input', function () {
            var select = inputs[slider.dataset.promptSlider];
            if (!select) return;
            select.selectedIndex = Number(slider.value);
            syncSlider(slider);
        });
    });

    loadEditor();

    addUnitButton.addEventListener('click', function () {
        var count = Array.from(unitList.querySelectorAll('[data-business-unit-row]'))
            .filter(function (row) { return row.dataset.defaultUnit !== 'true'; }).length;
        if (count < 50) addUnit({}).focus();
    });

    addServiceButton.addEventListener('click', function () {
        if (serviceList.querySelectorAll('[data-service-flow-row]').length < 50) addServiceFlow({}).focus();
    });

    addEscalationRuleButton.addEventListener('click', function () {
        if (escalationRuleList.querySelectorAll('[data-escalation-rule-row]').length < 30) addEscalationRule('').focus();
    });

    if (inputs.qualification_method) {
        inputs.qualification_method.addEventListener('change', syncQualificationMethodology);
        syncQualificationMethodology();
    }


    steps.forEach(function (step, index) {
        step.addEventListener('click', function () { selectStep(step.dataset.promptStep); });
        step.addEventListener('keydown', function (event) {
            var next = index;
            if (event.key === 'ArrowRight') next = (index + 1) % steps.length;
            else if (event.key === 'ArrowLeft') next = (index + steps.length - 1) % steps.length;
            else if (event.key === 'Home') next = 0;
            else if (event.key === 'End') next = steps.length - 1;
            else return;
            event.preventDefault();
            selectStep(steps[next].dataset.promptStep);
            steps[next].focus();
        });
    });

    if (window.location.hash === '#anotaciones') selectStep('annotations');

    var annotationPanel = document.getElementById('anotaciones');
    var annotationDialog = document.getElementById('agentAnnotationDialog');
    var annotationDetail = annotationDialog.querySelector('[data-annotation-detail]');
    var annotationCondenseDialog = document.getElementById('agentAnnotationCondenseDialog');
    annotationPanel.addEventListener('click', function (event) {
        if (event.target.closest('[data-annotation-condense]')) {
            if (annotationDialog.open) annotationDialog.close();
            annotationCondenseDialog.showModal();
            return;
        }
        var row = event.target.closest('[data-annotation-row]');
        if (!row) return;

        annotationDetail.replaceChildren(row.querySelector('template').content.cloneNode(true));
        updateAnnotationCounter();
        row.querySelector('button').focus({ preventScroll: true });
        annotationDialog.showModal();
        annotationDialog.scrollTop = 0;
    });

    if (annotationCondenseDialog) {
        var annotationCondenseForm = document.getElementById('agentAnnotationCondenseForm');
        var annotationCondenseCancel = document.getElementById('agentAnnotationCondenseCancel');
        var annotationCondenseSubmit = document.getElementById('agentAnnotationCondenseSubmit');
        var annotationCondensing = false;

        annotationCondenseCancel.addEventListener('click', function () {
            annotationCondenseDialog.close();
        });
        annotationCondenseDialog.addEventListener('cancel', function (event) {
            if (annotationCondensing) event.preventDefault();
        });
        annotationCondenseForm.addEventListener('submit', function (event) {
            if (annotationCondensing) {
                event.preventDefault();
                return;
            }
            annotationCondensing = true;
            annotationCondenseForm.setAttribute('aria-busy', 'true');
            annotationCondenseCancel.disabled = true;
            annotationCondenseSubmit.disabled = true;
            annotationCondenseSubmit.textContent = 'Resumiendo…';
            document.getElementById('agentAnnotationCondenseStatus').hidden = false;
        });
    }

    // Detalle de la anotación: editar sugerencias y confirmar borrado.
    // El contenido se clona de una plantilla por fila, así que
    // los botones se atienden con un solo listener en el contenedor.
    function annotationPart(name) {
        return annotationDetail.querySelector('[data-annotation-' + name + ']');
    }

    function updateAnnotationCounter() {
        var textarea = annotationPart('textarea');
        var counter = annotationPart('counter');
        if (!textarea || !counter) return;
        var length = textarea.value.length;
        counter.textContent = length + ' de ' + textarea.maxLength + ' caracteres';
        counter.classList.toggle('text-red-600', length === 0);
    }

    function toggleAnnotationEdit(editing) {
        var textarea = annotationPart('textarea');
        annotationPart('view').hidden = editing;
        annotationPart('edit-form').hidden = !editing;
        annotationPart('edit').hidden = editing;
        annotationPart('edit').setAttribute('aria-expanded', String(editing));
        if (editing) {
            textarea.focus();
            textarea.setSelectionRange(textarea.value.length, textarea.value.length);
        } else {
            textarea.value = textarea.defaultValue;
            updateAnnotationCounter();
            annotationPart('edit').focus();
        }
    }

    function toggleAnnotationDelete(confirming) {
        annotationPart('delete-confirm').hidden = !confirming;
        annotationPart('actions').hidden = confirming;
        annotationPart('delete').setAttribute('aria-expanded', String(confirming));
        (confirming ? annotationPart('cancel-delete') : annotationPart('delete')).focus();
    }

    annotationDetail.addEventListener('input', function (event) {
        if (event.target.matches('[data-annotation-textarea]')) updateAnnotationCounter();
    });

    annotationDetail.addEventListener('click', function (event) {
        var target = event.target.closest('button');
        if (!target) return;

        if (target.matches('[data-annotation-edit]')) toggleAnnotationEdit(true);
        else if (target.matches('[data-annotation-cancel-edit]')) toggleAnnotationEdit(false);
        else if (target.matches('[data-annotation-delete]')) toggleAnnotationDelete(true);
        else if (target.matches('[data-annotation-cancel-delete]')) toggleAnnotationDelete(false);
    });

    // Evita enviar dos veces al guardar o eliminar.
    annotationDetail.addEventListener('submit', function (event) {
        var button = event.target.querySelector('button[type="submit"]');
        if (button) {
            if (button.disabled) { event.preventDefault(); return; }
            setTimeout(function () { button.disabled = true; }, 0);
        }
    });

    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (saving || saveButton.disabled) return;
        clearErrors();
        var incompleteService = firstIncompleteServiceField();
        if (incompleteService) {
            var serviceError = document.getElementById('error_service_flows');
            serviceError.textContent = 'Completa el nombre y todas las preguntas del servicio antes de guardar.';
            serviceError.hidden = false;
            showError('Revisa el servicio incompleto.');
            selectStep(stepForError('service_flows'));
            incompleteService.focus();
            return;
        }
        // FormData se construye antes de deshabilitar el formulario para no perder campos.
        var body = new FormData(form);
        body.set('business_units', JSON.stringify(businessUnits()));
        body.set('service_flows', JSON.stringify(serviceFlows()));
        body.set('escalation_rules', JSON.stringify(escalationRules()));
        body.set('reminder_schedule', JSON.stringify(reminderSchedule()));
        var invalidField = null;
        setSaving(true);
        try {
            var response = await fetch(form.action, {
                method: 'POST', body: body,
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
            });
            var data = await readResponse(response);
            if (!response.ok) {
                Object.entries(data.errors || {}).forEach(function (entry) {
                    var name = entry[0];
                    if (!Object.prototype.hasOwnProperty.call(inputs, name)
                            && name !== 'business_units' && name !== 'service_flows'
                            && name !== 'escalation_rules') return;
                    var label = editor.querySelector('[data-prompt-error="' + name + '"]');
                    if (!label) return;
                    label.textContent = entry[1].map(function (item) { return item.message; }).join(' ');
                    label.hidden = false;
                    if (inputs[name]) inputs[name].setAttribute('aria-invalid', 'true');
                    var slider = editor.querySelector('[data-prompt-slider="' + name + '"]');
                    if (slider) slider.setAttribute('aria-invalid', 'true');
                    if (!invalidField) invalidField = name;
                });
                if (invalidField) {
                    selectStep(stepForError(invalidField));
                    showError('Revisa los campos señalados antes de guardar.');
                } else {
                    var generalErrors = (data.errors && data.errors.__all__) || [];
                    showError(generalErrors.length ? generalErrors.map(function (item) { return item.message; }).join(' ') : 'No se pudieron guardar los cambios. Inténtalo de nuevo.');
                }
                return;
            }
            toast.textContent = data.message;
            populateEditor(data.prompt);
            if (data.name) {
                document.getElementById('agentPromptTitle').textContent = 'Configuraciones · ' + data.name;
                document.title = 'Configuraciones de ' + data.name + ' | Lead Manager';
            }
            toast.hidden = false;
            clearTimeout(toastTimer);
            toastTimer = setTimeout(function () { toast.hidden = true; }, 5000);
        } catch (failure) {
            showError(failure.message || 'No se pudieron guardar los cambios. Inténtalo de nuevo.');
        } finally {
            setSaving(false);
            if (invalidField) {
                var invalidSlider = editor.querySelector('[data-prompt-slider="' + invalidField + '"]');
                (invalidSlider || inputs[invalidField]
                    || (invalidField === 'service_flows' ? addServiceButton
                        : invalidField === 'escalation_rules' ? addEscalationRuleButton : addUnitButton)).focus();
            }
        }
    });
})();
