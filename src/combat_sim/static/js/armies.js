/**
 * Armies page: build an army list, save it, share it as text, and send it to Analytics.
 *
 * The list being built lives here (`state.army`); every change is sent to /api/armies/price,
 * which prices and checks it (the rules are in armies.py) and whose answer is what is drawn.
 */

(function() {
    'use strict';

    const { escapeHtml, copyLabel } = Core;
    const PAGE_STATE_KEY = 'armyBuilder';
    const DEFAULT_LIMIT = 2000;

    const state = {
        // {id (null until saved), name, faction_id, detachment_keys, points_limit, units: [{uid, unit_id, models, attached_to}]}
        army: newArmy(),
        priced: null,  // /api/armies/price answer for state.army
        dirty: false,  // Changed since it was last saved
        factionUnits: [],  // /api/units for the army's faction (picker)
        detachments: [],
    };
    const $ = id => document.getElementById(id);
    let priceRequest = 0;

    document.addEventListener('DOMContentLoaded', init);

    function newArmy(factionId = null) {
        return { id: null, name: '', faction_id: factionId, detachment_keys: [], points_limit: DEFAULT_LIMIT, units: [] };
    }

    function newUid() {
        return 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    }

    async function init() {
        $('new-army-btn').addEventListener('click', () => confirmDiscard() && openArmy(newArmy(state.army.faction_id)));
        $('army-name').addEventListener('input', e => { state.army.name = e.target.value; markDirty(); });
        $('army-faction').addEventListener('change', e => changeFaction(e.target.value ? parseInt(e.target.value) : null));
        $('army-detachments').addEventListener('change', () => {
            state.army.detachment_keys = [...$('army-detachments').querySelectorAll('input:checked')].map(cb => cb.dataset.key);
            changed();
        });
        $('army-limit').addEventListener('change', e => { state.army.points_limit = e.target.value ? parseInt(e.target.value) : null; changed(); });
        $('unit-search').addEventListener('input', renderPicker);
        $('unit-legends').addEventListener('change', loadFactionUnits);
        $('army-units').addEventListener('change', onUnitsChange);
        $('army-units').addEventListener('click', onUnitsClick);
        $('save-army-btn').addEventListener('click', () => saveArmy());
        $('attackers-btn').addEventListener('click', () => analyse('attacker'));
        $('defenders-btn').addEventListener('click', () => analyse('defender'));
        $('text-btn').addEventListener('click', showText);
        $('copy-text-btn').addEventListener('click', copyText);
        document.querySelectorAll('[data-close]').forEach(btn =>
            btn.addEventListener('click', () => $(btn.dataset.close).classList.remove('show')));
        window.addEventListener('pagehide', () => PageState.save(PAGE_STATE_KEY, { army: state.army, dirty: state.dirty }));
        window.addEventListener('beforeunload', e => { if (state.dirty && state.army.units.length) e.preventDefault(); });

        loadArmyList();
        const saved = PageState.load(PAGE_STATE_KEY);
        await openArmy(saved?.army || newArmy(), !!saved?.dirty);
    }

    // =========================
    // The army being built
    // =========================

    /** Show an army (a new one, a saved one, or the draft kept across tab changes). */
    async function openArmy(army, dirty = false) {
        state.army = { ...newArmy(), ...army, units: army.units || [], detachment_keys: Core.detachmentKeys(army.detachment_keys ?? army.detachment_key) };
        delete state.army.detachment_key;  // Drafts kept from before several detachments could be chosen
        state.dirty = dirty;
        $('army-name').value = state.army.name || '';
        $('army-faction').value = state.army.faction_id || '';
        $('army-limit').value = state.army.points_limit || '';
        await Promise.all([loadDetachments(), loadFactionUnits()]);
        $('army-detachments-section').open = !state.army.detachment_keys.length;  // Folded once some are chosen
        await reprice();
        renderArmyList();
    }

    async function changeFaction(factionId) {
        if (state.army.units.length && !confirm('Changing the faction removes the units in this army. Carry on?')) {
            $('army-faction').value = state.army.faction_id || '';
            return;
        }
        state.army.faction_id = factionId;
        state.army.detachment_keys = [];
        state.army.units = [];
        await Promise.all([loadDetachments(), loadFactionUnits()]);
        $('army-detachments-section').open = true;
        changed();
    }

    async function loadDetachments() {
        state.detachments = state.army.faction_id
            ? await fetch(`/api/detachments?faction_id=${state.army.faction_id}`).then(r => r.json()).catch(() => [])
            : [];
        state.army.detachment_keys = state.army.detachment_keys.filter(key => state.detachments.some(d => d.key === key));
        renderDetachments();
    }

    /** The faction's detachments to tick, cheapest first, with their DP, force dispositions and Unique Tags. */
    function renderDetachments() {
        const list = $('army-detachments');
        if (!state.army.faction_id) {
            list.innerHTML = '<p class="empty-message">Choose a faction first</p>';
            return;
        }
        if (!state.detachments.length) {
            list.innerHTML = '<p class="empty-message">This faction has no detachments in the data</p>';
            return;
        }
        const chosen = new Set(state.army.detachment_keys);
        const sorted = [...state.detachments].sort((a, b) => (a.dp ?? 9) - (b.dp ?? 9) || a.name.localeCompare(b.name));
        list.innerHTML = sorted.map(d => `<label class="army-detachment ${chosen.has(d.key) ? 'chosen' : ''}">
            <input type="checkbox" data-key="${escapeHtml(d.key)}" ${chosen.has(d.key) ? 'checked' : ''}>
            <span class="army-detachment-name">${escapeHtml(d.name)}</span>
            <span class="army-detachment-meta">${[d.dp != null ? `${d.dp} DP` : null, ...(d.dispositions || [])].filter(Boolean).map(escapeHtml).join(' · ')}</span>
            ${(d.tags || []).map(t => `<span class="army-detachment-tag" title="Unique Tag: only one detachment with it">${escapeHtml(t)}</span>`).join('')}
        </label>`).join('');
    }

    function markDirty() {
        state.dirty = true;
        renderSavedState();
    }

    /** Something that changes the price or the checks: mark unsaved and ask the server again. */
    function changed() {
        markDirty();
        reprice();
    }

    async function reprice() {
        const request = ++priceRequest;
        if (!state.army.faction_id) {
            state.priced = null;
            render();
            return;
        }
        try {
            const resp = await fetch('/api/armies/price', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.army),
            });
            const priced = await resp.json();
            if (request !== priceRequest) return;  // A newer change is on its way
            if (!resp.ok) throw new Error(priced.error);
            state.priced = priced;
            // The server fills in sizes and drops attachments to removed units
            state.army.units = priced.units.map(u => ({ uid: u.uid, unit_id: u.unit_id, models: u.models, attached_to: u.attached_to }));
        } catch (err) {
            console.error('Error pricing army:', err);
        }
        render();
    }

    // =========================
    // Drawing
    // =========================

    function render() {
        const priced = state.priced;
        const points = Core.armyPoints(priced?.total || 0, state.army.points_limit);
        const pointsEl = $('army-points');
        pointsEl.querySelector('.army-points-total').textContent = points.text;
        pointsEl.classList.toggle('over', points.over);
        const dp = priced && priced.detachments.length ? Core.armyDetachmentPoints(priced.dp_total, priced.dp_limit) : null;
        $('army-dp').textContent = dp ? dp.text : '';
        const names = (priced?.detachments || []).map(d => d.name);
        $('army-detachments-chosen').textContent = names.length ? names.join(' + ') : 'None chosen';
        $('army-dp').classList.toggle('over', !!dp?.over);
        renderDetachments();

        const problems = priced?.problems || [];
        $('army-problems').innerHTML = problems.map(p => `<li>${escapeHtml(p)}</li>`).join('');

        const units = priced?.units || [];
        const labelled = units.map(u => ({ unit_id: u.unit_id, unit_name: u.name }));
        const labels = new Map(units.map((u, i) => [u.uid, copyLabel(labelled, i)]));
        $('army-units').innerHTML = units.length ? units.map((u, i) => unitRow(u, labels)).join('') :
            `<tr><td colspan="5" class="empty-cell">${state.army.faction_id ? 'No units yet: add them from the list on the right.' : 'Choose a faction to start.'}</td></tr>`;
        renderSavedState();
    }

    function unitRow(u, labels) {
        const sizes = u.sizes.length > 1
            ? `<select class="filter-select army-size" data-uid="${escapeHtml(u.uid)}" title="Models in the unit">${u.sizes.map((s, i) =>
                `<option value="${s}" ${s === u.models ? 'selected' : ''}>${s}${u.size_points[i] != null ? ` (${u.size_points[i]} pts)` : ''}</option>`).join('')}</select>`
            : `${u.models || ''}`;
        let leader = '';
        if (u.is_leader) {
            leader = `<select class="filter-select army-attach" data-uid="${escapeHtml(u.uid)}" title="The unit this leader joins">
                <option value="">On its own</option>
                ${u.can_join.map(uid => `<option value="${escapeHtml(uid)}" ${uid === u.attached_to ? 'selected' : ''}>Leads ${escapeHtml(labels.get(uid))}</option>`).join('')}
            </select>`;
        } else if (u.led_by.length) {
            leader = `<span class="muted">Led by ${u.led_by.map(uid => escapeHtml(labels.get(uid))).join(', ')}</span>`;
        }
        return `<tr class="${u.problems.length ? 'army-unit-problem' : ''} ${u.attached_to ? 'army-unit-attached' : ''}">
            <td>${escapeHtml(labels.get(u.uid))}${u.problems.length ? ` <span class="army-unit-warning" title="${escapeHtml(u.problems.join(' '))}">!</span>` : ''}</td>
            <td>${sizes}</td>
            <td>${u.points ?? '-'}</td>
            <td>${leader}</td>
            <td class="col-remove">
                <button class="btn-small army-copy-unit" data-uid="${escapeHtml(u.uid)}" title="Add another">+1</button>
                <button class="btn-remove army-remove-unit" data-uid="${escapeHtml(u.uid)}" title="Remove"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>
            </td>
        </tr>`;
    }

    function renderSavedState() {
        const label = !state.army.id ? 'Not saved yet' : state.dirty ? 'Unsaved changes' : 'Saved';
        $('army-saved-state').textContent = label;
        $('army-saved-state').classList.toggle('unsaved', state.dirty || !state.army.id);
    }

    // =========================
    // Editing the units
    // =========================

    function findUnit(uid) {
        return state.army.units.find(u => u.uid === uid);
    }

    function onUnitsChange(e) {
        const unit = findUnit(e.target.dataset.uid);
        if (!unit) return;
        if (e.target.classList.contains('army-size')) unit.models = parseInt(e.target.value);
        if (e.target.classList.contains('army-attach')) unit.attached_to = e.target.value || null;
        changed();
    }

    function onUnitsClick(e) {
        const copyBtn = e.target.closest('.army-copy-unit');
        const removeBtn = e.target.closest('.army-remove-unit');
        if (copyBtn) {
            const unit = findUnit(copyBtn.dataset.uid);
            const at = state.army.units.indexOf(unit);
            state.army.units.splice(at + 1, 0, { uid: newUid(), unit_id: unit.unit_id, models: unit.models, attached_to: null });
        } else if (removeBtn) {
            state.army.units = state.army.units.filter(u => u.uid !== removeBtn.dataset.uid);
        } else {
            return;
        }
        changed();
    }

    function addUnit(unitId) {
        state.army.units.push({ uid: newUid(), unit_id: unitId, models: null, attached_to: null });  // Server picks the size
        changed();
    }

    // =========================
    // Add units: the faction's datasheets
    // =========================

    async function loadFactionUnits() {
        if (!state.army.faction_id) {
            state.factionUnits = [];
        } else {
            const legends = $('unit-legends').checked ? '&legends=1' : '';
            state.factionUnits = await fetch(`/api/units?faction_id=${state.army.faction_id}${legends}`)
                .then(r => r.json()).catch(() => []);
        }
        renderPicker();
    }

    function renderPicker() {
        const picker = $('unit-picker');
        if (!state.army.faction_id) {
            picker.innerHTML = '<p class="empty-message">Choose a faction first</p>';
            return;
        }
        const search = $('unit-search').value.trim().toLowerCase();
        const units = state.factionUnits.filter(u => !search || u.name.toLowerCase().includes(search));
        picker.innerHTML = units.length ? units.map(u => {
            const smallest = (u.points_by_size || [])[0];
            const roles = ['Character', 'Battleline', 'Epic Hero'].filter(k => (u.keywords || []).some(kw => kw.toLowerCase() === k.toLowerCase()));
            return `<button type="button" class="unit-picker-item" data-unit-id="${u.id}">
                <span class="unit-picker-name">${escapeHtml(u.name)}${u.legends ? ' <span class="legends-tag">Legends</span>' : ''}</span>
                <span class="unit-picker-faction">${roles.map(escapeHtml).join(' · ')}</span>
                <span class="unit-picker-stats">${smallest ? `${smallest.models} for ${smallest.points} pts` : 'No points'}</span>
            </button>`;
        }).join('') : '<p class="empty-message">No units found</p>';
        picker.querySelectorAll('.unit-picker-item').forEach(btn =>
            btn.addEventListener('click', () => addUnit(parseInt(btn.dataset.unitId))));
    }

    // =========================
    // Saved armies
    // =========================

    async function loadArmyList() {
        state.savedArmies = await fetch('/api/armies').then(r => r.json()).catch(() => []);
        renderArmyList();
    }

    function renderArmyList() {
        const list = $('army-list');
        const armies = state.savedArmies || [];
        if (!armies.length) {
            list.innerHTML = '<p class="empty-message">No saved armies yet</p>';
            return;
        }
        list.innerHTML = armies.map(a => {
            const points = Core.armyPoints(a.total, a.points_limit);
            return `<div class="army-list-item ${a.id === state.army.id ? 'active' : ''}">
                <button type="button" class="army-open" data-id="${a.id}">
                    <span class="army-list-name">${escapeHtml(a.name)}</span>
                    <span class="army-list-meta">${escapeHtml([a.faction, a.detachment].filter(Boolean).join(' · '))}</span>
                    <span class="army-list-meta ${points.over ? 'over' : ''}">${points.text} · ${a.unit_count} units${a.problem_count ? ` · ${a.problem_count} to fix` : ''}</span>
                </button>
                <div class="army-list-actions">
                    <button class="btn-small army-duplicate" data-id="${a.id}" title="Make a copy"><i class="fa-solid fa-copy" aria-hidden="true"></i></button>
                    <button class="btn-small btn-danger army-delete" data-id="${a.id}" title="Delete"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>
                </div>
            </div>`;
        }).join('');
        list.querySelectorAll('.army-open').forEach(btn => btn.addEventListener('click', () => openSaved(parseInt(btn.dataset.id))));
        list.querySelectorAll('.army-duplicate').forEach(btn => btn.addEventListener('click', () => duplicateArmy(parseInt(btn.dataset.id))));
        list.querySelectorAll('.army-delete').forEach(btn => btn.addEventListener('click', () => deleteArmy(parseInt(btn.dataset.id))));
    }

    function confirmDiscard() {
        return !state.dirty || !state.army.units.length || confirm('Discard the unsaved changes to this army?');
    }

    async function openSaved(id) {
        if (id === state.army.id && !state.dirty) return;
        if (!confirmDiscard()) return;
        const army = await fetch(`/api/armies/${id}`).then(r => r.json());
        await openArmy(armyFields(army));
    }

    function armyFields(saved) {
        return {
            id: saved.id, name: saved.name, faction_id: saved.faction_id, detachment_keys: saved.detachment_keys || [],
            points_limit: saved.points_limit,
            units: (saved.units || []).map(u => ({ uid: u.uid, unit_id: u.unit_id, models: u.models, attached_to: u.attached_to })),
        };
    }

    /** Save the army (new or existing); returns its id, or null if it couldn't be saved. */
    async function saveArmy() {
        if (!state.army.faction_id) {
            alert('Choose a faction first.');
            return null;
        }
        const id = state.army.id;
        const resp = await fetch(id ? `/api/armies/${id}` : '/api/armies', {
            method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.army),
        });
        const saved = await resp.json();
        if (!resp.ok) {
            alert(saved.error || 'The army could not be saved.');
            return null;
        }
        state.army.id = saved.id;
        state.army.name = saved.name;
        $('army-name').value = saved.name;
        state.dirty = false;
        renderSavedState();
        await loadArmyList();
        return saved.id;
    }

    async function duplicateArmy(id) {
        if (!confirmDiscard()) return;
        const copy = await fetch(`/api/armies/${id}/copy`, { method: 'POST' }).then(r => r.json());
        await loadArmyList();
        await openArmy(armyFields(copy));
    }

    async function deleteArmy(id) {
        const army = (state.savedArmies || []).find(a => a.id === id);
        if (!confirm(`Delete "${army?.name || 'this army'}"?`)) return;
        await fetch(`/api/armies/${id}`, { method: 'DELETE' });
        if (state.army.id === id) {
            state.army.id = null;
            state.dirty = true;
        }
        await loadArmyList();
        renderSavedState();
    }

    // =========================
    // Using the army
    // =========================

    async function analyse(side) {
        if (!state.army.units.length) {
            alert('Add some units first.');
            return;
        }
        const id = state.dirty || !state.army.id ? await saveArmy() : state.army.id;
        if (id) window.location.href = `/analytics?army=${id}&side=${side}`;
    }

    async function showText() {
        if (!state.army.faction_id) return;
        const resp = await fetch('/api/armies/text', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...state.army, name: state.army.name || 'New army' }),
        });
        const data = await resp.json();
        $('army-text').value = data.text || data.error || '';
        $('army-copied').textContent = '';
        $('text-modal').classList.add('show');
    }

    async function copyText() {
        const text = $('army-text').value;
        try {
            await navigator.clipboard.writeText(text);
        } catch (err) {
            $('army-text').select();
            document.execCommand('copy');
        }
        $('army-copied').textContent = 'Copied';
    }
})();
