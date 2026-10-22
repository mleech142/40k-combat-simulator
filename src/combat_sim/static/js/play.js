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
    const PHASE_NAMES = { setup: 'Setup', deployment: 'Deployment', command: 'Command', movement: 'Movement', shooting: 'Shooting',
                          charge: 'Charge', fight: 'Fight' };
    const MOVE_MODES = [['normal', 'Normal move'], ['advance', 'Advance'], ['fall_back', 'Fall Back']];
    const DONE = { normal: 'moved', advance: 'advanced', fall_back: 'fell back', stationary: 'remained stationary' };
    const STEP_NAMES = { pile_in: 'Pile in', fight: 'Fight', consolidate: 'Consolidate' };
    const SHORT_MOVE = 3;  // Pile-in and consolidation moves, as the server sends them in errors

    const state = {
        game: null,  // /api/play/games/<id> answer
        working: false,  // A request that changes the game hasn't answered yet (see whileWorking)
        selected: null,  // Unit id picked in the list
        view: null,  // The board's viewBox, in SVG units (inches, y down)
        drag: null,  // {unitId, modelIds, start, dx, dy} while dragging
        moveMode: 'normal',  // How the selected unit will move (Movement phase)
        waypoint: false,  // Its next move carries on from where it stands (Movement phase)
        shooting: null,  // /api/play/games/<id>/shooting/<unit> for the selected unit (Shooting phase)
        targets: {},  // The target chosen for each weapon, 'part:weapon' -> unit id
        fire: {},  // Whether each weapon is ticked to fire, 'part:weapon' -> bool
        charge: null,  // /api/play/games/<id>/charge/<unit> for the selected unit (Charge phase)
        chargeTargets: [],  // Enemy units picked as charge targets
        fight: null,  // /api/play/games/<id>/fight/<unit> for the selected unit (Fight step)
        fightWeapon: {},  // The melee weapon chosen for each part, part -> weapon
        fightTarget: {},  // The enemy unit each part attacks, part -> unit id
        correcting: false,  // The "Correct the game" panel is open
    };
    const $ = id => document.getElementById(id);

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        $('new-game-btn').addEventListener('click', newGame);
        $('games-btn').addEventListener('click', () => openModal('games-modal'));
        document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => closeModal(b.dataset.close)));
        document.querySelectorAll('.modal').forEach(m => m.addEventListener('click', e => { if (e.target === m) closeModal(m.id); }));
        $('new-red').addEventListener('change', loadMatchup);
        $('new-twist').addEventListener('change', renderTwistChoice);
        loadTwists();
        $('new-blue').addEventListener('change', loadMatchup);
        $('undo-btn').addEventListener('click', undo);
        $('fit-btn').addEventListener('click', () => { fitView(); drawBoard(); });
        $('phase-btn').addEventListener('click', nextPhase);
        $('correct-btn').addEventListener('click', () => { state.correcting = !state.correcting; renderCorrection(); });
        const board = $('play-board');
        board.addEventListener('wheel', zoom, { passive: false });
        board.addEventListener('pointerdown', pointerDown);
        board.addEventListener('pointermove', pointerMove);
        board.addEventListener('pointerup', pointerUp);
        document.addEventListener('battlefields-changed', e => fillBattlefields(e.detail));
        document.addEventListener('keydown', e => {
            const open = [...document.querySelectorAll('.modal.show')];
            if (e.key === 'Escape' && open.length) {  // Escape closes the pop-up on top
                closeModal((open.find(m => m.id === 'battlefields-modal') || open[0]).id);
                return;
            }
            if (open.length || !state.placing || state.working || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
            const key = e.key.toLowerCase();
            if (key === 'q') pivotPlacing(15);
            else if (key === 'e') pivotPlacing(-15);
            else if (key === 'enter') confirmPlacing();
            else if (key === 'escape') cancelPlacing();
            else return;
            e.preventDefault();
        });
        await Promise.all([loadArmies(), loadGames()]);
        await loadMatchup();
        const id = new URLSearchParams(location.search).get('game');
        if (id) await openGame(id);
        else openModal('games-modal');  // Nothing open yet: choose or start a game
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

    /** The Twist cards for the new-game form. */
    async function loadTwists() {
        state.twists = await fetch('/api/play/twists').then(r => r.json()).catch(() => ({ twists: [], mirrored: [] }));
        $('new-mirrored').innerHTML = state.twists.mirrored.map(m => `<option value="${m.key}">${escapeHtml(m.name)}</option>`).join('')
            + '<option value="roll" selected>Roll for it in setup</option>';
        renderTwistChoice();
    }

    /** The twist picker: twists that change primaries only when both armies have a force disposition. */
    function renderTwistChoice() {
        if (!state.twists) return;
        const select = $('new-twist');
        const current = select.value;
        const missions = !!(state.matchup && state.matchup.primaries);
        select.innerHTML = '<option value="">None</option>' + state.twists.twists.map(t =>
            `<option value="${t.key}" ${t.needs_missions && !missions ? 'disabled' : ''}>${escapeHtml(t.name)}</option>`).join('')
            + '<option value="random">Random</option>';
        select.value = [...select.options].some(o => o.value === current && !o.disabled) ? current : '';
        const chosen = state.twists.twists.find(t => t.key === select.value);
        $('new-mirrored').hidden = select.value !== 'mirrored-world';
        $('new-twist-text').textContent = chosen ? chosen.text : select.value === 'random' ? 'A twist drawn at random.' : '';
    }

    /** The two chosen armies' force dispositions and primary missions, and their pairing's layouts. */
    async function loadMatchup() {
        const red = $('new-red').value, blue = $('new-blue').value;
        state.matchup = red && blue
            ? await fetch(`/api/play/matchup?armies=${red}&armies=${blue}`).then(r => r.ok ? r.json() : null).catch(() => null) : null;
        const m = state.matchup;
        $('new-matchup').textContent = !m ? ''
            : m.primaries ? `${m.dispositions[0]} vs ${m.dispositions[1]}. Primary missions: ${m.primaries[0]} / ${m.primaries[1]}.`
            : 'An army has no force disposition: this game is scored by kill points.';
        fillBattlefields(state.battlefields || [], true);
        renderTwistChoice();
    }

    /** The New game form's battlefields (the editor sends the list whenever it changes): the pairing's
     * Event Companion layouts first, chosen at random unless one is picked. */
    function fillBattlefields(list, matchupChanged = false) {
        state.battlefields = list;
        const select = $('new-battlefield');
        const current = select.value;
        const layouts = (state.matchup && state.matchup.layouts) || [];
        const pairing = layouts.length ? `<optgroup label="This pairing (Event Companion)">
            <option value="pairing:random">Random layout (A, B or C)</option>
            ${layouts.map(b => `<option value="pairing:${b.layout}">${escapeHtml(b.name)}</option>`).join('')}</optgroup>` : '';
        select.innerHTML = pairing + Core.playBattlefieldGroups(list).map(g => `<optgroup label="${escapeHtml(g.label)}">${g.items.map(b =>
            `<option value="${b.key}">${escapeHtml(g.kind === 'layout' ? b.name : b.label)}${g.kind === 'saved' && b.terrain ? ` (${b.terrain} terrain)` : ''}</option>`).join('')}</optgroup>`).join('');
        if (matchupChanged && layouts.length) select.value = 'pairing:random';
        else if ([...select.options].some(o => o.value === current)) select.value = current;
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
        const twist = $('new-twist').value;
        const body = { armies: [$('new-red').value, $('new-blue').value].map(Number), name: $('new-name').value,
                       battlefield: $('new-battlefield').value || null,
                       twist: twist ? { key: twist, primary: twist === 'mirrored-world' ? $('new-mirrored').value : null } : null };
        const resp = await fetch('/api/play/games', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) {
            $('new-game-hint').textContent = data.error || 'The game could not be started.';
            return;
        }
        $('new-name').value = '';
        closeModal('games-modal');
        show(data);
        await loadGames();
    }

    /** The Games and Battlefields pop-ups (the Battlefields one opens over the Games one). */
    function openModal(id) {
        $(id).classList.add('show');
        document.dispatchEvent(new CustomEvent('modal-opened', { detail: id }));
    }

    function closeModal(id) {
        $(id).classList.remove('show');
    }

    async function openGame(id) {
        const resp = await fetch(`/api/play/games/${id}`);
        if (!resp.ok) return;
        closeModal('games-modal');
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
        state.placing = null;  // The server's game replaces anything placed provisionally
        if (first) {
            state.selected = null;
            fitView();
            history.replaceState(null, '', `/play?game=${game.id}`);
        }
        render();
    }

    // --- Actions -----------------------------------------------------------------------

    /**
     * Run a request that changes the game, one at a time: until it answers, the game ignores clicks, drags
     * and keys (so a second click can't send the action twice), and after a moment says it's working.
     * Answers the task's result, or null when another request was still going or the app couldn't be reached.
     */
    async function whileWorking(task) {
        if (state.working) return null;
        state.working = true;
        const page = $('tab-games');
        page.classList.add('play-busy');
        page.setAttribute('aria-busy', 'true');
        const shown = setTimeout(() => page.classList.add('play-busy-shown'), 150);
        try {
            return await task();
        } catch (error) {
            message("Couldn't reach the app: is it still running?", true);
            return null;
        } finally {
            clearTimeout(shown);
            page.classList.remove('play-busy', 'play-busy-shown');
            page.removeAttribute('aria-busy');
            state.working = false;
        }
    }

    async function send(action) {
        const done = await whileWorking(async () => {
            const resp = await fetch(`/api/play/games/${state.game.id}/actions`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(action),
            });
            const data = await resp.json();
            if (!resp.ok) {
                message(data.error || 'That move was not allowed.', true);
                return false;
            }
            message('');
            show(data);
            return true;
        });
        if (!done) render();  // Put any dragged models back
        return !!done;
    }

    async function undo() {
        await whileWorking(async () => {
            const resp = await fetch(`/api/play/games/${state.game.id}/undo`, { method: 'POST' });
            const data = await resp.json();
            if (!resp.ok) return message(data.error, true);
            message('Undone.');
            show(data);
        });
    }

    function place(unitId, positions) {
        return send({ type: 'place', unit: unitId, models: positions });
    }

    async function nextPhase() {
        const turn = state.game.state.turn;
        if (await send({ type: turn.phase === 'deployment' ? 'end_deployment' : 'next_phase' })) {
            state.moveMode = 'normal';
            clearOptions();
            loadOptions();
            const now = state.game.state.turn;
            message(`${state.game.state.players[now.player].name}: ${PHASE_NAMES[now.phase]} phase${now.step ? ' · ' + STEP_NAMES[now.step] : ''}.`);
        }
    }

    /** The Movement phase buttons: choose a move, roll to Advance, or remain stationary. */
    async function movementChoice(choice) {
        const unit = unitById(state.selected);
        if (choice === 'waypoint') {
            state.waypoint = !state.waypoint;
            message(state.waypoint ? `Drag ${unit.name} on from where it stands: the whole path counts against its Move.`
                : `Moves of ${unit.name} are measured in a straight line from where it started the phase.`);
            renderUnits();
            drawBoard();
            return;
        }
        if (choice === 'stationary' || choice === 'reset') {
            state.waypoint = false;
            if (await send({ type: choice === 'reset' ? 'reset_move' : 'stationary', unit: unit.id })) {
                message(choice === 'reset' ? `${unit.name} is back where it started the phase.` : `${unit.name} remains stationary.`);
            }
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

    /**
     * How far a model may move now, or null if it can't: its Move (+ the Advance roll) in the Movement
     * phase, the charge roll in the Charge phase, 3" when piling in or consolidating (either player).
     */
    function allowance(unit, model) {
        const turn = state.game.state.turn;
        if (turn.phase === 'movement' && unit.player === turn.player) {
            const roll = state.moveMode === 'advance' ? (unit.activity.advance_roll || 0) : 0;
            return state.game.moves[unit.id][model.part] + roll;
        }
        if (turn.phase === 'charge' && unit.player === turn.player) return unit.activity.charge_roll ?? null;
        if (turn.phase === 'fight' && (turn.step === 'pile_in' || turn.step === 'consolidate')) return SHORT_MOVE;
        return null;
    }

    /** Where a unit's moves this phase (or step) are measured from, in its activity. */
    function startKey() {
        const turn = state.game.state.turn;
        return { movement: 'start', charge: 'charge_start' }[turn.phase]
            || { pile_in: 'pile_start', consolidate: 'consolidate_start' }[turn.step] || 'start';
    }

    /** Whether the unit's next move carries on from where it stands (Movement phase, Waypoint ticked). */
    function waypointing(unit) {
        return state.waypoint && state.selected === unit.id && state.game.state.turn.phase === 'movement';
    }

    /** A model's path this Movement phase: where it started, its waypoints, where it stands. */
    function pathOf(unit, model) {
        return Core.playPath(startOf(unit, model), (unit.activity.via || {})[model.id], model.position);
    }

    /** Where a model started this phase's (or step's) moves: they are measured from there until it ends. */
    function startOf(unit, model) {
        const starts = unit.activity[startKey()];
        return (starts && starts[model.id]) || model.position;
    }

    function clearOptions() {
        state.shooting = null;
        state.missionActions = null;
        state.targets = {};
        state.fire = {};
        state.charge = null;
        state.chargeTargets = [];
        state.fight = null;
        state.fightWeapon = {};
        state.fightTarget = {};
    }

    /** Place the selected unit in formation, centred on a board point. */
    async function placeSelectedAt(point) {
        const unit = unitById(state.selected);
        const living = unit.models.filter(m => m.alive);
        const outlines = state.game.outlines[unit.id];
        const sizes = living.map(m => Math.max(outlines[m.part].length, outlines[m.part].width));
        // Fitted into the box round the player's zone (the server checks it's wholly within the zone itself)
        const zone = Core.playBounds(state.game.zones[unit.player]);
        const topEdge = (zone.bottom + zone.top) / 2 > state.game.board.depth / 2;  // The back row towards their own edge
        const spots = Core.playFit(Core.playFormation(sizes, point.x, point.y, topEdge), sizes.map(s => s / 2), zone);
        const angle = topEdge ? 180 : 0;
        startPlacing(unit, living.map((m, i) => ({ id: m.id, x: spots[i].x, y: spots[i].y, angle })));
    }

    // --- Deployment: a unit is placed provisionally, moved and pivoted, then confirmed with OK -------------

    /** Show a unit at these spots ([{id, x, y, angle}]) on the page only, until OK sends it (or Cancel). */
    function startPlacing(unit, spots) {
        if (state.placing && state.placing.unitId !== unit.id) {
            return message(`Press OK or Cancel for ${unitById(state.placing.unitId).name} first.`, true);
        }
        if (!state.placing) {  // Where its models were, to go back to on Cancel
            state.placing = { unitId: unit.id, original: Object.fromEntries(unit.models.map(m => [m.id, m.position && { ...m.position }])) };
        }
        const byId = Object.fromEntries(spots.map(s => [s.id, s]));
        unit.models.forEach(m => {
            if (byId[m.id]) m.position = { x: byId[m.id].x, y: byId[m.id].y, angle: byId[m.id].angle || 0 };
        });
        state.selected = unit.id;
        render();
    }

    function placingSpots() {
        const unit = unitById(state.placing.unitId);
        return unit.models.filter(m => m.alive && m.position).map(m => ({ id: m.id, ...m.position }));
    }

    function pivotPlacing(degrees) {
        if (!state.placing) return;
        startPlacing(unitById(state.placing.unitId), Core.playPivot(placingSpots(), degrees));
    }

    async function confirmPlacing() {
        if (!state.placing) return;
        const unitId = state.placing.unitId;
        if (await place(unitId, placingSpots())) {  // The server checks it (wholly in the zone, no overlaps)
            state.placing = null;
            state.selected = null;
            render();
        }
    }

    function cancelPlacing() {
        if (!state.placing) return;
        const unit = unitById(state.placing.unitId);
        unit.models.forEach(m => { m.position = state.placing.original[m.id] || null; });
        state.placing = null;
        render();
    }

    /** The bar above the board while a unit is being placed: pivot, OK, Cancel. */
    function renderPlacing() {
        const bar = $('place-bar');
        const placing = state.placing && state.game && state.game.state.turn.phase === 'deployment' ? state.placing : null;
        bar.hidden = !placing;
        if (!placing) return;
        const pivot = (deg, icon, label) => `<button class="btn-small" data-pivot="${deg}" title="Pivot ${label}">
            <i class="fa-solid fa-${icon}" aria-hidden="true"></i> ${Math.abs(deg)}°</button>`;
        bar.innerHTML = `<span>Placing <strong>${escapeHtml(unitById(placing.unitId).name)}</strong>: drag it or click the board to move it,
                pivot it (Q / E), then press OK.</span>
            <span class="play-pending-actions">${pivot(45, 'rotate-left', 'anticlockwise')}${pivot(15, 'rotate-left', 'anticlockwise')}
                ${pivot(-15, 'rotate-right', 'clockwise')}${pivot(-45, 'rotate-right', 'clockwise')}
                <button class="btn btn-primary" id="place-ok"><i class="fa-solid fa-check" aria-hidden="true"></i> OK</button>
                <button class="btn-small" id="place-cancel"><i class="fa-solid fa-xmark" aria-hidden="true"></i> Cancel</button></span>`;
        bar.querySelectorAll('[data-pivot]').forEach(b => b.addEventListener('click', () => pivotPlacing(Number(b.dataset.pivot))));
        $('place-ok').addEventListener('click', confirmPlacing);
        $('place-cancel').addEventListener('click', cancelPlacing);
    }

    // --- Drawing -----------------------------------------------------------------------

    function render() {
        const game = state.game;
        $('game-title').textContent = game ? game.name : 'Choose or start a game';
        $('twist-chip').hidden = !(game && game.twist);
        if (game && game.twist) {
            $('twist-chip').textContent = `Twist: ${game.twist.name}`;
            $('twist-chip').title = game.twist.text;
        }
        $('undo-btn').disabled = !game || !game.events.length || game.events[game.events.length - 1].rolls.length > 0;
        const turn = game && game.state.turn;
        const allPlaced = game && game.state.units.every(u => u.models.every(m => !m.alive || m.position));
        let banner = '';
        const setup = turn && turn.phase === 'setup';
        if (setup) {
            banner = 'Setting up the game';
        } else if (turn && turn.phase === 'deployment') {
            const roles = game.state.roles || {};
            const role = p => roles.attacker === p ? 'attacker' : 'defender';
            banner = allPlaced ? 'Deployment · every unit placed: press Deployment done'
                : `Deployment · ${game.state.players[turn.player].name} (${role(turn.player)}) to place`;
        } else if (turn) {
            banner = `Round ${turn.round} · ${game.state.players[turn.player].name} · ${PHASE_NAMES[turn.phase]} phase`;
            if (turn.step) banner += ` · ${STEP_NAMES[turn.step]}`;
            if (turn.step === 'fight' && turn.fight) {
                banner += turn.fight.picker == null ? ': every unit has fought'
                    : ` (${turn.fight.stage === 'fights_first' ? 'Fights First' : 'remaining combats'}): ${game.state.players[turn.fight.picker].name} to pick`;
            }
            if (turn.step === 'pile_in' || turn.step === 'consolidate') banner += ': both players';
        }
        const result = game && Core.playResult(game.state.players, game.score, turn);
        if (result) banner = result;
        $('turn-banner').textContent = banner;
        // The banner says what the board above doesn't: setup, deployment, the result, and who picks in the Fight phase
        $('turn-banner').hidden = !banner || (turn && !result && !['setup', 'deployment', 'fight'].includes(turn.phase));
        renderHud();
        $('turn-banner').className = `play-turn ${turn ? PLAYER_CLASSES[turn.step === 'fight' && turn.fight && turn.fight.picker != null ? turn.fight.picker : turn.player] : ''}`;
        $('phase-btn').hidden = !game || turn.phase === 'ended' || setup;
        $('correct-btn').hidden = !game || ['setup', 'deployment'].includes(turn.phase);
        if (turn) {
            $('phase-btn').querySelector('span').textContent = turn.phase === 'deployment' ? 'Deployment done'
                : turn.phase === 'fight' ? (turn.step === 'consolidate' ? 'End turn' : 'Next step') : 'Next phase';
        }
        renderPhaseTrack();
        renderArmies();
        renderUnits();
        drawBoard();
        renderLog();
        renderPending();
        renderSetup();
        renderPlacing();
        renderMission();
        renderCorrection();
        renderStratagems();
    }

    /** The Mission panel: each player's primary mission, its lines (when each scores, what it would score
     * now), VP against the caps, and Punishment's condemned units and choice. */
    function renderMission() {
        const game = state.game;
        const m = game && game.mission;
        [0, 1].forEach(i => { $(`army-missions-sec-${i}`).hidden = !m; });
        if (!m) return;
        const turn = game.state.turn;
        const name = id => (game.state.units.find(u => u.id === id) || { name: id }).name;
        m.primaries.forEach((p, i) => {
            const caps = Core.playVpCaps(m.vp[i], turn.round, m.caps);
            const lines = p.lines.map(line => {
                const now = (m.now[i] || {})[line.id];
                const badge = line.needs ? '<span class="play-mission-later">comes with actions</span>'
                    : now && now.vp ? `<span class="play-mission-now" title="${escapeHtml(now.how)}">now ${now.vp}VP</span>` : '';
                return `<li class="${line.needs ? 'later' : ''}">${vpChip(line)}<span>${escapeHtml(line.text)}</span>${badge}<small>${escapeHtml(line.timing)}</small></li>`;
            }).join('');
            const locating = m.choice && m.choice.kind === 'locate' && m.choice.player === i;
            const locate = locating ? `<div class="play-mission-choice"><strong>Locate and Deny:</strong>
                place a marker in each of ${Math.min(5, m.locate_options.length)} terrain areas outside your deployment zone (named on the board).
                ${m.locate_options.map(id => `<label><input type="checkbox" data-locate="${escapeHtml(id)}"> Terrain area ${escapeHtml(id)}</label>`).join('')}
                <button class="btn-small" id="locate-btn">Place markers</button></div>` : '';
            const beacon = m.choice && m.choice.kind === 'beacon' && m.choice.player === i ? `<div class="play-mission-choice">
                <strong>Beacon:</strong> choose your beacon unit.
                <select class="filter-select" id="beacon-unit">${m.beacon_options.map(id => `<option value="${id}">${escapeHtml(name(id))}</option>`).join('')}</select>
                <button class="btn-small" id="beacon-btn">Choose</button></div>` : '';
            const tempting = m.choice && m.choice.kind === 'tempting' && m.choice.player === i ? `<div class="play-mission-choice">
                <strong>A Tempting Target:</strong> choose ${escapeHtml(game.state.players[m.choice.for].name)}'s tempting target (an objective in No Man's Land).
                <select class="filter-select" id="tempting-objective">${m.tempting_options.map(id => `<option value="${id}">${escapeHtml(id)}</option>`).join('')}</select>
                <button class="btn-small" id="tempting-btn">Choose</button></div>` : '';
            const condemned = m.condemned[String(i)] || [];
            const choosing = m.choice && m.choice.player === i && m.condemn_options;
            const choice = choosing ? `<div class="play-mission-choice"><strong>Punishment:</strong>
                ${m.condemn_options.any ? 'no enemy unit qualifies, so condemn any one.' : 'condemn 1 to 3 enemy units within range of objectives or that destroyed one of yours last turn.'}
                ${m.condemn_options.units.map(id => `<label><input type="checkbox" data-condemn="${escapeHtml(id)}"> ${escapeHtml(name(id))}</label>`).join('')}
                <button class="btn-small" id="condemn-btn">Condemn</button></div>` : '';
            $(`army-missions-${i}`).innerHTML = `${choice}${locate}${beacon}${tempting}
                <div class="play-mission-block"><h6>Primary · ${escapeHtml(p.name)} <span class="play-mission-vp">${caps.primary} VP</span></h6>
                <p class="play-hint">This round ${caps.round}/${caps.roundCap}${caps.roundFull ? ' (full)' : ''} · game ${caps.primary}/${caps.gameCap}</p>
                ${condemned.length ? `<p class="play-hint">Condemned: ${condemned.map(id => escapeHtml(name(id))).join(', ')}</p>` : ''}
                <ul class="play-mission-lines">${lines}</ul></div>
                ${secondaryCards(m, i, turn)}`;
        });
        const panel = $('tab-games');
        panel.querySelectorAll('[data-card-op]').forEach(b => b.addEventListener('click', async () => {
            const result = await send({ type: 'tactical', player: Number(b.dataset.player), card: b.dataset.card, op: b.dataset.cardOp });
            if (result) message(b.dataset.cardOp === 'redraw' || b.dataset.cardOp === 'redraw_cp' ? 'Card redrawn.' : 'Done.');
        }));
        panel.querySelectorAll('[data-guard]').forEach(select => select.addEventListener('change', async () => {
            const player = Number(select.dataset.player);
            const guards = Object.fromEntries([...panel.querySelectorAll(`[data-guard][data-player="${player}"]`)].map(s => [s.dataset.guard, s.value || null]));
            if (await send({ type: 'set_guards', player, guards })) message('Guards set.');
        }));
        if ($('beacon-btn')) {
            $('beacon-btn').addEventListener('click', async () => {
                if (await send({ type: 'beacon', unit: $('beacon-unit').value })) message('Beacon chosen.');
            });
        }
        if ($('tempting-btn')) {
            $('tempting-btn').addEventListener('click', async () => {
                if (await send({ type: 'tempting', objective: $('tempting-objective').value })) message('Tempting target chosen.');
            });
        }
        if ($('locate-btn')) {
            $('locate-btn').addEventListener('click', async () => {
                const terrain = [...panel.querySelectorAll('[data-locate]:checked')].map(cb => cb.dataset.locate);
                if (await send({ type: 'place_markers', terrain })) message('Locate and Deny markers placed.');
            });
        }
        const button = $('condemn-btn');
        if (button) {
            button.addEventListener('click', async () => {
                const units = [...panel.querySelectorAll('[data-condemn]:checked')].map(cb => cb.dataset.condemn);
                if (await send({ type: 'condemn', units })) message(`Condemned: ${units.map(name).join(', ')}.`);
            });
        }
    }

    /** A mission line's VP chip: what it's worth, and "each" when it scores per objective, unit and so on. */
    function vpChip(line) {
        const worth = Core.playLineVp(line);
        return `<span class="play-line-vp" title="${escapeHtml(worth.note)}">${worth.amount}${worth.each ? '<small>each</small>' : ''}</span>`;
    }

    /** A player's secondary missions in the Mission panel: each card's lines and VP against its cap. */
    function secondaryCards(m, i, turn) {
        const chosen = m.secondaries && m.secondaries[i];
        if (!chosen) return '';
        if (!chosen.cards) return '<p class="play-hint">Secondary missions chosen: shown once both players have chosen.</p>';
        const caps = Core.playSecondaryCaps(m.vp[i], turn.round, m.secondary_caps);
        if (chosen.mode === 'tactical') return tacticalCards(m, i, turn, chosen, caps);
        return `<div class="play-mission-block"><h6>Secondary · ${escapeHtml(chosen.mode)} <span class="play-mission-vp">${caps.total} VP</span></h6>
            <p class="play-hint play-secondary-head">This round ${caps.round}/${caps.roundCap}${caps.roundFull ? ' (full)' : ''} · game ${caps.total}/${caps.gameCap}</p>
            ${chosen.cards.map(c => `<div class="play-secondary-card"><strong>${escapeHtml(c.name)}</strong>
                <span class="play-mission-vp">${(m.vp[i].cards || {})[c.key] || 0}/${caps.cardCap} VP</span>
                <ul class="play-mission-lines">${c.lines.map(line => {
                    const now = (m.now[i] || {})[line.id];
                    const badge = now && now.vp ? `<span class="play-mission-now" title="${escapeHtml(now.how)}">now ${now.vp}VP</span>` : '';
                    return `<li>${vpChip(line)}<span>${escapeHtml(line.text)}</span>${badge}<small>${escapeHtml(line.timing)}</small></li>`;
                }).join('')}</ul></div>`).join('')}</div>`;
    }

    /** A Tactical player's active cards: each line and what it would score now, Hold, discard at the end of
     * your turn (+1CP), redraw (a just-drawn card's own rule) and the once-per-battle discard and draw (1CP). */
    function tacticalCards(m, i, turn, chosen, caps) {
        const game = state.game;
        const name = id => (game.state.units.find(u => u.id === id) || { name: id }).name;
        const myTurn = turn.player === i, command = myTurn && turn.phase === 'command';
        const cards = chosen.cards.map(c => {
            const lines = c.lines.map(line => {
                const now = (m.now[i] || {})[line.id];
                const badge = now && now.vp ? `<span class="play-mission-now" title="${escapeHtml(now.how)}">now ${now.vp}VP</span>` : '';
                return `<li>${vpChip(line)}<span>${escapeHtml(line.text)}</span>${badge}<small>${escapeHtml(line.timing)}</small></li>`;
            }).join('');
            const extra = c.short === 'beacon' && chosen.beacon ? `<p class="play-hint">Beacon: ${escapeHtml(name(chosen.beacon))}</p>`
                : c.short === 'a-tempting-target' && chosen.tempting ? `<p class="play-hint">Tempting target: ${escapeHtml(chosen.tempting)}</p>`
                : c.short === 'burden-of-trust' ? guardsControls(m, i, chosen) : '';
            const buttons = [
                `<button class="btn-small ${c.hold ? 'active' : ''}" data-card-op="${c.hold ? 'unhold' : 'hold'}" data-card="${c.short}" data-player="${i}"
                    title="A held card isn't scored (or discarded) until you let it go">${c.hold ? 'Held' : 'Hold'}</button>`,
                myTurn ? `<button class="btn-small ${c.discard ? 'active' : ''}" data-card-op="${c.discard ? 'keep' : 'discard'}" data-card="${c.short}" data-player="${i}"
                    title="Discard it at the end of your turn, for 1CP">${c.discard ? 'Discarding (+1CP)' : 'Discard at end of turn'}</button>` : '',
                command && c.redraw ? `<button class="btn-small" data-card-op="redraw" data-card="${c.short}" data-player="${i}" title="${escapeHtml(c.redraw)}">Redraw</button>` : '',
                command && !chosen.redraw_used ? `<button class="btn-small" data-card-op="redraw_cp" data-card="${c.short}" data-player="${i}"
                    title="Once per battle: discard it and draw a new card">Discard &amp; draw (1CP)</button>` : '',
            ].join('');
            return `<div class="play-secondary-card ${c.hold ? 'held' : ''}"><strong>${escapeHtml(c.name)}</strong>
                <div class="play-card-buttons">${buttons}</div>${extra}<ul class="play-mission-lines">${lines}</ul></div>`;
        }).join('');
        return `<div class="play-mission-block"><h6>Secondary · tactical <span class="play-mission-vp">${caps.total} VP</span></h6>
            <p class="play-hint play-secondary-head">This round ${caps.round}/${caps.roundCap}${caps.roundFull ? ' (full)' : ''}
                · game ${caps.total}/${caps.gameCap} · deck ${chosen.deck}, discarded ${chosen.discarded}</p>
            ${cards || '<p class="play-hint">No active cards: two are drawn at the start of your Command phase.</p>'}</div>`;
    }

    /** Burden of Trust: which unit guards each objective (until the start of your next turn). */
    function guardsControls(m, i, chosen) {
        const game = state.game;
        const units = game.state.units.filter(u => u.player === i && u.models.some(x => x.alive && x.position));
        return `<div class="play-guards">${game.state.objectives.map(o => `<label>${escapeHtml(o.id)}
            <select class="filter-select" data-guard="${o.id}" data-player="${i}"><option value="">nobody</option>${units.map(u =>
                `<option value="${u.id}" ${chosen.guards[o.id] === u.id ? 'selected' : ''}>${escapeHtml(u.name)}</option>`).join('')}</select></label>`).join('')}</div>`;
    }

    /** The setup bar: the mission sequence's next step (central objective roll, roll-off, attacker or defender). */
    function renderSetup() {
        const game = state.game;
        const prompt = game && Core.playSetupPrompt(game.state.turn, game.state.players);
        $('setup-bar').hidden = !prompt;
        if (!prompt) return;
        if (prompt.secondaries) {
            renderSecondaryChoice(prompt);
            return;
        }
        $('setup-bar').innerHTML = `<span>${escapeHtml(prompt.text)}</span>
            <span class="play-pending-actions">${prompt.buttons.map((b, i) =>
                `<button class="btn-small" data-setup="${i}"><i class="fa-solid fa-${b.icon}" aria-hidden="true"></i> ${escapeHtml(b.label)}</button>`).join('')}</span>`;
        $('setup-bar').querySelectorAll('[data-setup]').forEach(button => button.addEventListener('click', async () => {
            const action = prompt.buttons[Number(button.dataset.setup)].action;
            if (await send(action) && state.game.state.turn.phase === 'deployment') {
                message(`${state.game.state.players[state.game.state.turn.player].name} is the defender and deploys first.`);
            }
        }));
    }

    /** Setup: each player chooses Fixed and two Fixed secondary missions (a choice shows as 'chosen' until
     * both have chosen; Tactical comes later). */
    function renderSecondaryChoice(prompt) {
        const game = state.game;
        const m = game.mission;
        const most = m.secondary_caps.cards;
        $('setup-bar').innerHTML = `<span>${escapeHtml(prompt.text)}</span>
            <div class="play-secondary-choice">${game.state.players.map((pl, i) => m.secondaries[i]
                ? `<div class="${PLAYER_CLASSES[i]}"><strong>${escapeHtml(pl.name)}</strong>: chosen</div>`
                : `<div class="${PLAYER_CLASSES[i]}" data-choose="${i}"><strong>${escapeHtml(pl.name)}</strong>
                    <label><input type="radio" name="mode-${i}" value="fixed" checked> Fixed</label>
                    <label><input type="radio" name="mode-${i}" value="tactical"> Tactical (draw 2 each Command phase)</label>
                    ${m.secondary_options.map(c => `<label title="${escapeHtml(c.lines.map(l => l.text).join(' / '))}">
                        <input type="checkbox" data-card="${c.key}"> ${escapeHtml(c.name)}</label>`).join('')}
                    <button class="btn-small" data-lock="${i}">Lock in for ${escapeHtml(pl.name)}</button></div>`).join('')}</div>`;
        $('setup-bar').querySelectorAll('[data-lock]').forEach(button => button.addEventListener('click', async () => {
            const box = $('setup-bar').querySelector(`[data-choose="${button.dataset.lock}"]`);
            const mode = box.querySelector(`[name="mode-${button.dataset.lock}"]:checked`).value;
            const cards = mode === 'fixed' ? [...box.querySelectorAll('[data-card]:checked')].map(cb => cb.dataset.card) : [];
            if (mode === 'fixed' && cards.length !== most) return message(`Choose ${most} Fixed secondary missions.`, true);
            if (await send({ type: 'choose_secondaries', player: Number(button.dataset.lock), mode, cards })) {
                const now = state.game.state.turn;
                message(now.phase === 'deployment' ? `Secondary missions revealed. ${state.game.state.players[now.player].name} is the defender and deploys first.`
                    : `${game.state.players[button.dataset.lock].name}'s secondary missions are locked in.`);
            }
        }));
    }

    /** The scoreboard above the board: each player's name and CP either side of the score (the player acting
     * lit up), and a pip for each battle round. */
    function renderHud() {
        const game = state.game;
        const turn = game && game.state.turn;
        const playing = turn && !['setup', 'deployment'].includes(turn.phase);
        const acting = actingPlayer(turn);
        [0, 1].forEach(i => {
            const side = $(`hud-side-${i}`);
            const player = game && game.state.players[i];
            side.classList.toggle('active', !!playing && turn.phase !== 'ended' && acting === i);
            side.innerHTML = player ? `<span class="play-side-name">${escapeHtml(player.name)}</span>
                <span class="play-side-cp">${player.cp ?? 0} CP</span>` : '';
        });
        $('hud-score').innerHTML = game ? `${game.score[0]}<span>–</span>${game.score[1]}` : '';
        $('hud-score').title = game ? (game.mission ? 'Victory points' : 'Kill points') : '';
        $('hud-pips').innerHTML = game ? [1, 2, 3, 4, 5].map(r =>
            `<span class="play-pip ${r < turn.round || turn.phase === 'ended' ? 'done' : r === turn.round && playing ? 'now' : ''}"></span>`).join('') : '';
    }

    /** Who is acting: the player whose turn it is, or whoever picks the next unit to fight. */
    function actingPlayer(turn) {
        return turn && (turn.step === 'fight' && turn.fight && turn.fight.picker != null ? turn.fight.picker : turn.player);
    }

    /** Each army panel's header: name, faction and disposition, CP and VP (primary and secondary), lit up for
     * the player acting. */
    function renderArmies() {
        const game = state.game;
        const turn = game && game.state.turn;
        const playing = turn && !['setup', 'deployment', 'ended'].includes(turn.phase);
        [0, 1].forEach(i => {
            const player = game && game.state.players[i];
            $(`army-${i}`).classList.toggle('active', !!playing && actingPlayer(turn) === i);
            if (!player) {
                $(`army-head-${i}`).innerHTML = `<span class="play-army-name">Player ${i + 1}</span>`;
                return;
            }
            const m = game.mission;
            const split = m ? `Primary ${Core.playVpCaps(m.vp[i], turn.round, m.caps).primary}
                · Secondary ${Core.playSecondaryCaps(m.vp[i], turn.round, m.secondary_caps).total}` : '';
            $(`army-head-${i}`).innerHTML = `<span class="play-army-name">${escapeHtml(player.name)}
                    <small>${escapeHtml(player.faction || '')}${player.disposition ? ` · ${escapeHtml(player.disposition)}` : ''}</small></span>
                <span class="play-army-stats"><span class="play-army-cp" title="Command points">${player.cp ?? 0}<small>CP</small></span>
                    <span class="play-army-vp" title="${m ? 'Victory points' : 'Kill points'}">${game.score[i]}<small>${m ? 'VP' : 'pts'}</small></span></span>
                ${split ? `<span class="play-army-split">${split}</span>` : ''}`;
        });
    }

    /** The squares above the board: this turn's phases, the current one lit in the active player's colour. */
    function renderPhaseTrack() {
        const game = state.game;
        const track = $('phase-track');
        const turn = game && game.state.turn;
        track.hidden = !game || ['setup', 'deployment'].includes(turn.phase);
        if (track.hidden) return;
        track.className = `play-phases ${PLAYER_CLASSES[turn.player]}`;
        track.innerHTML = Core.playPhaseTrack(turn).map(p => `
            <div class="play-phase ${p.state}" title="${p.label} phase${p.state === 'current' ? ' (now)' : ''}"
                 ${p.state === 'current' ? 'aria-current="step"' : ''}>
                <span class="play-phase-box"><i class="fa-solid fa-${p.icon}" aria-hidden="true"></i>
                    ${p.state === 'done' ? '<i class="fa-solid fa-check play-phase-done" aria-hidden="true"></i>' : ''}</span>
                <span class="play-phase-label">${p.label}${p.step ? `<small>${p.step}</small>` : ''}</span>
            </div>`).join('<span class="play-phase-link" aria-hidden="true"></span>');
    }

    function unitById(id) {
        return state.game.state.units.find(u => u.id === id);
    }

    function renderUnits() {
        const game = state.game;
        if (!game) {
            [0, 1].forEach(p => {
                $(`army-units-${p}`).innerHTML = '<p class="empty-message">No game open</p>';
                $(`army-units-count-${p}`).textContent = '';
            });
            $('unit-detail').innerHTML = '';
            return;
        }
        game.state.players.forEach((player, p) => {
            const units = game.state.units.filter(u => u.player === p);
            const orders = (player.orders || {}).casualties || 'farthest';
            const standing = units.filter(u => u.models.some(m => m.alive)).length;
            $(`army-units-count-${p}`).textContent = `${standing}/${units.length}`;
            $(`army-units-${p}`).innerHTML = `<div class="play-player ${PLAYER_CLASSES[p]}">
                <label class="play-orders">Casualties
                    <select data-orders="${p}" class="filter-select">
                        <option value="farthest" ${orders === 'farthest' ? 'selected' : ''}>farthest from the attacker first</option>
                        <option value="closest" ${orders === 'closest' ? 'selected' : ''}>closest to the attacker first</option>
                    </select></label>${units.map(u => {
                const living = u.models.filter(m => m.alive);
                const placed = living.length && living.every(m => m.position);
                const done = game.state.turn.phase === 'deployment' ? (state.placing && state.placing.unitId === u.id ? 'placing: press OK' : placed ? 'placed' : 'not placed')
                    : [DONE[u.activity.moved], u.activity.advance_roll != null ? `Advance roll ${u.activity.advance_roll}` : '',
                       u.activity.shot ? 'shot' : '',
                       u.activity.charge_roll != null ? `charge roll ${u.activity.charge_roll}${u.activity.charged ? ', charged' : ''}` : '',
                       u.activity.fought ? 'fought' : '']
                        .filter(Boolean).join(' · ');
                const ready = game.ready.includes(u.id);
                const acted = actedThisPhase(u, game.state.turn);
                const tags = Core.playUnitTags(u, { ready, inRange: (game.unit_objectives || {})[u.id], objectives: game.state.objectives, players: game.state.players });
                return `<button class="play-unit ${state.selected === u.id ? 'selected' : ''} ${ready ? 'ready' : ''} ${acted ? 'acted' : ''} ${living.length ? '' : 'destroyed'}" data-unit="${u.id}">
                    <span class="play-unit-name">${escapeHtml(u.name)}</span>${tags.length ? `<span class="play-tags">${tags.map(t =>
                        `<span class="play-tag ${t.kind}" title="${escapeHtml(t.title)}">${t.icon ? `<i class="${t.icon}" aria-hidden="true"></i> ` : ''}${escapeHtml(t.label)}</span>`).join('')}</span>` : ''}
                    <small>${living.length} model${living.length === 1 ? '' : 's'}${done ? ' · ' + escapeHtml(done) : ''}</small>
                </button>`;
            }).join('')}</div>`;
            $(`army-units-${p}`).querySelectorAll('[data-unit]').forEach(b => b.addEventListener('click', () => select(b.dataset.unit)));
            $(`army-units-${p}`).querySelectorAll('[data-orders]').forEach(s => s.addEventListener('change', () =>
                send({ type: 'orders', player: Number(s.dataset.orders), casualties: s.value })));
        });
        renderDetail();
    }

    /** Whether a unit has already done this phase's (or step's) thing: moved, shot, charged, piled in, fought... */
    function actedThisPhase(unit, turn) {
        const a = unit.activity;
        if (turn.phase === 'movement') return turn.player === unit.player && !!a.moved;
        if (turn.phase === 'shooting') return !!a.shot;
        if (turn.phase === 'charge') return !!a.charged || (a.charge_roll != null && !state.game.ready.includes(unit.id));
        if (turn.phase === 'fight' && turn.step === 'pile_in') return !!a.pile_start;
        if (turn.phase === 'fight' && turn.step === 'fight') return !!a.fought;
        if (turn.phase === 'fight' && turn.step === 'consolidate') return !!a.consolidate_start;
        return false;
    }

    function select(unitId) {
        if (state.placing && state.placing.unitId !== unitId) {
            return message(`Press OK or Cancel for ${unitById(state.placing.unitId).name} first.`, true);
        }
        const changed = state.selected !== unitId;
        state.selected = changed ? unitId : null;
        if (changed) {
            state.waypoint = false;
            const activity = unitById(unitId).activity;
            state.moveMode = activity.advance_roll != null ? 'advance'
                : ['normal', 'fall_back'].includes(activity.moved) ? activity.moved : 'normal';
        }
        const turn = state.game.state.turn;
        if (!state.selected) message('');
        else if (turn.phase === 'deployment') message(`Click the board to place ${unitById(unitId).name} in its deployment zone, then press OK.`);
        else message('');
        if (changed) {
            clearOptions();
            loadOptions();
        }
        renderUnits();
        drawBoard();
        renderStratagems();
        renderCorrection();
    }

    function renderDetail() {
        const unit = state.selected && unitById(state.selected);
        if (!unit) {
            $('unit-detail').innerHTML = '';
            return;
        }
        const home = $(`army-detail-${unit.player}`);
        if ($('unit-detail').parentNode !== home) home.appendChild($('unit-detail'));
        const turn = state.game.state.turn;
        let controls = '';
        if (turn.phase === 'movement' && unit.player === turn.player) {
            const rolled = unit.activity.advance_roll != null;
            const moved = unit.activity.moved;
            controls = `<div class="play-move-modes">${MOVE_MODES.map(([mode, label]) => `
                <button class="btn-small ${state.moveMode === mode ? 'active' : ''}" data-mode="${mode}"
                    ${rolled && mode !== 'advance' ? 'disabled' : ''}>${label}${mode === 'advance' && rolled ? ` (+${unit.activity.advance_roll}")` : ''}</button>`).join('')}
                <button class="btn-small ${moved === 'stationary' ? 'active' : ''}" data-mode="stationary" ${rolled ? 'disabled' : ''}>Remain stationary</button>
                ${moved && moved !== 'stationary' ? `<button class="btn-small ${state.waypoint ? 'active' : ''}" data-mode="waypoint"
                    title="The next move carries on from here, so the unit can go round terrain"><i class="fa-solid fa-route" aria-hidden="true"></i> Waypoint</button>` : ''}
                ${moved ? '<button class="btn-small" data-mode="reset">Reset move</button>' : ''}
            </div><p class="play-hint">Drag the unit to make the ${MOVE_MODES.find(m => m[0] === state.moveMode)[1]}.
                ${state.waypoint ? 'Waypoint: the next drag carries on from where it stands, and the whole path counts.'
                    : 'Each drag is measured in a straight line from where it started; Waypoint carries on from where it stands instead.'}
                You can move it again or reset it until the Movement phase ends; then moves are locked.</p>`;
        }
        if (turn.phase === 'shooting' && unit.player === turn.player && state.shooting && state.shooting.unit === unit.id) {
            controls = shootingControls(unit);
        }
        if (turn.phase === 'shooting' && state.missionActions && state.missionActions.unit === unit.id) {
            controls = missionActionControls() + controls;
        }
        if (turn.phase === 'charge' && unit.player === turn.player && state.charge && state.charge.unit === unit.id) {
            controls = chargeControls(unit);
        }
        if (turn.phase === 'fight' && turn.step === 'fight' && state.fight && state.fight.unit === unit.id) {
            controls = fightControls(unit);
        }
        if (turn.phase === 'fight' && (turn.step === 'pile_in' || turn.step === 'consolidate')) {
            const verb = turn.step === 'pile_in' ? 'pile in' : 'consolidate';
            const moved = unit.activity[startKey()];
            controls = `<p class="play-hint">Drag the unit to ${verb} (up to 3", each model ending closer to the enemy).
                Both players can move units now; moves lock when the step ends.</p>
                ${moved ? '<div class="play-move-modes"><button class="btn-small" data-reset>Reset move</button></div>' : ''}`;
        }
        if (turn.phase !== 'deployment' && turn.phase !== 'ended') controls += effectsControls(unit);
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
        $('unit-detail').querySelectorAll('[data-start-action]').forEach(b => b.addEventListener('click', () => startAction(unit, b.dataset.startAction)));
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
        $('unit-detail').querySelectorAll('[data-reset]').forEach(b => b.addEventListener('click', async () => {
            if (await send({ type: 'reset_move', unit: unit.id })) message(`${unit.name} is back where it started.`);
        }));
        const roll = $('charge-btn');
        if (roll) roll.addEventListener('click', declareCharge);
        $('unit-detail').querySelectorAll('[data-charge-target]').forEach(b => b.addEventListener('click', () => toggleChargeTarget(b.dataset.chargeTarget)));
        $('unit-detail').querySelectorAll('[data-fight-weapon]').forEach(s => s.addEventListener('change', () => {
            state.fightWeapon[s.dataset.fightWeapon] = Number(s.value);
        }));
        $('unit-detail').querySelectorAll('[data-fight-target]').forEach(s => s.addEventListener('change', () => {
            state.fightTarget[s.dataset.fightTarget] = s.value;
            drawBoard();
        }));
        const strike = $('fight-btn');
        if (strike) strike.addEventListener('click', fightSelected);
        const addEffect = $('effect-add');
        if (addEffect) {
            addEffect.addEventListener('click', () => send({ type: 'effect', unit: unit.id, effect: $('effect-kind').value,
                                                             until: $('effect-until').value }));
        }
        $('unit-detail').querySelectorAll('[data-remove-effect]').forEach(b => b.addEventListener('click', () =>
            send({ type: 'remove_effect', unit: unit.id, index: Number(b.dataset.removeEffect) })));
    }

    // --- Effects, stratagems and rules ---------------------------------------------------------

    const UNTIL = [['phase', 'this phase'], ['turn', 'this turn'], ['game', 'the rest of the game']];

    function effectsControls(unit) {
        const list = (unit.effects || []).map((e, i) => `<li><span class="play-effect ${e.source}">${escapeHtml(e.name)}</span>
            <small>${e.until === 'game' ? 'for the game' : 'this ' + e.until}</small>
            <button class="btn-icon" data-remove-effect="${i}" title="Remove" aria-label="Remove ${escapeHtml(e.name)}">
                <i class="fa-solid fa-xmark" aria-hidden="true"></i></button></li>`).join('');
        return `<details class="play-effects" ${(unit.effects || []).length ? 'open' : ''}>
            <summary>Effects on ${escapeHtml(unit.name)}${(unit.effects || []).length ? ` (${unit.effects.length})` : ''}</summary>
            ${list ? `<ul>${list}</ul>` : ''}
            <div class="play-effect-add">
                <select id="effect-kind" class="filter-select">${state.game.manual_effects.map(e =>
                    `<option value="${e.key}">${escapeHtml(e.name)} (${e.side === 'Target' ? 'when attacked' : 'when attacking'})</option>`).join('')}</select>
                <select id="effect-until" class="filter-select">${UNTIL.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select>
                <button class="btn-small" id="effect-add">Add effect</button>
            </div></details>`;
    }

    function renderStratagems() {
        const game = state.game;
        const playing = game && ['command', 'movement', 'shooting', 'charge', 'fight'].includes(game.state.turn.phase);
        [0, 1].forEach(p => { $(`army-strats-sec-${p}`).hidden = !playing; });
        if (!playing) return;
        const turn = game.state.turn;
        const selected = state.selected && unitById(state.selected);
        game.state.players.forEach((pl, p) => {
                const usable = Core.playUsableNow(pl.stratagems || [], turn.phase);
                const used = pl.used || {};
                $(`army-strats-count-${p}`).textContent = usable.length ? `${usable.length} now` : '';
                // Used on the selected unit, so only its own side's are live (and say how long a rule lasts)
                const hint = selected && selected.player === p
                    ? `<p class="play-hint">Used on <strong>${escapeHtml(selected.name)}</strong>. Rules last
                        <select id="rule-until" class="filter-select">${UNTIL.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></p>`
                    : `<p class="play-hint">Select one of ${escapeHtml(pl.name)}'s units to use these on it.</p>`;
                $(`army-strats-${p}`).innerHTML = `<div class="play-player-stratagems ${PLAYER_CLASSES[p]}">${hint}
                    ${usable.map(e => {
                        const disabled = !selected || selected.player !== p || (e.kind === 'stratagem' && (e.cp > (pl.cp ?? 0)
                            || Object.values(used).length && used[e.key] === phaseId(turn)));
                        return `<button class="play-stratagem ${e.kind}" data-use="${p}|${e.key}" ${disabled ? 'disabled' : ''}
                            title="${escapeHtml(e.text || '')}"><span>${escapeHtml(e.name)}</span>
                            <small>${e.kind === 'stratagem' ? e.cp + ' CP' : 'rule'}${e.modifier ? ' · ' + escapeHtml(e.modifier) : ' · apply by hand'}</small></button>`;
                    }).join('') || '<p class="empty-message">Nothing for this phase</p>'}
                </div>`;
                $(`army-strats-${p}`).querySelectorAll('[data-use]').forEach(b => b.addEventListener('click', () => useEntry(b.dataset.use)));
        });
    }

    function phaseId(turn) {
        return `${turn.round}.${turn.player}.${turn.phase}.${turn.step || ''}`;
    }

    async function useEntry(code) {
        const [player, key] = code.split('|');
        const entry = state.game.state.players[player].stratagems.find(e => e.key === key);
        const action = { type: entry.kind, player: Number(player), key, unit: state.selected };
        if (entry.kind === 'rule') action.until = $('rule-until').value;
        if (await send(action)) {
            message(entry.modifier ? `${entry.name}: ${entry.modifier} on ${unitById(action.unit).name}.`
                : `${entry.name} used: apply it by hand (add an effect if it changes the dice). ${entry.text || ''}`.slice(0, 400));
        }
    }

    // --- Correcting the game -----------------------------------------------------------------------

    function renderPending() {
        const game = state.game;
        const pending = game && game.state.pending;
        $('pending-bar').hidden = !pending;
        if (!pending) return;
        const by = game.state.players[pending.player].name, other = game.state.players[1 - pending.player].name;
        const models = Object.keys(pending.models).length;
        const cp = [...Object.entries(pending.cp).map(([p, v]) => `${game.state.players[p].name} ${v} CP`),
                    ...Object.entries(pending.vp || {}).map(([p, v]) => `${game.state.players[p].name} ${v > 0 ? '+' : ''}${v} VP`)].join(', ');
        $('pending-bar').innerHTML = `<strong>${escapeHtml(by)} proposes a correction:</strong> ${escapeHtml(pending.note || '(no note)')}
            <small>${models ? `${models} model${models === 1 ? '' : 's'} changed` : ''}${models && cp ? '; ' : ''}${escapeHtml(cp)}</small>
            <span class="play-pending-actions">${escapeHtml(other)}:
                <button class="btn-small" data-answer="accept">Accept</button>
                <button class="btn-small" data-answer="decline">Decline</button></span>`;
        $('pending-bar').querySelectorAll('[data-answer]').forEach(b => b.addEventListener('click', async () => {
            if (await send({ type: b.dataset.answer, player: 1 - pending.player })) {
                message(b.dataset.answer === 'accept' ? 'Correction made.' : 'Correction declined.');
            }
        }));
    }

    function renderCorrection() {
        const panel = $('correction-panel');
        const game = state.game;
        panel.hidden = !state.correcting || !game || !!game.state.pending;
        if (panel.hidden) return;
        const unit = state.selected && unitById(state.selected);
        const rows = unit ? unit.models.map(m => {
            const part = unit.parts[m.part];
            return `<tr><td>${escapeHtml(part.name)} ${m.id.split('-').pop()}</td>
                <td><input type="number" min="0" max="${part.stats.wounds}" value="${m.wounds_left}" data-fix-wounds="${m.id}" class="filter-input"></td>
                <td><label><input type="checkbox" data-fix-alive="${m.id}" ${m.alive ? 'checked' : ''}> alive</label></td></tr>`;
        }).join('') : '';
        panel.innerHTML = `<h3>Correct the game</h3>
            <p class="play-hint">Propose a fix; the other player must accept it before anything else happens.
                ${unit ? '' : 'Select a unit to change its models.'}</p>
            ${unit ? `<table class="data-table data-table-compact"><thead><tr><th>${escapeHtml(unit.name)}</th><th>Wounds</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : ''}
            <div class="play-correction-row">${game.state.players.map((pl, i) =>
                `<label>${escapeHtml(pl.name)} CP <input type="number" min="0" value="${pl.cp ?? 0}" data-fix-cp="${i}" class="filter-input"></label>`).join('')}</div>
            ${game.mission ? `<div class="play-correction-row">${game.state.players.map((pl, i) =>
                `<label>${escapeHtml(pl.name)} VP + / − <input type="number" step="1" value="0" data-fix-vp="${i}" class="filter-input"></label>`).join('')}</div>` : ''}
            <div class="play-correction-row"><input type="text" id="fix-note" class="filter-input" maxlength="300" placeholder="What went wrong?">
                <select id="fix-by" class="filter-select">${game.state.players.map((pl, i) => `<option value="${i}">Proposed by ${escapeHtml(pl.name)}</option>`).join('')}</select>
                <button class="btn btn-primary" id="fix-propose">Propose</button>
                <button class="btn-small" id="fix-cancel">Cancel</button></div>`;
        $('fix-cancel').addEventListener('click', () => { state.correcting = false; renderCorrection(); });
        $('fix-propose').addEventListener('click', proposeCorrection);
    }

    async function proposeCorrection() {
        const game = state.game;
        const unit = state.selected && unitById(state.selected);
        const models = {};
        if (unit) {
            for (const m of unit.models) {
                const wounds = Number($('correction-panel').querySelector(`[data-fix-wounds="${m.id}"]`).value);
                const alive = $('correction-panel').querySelector(`[data-fix-alive="${m.id}"]`).checked;
                if (wounds !== m.wounds_left || alive !== m.alive) models[m.id] = { wounds_left: alive && !wounds ? 1 : wounds, alive };
            }
        }
        const cp = {};
        $('correction-panel').querySelectorAll('[data-fix-cp]').forEach(input => {
            const i = Number(input.dataset.fixCp);
            if (Number(input.value) !== (game.state.players[i].cp ?? 0)) cp[i] = Number(input.value);
        });
        const vp = {};
        $('correction-panel').querySelectorAll('[data-fix-vp]').forEach(input => {
            if (Number(input.value)) vp[input.dataset.fixVp] = Math.trunc(Number(input.value));
        });
        if (await send({ type: 'correction', player: Number($('fix-by').value), note: $('fix-note').value, models, cp, vp })) {
            state.correcting = false;
            message('Correction proposed: waiting for the other player.');
        }
    }

    // --- Options for the selected unit (Shooting, Charge, Fight) ----------------------------

    async function loadOptions() {
        const unit = state.selected && unitById(state.selected);
        if (!unit) return;
        const turn = state.game.state.turn;
        const kind = turn.phase === 'shooting' && unit.player === turn.player ? 'shooting'
            : turn.phase === 'charge' && unit.player === turn.player ? 'charge'
            : turn.phase === 'fight' && turn.step === 'fight' ? 'fight' : null;
        if (!kind) return;
        const resp = await fetch(`/api/play/games/${state.game.id}/${kind}/${unit.id}`);
        if (!resp.ok || state.selected !== unit.id) return;
        const options = { unit: unit.id, ...(await resp.json()) };
        if (kind === 'shooting') {
            state.shooting = options;
            const actions = state.game.mission
                ? await fetch(`/api/play/games/${state.game.id}/mission-actions/${unit.id}`).then(r => r.ok ? r.json() : null).catch(() => null)
                : null;
            state.missionActions = actions && actions.actions.length ? { unit: unit.id, actions: actions.actions } : null;
        }
        if (kind === 'charge') {
            state.charge = options;
            state.chargeTargets = state.chargeTargets.filter(id => options.targets[id])
                .concat((unit.activity.charge_targets || []).filter(id => !state.chargeTargets.includes(id)));
        }
        if (kind === 'fight') {
            state.fight = options;
            for (const part of options.parts) {
                if (state.fightWeapon[part.part] == null && part.choice != null) state.fightWeapon[part.part] = part.choice;
                if (!state.fightTarget[part.part] && options.targets.length) state.fightTarget[part.part] = options.targets[0];
            }
        }
        renderDetail();
        drawBoard();
    }

    // --- Mission actions -------------------------------------------------------------------

    /** The selected unit's mission actions: each with its target (and marker, for a sensor sweep), or why not. */
    function missionActionControls() {
        const players = state.game.state.players;
        const rows = state.missionActions.actions.map(a => {
            if (a.reason) return `<p class="play-hint"><strong>${escapeHtml(a.name)}</strong>: ${escapeHtml(a.reason)}</p>`;
            const markers = a.markers.length ? `<select class="filter-select" data-action-marker="${a.key}">${a.markers.map(m =>
                `<option value="${m.id}">Remove ${escapeHtml(players[m.player].name)}'s ${escapeHtml(Core.playMarker(m.tag).label.toLowerCase())} (${m.id})</option>`).join('')}</select>` : '';
            return `<div class="play-action-row"><strong>${escapeHtml(a.name)}</strong>
                <select class="filter-select" data-action-target="${a.key}">${a.targets.map(t => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.label)}</option>`).join('')}</select>
                ${markers}<button class="btn-small" data-start-action="${a.key}">Start</button></div>`;
        }).join('');
        return `<div class="play-mission-actions"><h4>Mission actions</h4>${rows}
            <p class="play-hint">A unit that starts an action can't shoot or charge this turn.</p></div>`;
    }

    async function startAction(unit, key) {
        const detail = $('unit-detail');
        const target = detail.querySelector(`[data-action-target="${key}"]`).value;
        const marker = detail.querySelector(`[data-action-marker="${key}"]`);
        const name = state.missionActions.actions.find(a => a.key === key).name;
        if (await send({ type: 'start_action', unit: unit.id, action: key, target, ...(marker ? { marker: marker.value } : {}) })) {
            message(`${unit.name} started ${name}.`);
            clearOptions();
            loadOptions();
        }
    }

    // --- Charges --------------------------------------------------------------------------

    function chargeControls(unit) {
        const options = state.charge;
        if (options.roll == null) {
            if (options.reason) return `<p class="play-hint">${escapeHtml(options.reason)}</p>`;
            return `<div class="play-shooting"><button class="btn btn-primary" id="charge-btn">
                <i class="fa-solid fa-bolt" aria-hidden="true"></i> Declare charge (roll 2D6)</button></div>`;
        }
        const reachable = Object.keys(options.targets);
        if (!reachable.length) {
            return `<p class="play-hint">Charge roll ${options.roll}: no enemy unit is within reach, so the charge fails.</p>`;
        }
        const chips = reachable.map(id => `<button class="btn-small ${state.chargeTargets.includes(id) ? 'active' : ''}"
            data-charge-target="${id}">${escapeHtml(unitById(id).name)} <small>${options.targets[id].distance}"</small></button>`).join('');
        return `<p class="play-hint">Charge roll <strong>${options.roll}"</strong>. Pick the units to charge (or click them on
            the board), then drag the unit so it ends engaged with every one of them and no other.
            You can redo the move until the Charge phase ends.</p>
            <div class="play-move-modes">${chips}
            ${unit.activity.charged ? '<button class="btn-small" data-reset>Reset charge</button>' : ''}</div>`;
    }

    async function declareCharge() {
        const unit = unitById(state.selected);
        if (await send({ type: 'charge', unit: unit.id })) {
            message(`${unit.name} charge roll: ${unitById(unit.id).activity.charge_roll}".`);
            state.charge = null;
            loadOptions();
        }
    }

    function toggleChargeTarget(id) {
        if (!state.charge || !state.charge.targets[id]) return;
        state.chargeTargets = state.chargeTargets.includes(id) ? state.chargeTargets.filter(t => t !== id) : [...state.chargeTargets, id];
        renderDetail();
        drawBoard();
    }

    // --- Fighting ---------------------------------------------------------------------------

    function fightControls(unit) {
        const options = state.fight;
        if (options.reason) return `<p class="play-hint">${escapeHtml(options.reason)}</p>`;
        const targetSelect = part => `<select class="filter-select" data-fight-target="${part}">
            ${options.targets.map(id => `<option value="${id}" ${state.fightTarget[part] === id ? 'selected' : ''}>${escapeHtml(unitById(id).name)}</option>`).join('')}
        </select>`;
        const rows = options.parts.map(part => {
            const plain = part.weapons.filter(w => !w.extra);
            const extra = part.weapons.filter(w => w.extra);
            return `<div class="play-weapon"><strong>${escapeHtml(part.name)}</strong>
                ${plain.length ? `<select class="filter-select" data-fight-weapon="${part.part}">
                    ${plain.map(w => `<option value="${w.weapon}" ${state.fightWeapon[part.part] === w.weapon ? 'selected' : ''}>${escapeHtml(w.name)}</option>`).join('')}
                </select>` : ''}
                ${extra.length ? `<small>+ ${extra.map(w => escapeHtml(w.name)).join(', ')} (Extra Attacks)</small>` : ''}
                ${targetSelect(part.part)}</div>`;
        }).join('');
        return `<div class="play-shooting">${rows}
            <button class="btn btn-primary" id="fight-btn"><i class="fa-solid fa-hand-fist" aria-hidden="true"></i> Fight</button></div>`;
    }

    async function fightSelected() {
        const options = state.fight;
        const weapons = options.parts.filter(p => state.fightWeapon[p.part] != null)
            .map(p => ({ part: p.part, weapon: state.fightWeapon[p.part], target: state.fightTarget[p.part] }));
        const name = unitById(state.selected).name;
        if (await send({ type: 'fight', unit: state.selected, weapons, target: options.targets[0] })) {
            const event = state.game.events[state.game.events.length - 1];
            const killed = event.attacks.reduce((n, a) => n + a.killed.length, 0);
            message(`${name} fought: ${killed} model${killed === 1 ? '' : 's'} killed.`);
            clearOptions();
            loadOptions();
        }
    }

    // --- Shooting -----------------------------------------------------------------------


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
            clearOptions();
            loadOptions();
        }
    }

    // --- Dice log -------------------------------------------------------------------------

    function renderLog() {
        const game = state.game;
        const ONE_LINE = ['roll_mirrored', 'roll_objectives', 'roll_off', 'choose_role', 'choose_secondaries', 'condemn', 'start_action', 'place_markers', 'tactical', 'beacon', 'tempting', 'charge', 'advance', 'stratagem', 'rule', 'effect', 'correction', 'accept', 'decline'];
        const shots = game ? game.events.map((e, i) => [e, i])
            .filter(([e]) => (e.attacks && e.attacks.length) || ONE_LINE.includes(e.action.type)
                || (['next_phase', 'end_deployment'].includes(e.action.type)
                    && (e.rolls.length || (e.vp && e.vp.length) || (e.actions && e.actions.length) || (e.markers_removed && e.markers_removed.length)
                        || e.drawn || (e.discards && e.discards.length)))).reverse() : [];
        if (!shots.length) {
            $('dice-log').innerHTML = '<p class="empty-message">No dice rolled yet</p>';
            return;
        }
        const name = id => (game.state.units.find(u => u.id === id) || { name: id }).name;
        $('dice-log').innerHTML = shots.map(([event, i], n) => {
            if (!event.attacks) return `<div class="play-log-entry play-log-roll">${logLine(event, name)}</div>`;
            const targets = [...new Set(event.attacks.filter(a => a.name !== 'Hazardous').map(a => name(a.target)))];
            return `<details class="play-log-entry" ${n === 0 ? 'open' : ''}>
                <summary>${escapeHtml(name(event.action.unit))} ${event.action.type === 'fight' ? 'fights' : 'shoots'} ${escapeHtml(targets.join(', '))}</summary>
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

    function actionName(key) {
        return { consecrate: 'Consecrate', 'booby-trap': 'Booby Trap', decoy: 'Decoy', 'sensor-sweep': 'Sensor Sweep',
                 triangulate: 'Triangulate', surveil: 'Surveil the Foe', 'extract-intelligence': 'Extract Intelligence',
                 'secure-asset': 'Secure Asset', 'maintain-control': 'Maintain Control', 'vanguard-operation': 'Vanguard Operation',
                 sabotage: 'Sabotage' }[key] || key;
    }

    /** Markers an event removed, for its log line. */
    function markersRemoved(event) {
        return (event.markers_removed || []).map(r => `<br>Removed ${r.markers.map(m =>
            `${escapeHtml(state.game.state.players[m.player].name)}'s ${escapeHtml(Core.playMarker(m.tag).label.toLowerCase())}`).join(', ')} (${escapeHtml(r.why)})`).join('');
    }

    /** One line of the dice log for an event that isn't an attack. */
    function logLine(event, name) {
        const a = event.action;
        const dice = roll => roll.dice.map(d => `<span class="play-die ${roll.need == null ? 'value' : d.success ? 'success' : 'fail'}">${d.face}</span>`).join('');
        const player = p => escapeHtml(state.game.state.players[p].name);
        switch (a.type) {
            case 'charge':
            case 'advance':
                return `${escapeHtml(name(a.unit))} ${a.type === 'charge' ? 'charge' : 'Advance'} roll: ${dice(event.rolls[0])} = <strong>${event.rolls[0].total}"</strong>`;
            case 'stratagem':
                return `<span class="play-effect stratagem">${player(event.player)} used ${escapeHtml(event.stratagem)} (${event.cp} CP)</span> on ${escapeHtml(name(event.unit))}`;
            case 'rule':
                return `<span class="play-effect rule">${player(event.player)}: ${escapeHtml(event.rule)}</span> on ${escapeHtml(name(event.unit))}`;
            case 'effect':
                return `<span class="play-effect manual">Manual effect: ${escapeHtml(event.effect)}</span> on ${escapeHtml(name(event.unit))}`;
            case 'roll_objectives':
                return `Central objective roll: ${dice(event.rolls[0])}: <strong>${event.central.objectives === 2 ? 'two central objectives' : 'one central objective'}</strong>`;
            case 'roll_off':
                return `Roll-off for attacker or defender: ${escapeHtml(Core.playRollOff(event.roll_off, state.game.state.players))}`;
            case 'choose_secondaries': {
                const options = Object.fromEntries(((state.game.mission || {}).secondary_options || []).map(c => [c.key, c.name]));
                const revealed = event.revealed ? ' · revealed: ' + Object.entries(event.revealed).map(([p, s]) =>
                    `${state.game.state.players[p].name}: ${s.mode === 'tactical' ? 'Tactical' : s.cards.map(k => options[k] || k).join(', ')}`).join(' · ') : '';
                return `<strong>${player(a.player)}</strong> chose their secondary missions${escapeHtml(revealed)}`;
            }
            case 'start_action': {
                const s = event.action_started;
                return `${escapeHtml(name(s.unit))} started <strong>${escapeHtml(actionName(s.action))}</strong> on ${escapeHtml(s.target)}${event.action_completed ? ': done' : ''}`
                    + markersRemoved(event);
            }
            case 'roll_mirrored':
                return `Mirrored World roll: ${event.mirrored.rolls.join(', then ')}: both players play <strong>${escapeHtml(
                    (state.twists.mirrored.find(m => m.key === event.mirrored.primary) || { name: event.mirrored.primary }).name)}</strong>`;
            case 'tactical': {
                const what = { hold: 'is holding', unhold: 'let go of', discard: 'will discard', keep: 'will keep',
                               redraw: 'redrew', redraw_cp: 'paid 1CP to discard' }[event.op] || event.op;
                return `<strong>${player(event.player)}</strong> ${what} ${escapeHtml(event.card)}${event.drew ? `, drawing ${escapeHtml(event.drew)}` : ''}`;
            }
            case 'beacon':
                return `<strong>${player(event.player)}</strong>'s beacon unit: ${escapeHtml(name(event.beacon))}`;
            case 'tempting':
                return `<strong>${player(event.player)}</strong> chose ${escapeHtml(state.game.state.players[event.for].name)}'s tempting target: ${escapeHtml(event.tempting)}`;
            case 'place_markers':
                return `<strong>${player(event.markers_placed.player)}</strong> placed Locate and Deny markers in ${escapeHtml(event.markers_placed.terrain.join(', '))}`;
            case 'condemn':
                return `<strong>${player(state.game.state.turn.player)}</strong> condemned ${escapeHtml(a.units.map(name).join(', '))} (Punishment)`;
            case 'choose_role':
                return `<strong>${player(event.chose.player)}</strong> chose to be the ${event.chose.role}`;
            case 'correction':
                return `${player(a.player)} proposed a correction: ${escapeHtml(a.note || '')}`;
            case 'accept':
            case 'decline':
                return `Correction ${a.type === 'accept' ? 'accepted' : 'declined'}: ${escapeHtml(event.corrected || event.declined || '')}`;
            default: {  // A phase change with battle-shock or hazard rolls, unit by unit
                const lines = [];
                for (const b of event.battle_shock || []) {
                    const outcome = b.shocked ? '<strong class="play-shock-bad">battle-shocked</strong>'
                        : b.was_shocked ? '<strong class="play-shock-good">recovers</strong>' : '<strong class="play-shock-good">holds</strong>';
                    lines.push(`<strong>${escapeHtml(b.name)}</strong> battle-shock test: ${dice(event.rolls[b.rolls[0]])}
                        = ${b.total} (needs ${b.leadership}+): ${outcome}`);
                }
                for (const h of event.hazards || []) {
                    const hurt = h.mortal_wounds ? `${h.mortal_wounds} mortal wound${h.mortal_wounds === 1 ? '' : 's'}` : 'no harm';
                    const dead = h.killed.length ? `, ${h.killed.length} model${h.killed.length === 1 ? '' : 's'} lost` : '';
                    lines.push(`<strong>${escapeHtml(h.name)}</strong> ${escapeHtml(h.reason)} hazard rolls: ${dice(event.rolls[h.rolls[0]])}: ${hurt}${dead}`);
                }
                if (event.drawn) {
                    lines.push(`<strong>${escapeHtml(state.game.state.players[event.drawn.player].name)}</strong> drew ${escapeHtml(event.drawn.cards.join(' and ') || 'nothing')}`);
                }
                for (const d of event.discards || []) {
                    lines.push(`<strong>${escapeHtml(state.game.state.players[d.player].name)}</strong> discarded ${escapeHtml(d.cards.join(', '))} for ${d.cp}CP`);
                }
                for (const a of event.actions || []) {
                    lines.push(`${escapeHtml(name(a.unit))}: <strong>${escapeHtml(actionName(a.action))}</strong> ${escapeHtml(a.result)} (${escapeHtml(a.target)})`);
                }
                if (event.markers_removed && event.markers_removed.length) lines.push(markersRemoved(event).replace(/^<br>/, ''));
                for (const v of event.vp || []) {
                    lines.push(`<span class="play-vp-line">${escapeHtml(Core.playVpLine(v, state.game.state.players))}</span>`);
                }
                if (event.first_turn) {
                    lines.unshift(`First turn roll-off: ${escapeHtml(Core.playRollOff(event.first_turn, state.game.state.players))} and goes first`);
                }
                if (lines.length) return lines.map(l => `<div>${l}</div>`).join('');
                return event.rolls.map(r => r.purpose === 'battle-shock'  // Games saved before units were recorded
                    ? `Battle-shock roll: ${dice(r)} = ${r.total}` : `Hazard rolls: ${dice(r)}`).join('<br>');
            }
        }
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
        const board = game ? game.board : { width: 60, depth: 44 };
        const W = board.width, D = board.depth;
        svgEl('rect', { x: 0, y: 0, width: W, height: D, class: 'play-field' }, svg);
        // Each player's deployment zone, as the battlefield draws it (SVG y runs down)
        (game ? game.zones : []).forEach((zone, p) => svgEl('polygon', {
            points: zone.map(([x, y]) => `${x},${D - y}`).join(' '), class: `play-zone ${PLAYER_CLASSES[p]}` }, svg));
        const grid = svgEl('g', { class: 'play-grid' }, svg);
        for (let x = 1; x < W; x++) svgEl('line', { x1: x, y1: 0, x2: x, y2: D, class: x % 6 ? '' : 'major' }, grid);
        for (let y = 1; y < D; y++) svgEl('line', { x1: 0, y1: y, x2: W, y2: y, class: y % 6 ? '' : 'major' }, grid);
        if (!game) return;
        const divider = game.state.battlefield && game.state.battlefield.divider;
        if (divider) {  // The line between the two territories
            svgEl('line', { x1: divider[0][0], y1: D - divider[0][1], x2: divider[1][0], y2: D - divider[1][1], class: 'play-divider' }, svg);
        }
        drawTerrain(svg, D);
        drawObjectives(svg, D);
        drawShotLines(svg, D);

        for (const unit of game.state.units) {
            const placed = unit.models.filter(m => m.alive && m.position);
            if (!placed.length) continue;
            const targeted = (game.state.turn.phase === 'shooting'
                && Object.entries(state.targets).some(([key, id]) => id === unit.id && state.fire[key]))
                || (game.state.turn.phase === 'charge' && state.charge && state.chargeTargets.includes(unit.id));
            const shocked = unit.status && unit.status.shocked ? 'shocked' : '';
            const condemned = game.mission && Object.values(game.mission.condemned).some(ids => ids.includes(unit.id)) ? 'condemned' : '';
            const provisional = state.placing && state.placing.unitId === unit.id ? 'provisional' : '';
            const ready = game.ready.includes(unit.id) ? 'ready' : '';
            const g = svgEl('g', { class: `play-unit-group ${PLAYER_CLASSES[unit.player]} ${state.selected === unit.id ? 'selected' : ''} ${targeted ? 'targeted' : ''} ${shocked} ${ready} ${condemned} ${provisional}`,
                                   'data-unit': unit.id }, svg);
            // The unit's outline: around every model's whole base, 0.4" out
            const reach = m => Math.max(game.outlines[unit.id][m.part].length, game.outlines[unit.id][m.part].width) / 2 + 0.4;
            const left = Math.min(...placed.map(m => m.position.x - reach(m)));
            const right = Math.max(...placed.map(m => m.position.x + reach(m)));
            const top = Math.min(...placed.map(m => D - m.position.y - reach(m)));
            const bottom = Math.max(...placed.map(m => D - m.position.y + reach(m)));
            svgEl('rect', { x: left, y: top, width: right - left, height: bottom - top, rx: 0.6, class: 'play-unit-outline' }, g);
            if (unit.activity[startKey()] && allowance(unit, placed[0]) != null) {
                for (const model of placed) {  // Where it started the phase, until moves lock
                    const from = startOf(unit, model);
                    if (from.x === model.position.x && from.y === model.position.y) continue;
                    const o = game.outlines[unit.id][model.part];
                    svgEl('ellipse', { cx: from.x, cy: D - from.y, rx: o.length / 2, ry: o.width / 2, class: 'play-ghost' }, g);
                    if (game.state.turn.phase === 'movement' && (unit.activity.via || {})[model.id]) {  // Its route by way of waypoints
                        svgEl('polyline', { points: pathOf(unit, model).map(p => `${p.x},${D - p.y}`).join(' '), class: 'play-path' }, g);
                    }
                }
            }
            if (state.selected === unit.id) {
                for (const model of placed) {
                    const reach = allowance(unit, model);
                    if (reach == null) continue;
                    const o = game.outlines[unit.id][model.part];
                    // How far it can go: from where it started, or (Waypoint) what's left of its Move from here
                    const onward = waypointing(unit);
                    const from = onward ? model.position : startOf(unit, model);
                    const left = onward ? Math.max(0, reach - Core.playPathLength(pathOf(unit, model))) : reach;
                    svgEl('circle', { cx: from.x, cy: D - from.y, r: left + Math.max(o.length, o.width) / 2,
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
        drawMarkers(svg, D);  // Operation markers above the models, so a unit standing on one doesn't hide it
    }

    /** Terrain areas: each footprint shaded by category (Exposed, Light, Dense), its height on hover. */
    function drawTerrain(svg, depth) {
        const layer = svgEl('g', { class: 'play-terrain-layer' }, svg);
        for (const piece of state.game.terrain || []) {
            const poly = svgEl('polygon', { points: piece.points.map(([x, y]) => `${x},${depth - y}`).join(' '),
                                            class: `play-terrain ${piece.category}` }, layer);
            const title = svgEl('title', {}, poly);
            title.textContent = `${piece.category[0].toUpperCase() + piece.category.slice(1)} terrain, ${piece.height}" high`;
        }
    }

    /**
     * Objectives under the units: the 40mm marker, a faint ring at its range, coloured by whoever controls it
     * (as settled at the end of the last phase), lettered H (home), C (central) or E (expansion).
     */
    function drawObjectives(svg, depth) {
        const game = state.game;
        const r = game.objective_size.marker_mm / 25.4 / 2;
        const layer = svgEl('g', { class: 'play-objectives' }, svg);
        for (const o of game.state.objectives) {
            const owner = o.controller == null ? 'neutral' : PLAYER_CLASSES[o.controller];
            const g = svgEl('g', { class: `play-objective ${owner}`, transform: `translate(${o.x} ${depth - o.y})` }, layer);
            svgEl('circle', { r: r + game.objective_size.range, class: 'play-objective-range' }, g);
            svgEl('circle', { r, class: 'play-objective-marker' }, g);
            const letter = svgEl('text', { class: 'play-objective-letter', y: 0.02 }, g);
            letter.textContent = { home: 'H', central: 'C', expansion: 'E' }[o.kind] || 'O';
            const oc = game.objective_oc[o.id] || [0, 0];
            const players = game.state.players;
            const title = svgEl('title', {}, g);
            title.textContent = `${o.kind === 'home' ? `${players[o.owner].name}'s home` : o.kind[0].toUpperCase() + o.kind.slice(1)} objective`
                + ` · ${o.controller == null ? 'not controlled' : `held by ${players[o.controller].name}`}`
                + ` · OC in range now: ${players[0].name} ${oc[0]}, ${players[1].name} ${oc[1]}`;
        }
    }

    /** Operation markers: small diamonds in their owner's colour, lettered by what they mark. */
    function drawMarkers(svg, depth) {
        const mission = state.game.mission;
        if (!mission) return;
        const layer = svgEl('g', { class: 'play-markers' }, svg);
        for (const m of mission.markers) {
            const info = Core.playMarker(m.tag);
            const g = svgEl('g', { class: `play-marker ${PLAYER_CLASSES[m.player]}`, transform: `translate(${m.x} ${depth - m.y})` }, layer);
            svgEl('rect', { x: -0.45, y: -0.45, width: 0.9, height: 0.9, transform: 'rotate(45)' }, g);
            const letter = svgEl('text', { y: 0.02 }, g);
            letter.textContent = info.letter;
            const title = svgEl('title', {}, g);
            title.textContent = `${state.game.state.players[m.player].name}: ${info.label}${m.objective ? ` (${m.objective})` : m.terrain ? ` (terrain area ${m.terrain})` : ''}`;
        }
        if (mission.choice && mission.choice.kind === 'locate') {  // Name the areas to choose from
            for (const piece of state.game.terrain || []) {
                const cx = piece.points.reduce((s, p) => s + p[0], 0) / piece.points.length;
                const cy = piece.points.reduce((s, p) => s + p[1], 0) / piece.points.length;
                const label = svgEl('text', { x: cx, y: depth - cy, class: `play-terrain-id ${mission.locate_options.includes(piece.id) ? 'choosable' : ''}` }, layer);
                label.textContent = piece.id;
            }
        }
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
        if (turn.phase === 'charge' && shooter && state.charge && state.charge.unit === shooter.id) {
            state.chargeTargets.forEach(to => lines.push({ from: shooter.id, to, kind: 'planned' }));
        }
        if (turn.phase === 'fight' && turn.step === 'fight' && shooter && state.fight && !state.fight.reason) {
            new Set(Object.values(state.fightTarget)).forEach(to => lines.push({ from: shooter.id, to, kind: 'planned' }));
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
        // The farthest any dragged model has come from where it started the phase, against its allowance
        let label = null;
        for (const id of state.drag.modelIds) {
            const model = unit.models.find(m => m.id === id);
            const reach = allowance(unit, model);
            if (reach == null) continue;
            const onward = waypointing(unit);  // Measured along the path so far, then on from here
            const from = onward ? model.position : startOf(unit, model);
            const before = onward ? Core.playPathLength(pathOf(unit, model)) : 0;
            const here = Core.playMoveLabel(model.position.x + state.drag.dx - from.x, model.position.y + state.drag.dy - from.y, reach, before);
            if (!label || parseFloat(here.text) > parseFloat(label.text) || here.over) label = here;
        }
        showReadout(event, label);
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
            } else if (turn.phase === 'charge' && mine && state.charge && state.charge.targets[unit.id]) {
                toggleChargeTarget(unit.id);
            } else if (turn.phase === 'fight' && turn.step === 'fight' && mine && state.fight && state.fight.targets.includes(unit.id)) {
                state.fight.parts.forEach(p => { state.fightTarget[p.part] = unit.id; });
                renderDetail();
                drawBoard();
            } else if (state.selected !== unit.id) {
                select(unit.id);
            }
            return;
        }
        showReadout(null, null);
        const round = v => Math.round(v * 100) / 100;
        const moved = new Set(drag.modelIds);
        const turn = state.game.state.turn;
        if (turn.phase !== 'deployment') {
            const models = unit.models.filter(m => m.alive).map(m => ({
                id: m.id, x: round(m.position.x + (moved.has(m.id) ? drag.dx : 0)),
                y: round(m.position.y + (moved.has(m.id) ? drag.dy : 0)), angle: m.position.angle || 0,
            }));
            if (turn.phase === 'movement') {
                const mode = state.selected === unit.id ? state.moveMode : 'normal';
                await send(Object.assign({ type: 'move', unit: unit.id, mode, models }, waypointing(unit) ? { waypoint: true } : {}));
            } else if (turn.phase === 'charge') {
                const targets = state.charge && state.charge.unit === unit.id ? state.chargeTargets : [];
                if (await send({ type: 'charge_move', unit: unit.id, targets, models })) loadOptions();
            } else if (turn.phase === 'fight' && (turn.step === 'pile_in' || turn.step === 'consolidate')) {
                await send({ type: turn.step, unit: unit.id, models });
            } else {
                message('Models move in the Movement and Charge phases and the Fight phase\'s Pile in and Consolidate steps.', true);
                render();
            }
            return;
        }
        startPlacing(unit, unit.models.filter(m => m.alive && m.position).map(m => ({
            id: m.id, angle: m.position.angle || 0,
            x: round(m.position.x + (moved.has(m.id) ? drag.dx : 0)), y: round(m.position.y + (moved.has(m.id) ? drag.dy : 0)),
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
