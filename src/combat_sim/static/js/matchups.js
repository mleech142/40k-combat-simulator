/**
 * Matchups page JavaScript for matrix comparison functionality.
 */

(function() {
    'use strict';

    // State
    const state = {
        attackers: [],  // [{weapon_id, weapon_type, label, unit_name, points}]
        defenders: [],  // [{unit_id, model_count, label, points}]
        results: null,
        currentMetric: 'avg_damage',
        weaponTypeFilter: 'both',  // 'ranged', 'melee', or 'both'
        conditions: {
            half_range: false,
            charged: false,
            stationary: false,
            cover: false,
            reroll_hits: 'none',
            reroll_wounds: 'none'
        }
    };

    // DOM Elements
    const elements = {};

    // Initialize on DOM ready
    document.addEventListener('DOMContentLoaded', init);

    function init() {
        cacheElements();
        bindEvents();
        loadSavedComparisons();
    }

    function cacheElements() {
        elements.attackerSearch = document.getElementById('attacker-search');
        elements.attackerDropdown = document.getElementById('attacker-dropdown');
        elements.attackerList = document.getElementById('attacker-list');

        elements.defenderSearch = document.getElementById('defender-search');
        elements.defenderDropdown = document.getElementById('defender-dropdown');
        elements.defenderList = document.getElementById('defender-list');

        elements.matrixContainer = document.getElementById('matrix-container');
        elements.matrixSummary = document.getElementById('matrix-summary');
        elements.bestAttackers = document.getElementById('best-attackers');
        elements.biggestThreats = document.getElementById('biggest-threats');

        elements.detailPanel = document.getElementById('detail-panel');
        elements.metricSelect = document.getElementById('mat-metric');

        elements.loadModal = document.getElementById('load-modal');
        elements.saveModal = document.getElementById('save-modal');
        elements.comparisonList = document.getElementById('comparison-list');
    }

    function bindEvents() {
        // Weapon type toggle
        document.querySelectorAll('.weapon-type-toggle .toggle-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.weapon-type-toggle .toggle-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                state.weaponTypeFilter = btn.dataset.type;
            });
        });

        // Search inputs - both now search for units
        elements.attackerSearch.addEventListener('input', debounce(() => searchUnitsForAttacker(), 300));
        elements.attackerSearch.addEventListener('focus', () => searchUnitsForAttacker());
        elements.defenderSearch.addEventListener('input', debounce(() => searchUnitsForDefender(), 300));
        elements.defenderSearch.addEventListener('focus', () => searchUnitsForDefender());

        // Close dropdowns on outside click
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.search-add')) {
                elements.attackerDropdown.innerHTML = '';
                elements.defenderDropdown.innerHTML = '';
            }
        });

        // Condition toggles
        document.getElementById('mat-half-range').addEventListener('change', (e) => {
            state.conditions.half_range = e.target.checked;
        });
        document.getElementById('mat-charged').addEventListener('change', (e) => {
            state.conditions.charged = e.target.checked;
        });
        document.getElementById('mat-stationary').addEventListener('change', (e) => {
            state.conditions.stationary = e.target.checked;
        });
        document.getElementById('mat-cover').addEventListener('change', (e) => {
            state.conditions.cover = e.target.checked;
        });
        document.getElementById('mat-reroll-hits').addEventListener('change', (e) => {
            state.conditions.reroll_hits = e.target.value;
        });
        document.getElementById('mat-reroll-wounds').addEventListener('change', (e) => {
            state.conditions.reroll_wounds = e.target.value;
        });

        // Metric selector
        elements.metricSelect.addEventListener('change', (e) => {
            state.currentMetric = e.target.value;
            if (state.results) {
                renderMatrix();
            }
        });

        // Action buttons
        document.getElementById('btn-run-matrix').addEventListener('click', runMatrix);
        document.getElementById('btn-save-comparison').addEventListener('click', showSaveModal);
        document.getElementById('btn-load-comparison').addEventListener('click', showLoadModal);

        // Modal close buttons
        document.getElementById('close-detail').addEventListener('click', () => {
            elements.detailPanel.style.display = 'none';
        });
        document.getElementById('close-load-modal').addEventListener('click', () => {
            elements.loadModal.style.display = 'none';
        });
        document.getElementById('close-save-modal').addEventListener('click', () => {
            elements.saveModal.style.display = 'none';
        });
        document.getElementById('btn-confirm-save').addEventListener('click', saveComparison);

        // Close modals on backdrop click
        elements.loadModal.addEventListener('click', (e) => {
            if (e.target === elements.loadModal) {
                elements.loadModal.style.display = 'none';
            }
        });
        elements.saveModal.addEventListener('click', (e) => {
            if (e.target === elements.saveModal) {
                elements.saveModal.style.display = 'none';
            }
        });
    }

    // =========================
    // Search Functions
    // =========================

    async function searchUnitsForAttacker() {
        const query = elements.attackerSearch.value.trim();
        if (query.length < 1) {
            elements.attackerDropdown.innerHTML = '';
            return;
        }

        try {
            const response = await fetch(`/api/units?search=${encodeURIComponent(query)}`);
            const units = await response.json();

            elements.attackerDropdown.innerHTML = units.map(u => `
                <div class="dropdown-item" data-unit-id="${u.id}">
                    <span class="unit-name">${u.name}</span>
                    <span class="unit-faction">${u.faction || ''}</span>
                </div>
            `).join('');

            // Bind click events
            elements.attackerDropdown.querySelectorAll('.dropdown-item').forEach(item => {
                item.addEventListener('click', () => {
                    const unitId = parseInt(item.dataset.unitId);
                    const unitName = item.querySelector('.unit-name').textContent;
                    addUnitAsAttacker(unitId, unitName);
                    elements.attackerSearch.value = '';
                    elements.attackerDropdown.innerHTML = '';
                });
            });
        } catch (err) {
            console.error('Error searching units:', err);
        }
    }

    async function searchUnitsForDefender() {
        const query = elements.defenderSearch.value.trim();
        if (query.length < 1) {
            elements.defenderDropdown.innerHTML = '';
            return;
        }

        try {
            const response = await fetch(`/api/units?search=${encodeURIComponent(query)}`);
            const units = await response.json();

            elements.defenderDropdown.innerHTML = units.map(u => `
                <div class="dropdown-item" data-unit-id="${u.id}">
                    <span class="unit-name">${u.name}</span>
                    <span class="unit-faction">${u.faction || ''}</span>
                </div>
            `).join('');

            // Bind click events
            elements.defenderDropdown.querySelectorAll('.dropdown-item').forEach(item => {
                item.addEventListener('click', () => {
                    const unitId = parseInt(item.dataset.unitId);
                    const unitName = item.querySelector('.unit-name').textContent;
                    showModelCountPrompt(unitId, unitName);
                    elements.defenderSearch.value = '';
                    elements.defenderDropdown.innerHTML = '';
                });
            });
        } catch (err) {
            console.error('Error searching units:', err);
        }
    }

    function showModelCountPrompt(unitId, unitName) {
        const count = prompt(`How many models for "${unitName}"?`, '5');
        if (count && !isNaN(parseInt(count))) {
            addDefender(unitId, parseInt(count), unitName);
        }
    }

    // =========================
    // Add/Remove Functions
    // =========================

    async function addUnitAsAttacker(unitId, unitName) {
        try {
            // Fetch the unit with all its weapons
            const response = await fetch(`/api/units/${unitId}`);
            const unit = await response.json();

            const filter = state.weaponTypeFilter;
            let weaponsAdded = 0;

            // Add ranged weapons
            if ((filter === 'both' || filter === 'ranged') && unit.ranged_weapons) {
                for (const weapon of unit.ranged_weapons) {
                    // Check if already added
                    if (!state.attackers.some(a => a.weapon_id === weapon.id && a.weapon_type === 'ranged')) {
                        state.attackers.push({
                            weapon_id: weapon.id,
                            weapon_type: 'ranged',
                            label: `${unitName} - ${weapon.name}`,
                            unit_name: unitName,
                            points: (unit.points_cost || 0) + (weapon.points_cost || 0)
                        });
                        weaponsAdded++;
                    }
                }
            }

            // Add melee weapons
            if ((filter === 'both' || filter === 'melee') && unit.melee_weapons) {
                for (const weapon of unit.melee_weapons) {
                    // Check if already added
                    if (!state.attackers.some(a => a.weapon_id === weapon.id && a.weapon_type === 'melee')) {
                        state.attackers.push({
                            weapon_id: weapon.id,
                            weapon_type: 'melee',
                            label: `${unitName} - ${weapon.name}`,
                            unit_name: unitName,
                            points: (unit.points_cost || 0) + (weapon.points_cost || 0)
                        });
                        weaponsAdded++;
                    }
                }
            }

            if (weaponsAdded === 0) {
                alert(`No ${filter === 'both' ? '' : filter + ' '}weapons found for ${unitName}`);
            }

            renderAttackerList();

        } catch (err) {
            console.error('Error adding unit:', err);
            alert('Error loading unit weapons');
        }
    }

    function removeAttacker(index) {
        state.attackers.splice(index, 1);
        renderAttackerList();
    }

    function removeAttackersByUnit(unitName) {
        state.attackers = state.attackers.filter(a => a.unit_name !== unitName);
        renderAttackerList();
    }

    function addDefender(unitId, modelCount, unitName) {
        // Check if already added with same model count
        if (state.defenders.some(d => d.unit_id === unitId && d.model_count === modelCount)) {
            return;
        }

        state.defenders.push({
            unit_id: unitId,
            model_count: modelCount,
            label: `${unitName} (x${modelCount})`
        });

        renderDefenderList();
    }

    function removeDefender(index) {
        state.defenders.splice(index, 1);
        renderDefenderList();
    }

    function renderAttackerList() {
        if (state.attackers.length === 0) {
            elements.attackerList.innerHTML = '<p class="empty-message">No attackers selected</p>';
            return;
        }

        // Group by unit
        const byUnit = {};
        state.attackers.forEach((a, idx) => {
            const unit = a.unit_name || 'Unknown';
            if (!byUnit[unit]) byUnit[unit] = [];
            byUnit[unit].push({ ...a, index: idx });
        });

        let html = '';
        for (const [unitName, weapons] of Object.entries(byUnit)) {
            html += `<div class="unit-group">
                <div class="unit-group-header">
                    <span class="unit-group-name">${unitName}</span>
                    <button class="btn-remove-group" data-unit="${unitName}">&times;</button>
                </div>
                <div class="unit-group-weapons">`;

            for (const w of weapons) {
                const typeIcon = w.weapon_type === 'ranged' ? '🎯' : '⚔️';
                html += `<div class="selection-item">
                    <span class="weapon-icon">${typeIcon}</span>
                    <span class="selection-label">${w.label.replace(unitName + ' - ', '')}</span>
                    <button class="btn-remove" data-index="${w.index}">&times;</button>
                </div>`;
            }

            html += '</div></div>';
        }

        elements.attackerList.innerHTML = html;

        // Bind remove events
        elements.attackerList.querySelectorAll('.btn-remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                removeAttacker(parseInt(btn.dataset.index));
            });
        });

        elements.attackerList.querySelectorAll('.btn-remove-group').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                removeAttackersByUnit(btn.dataset.unit);
            });
        });
    }

    function renderDefenderList() {
        if (state.defenders.length === 0) {
            elements.defenderList.innerHTML = '<p class="empty-message">No defenders selected</p>';
            return;
        }

        elements.defenderList.innerHTML = state.defenders.map((d, idx) => `
            <div class="selection-item">
                <span class="selection-label">${d.label}</span>
                <button class="btn-remove" data-index="${idx}">&times;</button>
            </div>
        `).join('');

        elements.defenderList.querySelectorAll('.btn-remove').forEach(btn => {
            btn.addEventListener('click', () => removeDefender(parseInt(btn.dataset.index)));
        });
    }

    // =========================
    // Matrix Simulation
    // =========================

    async function runMatrix() {
        if (state.attackers.length === 0 || state.defenders.length === 0) {
            alert('Please add at least one attacker unit and one defender unit.');
            return;
        }

        // Show loading state
        elements.matrixContainer.innerHTML = '<div class="matrix-loading">Running simulations...</div>';

        try {
            const response = await fetch('/api/matrix/simulate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    attackers: state.attackers,
                    defenders: state.defenders,
                    conditions: state.conditions,
                    metrics: ['avg_damage', 'models_killed', 'damage_per_point', 'kill_probability']
                })
            });

            state.results = await response.json();
            renderMatrix();
            renderSummary();

        } catch (err) {
            console.error('Error running matrix:', err);
            elements.matrixContainer.innerHTML = '<div class="matrix-error">Error running simulation. Please try again.</div>';
        }
    }

    function renderMatrix() {
        if (!state.results || !state.results.matrix.length) {
            return;
        }

        const metric = state.currentMetric;
        const matrix = state.results.matrix;
        const attackers = state.results.attackers;
        const defenders = state.results.defenders;

        // Find min/max for color scaling
        let minVal = Infinity, maxVal = -Infinity;
        for (const row of matrix) {
            for (const cell of row) {
                const val = cell[metric] || 0;
                minVal = Math.min(minVal, val);
                maxVal = Math.max(maxVal, val);
            }
        }

        // Build table HTML
        let html = '<div class="matrix-scroll"><table class="matrix-table">';

        // Header row
        html += '<thead><tr><th class="matrix-corner"></th>';
        for (const def of defenders) {
            html += `<th class="matrix-header-cell" title="${def.label}">${truncate(def.label, 20)}</th>`;
        }
        html += '</tr></thead>';

        // Data rows
        html += '<tbody>';
        for (let atkIdx = 0; atkIdx < attackers.length; atkIdx++) {
            html += '<tr>';
            html += `<th class="matrix-row-header" title="${attackers[atkIdx].label}">${truncate(attackers[atkIdx].label, 25)}</th>`;

            for (let defIdx = 0; defIdx < defenders.length; defIdx++) {
                const cell = matrix[atkIdx][defIdx];
                const value = cell[metric] || 0;
                const color = getHeatColor(value, minVal, maxVal);
                const displayVal = formatMetricValue(value, metric);

                html += `<td class="matrix-cell" style="background-color: ${color}"
                            data-atk-idx="${atkIdx}" data-def-idx="${defIdx}"
                            title="${attackers[atkIdx].label} vs ${defenders[defIdx].label}: ${displayVal}">
                            ${displayVal}
                         </td>`;
            }
            html += '</tr>';
        }
        html += '</tbody></table></div>';

        elements.matrixContainer.innerHTML = html;

        // Bind cell click events
        elements.matrixContainer.querySelectorAll('.matrix-cell').forEach(cell => {
            cell.addEventListener('click', () => {
                const atkIdx = parseInt(cell.dataset.atkIdx);
                const defIdx = parseInt(cell.dataset.defIdx);
                showCellDetail(atkIdx, defIdx);
            });
        });
    }

    function getHeatColor(value, min, max) {
        // Green (high) -> Yellow (mid) -> Red (low)
        if (max === min) return 'rgba(255, 235, 59, 0.5)'; // Yellow if all same

        const ratio = (value - min) / (max - min);

        // Interpolate: red (0) -> yellow (0.5) -> green (1)
        let r, g, b;
        if (ratio < 0.5) {
            // Red to Yellow
            const t = ratio * 2;
            r = 239;
            g = Math.round(68 + (235 - 68) * t);
            b = 68;
        } else {
            // Yellow to Green
            const t = (ratio - 0.5) * 2;
            r = Math.round(255 - (255 - 76) * t);
            g = Math.round(235 - (235 - 175) * t);
            b = Math.round(59 + (80 - 59) * t);
        }

        return `rgba(${r}, ${g}, ${b}, 0.7)`;
    }

    function formatMetricValue(value, metric) {
        switch (metric) {
            case 'kill_probability':
                return `${value}%`;
            case 'damage_per_point':
                return value.toFixed(3);
            default:
                return value.toFixed(2);
        }
    }

    function truncate(str, maxLen) {
        if (str.length <= maxLen) return str;
        return str.substring(0, maxLen - 3) + '...';
    }

    function renderSummary() {
        if (!state.results || !state.results.summary) {
            elements.matrixSummary.style.display = 'none';
            return;
        }

        const summary = state.results.summary;

        // Best attackers
        elements.bestAttackers.innerHTML = summary.best_attackers.map(item => `
            <li>
                <strong>${item.defender}:</strong>
                ${item.attacker || 'N/A'} (${item.efficiency ? item.efficiency.toFixed(3) : '-'} dmg/pt)
            </li>
        `).join('');

        // Biggest threats
        elements.biggestThreats.innerHTML = summary.biggest_threats.map(item => `
            <li>
                <strong>${item.defender}:</strong>
                ${item.attacker || 'N/A'} (${item.damage ? item.damage.toFixed(2) : '-'} avg dmg)
            </li>
        `).join('');

        elements.matrixSummary.style.display = 'block';
    }

    // =========================
    // Cell Detail Panel
    // =========================

    async function showCellDetail(atkIdx, defIdx) {
        const atk = state.attackers[atkIdx];
        const def = state.defenders[defIdx];

        // Show panel with loading state
        elements.detailPanel.style.display = 'block';
        document.getElementById('detail-title').textContent = 'Loading...';
        document.getElementById('detail-attacker').textContent = atk.label;
        document.getElementById('detail-defender').textContent = def.label;

        try {
            const response = await fetch('/api/matrix/cell', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    weapon_id: atk.weapon_id,
                    weapon_type: atk.weapon_type,
                    unit_id: def.unit_id,
                    model_count: def.model_count,
                    conditions: state.conditions,
                    iterations: 1000
                })
            });

            const data = await response.json();

            document.getElementById('detail-title').textContent = 'Matchup Details';
            document.getElementById('detail-avg-damage').textContent = data.summary.average_damage.toFixed(2);
            document.getElementById('detail-models-killed').textContent = data.summary.models_killed.toFixed(2);
            document.getElementById('detail-attacks').textContent = data.breakdown.attacks.toFixed(2);
            document.getElementById('detail-hits').textContent = data.breakdown.hits.toFixed(2);
            document.getElementById('detail-wounds').textContent = data.breakdown.wounds.toFixed(2);
            document.getElementById('detail-failed-saves').textContent = data.breakdown.failed_saves.toFixed(2);

            // Render distribution chart
            renderDetailChart(data.distribution);

        } catch (err) {
            console.error('Error loading cell detail:', err);
            document.getElementById('detail-title').textContent = 'Error loading details';
        }
    }

    function renderDetailChart(distribution) {
        if (!distribution || Object.keys(distribution).length === 0) {
            document.getElementById('detail-chart').innerHTML = '<p>No distribution data</p>';
            return;
        }

        const damages = Object.keys(distribution).map(Number).sort((a, b) => a - b);
        const probabilities = damages.map(d => distribution[d] * 100);

        Plotly.newPlot('detail-chart', [{
            x: damages,
            y: probabilities,
            type: 'bar',
            marker: {
                color: '#4fc3f7'
            }
        }], {
            margin: { t: 10, r: 10, b: 30, l: 40 },
            paper_bgcolor: 'transparent',
            plot_bgcolor: 'transparent',
            font: { color: '#e0e0e0' },
            xaxis: {
                title: 'Damage',
                gridcolor: 'rgba(255,255,255,0.1)'
            },
            yaxis: {
                title: '%',
                gridcolor: 'rgba(255,255,255,0.1)'
            }
        }, {
            responsive: true,
            displayModeBar: false
        });
    }

    // =========================
    // Save/Load Comparisons
    // =========================

    function showSaveModal() {
        if (state.attackers.length === 0 || state.defenders.length === 0) {
            alert('Add attackers and defenders before saving.');
            return;
        }
        document.getElementById('comparison-name').value = '';
        elements.saveModal.style.display = 'flex';
    }

    async function saveComparison() {
        const name = document.getElementById('comparison-name').value.trim();
        if (!name) {
            alert('Please enter a name for the comparison.');
            return;
        }

        try {
            await fetch('/api/comparisons', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    attackers: state.attackers,
                    defenders: state.defenders,
                    conditions: state.conditions,
                    results: state.results
                })
            });

            elements.saveModal.style.display = 'none';
            alert('Comparison saved!');
            loadSavedComparisons();

        } catch (err) {
            console.error('Error saving comparison:', err);
            alert('Error saving comparison.');
        }
    }

    function showLoadModal() {
        loadSavedComparisons();
        elements.loadModal.style.display = 'flex';
    }

    async function loadSavedComparisons() {
        try {
            const response = await fetch('/api/comparisons');
            const comparisons = await response.json();

            if (comparisons.length === 0) {
                elements.comparisonList.innerHTML = '<p class="empty-message">No saved comparisons</p>';
                return;
            }

            elements.comparisonList.innerHTML = comparisons.map(c => `
                <div class="comparison-item" data-id="${c.id}">
                    <div class="comparison-info">
                        <span class="comparison-name">${c.name}</span>
                        <span class="comparison-meta">${c.attacker_count} attackers vs ${c.defender_count} defenders</span>
                    </div>
                    <div class="comparison-actions">
                        <button class="btn btn-small btn-load" data-id="${c.id}">Load</button>
                        <button class="btn btn-small btn-danger btn-delete" data-id="${c.id}">Delete</button>
                    </div>
                </div>
            `).join('');

            // Bind events
            elements.comparisonList.querySelectorAll('.btn-load').forEach(btn => {
                btn.addEventListener('click', () => loadComparison(parseInt(btn.dataset.id)));
            });
            elements.comparisonList.querySelectorAll('.btn-delete').forEach(btn => {
                btn.addEventListener('click', () => deleteComparison(parseInt(btn.dataset.id)));
            });

        } catch (err) {
            console.error('Error loading comparisons:', err);
        }
    }

    async function loadComparison(id) {
        try {
            const response = await fetch(`/api/comparisons/${id}`);
            const data = await response.json();

            state.attackers = data.attackers || [];
            state.defenders = data.defenders || [];
            state.conditions = data.battle_conditions || state.conditions;
            state.results = data.results;

            // Update UI
            renderAttackerList();
            renderDefenderList();
            updateConditionUI();

            if (state.results) {
                renderMatrix();
                renderSummary();
            }

            elements.loadModal.style.display = 'none';

        } catch (err) {
            console.error('Error loading comparison:', err);
            alert('Error loading comparison.');
        }
    }

    async function deleteComparison(id) {
        if (!confirm('Delete this comparison?')) return;

        try {
            await fetch(`/api/comparisons/${id}`, { method: 'DELETE' });
            loadSavedComparisons();
        } catch (err) {
            console.error('Error deleting comparison:', err);
        }
    }

    function updateConditionUI() {
        document.getElementById('mat-half-range').checked = state.conditions.half_range;
        document.getElementById('mat-charged').checked = state.conditions.charged;
        document.getElementById('mat-stationary').checked = state.conditions.stationary;
        document.getElementById('mat-cover').checked = state.conditions.cover;
        document.getElementById('mat-reroll-hits').value = state.conditions.reroll_hits;
        document.getElementById('mat-reroll-wounds').value = state.conditions.reroll_wounds;
    }

    // =========================
    // Utilities
    // =========================

    function debounce(fn, delay) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => fn.apply(this, args), delay);
        };
    }

})();
