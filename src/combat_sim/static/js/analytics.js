/**
 * Analytics page (formerly Matchups): the attacker x defender matrix and Army Analysis.
 *
 * Attackers are unit entries (the same unit may appear several times, labelled
 * "#2", "#3"...), each holding its weapons with an include tick and a model count.
 * The matrix has one row per attacker unit plus an "All attackers" total row.
 */

(function() {
    'use strict';

    // Pure helpers shared with the other pages and unit-tested in Node (static/js/core.js)
    const { escapeHtml, truncate, formatMetricValue, getHeatColor, copyLabel, pointsLabel } = Core;

    const DEFAULT_DEFENDER_MODELS = 5;

    // State
    const state = {
        // [{unit_id, unit_name, faction, weapons: [{weapon_id, weapon_type, name, range, attacks,
        //   skill, strength, ap, damage, abilities, models, include, leader}], leaders, leader_id, leader_name,
        //   leader_points}] (an attached leader's weapons are in `weapons`, marked leader: true)
        attackers: [],
        // [{unit_id, unit_name, faction, toughness, save, wounds, invulnerable_save, feel_no_pain, model_count,
        //   leaders, leader_id}] (a leader's buffs apply to the defender; its stats aren't part of it yet)
        defenders: [],
        results: null,
        currentMetric: 'avg_damage',
        weaponTypeFilter: 'both',  // 'ranged', 'melee', or 'both'
        showWeapons: false,
        analysisTab: 'summary',
        defenderMode: 'real',  // 'real' (defender table) or 'generic' (benchmark profiles)
        genericProfiles: [],  // read-only, from /api/data/target-profiles
        genericIncluded: null,  // Set of included profile ids (null = each profile's default)
        // Rules & stratagems each side can tick, which are ticked ('rule:3', 'stratagem:7'), and the
        // side's detachment (BSData key; '' = faction-wide rules only) from its factions' detachments
        rules: {
            attacker: { available: [], ticked: new Set(), factions: '', detachment: '', detachments: [] },
            defender: { available: [], ticked: new Set(), factions: '', detachment: '', detachments: [] },
        },
        pickerMode: 'attacker',  // which table the Add Unit pop-up adds to
        conditions: {
            half_range: false,
            charged: false,
            stationary: false,
            cover: false,
            damaged: false,
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
        document.querySelectorAll('.defender-mode-toggle .toggle-btn').forEach(btn => {
            btn.addEventListener('click', () => setDefenderMode(btn.dataset.mode));
        });
        document.querySelectorAll('.analysis-tabs .toggle-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                state.analysisTab = btn.dataset.tab;
                renderAnalysis();
            });
        });
        loadGenericProfiles();
        populateFactionFilter();
        restorePageState();
        renderAttackerTable();
        renderDefenderTable();
        loadSavedComparisons();
        window.addEventListener('pagehide', savePageState);
    }

    // =========================
    // Remember setup across tab changes (see PageState in app.js)
    // =========================

    const PAGE_STATE_KEY = 'analyticsState';

    function savePageState() {
        PageState.save(PAGE_STATE_KEY, {
            attackers: state.attackers,
            defenders: state.defenders,
            conditions: state.conditions,
            currentMetric: state.currentMetric,
            weaponTypeFilter: state.weaponTypeFilter,
            showWeapons: state.showWeapons,
            results: state.results,
            rulesTicked: ticksForSaving(),
            defenderMode: state.defenderMode,
            genericIncluded: state.genericIncluded ? [...state.genericIncluded] : null,
        });
    }

    function restorePageState() {
        const saved = PageState.load(PAGE_STATE_KEY);
        if (!saved) return;

        state.attackers = saved.attackers || [];
        state.defenders = saved.defenders || [];
        state.conditions = { ...state.conditions, ...(saved.conditions || {}) };
        state.currentMetric = saved.currentMetric || state.currentMetric;
        state.weaponTypeFilter = saved.weaponTypeFilter || state.weaponTypeFilter;
        state.showWeapons = !!saved.showWeapons;
        state.results = saved.results || null;
        restoreTicks(saved.rulesTicked);
        state.defenderMode = saved.defenderMode === 'generic' ? 'generic' : 'real';
        state.genericIncluded = saved.genericIncluded ? new Set(saved.genericIncluded) : null;

        updateConditionUI();
        elements.metricSelect.value = state.currentMetric;
        document.querySelectorAll('.weapon-type-toggle .toggle-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.type === state.weaponTypeFilter);
        });

        if (state.results) {
            renderMatrix();
            renderSummary();
        }
    }

    function cacheElements() {
        elements.attackerBody = document.querySelector('#attacker-table tbody');
        elements.defenderBody = document.querySelector('#defender-table tbody');

        elements.matrixContainer = document.getElementById('matrix-container');
        elements.matrixSummary = document.getElementById('matrix-summary');
        elements.bestAttackers = document.getElementById('best-attackers');
        elements.biggestThreats = document.getElementById('biggest-threats');

        elements.detailPanel = document.getElementById('detail-panel');
        elements.metricSelect = document.getElementById('mat-metric');

        elements.unitPickerModal = document.getElementById('unit-picker-modal');
        elements.unitPickerSearch = document.getElementById('unit-picker-search');
        elements.unitPickerFaction = document.getElementById('unit-picker-faction');
        elements.unitPickerList = document.getElementById('unit-picker-list');

        elements.loadModal = document.getElementById('load-modal');
        elements.saveModal = document.getElementById('save-modal');
        elements.comparisonList = document.getElementById('comparison-list');
    }

    function bindEvents() {
        // Weapon type filter (live: hidden weapons are left out of the matrix)
        document.querySelectorAll('.weapon-type-toggle .toggle-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.weapon-type-toggle .toggle-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                state.weaponTypeFilter = btn.dataset.type;
                renderAttackerTable();
                renderRulesList('attacker');
                renderRulesList('defender');
            });
        });

        // Add Unit pop-up
        document.getElementById('btn-add-attacker').addEventListener('click', () => openUnitPicker('attacker'));
        document.getElementById('btn-add-defender').addEventListener('click', () => openUnitPicker('defender'));
        document.getElementById('close-unit-picker').addEventListener('click', closeUnitPicker);
        elements.unitPickerSearch.addEventListener('input', debounce(loadUnitPickerList, 250));
        elements.unitPickerFaction.addEventListener('change', loadUnitPickerList);
        document.getElementById('unit-picker-legends').addEventListener('change', loadUnitPickerList);
        ['attacker', 'defender'].forEach(side => {
            document.getElementById(`${side}-detachment`).addEventListener('change', (e) => {
                state.rules[side].detachment = e.target.value;
                state.rules[side].factions = '';  // force a reload of the offered rules
                refreshRules(side);
            });
        });

        // Table edits (event delegation, rows are re-rendered)
        elements.attackerBody.addEventListener('change', onAttackerTableChange);
        elements.attackerBody.addEventListener('click', onAttackerTableClick);
        elements.defenderBody.addEventListener('change', onDefenderTableChange);
        elements.defenderBody.addEventListener('input', (e) => {
            if (!e.target.classList.contains('def-extra')) return;
            state.defenders[parseInt(e.target.dataset.d)].extra_modifiers = e.target.value;
        });
        elements.defenderBody.addEventListener('click', onDefenderTableClick);

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
        document.getElementById('mat-damaged').addEventListener('change', (e) => {
            state.conditions.damaged = e.target.checked;
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
        [elements.loadModal, elements.saveModal, elements.unitPickerModal].forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.style.display = 'none';
                }
            });
        });
    }

    // =========================
    // Rules & stratagems (army-wide)
    // =========================

    const RULE_KIND_LABEL = { rule: 'Rule', stratagem: 'Stratagem', core: 'Core' };

    function sideEntries(side) {
        if (side === 'attacker') return state.attackers;
        return state.defenderMode === 'generic' ? [] : state.defenders;
    }

    /** Reload a side's detachments and tickable list when the factions in its table change. */
    async function refreshRules(side) {
        const panel = state.rules[side];
        const factionIds = [...new Set(sideEntries(side).map(e => e.faction_id).filter(Boolean))].sort((a, b) => a - b);
        const signature = factionIds.join(',') + '|' + panel.detachment;
        if (signature === panel.factions && panel.available.length) {
            renderRulesList(side);
            return;
        }
        panel.factions = signature;

        const params = new URLSearchParams({ side: side === 'attacker' ? 'Attacker' : 'Target' });
        factionIds.forEach(id => params.append('faction_id', id));
        try {
            panel.detachments = factionIds.length
                ? await fetch(`/api/detachments?${params}`).then(r => r.json())
                : [];
            if (!panel.detachments.some(d => d.key === panel.detachment)) panel.detachment = '';
            const select = document.getElementById(`${side}-detachment`);
            select.innerHTML = Core.detachmentOptionsHtml(panel.detachments, panel.detachment);
            select.style.display = panel.detachments.length ? '' : 'none';
            if (panel.detachment) params.append('detachment', panel.detachment);

            const available = await fetch(`/api/rules/available?${params}`).then(r => r.json());
            if (panel.factions !== factionIds.join(',') + '|' + panel.detachment) return;  // a newer refresh started
            const keys = new Set(available.map(e => e.key));
            panel.available = available;
            panel.ticked = new Set([...panel.ticked].filter(k => keys.has(k)));
            renderRulesList(side);
        } catch (err) {
            console.error('Error loading rules:', err);
        }
    }

    /** Entries usable with the current Shooting / Both / Melee filter. */
    function visibleRules(side) {
        return Core.rulesForFilter(state.rules[side].available, state.weaponTypeFilter);
    }

    function renderRulesList(side) {
        const listEl = document.getElementById(`${side}-rules-list`);
        if (!listEl) return;
        const panel = state.rules[side];
        const visible = visibleRules(side);

        // Group by faction (core stratagems last)
        const groups = new Map();
        for (const e of visible) {
            const title = e.kind === 'core' ? 'Core stratagems' : e.faction;
            if (!groups.has(title)) groups.set(title, []);
            groups.get(title).push(e);
        }
        const titles = [...groups.keys()].sort((a, b) =>
            (a === 'Core stratagems') - (b === 'Core stratagems') || a.localeCompare(b));

        listEl.innerHTML = titles.length ? titles.map(title => `
            <div class="rules-group-title">${escapeHtml(title)}</div>
            ${groups.get(title).map(e => `
                <label class="rule-row${e.has_effect ? '' : ' rule-no-effect'}"
                       title="${e.has_effect ? 'Changes the calculations' : 'No effect on the calculations (listed for reference)'}">
                    <input type="checkbox" data-key="${e.key}" ${panel.ticked.has(e.key) ? 'checked' : ''}>
                    <span class="rule-name">${escapeHtml(e.name)}</span>
                    ${e.detachment ? `<span class="rule-detachment">${escapeHtml(e.detachment)}</span>` : ''}
                    <span class="rule-kind">${RULE_KIND_LABEL[e.kind]}</span>
                    <span class="rule-phase">${escapeHtml(e.phase)}</span>
                    ${e.cp ? `<span class="rule-cp">${escapeHtml(e.cp)} CP</span>` : ''}
                    <span class="rule-modifier">${e.has_effect ? escapeHtml(e.modifier) : 'No effect on the calculations'}</span>
                </label>`).join('')}
        `).join('') : `<div class="rules-empty">${sideEntries(side).length ? 'No rules or stratagems for this phase' : 'Add units to see their rules and stratagems'}</div>`;

        listEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', () => {
                cb.checked ? panel.ticked.add(cb.dataset.key) : panel.ticked.delete(cb.dataset.key);
                updateRulesSummary(side);
            });
        });
        updateRulesSummary(side);
    }

    function updateRulesSummary(side) {
        const summaryEl = document.getElementById(`${side}-rules-summary`);
        if (!summaryEl) return;
        const active = visibleRules(side).filter(e => state.rules[side].ticked.has(e.key) && e.has_effect);
        summaryEl.textContent = active.length
            ? 'Active: ' + active.map(e => `${e.name} (${e.modifier})`).join(', ')
            : (state.rules[side].ticked.size ? 'Ticked, but no effect with this filter' : 'None ticked');
        summaryEl.classList.toggle('rules-summary-active', active.length > 0);
    }

    function ticksForSaving() {
        return {
            attacker: [...state.rules.attacker.ticked], defender: [...state.rules.defender.ticked],
            detachments: { attacker: state.rules.attacker.detachment, defender: state.rules.defender.detachment },
        };
    }

    function restoreTicks(saved) {
        state.rules.attacker.ticked = new Set(saved?.attacker || []);
        state.rules.defender.ticked = new Set(saved?.defender || []);
        state.rules.attacker.detachment = saved?.detachments?.attacker || '';
        state.rules.defender.detachment = saved?.detachments?.defender || '';
        state.rules.attacker.factions = '';  // force a reload; ticks are kept if still available
        state.rules.defender.factions = '';
    }

    // =========================
    // Helpers
    // =========================





    const toWeaponEntry = w => Core.matchupWeaponEntry(w);


    function toDefenderEntry(unit, modelCount) {
        return {
            leaders: unit.leaders || [],
            leader_id: null,
            unit_id: unit.id,
            unit_name: unit.name,
            faction: unit.faction,
            faction_id: unit.faction_id,
            points_table: unit.points_by_model_count || [],
            toughness: unit.toughness,
            save: unit.save,
            wounds: unit.wounds,
            invulnerable_save: unit.invulnerable_save,
            feel_no_pain: unit.feel_no_pain,
            model_count: modelCount,
        };
    }

    function populateFactionFilter() {
        const factions = window.APP_DATA?.factions || [];
        elements.unitPickerFaction.innerHTML = '<option value="">All Factions</option>' +
            factions.map(f => `<option value="${f.id}">${escapeHtml(f.name)}</option>`).join('');
    }

    // =========================
    // Add Unit pop-up
    // =========================

    function openUnitPicker(mode) {
        state.pickerMode = mode;
        document.getElementById('unit-picker-title').textContent =
            mode === 'attacker' ? 'Add Attacker Unit' : 'Add Defender Unit';
        elements.unitPickerSearch.value = '';
        elements.unitPickerModal.style.display = 'flex';
        elements.unitPickerSearch.focus();
        loadUnitPickerList();
    }

    function closeUnitPicker() {
        elements.unitPickerModal.style.display = 'none';
    }

    async function loadUnitPickerList() {
        const params = new URLSearchParams();
        const search = elements.unitPickerSearch.value.trim();
        if (search) params.set('search', search);
        if (elements.unitPickerFaction.value) params.set('faction_id', elements.unitPickerFaction.value);
        if (document.getElementById('unit-picker-legends').checked) params.set('legends', '1');

        try {
            const response = await fetch(`/api/units?${params}`);
            const units = await response.json();

            if (units.length === 0) {
                elements.unitPickerList.innerHTML = '<p class="empty-message">No units found</p>';
                return;
            }

            elements.unitPickerList.innerHTML = units.map(u => `
                <button type="button" class="unit-picker-item" data-unit-id="${u.id}">
                    <span class="unit-picker-name">${escapeHtml(u.name)}</span>
                    <span class="unit-picker-faction">${escapeHtml(u.faction || '')}</span>
                    <span class="unit-picker-stats">T${u.toughness} · ${u.save}+ · W${u.wounds}${u.invulnerable_save ? ` · ${u.invulnerable_save}++` : ''}</span>
                </button>
            `).join('');

            elements.unitPickerList.querySelectorAll('.unit-picker-item').forEach(item => {
                item.addEventListener('click', () => addUnit(parseInt(item.dataset.unitId)));
            });
        } catch (err) {
            console.error('Error searching units:', err);
        }
    }

    async function addUnit(unitId) {
        try {
            const response = await fetch(`/api/units/${unitId}`);
            const unit = await response.json();

            if (state.pickerMode === 'attacker') {
                state.attackers.push({
                    unit_id: unit.id,
                    unit_name: unit.name,
                    faction: unit.faction,
                    faction_id: unit.faction_id,
                    default_models: unit.default_models || null,
                    unit_size: unit.default_models || null,
                    points_table: unit.points_by_model_count || [],
                    weapons: Core.matchupWeapons(unit),  // Loadout, then wargear options (not included)
                    leaders: unit.leaders || [],
                    leader_id: null,
                });
                renderAttackerTable();
            } else {
                state.defenders.push(toDefenderEntry(unit, unit.default_models || DEFAULT_DEFENDER_MODELS));
                renderDefenderTable();
            }
            closeUnitPicker();
        } catch (err) {
            console.error('Error adding unit:', err);
            alert('Error loading unit');
        }
    }

    // =========================
    // Attacker / Defender tables
    // =========================

    function renderAttackerTable() {
        refreshRules('attacker');
        if (state.attackers.length === 0) {
            elements.attackerBody.innerHTML =
                '<tr class="roster-empty"><td colspan="13">No attackers yet. Click "+ Add Unit".</td></tr>';
            return;
        }

        const typeName = { ranged: 'shooting', melee: 'melee' };
        let html = '';

        state.attackers.forEach((atk, a) => {
            const label = escapeHtml(copyLabel(state.attackers, a));
            const visible = atk.weapons.map((w, idx) => ({ w, idx })).filter(({ w }) => Core.weaponVisible(w, state.weaponTypeFilter));
            const unitCell = (rowspan) => `
                <td class="roster-unit" rowspan="${rowspan}">
                    <span class="roster-unit-name">${label}</span>
                    <span class="roster-unit-faction">${escapeHtml(atk.faction || '')}</span>
                    ${atk.default_models ? `
                    <label class="unit-size" title="Unit size: scales points and weapon quantities">
                        Size <input type="number" class="roster-models unit-size-input" data-a="${a}"
                                    value="${atk.unit_size || atk.default_models}" min="1" max="99">
                    </label>` : ''}
                    <span class="unit-points">${unitPointsLabel(atk)}</span>
                    ${leaderSelect(atk.leaders, atk.leader_id, `data-a="${a}"`, 'atk-leader')}
                </td>`;
            const removeUnit = `<button class="btn-remove remove-unit" data-a="${a}" title="Remove unit"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>`;

            if (visible.length === 0) {
                const msg = atk.weapons.length === 0
                    ? 'This unit has no weapons'
                    : `No ${typeName[state.weaponTypeFilter]} weapons`;
                html += `<tr class="roster-group-start">
                    <td></td>${unitCell(1)}
                    <td colspan="10" class="roster-muted">${msg}</td>
                    <td class="col-remove">${removeUnit}</td>
                </tr>`;
                return;
            }

            visible.forEach(({ w, idx }, i) => {
                const isRanged = w.weapon_type === 'ranged';
                html += `<tr class="${i === 0 ? 'roster-group-start' : ''} ${w.include ? '' : 'excluded'}">
                    <td class="col-check">
                        <input type="checkbox" class="inc" data-a="${a}" data-w="${idx}" ${w.include ? 'checked' : ''}
                               title="Include in matrix">
                    </td>
                    ${i === 0 ? unitCell(visible.length) : ''}
                    <td class="roster-weapon">${escapeHtml(w.name)}${w.option ? ' <span class="option-tag" title="Wargear option: tick to include it">option</span>' : ''}${w.leader ? ` <span class="option-tag" title="${escapeHtml(w.leader_name)}'s weapon">leader</span>` : ''}</td>
                    <td class="roster-type" title="${isRanged ? 'Shooting' : 'Melee'}">${isRanged ? '<i class="fa-solid fa-crosshairs" aria-hidden="true"></i>' : '<i class="fa-solid fa-hand-fist" aria-hidden="true"></i>'}</td>
                    <td>${isRanged ? (w.range ? escapeHtml(w.range) + '"' : '-') : 'Melee'}</td>
                    <td>${escapeHtml(w.attacks)}</td>
                    <td>${w.skill ? escapeHtml(w.skill) + '+' : '-'}</td>
                    <td>${escapeHtml(w.strength)}</td>
                    <td>${w.ap ? '-' + escapeHtml(w.ap) : '0'}</td>
                    <td>${escapeHtml(w.damage)}</td>
                    <td class="roster-abilities">${escapeHtml((w.abilities || []).join(', '))}</td>
                    <td><input type="number" class="roster-models models" data-a="${a}" data-w="${idx}"
                               value="${w.models}" min="1" max="99"></td>
                    <td class="col-remove">${i === 0 ? removeUnit : ''}</td>
                </tr>`;
            });
        });

        elements.attackerBody.innerHTML = html;
    }

    /** Points of the unit at its size, plus its leader's. */
    function unitPointsLabel(atk) {
        const label = pointsLabel(atk.points_table, atk.unit_size || atk.default_models);
        return atk.leader_points ? `${label} + ${atk.leader_points} leader` : label;
    }

    /** A "Leader" dropdown for a table row ('' when no leader can join the unit). */
    function leaderSelect(leaders, selectedId, dataAttr, cls) {
        if (!leaders?.length) return '';
        const chosen = leaders.find(l => l.id === selectedId);
        const summary = chosen ? Core.leaderBuffSummary(chosen.leader_buffs) : '';
        return `<select class="roster-leader ${cls}" ${dataAttr} title="${escapeHtml(summary ? 'Grants: ' + summary : 'Attach a leader')}">
            <option value="">No leader</option>
            ${leaders.map(l => `<option value="${l.id}" ${l.id === selectedId ? 'selected' : ''}>${escapeHtml(l.name)}</option>`).join('')}
        </select>${chosen ? `<span class="leader-buffs">${escapeHtml(summary || 'nothing the maths uses')}</span>` : ''}`;
    }

    /** Attach a leader to an attacker unit (its weapons and points join the unit) or detach it. */
    async function setAttackerLeader(a, leaderId) {
        const atk = state.attackers[a];
        const choice = (atk.leaders || []).find(l => l.id === leaderId) || null;
        const detail = choice ? await fetch(`/api/units/${choice.id}`).then(r => r.json()) : null;
        atk.weapons = Core.withLeaderEntries(atk.weapons, detail);
        atk.leader_id = choice?.id ?? null;
        atk.leader_name = choice?.name ?? null;
        atk.leader_points = choice?.points ?? null;
        renderAttackerTable();
    }

    function onAttackerTableChange(e) {
        const el = e.target;
        if (el.classList.contains('atk-leader')) {
            setAttackerLeader(parseInt(el.dataset.a), el.value ? parseInt(el.value) : null);
            return;
        }
        if (el.classList.contains('unit-size-input')) {
            Core.scaleUnitSize(state.attackers[parseInt(el.dataset.a)], parseInt(el.value));
            renderAttackerTable();
            return;
        }
        const a = parseInt(el.dataset.a);
        const w = parseInt(el.dataset.w);
        if (isNaN(a) || isNaN(w)) return;
        const weapon = state.attackers[a].weapons[w];

        if (el.classList.contains('inc')) {
            weapon.include = el.checked;
            el.closest('tr').classList.toggle('excluded', !el.checked);
        } else if (el.classList.contains('models')) {
            weapon.models = Math.max(1, parseInt(el.value) || 1);
            el.value = weapon.models;
        }
    }

    function onAttackerTableClick(e) {
        const btn = e.target.closest('.remove-unit');
        if (!btn) return;
        state.attackers.splice(parseInt(btn.dataset.a), 1);
        renderAttackerTable();
    }

    function renderDefenderTable() {
        refreshRules('defender');
        document.querySelectorAll('.defender-mode-toggle .toggle-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === state.defenderMode);
        });
        document.getElementById('btn-add-defender').style.display = state.defenderMode === 'generic' ? 'none' : '';
        const head = document.querySelector('#defender-table thead');

        if (state.defenderMode === 'generic') {
            head.innerHTML = `<tr><th class="col-check" title="Include"></th><th>Profile</th><th>Keywords</th>
                <th>T</th><th>Sv</th><th>W</th><th>Inv</th><th>FNP</th><th>Models</th><th>Weight</th></tr>`;
            const included = genericIncludedSet();
            elements.defenderBody.innerHTML = state.genericProfiles.length ? state.genericProfiles.map(t => `
                <tr class="${included.has(t.id) ? '' : 'excluded'}">
                    <td class="col-check"><input type="checkbox" class="profile-inc" data-id="${t.id}" ${included.has(t.id) ? 'checked' : ''}></td>
                    <td class="roster-unit-name">${escapeHtml(t.name)}</td>
                    <td class="roster-muted">${escapeHtml((t.keywords || []).join(', '))}</td>
                    <td>${t.toughness}</td>
                    <td>${t.save}+</td>
                    <td>${t.wounds}</td>
                    <td>${t.invulnerable_save ? t.invulnerable_save + '+' : '-'}</td>
                    <td>${t.feel_no_pain ? t.feel_no_pain + '+' : '-'}</td>
                    <td>${t.models}</td>
                    <td>${t.weight ?? '-'}</td>
                </tr>
            `).join('') : '<tr class="roster-empty"><td colspan="10">No generic profiles in the data file.</td></tr>';
            return;
        }

        head.innerHTML = `<tr><th>Unit</th><th>Faction</th><th>T</th><th>Sv</th><th>W</th><th>Inv</th><th>FNP</th>
            <th>Models</th><th>Pts</th><th title="One-off target modifiers for this defender only">Extra modifiers</th>
            <th class="col-remove"></th></tr>`;
        if (state.defenders.length === 0) {
            elements.defenderBody.innerHTML =
                '<tr class="roster-empty"><td colspan="11">No defenders yet. Click "+ Add Unit".</td></tr>';
            return;
        }

        elements.defenderBody.innerHTML = state.defenders.map((d, idx) => `
            <tr>
                <td class="roster-unit-name">${escapeHtml(copyLabel(state.defenders, idx))}
                    ${leaderSelect(d.leaders, d.leader_id, `data-d="${idx}"`, 'def-leader')}</td>
                <td class="roster-muted">${escapeHtml(d.faction || '')}</td>
                <td>${d.toughness}</td>
                <td>${d.save}+</td>
                <td>${d.wounds}</td>
                <td>${d.invulnerable_save ? d.invulnerable_save + '+' : '-'}</td>
                <td>${d.feel_no_pain ? d.feel_no_pain + '+' : '-'}</td>
                <td><input type="number" class="roster-models def-models" data-d="${idx}"
                           value="${d.model_count}" min="1" max="99"></td>
                <td class="roster-muted def-points">${pointsLabel(d.points_table, d.model_count)}</td>
                <td class="roster-extra">
                    <input type="text" class="roster-text def-extra" data-d="${idx}" value="${escapeHtml(d.extra_modifiers || '')}"
                           placeholder="e.g. Stealth" autocomplete="off" title="For this defender only; not saved to the datasheet">
                    <div class="kw-hint" id="def-extra-hint-${idx}"></div>
                </td>
                <td class="col-remove"><button class="btn-remove remove-defender" data-d="${idx}" title="Remove"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button></td>
            </tr>
        `).join('');

        elements.defenderBody.querySelectorAll('.def-extra').forEach(input => {
            KeywordHints.attach(input, document.getElementById(`def-extra-hint-${input.dataset.d}`), 'unit');
        });
    }

    // =========================
    // Unit size, points and generic profiles
    // =========================




    async function loadGenericProfiles() {
        try {
            const profiles = await fetch('/api/data/target-profiles').then(r => r.json());
            state.genericProfiles = profiles.filter(t => t.kind === 'generic');
            if (state.defenderMode === 'generic') renderDefenderTable();
        } catch (err) {
            console.error('Error loading target profiles:', err);
        }
    }

    function genericIncludedSet() {
        if (!state.genericIncluded) {
            state.genericIncluded = new Set(state.genericProfiles.filter(t => t.use).map(t => t.id));
        }
        return state.genericIncluded;
    }

    function includedProfiles() {
        const included = genericIncludedSet();
        return state.genericProfiles.filter(t => included.has(t.id));
    }

    function setDefenderMode(mode) {
        state.defenderMode = mode;
        state.rules.defender.factions = null;  // reload the defender rules for the new mode
        renderDefenderTable();
    }

    function onDefenderTableChange(e) {
        if (e.target.classList.contains('def-leader')) {
            const defender = state.defenders[parseInt(e.target.dataset.d)];
            defender.leader_id = e.target.value ? parseInt(e.target.value) : null;
            defender.leader_name = defender.leaders.find(l => l.id === defender.leader_id)?.name || null;
            renderDefenderTable();
            return;
        }
        const el = e.target;
        if (el.classList.contains('profile-inc')) {
            const set = genericIncludedSet();
            el.checked ? set.add(parseInt(el.dataset.id)) : set.delete(parseInt(el.dataset.id));
            el.closest('tr').classList.toggle('excluded', !el.checked);
            return;
        }
        if (!el.classList.contains('def-models')) return;
        const d = state.defenders[parseInt(el.dataset.d)];
        d.model_count = Math.max(1, parseInt(el.value) || 1);
        el.value = d.model_count;
        const pointsCell = el.closest('tr').querySelector('.def-points');
        if (pointsCell) pointsCell.textContent = pointsLabel(d.points_table, d.model_count);
    }

    function onDefenderTableClick(e) {
        const btn = e.target.closest('.remove-defender');
        if (!btn) return;
        state.defenders.splice(parseInt(btn.dataset.d), 1);
        renderDefenderTable();
    }

    // =========================
    // Matrix Simulation
    // =========================

    function buildMatrixRequest() {
        return Core.buildMatrixRequest({
            attackers: state.attackers,
            defenders: state.defenders,
            conditions: state.conditions,
            weaponTypeFilter: state.weaponTypeFilter,
            defenderMode: state.defenderMode,
            includedProfiles: includedProfiles(),
            attackerTicked: state.rules.attacker.ticked,
            defenderTicked: state.rules.defender.ticked,
        });
    }

    async function runMatrix() {
        const request = buildMatrixRequest();

        if (request.attackers.length === 0 || request.defenders.length === 0) {
            alert('Please add at least one attacker unit with an included weapon, and one defender unit.');
            return;
        }

        // Show loading state
        elements.matrixContainer.innerHTML = '<div class="matrix-loading">Running simulations...</div>';

        try {
            const response = await fetch('/api/matrix/simulate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(request)
            });

            state.results = await response.json();
            // Keep what was run so cell details match the matrix even after later edits
            state.results.request = request;
            renderMatrix();
            renderSummary();

        } catch (err) {
            console.error('Error running matrix:', err);
            elements.matrixContainer.innerHTML = '<div class="matrix-error">Error running simulation. Please try again.</div>';
        }
    }

    function renderMatrix() {
        const results = state.results;
        if (!results || !results.matrix || !results.matrix.length) {
            return;
        }

        const metric = state.currentMetric;
        const { matrix, weapon_matrix: weaponMatrix, attackers, defenders, total } = results;

        // Colour scale from the unit rows (the total row would always dominate)
        let minVal = Infinity, maxVal = -Infinity;
        for (const row of matrix) {
            for (const cell of row) {
                const val = cell[metric] || 0;
                minVal = Math.min(minVal, val);
                maxVal = Math.max(maxVal, val);
            }
        }

        const cellHtml = (cell, kind, a, w, d, rowLabel) => {
            const value = cell[metric] || 0;
            const displayVal = formatMetricValue(value, metric);
            const style = kind === 'total' ? '' : `style="background-color: ${getHeatColor(Math.min(value, maxVal), minVal, maxVal)}"`;
            const wiped = kind === 'total' && cell.wiped ? '<span class="wiped-badge" title="Wiped in one turn"><i class="fa-solid fa-check" aria-hidden="true"></i> wiped</span>' : '';
            return `<td class="matrix-cell ${kind === 'total' ? 'matrix-total-cell' : ''}" ${style}
                        data-kind="${kind}" data-a="${a}" data-w="${w}" data-d="${d}"
                        title="${escapeHtml(rowLabel)} vs ${escapeHtml(defenders[d].label)}: ${displayVal}${cell.leader_killed != null
                            ? ` (${cell.bodyguards_killed} bodyguards killed; expected damage to the leader ${cell.leader_damage} of ${cell.leader_wounds} wounds)` : ''}">
                        ${displayVal}${wiped}
                    </td>`;
        };

        let html = `<div class="matrix-toolbar">
            <label class="toggle-inline">
                <input type="checkbox" id="matrix-show-weapons" ${state.showWeapons ? 'checked' : ''}> Show weapons
            </label>
        </div>`;
        html += '<div class="matrix-scroll"><table class="matrix-table">';

        // Header row
        html += '<thead><tr><th class="matrix-corner"></th>';
        for (const def of defenders) {
            const weight = def.weight != null ? `<span class="matrix-weight">weight ${def.weight}</span>` : '';
            html += `<th class="matrix-header-cell" title="${escapeHtml(def.label)}">${escapeHtml(truncate(def.label, 20))}${weight}</th>`;
        }
        html += '</tr></thead><tbody>';

        // One row per attacker unit, optionally followed by its weapons
        attackers.forEach((atk, a) => {
            html += `<tr class="matrix-unit-row"><th class="matrix-row-header" title="${escapeHtml(atk.label)}">${escapeHtml(truncate(atk.label, 28))}</th>`;
            matrix[a].forEach((cell, d) => { html += cellHtml(cell, 'unit', a, -1, d, atk.label); });
            html += '</tr>';

            if (state.showWeapons && weaponMatrix) {
                atk.weapons.forEach((w, wi) => {
                    const wLabel = `${w.label}${w.models > 1 ? ` ×${w.models}` : ''}`;
                    html += `<tr class="matrix-weapon-row"><th class="matrix-row-header" title="${escapeHtml(atk.label + ' - ' + wLabel)}">↳ ${escapeHtml(truncate(wLabel, 26))}</th>`;
                    weaponMatrix[a][wi].forEach((cell, d) => { html += cellHtml(cell, 'weapon', a, wi, d, `${atk.label} - ${wLabel}`); });
                    html += '</tr>';
                });
            }
        });

        // Army total row
        if (total) {
            html += `<tr class="matrix-total-row"><th class="matrix-row-header">${escapeHtml(total.label)}</th>`;
            total.cells.forEach((cell, d) => { html += cellHtml(cell, 'total', -1, -1, d, total.label); });
            html += '</tr>';
        }
        html += '</tbody></table></div>';

        elements.matrixContainer.innerHTML = html;

        document.getElementById('matrix-show-weapons').addEventListener('change', (e) => {
            state.showWeapons = e.target.checked;
            renderMatrix();
        });

        elements.matrixContainer.querySelectorAll('.matrix-cell').forEach(cell => {
            cell.addEventListener('click', () => {
                showCellDetail(cell.dataset.kind, parseInt(cell.dataset.a), parseInt(cell.dataset.w), parseInt(cell.dataset.d));
            });
        });
    }




    function renderSummary() {
        if (!state.results || !state.results.summary) {
            elements.matrixSummary.style.display = 'none';
            document.getElementById('army-analysis').style.display = 'none';
            return;
        }

        const summary = state.results.summary;

        // Best attackers
        elements.bestAttackers.innerHTML = summary.best_attackers.map(item => `
            <li>
                <strong>${escapeHtml(item.defender)}:</strong>
                ${escapeHtml(item.attacker || 'N/A')} (${item.efficiency ? item.efficiency.toFixed(3) : '-'} dmg/pt)
            </li>
        `).join('');

        // Biggest threats
        elements.biggestThreats.innerHTML = summary.biggest_threats.map(item => `
            <li>
                <strong>${escapeHtml(item.defender)}:</strong>
                ${escapeHtml(item.attacker || 'N/A')} (${item.damage ? item.damage.toFixed(2) : '-'} avg dmg)
            </li>
        `).join('');

        elements.matrixSummary.style.display = 'block';
        renderAnalysis();
    }

    // =========================
    // Army Analysis (Excel's Results tab)
    // =========================

    function renderAnalysis() {
        const section = document.getElementById('army-analysis');
        const analysis = state.results?.analysis;
        if (!analysis) {
            section.style.display = 'none';
            return;
        }
        section.style.display = 'block';
        document.querySelectorAll('.analysis-tabs .toggle-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === state.analysisTab);
        });

        const content = document.getElementById('analysis-content');
        const render = {
            summary: Core.renderAnalysisSummary,
            gaps: Core.renderGaps,
            roles: Core.renderRoleScores,
            coverage: Core.renderCoverage,
            reverse: Core.renderReverse,
        }[state.analysisTab] || Core.renderAnalysisSummary;
        content.innerHTML = render(analysis);
        if (state.analysisTab === 'roles') drawRoleChart(analysis.roles);
    }





    function drawRoleChart(roles) {
        const el = document.getElementById('roles-chart');
        if (!el || !roles.length || typeof Plotly === 'undefined') return;
        const names = roles.map(r => r.name);
        const scores = roles.map(r => r.score);
        const top = Math.max(100, ...scores);
        Plotly.newPlot(el, [
            { type: 'scatterpolar', r: names.map(() => 100).concat(100), theta: names.concat(names[0]),
              mode: 'lines', line: { color: themeColor('--text-muted'), dash: 'dot' }, name: 'Wiped (100%)', hoverinfo: 'skip' },
            { type: 'scatterpolar', r: scores.concat(scores[0]), theta: names.concat(names[0]), fill: 'toself',
              fillcolor: themeColor('--accent-tint'), line: { color: themeColor('--accent') }, name: 'Army',
              hovertemplate: '%{theta}: %{r}%<extra></extra>' },
        ], {
            polar: { bgcolor: 'transparent', radialaxis: { range: [0, top], ticksuffix: '%', gridcolor: themeColor('--border'), color: themeColor('--text-secondary') },
                     angularaxis: { gridcolor: themeColor('--border'), color: themeColor('--text-primary') } },
            paper_bgcolor: 'transparent', font: { color: themeColor('--text-primary'), size: 11 },
            margin: { t: 30, r: 60, b: 30, l: 60 }, showlegend: false,
        }, { responsive: true, displayModeBar: false });
    }



    // =========================
    // Cell Detail Panel
    // =========================

    async function showCellDetail(kind, a, w, d) {
        const request = state.results?.request;
        if (!request) return;

        // Labels come from the results (they match the matrix), weapons from the request that produced it
        const resultAttackers = state.results.attackers;
        let weapons, attackerLabel;
        if (kind === 'total') {
            weapons = request.attackers.flatMap(atk => atk.weapons);
            attackerLabel = state.results.total.label;
        } else if (kind === 'weapon') {
            weapons = [request.attackers[a].weapons[w]];
            attackerLabel = `${resultAttackers[a].label} - ${resultAttackers[a].weapons[w].label}`;
        } else {
            weapons = request.attackers[a].weapons;
            attackerLabel = resultAttackers[a].label;
        }
        const defender = request.defenders[d];

        // Show panel with loading state
        elements.detailPanel.style.display = 'block';
        document.getElementById('detail-title').textContent = 'Loading...';
        document.getElementById('detail-attacker').textContent = attackerLabel;
        document.getElementById('detail-defender').textContent = state.results.defenders[d].label;

        try {
            const response = await fetch('/api/matrix/cell', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    weapons: weapons,
                    unit_id: defender.unit_id,
                    profile_id: defender.profile_id,
                    extra_modifiers: defender.extra_modifiers || '',
                    leader_id: defender.leader_id || null,
                    model_count: defender.model_count,
                    conditions: request.conditions,
                    attacker_rules: request.attacker_rules,
                    defender_rules: request.defender_rules,
                    iterations: 1000
                })
            });

            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Request failed');

            document.getElementById('detail-title').textContent = 'Matchup Details';
            document.getElementById('detail-avg-damage').textContent = data.summary.average_damage.toFixed(2);
            document.getElementById('detail-models-killed').textContent = data.summary.models_killed.toFixed(2);
            document.getElementById('detail-kill-prob').textContent = `${data.summary.kill_probability}%`;
            document.getElementById('detail-wipe-prob').textContent = `${data.summary.wipe_probability}%`;
            document.getElementById('detail-attached').textContent = data.summary.leader_killed != null
                ? `Attached unit: ${data.summary.bodyguards_killed} bodyguard models killed on average; ` +
                  `the leader dies in ${data.summary.leader_kill_probability}% of runs (average damage to it ${data.summary.leader_damage})`
                : '';
            document.getElementById('detail-attacks').textContent = data.breakdown.attacks.toFixed(2);
            document.getElementById('detail-hits').textContent = data.breakdown.hits.toFixed(2);
            document.getElementById('detail-wounds').textContent = data.breakdown.wounds.toFixed(2);
            document.getElementById('detail-failed-saves').textContent = data.breakdown.failed_saves.toFixed(2);

            renderDetailChart(data.distribution);

        } catch (err) {
            console.error('Error loading cell detail:', err);
            document.getElementById('detail-title').textContent = 'Error loading details';
        }
    }

    function renderDetailChart(distribution) {
        if (!distribution || !distribution.labels || distribution.labels.length === 0) {
            document.getElementById('detail-chart').innerHTML = '<p>No distribution data</p>';
            return;
        }

        Plotly.newPlot('detail-chart', [{
            x: distribution.labels,
            y: distribution.probabilities,
            type: 'bar',
            marker: {
                color: themeColor('--accent')
            },
            hovertemplate: '%{x} damage: %{y:.1f}%<extra></extra>'
        }], {
            margin: { t: 10, r: 10, b: 30, l: 40 },
            paper_bgcolor: 'transparent',
            plot_bgcolor: 'transparent',
            font: { color: themeColor('--text-primary') },
            xaxis: {
                title: 'Damage',
                type: 'category',
                gridcolor: themeColor('--border')
            },
            yaxis: {
                title: '%',
                gridcolor: themeColor('--border')
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
                    conditions: {
                        ...state.conditions,
                        rules_ticked: ticksForSaving(),
                        defender_mode: state.defenderMode,
                        generic_included: state.genericIncluded ? [...state.genericIncluded] : null,
                    },
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
                        <span class="comparison-name">${escapeHtml(c.name)}</span>
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

    /**
     * Older comparisons stored one attacker entry per weapon ({weapon_id, weapon_type, unit_name, ...})
     * and defenders without stats. Rebuild them as unit entries: each saved weapon goes under its
     * unit, ticked, with 1 model.
     */
    async function convertLegacyAttackers(entries) {
        const groups = [];
        const byKey = new Map();
        for (const entry of entries) {
            let weapon;
            try {
                const response = await fetch(`/api/data/weapons/${entry.weapon_type}/${entry.weapon_id}`);
                if (!response.ok) continue;
                weapon = await response.json();
            } catch (err) {
                continue;
            }

            const unitName = entry.unit_name || (entry.label || '').split(' - ')[0] || 'Unknown';
            const key = `${entry.unit_id ?? ''}|${unitName}`;
            if (!byKey.has(key)) {
                const group = { unit_id: entry.unit_id ?? null, unit_name: unitName, faction: '', weapons: [] };
                byKey.set(key, group);
                groups.push(group);
            }
            byKey.get(key).weapons.push(toWeaponEntry(weapon));
        }
        return groups;
    }

    async function convertLegacyDefenders(entries) {
        const defenders = [];
        for (const entry of entries) {
            try {
                const response = await fetch(`/api/units/${entry.unit_id}`);
                if (!response.ok) continue;
                defenders.push(toDefenderEntry(await response.json(), entry.model_count || DEFAULT_DEFENDER_MODELS));
            } catch (err) {
                continue;
            }
        }
        return defenders;
    }

    async function loadComparison(id) {
        try {
            const response = await fetch(`/api/comparisons/${id}`);
            const data = await response.json();

            let attackers = data.attackers || [];
            let defenders = data.defenders || [];
            let results = data.results;

            const legacyAttackers = attackers.some(a => !Array.isArray(a.weapons));
            const legacyDefenders = defenders.some(d => d.toughness === undefined);
            if (legacyAttackers) attackers = await convertLegacyAttackers(attackers);
            if (legacyDefenders) defenders = await convertLegacyDefenders(defenders);
            // Old results have a different shape; they need a re-run
            if (legacyAttackers || legacyDefenders || !results?.request) results = null;

            state.attackers = attackers;
            state.defenders = defenders;
            const { rules_ticked: savedTicks, defender_mode: savedMode, generic_included: savedIncluded,
                    ...savedConditions } = data.battle_conditions || {};
            state.defenderMode = savedMode === 'generic' ? 'generic' : 'real';
            state.genericIncluded = savedIncluded ? new Set(savedIncluded) : null;
            state.conditions = { ...state.conditions, ...savedConditions };
            state.results = results;
            restoreTicks(savedTicks);

            // Update UI
            renderAttackerTable();
            renderDefenderTable();
            updateConditionUI();

            if (state.results) {
                renderMatrix();
                renderSummary();
            } else {
                elements.matrixContainer.innerHTML = `<div class="matrix-placeholder">
                    <p>Comparison loaded. Click "Run Matrix" to generate the comparison grid.</p></div>`;
                elements.matrixSummary.style.display = 'none';
            document.getElementById('army-analysis').style.display = 'none';
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
        document.getElementById('mat-damaged').checked = !!state.conditions.damaged;
        document.getElementById('mat-reroll-hits').value = state.conditions.reroll_hits;
        document.getElementById('mat-reroll-wounds').value = state.conditions.reroll_wounds;
    }

    function debounce(fn, delay) {
        let timer;
        return function(...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

})();
