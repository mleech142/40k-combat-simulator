/**
 * Armies page: build an army list, save it, share it as text or a share code (and import one), send
 * it to Analytics and open its Match view (match.js).
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
        // {id (null until saved), name, faction_id, detachment_keys, points_limit, warlord (a uid),
        //  units: [{uid, unit_id, models, attached_to, enhancement}]}
        army: newArmy(),
        priced: null,  // /api/armies/price answer for state.army
        dirty: false,  // Changed since it was last saved
        factionUnits: [],  // /api/units for the army's faction (picker)
        detachments: [],
        wargear: null,  // The popup: {uid, unit_id, models, choices, result} while it is open
    };
    const $ = id => document.getElementById(id);
    let priceRequest = 0;

    document.addEventListener('DOMContentLoaded', init);

    function newArmy(factionId = null) {
        return { id: null, name: '', faction_id: factionId, detachment_keys: [], points_limit: DEFAULT_LIMIT, warlord: null, disposition: null, units: [] };
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
            const offered = state.detachments.filter(d => state.army.detachment_keys.includes(d.key)).flatMap(d => d.dispositions || []);
            if (!offered.includes(state.army.disposition)) state.army.disposition = null;  // Its detachment was unticked
            changed();
        });
        $('army-disposition').addEventListener('change', e => { state.army.disposition = e.target.value || null; changed(); });
        $('army-limit').addEventListener('change', e => { state.army.points_limit = e.target.value ? parseInt(e.target.value) : null; changed(); });
        $('unit-search').addEventListener('input', renderPicker);
        $('unit-legends').addEventListener('change', loadFactionUnits);
        $('army-units').addEventListener('change', onUnitsChange);
        $('army-units').addEventListener('click', onUnitsClick);
        $('save-army-btn').addEventListener('click', () => saveArmy());
        $('attackers-btn').addEventListener('click', () => analyse('attacker'));
        $('defenders-btn').addEventListener('click', () => analyse('defender'));
        $('match-btn').addEventListener('click', openMatchView);
        $('text-btn').addEventListener('click', showText);
        $('copy-text-btn').addEventListener('click', () => copyFrom('army-text', 'army-copied'));
        $('share-btn').addEventListener('click', showShareCode);
        $('copy-share-btn').addEventListener('click', () => copyFrom('share-code', 'share-copied'));
        $('import-army-btn').addEventListener('click', () => {
            $('import-code').value = '';
            $('import-error').textContent = '';
            $('import-modal').classList.add('show');
            $('import-code').focus();
        });
        $('import-apply').addEventListener('click', importCode);
        $('wargear-sections').addEventListener('change', onWargearChange);
        $('wargear-size').addEventListener('change', e => { state.wargear.models = parseInt(e.target.value); loadWargear(); });
        $('wargear-apply').addEventListener('click', () => closeWargear(true));
        $('wargear-reset').addEventListener('click', () => closeWargear(false, true));
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

    /** Show an army (a new one, a saved one, the draft kept across tab changes, or an imported one with its notes). */
    async function openArmy(army, dirty = false, notes = []) {
        $('army-import-notes').innerHTML = notes.map(n => `<li>${escapeHtml(n)}</li>`).join('');
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

    /** The force disposition: one of the chosen detachments' (it decides the primary mission in Play). */
    function renderDisposition() {
        const priced = state.priced;
        const options = priced?.disposition_options || [];
        $('army-disposition-row').hidden = !options.length;
        $('army-disposition').innerHTML = options.map(d => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`).join('');
        $('army-disposition').value = priced?.disposition || '';
        $('army-disposition').disabled = options.length < 2;
        const note = $('army-disposition-note');
        note.innerHTML = priced?.disposition_unconfirmed
            ? 'It decides your primary mission in Play. <button class="btn-small" id="army-disposition-confirm">Confirm</button>' : '';
        if (priced?.disposition_unconfirmed) {
            $('army-disposition-confirm').addEventListener('click', () => { state.army.disposition = priced.disposition; changed(); });
        }
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
            state.army.units = priced.units.map(u => ({ uid: u.uid, unit_id: u.unit_id, models: u.models, attached_to: u.attached_to,
                                                         enhancement: u.enhancement, wargear: u.wargear }));
            state.army.warlord = priced.warlord;
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
        renderDisposition();

        const problems = priced?.problems || [];
        $('army-problems').innerHTML = problems.map(p => `<li>${escapeHtml(p)}</li>`).join('');

        const units = priced?.units || [];
        const labelled = units.map(u => ({ unit_id: u.unit_id, unit_name: u.name }));
        const labels = new Map(units.map((u, i) => [u.uid, copyLabel(labelled, i)]));
        const enhancements = priced ? `${priced.enhancement_count} / ${priced.enhancement_limit} enhancements` : '';
        $('army-enhancement-count').textContent = priced && (priced.enhancement_count || units.some(u => u.enhancement_options.length)) ? enhancements : '';
        $('army-enhancement-count').classList.toggle('over', !!priced && priced.enhancement_count > priced.enhancement_limit);
        $('army-units').innerHTML = units.length ? units.map(u => unitRow(u, labels)).join('') :
            `<tr><td colspan="7" class="empty-cell">${state.army.faction_id ? 'No units yet: add them from the list on the right.' : 'Choose a faction to start.'}</td></tr>`;
        renderSavedState();
    }

    function unitRow(u, labels) {
        const sizes = u.sizes.length > 1
            ? `<select class="filter-select army-size" data-uid="${escapeHtml(u.uid)}" title="Models in the unit">${u.sizes.map((s, i) =>
                `<option value="${s}" ${s === u.models ? 'selected' : ''}>${s}${u.size_points[i] != null ? ` (${u.size_points[i]} pts)` : ''}</option>`).join('')}</select>`
            : `${u.models || ''}`;
        let joins = '';
        if (u.is_leader || u.is_support) {
            const role = uid => (u.join_roles[uid] === 'supports' ? 'Supports ' : 'Leads ') + labels.get(uid);
            joins = `<select class="filter-select army-attach" data-uid="${escapeHtml(u.uid)}" title="The unit this one joins">
                <option value="">On its own</option>
                ${u.can_join.map(uid => `<option value="${escapeHtml(uid)}" ${uid === u.attached_to ? 'selected' : ''}>${escapeHtml(role(uid))}</option>`).join('')}
            </select>`;
        }
        const joinedBy = [...u.led_by.map(uid => `Led by ${labels.get(uid)}`), ...u.supported_by.map(uid => `Supported by ${labels.get(uid)}`)];
        if (joinedBy.length) joins += `<span class="muted army-joined-by">${joinedBy.map(escapeHtml).join('; ')}</span>`;
        const enhancement = u.enhancement_options.length || u.enhancement
            ? `<select class="filter-select army-enhancement" data-uid="${escapeHtml(u.uid)}" title="Enhancement">
                <option value="">None</option>
                ${u.enhancement_options.map(o => `<option value="${escapeHtml(o.key)}" ${o.key === u.enhancement ? 'selected' : ''}>${escapeHtml(o.name)} (${o.points} pts${o.upgrade ? ', Upgrade' : ''})</option>`).join('')}
            </select>`
            : '';
        const warlord = u.is_character
            ? `<input type="radio" name="army-warlord" class="army-warlord" data-uid="${escapeHtml(u.uid)}" title="Warlord" ${state.army.warlord === u.uid ? 'checked' : ''}>`
            : '';
        const wargear = u.has_wargear
            ? `<button class="btn-small army-wargear-btn" data-uid="${escapeHtml(u.uid)}" title="Choose this unit's wargear">Wargear</button>
               ${u.wargear_summary ? `<span class="army-wargear-summary">${escapeHtml(u.wargear_summary)}</span>` : ''}`
            : '';
        return `<tr class="${u.problems.length ? 'army-unit-problem' : ''} ${u.attached_to ? 'army-unit-attached' : ''}">
            <td class="col-check">${warlord}</td>
            <td>${escapeHtml(labels.get(u.uid))}${state.army.warlord === u.uid ? ' <span class="army-warlord-tag">Warlord</span>' : ''}${u.problems.length ? ` <span class="army-unit-warning" title="${escapeHtml(u.problems.join(' '))}">!</span>` : ''}
                <div class="army-wargear">${wargear}</div></td>
            <td>${sizes}</td>
            <td title="${u.enhancement_points ? `Unit ${u.unit_points} + enhancement ${u.enhancement_points}` : ''}">${u.points ?? '-'}</td>
            <td>${joins}</td>
            <td>${enhancement}</td>
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
        if (e.target.classList.contains('army-enhancement')) unit.enhancement = e.target.value || null;
        if (e.target.classList.contains('army-warlord')) state.army.warlord = unit.uid;
        changed();
    }

    function onUnitsClick(e) {
        const wargearBtn = e.target.closest('.army-wargear-btn');
        if (wargearBtn) {
            openWargear(wargearBtn.dataset.uid);
            return;
        }
        const copyBtn = e.target.closest('.army-copy-unit');
        const removeBtn = e.target.closest('.army-remove-unit');
        if (copyBtn) {
            const unit = findUnit(copyBtn.dataset.uid);
            const at = state.army.units.indexOf(unit);
            state.army.units.splice(at + 1, 0, { uid: newUid(), unit_id: unit.unit_id, models: unit.models, attached_to: null,
                                                 enhancement: null, wargear: unit.wargear ? { ...unit.wargear } : null });
        } else if (removeBtn) {
            state.army.units = state.army.units.filter(u => u.uid !== removeBtn.dataset.uid);
        } else {
            return;
        }
        changed();
    }

    function addUnit(unitId) {
        state.army.units.push({ uid: newUid(), unit_id: unitId, models: null, attached_to: null, enhancement: null });  // Server picks the size
        changed();
    }

    // =========================
    // Wargear popup: BSData's choices for one unit, checked and priced by the server as they change
    // =========================

    async function openWargear(uid) {
        const unit = findUnit(uid);
        if (!unit) return;
        state.wargear = { uid, unit_id: unit.unit_id, models: unit.models, choices: unit.wargear ? { ...unit.wargear } : null, result: null };
        $('wargear-sections').innerHTML = '<p class="empty-message">Loading…</p>';
        $('wargear-modal').classList.add('show');
        await loadWargear();
    }

    async function loadWargear() {
        const edit = state.wargear;
        if (!edit) return;
        try {
            const resp = await fetch('/api/armies/wargear', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ unit_id: edit.unit_id, faction_id: state.army.faction_id, models: edit.models, wargear: edit.choices }),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.error);
            if (state.wargear !== edit) return;  // Closed meanwhile
            edit.result = result;
            edit.choices = result.wargear;
            edit.models = result.models;
            renderWargear();
        } catch (err) {
            $('wargear-sections').innerHTML = `<p class="empty-message">${escapeHtml(err.message || 'The wargear could not be loaded.')}</p>`;
        }
    }

    function renderWargear() {
        const { result, models } = state.wargear;
        $('wargear-title').textContent = `Wargear: ${result.name}`;
        $('wargear-size').innerHTML = result.sizes.map(s => `<option value="${s}" ${s === models ? 'selected' : ''}>${s}</option>`).join('');
        $('wargear-size').disabled = result.sizes.length < 2;
        $('wargear-points').textContent = `${result.points} pts`;
        $('wargear-problems').innerHTML = result.problems.map(p => `<li>${escapeHtml(p)}</li>`).join('');
        $('wargear-sections').innerHTML = result.form.length
            ? result.form.map(wargearSection).join('')
            : '<p class="empty-message">Nothing to choose at this size.</p>';
        $('wargear-loadout').innerHTML = result.loadout.length
            ? `<strong>Loadout:</strong> ${result.loadout.map(w => escapeHtml(w.qty > 1 ? `${w.qty}× ${w.name}` : w.name)).join(', ')}` : '';
    }

    /** One choice in the popup: radio buttons (pick one), number boxes (how many of each) or a tick box. */
    function wargearSection(section) {
        const range = section.kind === 'counts'
            ? ` <span class="wargear-range">${section.total} chosen (${section.min ?? 0}–${section.max ?? 'any'})</span>` : '';
        let body;
        if (section.kind === 'one') {
            const none = !(section.min > 0)
                ? `<label class="wargear-option"><input type="radio" name="wg-${section.k}" data-section="${section.k}" value="" ${section.total ? '' : 'checked'}> None</label>`
                : '';
            body = none + section.members.map(m => `<label class="wargear-option">
                <input type="radio" name="wg-${section.k}" data-section="${section.k}" value="${m.k}" ${m.count ? 'checked' : ''}>
                ${escapeHtml(m.name)}${m.default ? ' <span class="muted">(default)</span>' : ''}</label>`).join('');
        } else if (section.kind === 'optional') {
            const m = section.members[0];
            body = (m.max ?? 1) > 1
                ? `<label class="wargear-option"><input type="number" class="filter-input wargear-count" data-k="${m.k}" min="0" max="${m.max}" value="${m.count}"> ${escapeHtml(m.name)}</label>`
                : `<label class="wargear-option"><input type="checkbox" data-k="${m.k}" ${m.count ? 'checked' : ''}> ${escapeHtml(m.name)}</label>`;
        } else {
            body = section.members.map(m => `<label class="wargear-option">
                <input type="number" class="filter-input wargear-count" data-k="${m.k}" min="0" max="${m.max ?? section.max ?? 99}" value="${m.count}">
                ${escapeHtml(m.name)}${m.max != null ? ` <span class="muted">(max ${m.max})</span>` : ''}</label>`).join('');
        }
        return `<fieldset class="wargear-section"><legend>${escapeHtml(section.title)}${range}</legend>${body}</fieldset>`;
    }

    function onWargearChange(e) {
        const edit = state.wargear;
        if (!edit?.result) return;
        const el = e.target;
        const choices = { ...edit.choices };
        if (el.type === 'radio') {
            const section = edit.result.form.find(s => String(s.k) === el.dataset.section);
            section.members.forEach(m => { choices[m.k] = 0; });
            if (el.value) choices[el.value] = 1;
        } else if (el.type === 'checkbox') {
            choices[el.dataset.k] = el.checked ? 1 : 0;
        } else if (el.dataset.k) {
            const max = parseInt(el.max);
            choices[el.dataset.k] = Math.max(0, Math.min(parseInt(el.value) || 0, isNaN(max) ? 99 : max));
        }
        edit.choices = choices;
        loadWargear();
    }

    /** Close the popup: Apply keeps the choices and size; Defaults goes back to the datasheet's wargear. */
    function closeWargear(apply = false, reset = false) {
        const edit = state.wargear;
        const unit = edit && findUnit(edit.uid);
        if (unit && (apply || reset)) {
            unit.wargear = reset ? null : edit.choices;
            unit.models = edit.models;
            changed();
        }
        state.wargear = null;
        $('wargear-modal').classList.remove('show');
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
                    ${a.mine === false ? `<span class="army-list-meta">${escapeHtml(a.owner || 'Another player')}'s · read-only</span>` : ''}
                    <span class="army-list-meta">${escapeHtml([a.faction, a.detachment].filter(Boolean).join(' · '))}</span>
                    <span class="army-list-meta ${points.over ? 'over' : ''}">${points.text} · ${a.unit_count} units${a.problem_count ? ` · ${a.problem_count} to fix` : ''}</span>
                </button>
                <div class="army-list-actions">
                    <button class="btn-small army-duplicate" data-id="${a.id}" title="Make a copy"><i class="fa-solid fa-copy" aria-hidden="true"></i></button>
                    ${a.mine === false ? '' : `<button class="btn-small btn-danger army-delete" data-id="${a.id}" title="Delete"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>`}
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
        if (army.mine === false) {  // Another player's (a self-hosted copy): saving makes your own copy
            await openArmy({ ...armyFields(army), id: null },
                           false, [`This is ${army.owner || 'another player'}'s army. Saving it makes a copy of your own.`]);
            return;
        }
        await openArmy(armyFields(army));
    }

    function armyFields(saved) {
        return {
            id: saved.id, name: saved.name, faction_id: saved.faction_id, detachment_keys: saved.detachment_keys || [],
            points_limit: saved.points_limit, warlord: saved.warlord || null,
            units: (saved.units || []).map(u => ({ uid: u.uid, unit_id: u.unit_id, models: u.models, attached_to: u.attached_to,
                                                    enhancement: u.enhancement || null, wargear: u.wargear || null })),
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

    /** Save, then show the army's Match view: its datasheets, rules and stratagems for the table. */
    async function openMatchView() {
        if (!state.army.units.length) {
            alert('Add some units first.');
            return;
        }
        const id = state.dirty || !state.army.id ? await saveArmy() : state.army.id;
        if (id) window.location.href = `/armies/${id}/match`;
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

    /** A share code for the list as it stands (saved or not): units by their BSData IDs, wargear included. */
    async function showShareCode() {
        if (!state.army.faction_id || !state.army.units.length) {
            alert('Add some units first.');
            return;
        }
        const resp = await fetch('/api/armies/share', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...state.army, name: state.army.name || 'New army' }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            alert(data.error || 'This list could not be shared.');
            return;
        }
        $('share-code').value = data.code;
        $('share-copied').textContent = '';
        $('share-modal').classList.add('show');
        $('share-code').select();
    }

    /** Open a shared list as a new, unsaved army; anything not in this install's data is listed above the units. */
    async function importCode() {
        const code = $('import-code').value.trim();
        if (!code) return;
        const resp = await fetch('/api/armies/import', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            $('import-error').textContent = data.error || 'The code could not be imported.';
            return;
        }
        if (!confirmDiscard()) return;
        $('import-modal').classList.remove('show');
        await openArmy({ ...data.army, id: null }, true, data.notes || []);
    }

    async function copyFrom(textareaId, labelId) {
        const text = $(textareaId).value;
        try {
            await navigator.clipboard.writeText(text);
        } catch (err) {
            $(textareaId).select();
            document.execCommand('copy');
        }
        $(labelId).textContent = 'Copied';
    }
})();
