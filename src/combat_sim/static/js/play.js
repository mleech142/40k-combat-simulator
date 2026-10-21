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

    const state = {
        game: null,  // /api/play/games/<id> answer
        selected: null,  // Unit id picked in the list
        view: null,  // The board's viewBox, in SVG units (inches, y down)
        drag: null,  // {unitId, modelIds, start, moved} while dragging
    };
    const $ = id => document.getElementById(id);

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        $('new-game-btn').addEventListener('click', newGame);
        $('undo-btn').addEventListener('click', undo);
        $('fit-btn').addEventListener('click', () => { fitView(); drawBoard(); });
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
        renderUnits();
        drawBoard();
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
            return `<div class="play-player ${PLAYER_CLASSES[p]}"><h4>${escapeHtml(player.name)}</h4>${units.map(u => {
                const living = u.models.filter(m => m.alive);
                const placed = living.length && living.every(m => m.position);
                return `<button class="play-unit ${state.selected === u.id ? 'selected' : ''}" data-unit="${u.id}">
                    <span>${escapeHtml(u.name)}</span>
                    <small>${living.length} model${living.length === 1 ? '' : 's'} · ${placed ? 'placed' : 'not placed'}</small>
                </button>`;
            }).join('')}</div>`;
        }).join('');
        $('unit-list').querySelectorAll('[data-unit]').forEach(b => b.addEventListener('click', () => select(b.dataset.unit)));
        renderDetail();
    }

    function select(unitId) {
        state.selected = state.selected === unitId ? null : unitId;
        if (state.selected) message(`Click the board to place ${unitById(unitId).name} in its deployment zone.`);
        else message('');
        renderUnits();
        drawBoard();
    }

    function renderDetail() {
        const unit = state.selected && unitById(state.selected);
        if (!unit) {
            $('unit-detail').innerHTML = '';
            return;
        }
        $('unit-detail').innerHTML = unit.parts.map((part, i) => {
            const s = part.stats;
            const outline = state.game.outlines[unit.id][i];
            const alive = unit.models.filter(m => m.alive && m.part === i).length;
            return `<div class="play-part">
                <strong>${escapeHtml(part.name)}</strong>${part.role === 'leader' ? ' <small>(leader)</small>' : ''}
                <div class="play-stats">M ${escapeHtml(s.move || '-')}" · T ${s.toughness} · Sv ${s.save}+${s.invulnerable_save ? ` · ${s.invulnerable_save}++` : ''} · W ${s.wounds}</div>
                <small>${alive} of ${part.models} models · base ${escapeHtml((part.base && part.base.text) || 'unknown')}${outline.estimated ? ' (size estimated)' : ''}</small>
            </div>`;
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

        for (const unit of game.state.units) {
            const placed = unit.models.filter(m => m.alive && m.position);
            if (!placed.length) continue;
            const g = svgEl('g', { class: `play-unit-group ${PLAYER_CLASSES[unit.player]} ${state.selected === unit.id ? 'selected' : ''}`,
                                   'data-unit': unit.id }, svg);
            // The unit's outline: around every model's whole base, 0.4" out
            const reach = m => Math.max(game.outlines[unit.id][m.part].length, game.outlines[unit.id][m.part].width) / 2 + 0.4;
            const left = Math.min(...placed.map(m => m.position.x - reach(m)));
            const right = Math.max(...placed.map(m => m.position.x + reach(m)));
            const top = Math.min(...placed.map(m => D - m.position.y - reach(m)));
            const bottom = Math.max(...placed.map(m => D - m.position.y + reach(m)));
            svgEl('rect', { x: left, y: top, width: right - left, height: bottom - top, rx: 0.6, class: 'play-unit-outline' }, g);
            for (const model of placed) drawModel(g, unit, model, D);
            const label = svgEl('text', { x: (left + right) / 2, y: top - 0.3, class: 'play-label' }, g);
            label.textContent = unit.name;
        }
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
            if (state.selected) placeSelectedAt(boardPoint(event));
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
        for (const id of state.drag.modelIds) {
            const el = $('play-board').querySelector(`[data-model="${id}"]`);
            const model = unitById(state.drag.unitId).models.find(m => m.id === id);
            const x = model.position.x + state.drag.dx, y = state.game.board.depth - (model.position.y + state.drag.dy);
            el.setAttribute('transform', `translate(${x} ${y}) rotate(${-(model.position.angle || 0)})`);
        }
    }

    async function pointerUp() {
        const drag = state.drag;
        state.drag = null;
        if (!drag) return;
        const unit = unitById(drag.unitId);
        if (Math.hypot(drag.dx, drag.dy) < 0.05) {  // A click on a unit: show it
            state.selected = unit.id;
            message('');
            renderUnits();
            drawBoard();
            return;
        }
        const round = v => Math.round(v * 100) / 100;
        const moved = new Set(drag.modelIds);
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

    function message(text, isError = false) {
        $('play-message').textContent = text || '';
        $('play-message').classList.toggle('error', isError);
    }
})();
