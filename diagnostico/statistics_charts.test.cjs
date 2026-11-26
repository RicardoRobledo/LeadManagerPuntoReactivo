const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname,
    '../leadmanager/apps/prospects/templates/prospects/statistics.html'), 'utf8');
const script = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).find(source => source.includes('const readJSON'));

function renderCharts(unrated = 0) {
    const values = { qualified_count: 2, in_progress_count: 1, discarded_count: 3, unrated_count: unrated };
    const distribution = [
        { source: 'web', qualified: 1, in_progress: 1, discarded: 2, unrated },
        { source: 'email', qualified: 1, in_progress: 0, discarded: 1, unrated: 0 },
    ];
    const charts = {};
    const nodes = new Map();
    const callbacks = [];
    function Chart(element, config) {
        charts[element.id] = JSON.parse(JSON.stringify(config));
    }
    Chart.defaults = { font: {} };
    const document = {
        getElementById(id) {
            if (!nodes.has(id)) {
                nodes.set(id, {
                    id, addEventListener() {},
                    textContent: JSON.stringify(id === 'data-qualification-source' ? distribution : []),
                });
            }
            return nodes.get(id);
        },
        querySelector() { return null; },
        addEventListener(event, callback) {
            if (event === 'DOMContentLoaded') callbacks.push(callback);
        },
    };
    const source = script.replace(/{{\s*(\w+)[^}]*}}/g, (_, key) => values[key] || 0);
    vm.runInNewContext(source, { document, Chart });
    callbacks.forEach(callback => callback());
    return charts;
}

test('las gráficas general y por canal separan escalados, en proceso y descartados', () => {
    const charts = renderCharts();
    const general = charts.qualificationChart.data;
    const channels = charts.qualificationSourceChart.data;
    assert.deepEqual(general.labels, ['Escalados', 'En proceso', 'Descartados']);
    assert.deepEqual(general.datasets[0].data, [2, 1, 3]);
    assert.deepEqual(general.datasets[0].backgroundColor, ['#10b981', '#f59e0b', '#8b5cf6']);
    assert.deepEqual(channels.labels, ['chatweb', 'Correo']);
    assert.deepEqual(channels.datasets.map(row => [row.label, row.data]), [
        ['Escalados', [1, 1]], ['En proceso', [1, 0]], ['Descartados', [2, 1]],
    ]);
});

test('las gráficas omiten los registros sin estado y conservan solo las tres categorías', () => {
    const charts = renderCharts(1);
    const general = charts.qualificationChart.data;
    const channels = charts.qualificationSourceChart.data;
    assert.deepEqual(general.labels, ['Escalados', 'En proceso', 'Descartados']);
    assert.deepEqual(general.datasets[0].data, [2, 1, 3]);
    assert.deepEqual(channels.datasets.map(row => row.label), ['Escalados', 'En proceso', 'Descartados']);
    assert.equal(channels.datasets.length, 3);
});
