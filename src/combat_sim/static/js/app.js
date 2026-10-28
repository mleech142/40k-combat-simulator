/**
 * 40K Combat Simulator - Frontend Application
 */

/**
 * Page setup that survives switching tabs but not restarting the app.
 * Stored in sessionStorage (cleared when the browser tab closes) and tagged with
 * the server's boot ID (new on every start), so stale state from a previous run is ignored.
 * Shared by every page, since base.html loads this script everywhere.
 */
const PageState = Core.createPageState(
    (() => { try { return window.sessionStorage; } catch (err) { return null; } })(),
    document.querySelector('meta[name="app-boot-id"]')?.content || ''
);

const SIMULATOR_STATE_KEY = 'simulatorState';

/**
 * As-you-type warnings for keyword/modifier text boxes (same rules as the Data page forms).
 * Usage: KeywordHints.attach(inputElement, hintElement, 'unit')
 * mode 'unit': plain words are fine (they are unit keywords used by Anti); only entries with values are checked.
 */
const KeywordHints = {
    /** Ask the server to check the box's keywords and show the result under it. */
    async check(input, hint, mode = '') {
        const text = input.value;
        try {
            const response = await fetch('/api/keywords/check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, mode }),
            });
            const result = await response.json();
            if (input.value !== text) return;  // a newer check is on its way
            hint.innerHTML = Core.keywordHintHtml(result);
        } catch (err) {
            hint.innerHTML = '';
        }
    },

    attach(input, hint, mode = '') {
        let timer;
        input.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(() => this.check(input, hint, mode), 200);
        });
        if (input.value) this.check(input, hint, mode);
    },
};

// State management
const state = {
    attacker: {
        unit: null,
        weapons: { ranged: [], melee: [] },
        weaponType: 'ranged',
        leaders: [],  // Leaders that can join the unit: [{id, name, points, leader_buffs}]
        leader: null,  // The attached one (its weapons are in `weapons`, marked leader: true)
    },
    defender: {
        unit: null,
        extraModifiers: '',  // One-off target modifiers for this run (never saved to the datasheet)
        leaders: [],
        leader: null,  // Its buffs apply to the target; its stats aren't part of the target (yet)
    },
    conditions: {
        half_range: false,
        charged: false,
        stationary: false,
        cover: false,
        damaged: false,
        reroll_hits: 'none',
        reroll_wounds: 'none',
        attacker_afflicted: '',  // Death Guard's Nurgle's Gift: '' (not Afflicted), 'none' (no Plague) or a Plague
        defender_afflicted: '',
    },
    simulation: {
        method: 'analytical',
        iterations: 1000,
    },
    // Rules & stratagems that can be ticked for each side, which are ticked ('rule:3', 'stratagem:7'),
    // and the side's chosen detachments (BSData keys; none = faction-wide rules only)
    rules: {
        attacker: { available: [], ticked: new Set(), factionId: null, chosen: [], detachments: [] },
        target: { available: [], ticked: new Set(), factionId: null, chosen: [], detachments: [] },
    },
};

// Last simulation result for saving
let lastSimulationResult = null;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', async () => {
    // app.js is loaded on every page; the rest is for the simulator page only
    if (!document.getElementById('attacker-unit')) return;

    initAutocomplete();
    initWeaponTypeToggle();
    initToggles();
    initRerollButtons();
    initAfflictionSelects();
    initSimulationOptions();
    initSimulateButton();
    initSaveButton();
    initExtraModifiers();
    initDetachmentSelects();
    initLeaderSelects();
    loadRulesFor('attacker', null);
    loadRulesFor('target', null);

    // A scenario opened from the Scenarios tab replaces the remembered setup
    if (sessionStorage.getItem('loadScenario')) {
        checkForLoadedScenario();
    } else {
        await restoreSimulatorState();
    }
    window.addEventListener('pagehide', saveSimulatorState);
});

/**
 * Extra modifiers: one-off target modifiers for this defender, e.g. "Stealth, Feel No Pain 5+"
 */
function initExtraModifiers() {
    const input = document.getElementById('defender-extra-modifiers');
    if (!input) return;
    input.addEventListener('input', () => { state.defender.extraModifiers = input.value; });
    KeywordHints.attach(input, document.getElementById('defender-extra-modifiers-hint'), 'unit');
}

function setExtraModifiers(text) {
    state.defender.extraModifiers = text;
    const input = document.getElementById('defender-extra-modifiers');
    if (input) {
        input.value = text;
        input.dispatchEvent(new Event('input'));
    }
}

/**
 * Remember the simulator setup for when the user comes back to this tab
 */
function saveSimulatorState() {
    PageState.save(SIMULATOR_STATE_KEY, {
        attackerUnit: state.attacker.unit,
        attackerUnitText: document.getElementById('attacker-unit').value,
        weapons: state.attacker.weapons,
        weaponType: state.attacker.weaponType,
        defenderUnit: state.defender.unit,
        defenderUnitText: document.getElementById('defender-unit').value,
        modelCount: document.getElementById('model-count').value,
        extraModifiers: state.defender.extraModifiers,
        conditions: state.conditions,
        simulation: state.simulation,
        result: lastSimulationResult,
        rulesTicked: {
            attacker: [...state.rules.attacker.ticked],
            target: [...state.rules.target.ticked],
        },
        detachments: { attacker: state.rules.attacker.chosen, target: state.rules.target.chosen },
        leaders: { attacker: state.attacker.leader?.id ?? null, defender: state.defender.leader?.id ?? null },
        legends: {
            attacker: document.getElementById('attacker-legends').checked,
            defender: document.getElementById('defender-legends').checked,
        },
    });
}

async function restoreSimulatorState() {
    const saved = PageState.load(SIMULATOR_STATE_KEY);
    if (!saved) return;

    applyConditions(saved.conditions || {});

    state.simulation = { ...state.simulation, ...(saved.simulation || {}) };
    const monteCarlo = document.getElementById('monte-carlo');
    monteCarlo.checked = state.simulation.method === 'monte_carlo';
    monteCarlo.closest('.toggle')?.classList.toggle('active', monteCarlo.checked);
    document.getElementById('iterations').value = state.simulation.iterations;
    document.getElementById('model-count').value = saved.modelCount || 5;
    setExtraModifiers(saved.extraModifiers || '');

    document.getElementById('attacker-legends').checked = !!saved.legends?.attacker;
    document.getElementById('defender-legends').checked = !!saved.legends?.defender;
    state.rules.attacker.chosen = Core.detachmentKeys(saved.detachments?.attacker);
    state.rules.target.chosen = Core.detachmentKeys(saved.detachments?.target);

    document.getElementById('defender-unit').value = saved.defenderUnitText || '';
    if (saved.defenderUnit) {
        state.defender.unit = saved.defenderUnit;
        showUnitStats('defender', saved.defenderUnit);
        if (saved.defenderUnit.id) await loadDefenderLeaders(saved.defenderUnit.id, saved.leaders?.defender);
    }

    document.getElementById('attacker-unit').value = saved.attackerUnitText || '';
    if (saved.attackerUnit) {
        state.attacker.unit = saved.attackerUnit;
        showUnitStats('attacker', saved.attackerUnit);
    }

    // Reload the unit's weapons so edits made on the Data tab show up; fall back to the saved list
    let restored = false;
    if (saved.attackerUnit?.id) {
        try {
            await loadWeaponsForUnit(saved.attackerUnit.id);
            if (saved.leaders?.attacker) await attachAttackerLeader(saved.leaders.attacker);
            for (const t of ['ranged', 'melee']) {
                for (const w of state.attacker.weapons[t]) {
                    const before = (saved.weapons?.[t] || []).find(s => s.id === w.id);
                    if (before?.qty) w.qty = before.qty;
                    if (typeof before?.use === 'boolean') w.use = before.use;
                }
            }
            setWeaponType(saved.weaponType || 'ranged');
            restored = true;
        } catch (err) {
            console.error('Error reloading weapons:', err);
        }
    }
    if (!restored && saved.weapons) {
        state.attacker.weapons = saved.weapons;
        setWeaponType(saved.weaponType || 'ranged');
    }

    loadRulesFor('attacker', saved.attackerUnit?.faction_id ?? null, saved.rulesTicked?.attacker);
    loadRulesFor('target', saved.defenderUnit?.faction_id ?? null, saved.rulesTicked?.target);

    if (saved.result) {
        lastSimulationResult = saved.result;
        displayResults(saved.result);
    }
}

// =========================
// Rules & stratagems panels
// =========================

const PHASE_FOR_WEAPON = { ranged: 'Shooting', melee: 'Fighting' };
const RULE_GROUPS = [
    ['rule', 'Faction rules'],
    ['stratagem', 'Faction stratagems'],
    ['core', 'Core stratagems'],
];

/** The detachment checklists reload their side's rules when changed. */
function initDetachmentSelects() {
    for (const side of ['attacker', 'target']) {
        const picker = document.getElementById(`${side}-detachment`);
        if (!picker) continue;
        picker.style.display = 'none';
        picker.addEventListener('change', () => {
            state.rules[side].chosen = [...picker.querySelectorAll('input:checked')].map(cb => cb.dataset.key);
            loadRulesFor(side, state.rules[side].factionId);
        });
    }
}

/** Fill a side's detachment checklist for its faction (hidden when the faction has none). */
async function loadDetachments(side, factionId) {
    const panel = state.rules[side];
    const select = document.getElementById(`${side}-detachment`);
    if (factionId !== panel.factionId || !panel.detachments.length) {
        panel.detachments = factionId
            ? await fetch(`/api/detachments?faction_id=${factionId}`).then(r => r.json()).catch(() => [])
            : [];
    }
    panel.factionId = factionId;
    panel.chosen = (panel.chosen || []).filter(key => panel.detachments.some(d => d.key === key));
    if (select) {
        const open = select.open;
        select.innerHTML = Core.detachmentPickerHtml(panel.detachments, panel.chosen);
        select.open = open;
        select.style.display = panel.detachments.length ? '' : 'none';
    }
}

/**
 * Load the rules/stratagems one side can tick (its faction's, those of its detachment, and core);
 * keep ticks that still exist.
 */
async function loadRulesFor(side, factionId, ticked = null) {
    await loadDetachments(side, factionId);
    const params = new URLSearchParams({ side: side === 'attacker' ? 'Attacker' : 'Target' });
    if (factionId) params.append('faction_id', factionId);
    (state.rules[side].chosen || []).forEach(key => params.append('detachment', key));
    try {
        const available = await fetch(`/api/rules/available?${params}`).then(r => r.json());
        const panel = state.rules[side];
        const keys = new Set(available.map(e => e.key));
        panel.available = available;
        panel.ticked = new Set([...(ticked || panel.ticked)].filter(k => keys.has(k)));
        renderRulesPanel(side);
    } catch (err) {
        console.error('Error loading rules:', err);
    }
}

/** Entries usable in the current phase (from the attacker's Shooting/Melee choice). */
function visibleRules(side) {
    return Core.rulesForFilter(state.rules[side].available, state.attacker.weaponType);
}

function renderRulesPanel(side) {
    const listEl = document.getElementById(`${side}-rules-list`);
    if (!listEl) return;
    const panel = state.rules[side];
    const visible = visibleRules(side);

    let html = '';
    for (const [kind, title] of RULE_GROUPS) {
        const group = visible.filter(e => e.kind === kind);
        if (!group.length) continue;
        html += `<div class="rules-group-title">${title}</div>`;
        html += group.map(e => `
            <label class="rule-row${e.has_effect ? '' : ' rule-no-effect'}"
                   title="${escapeHtml(Core.ruleRowTitle(e))}">
                <input type="checkbox" data-key="${e.key}" ${panel.ticked.has(e.key) ? 'checked' : ''}>
                <span class="rule-name">${escapeHtml(e.name)}</span>
                ${e.detachment ? `<span class="rule-detachment">${escapeHtml(e.detachment)}</span>` : ''}
                ${e.cp ? `<span class="rule-cp">${escapeHtml(e.cp)} CP</span>` : ''}
                <span class="rule-modifier">${e.has_effect ? escapeHtml(e.modifier) : 'No effect on the calculations'}</span>
            </label>`).join('');
    }
    listEl.innerHTML = html || '<div class="rules-empty">No rules or stratagems for this phase</div>';

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
        ? 'Active: ' + active.map(e => e.modifier).join(', ')
        : (state.rules[side].ticked.size ? 'Ticked, but no effect in this phase' : 'None ticked');
    summaryEl.classList.toggle('rules-summary-active', active.length > 0);
}

/** {rule_ids, stratagem_ids} for the request (the server applies only those matching side/phase). */
function rulesSelection(side) {
    return Core.rulesSelection(state.rules[side].ticked);
}

/**
 * Set the battle condition toggles and re-roll buttons (and state) to match `conditions`
 */
function applyConditions(conditions) {
    ['half_range', 'charged', 'stationary', 'cover', 'damaged'].forEach(cond => {
        const toggle = document.querySelector(`[data-condition="${cond}"]`);
        const on = !!conditions[cond];
        if (toggle) {
            const input = toggle.querySelector('input');
            if (input) input.checked = on;
            toggle.classList.toggle('active', on);
        }
        state.conditions[cond] = on;
    });

    ['hits', 'wounds'].forEach(type => {
        const value = conditions[`reroll_${type}`] || 'none';
        const group = document.querySelector(`[data-reroll="${type}"]`);
        if (group) {
            group.querySelectorAll('.btn-option').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.value === value);
            });
        }
        state.conditions[`reroll_${type}`] = value;
    });

    document.querySelectorAll('[data-affliction]').forEach(select => {
        const key = select.dataset.affliction;
        select.value = conditions[key] || '';
        if (select.selectedIndex < 0) select.value = '';  // A choice this version doesn't have
        state.conditions[key] = select.value;
    });
}

/**
 * Initialize autocomplete inputs
 */
function initAutocomplete() {
    // Attacker unit autocomplete
    setupAutocomplete('attacker-unit', async (search) => {
        const response = await fetch(unitSearchUrl(search, 'attacker'));
        return response.json();
    }, (item) => {
        state.attacker.unit = item;
        showUnitStats('attacker', item);
        loadWeaponsForUnit(item.id);  // Also lists the leaders that can join it
        loadRulesFor('attacker', item.faction_id);
    }, formatUnitItem);

    // Defender unit autocomplete
    setupAutocomplete('defender-unit', async (search) => {
        const response = await fetch(unitSearchUrl(search, 'defender'));
        return response.json();
    }, (item) => {
        state.defender.unit = item;
        showUnitStats('defender', item);
        loadDefenderLeaders(item.id);
        document.getElementById('model-count').value = item.default_models || 5;
        loadRulesFor('target', item.faction_id);
    }, formatUnitItem);
}

/** Unit search URL; Legends units only when that side's "Show Legends" box is ticked. */
function unitSearchUrl(search, side) {
    const legends = document.getElementById(`${side}-legends`)?.checked ? '&legends=1' : '';
    return `/api/units?search=${encodeURIComponent(search)}${legends}`;
}

/**
 * Setup autocomplete for an input
 */
function setupAutocomplete(inputId, fetchFn, onSelect, formatFn) {
    const input = document.getElementById(inputId);
    const dropdown = document.getElementById(`${inputId}-dropdown`);

    if (!input || !dropdown) return;

    let debounceTimer;

    input.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(async () => {
            const search = input.value.trim();
            if (search.length < 1) {
                dropdown.classList.remove('show');
                return;
            }

            const items = await fetchFn(search);
            renderDropdown(dropdown, items, formatFn, (item) => {
                input.value = item.name;
                dropdown.classList.remove('show');
                onSelect(item);
            });
        }, 200);
    });

    input.addEventListener('focus', async () => {
        if (input.value.length >= 1) {
            const items = await fetchFn(input.value);
            renderDropdown(dropdown, items, formatFn, (item) => {
                input.value = item.name;
                dropdown.classList.remove('show');
                onSelect(item);
            });
        }
    });

    input.addEventListener('blur', () => {
        setTimeout(() => dropdown.classList.remove('show'), 200);
    });
}

/**
 * Render dropdown items
 */
function renderDropdown(dropdown, items, formatFn, onClick) {
    if (items.length === 0) {
        dropdown.innerHTML = '<div class="autocomplete-item"><em>No results</em></div>';
    } else {
        dropdown.innerHTML = items.map(item => formatFn(item)).join('');
    }
    dropdown.classList.add('show');

    dropdown.querySelectorAll('.autocomplete-item').forEach((el, idx) => {
        el.addEventListener('click', () => onClick(items[idx]));
    });
}

/**
 * Format unit item for dropdown
 */
function formatUnitItem(unit) {
    return `
        <div class="autocomplete-item" data-id="${unit.id}">
            <div class="item-name">${escapeHtml(unit.name)}</div>
            <div class="item-faction">${escapeHtml(unit.faction)}</div>
        </div>
    `;
}

/**
 * Show unit stats in the panel
 */
function showUnitStats(side, unit) {
    const prefix = side === 'attacker' ? 'att' : 'def';
    const statsEl = document.getElementById(`${side}-stats`);

    if (!statsEl) return;

    statsEl.style.display = 'grid';
    document.getElementById(`${prefix}-t`).textContent = unit.toughness;
    document.getElementById(`${prefix}-sv`).textContent = unit.save + '+';
    document.getElementById(`${prefix}-w`).textContent = unit.wounds;
    document.getElementById(`${prefix}-inv`).textContent = unit.invulnerable_save ? unit.invulnerable_save + '+' : '-';
    document.getElementById(`${prefix}-fnp`).textContent = unit.feel_no_pain ? unit.feel_no_pain + '+' : '-';
}

const escapeHtml = Core.escapeHtml;

/** A colour token from style.css's :root (e.g. themeColor('--accent')), so charts follow the theme. */
function themeColor(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Load weapons for selected unit and show them in the weapon list
 */

/**
 * Load the unit's loadout and wargear options. qty = models carrying the weapon (pre-filled from
 * the loadout, editable per run); use = ticked for the simulation. Neither is saved to the data.
 */
async function loadWeaponsForUnit(unitId) {
    const response = await fetch(`/api/units/${unitId}`);
    const unitData = await response.json();

    // default_use: the server's default ticks (only the first "Weapon - Profile" variant of a gun); options start unticked
    state.attacker.weapons = Core.simulatorWeapons(unitData);
    state.attacker.leaders = unitData.leaders || [];
    state.attacker.leader = null;
    renderLeaderSelect('attacker');
    setWeaponType(state.attacker.weapons.ranged.length ? 'ranged' : 'melee');
}

// =========================
// Leaders
// =========================

function initLeaderSelects() {
    document.getElementById('attacker-leader')?.addEventListener('change', async (e) => {
        await attachAttackerLeader(e.target.value ? parseInt(e.target.value) : null);
    });
    document.getElementById('defender-leader')?.addEventListener('change', (e) => {
        const id = e.target.value ? parseInt(e.target.value) : null;
        state.defender.leader = state.defender.leaders.find(l => l.id === id) || null;
        renderLeaderSelect('defender');
    });
}

/** "Led by" dropdown for a side: hidden when no leader can join its unit; shows what the chosen one grants. */
function renderLeaderSelect(side) {
    const group = document.getElementById(`${side}-leader-group`);
    const select = document.getElementById(`${side}-leader`);
    const hint = document.getElementById(`${side}-leader-buffs`);
    if (!group || !select) return;
    const { leaders, leader } = state[side];
    group.style.display = leaders.length ? '' : 'none';
    select.innerHTML = '<option value="">No leader</option>' + leaders.map(l =>
        `<option value="${l.id}" ${leader?.id === l.id ? 'selected' : ''}>${escapeHtml(l.name)}${l.points ? ` (+${l.points} pts)` : ''}</option>`).join('');
    const summary = leader ? Core.leaderBuffSummary(leader.leader_buffs) : '';
    hint.textContent = !leader ? ''
        : (summary ? `Grants: ${summary}.` : 'Grants nothing the calculations use (buffs can be added on the Data tab).') +
          (side === 'defender' ? ' Attacks go to the bodyguard models first, then the leader (with its own profile).' : '');
}

/** Attach a leader to the attacker (its weapons join the list) or detach (null). */
async function attachAttackerLeader(leaderId) {
    const choice = state.attacker.leaders.find(l => l.id === leaderId) || null;
    const detail = choice ? await fetch(`/api/units/${choice.id}`).then(r => r.json()) : null;
    state.attacker.leader = choice;
    state.attacker.weapons = Core.withLeaderWeapons(state.attacker.weapons, detail);
    renderLeaderSelect('attacker');
    renderWeaponList();
}

/** The leaders that can join the defender unit; keep `keepId` attached if it is one of them. */
async function loadDefenderLeaders(unitId, keepId = null) {
    const detail = await fetch(`/api/units/${unitId}`).then(r => r.json()).catch(() => ({}));
    state.defender.leaders = detail.leaders || [];
    state.defender.leader = state.defender.leaders.find(l => l.id === keepId) || null;
    renderLeaderSelect('defender');
}

/**
 * Initialize the shooting / melee toggle above the weapon list
 */
function initWeaponTypeToggle() {
    document.querySelectorAll('#weapon-type-toggle .toggle-btn').forEach(btn => {
        btn.addEventListener('click', () => setWeaponType(btn.dataset.type));
    });
}

/**
 * Switch the weapon list to ranged or melee, selecting a weapon of that type
 */
function setWeaponType(type) {
    state.attacker.weaponType = type;
    renderRulesPanel('attacker');
    renderRulesPanel('target');
    document.querySelectorAll('#weapon-type-toggle .toggle-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.type === type);
    });
    renderWeaponList();
}

/** Ticked weapons of the current phase. */
function usedWeapons() {
    return Core.usedWeapons(state.attacker.weapons, state.attacker.weaponType);
}

function weaponPayload(w) {
    return Core.weaponPayload(w, state.attacker.weaponType);
}

/**
 * Render the attacker's weapons of the active type, one per row
 */
function renderWeaponList() {
    const listEl = document.getElementById('attacker-weapon-list');
    if (!listEl) return;

    const type = state.attacker.weaponType;
    const weapons = state.attacker.weapons[type];

    if (!state.attacker.unit && weapons.length === 0) {
        listEl.innerHTML = '<div class="weapon-list-empty">Select a unit to see its weapons</div>';
        return;
    }

    if (weapons.length === 0) {
        listEl.innerHTML = `<div class="weapon-list-empty">No ${type === 'ranged' ? 'shooting' : 'melee'} weapons</div>`;
        return;
    }

    const isRanged = type === 'ranged';
    const columns = isRanged ? 9 : 8;
    const firstOption = weapons.findIndex(w => w.option);
    const rows = weapons.map((w, idx) => {
        const abilities = w.abilities?.length
            ? `<div class="weapon-row-abilities">${escapeHtml(w.abilities.join(', '))}</div>`
            : '';
        const optionsHeader = idx === firstOption
            ? `<tr class="weapon-options-header"><td colspan="${columns}">Wargear options (tick to swap in; set Qty)</td></tr>`
            : '';
        return `${optionsHeader}
            <tr class="weapon-row${w.use ? ' selected' : ' excluded'}${w.option ? ' weapon-option' : ''}" data-idx="${idx}">
                <td class="weapon-use"><input type="checkbox" class="weapon-use-box" data-idx="${idx}" ${w.use ? 'checked' : ''}
                           title="Use this weapon in the simulation"></td>
                <td class="weapon-row-name">${escapeHtml(w.name)}${w.leader ? ` <span class="option-tag" title="${escapeHtml(w.leader_name)}'s weapon">leader</span>` : ''}${abilities}</td>
                ${isRanged ? `<td>${w.range ? escapeHtml(w.range) + '"' : '-'}</td>` : ''}
                <td>${escapeHtml(w.attacks)}</td>
                <td>${w.skill ? escapeHtml(w.skill) + '+' : '-'}</td>
                <td>${escapeHtml(w.strength)}</td>
                <td>${w.ap === 0 ? '0' : '-' + escapeHtml(w.ap)}</td>
                <td>${escapeHtml(w.damage)}</td>
                <td><input type="number" class="weapon-qty" data-idx="${idx}" value="${w.qty || 1}" min="1" max="99"
                           title="Models with this weapon (this run only)"></td>
            </tr>
        `;
    }).join('');

    listEl.innerHTML = `
        <table class="weapon-table">
            <thead>
                <tr>
                    <th title="Use">Use</th>
                    <th>Weapon</th>
                    ${isRanged ? '<th>Range</th>' : ''}
                    <th>A</th>
                    <th>${isRanged ? 'BS' : 'WS'}</th>
                    <th>S</th>
                    <th>AP</th>
                    <th>D</th>
                    <th title="Models with this weapon">Qty</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;

    // Clicking a row toggles it (except on the inputs themselves)
    listEl.querySelectorAll('.weapon-row').forEach(row => {
        row.addEventListener('click', (e) => {
            if (e.target.tagName === 'INPUT') return;
            const w = weapons[parseInt(row.dataset.idx)];
            w.use = !w.use;
            renderWeaponList();
        });
    });
    listEl.querySelectorAll('.weapon-use-box').forEach(box => {
        box.addEventListener('change', () => {
            weapons[parseInt(box.dataset.idx)].use = box.checked;
            box.closest('tr').classList.toggle('selected', box.checked);
            box.closest('tr').classList.toggle('excluded', !box.checked);
        });
    });
    listEl.querySelectorAll('.weapon-qty').forEach(input => {
        input.addEventListener('change', () => {
            const qty = Math.max(1, parseInt(input.value) || 1);
            input.value = qty;
            weapons[parseInt(input.dataset.idx)].qty = qty;
        });
    });
}

/**
 * Initialize toggle buttons
 */
function initToggles() {
    document.querySelectorAll('.toggle').forEach(toggle => {
        const input = toggle.querySelector('input');
        if (!input) return;

        toggle.addEventListener('click', () => {
            input.checked = !input.checked;
            toggle.classList.toggle('active', input.checked);

            // Update state
            const condition = toggle.dataset.condition;
            if (condition) {
                state.conditions[condition] = input.checked;
            }
        });
    });
}

/** Death Guard's Afflicted choices (one per side) */
function initAfflictionSelects() {
    document.querySelectorAll('[data-affliction]').forEach(select => {
        select.addEventListener('change', () => { state.conditions[select.dataset.affliction] = select.value; });
    });
}

/**
 * Initialize re-roll button groups
 */
function initRerollButtons() {
    document.querySelectorAll('.btn-group').forEach(group => {
        const rerollType = group.dataset.reroll;

        group.querySelectorAll('.btn-option').forEach(btn => {
            btn.addEventListener('click', () => {
                group.querySelectorAll('.btn-option').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');

                if (rerollType === 'hits') {
                    state.conditions.reroll_hits = btn.dataset.value;
                } else if (rerollType === 'wounds') {
                    state.conditions.reroll_wounds = btn.dataset.value;
                }
            });
        });
    });
}

/**
 * Initialize simulation options
 */
function initSimulationOptions() {
    const monteCarloToggle = document.getElementById('monte-carlo');
    const iterationsInput = document.getElementById('iterations');

    if (monteCarloToggle) {
        monteCarloToggle.addEventListener('change', () => {
            state.simulation.method = monteCarloToggle.checked ? 'monte_carlo' : 'analytical';
        });
    }

    if (iterationsInput) {
        iterationsInput.addEventListener('change', () => {
            state.simulation.iterations = parseInt(iterationsInput.value) || 1000;
        });
    }
}

/**
 * Initialize simulate button
 */
function initSimulateButton() {
    const btn = document.getElementById('btn-simulate');
    if (!btn) return;

    btn.addEventListener('click', runSimulation);
}

/**
 * Run simulation
 */
async function runSimulation() {
    const weapons = usedWeapons();
    const defender = state.defender.unit;

    if (!weapons.length || !defender) {
        alert('Please tick at least one attacker weapon and select a defender unit');
        return;
    }

    const modelCount = parseInt(document.getElementById('model-count')?.value) || 1;

    const payload = {
        attacker: {
            faction_id: state.attacker.unit?.faction_id ?? null,
            unit_id: state.attacker.unit?.id ?? null,
            leader_id: state.attacker.leader?.id ?? null,
            weapons: weapons.map(weaponPayload),
            afflicted: state.conditions.attacker_afflicted || null,
        },
        defender: {
            toughness: defender.toughness,
            save: defender.save,
            wounds: defender.wounds,
            invulnerable_save: defender.invulnerable_save,
            feel_no_pain: defender.feel_no_pain,
            keywords: [...(defender.keywords || []), ...(defender.modifiers || [])],
            faction_id: defender.faction_id ?? null,
            model_count: modelCount,
            extra_modifiers: state.defender.extraModifiers,
            leader_id: state.defender.leader?.id ?? null,
            afflicted: state.conditions.defender_afflicted || null,
        },
        conditions: state.conditions,
        method: state.simulation.method,
        iterations: state.simulation.iterations,
        attacker_rules: rulesSelection('attacker'),
        target_rules: rulesSelection('target'),
    };

    try {
        const response = await fetch('/api/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Simulation failed');
        lastSimulationResult = result;
        displayResults(result);
    } catch (err) {
        console.error('Simulation error:', err);
        alert('Error running simulation');
    }
}

/**
 * Display simulation results
 */
function displayResults(result) {
    // Summary (totals across the ticked weapons)
    document.getElementById('res-avg-damage').textContent = result.summary.average_damage;
    document.getElementById('res-models-killed').textContent = result.summary.models_killed;
    document.getElementById('res-overkill').textContent = result.summary.overkill;

    const extra = document.getElementById('res-mc-extra');
    if (extra) {
        extra.textContent = result.summary.wipe_probability != null
            ? `Monte Carlo (${result.iterations} runs, all weapons rolled together): ` +
              `${result.summary.kill_probability}% chance to kill at least one model, ${result.summary.wipe_probability}% to wipe the unit`
            : '';
    }

    // Attached unit: bodyguard models first, then the leader
    const attachedEl = document.getElementById('res-attached');
    if (attachedEl) {
        const a = result.attached, s = result.summary;
        // Analytical: expected damage to the leader (averages overstate "kills" near the threshold);
        // Monte Carlo: how often the leader actually dies
        attachedEl.textContent = !a ? ''
            : `Attached unit: ${s.bodyguards_killed} of ${a.bodyguard_models} bodyguard models killed; ` +
              (s.leader_kill_probability != null
                  ? `${a.leader} dies in ${s.leader_kill_probability}% of runs (average damage ${s.leader_damage} of ${a.leader_wounds} wounds)`
                  : `expected damage to ${a.leader}: ${s.leader_damage} of ${a.leader_wounds} wounds`);
    }

    // Breakdown
    document.getElementById('res-attacks').textContent = result.breakdown.attacks;
    document.getElementById('res-hits').textContent = result.breakdown.hits;
    document.getElementById('res-wounds').textContent = result.breakdown.wounds;
    document.getElementById('res-failed-saves').textContent = result.breakdown.failed_saves;
    document.getElementById('res-damage').textContent = result.breakdown.damage;

    renderWeaponResults(result.weapons || []);

    // Chart
    drawChart(result.distribution);
}

/** Per-weapon results table (Excel's Sim-sheet columns), with a total row. */
function renderWeaponResults(rows) {
    const el = document.getElementById('weapon-results');
    if (!el) return;
    if (!rows.length) {
        el.innerHTML = '';
        return;
    }

    const fmt = v => (v === null || v === undefined) ? '-' : (Math.round(v * 100) / 100).toString();
    const sum = key => rows.reduce((total, r) => total + (r[key] || 0), 0);
    const cols = [
        ['attacks', 'Attacks'], ['hits', 'Hits'], ['auto_wounds', 'Auto Wounds'], ['wounds', 'Wounds'],
        ['mortal_wounds', 'Mortal Wounds'], ['unsaved', 'Unsaved'], ['damage_per_attack', 'Dmg / Attack'],
        ['total_damage', 'Total Damage'], ['models_killed', 'Models Killed'],
    ];
    // Attached unit: weapons in order, bodyguards then leader, so their kills add up (otherwise capped, as in Excel)
    const attached = rows.some(r => r.leader_killed != null);
    if (attached) cols.push(['bodyguards_killed', 'Bodyguards killed'], ['leader_damage', 'Damage to leader']);

    el.innerHTML = `
        <h3>Per Weapon <span class="weapon-results-note">(average results)</span></h3>
        <div class="weapon-results-scroll">
            <table class="breakdown-table weapon-results-table">
                <thead><tr><th>Weapon</th><th>Qty</th>${cols.map(([, label]) => `<th>${label}</th>`).join('')}</tr></thead>
                <tbody>
                    ${rows.map(r => `<tr>
                        <td>${escapeHtml(r.name)}</td><td>${r.models}</td>
                        ${cols.map(([key]) => `<td>${fmt(r[key])}</td>`).join('')}
                    </tr>`).join('')}
                </tbody>
                ${rows.length > 1 ? `<tfoot><tr><td>Total</td><td></td>
                    ${cols.map(([key]) => `<td>${key === 'damage_per_attack' || (key === 'models_killed' && !attached) ? '' : fmt(sum(key))}</td>`).join('')}
                </tr></tfoot>` : ''}
            </table>
        </div>
    `;
}

/**
 * Draw the damage distribution chart (Monte Carlo only: the analytical engine gives averages,
 * not a spread, so the chart is hidden then).
 */
function drawChart(distribution) {
    const chartEl = document.getElementById('chart');
    if (!chartEl) return;
    const section = document.getElementById('distribution-section');
    if (!distribution?.labels?.length) {
        Plotly.purge(chartEl);
        if (section) section.style.display = 'none';
        return;
    }
    if (section) section.style.display = '';
    const marker = Core.woundsMarker(distribution, themeColor('--text-secondary'));

    Plotly.newPlot(chartEl, [{
        x: distribution.labels,
        y: distribution.probabilities,
        type: 'bar',
        marker: {
            color: themeColor('--accent'),
            line: {
                color: themeColor('--accent-hover'),
                width: 1
            }
        },
        hovertemplate: '%{x} damage: %{y:.1f}%<extra></extra>'
    }], {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: themeColor('--text-primary'), size: 11 },
        margin: { t: 30, r: 20, b: 50, l: 50 },
        xaxis: {
            title: 'Damage',
            type: 'category',
            gridcolor: themeColor('--border'),
            linecolor: themeColor('--border')
        },
        shapes: marker.shapes,
        annotations: marker.annotations,
        yaxis: {
            title: 'Probability %',
            gridcolor: themeColor('--border'),
            linecolor: themeColor('--border')
        },
        bargap: 0.15
    }, {
        responsive: true,
        displayModeBar: false
    });
}

/**
 * Initialize save button
 */
function initSaveButton() {
    const btn = document.getElementById('btn-save');
    if (!btn) return;

    btn.addEventListener('click', saveScenario);
}

/**
 * Save current scenario
 */
async function saveScenario() {
    const weapons = usedWeapons();
    const attackerUnit = state.attacker.unit;
    const defender = state.defender.unit;

    if (!weapons.length || !defender) {
        alert('Please run a simulation first');
        return;
    }

    const modelCount = parseInt(document.getElementById('model-count')?.value) || 1;

    // Generate a default name
    const defaultName = `${attackerUnit?.name || 'Unknown'} vs ${defender.name}`;
    const name = prompt('Enter a name for this scenario:', defaultName);

    if (!name) return;

    const scenario = {
        name: name,
        attacker: {
            unit_name: attackerUnit?.name,
            unit_id: state.attacker.unit?.id ?? null,
            weapon_name: weapons.map(w => w.qty > 1 ? `${w.name} x${w.qty}` : w.name).join(', '),
            faction_id: state.attacker.unit?.faction_id ?? null,
            type: state.attacker.weaponType,
            weapons: weapons.map(weaponPayload),
            rules: [...state.rules.attacker.ticked],
            detachments: state.rules.attacker.chosen,
            leader_id: state.attacker.leader?.id ?? null,
            leader_name: state.attacker.leader?.name ?? null,
        },
        defender: {
            unit_name: defender.name,
            toughness: defender.toughness,
            save: defender.save,
            wounds: defender.wounds,
            invulnerable_save: defender.invulnerable_save,
            feel_no_pain: defender.feel_no_pain,
            keywords: [...(defender.keywords || []), ...(defender.modifiers || [])],
            faction_id: defender.faction_id ?? null,
            model_count: modelCount,
            extra_modifiers: state.defender.extraModifiers,
            rules: [...state.rules.target.ticked],
            detachments: state.rules.target.chosen,
            unit_id: defender.id ?? null,
            leader_id: state.defender.leader?.id ?? null,
            leader_name: state.defender.leader?.name ?? null,
        },
        conditions: state.conditions,
        results: lastSimulationResult
    };

    try {
        await fetch('/api/scenarios', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(scenario)
        });
        alert('Scenario saved successfully!');
    } catch (err) {
        console.error('Save error:', err);
        alert('Error saving scenario');
    }
}

/**
 * Restore the attacker from a saved scenario. Loads the unit's full weapon list
 * if the unit still exists; otherwise shows just the saved weapon profile.
 */
/**
 * Restore the attacker from a saved scenario: the unit's current loadout with the saved weapons
 * ticked (and their quantities), or just the saved weapon profiles if the unit no longer exists.
 * Older scenarios saved a single weapon directly in the config.
 */
async function loadScenarioAttacker(config) {
    const savedWeapons = (config.weapons || [{ ...config, name: config.weapon_name }]).map(w => ({
        name: w.name,
        type: w.type === 'melee' ? 'melee' : 'ranged',
        attacks: w.attacks,
        skill: w.skill,
        strength: w.strength,
        ap: w.ap,
        damage: w.damage,
        abilities: w.abilities || [],
        qty: w.models || 1,
        use: true,
    }));
    const type = savedWeapons[0]?.type || 'ranged';

    try {
        if (config.unit_name) {
            // Within the saved faction when known (no search cap there); Legends included
            const params = new URLSearchParams({ search: config.unit_name, legends: '1' });
            if (config.faction_id != null) params.set('faction_id', config.faction_id);
            let unit = Core.findSavedUnit(await (await fetch(`/api/units?${params}`)).json(), config);
            if (!unit && params.has('faction_id')) {  // That faction is gone: any copy by name
                params.delete('faction_id');
                unit = Core.findSavedUnit(await (await fetch(`/api/units?${params}`)).json(), config);
            }
            if (unit) {
                state.attacker.unit = unit;
                showUnitStats('attacker', unit);
                await loadWeaponsForUnit(unit.id);
                if (config.leader_id) await attachAttackerLeader(config.leader_id);
                let matched = 0;
                for (const t of ['ranged', 'melee']) {
                    for (const w of state.attacker.weapons[t]) {
                        const saved = savedWeapons.find(sw => sw.type === t && sw.name === w.name);
                        w.use = !!saved;
                        if (saved) {
                            w.qty = saved.qty;
                            matched++;
                        }
                    }
                }
                if (matched) {
                    setWeaponType(type);
                    return;
                }
            }
        }
    } catch (err) {
        console.error('Error loading scenario unit:', err);
    }

    state.attacker.weapons = { ranged: [], melee: [] };
    savedWeapons.forEach(w => state.attacker.weapons[w.type].push(w));
    setWeaponType(type);
}

/**
 * Check if there's a scenario to load from session storage
 */
function checkForLoadedScenario() {
    const stored = sessionStorage.getItem('loadScenario');
    if (!stored) return;

    sessionStorage.removeItem('loadScenario');

    try {
        const scenario = JSON.parse(stored);

        // We need to find the actual unit and weapon objects
        // For now, just set the names in the inputs
        const attackerUnitInput = document.getElementById('attacker-unit');
        const defenderUnitInput = document.getElementById('defender-unit');
        const modelCountInput = document.getElementById('model-count');

        if (attackerUnitInput && scenario.attacker_config?.unit_name) {
            attackerUnitInput.value = scenario.attacker_config.unit_name;
        }

        if (defenderUnitInput && scenario.defender_config?.unit_name) {
            defenderUnitInput.value = scenario.defender_config.unit_name;
        }

        if (modelCountInput && scenario.defender_config?.model_count) {
            modelCountInput.value = scenario.defender_config.model_count;
        }
        setExtraModifiers(scenario.defender_config?.extra_modifiers || '');

        // Set conditions
        if (scenario.battle_conditions) {
            applyConditions(scenario.battle_conditions);
        }

        // Store the attacker and defender config for simulation
        // We need to reconstruct the weapon and unit objects
        if (scenario.attacker_config) {
            const config = scenario.attacker_config;
            state.rules.attacker.chosen = Core.detachmentKeys(config.detachments ?? config.detachment);
            loadScenarioAttacker(config).then(() =>
                loadRulesFor('attacker', state.attacker.unit?.faction_id ?? config.faction_id ?? null, config.rules || []));
        }

        if (scenario.defender_config) {
            state.defender.unit = {
                name: scenario.defender_config.unit_name,
                toughness: scenario.defender_config.toughness,
                save: scenario.defender_config.save,
                wounds: scenario.defender_config.wounds,
                invulnerable_save: scenario.defender_config.invulnerable_save,
                feel_no_pain: scenario.defender_config.feel_no_pain,
                keywords: scenario.defender_config.keywords || [],
                faction_id: scenario.defender_config.faction_id ?? null,
            };

            // Show defender stats
            showUnitStats('defender', state.defender.unit);
            if (scenario.defender_config.unit_id) {
                state.defender.unit.id = scenario.defender_config.unit_id;
                loadDefenderLeaders(scenario.defender_config.unit_id, scenario.defender_config.leader_id);
            }
            state.rules.target.chosen = Core.detachmentKeys(scenario.defender_config.detachments ?? scenario.defender_config.detachment);
            loadRulesFor('target', state.defender.unit.faction_id, scenario.defender_config.rules || []);
        }

        console.log('Loaded scenario:', scenario.name);

    } catch (err) {
        console.error('Error loading scenario:', err);
    }
}
