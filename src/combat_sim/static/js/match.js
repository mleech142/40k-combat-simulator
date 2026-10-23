/**
 * Match view: a saved army as the table needs it. Every unit's datasheet (characteristics,
 * weapons at its size and wargear, abilities, keywords, enhancement, who it leads), the army's
 * rules and enhancements, and the stratagems it can use, filtered by phase and turn.
 *
 * Everything comes from GET /api/armies/<id>/match (match_view.py); this file only draws it.
 */

(function() {
    'use strict';

    const { escapeHtml, formatRuleText, copyLabel } = Core;
    const PAGE_STATE_KEY = 'matchView';
    const PHASES = ['Command phase', 'Movement phase', 'Shooting phase', 'Charge phase', 'Fight phase'];
    const TURNS = [['all', 'Either turn'], ['yours', 'Your turn'], ['theirs', "Opponent's turn"]];
    const $ = id => document.getElementById(id);

    const state = {
        armyId: parseInt($('match-view').dataset.armyId),
        view: null,
        labels: new Map(),  // uid -> unit name, numbered when the army has two of a datasheet
        tab: 'units',
        phase: 'all',
        turn: 'all',
        search: '',
    };

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        const saved = PageState.load(PAGE_STATE_KEY) || {};
        const linked = ['units', 'rules', 'stratagems'].find(t => window.location.hash === `#${t}`);
        Object.assign(state, { tab: linked || saved.tab || 'units', phase: saved.phase || 'all', turn: saved.turn || 'all' });
        document.querySelectorAll('.match-tab').forEach(btn => btn.addEventListener('click', () => showTab(btn.dataset.tab)));
        $('match-search').addEventListener('input', e => { state.search = e.target.value; applySearch(); });
        $('match-expand').addEventListener('click', () => setOpen(true));
        $('match-collapse').addEventListener('click', () => setOpen(false));
        $('match-print').addEventListener('click', () => window.print());
        $('match-army').addEventListener('change', e => { window.location.href = `/armies/${e.target.value}/match`; });
        $('match-roster').addEventListener('click', onRosterClick);
        $('match-units').addEventListener('click', onFactionTag);
        $('match-phase-filter').addEventListener('click', e => onChip(e, 'phase'));
        $('match-turn-filter').addEventListener('click', e => onChip(e, 'turn'));
        window.addEventListener('pagehide', () => PageState.save(PAGE_STATE_KEY, { tab: state.tab, phase: state.phase, turn: state.turn }));
        // Printing shows everything: every pane, every datasheet open
        let reopen = [];
        window.addEventListener('beforeprint', () => {
            reopen = [...document.querySelectorAll('.match-unit:not([open])')];
            reopen.forEach(d => { d.open = true; });
        });
        window.addEventListener('afterprint', () => reopen.forEach(d => { d.open = false; }));

        loadArmyList();
        try {
            const resp = await fetch(`/api/armies/${state.armyId}/match`);
            if (!resp.ok) throw new Error((await resp.json()).error || 'The army could not be loaded.');
            state.view = await resp.json();
        } catch (err) {
            $('match-units').innerHTML = `<p class="empty-message">${escapeHtml(err.message)}</p>`;
            return;
        }
        render();
        showTab(state.tab);
    }

    async function loadArmyList() {
        const armies = await fetch('/api/armies').then(r => r.json()).catch(() => []);
        $('match-army').innerHTML = armies.map(a =>
            `<option value="${a.id}" ${a.id === state.armyId ? 'selected' : ''}>${escapeHtml(a.name)}</option>`).join('');
        $('match-army').hidden = armies.length < 2;
    }

    function showTab(tab) {
        state.tab = tab;
        document.querySelectorAll('.match-tab').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tab);
            btn.setAttribute('aria-selected', btn.dataset.tab === tab);
        });
        document.querySelectorAll('.match-pane').forEach(pane => { pane.hidden = pane.dataset.pane !== tab; });
        history.replaceState(null, '', `#${tab}`);
    }

    function render() {
        const v = state.view;
        const labelled = v.units.map(u => ({ unit_id: u.unit_id, unit_name: u.name }));
        state.labels = new Map(v.units.map((u, i) => [u.uid, copyLabel(labelled, i)]));
        renderHeader();
        renderUnits();
        renderRules();
        renderStratagems();
    }

    // =========================
    // Header
    // =========================

    function renderHeader() {
        const v = state.view;
        document.title = `${v.name} - Match view - 40K Combat Simulator`;
        $('match-name').textContent = v.name;
        const points = Core.armyPoints(v.points, v.points_limit);
        const warlord = v.units.find(u => u.warlord);
        const parts = [
            v.faction && `<span class="match-meta-faction">${escapeHtml(v.faction)}</span>`,
            v.detachments.length && escapeHtml(v.detachments.map(d => d.name).join(' + ')),
            `<span class="${points.over ? 'over' : ''}">${escapeHtml(points.text)}</span>`,
            v.detachments.length && escapeHtml(Core.armyDetachmentPoints(v.dp_total, v.dp_limit).text),
            v.disposition && escapeHtml(v.disposition),
            warlord && `<i class="fa-solid fa-crown" aria-hidden="true"></i> ${escapeHtml(state.labels.get(warlord.uid))}`,
        ].filter(Boolean);
        $('match-meta').innerHTML = parts.join('<span class="match-meta-sep">·</span>');
        const problems = v.problems || [];
        $('match-problems').hidden = !problems.length;
        $('match-problems').innerHTML = problems.length
            ? `<li><a href="/armies">This list has ${problems.length} thing${problems.length === 1 ? '' : 's'} to fix in the builder:</a> ${problems.map(escapeHtml).join(' ')}</li>` : '';
        $('count-units').textContent = v.units.length;
        $('count-rules').textContent = v.army_rules.length + v.detachments.reduce((n, d) => n + d.rules.length, 0) + v.enhancements.length;
        $('count-stratagems').textContent = v.stratagems.length;
    }

    // =========================
    // Units
    // =========================

    function renderUnits() {
        const v = state.view;
        const byUid = new Map(v.units.map(u => [u.uid, u]));
        if (!v.units.length) {
            $('match-units').innerHTML = '<p class="empty-message">This army has no units yet. Add some on the Armies page.</p>';
            $('match-roster').innerHTML = '';
            return;
        }
        $('match-units').innerHTML = v.groups.map(uids => {
            const units = uids.map(uid => byUid.get(uid)).filter(Boolean);
            const cards = units.map(unitCard).join('');
            if (units.length < 2) return cards;
            const points = units.reduce((n, u) => n + (u.points || 0), 0);
            return `<div class="match-group">
                <div class="match-group-title"><i class="fa-solid fa-link" aria-hidden="true"></i> Attached unit:
                    ${units.map(u => escapeHtml(state.labels.get(u.uid))).join(' + ')} <span class="match-group-points">${points} pts</span></div>
                ${cards}</div>`;
        }).join('');
        $('match-roster').innerHTML = '<h3>Units</h3>' + v.groups.map(uids => uids.map((uid, i) => {
            const u = byUid.get(uid);
            if (!u) return '';
            return `<button type="button" class="match-roster-item ${i ? 'attached' : ''}" data-uid="${escapeHtml(uid)}">
                <span>${u.warlord ? '<i class="fa-solid fa-crown" aria-hidden="true" title="Warlord"></i> ' : ''}${escapeHtml(state.labels.get(uid))}</span>
                <span class="match-roster-points">${u.points ?? '-'}</span></button>`;
        }).join('')).join('');
        applySearch();
    }

    function unitCard(u) {
        const label = state.labels.get(u.uid);
        const chips = [];
        if (u.warlord) chips.push('<span class="match-chip match-chip-warlord"><i class="fa-solid fa-crown" aria-hidden="true"></i> Warlord</span>');
        if (u.join_role && u.attached_to) {
            chips.push(`<span class="match-chip">${u.join_role === 'supports' ? 'Supports' : 'Leads'} ${escapeHtml(state.labels.get(u.attached_to) || '')}</span>`);
        }
        if (u.enhancement) chips.push(`<span class="match-chip match-chip-enhancement"><i class="fa-solid fa-star" aria-hidden="true"></i> ${escapeHtml(u.enhancement.name)}</span>`);
        if (u.legends) chips.push('<span class="legends-tag">Legends</span>');
        if (u.problems.length) chips.push(`<span class="army-unit-warning" title="${escapeHtml(u.problems.join(' '))}">!</span>`);
        const stats = u.stats ? Core.matchStatLine(u.stats) : [];
        const mini = stats.map(s => `<span><b>${s.label}</b> ${escapeHtml(s.value)}</span>`).join('') +
            (u.stats?.invulnerable_save ? `<span class="match-mini-invuln"><b>INV</b> ${u.stats.invulnerable_save}+</span>` : '');

        return `<details class="match-unit" id="unit-${escapeHtml(u.uid)}" data-uid="${escapeHtml(u.uid)}" open>
            <summary class="match-unit-head">
                <div class="match-unit-title">
                    <span class="match-unit-name">${escapeHtml(label)}</span>
                    <span class="match-unit-size">${u.models > 1 ? `${u.models} models` : '1 model'}</span>
                    ${chips.join('')}
                </div>
                <div class="match-unit-mini">${mini}</div>
                <span class="match-unit-points">${u.points ?? '-'} pts</span>
            </summary>
            ${u.stats ? unitBody(u) : '<p class="empty-message">This unit is no longer in the data.</p>'}
        </details>`;
    }

    function unitBody(u) {
        const boxes = Core.matchStatLine(u.stats).map(s =>
            `<div class="match-stat"><span class="match-stat-label">${s.label}</span><span class="match-stat-value">${escapeHtml(s.value)}</span></div>`).join('');
        const saves = [
            u.stats.invulnerable_save && `<div class="match-save"><i class="fa-solid fa-shield-halved" aria-hidden="true"></i><span><b>${u.stats.invulnerable_save}+</b> invulnerable save</span></div>`,
            u.stats.feel_no_pain && `<div class="match-save"><i class="fa-solid fa-heart-pulse" aria-hidden="true"></i><span><b>${u.stats.feel_no_pain}+</b> Feel No Pain</span></div>`,
        ].filter(Boolean).join('');
        return `<div class="match-unit-body">
            <div class="match-stats">${boxes}${saves}</div>
            <div class="match-unit-columns">
                <div class="match-weapons">
                    ${weaponTable(u.weapons.ranged, 'ranged', u.models)}
                    ${weaponTable(u.weapons.melee, 'melee', u.models)}
                    ${!u.weapons.ranged.length && !u.weapons.melee.length ? '<p class="empty-message">No weapons</p>' : ''}
                </div>
                <div class="match-abilities">${abilitiesHtml(u)}</div>
            </div>
            <div class="match-keywords"><span class="match-keywords-label">Keywords</span> ${escapeHtml((u.keywords || []).join(', ')) || '-'}
                ${u.base ? `<span class="match-base">Base ${escapeHtml(u.base)}</span>` : ''}</div>
        </div>`;
    }

    function weaponTable(weapons, type, models) {
        if (!weapons.length) return '';
        const skill = type === 'melee' ? 'WS' : 'BS';
        const rows = weapons.map(w => {
            const c = Core.matchWeaponCells(w, type);
            const abilities = w.abilities.length
                ? `<div class="match-weapon-abilities">${w.abilities.map(a =>
                    `<span class="match-weapon-keyword" ${a.hint ? `title="${escapeHtml(a.hint)}"` : ''}>${escapeHtml(a.text)}</span>`).join('')}</div>` : '';
            const qty = models > 1 || w.qty > 1 ? `<span class="match-weapon-qty" title="Models carrying it">${w.qty}×</span> ` : '';
            return `<tr><td class="match-weapon-name">${qty}${escapeHtml(w.name)}${abilities}</td>
                <td>${escapeHtml(c.range)}</td><td>${escapeHtml(c.attacks)}</td><td>${escapeHtml(c.skill)}</td>
                <td>${escapeHtml(c.strength)}</td><td>${escapeHtml(c.ap)}</td><td>${escapeHtml(c.damage)}</td></tr>`;
        }).join('');
        const title = type === 'melee'
            ? '<i class="fa-solid fa-khanda" aria-hidden="true"></i> Melee weapons'
            : '<i class="fa-solid fa-crosshairs" aria-hidden="true"></i> Ranged weapons';
        return `<div class="match-weapon-wrap"><table class="match-weapon-table">
            <thead><tr><th class="match-weapon-name">${title}</th><th>Range</th><th>A</th><th>${skill}</th><th>S</th><th>AP</th><th>D</th></tr></thead>
            <tbody>${rows}</tbody></table></div>`;
    }

    function abilitiesHtml(u) {
        const parts = [];
        if (u.core_abilities.length) {
            parts.push(`<div class="match-ability-line"><span class="match-ability-kind">Core</span> ${u.core_abilities.map(a =>
                `<span class="match-tag" ${a.hint ? `title="${escapeHtml(a.hint)}"` : ''}>${escapeHtml(a.name)}</span>`).join('')}</div>`);
        }
        if (u.faction_abilities.length) {
            parts.push(`<div class="match-ability-line"><span class="match-ability-kind">Faction</span> ${u.faction_abilities.map(name =>
                `<button type="button" class="match-tag match-tag-link" data-rule="${escapeHtml(name)}" title="Show this army rule">${escapeHtml(name)}</button>`).join('')}</div>`);
        }
        parts.push(...u.abilities.map(a => `<div class="match-ability rule-text">
            ${a.name ? `<div class="match-ability-name">${escapeHtml(a.name)}</div>` : ''}${formatRuleText(a.text)}</div>`));
        if (u.enhancement) {
            parts.push(`<div class="match-ability match-ability-enhancement rule-text">
                <div class="match-ability-name"><i class="fa-solid fa-star" aria-hidden="true"></i> Enhancement: ${escapeHtml(u.enhancement.name)}
                    <span class="muted">(${u.enhancement.points} pts)</span></div>${formatRuleText(u.enhancement.text)}</div>`);
        }
        if (u.wargear_summary) parts.push(`<div class="match-ability-line muted">Wargear: ${escapeHtml(u.wargear_summary)}</div>`);
        return parts.join('') || '<p class="empty-message">No abilities</p>';
    }

    function onRosterClick(e) {
        const item = e.target.closest('.match-roster-item');
        if (!item) return;
        const card = $(`unit-${item.dataset.uid}`);
        if (!card) return;
        card.open = true;
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function setOpen(open) {
        document.querySelectorAll('.match-unit').forEach(d => { d.open = open; });
    }

    // =========================
    // Rules: army rules, detachments' rules and the enhancements taken
    // =========================

    function ruleCard(rule, extra = '') {
        const body = rule.text
            ? formatRuleText(rule.text)
            : `<p class="muted">Written in this app as a modifier: ${escapeHtml(rule.modifier || '-')}</p>`;
        return `<article class="match-rule rule-text" data-rule="${escapeHtml(rule.name)}">
            <h4>${escapeHtml(rule.name)}${extra}</h4>${body}</article>`;
    }

    function renderRules() {
        const v = state.view;
        const sections = [];
        sections.push(`<section class="match-rule-section"><h3><i class="fa-solid fa-flag" aria-hidden="true"></i> Army rules${v.faction ? ` · ${escapeHtml(v.faction)}` : ''}</h3>
            ${v.army_rules.length ? v.army_rules.map(r => ruleCard(r)).join('') : '<p class="empty-message">No army rules in the data</p>'}</section>`);
        if (!v.detachments.length) {
            sections.push('<section class="match-rule-section"><h3><i class="fa-solid fa-sitemap" aria-hidden="true"></i> Detachment</h3><p class="empty-message">No detachment chosen</p></section>');
        }
        for (const d of v.detachments) {
            const meta = [d.dp != null ? `${d.dp} DP` : null, ...(d.dispositions || [])].filter(Boolean).join(' · ');
            sections.push(`<section class="match-rule-section"><h3><i class="fa-solid fa-sitemap" aria-hidden="true"></i> ${escapeHtml(d.name)}
                ${meta ? `<span class="match-rule-meta">${escapeHtml(meta)}</span>` : ''}</h3>
                ${d.rules.length ? d.rules.map(r => ruleCard(r)).join('') : '<p class="empty-message">No rules in the data</p>'}</section>`);
        }
        if (v.enhancements.length) {
            sections.push(`<section class="match-rule-section"><h3><i class="fa-solid fa-star" aria-hidden="true"></i> Enhancements</h3>
                ${v.enhancements.map(e => ruleCard(e, ` <span class="match-rule-meta">${escapeHtml(state.labels.get(e.bearer) || '')} · ${e.points} pts</span>`)).join('')}</section>`);
        }
        $('match-rules').innerHTML = sections.join('');
    }

    /** A faction ability tag on a datasheet: show that army rule. */
    function onFactionTag(e) {
        const tag = e.target.closest('.match-tag-link');
        if (!tag) return;
        showTab('rules');
        const rule = [...document.querySelectorAll('.match-rule')].find(r => r.dataset.rule === tag.dataset.rule);
        if (rule) {
            rule.scrollIntoView({ behavior: 'smooth', block: 'center' });
            rule.classList.add('flash');
            setTimeout(() => rule.classList.remove('flash'), 1500);
        }
    }

    // =========================
    // Stratagems
    // =========================

    function renderStratagems() {
        const v = state.view;
        const used = new Set(v.stratagems.flatMap(s => s.phases));
        const phases = [['all', 'All phases'], ...PHASES.filter(p => used.has(p)).map(p => [p, p.replace(' phase', '')]),
                        ...(v.stratagems.some(s => !s.phases.length) ? [['other', 'Any / other']] : [])];
        if (!phases.some(([key]) => key === state.phase)) state.phase = 'all';
        $('match-phase-filter').innerHTML = phases.map(([key, label]) => chip(key, label, state.phase === key)).join('');
        $('match-turn-filter').innerHTML = TURNS.map(([key, label]) => chip(key, label, state.turn === key)).join('');
        $('match-wahapedia-note').hidden = v.wahapedia;
        drawStratagems();
    }

    function chip(key, label, active) {
        return `<button type="button" class="match-filter-chip ${active ? 'active' : ''}" data-key="${escapeHtml(key)}">${escapeHtml(label)}</button>`;
    }

    function onChip(e, kind) {
        const btn = e.target.closest('.match-filter-chip');
        if (!btn) return;
        state[kind] = btn.dataset.key;
        btn.parentElement.querySelectorAll('.match-filter-chip').forEach(b => b.classList.toggle('active', b === btn));
        drawStratagems();
    }

    function drawStratagems() {
        const shown = state.view.stratagems.filter(s => Core.matchStratagemShown(s, state));
        $('match-stratagems').innerHTML = shown.length ? shown.map(stratagemCard).join('')
            : `<p class="empty-message">${state.view.stratagems.length ? 'No stratagems match' : 'No stratagems in the data'}</p>`;
    }

    function stratagemCard(s) {
        const turn = String(s.turn || '').toLowerCase();
        const side = turn.startsWith('your') ? 'yours' : turn.startsWith('opponent') ? 'theirs' : 'either';
        const source = s.core ? 'Core' : (s.detachment || 'Faction');
        const when = [s.turn, s.timing].filter(Boolean).join(' · ');
        const body = s.text
            ? formatRuleText(s.text)
            : `<p class="muted">Written in this app as a modifier: ${escapeHtml(s.modifier || '-')}</p>`;
        return `<article class="match-stratagem match-stratagem-${side} ${s.core ? 'core' : ''}">
            <header class="match-stratagem-head">
                <span class="match-cp" title="Command points">${escapeHtml(s.cp ?? '?')}<small>CP</small></span>
                <div>
                    <h4>${escapeHtml(s.name)}</h4>
                    <div class="match-stratagem-meta">${escapeHtml([source, s.category].filter(Boolean).join(' · '))}</div>
                </div>
            </header>
            ${when ? `<div class="match-stratagem-when"><i class="fa-solid fa-clock" aria-hidden="true"></i> ${escapeHtml(when)}</div>` : ''}
            <div class="rule-text match-stratagem-text">${body}</div>
        </article>`;
    }

    // =========================
    // Search: hides what doesn't match in every pane
    // =========================

    function applySearch() {
        const words = state.search.trim().toLowerCase();
        const matches = el => !words || el.textContent.toLowerCase().includes(words);
        document.querySelectorAll('.match-unit').forEach(card => { card.hidden = !matches(card); });
        document.querySelectorAll('.match-group').forEach(group => {
            group.hidden = ![...group.querySelectorAll('.match-unit')].some(c => !c.hidden);
        });
        document.querySelectorAll('.match-roster-item').forEach(item => {
            item.hidden = !!$(`unit-${item.dataset.uid}`)?.hidden;
        });
        document.querySelectorAll('.match-rule').forEach(rule => { rule.hidden = !matches(rule); });
        if (state.view) drawStratagems();
    }
})();
