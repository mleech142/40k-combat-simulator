/**
 * Play page (experimental): a game between two saved armies on one PC, drawn top-down in inches.
 *
 * The game lives on the server (/api/play/games): every action is sent there, checked and logged,
 * and the answer's state is what is drawn. This page only lays units out and turns pointer moves
 * into actions; the rules (deployment zones, overlaps, sizes) are all checked by the server.
 */

(function() {
    'use strict';

    const { escapeHtml } = Core;
    const SVG = 'http://www.w3.org/2000/svg';
    const PLAYER_CLASSES = ['play-p1', 'play-p2'];
    const PHASE_NAMES = { deployment: 'Deployment', command: 'Command', movement: 'Movement', shooting: 'Shooting',
                          charge: 'Charge', fight: 'Fight' };
    const MOVE_MODES = [['normal', 'Normal move'], ['advance', 'Advance'], ['fall_back', 'Fall Back']];
    const DONE = { normal: 'moved', advance: 'advanced', fall_back: 'fell back', stationary: 'remained stationary' };

    const state = {
        game: null,  // /api/play/games/<id> answer
        selected: null,  // Unit id picked in the list
        view: null,  // The board's viewBox, in SVG units (inches, y down)
        drag: null,  // {unitId, modelIds, start, dx, dy} while dragging
        moveMode: 'normal',  // How the selected unit will move (Movement phase)
        shooting: null,  // /api/play/games/<id>/shooting/<unit> for the selected unit (Shooting phase)
        targets: {},  // The target chosen for each weapon, 'part:weapon' -> unit id
        fire: {},  // Whether each weapon is ticked to fire, 'part:weapon' -> bool
    };
    const $ = id => document.getElementById(id);

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        $('new-game-btn').addEventListener('click', newGame);
        $('undo-btn').addEventListener('click', undo);
        $('fit-btn').addEventListener('click', () => { fitView(); drawBoard(); });
        $('phase-btn').addEventListener('click', nextPhase);
        const board = $('play-board');
        board.addEventListener('wheel', zoom, { passive: false });
        board.addEventListener('pointerdown', pointerDown);
        board.addEventListener('pointermove', pointerMove);
        board.addEventListener('pointerup', pointerUp);
        await Promise.all([loadArmies(), loadGames()]);
        const id = new URLSearchParams(location.search).get('game');
        if (id) await openGame(id);
    }

    // --- Games -------------------------------------------------------------------------

    async function loadArmies() {
        const armies = await fetch('/api/armies').then(r => r.json()).catch(() => []);
        const options = armies.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('');
        $('new-red').innerHTML = options;
        $('new-blue').innerHTML = options;
        if (armies.length > 1) $('new-blue').selectedIndex = 1;
        $('new-game-hint').textContent = armies.length ? '' : 'Save an army on the Armies page first.';
        $('new-game-btn').disabled = !armies.length;
    }

    async function loadGames() {
        const games = await fetch('/api/play/games').then(r => r.json()).catch(() => []);
        $('game-list').innerHTML = games.length ? games.map(g => `
            <div class="play-game-row ${state.game && state.game.id === g.id ? 'active' : ''}">
                <button class="play-game-open" data-id="${g.id}">${escapeHtml(g.name)}
                    <small>${escapeHtml(g.players.join(' vs '))}</small></button>
                <button class="btn-icon" data-delete="${g.id}" title="Delete game" aria-label="Delete ${escapeHtml(g.name)}">
                    <i class="fa-solid fa-trash" aria-hidden="true"></i></button>
            </div>`).join('') : '<p class="empty-message">No games yet</p>';
        $('game-list').querySelectorAll('[data-id]').forEach(b => b.addEventListener('click', () => openGame(b.dataset.id)));
        $('game-list').querySelectorAll('[data-delete]').forEach(b => b.addEventListener('click', () => deleteGame(b.dataset.delete)));
    }

    async function newGame() {
        const body = { armies: [$('new-red').value, $('new-blue').value].map(Number), name: $('new-name').value };
        const resp = await fetch('/api/play/games', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) {
            $('new-game-hint').textContent = data.error || 'The game could not be started.';
            return;
        }
        $('new-name').value = '';
        show(data);
        await loadGames();
    }

    async function openGame(id) {
        const resp = await fetch(`/api/play/games/${id}`);
        if (!resp.ok) return;
        show(await resp.json());
        await loadGames();
    }

    async function deleteGame(id) {
        if (!confirm('Delete this game? This cannot be undone.')) return;
        await fetch(`/api/play/games/${id}`, { method: 'DELETE' });
        if (state.game && String(state.game.id) === String(id)) {
            state.game = null;
            history.replaceState(null, '', '/play');
            render();
        }
        await loadGames();
    }

    function show(game) {
        const first = !state.game || state.game.id !== game.id;
        state.game = game;
        if (first) {
            state.selected = null;
            fitView();
            history.replaceState(null, '', `/play?game=${game.id}`);
        }
        render();
    }

    // --- Actions -----------------------------------------------------------------------

    async function send(action) {
        const resp = await fetch(`/api/play/games/${state.game.id}/actions`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(action),
        });
        const data = await resp.json();
        if (!resp.ok) {
            message(data.error || 'That move was not allowed.', true);
            render();  // Put any dragged models back
            return false;
        }
        message('');
        show(data);
        return true;
    }

    async function undo() {
        const resp = await fetch(`/api/play/games/${state.game.id}/undo`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) return message(data.error, true);
        message('Undone.');
        show(data);
    }

    function place(unitId, positions) {
        return send({ type: 'place', unit: unitId, models: positions });
    }

    async function nextPhase() {
        const turn = state.game.state.turn;
        if (await send({ type: turn.phase === 'deployment' ? 'end_deployment' : 'next_phase' })) {
            state.moveMode = 'normal';
            state.shooting = null;
            state.targets = {};
            state.fire = {};
            loadShooting();
            const now = state.game.state.turn;
            message(`${state.game.state.players[now.player].name}: ${PHASE_NAMES[now.phase]} phase.`);
        }
    }

    /** The Movement phase buttons: choose a move, roll to Advance, or remain stationary. */
    async function movementChoice(choice) {
        const unit = unitById(state.selected);
        if (choice === 'stationary') {
            await send({ type: 'stationary', unit: unit.id });
            return;
        }
        if (choice === 'advance' && unit.activity.advance_roll == null) {
            if (!await send({ type: 'advance', unit: unit.id })) return;
            message(`Advance roll: ${unitById(unit.id).activity.advance_roll}. Drag the unit to move it.`);
        }
        state.moveMode = choice;
        renderUnits();
        drawBoard();
    }

    /** How far a model may move now (its Move, plus the Advance roll when advancing), or null if it can't. */
    function allowance(unit, model) {
        const turn = state.game.state.turn;
        if (turn.phase !== 'movement' || unit.player !== turn.player || unit.activity.moved) return null;
        const roll = state.moveMode === 'advance' ? (unit.activity.advance_roll || 0) : 0;
        return state.game.moves[unit.id][model.part] + roll;
    }

    /** Place the selected unit in formation, centred on a board point. */
    async function placeSelectedAt(point) {
        const unit = unitById(state.selected);
        const living = unit.models.filter(m => m.alive);
        const outlines = state.game.outlines[unit.id];
        const sizes = living.map(m => Math.max(outlines[m.part].length, outlines[m.part].width));
        const board = state.game.board;
        const zone = unit.player === 0 ? { left: 0, right: board.width, bottom: 0, top: board.deployment_depth }
            : { left: 0, right: board.width, bottom: board.depth - board.deployment_depth, top: board.depth };
        const spots = Core.playFit(Core.playFormation(sizes, point.x, point.y), sizes.map(s => s / 2), zone);
        const angle = unit.player === 0 ? 0 : 180;
        if (await place(unit.id, living.map((m, i) => ({ id: m.id, x: spots[i].x, y: spots[i].y, angle })))) {
            state.selected = null;
            render();
        }
    }

    // --- Drawing -----------------------------------------------------------------------

    function render() {
        const game = state.game;
        $('game-title').textContent = game ? game.name : 'Choose or start a game';
        $('undo-btn').disabled = !game || !game.events.length || game.events[game.events.length - 1].rolls.length > 0;
        const turn = game && game.state.turn;
        const allPlaced = game && game.state.units.every(u => u.models.every(m => !m.alive || m.position));
        $('turn-banner').textContent = !turn ? '' : turn.phase === 'deployment'
            ? (allPlaced ? 'Deployment · every unit placed: press Deployment done' : `Deployment · ${game.state.players[turn.player].name} to place`)
            : `Round ${turn.round} · ${game.state.players[turn.player].name} · ${PHASE_NAMES[turn.phase]} phase`;
        $('turn-banner').className = `play-turn ${turn ? PLAYER_CLASSES[turn.player] : ''}`;
        $('phase-btn').hidden = !game;
        if (turn) {
            $('phase-btn').querySelector('span').textContent = turn.phase === 'deployment' ? 'Deployment done'
                : turn.phase === 'fight' ? 'End turn' : 'Next phase';
        }
        renderUnits();
        drawBoard();
        renderLog();
    }

    function unitById(id) {
        return state.game.state.units.find(u => u.id === id);
    }

    function renderUnits() {
        const game = state.game;
        if (!game) {
            $('unit-list').innerHTML = '<p class="empty-message">No game open</p>';
            $('unit-detail').innerHTML = '';
            return;
        }
        $('unit-list').innerHTML = game.state.players.map((player, p) => {
            const units = game.state.units.filter(u => u.player === p);
            const orders = (player.orders || {}).casualties || 'farthest';
            return `<div class="play-player ${PLAYER_CLASSES[p]}"><h4>${escapeHtml(player.name)}</h4>
                <label class="play-orders">Casualties
                    <select data-orders="${p}" class="filter-select">
                        <option value="farthest" ${orders === 'farthest' ? 'selected' : ''}>farthest from the attacker first</option>
                        <option value="closest" ${orders === 'closest' ? 'selected' : ''}>closest to the attacker first</option>
                    </select></label>${units.map(u => {
                const living = u.models.filter(m => m.alive);
                const placed = living.length && living.every(m => m.position);
                const done = game.state.turn.phase === 'deployment' ? (placed ? 'placed' : 'not placed')
                    : [DONE[u.activity.moved], u.activity.advance_roll != null ? `Advance roll ${u.activity.advance_roll}` : '',
                       u.activity.shot ? 'shot' : '']
                        .filter(Boolean).join(' · ');
                return `<button class="play-unit ${state.selected === u.id ? 'selected' : ''}" data-unit="${u.id}">
                    <span>${escapeHtml(u.name)}</span>
                    <small>${living.length} model${living.length === 1 ? '' : 's'}${done ? ' · ' + escapeHtml(done) : ''}</small>
                </button>`;
            }).join('')}</div>`;
        }).join('');
        $('unit-list').querySelectorAll('[data-unit]').forEach(b => b.addEventListener('click', () => select(b.dataset.unit)));
        $('unit-list').querySelectorAll('[data-orders]').forEach(s => s.addEventListener('change', () =>
            send({ type: 'orders', player: Number(s.dataset.orders), casualties: s.value })));
        renderDetail();
    }

    function select(unitId) {
        const changed = state.selected !== unitId;
        state.selected = changed ? unitId : null;
        if (changed) state.moveMode = unitById(unitId).activity.advance_roll != null ? 'advance' : 'normal';
        const turn = state.game.state.turn;
        if (!state.selected) message('');
        else if (turn.phase === 'deployment') message(`Click the board to place ${unitById(unitId).name} in its deployment zone.`);
        else message('');
        if (changed) {
            state.shooting = null;
            state.targets = {};
            state.fire = {};
            loadShooting();
        }
        renderUnits();
        drawBoard();
    }

    function renderDetail() {
        const unit = state.selected && unitById(state.selected);
        if (!unit) {
            $('unit-detail').innerHTML = '';
            return;
        }
        const turn = state.game.state.turn;
        let controls = '';
        if (turn.phase === 'movement' && unit.player === turn.player && !unit.activity.moved) {
            const rolled = unit.activity.advance_roll != null;
            controls = `<div class="play-move-modes">${MOVE_MODES.map(([mode, label]) => `
                <button class="btn-small ${state.moveMode === mode ? 'active' : ''}" data-mode="${mode}"
                    ${rolled && mode !== 'advance' ? 'disabled' : ''}>${label}${mode === 'advance' && rolled ? ` (+${unit.activity.advance_roll}")` : ''}</button>`).join('')}
                <button class="btn-small" data-mode="stationary" ${rolled ? 'disabled' : ''}>Remain stationary</button>
            </div><p class="play-hint">Drag the unit to make the ${MOVE_MODES.find(m => m[0] === state.moveMode)[1]}.</p>`;
        }
        if (turn.phase === 'shooting' && unit.player === turn.player && state.shooting && state.shooting.unit === unit.id) {
            controls = shootingControls(unit);
        }
        $('unit-detail').innerHTML = controls + unit.parts.map((part, i) => {
            const s = part.stats;
            const outline = state.game.outlines[unit.id][i];
            const alive = unit.models.filter(m => m.alive && m.part === i).length;
            return `<div class="play-part">
                <strong>${escapeHtml(part.name)}</strong>${part.role === 'leader' ? ' <small>(leader)</small>' : ''}
                <div class="play-stats">M ${escapeHtml(s.move || '-')}" · T ${s.toughness} · Sv ${s.save}+${s.invulnerable_save ? ` · ${s.invulnerable_save}++` : ''} · W ${s.wounds}</div>
                <small>${alive} of ${part.models} models · base ${escapeHtml((part.base && part.base.text) || 'unknown')}${outline.estimated ? ' (size estimated)' : ''}</small>
            </div>`;
        }).join('');
        $('unit-detail').querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => movementChoice(b.dataset.mode)));
        $('unit-detail').querySelectorAll('[data-fire]').forEach(c => c.addEventListener('change', () => {
            state.fire[c.dataset.fire] = c.checked;
            drawBoard();
        }));
        $('unit-detail').querySelectorAll('[data-target]').forEach(s => s.addEventListener('change', () => {
            state.targets[s.dataset.target] = s.value;
            state.fire[s.dataset.target] = true;
            renderDetail();
            drawBoard();
        }));
        const fire = $('fire-btn');
        if (fire) fire.addEventListener('click', fireSelected);
    }

    // --- Shooting -----------------------------------------------------------------------

    async function loadShooting() {
        const unit = state.selected && unitById(state.selected);
        const turn = state.game.state.turn;
        if (!unit || turn.phase !== 'shooting' || unit.player !== turn.player) return;
        const resp = await fetch(`/api/play/games/${state.game.id}/shooting/${unit.id}`);
        if (!resp.ok || state.selected !== unit.id) return;
        state.shooting = { unit: unit.id, ...(await resp.json()) };
        renderDetail();
    }

    /** Point every weapon that can fire at an enemy unit (clicked on the board). */
    function targetAll(enemyId) {
        if (!state.shooting) return;
        for (const w of state.shooting.weapons) {
            const key = `${w.part}:${w.weapon}`;
            state.targets[key] = enemyId;
            state.fire[key] = w.targets[enemyId] ? w.targets[enemyId].ok && (w.default_use || state.fire[key]) : false;
        }
        message(`Target: ${unitById(enemyId).name}.`);
        renderDetail();
        drawBoard();
    }

    function shootingControls(unit) {
        const options = state.shooting;
        if (options.reason) return `<p class="play-hint">${escapeHtml(options.reason)}</p>`;
        if (!options.weapons.length) return '<p class="play-hint">No ranged weapons.</p>';
        const enemies = state.game.state.units.filter(u => u.player !== unit.player && u.models.some(m => m.alive && m.position));
        const rows = options.weapons.map(w => {
            const key = `${w.part}:${w.weapon}`;
            const target = state.targets[key];
            const choice = target && w.targets[target];
            const checked = !!(choice && choice.ok && state.fire[key]);
            const select = `<select class="filter-select" data-target="${key}">
                <option value="">Choose a target</option>
                ${enemies.map(e => {
                    const t = w.targets[e.id];
                    return `<option value="${e.id}" ${target === e.id ? 'selected' : ''} ${t && t.ok ? '' : 'disabled'}
                        title="${escapeHtml(t && t.reason ? t.reason : '')}">${escapeHtml(e.name)}${t && t.ok ? ` (${t.models} firing)` : ' (no)'}</option>`;
                }).join('')}</select>`;
            const why = choice && !choice.ok ? `<small class="play-why">${escapeHtml(choice.reason)}</small>` : '';
            return `<div class="play-weapon ${choice && !choice.ok ? 'blocked' : ''}">
                <label><input type="checkbox" data-fire="${key}" ${checked ? 'checked' : ''} ${choice && choice.ok ? '' : 'disabled'}>
                    ${escapeHtml(w.name)} <small>${w.range ? w.range + '"' : ''}</small></label>${select}${why}</div>`;
        }).join('');
        return `<div class="play-shooting">${rows}
            <button class="btn btn-primary" id="fire-btn"><i class="fa-solid fa-crosshairs" aria-hidden="true"></i> Fire</button></div>`;
    }

    async function fireSelected() {
        const weapons = state.shooting.weapons
            .map(w => ({ key: `${w.part}:${w.weapon}`, w }))
            .filter(({ key, w }) => state.fire[key] && state.targets[key] && w.targets[state.targets[key]]?.ok)
            .map(({ key, w }) => ({ part: w.part, weapon: w.weapon, target: state.targets[key] }));
        if (!weapons.length) return message('Tick at least one weapon with a target.', true);
        if (await send({ type: 'shoot', unit: state.selected, weapons })) {
            const event = state.game.events[state.game.events.length - 1];
            const killed = event.attacks.reduce((n, a) => n + a.killed.length, 0);
            message(`${unitById(state.selected).name} fired: ${killed} model${killed === 1 ? '' : 's'} killed.`);
            state.shooting = null;
            loadShooting();
        }
    }

    // --- Dice log -------------------------------------------------------------------------

    function renderLog() {
        const game = state.game;
        const shots = game ? game.events.map((e, i) => [e, i]).filter(([e]) => e.attacks && e.attacks.length).reverse() : [];
        if (!shots.length) {
            $('dice-log').innerHTML = '<p class="empty-message">No dice rolled yet</p>';
            return;
        }
        const name = id => (game.state.units.find(u => u.id === id) || { name: id }).name;
        $('dice-log').innerHTML = shots.map(([event, i], n) => {
            const targets = [...new Set(event.attacks.filter(a => a.name !== 'Hazardous').map(a => name(a.target)))];
            return `<details class="play-log-entry" ${n === 0 ? 'open' : ''}>
                <summary>${escapeHtml(name(event.action.unit))} shoots ${escapeHtml(targets.join(', '))}</summary>
                ${event.attacks.map(a => `<div class="play-log-attack">
                    <div>${escapeHtml(Core.playAttackSummary(a))}${a.name !== 'Hazardous' && targets.length > 1 ? ` at ${escapeHtml(name(a.target))}` : ''}</div>
                    ${Core.playDiceGroups(event.rolls, a.rolls).map(g => `<div class="play-dice-row">
                        <span class="play-dice-label">${escapeHtml(g.label)}${g.need ? ' ' + g.need : ''}</span>
                        ${g.dice.map(d => `<span class="play-die ${d.kind}" title="${d.rerolled_from ? 'Re-rolled from ' + d.rerolled_from : ''}">${d.face}</span>`).join('')}
                        ${g.note ? `<small>${escapeHtml(g.note)}</small>` : ''}
                    </div>`).join('')}
                </div>`).join('')}
            </details>`;
        }).join('');
    }

    function fitView() {
        const board = state.game ? state.game.board : { width: 60, depth: 44 };
        state.view = { x: -1, y: -1, w: board.width + 2, h: board.depth + 2 };
    }

    function svgEl(name, attrs, parent) {
        const el = document.createElementNS(SVG, name);
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        if (parent) parent.appendChild(el);
        return el;
    }

    function drawBoard() {
        const svg = $('play-board');
        if (!state.view) fitView();
        svg.setAttribute('viewBox', `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`);
        svg.innerHTML = '';
        const game = state.game;
        const board = game ? game.board : { width: 60, depth: 44, deployment_depth: 12 };
        const W = board.width, D = board.depth;
        svgEl('rect', { x: 0, y: 0, width: W, height: D, class: 'play-field' }, svg);
        // Deployment zones: player 1 at the bottom edge, player 2 at the top (SVG y runs down)
        svgEl('rect', { x: 0, y: D - board.deployment_depth, width: W, height: board.deployment_depth, class: 'play-zone play-p1' }, svg);
        svgEl('rect', { x: 0, y: 0, width: W, height: board.deployment_depth, class: 'play-zone play-p2' }, svg);
        const grid = svgEl('g', { class: 'play-grid' }, svg);
        for (let x = 1; x < W; x++) svgEl('line', { x1: x, y1: 0, x2: x, y2: D, class: x % 6 ? '' : 'major' }, grid);
        for (let y = 1; y < D; y++) svgEl('line', { x1: 0, y1: y, x2: W, y2: y, class: y % 6 ? '' : 'major' }, grid);
        if (!game) return;
        drawShotLines(svg, D);

        for (const unit of game.state.units) {
            const placed = unit.models.filter(m => m.alive && m.position);
            if (!placed.length) continue;
            const targeted = game.state.turn.phase === 'shooting'
                && Object.entries(state.targets).some(([key, id]) => id === unit.id && state.fire[key]);
            const g = svgEl('g', { class: `play-unit-group ${PLAYER_CLASSES[unit.player]} ${state.selected === unit.id ? 'selected' : ''} ${targeted ? 'targeted' : ''}`,
                                   'data-unit': unit.id }, svg);
            // The unit's outline: around every model's whole base, 0.4" out
            const reach = m => Math.max(game.outlines[unit.id][m.part].length, game.outlines[unit.id][m.part].width) / 2 + 0.4;
            const left = Math.min(...placed.map(m => m.position.x - reach(m)));
            const right = Math.max(...placed.map(m => m.position.x + reach(m)));
            const top = Math.min(...placed.map(m => D - m.position.y - reach(m)));
            const bottom = Math.max(...placed.map(m => D - m.position.y + reach(m)));
            svgEl('rect', { x: left, y: top, width: right - left, height: bottom - top, rx: 0.6, class: 'play-unit-outline' }, g);
            if (state.selected === unit.id) {
                for (const model of placed) {
                    const reach = allowance(unit, model);
                    if (reach == null) continue;
                    const o = game.outlines[unit.id][model.part];
                    svgEl('circle', { cx: model.position.x, cy: D - model.position.y, r: reach + Math.max(o.length, o.width) / 2,
                                      class: 'play-move-ring' }, g);
                }
            }
            for (const model of placed) drawModel(g, unit, model, D);
            const label = svgEl('text', { x: (left + right) / 2, y: top - 0.3, class: 'play-label' }, g);
            label.textContent = unit.name;
        }
        // Wounds badges on top of every model, so a neighbouring base never hides one
        const badges = svgEl('g', { class: 'play-badges' }, svg);
        svg.querySelectorAll('.play-wounds').forEach(b => badges.appendChild(b));
    }

    /**
     * Dashed arrows from shooter to target: red for the targets being chosen, faint for the shots
     * already fired this phase. Each runs between the two units' nearest models, edge to edge.
     */
    function drawShotLines(svg, depth) {
        const game = state.game;
        const turn = game.state.turn;
        const lines = Core.playShotLines(game.events).map(l => ({ ...l, kind: 'fired' }));
        const shooter = state.selected && unitById(state.selected);
        if (turn.phase === 'shooting' && shooter && shooter.player === turn.player && !shooter.activity.shot) {
            const planned = new Set(Object.entries(state.targets).filter(([key, id]) => id && state.fire[key]).map(([, id]) => id));
            planned.forEach(to => lines.push({ from: shooter.id, to, kind: 'planned' }));
        }
        if (!lines.length) return;
        const defs = svgEl('defs', {}, svg);
        for (const kind of ['planned', 'fired']) {
            const marker = svgEl('marker', { id: `arrow-${kind}`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 5,
                                             markerHeight: 5, orient: 'auto-start-reverse' }, defs);
            svgEl('path', { d: 'M 0 0 L 10 5 L 0 10 z', class: `play-arrow ${kind}` }, marker);
        }
        const layer = svgEl('g', { class: 'play-shots' }, svg);
        for (const line of lines) {
            const from = unitById(line.from), to = unitById(line.to);
            const a = nearestEdge(from, to), b = nearestEdge(to, from);
            if (!a || !b) continue;
            svgEl('line', { x1: a.x, y1: depth - a.y, x2: b.x, y2: depth - b.y, class: `play-shot ${line.kind}`,
                            'marker-end': `url(#arrow-${line.kind})` }, layer);
        }
    }

    /** The point on `unit`'s model nearest the other unit's centre, on that model's edge (board inches). */
    function nearestEdge(unit, other) {
        const placed = m => m.alive && m.position;
        const centre = Core.playCentre(other.models.filter(placed).map(m => m.position));
        const models = unit.models.filter(placed);
        if (!centre || !models.length) return null;
        const model = models.reduce((best, m) => Math.hypot(m.position.x - centre.x, m.position.y - centre.y)
            < Math.hypot(best.position.x - centre.x, best.position.y - centre.y) ? m : best);
        const outline = state.game.outlines[unit.id][model.part];
        const reach = Math.min(outline.length, outline.width) / 2;
        const dx = centre.x - model.position.x, dy = centre.y - model.position.y;
        const length = Math.hypot(dx, dy) || 1;
        return { x: model.position.x + dx / length * reach, y: model.position.y + dy / length * reach };
    }

    function drawModel(g, unit, model, depth) {
        const outline = state.game.outlines[unit.id][model.part];
        const x = model.position.x, y = depth - model.position.y;
        const angle = -(model.position.angle || 0);  // Anticlockwise on the board is clockwise in SVG
        const attrs = { class: `play-model${outline.estimated ? ' estimated' : ''}${unit.parts[model.part].role === 'leader' ? ' leader' : ''}`,
                        'data-unit': unit.id, 'data-model': model.id, transform: `translate(${x} ${y}) rotate(${angle})` };
        if (outline.shape === 'rect') {
            svgEl('rect', { ...attrs, x: -outline.length / 2, y: -outline.width / 2, width: outline.length, height: outline.width }, g);
        } else {
            svgEl('ellipse', { ...attrs, rx: outline.length / 2, ry: outline.width / 2 }, g);
        }
        const full = unit.parts[model.part].stats.wounds;
        if (model.wounds_left < full) {
            // Wounds left, on the base's top-right edge (only once the model is damaged)
            const badge = svgEl('g', { class: 'play-wounds', 'data-badge': model.id,
                                       transform: `translate(${x + outline.length * 0.36} ${y - outline.width * 0.36})` }, g);
            svgEl('circle', { r: 0.34 }, badge);
            const text = svgEl('text', { y: 0.01 }, badge);
            text.textContent = model.wounds_left;
            const title = svgEl('title', {}, badge);
            title.textContent = `${model.wounds_left} of ${full} wounds left`;
        }
    }

    // --- Pointer: place, drag, zoom ----------------------------------------------------------

    /** A pointer event's position on the board, in inches with y up. */
    function boardPoint(event) {
        const svg = $('play-board');
        const pt = svg.createSVGPoint();
        pt.x = event.clientX;
        pt.y = event.clientY;
        const p = pt.matrixTransform(svg.getScreenCTM().inverse());
        return { x: p.x, y: state.game.board.depth - p.y, svgX: p.x, svgY: p.y };
    }

    function pointerDown(event) {
        if (!state.game || event.button !== 0) return;
        const target = event.target.closest('[data-model]');
        if (!target) {
            if (state.selected && state.game.state.turn.phase === 'deployment') placeSelectedAt(boardPoint(event));
            return;
        }
        const unit = unitById(target.dataset.unit);
        const modelIds = event.shiftKey ? [target.dataset.model] : unit.models.filter(m => m.alive && m.position).map(m => m.id);
        state.drag = { unitId: unit.id, modelIds, start: boardPoint(event), dx: 0, dy: 0 };
        $('play-board').setPointerCapture(event.pointerId);
    }

    function pointerMove(event) {
        if (!state.drag) return;
        const p = boardPoint(event);
        state.drag.dx = p.x - state.drag.start.x;
        state.drag.dy = p.y - state.drag.start.y;
        const unit = unitById(state.drag.unitId);
        const limits = state.drag.modelIds.map(id => allowance(unit, unit.models.find(m => m.id === id))).filter(v => v != null);
        showReadout(event, limits.length ? Core.playMoveLabel(state.drag.dx, state.drag.dy, Math.min(...limits)) : null);
        if (state.drag.modelIds.length === unit.models.filter(m => m.alive && m.position).length) {
            // The whole unit: its outline and name go with it (SVG y runs down)
            const group = $('play-board').querySelector(`.play-unit-group[data-unit="${unit.id}"]`);
            group.querySelectorAll('.play-unit-outline, .play-label').forEach(el =>
                el.setAttribute('transform', `translate(${state.drag.dx} ${-state.drag.dy})`));
        }
        for (const id of state.drag.modelIds) {
            const el = $('play-board').querySelector(`[data-model="${id}"]`);
            const model = unitById(state.drag.unitId).models.find(m => m.id === id);
            const x = model.position.x + state.drag.dx, y = state.game.board.depth - (model.position.y + state.drag.dy);
            el.setAttribute('transform', `translate(${x} ${y}) rotate(${-(model.position.angle || 0)})`);
            const badge = $('play-board').querySelector(`[data-badge="${id}"]`);
            if (badge) {
                const o = state.game.outlines[unit.id][model.part];
                badge.setAttribute('transform', `translate(${x + o.length * 0.36} ${y - o.width * 0.36})`);
            }
        }
    }

    async function pointerUp() {
        const drag = state.drag;
        state.drag = null;
        if (!drag) return;
        const unit = unitById(drag.unitId);
        if (Math.hypot(drag.dx, drag.dy) < 0.05) {  // A click on a unit: target it (shooting) or show it
            showReadout(null, null);
            const turn = state.game.state.turn;
            const mine = state.selected && unitById(state.selected);
            if (turn.phase === 'shooting' && mine && mine.player === turn.player && unit.player !== turn.player) {
                targetAll(unit.id);
            } else if (state.selected !== unit.id) {
                select(unit.id);
            }
            return;
        }
        showReadout(null, null);
        const round = v => Math.round(v * 100) / 100;
        const moved = new Set(drag.modelIds);
        if (state.game.state.turn.phase !== 'deployment') {
            if (state.game.state.turn.phase !== 'movement') {
                message('Models only move in the Movement phase (for now).', true);
                render();
                return;
            }
            const mode = state.selected === unit.id ? state.moveMode : 'normal';
            await send({ type: 'move', unit: unit.id, mode, models: unit.models.filter(m => m.alive).map(m => ({
                id: m.id, x: round(m.position.x + (moved.has(m.id) ? drag.dx : 0)),
                y: round(m.position.y + (moved.has(m.id) ? drag.dy : 0)), angle: m.position.angle || 0,
            })) });
            return;
        }
        await place(unit.id, unit.models.filter(m => m.alive).map(m => ({
            id: m.id, angle: m.position ? m.position.angle : 0,
            x: m.position ? round(m.position.x + (moved.has(m.id) ? drag.dx : 0)) : null,
            y: m.position ? round(m.position.y + (moved.has(m.id) ? drag.dy : 0)) : null,
        })));
    }

    function zoom(event) {
        event.preventDefault();
        if (!state.view) fitView();
        const svg = $('play-board');
        const pt = svg.createSVGPoint();
        pt.x = event.clientX;
        pt.y = event.clientY;
        const p = pt.matrixTransform(svg.getScreenCTM().inverse());
        state.view = Core.playZoom(state.view, event.deltaY > 0 ? 1.1 : 1 / 1.1, p.x, p.y);
        svg.setAttribute('viewBox', `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`);
    }

    /** The distance readout beside the pointer while dragging ({text, over}), or hidden. */
    function showReadout(event, label) {
        let el = $('play-readout');
        if (!el) {
            el = document.createElement('div');
            el.id = 'play-readout';
            el.className = 'play-readout';
            document.body.appendChild(el);
        }
        el.hidden = !label;
        if (!label) return;
        el.textContent = label.text;
        el.classList.toggle('over', label.over);
        el.style.left = `${event.clientX + 14}px`;
        el.style.top = `${event.clientY + 14}px`;
    }

    function message(text, isError = false) {
        $('play-message').textContent = text || '';
        $('play-message').classList.toggle('error', isError);
    }
})();
