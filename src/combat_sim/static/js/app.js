/**
 * 40K Combat Simulator - Frontend Application
 */

// State management
const state = {
    attacker: {
        unit: null,
        weapon: null,
    },
    defender: {
        unit: null,
    },
    conditions: {
        half_range: false,
        charged: false,
        stationary: false,
        cover: false,
        reroll_hits: 'none',
        reroll_wounds: 'none',
    },
    simulation: {
        method: 'analytical',
        iterations: 1000,
    }
};

// Last simulation result for saving
let lastSimulationResult = null;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    initAutocomplete();
    initToggles();
    initRerollButtons();
    initSimulationOptions();
    initSimulateButton();
    initSaveButton();
    checkForLoadedScenario();
});

/**
 * Initialize autocomplete inputs
 */
function initAutocomplete() {
    // Attacker unit autocomplete
    setupAutocomplete('attacker-unit', async (search) => {
        const response = await fetch(`/api/units?search=${encodeURIComponent(search)}`);
        return response.json();
    }, (item) => {
        state.attacker.unit = item;
        showUnitStats('attacker', item);
        loadWeaponsForUnit(item.id);
    }, formatUnitItem);

    // Attacker weapon autocomplete
    setupAutocomplete('attacker-weapon', async (search) => {
        const unitId = state.attacker.unit?.id;
        const url = unitId
            ? `/api/weapons?unit_id=${unitId}&search=${encodeURIComponent(search)}`
            : `/api/weapons?search=${encodeURIComponent(search)}`;
        const response = await fetch(url);
        return response.json();
    }, (item) => {
        state.attacker.weapon = item;
        showWeaponStats(item);
    }, formatWeaponItem);

    // Defender unit autocomplete
    setupAutocomplete('defender-unit', async (search) => {
        const response = await fetch(`/api/units?search=${encodeURIComponent(search)}`);
        return response.json();
    }, (item) => {
        state.defender.unit = item;
        showUnitStats('defender', item);
    }, formatUnitItem);
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
            <div class="item-name">${unit.name}</div>
            <div class="item-faction">${unit.faction}</div>
        </div>
    `;
}

/**
 * Format weapon item for dropdown
 */
function formatWeaponItem(weapon) {
    const abilities = weapon.abilities?.join(', ') || '';
    return `
        <div class="autocomplete-item" data-id="${weapon.id}">
            <div class="item-name">${weapon.name}</div>
            <div class="item-faction">${weapon.type} · ${abilities || 'No abilities'}</div>
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

/**
 * Show weapon stats
 */
function showWeaponStats(weapon) {
    const statsEl = document.getElementById('attacker-weapon-stats');
    const abilitiesEl = document.getElementById('wpn-abilities');

    if (!statsEl) return;

    statsEl.style.display = 'grid';
    document.getElementById('wpn-a').textContent = weapon.attacks;
    document.getElementById('wpn-skill').textContent = weapon.skill + '+';
    document.getElementById('wpn-s').textContent = weapon.strength;
    document.getElementById('wpn-ap').textContent = weapon.ap === 0 ? '0' : '-' + weapon.ap;
    document.getElementById('wpn-d').textContent = weapon.damage;

    if (abilitiesEl) {
        abilitiesEl.textContent = weapon.abilities?.join(', ') || '';
    }
}

/**
 * Load weapons for selected unit
 */
async function loadWeaponsForUnit(unitId) {
    // Pre-populate weapon dropdown with this unit's weapons
    const response = await fetch(`/api/units/${unitId}`);
    const unitData = await response.json();

    // Could auto-select first weapon or show suggestions
    console.log('Available weapons:', unitData.ranged_weapons, unitData.melee_weapons);
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
    const weapon = state.attacker.weapon;
    const defender = state.defender.unit;

    if (!weapon || !defender) {
        alert('Please select an attacker weapon and defender unit');
        return;
    }

    const modelCount = parseInt(document.getElementById('model-count')?.value) || 1;

    const payload = {
        attacker: {
            attacks: weapon.attacks,
            skill: weapon.skill,
            strength: weapon.strength,
            ap: weapon.ap,
            damage: weapon.damage,
            abilities: weapon.abilities || [],
        },
        defender: {
            toughness: defender.toughness,
            save: defender.save,
            wounds: defender.wounds,
            invulnerable_save: defender.invulnerable_save,
            feel_no_pain: defender.feel_no_pain,
            keywords: defender.keywords || [],
            model_count: modelCount,
        },
        conditions: state.conditions,
        method: state.simulation.method,
        iterations: state.simulation.iterations,
    };

    try {
        const response = await fetch('/api/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        const result = await response.json();
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
    // Summary
    document.getElementById('res-avg-damage').textContent = result.summary.average_damage;
    document.getElementById('res-models-killed').textContent = result.summary.models_killed;
    document.getElementById('res-overkill').textContent = result.summary.overkill;

    // Breakdown
    document.getElementById('res-attacks').textContent = result.breakdown.attacks;
    document.getElementById('res-hits').textContent = result.breakdown.hits;
    document.getElementById('res-wounds').textContent = result.breakdown.wounds;
    document.getElementById('res-failed-saves').textContent = result.breakdown.failed_saves;
    document.getElementById('res-damage').textContent = result.breakdown.damage;

    // Chart
    drawChart(result.distribution);
}

/**
 * Draw damage distribution chart
 */
function drawChart(distribution) {
    const chartEl = document.getElementById('chart');
    if (!chartEl) return;

    // Default distribution if none provided (analytical mode)
    const labels = distribution?.labels || ['0', '1', '2', '3', '4', '5', '6', '7', '8+'];
    const probabilities = distribution?.probabilities || [5, 8, 12, 18, 22, 18, 10, 5, 2];

    Plotly.newPlot(chartEl, [{
        x: labels,
        y: probabilities,
        type: 'bar',
        marker: {
            color: '#e94560',
            line: {
                color: '#ff6b6b',
                width: 1
            }
        },
        hovertemplate: '%{x} damage: %{y:.1f}%<extra></extra>'
    }], {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#eaeaea', size: 11 },
        margin: { t: 20, r: 20, b: 50, l: 50 },
        xaxis: {
            title: 'Damage',
            gridcolor: '#2a2a4a',
            linecolor: '#2a2a4a'
        },
        yaxis: {
            title: 'Probability %',
            gridcolor: '#2a2a4a',
            linecolor: '#2a2a4a'
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
    const weapon = state.attacker.weapon;
    const attackerUnit = state.attacker.unit;
    const defender = state.defender.unit;

    if (!weapon || !defender) {
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
            weapon_name: weapon.name,
            attacks: weapon.attacks,
            skill: weapon.skill,
            strength: weapon.strength,
            ap: weapon.ap,
            damage: weapon.damage,
            abilities: weapon.abilities || [],
        },
        defender: {
            unit_name: defender.name,
            toughness: defender.toughness,
            save: defender.save,
            wounds: defender.wounds,
            invulnerable_save: defender.invulnerable_save,
            feel_no_pain: defender.feel_no_pain,
            keywords: defender.keywords || [],
            model_count: modelCount,
        },
        conditions: state.conditions,
        results: lastSimulationResult
    };

    try {
        const response = await fetch('/api/scenarios', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(scenario)
        });

        const result = await response.json();
        alert('Scenario saved successfully!');
    } catch (err) {
        console.error('Save error:', err);
        alert('Error saving scenario');
    }
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
        const attackerWeaponInput = document.getElementById('attacker-weapon');
        const defenderUnitInput = document.getElementById('defender-unit');
        const modelCountInput = document.getElementById('model-count');

        if (attackerUnitInput && scenario.attacker_config?.unit_name) {
            attackerUnitInput.value = scenario.attacker_config.unit_name;
        }

        if (attackerWeaponInput && scenario.attacker_config?.weapon_name) {
            attackerWeaponInput.value = scenario.attacker_config.weapon_name;
        }

        if (defenderUnitInput && scenario.defender_config?.unit_name) {
            defenderUnitInput.value = scenario.defender_config.unit_name;
        }

        if (modelCountInput && scenario.defender_config?.model_count) {
            modelCountInput.value = scenario.defender_config.model_count;
        }

        // Set conditions
        if (scenario.battle_conditions) {
            const conditions = scenario.battle_conditions;

            // Set toggles
            ['half_range', 'charged', 'stationary', 'cover'].forEach(cond => {
                const toggle = document.querySelector(`[data-condition="${cond}"]`);
                if (toggle && conditions[cond]) {
                    const input = toggle.querySelector('input');
                    if (input) input.checked = true;
                    toggle.classList.add('active');
                    state.conditions[cond] = true;
                }
            });

            // Set reroll buttons
            if (conditions.reroll_hits) {
                const group = document.querySelector('[data-reroll="hits"]');
                if (group) {
                    group.querySelectorAll('.btn-option').forEach(btn => {
                        btn.classList.toggle('active', btn.dataset.value === conditions.reroll_hits);
                    });
                    state.conditions.reroll_hits = conditions.reroll_hits;
                }
            }

            if (conditions.reroll_wounds) {
                const group = document.querySelector('[data-reroll="wounds"]');
                if (group) {
                    group.querySelectorAll('.btn-option').forEach(btn => {
                        btn.classList.toggle('active', btn.dataset.value === conditions.reroll_wounds);
                    });
                    state.conditions.reroll_wounds = conditions.reroll_wounds;
                }
            }
        }

        // Store the attacker and defender config for simulation
        // We need to reconstruct the weapon and unit objects
        if (scenario.attacker_config) {
            state.attacker.weapon = {
                name: scenario.attacker_config.weapon_name,
                attacks: scenario.attacker_config.attacks,
                skill: scenario.attacker_config.skill,
                strength: scenario.attacker_config.strength,
                ap: scenario.attacker_config.ap,
                damage: scenario.attacker_config.damage,
                abilities: scenario.attacker_config.abilities || []
            };

            // Show weapon stats
            const statsEl = document.getElementById('attacker-weapon-stats');
            if (statsEl) {
                statsEl.style.display = 'grid';
                document.getElementById('wpn-a').textContent = scenario.attacker_config.attacks;
                document.getElementById('wpn-skill').textContent = scenario.attacker_config.skill + '+';
                document.getElementById('wpn-s').textContent = scenario.attacker_config.strength;
                document.getElementById('wpn-ap').textContent = scenario.attacker_config.ap === 0 ? '0' : '-' + scenario.attacker_config.ap;
                document.getElementById('wpn-d').textContent = scenario.attacker_config.damage;
            }
        }

        if (scenario.defender_config) {
            state.defender.unit = {
                name: scenario.defender_config.unit_name,
                toughness: scenario.defender_config.toughness,
                save: scenario.defender_config.save,
                wounds: scenario.defender_config.wounds,
                invulnerable_save: scenario.defender_config.invulnerable_save,
                feel_no_pain: scenario.defender_config.feel_no_pain,
                keywords: scenario.defender_config.keywords || []
            };

            // Show defender stats
            showUnitStats('defender', state.defender.unit);
        }

        console.log('Loaded scenario:', scenario.name);

    } catch (err) {
        console.error('Error loading scenario:', err);
    }
}
