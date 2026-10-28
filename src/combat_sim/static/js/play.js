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
    const PHASE_NAMES = { setup: 'Setup', deployment: 'Deployment', prebattle: 'Pre-battle', command: 'Command', movement: 'Movement', shooting: 'Shooting',
                          charge: 'Charge', fight: 'Fight' };
    const MOVE_MODES = [['normal', 'Normal move'], ['advance', 'Advance'], ['fall_back', 'Fall Back']];
    const DONE = { normal: 'moved', advance: 'advanced', fall_back: 'fell back', stationary: 'remained stationary', ingress: 'arrived',
                   disembark: 'disembarked' };
    const DISEMBARK_NAMES = { rapid: 'Rapid disembark', tactical: 'Tactical disembark', combat: 'Combat disembark' };
    const STEP_NAMES = { pile_in: 'Pile in', fight: 'Fight', consolidate: 'Consolidate' };
    const SHORT_MOVE = 3;  // Pile-in and consolidation moves, as the server sends them in errors
    const SKIES = 2;  // Inches off a move or charge when a Fly unit takes to the skies (game.SKIES)
    const OPEN = 'open';  // New-game form: this army is the other player's, chosen when they join online

    const state = {
        game: null,  // /api/play/games/<id> answer
        working: false,  // A request that changes the game hasn't answered yet (see whileWorking)
        selected: null,  // Unit id picked in the list
        view: null,  // The board's viewBox, in SVG units (inches, y down)
        drag: null,  // {unitId, modelIds, start, dx, dy} while dragging
        pan: null,  // {x, y, view, click, point, moved} while panning the camera
        turning: null,  // A Movement-phase turn not sent yet: {unitId, original, degrees, spins, timer}
        rerolling: null,  // The player picking a die of the last roll to Command Re-roll
        measure: null,  // The Measure tool while it's on: {from, to (ends: a point, or a model's base), distance, fixed}
        moveMode: 'normal',  // How the selected unit will move (Movement phase)
        skies: false,  // The selected Fly unit takes to the skies for its move or charge (2" less, over terrain and models)
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
        $('game-import').addEventListener('change', importGameFile);
        $('join-btn').addEventListener('click', () => joinGame());  // Not joinGame itself: the click would arrive as `army`
        $('join-code').addEventListener('keydown', e => { if (e.key === 'Enter') joinGame(); });
        document.addEventListener('visibilitychange', () => { if (!document.hidden) syncGame(); });
        $('undo-btn').addEventListener('click', undo);
        $('fit-btn').addEventListener('click', () => { fitView(); drawBoard(); });
        $('measure-btn').addEventListener('click', () => toggleMeasure());
        $('phase-btn').addEventListener('click', nextPhase);
        $('correct-btn').addEventListener('click', () => { state.correcting = !state.correcting; renderCorrection(); });
        const board = $('play-board');
        board.addEventListener('wheel', zoom, { passive: false });
        board.addEventListener('pointerdown', pointerDown);
        board.addEventListener('pointermove', pointerMove);
        board.addEventListener('pointerup', pointerUp);
        board.addEventListener('contextmenu', e => e.preventDefault());  // Right-drag pans
        board.addEventListener('pointerover', hoverModel);
        board.addEventListener('pointerout', e => { if (!e.relatedTarget || !e.relatedTarget.closest('[data-model]')) hideCard(); });
        board.addEventListener('mousedown', e => { if (e.button === 1) e.preventDefault(); });  // No auto-scroll on middle-drag
        document.addEventListener('battlefields-changed', e => fillBattlefields(e.detail));
        document.addEventListener('keydown', e => {
            const open = [...document.querySelectorAll('.modal.show')];
            if (e.key === 'Escape' && open.length) {  // Escape closes the pop-up on top
                closeModal((open.find(m => m.id === 'battlefields-modal') || open[0]).id);
                return;
            }
            if (e.key === 'Escape' && state.measure && !open.length) {
                toggleMeasure(false);
                return;
            }
            const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName);
            const mover = state.selected && unitById(state.selected);
            if (!open.length && !typing && !state.placing && mover && canTurn(mover) && ['q', 'e'].includes(e.key.toLowerCase())) {
                turnUnit(mover, e.key.toLowerCase() === 'q' ? 15 : -15);
                e.preventDefault();
                return;
            }
            if (open.length || !state.placing || state.working || typing) return;
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
        state.armies = armies;
        const options = armies.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('')
            + `<option value="${OPEN}">The other player joins online with theirs</option>`;
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
        const missions = !!(state.matchup && (state.matchup.primaries || state.matchup.open));  // Open: decided when they join
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
        const open = [red, blue].indexOf(OPEN);
        $('new-seat').disabled = open >= 0;
        if (open >= 0) {  // This PC plays the army it has; the pairing's layout and twists wait for theirs
            $('new-seat').value = String(1 - open);
            state.matchup = { open: true, layouts: [] };
            $('new-matchup').textContent = red === blue ? 'Choose your own army for one side.'
                : 'The game goes online with an invite code: the other player joins with their army, and choices that need both armies are made then.';
            fillBattlefields(state.battlefields || [], true);
            renderTwistChoice();
            return;
        }
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
        const pairing = state.matchup && state.matchup.open ? `<optgroup label="Their pairing (Event Companion, once they join)">
            <option value="pairing:random">Random layout (A, B or C)</option>
            ${['A', 'B', 'C'].map(letter => `<option value="pairing:${letter}">Layout ${letter}</option>`).join('')}</optgroup>`
            : layouts.length ? `<optgroup label="This pairing (Event Companion)">
            <option value="pairing:random">Random layout (A, B or C)</option>
            ${layouts.map(b => `<option value="pairing:${b.layout}">${escapeHtml(b.name)}</option>`).join('')}</optgroup>` : '';
        select.innerHTML = pairing + Core.playBattlefieldGroups(list).map(g => `<optgroup label="${escapeHtml(g.label)}">${g.items.map(b =>
            `<option value="${b.key}">${escapeHtml(g.kind === 'layout' ? b.name : b.label)}${g.kind === 'saved' && b.terrain ? ` (${b.terrain} terrain)` : ''}</option>`).join('')}</optgroup>`).join('');
        if (matchupChanged && (layouts.length || (state.matchup && state.matchup.open))) select.value = 'pairing:random';
        else if ([...select.options].some(o => o.value === current)) select.value = current;
    }

    async function loadGames() {
        const games = await fetch('/api/play/games').then(r => r.json()).catch(() => []);
        $('game-list').innerHTML = games.length ? games.map(g => `
            <div class="play-game-row ${state.game && state.game.id === g.id ? 'active' : ''}">
                <button class="play-game-open" data-id="${g.id}">${escapeHtml(g.name)}
                    <small>${escapeHtml(g.players.join(' vs '))}${g.seat == null ? '' : ` · you play ${escapeHtml(g.players[g.seat])}`}</small></button>
                <button class="btn-icon" data-delete="${g.id}" title="Delete game" aria-label="Delete ${escapeHtml(g.name)}">
                    <i class="fa-solid fa-trash" aria-hidden="true"></i></button>
            </div>`).join('') : '<p class="empty-message">No games yet</p>';
        $('game-list').querySelectorAll('[data-id]').forEach(b => b.addEventListener('click', () => openGame(b.dataset.id)));
        $('game-list').querySelectorAll('[data-delete]').forEach(b => b.addEventListener('click', () => deleteGame(b.dataset.delete)));
    }

    async function newGame() {
        const twist = $('new-twist').value;
        const body = { armies: [$('new-red').value, $('new-blue').value].map(v => v === OPEN ? null : Number(v)), name: $('new-name').value,
                       battlefield: $('new-battlefield').value || null, seat: $('new-seat').value === '' ? null : Number($('new-seat').value),
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

    /**
     * Open a game file the other player sent: a new game, or this one brought up to date. A file from a
     * hot-seat game asks which side this PC plays; copies that went different ways ask before replacing.
     */
    async function importGameFile(e) {
        const input = e.target;
        const chosen = input.files[0];
        input.value = '';
        if (!chosen) return;
        const hint = $('game-import-hint');
        let file;
        try {
            file = JSON.parse(await chosen.text());
        } catch (error) {
            hint.textContent = 'That is not a game file.';
            return;
        }
        const post = async extra => {
            const resp = await fetch('/api/play/games/import', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file, ...extra }),
            });
            return { resp, data: await resp.json() };
        };
        let { resp, data } = await post({});
        if (resp.status === 409 && data.conflict) {
            if (!confirm(`${data.error}\n\nReplace your copy with the file's? Your moves that aren't in it will be lost.`)) {
                hint.textContent = 'Kept your copy.';
                return;
            }
            ({ resp, data } = await post({ replace: true }));
        }
        if (!resp.ok && data.players) {  // A hot-seat game's file: choose a side
            hint.innerHTML = `${escapeHtml(data.error)} ${data.players.map((name, i) =>
                `<button class="btn-small" data-seat="${i}">Play ${escapeHtml(name)}</button>`).join(' ')}
                <button class="btn-small" data-seat="">Both</button>`;
            hint.querySelectorAll('[data-seat]').forEach(b => b.addEventListener('click', async () => {
                const answer = await post({ seat: b.dataset.seat === '' ? null : Number(b.dataset.seat) });
                if (answer.resp.ok) opened(answer.data);
                else hint.textContent = answer.data.error;
            }));
            return;
        }
        if (!resp.ok) {
            hint.textContent = data.error || 'The game file could not be opened.';
            return;
        }
        opened(data);

        async function opened(game) {
            hint.textContent = '';
            closeModal('games-modal');
            show(game);
            message({ new: 'Game opened.', newer: 'Game brought up to date.', same: 'You already had every move in that file.',
                      older: 'Your copy is ahead of that file: nothing changed.', replaced: "Your copy now matches the file's." }[game.imported] || '');
            await loadGames();
        }
    }

    /** Join an online game with the invite code the other player sent. */
    async function joinGame(army = null) {
        const code = $('join-code').value.trim();
        if (!code) return;
        $('join-btn').disabled = true;
        try {
            const resp = await fetch('/api/play/games/join', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(army == null ? { code } : { code, army }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                $('join-hint').textContent = data.error || 'Could not join that game.';
                return;
            }
            if (data.needs_army) return askJoinArmy(data);
            $('join-code').value = '';
            $('join-hint').textContent = '';
            closeModal('games-modal');
            show(data);
            message('Joined: the other player sees you have.');
            await loadGames();
        } finally {
            $('join-btn').disabled = false;
        }
    }

    /** A game that waits for your army: whose it is and what it will be, and which of your saved armies to bring. */
    function askJoinArmy(invite) {
        if (!(state.armies || []).length) {
            $('join-hint').textContent = 'Save an army on the Armies page first, then join.';
            return;
        }
        $('join-hint').innerHTML = `${escapeHtml(Core.playInviteText(invite))} Choose your army:
            <span class="play-join"><select class="filter-select" id="join-army">${state.armies.map(a =>
                `<option value="${Number(a.id)}">${escapeHtml(a.name)}</option>`).join('')}</select>
            <button class="btn-small" id="join-army-btn">Join with this army</button></span>`;
        $('join-army-btn').addEventListener('click', () => joinGame(Number($('join-army').value)));
    }

    /** Put the open game on the relay: the seat bar then shows the invite code to send. */
    async function goOnline() {
        await whileWorking(async () => {
            const resp = await fetch(`/api/play/games/${state.game.id}/online`, { method: 'POST' });
            const data = await resp.json();
            if (!resp.ok) return message(data.error || 'The game could not go online.', true);
            show(data);
            message('Online: send the other player the invite code.');
        });
    }

    /** Move the open online game to a new invite code: whoever has the old one is cut off, and the other player
     * joins again with the new one. */
    async function newInviteCode() {
        const game = state.game || state.waiting;
        const other = game.state ? `${game.state.players[1 - game.seat].name}'s player` : 'The other player';
        if (!confirm(`Make a new invite code? The old one stops working: ${other} joins with the new code.`)) return;
        await whileWorking(async () => {
            const resp = await fetch(`/api/play/games/${game.id}/new-code`, { method: 'POST' });
            const data = await resp.json();
            if (!resp.ok) return message(data.error || 'Could not make a new code.', true);
            show(data);
            message('New invite code made: send it to the other player.');
        });
    }

    /**
     * An online game checks the relay for the other player's moves: every few seconds while it waits for
     * them, less often on your move, never while the page is hidden or busy.
     */
    const SYNC_WAITING = 5000, SYNC_MINE = 20000;  // Milliseconds

    function scheduleSync() {
        clearTimeout(state.syncTimer);
        const game = state.game || state.waiting;
        if (!game || !game.online || !(game.waiting_for_army || (game.waiting_on || []).length)) return;
        const mine = !game.waiting_for_army && game.online.joined && game.waiting_on.includes(game.seat);
        state.syncTimer = setTimeout(syncGame, mine ? SYNC_MINE : SYNC_WAITING);
    }

    async function syncGame() {
        const game = state.game || state.waiting;
        if (!game || !game.online) return;
        if (document.hidden || state.working || state.drag || state.placing || state.arriving) return scheduleSync();
        const resp = await fetch(`/api/play/games/${game.id}/sync`, { method: 'POST' }).catch(() => null);
        const data = resp && await resp.json().catch(() => null);
        const now = state.game || state.waiting;
        if (!now || now.id !== game.id) return;
        if (!resp || !resp.ok) {
            message((data && data.error) || "Couldn't reach the app: is it still running?", true);
            if (resp && resp.status === 410) return;  // Moved to a new invite code: nothing more comes from this one
            return scheduleSync();
        }
        const joinedNow = data.online && data.online.joined && !game.online.joined;
        if (game.waiting_for_army && !data.waiting_for_army) {  // They joined with their army: the game begins
            show(data);
            message(`The other player joined with ${data.state.players[1 - data.seat].name}: the game begins.`);
        } else if (data.moved || joinedNow) {
            show(data);
            const other = data.state.players[1 - data.seat].name;
            message(joinedNow && !data.moved ? `${other} has joined.` : `${other} moved.`);
        } else {
            scheduleSync();
        }
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
        if (game.waiting_for_army) return showWaiting(game);
        state.waiting = null;
        const first = !state.game || state.game.id !== game.id;
        state.game = game;
        state.placing = null;  // The server's game replaces anything placed provisionally
        state.arriving = null;
        if (first) {
            state.selected = null;
            state.logSeen = game.events.length;  // Rolls already made don't animate when a game opens
            fitView();
            history.replaceState(null, '', `/play?game=${game.id}`);
        }
        render();
    }

    /** A game that waits for the other player to join with their army: the invite code, and what they'll play. */
    function showWaiting(game) {
        state.game = null;
        state.waiting = game;
        state.selected = null;
        history.replaceState(null, '', `/play?game=${game.id}`);
        render();
        $('game-title').textContent = game.name;
        const notice = Core.playWaitingNotice(game);
        $('seat-bar').hidden = false;
        $('seat-bar').classList.remove('play-seat-mine');
        $('seat-bar').innerHTML = `<strong>${escapeHtml(notice.text)}</strong>${notice.detail ? `<small>${escapeHtml(notice.detail)}</small>` : ''}
            <span class="play-pending-actions">
                <button class="btn-small" id="seat-copy"><i class="fa-solid fa-copy" aria-hidden="true"></i> Copy code</button>
                <button class="btn-small" id="seat-new-code" title="If someone else may have used the code: the old one stops working">
                    <i class="fa-solid fa-arrows-rotate" aria-hidden="true"></i> New invite code</button></span>`;
        $('seat-copy').addEventListener('click', () => navigator.clipboard.writeText(Core.playInviteCode(game.online.code))
            .then(() => message('Invite code copied.'), () => message('Copy the code by hand.', true)));
        $('seat-new-code').addEventListener('click', newInviteCode);
        scheduleSync();
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
                if (data.state) show(data);  // Online: the other player moved first, and the board now has their moves
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

    /** Hot-seat: the last action, unless dice were rolled. Online: your own last action, dice or not, with their OK. */
    function canUndo(game) {
        const last = game && game.events.length && game.events[game.events.length - 1];
        if (!last || (game.state && game.state.pending)) return false;
        if (!game.online) return last.rolls.length === 0;
        return !['correction', 'accept', 'decline'].includes(last.action.type) && (last.by == null || last.by === game.seat);
    }

    async function undo() {
        await whileWorking(async () => {
            const resp = await fetch(`/api/play/games/${state.game.id}/undo`, { method: 'POST' });
            const data = await resp.json();
            if (!resp.ok) {
                if (data.state) show(data);
                return message(data.error, true);
            }
            show(data);
            const other = data.state && data.state.pending ? data.state.players[1 - data.state.pending.player].name : null;
            message(other ? `Asked ${other} to accept taking it back.` : 'Undone.');
        });
    }

    function place(unitId, positions) {
        return send({ type: 'place', unit: unitId, models: positions });
    }

    async function nextPhase() {
        const turn = state.game.state.turn;
        if (await send({ type: turn.phase === 'deployment' ? 'end_deployment' : 'next_phase' })) {
            state.moveMode = 'normal';
            state.skies = false;
            clearOptions();
            loadOptions();
            const now = state.game.state.turn;
            message(now.phase === 'prebattle' ? `Pre-battle abilities: ${state.game.state.players[now.player].name}'s Scouts.`
                : `${state.game.state.players[now.player].name}: ${PHASE_NAMES[now.phase]} phase${now.step ? ' · ' + STEP_NAMES[now.step] : ''}.`);
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
        if ((state.placing && state.placing.unitId === unit.id) || unit.activity.moved === 'ingress') return null;  // Set up, not moved
        if (turn.phase === 'prebattle') return (state.game.scouts || {})[unit.id] ?? null;
        const skies = flying(unit) ? SKIES : 0;
        if (turn.phase === 'movement' && unit.player === turn.player) {
            const roll = state.moveMode === 'advance' ? (unit.activity.advance_roll || 0) : 0;
            return state.game.moves[unit.id][model.part] + roll - skies;
        }
        if (charging(unit)) return unit.activity.charge_roll == null ? null : unit.activity.charge_roll - skies;
        if (turn.phase === 'fight' && ['pile_in', 'fight', 'consolidate'].includes(turn.step)) return SHORT_MOVE;  // Fight: an overrun pile-in
        return null;
    }

    /** Whether a unit is taking to the skies for the move it's about to make (only the selected Fly unit can). */
    function flying(unit) {
        return state.skies && state.selected === unit.id && state.game.fly.includes(unit.id);
    }

    /** The Take to the skies switch, for a unit with Fly models. */
    function skiesButton(unit) {
        if (!state.game.fly.includes(unit.id)) return '';
        return `<button class="btn-small ${state.skies ? 'active' : ''}" data-skies
            title="Core rules: 2&quot; off this move, but it passes over all terrain and models"><i class="fa-solid fa-plane-up" aria-hidden="true"></i>
            Take to the skies (-2&quot;)</button>`;
    }

    /** Where a unit's moves this phase (or step) are measured from, in its activity. */
    function startKey() {
        const turn = state.game.state.turn;
        return { movement: 'start', charge: 'charge_start' }[turn.phase]
            || { pile_in: 'pile_start', fight: 'overrun_start', consolidate: 'consolidate_start' }[turn.step] || 'start';
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

    /** A reserve unit arriving (`state.arriving`: {unitId, kind, player}): in formation, centred on a board point,
     * kept on the board; then OK sends it (the server checks the edges, enemies and zones). */
    function placeArrivalAt(point) {
        const { unitId, kind, player } = state.arriving;
        const unit = unitById(unitId);
        const living = unit.models.filter(m => m.alive);
        const outlines = state.game.outlines[unit.id];
        const sizes = living.map(m => Math.max(outlines[m.part].length, outlines[m.part].width));
        const W = state.game.board.width, D = state.game.board.depth;
        const board = kind === 'scout' ? Core.playBounds(state.game.zones[unit.player]) : { left: 0, bottom: 0, right: W, top: D };
        // Rows run along the nearest battlefield edge, so the unit stays within the band along it
        const sideways = ['ingress', 'rapid_ingress'].includes(kind) && Math.min(point.x, W - point.x) < Math.min(point.y, D - point.y);
        let spots = living.map((m, i) => ({ id: m.id, ...Core.playFormation(sizes, point.x, point.y, false)[i], angle: 0 }));
        if (sideways) spots = Core.playPivot(spots, 90);
        const fitted = Core.playFit(spots, sizes.map(s => s / 2), board);
        state.arriving = null;
        startPlacing(unit, spots.map((s, i) => ({ ...s, x: fitted[i].x, y: fitted[i].y })), { kind, player });
    }

    /** Start bringing a unit in from reserves: the next click on the board sets it up there. */
    function startArriving(unit, kind, player) {
        if (state.placing) return message(`Press OK or Cancel for ${unitById(state.placing.unitId).name} first.`, true);
        state.arriving = { unitId: unit.id, kind, player };
        state.selected = unit.id;
        message(kind === 'disembark' ? `Click the shaded ground next to ${unitById(unit.status.embarked).name} to set ${unit.name} up there, then press OK.`
            : `Click the board where ${unit.name} arrives (the shaded ground), then press OK.`);
        render();
    }

    /** Place the selected unit in formation, centred on a board point. */
    async function placeSelectedAt(point) {
        const unit = unitById(state.selected);
        const living = unit.models.filter(m => m.alive);
        const outlines = state.game.outlines[unit.id];
        const sizes = living.map(m => Math.max(outlines[m.part].length, outlines[m.part].width));
        // Fitted into the box round the player's zone (the server checks it's wholly within the zone itself); Infiltrators anywhere
        const infiltrating = (state.game.infiltrators || []).includes(unit.id) && !Core.playInside(point, state.game.zones[unit.player]);
        const zone = infiltrating ? { left: 0, bottom: 0, right: state.game.board.width, top: state.game.board.depth }
            : Core.playBounds(state.game.zones[unit.player]);
        const topEdge = (zone.bottom + zone.top) / 2 > state.game.board.depth / 2;  // The back row towards their own edge
        const spots = Core.playFit(Core.playFormation(sizes, point.x, point.y, topEdge), sizes.map(s => s / 2), zone);
        const angle = topEdge ? 180 : 0;
        if (infiltrating) message(`Infiltrators: ${unit.name} can be set up anywhere more than 8" from the enemy deployment zone and enemy units (shaded).`);
        startPlacing(unit, living.map((m, i) => ({ id: m.id, x: spots[i].x, y: spots[i].y, angle })));
    }

    // --- Deployment: a unit is placed provisionally, moved and pivoted, then confirmed with OK -------------

    /** Show a unit at these spots ([{id, x, y, angle}]) on the page only, until OK sends it (or Cancel). */
    function startPlacing(unit, spots, arriving = null) {
        if (state.placing && state.placing.unitId !== unit.id) {
            return message(`Press OK or Cancel for ${unitById(state.placing.unitId).name} first.`, true);
        }
        if (!state.placing) {  // Where its models were, to go back to on Cancel; `arriving`: {kind, player} for reserves
            state.placing = { unitId: unit.id, original: Object.fromEntries(unit.models.map(m => [m.id, m.position && { ...m.position }])),
                              ...(arriving || {}) };
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
        const { unitId, kind, player } = state.placing;
        const action = kind ? Object.assign({ type: kind, unit: unitId, models: placingSpots() }, kind === 'rapid_ingress' ? { player } : {}) : null;
        if (await (action ? send(action) : place(unitId, placingSpots()))) {  // The server checks it (zone, edges, enemies, overlaps)
            state.placing = null;
            state.selected = null;
            if (kind) message(`${unitById(unitId).name} ${kind === 'scout' ? 'is set up' : kind === 'disembark' ? 'disembarked' : 'arrived from reserves'}.${
                kind === 'ingress' ? ' Drag it to set it up somewhere else until the phase ends.'
                : kind === 'disembark' && unitById(unitId).activity.disembarked === 'tactical' ? ' Now drag it to move it as usual.' : ''}`);
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
        const placing = state.placing && state.game && (state.game.state.turn.phase === 'deployment' || state.placing.kind) ? state.placing : null;
        bar.hidden = !placing;
        if (!placing) return;
        const pivot = (deg, icon, label) => `<button class="btn-small" data-pivot="${deg}" title="Pivot ${label}">
            <i class="fa-solid fa-${icon}" aria-hidden="true"></i> ${Math.abs(deg)}°</button>`;
        bar.innerHTML = `<span>${placing.kind === 'disembark' ? 'Disembarking' : placing.kind ? 'Arriving' : 'Placing'} <strong>${escapeHtml(unitById(placing.unitId).name)}</strong>:
                drag it or click the board to move it, pivot it (Q / E), then press OK.${placing.kind ? ' It can only be set up on the shaded ground.' : ''}</span>
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
        $('undo-btn').disabled = !canUndo(game);
        $('undo-btn').title = game && game.online ? 'Undo: ask the other player to take back your last action' : 'Undo';
        const turn = game && game.state.turn;
        const allPlaced = game && game.state.units.every(u => (u.status && u.status.reserve) || u.models.every(m => !m.alive || m.position));
        let banner = '';
        const setup = turn && turn.phase === 'setup';
        if (setup) {
            banner = 'Setting up the game';
        } else if (turn && turn.phase === 'prebattle') {
            banner = `Pre-battle · ${game.state.players[turn.player].name}: Scouts (drag a unit up to its Scouts distance, or press Done)`;
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
            if (turn.step === 'pile_in' || turn.step === 'consolidate') banner += `: ${game.state.players[turn.mover ?? turn.player].name} moves`;
        }
        const result = game && Core.playResult(game.state.players, game.score, turn);
        if (result) banner = result;
        $('turn-banner').textContent = banner;
        // The banner says what the board above doesn't: setup, deployment, the result, and who picks in the Fight phase
        $('turn-banner').hidden = !banner || (turn && !result && !['setup', 'deployment', 'prebattle', 'fight'].includes(turn.phase));
        renderHud();
        $('turn-banner').className = `play-turn ${turn ? PLAYER_CLASSES[actingPlayer(turn)] : ''}`;
        $('phase-btn').hidden = !game || turn.phase === 'ended' || setup;
        $('correct-btn').hidden = !game || ['setup', 'deployment', 'prebattle'].includes(turn.phase);
        if (turn) {
            $('phase-btn').querySelector('span').textContent = turn.phase === 'deployment' ? 'Deployment done'
                : turn.phase === 'prebattle' ? `${game.state.players[turn.player].name}: done with Scouts`
                : turn.phase === 'fight' && ['pile_in', 'consolidate'].includes(turn.step) && (turn.mover ?? turn.player) === turn.player
                    ? `${game.state.players[turn.player].name} done`  // The other player moves next, if they can
                : turn.phase === 'fight' ? (turn.step === 'consolidate' ? 'End turn' : 'Next step') : 'Next phase';
        }
        renderPhaseTrack();
        renderArmies();
        renderUnits();
        drawBoard();
        renderLog();
        renderSeat();
        renderPending();
        renderWindow();
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
                <select class="filter-select" id="beacon-unit">${m.beacon_options.map(id => `<option value="${escapeHtml(id)}">${escapeHtml(name(id))}</option>`).join('')}</select>
                <button class="btn-small" id="beacon-btn">Choose</button></div>` : '';
            const tempting = m.choice && m.choice.kind === 'tempting' && m.choice.player === i ? `<div class="play-mission-choice">
                <strong>A Tempting Target:</strong> choose ${escapeHtml(game.state.players[m.choice.for].name)}'s tempting target (an objective in No Man's Land).
                <select class="filter-select" id="tempting-objective">${m.tempting_options.map(id => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join('')}</select>
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
                `<button class="btn-small ${c.hold ? 'active' : ''}" data-card-op="${c.hold ? 'unhold' : 'hold'}" data-card="${escapeHtml(c.short)}" data-player="${i}"
                    title="A held card isn't scored (or discarded) until you let it go">${c.hold ? 'Held' : 'Hold'}</button>`,
                myTurn ? `<button class="btn-small ${c.discard ? 'active' : ''}" data-card-op="${c.discard ? 'keep' : 'discard'}" data-card="${escapeHtml(c.short)}" data-player="${i}"
                    title="Discard it at the end of your turn, for 1CP">${c.discard ? 'Discarding (+1CP)' : 'Discard at end of turn'}</button>` : '',
                command && c.redraw ? `<button class="btn-small" data-card-op="redraw" data-card="${escapeHtml(c.short)}" data-player="${i}" title="${escapeHtml(c.redraw)}">Redraw</button>` : '',
                command && !chosen.redraw_used ? `<button class="btn-small" data-card-op="redraw_cp" data-card="${escapeHtml(c.short)}" data-player="${i}"
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
            <select class="filter-select" data-guard="${escapeHtml(o.id)}" data-player="${i}"><option value="">nobody</option>${units.map(u =>
                `<option value="${escapeHtml(u.id)}" ${chosen.guards[o.id] === u.id ? 'selected' : ''}>${escapeHtml(u.name)}</option>`).join('')}</select></label>`).join('')}</div>`;
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
        const playing = turn && !['setup', 'deployment', 'prebattle'].includes(turn.phase);
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
        if (turn && ['pile_in', 'consolidate'].includes(turn.step)) return turn.mover ?? turn.player;  // Who is moving units
        return turn && (turn.step === 'fight' && turn.fight && turn.fight.picker != null ? turn.fight.picker : turn.player);
    }

    /** Each army panel's header: name, faction and disposition, CP and VP (primary and secondary), lit up for
     * the player acting. */
    function renderArmies() {
        const game = state.game;
        const turn = game && game.state.turn;
        const playing = turn && !['setup', 'deployment', 'prebattle', 'ended'].includes(turn.phase);
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
        track.hidden = !game || ['setup', 'deployment', 'prebattle'].includes(turn.phase);
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
                    </select></label>
                <label class="play-orders" title="At the end of the other player's Movement phase">Fire Overwatch
                    <select data-overwatch="${p}" class="filter-select">${overwatchOrderOptions(player, units)}</select></label>
                <label class="play-orders" title="At the end of the other player's Charge phase">Heroic Intervention
                    <select data-reaction="${p}|heroic" class="filter-select">${reactionOrderOptions(player, 'heroic', [['ask', 'ask me'], ['never', 'never']])}</select></label>
                <label class="play-orders" title="At the end of the other player's Movement phase, from battle round 2">Rapid Ingress
                    <select data-reaction="${p}|ingress" class="filter-select">${reactionOrderOptions(player, 'ingress', [['ask', 'ask me'], ['never', 'never']])}</select></label>
                <label class="play-orders" title="After an attack in which one of your saves failed">Re-roll a failed save
                    <select data-reaction="${p}|reroll" class="filter-select">${reactionOrderOptions(player, 'reroll',
                        [['ask', 'ask me'], ['deaths', 'only when a model dies'], ['never', 'never']])}</select></label>${units.map(u => {
                const living = u.models.filter(m => m.alive);
                const placed = living.length && living.every(m => m.position);
                const reserve = u.status && u.status.reserve;
                const ride = u.status && u.status.embarked ? `embarked in ${unitById(u.status.embarked).name}` : '';
                const aboard = game.state.units.filter(o => o.status && o.status.embarked === u.id).length;
                const done = (game.state.turn.phase === 'deployment' ? (state.placing && state.placing.unitId === u.id ? 'placing: press OK'
                        : ride || (reserve ? 'in reserves' : placed ? 'placed' : 'not placed'))
                    : ride || (reserve ? 'in reserves' : (u.status || {}).lost_in_reserve ? 'lost in reserves'
                        : (u.status || {}).lost_empty ? 'lost (empty transport)' : [DONE[u.activity.moved], u.activity.advance_roll != null ? `Advance roll ${u.activity.advance_roll}` : '',
                       u.activity.shot ? 'shot' : '',
                       u.activity.charge_roll != null ? `charge roll ${u.activity.charge_roll}${u.activity.charged ? ', charged' : ''}` : '',
                       u.activity.fought ? 'fought' : '']
                        .filter(Boolean).join(' · '))) + (aboard ? ` · carrying ${aboard} unit${aboard === 1 ? '' : 's'}` : '');
                const ready = game.ready.includes(u.id);
                const acted = actedThisPhase(u, game.state.turn);
                const tags = Core.playUnitTags(u, { ready, inRange: (game.unit_objectives || {})[u.id], objectives: game.state.objectives, players: game.state.players });
                return `<button class="play-unit ${state.selected === u.id ? 'selected' : ''} ${ready ? 'ready' : ''} ${acted ? 'acted' : ''} ${living.length ? '' : 'destroyed'}" data-unit="${escapeHtml(u.id)}">
                    <span class="play-unit-name">${escapeHtml(u.name)}</span>${tags.length ? `<span class="play-tags">${tags.map(t =>
                        `<span class="play-tag ${t.kind}" title="${escapeHtml(t.title)}">${t.icon ? `<i class="${t.icon}" aria-hidden="true"></i> ` : ''}${escapeHtml(t.label)}</span>`).join('')}</span>` : ''}
                    <small>${living.length} model${living.length === 1 ? '' : 's'}${done ? ' · ' + escapeHtml(done) : ''}</small>
                </button>`;
            }).join('')}</div>`;
            $(`army-units-${p}`).querySelectorAll('[data-unit]').forEach(b => b.addEventListener('click', () => select(b.dataset.unit)));
            $(`army-units-${p}`).querySelectorAll('[data-orders]').forEach(s => s.addEventListener('change', () =>
                send({ type: 'orders', player: Number(s.dataset.orders), casualties: s.value })));
            $(`army-units-${p}`).querySelectorAll('[data-reaction]').forEach(s => s.addEventListener('change', () => {
                const [who, kind] = s.dataset.reaction.split('|');
                send({ type: 'orders', player: Number(who), reactions: { [kind]: { mode: s.value } } });
            }));
            $(`army-units-${p}`).querySelectorAll('[data-overwatch]').forEach(s => s.addEventListener('change', () => {
                const [mode, unit] = s.value.split('|');
                send({ type: 'orders', player: Number(s.dataset.overwatch), reactions: { overwatch: unit ? { mode, unit } : { mode } } });
            }));
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
            state.skies = false;
            const activity = unitById(unitId).activity;
            state.moveMode = activity.advance_roll != null ? 'advance'
                : ['normal', 'fall_back'].includes(activity.moved) ? activity.moved : 'normal';
        }
        const turn = state.game.state.turn;
        if (!state.selected) message('');
        else if (turn.phase === 'prebattle' && state.game.ready.includes(unitId)) message(`Scouts: drag ${unitById(unitId).name}, or press Done.`);
        else if (turn.phase === 'deployment') message(`Click the board to place ${unitById(unitId).name} in its deployment zone, then press OK, or put it in reserves.`);
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

    /** For a unit whose models make more than one allocation group: which group takes wounds first
     * (the defender's choice, kept as a standing order; Characters always come last). */
    function allocationControl(unit) {
        const groups = (state.game.allocation || {})[unit.id];
        if (!groups) return '';
        const order = ((state.game.state.players[unit.player].orders || {}).allocation || {})[unit.id] || [];
        const chosen = Math.max(0, groups.findIndex(g => g.includes(order[0])));
        return `<label class="play-orders">Wounds go first to
            <select class="filter-select" data-allocation>${groups.map((g, i) => `<option value="${i}" ${i === chosen ? 'selected' : ''}>${
                escapeHtml(g.map(p => unit.parts[p].name).join(' and '))}</option>`).join('')}</select></label>`;
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
        const reserve = unit.status && unit.status.reserve;
        const placedNow = unit.models.some(m => m.alive && m.position);
        if (turn.phase === 'deployment' && unit.player === turn.player && !reserve && !placedNow && !state.placing) {
            controls = `<div class="play-move-modes"><button class="btn-small" data-reserve>
                    <i class="fa-solid fa-hourglass-half" aria-hidden="true"></i> Put in reserves</button>${embarkButtons(unit)}</div>
                <p class="play-hint">Strategic Reserves: off the board until it arrives in your Movement phase from battle round 2
                    (within 6" of an edge, or anywhere with Deep Strike; more than 8" from enemies). Up to half your points in all;
                    units still in reserves at the end of battle round 3 are lost.${embarkButtons(unit)
                        ? ' Or it can start embarked in a transport (with it, if that goes into reserves).' : ''}
                    ${unit.parts.some(p => p.keywords.includes('Dedicated Transport'))
                        ? ' A Dedicated Transport must carry a unit when deployment ends, or it is lost.' : ''}</p>`;
        }
        const disembark = (state.game.disembark || {})[unit.id];
        if (turn.phase === 'movement' && unit.player === turn.player && unit.status && unit.status.embarked) {
            const transport = unitById(unit.status.embarked);
            controls = disembark
                ? `<div class="play-move-modes"><button class="btn btn-primary" data-disembark>
                        <i class="fa-solid fa-door-open" aria-hidden="true"></i> ${DISEMBARK_NAMES[disembark.mode]}</button></div>
                    <p class="play-hint">Set it up wholly within ${disembark.reach}" of ${escapeHtml(transport.name)}${disembark.mode === 'combat'
                        ? ' (it may engage enemies the transport is engaged with). Each model makes a hazard roll; the unit is battle-shocked and can\'t charge'
                        : ', not in engagement range of enemies'}.${disembark.mode === 'tactical' ? ' Then move it as usual.'
                        : disembark.mode === 'rapid' ? ' It can\'t move again or charge this turn (the transport has moved).' : ''}</p>`
                : `<p class="play-hint">Embarked in ${escapeHtml(transport.name)}: it can't disembark now (it embarked this phase,
                    or the transport Advanced, Fell Back or isn't on the battlefield).</p>`;
        }
        if (turn.phase === 'prebattle' && unit.player === turn.player && state.game.ready.includes(unit.id)) {
            const reach = (state.game.scouts || {})[unit.id];
            controls = reserve
                ? `<div class="play-move-modes"><button class="btn btn-primary" data-scout-setup>
                        <i class="fa-solid fa-person-running" aria-hidden="true"></i> Set up in your zone</button></div>
                    <p class="play-hint">Scouts ${reach}": a unit in reserves can be set up wholly within your deployment zone now.</p>`
                : `<p class="play-hint">Scouts ${reach}": drag ${escapeHtml(unit.name)} up to ${reach}" (a scout move, along the path dragged),
                    ending more than 8" from every enemy unit. Each unit once; then the other player has a go.</p>`;
        }
        if (turn.phase === 'movement' && unit.player === turn.player && (reserve || unit.activity.moved === 'ingress')) {
            const deep = ((state.game.ingress || [])[unit.player] || { deep_strike: [] }).deep_strike.includes(unit.id);
            const ground = deep ? 'anywhere more than 8" from enemy units (Deep Strike)' : 'wholly within 6" of a battlefield edge, more than 8" from enemy units';
            const zone = turn.round < 3 ? ', and not in the enemy deployment zone before battle round 3' : '';
            controls = unit.activity.moved === 'ingress'
                ? `<div class="play-move-modes"><button class="btn-small" data-reset>Back into reserves</button></div>
                    <p class="play-hint">${escapeHtml(unit.name)} arrived: drag it to set it up somewhere else (${ground}${zone}).
                        It can't move again until the Charge phase.</p>`
                : turn.round < 2 ? '<p class="play-hint">In reserves: units arrive from battle round 2.</p>'
                : `<div class="play-move-modes"><button class="btn btn-primary" data-arrive>
                        <i class="fa-solid fa-parachute-box" aria-hidden="true"></i> Arrive</button></div>
                    <p class="play-hint">An ingress move: set it up ${ground}${zone}. It can't move again until the Charge phase.</p>`;
        } else if (turn.phase === 'movement' && unit.player === turn.player && !(unit.status && unit.status.embarked)) {
            const rolled = unit.activity.advance_roll != null;
            const moved = unit.activity.moved;
            controls = `<div class="play-move-modes">${MOVE_MODES.map(([mode, label]) => `
                <button class="btn-small ${state.moveMode === mode ? 'active' : ''}" data-mode="${mode}"
                    ${rolled && mode !== 'advance' ? 'disabled' : ''}>${label}${mode === 'advance' && rolled ? ` (+${unit.activity.advance_roll}")` : ''}</button>`).join('')}
                <button class="btn-small ${moved === 'stationary' ? 'active' : ''}" data-mode="stationary" ${rolled ? 'disabled' : ''}>Remain stationary</button>
                ${moved && moved !== 'stationary' ? `<button class="btn-small ${state.waypoint ? 'active' : ''}" data-mode="waypoint"
                    title="The next move carries on from here, so the unit can go round terrain"><i class="fa-solid fa-route" aria-hidden="true"></i> Waypoint</button>` : ''}
                ${moved ? '<button class="btn-small" data-mode="reset">Reset move</button>' : ''}
                ${state.moveMode !== 'stationary' ? skiesButton(unit) : ''}
            </div><div class="play-move-modes"><span class="play-turn-label">Turn</span>${[[45, 'rotate-left', 'anticlockwise'],
                [15, 'rotate-left', 'anticlockwise'], [-15, 'rotate-right', 'clockwise'], [-45, 'rotate-right', 'clockwise']].map(([deg, icon, way]) =>
                `<button class="btn-small" data-turn="${deg}" title="Turn the unit ${Math.abs(deg)}° ${way} (Q / E: 15°)">
                    <i class="fa-solid fa-${icon}" aria-hidden="true"></i> ${Math.abs(deg)}°</button>`).join('')}
            </div><p class="play-hint">Drag the unit to make the ${MOVE_MODES.find(m => m[0] === state.moveMode)[1]};
                Shift-drag moves one model. Turning the unit costs each model the distance its centre travels round the turn;
                Shift + mouse wheel over a model spins it on the spot, which is free.
                Climbing Dense terrain over 2" high (twice its height) counts against the move, unless the unit is Infantry,
                Beasts, Swarm or Mobile, or takes to the skies.
                ${state.waypoint ? 'Waypoint: the next drag carries on from where it stands, and the whole path counts.'
                    : 'Each drag is measured along the path you drag, from where the unit started the phase; Waypoint carries on from where it stands instead.'}
                You can move it again or reset it until the Movement phase ends; then moves are locked.</p>
                ${embarkButtons(unit) ? `<div class="play-move-modes">${embarkButtons(unit)}</div>` : ''}`;
        }
        if (turn.phase === 'shooting' && unit.player === turn.player && state.shooting && state.shooting.unit === unit.id) {
            controls = shootingControls(unit);
        }
        if (turn.phase === 'shooting' && state.missionActions && state.missionActions.unit === unit.id) {
            controls = missionActionControls() + controls;
        }
        if (charging(unit) && state.charge && state.charge.unit === unit.id) {
            controls = chargeControls(unit);
        }
        if (turn.phase === 'fight' && turn.step === 'fight' && state.fight && state.fight.unit === unit.id) {
            controls = fightControls(unit);
        }
        if (turn.phase === 'fight' && (turn.step === 'pile_in' || turn.step === 'consolidate')) {
            const verb = turn.step === 'pile_in' ? 'pile in' : 'consolidate';
            const moved = unit.activity[startKey()];
            const how = turn.step === 'pile_in' ? 'each model ending closer to the enemy, the unit ending engaged'
                : 'staying engaged with its enemies; or, unengaged, ending engaged with an enemy within 3"; or, with neither, ending within range of an objective within 3"';
            controls = `<p class="play-hint">Drag the unit to ${verb} (up to 3", ${how}).
                The player whose turn it is moves their units first, then the other player; a player's moves lock when they're done.</p>
                ${moved ? '<div class="play-move-modes"><button class="btn-small" data-reset>Reset move</button></div>' : ''}`;
        }
        if (turn.phase !== 'deployment' && turn.phase !== 'ended') controls += effectsControls(unit);
        $('unit-detail').innerHTML = controls + allocationControl(unit) + unit.parts.map((part, i) => {
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
        $('unit-detail').querySelectorAll('[data-reserve]').forEach(b => b.addEventListener('click', async () => {
            if (await send({ type: 'reserve', unit: unit.id })) {
                state.selected = null;
                message(`${unit.name} is in Strategic Reserves.`);
                render();
            }
        }));
        $('unit-detail').querySelectorAll('[data-embark]').forEach(b => b.addEventListener('click', async () => {
            if (await send({ type: 'embark', unit: unit.id, transport: b.dataset.embark })) {
                state.selected = null;
                message(`${unit.name} embarked in ${unitById(b.dataset.embark).name}.`);
                render();
            }
        }));
        $('unit-detail').querySelectorAll('[data-disembark]').forEach(b => b.addEventListener('click', () => startArriving(unit, 'disembark', unit.player)));
        $('unit-detail').querySelectorAll('[data-scout-setup]').forEach(b => b.addEventListener('click', () => startArriving(unit, 'scout', unit.player)));
        $('unit-detail').querySelectorAll('[data-arrive]').forEach(b => b.addEventListener('click', () => startArriving(unit, 'ingress', unit.player)));
        $('unit-detail').querySelectorAll('[data-turn]').forEach(b => b.addEventListener('click', () => turnUnit(unit, Number(b.dataset.turn))));
        $('unit-detail').querySelectorAll('[data-skies]').forEach(b => b.addEventListener('click', () => {
            state.skies = !state.skies;
            message(state.skies ? `${unit.name} takes to the skies: 2" less, over all terrain and models.` : '');
            renderDetail();
            drawBoard();
        }));
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
        $('unit-detail').querySelectorAll('[data-allocation]').forEach(select => select.addEventListener('change', () => {
            // The chosen group first, then the others in datasheet order, then every other part (Characters)
            const groups = state.game.allocation[unit.id];
            const first = groups[Number(select.value)];
            const rest = groups.filter((_, i) => i !== Number(select.value)).flat();
            const all = [...first, ...rest];
            const order = [...all, ...unit.parts.map((_, i) => i).filter(i => !all.includes(i))];
            send({ type: 'orders', player: unit.player, allocation: { [unit.id]: order } });
        }));
        $('unit-detail').querySelectorAll('[data-remove-effect]').forEach(b => b.addEventListener('click', () =>
            send({ type: 'remove_effect', unit: unit.id, index: Number(b.dataset.removeEffect) })));
    }

    /** Buttons to embark a unit in each transport the server says it can (game.embark). */
    function embarkButtons(unit) {
        return ((state.game.embark || {})[unit.id] || []).map(id => `<button class="btn-small" data-embark="${escapeHtml(id)}">
            <i class="fa-solid fa-truck-moving" aria-hidden="true"></i> Embark in ${escapeHtml(unitById(id).name)}</button>`).join('');
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
                        const targeted = selected && (selected.status || {}).stratagem_phase === phaseId(turn);  // One per unit per phase
                        const disabled = !selected || selected.player !== p || (e.kind === 'stratagem' && (e.cp > (pl.cp ?? 0) || targeted
                            || Object.values(used).length && used[e.key] === phaseId(turn)));
                        return `<button class="play-stratagem ${e.kind === 'rule' ? 'rule' : 'stratagem'}" data-use="${p}|${escapeHtml(e.key)}" ${disabled ? 'disabled' : ''}
                            title="${escapeHtml(ruleText(e.key))}"><span>${escapeHtml(e.name)}</span>
                            <small>${e.kind === 'stratagem' ? Number(e.cp) + ' CP' : 'rule'}${e.modifier ? ' · ' + escapeHtml(e.modifier) : ' · apply by hand'}</small></button>`;
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
                : `${entry.name} used: apply it by hand (add an effect if it changes the dice). ${ruleText(key)}`.slice(0, 400));
        }
    }

    /** A stratagem's or rule's text, from this PC's data (games carry no rule text: the server looks it up). */
    function ruleText(key) {
        return (state.game.texts || {})[key] || '';
    }

    // --- Correcting the game -----------------------------------------------------------------------

    /** The game file button, and in a game this PC plays one side of, whose move it is. */
    function renderSeat() {
        const game = state.game;
        $('file-btn').hidden = !game;
        if (game) $('file-btn').href = `/api/play/games/${game.id}/file`;
        const notice = Core.playSeatNotice(game);
        $('seat-bar').hidden = !notice;
        document.title = `${notice && notice.mine && game.online ? '● Your move · ' : ''}Play - 40K Combat Simulator`;
        scheduleSync();
        if (!notice) return;
        $('seat-bar').classList.toggle('play-seat-mine', notice.mine);
        const buttons = [
            notice.invite ? `<button class="btn-small" id="seat-copy"><i class="fa-solid fa-copy" aria-hidden="true"></i> Copy code</button>` : '',
            notice.send ? `<a class="btn-small" href="/api/play/games/${game.id}/file">
                <i class="fa-solid fa-file-export" aria-hidden="true"></i> Save game file</a>` : '',
            !game.online && !game.needs_update ? `<button class="btn-small" id="seat-online" title="Moves go to the other player as you make them">
                <i class="fa-solid fa-globe" aria-hidden="true"></i> Play online</button>` : '',
            game.online ? `<button class="btn-small" id="seat-new-code" title="If someone else may have used the code: the old one stops working">
                <i class="fa-solid fa-arrows-rotate" aria-hidden="true"></i> New invite code</button>` : '',
        ].filter(Boolean);
        $('seat-bar').innerHTML = `<strong>${escapeHtml(notice.text)}</strong>${buttons.length
            ? `<span class="play-pending-actions">${buttons.join('')}</span>` : ''}`;
        if (notice.invite) {
            $('seat-copy').addEventListener('click', () => navigator.clipboard.writeText(Core.playInviteCode(notice.invite))
                .then(() => message('Invite code copied.'), () => message('Copy the code by hand.', true)));
        }
        if ($('seat-online')) $('seat-online').addEventListener('click', goOnline);
        if ($('seat-new-code')) $('seat-new-code').addEventListener('click', newInviteCode);
    }

    /** The open reaction window: the reacting player picks (a unit and target for Fire Overwatch) or passes. */
    const REACTION_NAMES = { overwatch: 'Fire Overwatch', heroic: 'Heroic Intervention', reroll: 'Command Re-roll', ingress: 'Rapid Ingress' };
    const HEROIC_MODES = { leap: 'Leap to Defend', fray: 'Into the Fray' };

    /** The unit making a Heroic Intervention's charge move now, if any (it charges in the other player's turn). */
    function heroicUnit() {
        const r = state.game && state.game.reaction;
        return r && r.kind === 'heroic' ? r.unit || null : null;
    }

    /** Whether `unit` charges now: the active player's in their Charge phase, or the Heroic Intervention's unit. */
    function charging(unit) {
        const turn = state.game.state.turn;
        return turn.phase === 'charge' && (unit.player === turn.player || heroicUnit() === unit.id);
    }

    function renderWindow() {
        const game = state.game;
        const r = game && game.reaction;
        const bar = $('window-bar');
        bar.hidden = !r;
        if (!r) return;
        const who = game.state.players[r.player].name;
        if (game.seat != null && game.seat !== r.player) {
            bar.innerHTML = `<strong>Waiting for ${escapeHtml(who)}</strong> to decide on ${escapeHtml(r.name)}.`;
            return;
        }
        if (r.kind === 'overwatch') {
            const ids = Object.keys(r.units);
            const unit = state.reactUnit && r.units[state.reactUnit] ? state.reactUnit : ids[0];
            const targets = r.units[unit] || [];
            bar.innerHTML = `<span><strong>${escapeHtml(who)}: ${escapeHtml(r.name)}?</strong>
                    ${Number(r.cp)} CP · one unit snap shoots one visible enemy unit within 24": hits only on 6s, never critical.</span>
                <span class="play-pending-actions">
                    <select class="filter-select" id="react-unit">${ids.map(id =>
                        `<option value="${escapeHtml(id)}" ${id === unit ? 'selected' : ''}>${escapeHtml(unitById(id).name)}</option>`).join('')}</select>
                    at <select class="filter-select" id="react-target">${targets.map(id =>
                        `<option value="${escapeHtml(id)}">${escapeHtml(unitById(id).name)}</option>`).join('')}</select>
                    <button class="btn btn-primary" id="react-go"><i class="fa-solid fa-crosshairs" aria-hidden="true"></i> Fire Overwatch</button>
                    <button class="btn-small" id="react-pass">Pass</button></span>`;
            $('react-unit').addEventListener('change', e => { state.reactUnit = e.target.value; renderWindow(); });
            $('react-go').addEventListener('click', async () => {
                if (await send({ type: 'overwatch', player: r.player, unit: $('react-unit').value, target: $('react-target').value })) {
                    const event = state.game.events[state.game.events.length - 1];
                    const killed = event.attacks.reduce((n, a) => n + a.killed.length, 0);
                    message(`Overwatch: ${killed} model${killed === 1 ? '' : 's'} killed.`);
                }
            });
        } else if (r.kind === 'heroic' && r.unit) {
            bar.innerHTML = `<span><strong>${escapeHtml(unitById(r.unit).name)}: ${escapeHtml(HEROIC_MODES[r.mode])}, charge roll ${Number(r.roll)}".</strong>
                    Pick its targets, then drag it so it ends engaged with them (this move can't be redone).</span>
                <span class="play-pending-actions"><button class="btn-small" id="react-pass">Don't move</button></span>`;
        } else if (r.kind === 'heroic') {
            const ids = Object.keys(r.units);
            const unit = state.reactUnit && r.units[state.reactUnit] ? state.reactUnit : ids[0];
            const modes = r.units[unit] || {};
            bar.innerHTML = `<span><strong>${escapeHtml(who)}: ${escapeHtml(r.name)}?</strong>
                    One unit within 12" of the enemy charges. Leap to Defend: only units that charged this phase.
                    Into the Fray: any enemy within 6", the roll at most 6.</span>
                <span class="play-pending-actions">
                    <select class="filter-select" id="react-unit">${ids.map(id =>
                        `<option value="${escapeHtml(id)}" ${id === unit ? 'selected' : ''}>${escapeHtml(unitById(id).name)}</option>`).join('')}</select>
                    <button class="btn btn-primary" data-heroic="leap" ${modes.leap ? '' : 'disabled'}>Leap to Defend (${Number(r.cp)} CP)</button>
                    <button class="btn btn-primary" data-heroic="fray" ${modes.fray ? '' : 'disabled'}>Into the Fray (${Number(r.cp) + 1} CP)</button>
                    <button class="btn-small" id="react-pass">Pass</button></span>`;
            $('react-unit').addEventListener('change', e => { state.reactUnit = e.target.value; renderWindow(); });
            bar.querySelectorAll('[data-heroic]').forEach(b => b.addEventListener('click', async () => {
                const id = $('react-unit').value;
                if (await send({ type: 'heroic', player: r.player, unit: id, mode: b.dataset.heroic })) {
                    const event = state.game.events[state.game.events.length - 1];
                    message(event.failed ? `Charge roll ${event.roll}": no enemy unit within reach, so the Heroic Intervention fails.`
                        : `Charge roll ${event.roll}". Pick the targets and drag ${unitById(id).name} into combat.`);
                    if (!event.failed) {
                        state.charge = null;
                        state.chargeTargets = [];
                        select(id);
                    }
                }
            }));
        } else if (r.kind === 'ingress') {
            const ids = Object.keys(r.units);
            const arriving = state.placing && state.placing.kind === 'rapid_ingress' ? state.placing.unitId
                : state.arriving && state.arriving.kind === 'rapid_ingress' ? state.arriving.unitId : null;
            bar.innerHTML = arriving
                ? `<span><strong>${escapeHtml(who)}: Rapid Ingress with ${escapeHtml(unitById(arriving).name)}.</strong>
                    ${state.placing ? 'Drag it into place, then press OK.' : 'Click the shaded ground where it arrives.'}</span>
                    <span class="play-pending-actions"><button class="btn-small" id="react-cancel">Choose again</button></span>`
                : `<span><strong>${escapeHtml(who)}: ${escapeHtml(r.name)}?</strong> ${Number(r.cp)} CP · one unit arrives from reserves now
                    (an ingress move: within 6" of an edge, or anywhere with Deep Strike; more than 8" from enemies).</span>
                <span class="play-pending-actions">
                    <select class="filter-select" id="react-unit">${ids.map(id =>
                        `<option value="${escapeHtml(id)}">${escapeHtml(unitById(id).name)}${r.units[id].deep_strike ? ' (Deep Strike)' : ''}</option>`).join('')}</select>
                    <button class="btn btn-primary" id="react-go"><i class="fa-solid fa-parachute-box" aria-hidden="true"></i> Set it up</button>
                    <button class="btn-small" id="react-pass">Pass</button></span>`;
            if (arriving) {
                $('react-cancel').addEventListener('click', () => {
                    state.arriving = null;
                    cancelPlacing();
                    message('');
                });
                return;
            }
            $('react-go').addEventListener('click', () => startArriving(unitById($('react-unit').value), 'rapid_ingress', r.player));
        } else if (r.kind === 'reroll') {
            if (state.rerolling !== r.player) {
                state.rerolling = r.player;
                renderLog();
            }
            bar.innerHTML = `<span><strong>${escapeHtml(who)}: ${escapeHtml(r.name)}?</strong> A save failed: click one of your
                    glowing dice in the battle log to re-roll it (${Number(r.cp)} CP), or pass.</span>
                <span class="play-pending-actions"><button class="btn-small" id="react-pass">Pass</button></span>`;
        } else {
            bar.innerHTML = `<span><strong>${escapeHtml(who)}: ${escapeHtml(r.name)}?</strong></span>
                <span class="play-pending-actions"><button class="btn-small" id="react-pass">Pass</button></span>`;
        }
        $('react-pass').addEventListener('click', async () => {
            if (await send({ type: 'pass', player: r.player })) message(`${who} passed on ${r.name}.`);
        });
    }

    function renderPending() {
        const game = state.game;
        const pending = game && game.state.pending;
        $('pending-bar').hidden = !pending;
        if (!pending) return;
        const by = game.state.players[pending.player].name, other = game.state.players[1 - pending.player].name;
        const models = Object.keys(pending.models).length;
        const cp = [...Object.entries(pending.cp).map(([p, v]) => `${game.state.players[p].name} ${v} CP`),
                    ...Object.entries(pending.vp || {}).map(([p, v]) => `${game.state.players[p].name} ${v > 0 ? '+' : ''}${v} VP`)].join(', ');
        const asks = pending.undo ? `${escapeHtml(by)} asks to take back their last action:` : `${escapeHtml(by)} proposes a correction:`;
        $('pending-bar').innerHTML = `<strong>${asks}</strong> ${escapeHtml(pending.note || '(no note)')}
            <small>${models ? `${models} model${models === 1 ? '' : 's'} changed` : ''}${models && cp ? '; ' : ''}${escapeHtml(cp)}</small>
            <span class="play-pending-actions">${escapeHtml(other)}:
                <button class="btn-small" data-answer="accept">Accept</button>
                <button class="btn-small" data-answer="decline">Decline</button></span>`;
        $('pending-bar').querySelectorAll('[data-answer]').forEach(b => b.addEventListener('click', async () => {
            if (await send({ type: b.dataset.answer, player: 1 - pending.player })) {
                message(b.dataset.answer === 'accept' ? (pending.undo ? 'Taken back.' : 'Correction made.') : 'Correction declined.');
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
                <td><input type="number" min="0" max="${Number(part.stats.wounds)}" value="${Number(m.wounds_left)}" data-fix-wounds="${escapeHtml(m.id)}" class="filter-input"></td>
                <td><label><input type="checkbox" data-fix-alive="${escapeHtml(m.id)}" ${m.alive ? 'checked' : ''}> alive</label></td></tr>`;
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
                const wounds = Number($('correction-panel').querySelector(`[data-fix-wounds="${CSS.escape(m.id)}"]`).value);
                const alive = $('correction-panel').querySelector(`[data-fix-alive="${CSS.escape(m.id)}"]`).checked;
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
            : charging(unit) ? 'charge'
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
            const markers = a.markers.length ? `<select class="filter-select" data-action-marker="${escapeHtml(a.key)}">${a.markers.map(m =>
                `<option value="${escapeHtml(m.id)}">Remove ${escapeHtml(players[m.player].name)}'s ${escapeHtml(Core.playMarker(m.tag).label.toLowerCase())} (${escapeHtml(m.id)})</option>`).join('')}</select>` : '';
            return `<div class="play-action-row"><strong>${escapeHtml(a.name)}</strong>
                <select class="filter-select" data-action-target="${escapeHtml(a.key)}">${a.targets.map(t => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.label)}</option>`).join('')}</select>
                ${markers}<button class="btn-small" data-start-action="${escapeHtml(a.key)}">Start</button></div>`;
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
            data-charge-target="${escapeHtml(id)}">${escapeHtml(unitById(id).name)} <small>${options.targets[id].distance}"</small></button>`).join('');
        return `<p class="play-hint">Charge roll <strong>${options.roll}"</strong>. Pick the units to charge (or click them on
            the board), then drag the unit so it ends engaged with every one of them and no other: each model that can
            ends within 1" of a target (or, if it can't, engaged with one).
            ${heroicUnit() === unit.id ? 'A Heroic Intervention\'s move is made once.' : 'You can redo the move until the Charge phase ends.'}</p>
            <div class="play-move-modes">${chips}
            ${unit.activity.charged ? '<button class="btn-small" data-reset>Reset charge</button>' : ''}${skiesButton(unit)}</div>`;
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
        if (options.overrun) {
            return `<p class="play-hint"><strong>Overrun fight:</strong> ${escapeHtml(unit.name)} isn't engaged. Drag it up to 3"
                to end engaged with an enemy unit within 5" (its extra pile-in), then it fights.</p>`;
        }
        if (!options.targets.length) {
            return `<p class="play-hint">${escapeHtml(unit.name)} has no enemy within reach to fight.</p>
                <div class="play-shooting"><button class="btn btn-primary" id="fight-btn">Pass</button></div>`;
        }
        const targetSelect = part => `<select class="filter-select" data-fight-target="${part}">
            ${options.targets.map(id => `<option value="${escapeHtml(id)}" ${state.fightTarget[part] === id ? 'selected' : ''}>${escapeHtml(unitById(id).name)}</option>`).join('')}
        </select>`;
        const rows = options.parts.map(part => {
            const plain = part.weapons.filter(w => !w.extra);
            const extra = part.weapons.filter(w => w.extra);
            return `<div class="play-weapon"><strong>${escapeHtml(part.name)}</strong>
                ${plain.length ? `<select class="filter-select" data-fight-weapon="${part.part}">
                    ${plain.map(w => `<option value="${Number(w.weapon)}" ${state.fightWeapon[part.part] === w.weapon ? 'selected' : ''}>${escapeHtml(w.name)}</option>`).join('')}
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
            message(event.passed ? `${name} had no enemy within reach, so it didn't fight.`
                : `${name} fought: ${killed} model${killed === 1 ? '' : 's'} killed.`);
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
            const select = `<select class="filter-select" data-target="${escapeHtml(key)}">
                <option value="">Choose a target</option>
                ${enemies.map(e => {
                    const t = w.targets[e.id];
                    return `<option value="${escapeHtml(e.id)}" ${target === e.id ? 'selected' : ''} ${t && t.ok ? '' : 'disabled'}
                        title="${escapeHtml(t && t.reason ? t.reason : '')}">${escapeHtml(e.name)}${t && t.ok ? ` (${t.models} firing)` : ' (no)'}</option>`;
                }).join('')}</select>`;
            const why = choice && !choice.ok ? `<small class="play-why">${escapeHtml(choice.reason)}</small>` : '';
            return `<div class="play-weapon ${choice && !choice.ok ? 'blocked' : ''}">
                <label><input type="checkbox" data-fire="${escapeHtml(key)}" ${checked ? 'checked' : ''} ${choice && choice.ok ? '' : 'disabled'}>
                    ${escapeHtml(w.name)}${w.deck ? ' <small class="play-tag">Firing Deck</small>' : ''} <small>${w.range ? w.range + '"' : ''}</small></label>${select}${why}</div>`;
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
        const ONE_LINE = ['heroic', 'rapid_ingress', 'scout', 'embark', 'disembark', 'command_reroll', 'roll_mirrored', 'roll_objectives', 'roll_off', 'choose_role', 'choose_secondaries', 'condemn', 'start_action', 'place_markers', 'tactical', 'beacon', 'tempting', 'charge', 'advance', 'stratagem', 'rule', 'effect', 'correction', 'accept', 'decline'];
        const shots = game ? game.events.map((e, i) => [e, i])
            .filter(([e]) => (e.attacks && e.attacks.length) || e.passed || ONE_LINE.includes(e.action.type)
                || (e.demise && e.demise.length) || (e.emergency && e.emergency.length)
                || (['next_phase', 'end_deployment'].includes(e.action.type)
                    && (e.rolls.length || (e.vp && e.vp.length) || (e.actions && e.actions.length) || (e.markers_removed && e.markers_removed.length)
                        || (e.coherency && e.coherency.length) || (e.reserves_lost && e.reserves_lost.length)
                        || (e.empty_transports && e.empty_transports.length)
                        || e.drawn || (e.discards && e.discards.length)))).reverse() : [];
        if (!shots.length) {
            $('dice-log').innerHTML = '<p class="empty-message">No dice rolled yet</p>';
            state.logSeen = game ? game.events.length : 0;
            return;
        }
        const name = id => (game.state.units.find(u => u.id === id) || { name: id }).name;
        const owner = id => (game.state.units.find(u => u.id === id) || {}).player;
        const players = game.state.players;
        const turns = Core.playLogTurns(game.events);
        const heading = t => !t || t.phase === 'setup' ? ['Setup', null] : t.phase === 'deployment' ? ['Deployment', null]
            : t.phase === 'prebattle' ? ['Pre-battle', null]
            : [`Round ${t.round} · ${players[t.player].name} · ${PHASE_NAMES[t.phase]}`, t.player];
        let last = null;
        const latest = game.events.length - 1;
        $('dice-log').innerHTML = rerollBar() + shots.map(([event, i], n) => {
            const fresh = state.logSeen != null && i >= state.logSeen;  // Just made: its dice roll in
            const [title, player] = heading(turns[i]);
            const section = title !== last ? `<h4 class="play-log-heading ${player != null ? PLAYER_CLASSES[player] : ''}">${escapeHtml(title)}</h4>` : '';
            last = title;
            if (!event.attacks || !event.attacks.length) {
                return `${section}<div class="play-log-entry play-log-roll ${fresh ? 'fresh' : ''}">
                    <span class="play-log-icon"><i class="${logIcon(event)}" aria-hidden="true"></i></span><div class="play-log-text">${logLine(event, name, i === latest)}</div></div>`;
            }
            return section + attackEntry(event, n === 0, fresh, name, owner, i === latest);
        }).join('');
        wireReroll();
        state.logSeen = game.events.length;
    }

    /** A shooting or fighting event in the battle log: who attacked whom and how many died, then each weapon's
     * Attacks › Hits › Wounds › Killed and its dice. */
    function attackEntry(event, open, fresh, name, owner, latest = false) {
        const act = event.redo || event.action;  // A Command Re-roll works its action out again
        const fight = act.type === 'fight';
        const targets = [...new Set(event.attacks.filter(a => a.name !== 'Hazardous').map(a => a.target))];
        const killed = event.attacks.reduce((sum, a) => sum + a.killed.length, 0);
        let k = 0;  // Each die's place, so fresh dice roll in one after another
        const weapons = event.attacks.map(a => {
            const stages = a.name === 'Hazardous' ? [[a.models, 'Rolled'], [a.killed.length, 'Killed']]
                : [[a.attacks, 'Attacks'], [a.hits, 'Hits'], [a.wounds, 'Wounds'], ...(a.mortal ? [[a.mortal, 'Mortal']] : []), [a.killed.length, 'Killed']];
            const who = [a.name === 'Hazardous' ? '' : `${a.models} model${a.models === 1 ? '' : 's'}`, a.half_range ? 'half range' : '',
                a.cover ? 'target in cover' : '', a.name !== 'Hazardous' && targets.length > 1 ? `at ${name(a.target)}` : ''].filter(Boolean).join(' · ');
            const funnel = stages.map(([value, label], s) => `${s ? '<i class="fa-solid fa-chevron-right" aria-hidden="true"></i>' : ''}
                <span class="play-stage ${label === 'Killed' ? 'killed' : ''} ${value ? '' : 'zero'}"><b>${value}</b><small>${label}</small></span>`).join('');
            const groups = Core.playDiceGroups(event.rolls, a.rolls);
            const luck = Core.playLuck(groups);
            const notice = luck ? `<div class="play-luck ${luck.lucky ? 'hot' : 'cold'}"><i class="fa-solid fa-${luck.lucky ? 'fire' : 'snowflake'}" aria-hidden="true"></i>
                ${escapeHtml(luck.text)}</div>` : '';
            const dice = groups.map(g => `<div class="play-dice-row">
                <span class="play-dice-label">${escapeHtml(g.label)}${g.need ? `<em>${g.need}</em>` : ''}</span>
                <span class="play-dice">${g.dice.map(d => dieHtml(d.face, d.kind, k++, d.rerolled_from, latest ? d.ref : null)).join('')}</span>
                ${g.note ? `<small class="play-dice-note">${escapeHtml(g.note)}</small>` : ''}</div>`).join('');
            return `<div class="play-log-weapon ${a.name === 'Hazardous' ? 'hazardous' : ''}" title="${escapeHtml(Core.playAttackSummary(a))}">
                <div class="play-log-weapon-name"><strong>${escapeHtml(a.name)}</strong>${who ? `<small>${escapeHtml(who)}</small>` : ''}</div>
                <div class="play-funnel">${funnel}</div>${dice}${notice}</div>`;
        }).join('');
        const shooter = owner(act.unit);
        const redone = event.redo ? `<div class="play-log-redo"><i class="fa-solid fa-rotate" aria-hidden="true"></i>
            ${escapeHtml(state.game.state.players[event.player].name)} used Command Re-roll (${Number(event.cp)} CP): a ${event.rerolled.from} re-rolled.
            This replaces the roll above.</div>` : '';
        return `<details class="play-log-entry play-log-attack ${PLAYER_CLASSES[shooter] || ''} ${fresh ? 'fresh' : ''}" ${open ? 'open' : ''}>
            <summary class="play-log-head">
                <span class="play-log-icon"><i class="fa-solid ${fight ? 'fa-hand-fist' : 'fa-crosshairs'}" aria-hidden="true"></i></span>
                <span class="play-log-who"><strong>${escapeHtml(name(act.unit))}</strong>
                    <span class="play-log-verb">${event.overwatch || act.type === 'overwatch' ? 'fires Overwatch at' : fight ? 'fights' : 'shoots'}</span>
                    <strong class="play-log-target ${PLAYER_CLASSES[1 - shooter] || ''}">${escapeHtml(targets.map(name).join(', ') || 'itself')}</strong></span>
                <span class="play-log-kills ${killed ? '' : 'none'}"><b>${killed}</b><small>killed</small></span>
            </summary>
            <div class="play-log-body">${redone}${weapons}${aftermath(event, name)}</div>
        </details>`;
    }

    /** What models destroyed in an event set off: emergency disembarks and Deadly Demise. */
    function aftermath(event, name) {
        const lines = (event.emergency || []).map(e => `<strong>${escapeHtml(e.name)}</strong> made an emergency disembark from
            ${escapeHtml(name(e.transport))}: ${e.hazard.fails} hazard roll${e.hazard.fails === 1 ? '' : 's'} failed,
            ${e.hazard.killed.length + e.lost.length} model${e.hazard.killed.length + e.lost.length === 1 ? '' : 's'} lost; battle-shocked`)
            .concat((event.demise || []).map(d => `<strong>${escapeHtml(d.name)}</strong> Deadly Demise: rolled ${d.roll}${d.roll === 6
                ? ': ' + (d.hits.map(h => `${escapeHtml(h.name)} ${h.mortal_wounds} mortal wound${h.mortal_wounds === 1 ? '' : 's'}${h.killed.length ? ` (${h.killed.length} killed)` : ''}`).join(', ') || 'nothing within 6"')
                : ' (needs a 6)'}`));
        return lines.length ? `<div class="play-log-aftermath">${lines.map(l => `<div>${l}</div>`).join('')}</div>` : '';
    }

    /** A die: pips for a d6 face (else the number), lit by how it went: critical, success, fail or value. */
    function dieHtml(face, kind, place, rerolledFrom, ref = null) {
        face = Number(face);  // Faces come from the log, which the other player's app may have written
        rerolledFrom = rerolledFrom == null ? rerolledFrom : Number(rerolledFrom);
        const pips = Core.playDieFace(face);
        const what = { critical: 'critical', success: 'success', fail: 'fail' }[kind] || '';
        const inside = pips ? pips.map(p => `<i style="grid-area: p${p}"></i>`).join('') : `<span>${face}</span>`;
        const pick = ref != null && pickable(ref);
        return `<span class="play-die ${kind} ${pick ? 'pickable' : ''}" style="--i: ${Math.min(place, 40)}" ${pick
                ? `role="button" tabindex="0" data-reroll="${escapeHtml(ref)}"` : 'role="img"'} aria-label="${face}${what ? ', ' + what : ''}${pick ? ': re-roll it' : ''}"
            title="${face}${rerolledFrom ? ` (re-rolled from ${rerolledFrom})` : ''}${pick ? ' · click to Command Re-roll' : ''}">${inside}${rerolledFrom ? '<em class="play-die-reroll">&#8635;</em>' : ''}</span>`;
    }

    /** Whether a die of the last event ('roll:die') is one the player picking now may Command Re-roll. */
    function pickable(ref) {
        const picking = state.rerolling;
        return picking != null && ((state.game.rerollable || {})[picking] || []).some(([g, i]) => `${g}:${i}` === ref);
    }

    /** Players who could Command Re-roll a die of the last roll now (online, only this app's player). */
    function rerollers() {
        const game = state.game;
        return Object.keys(game.rerollable || {}).map(Number).filter(p => game.seat == null || game.seat === p);
    }

    /** Above the log: Command Re-roll for the last roll, or picking its die. */
    function rerollBar() {
        const game = state.game;
        const players = rerollers();
        if (state.rerolling != null && !players.includes(state.rerolling)) state.rerolling = null;
        if (!players.length) return '';
        if (state.rerolling != null) {
            return `<div class="play-reroll-bar"><i class="fa-solid fa-rotate" aria-hidden="true"></i>
                Command Re-roll (1 CP): click one of ${escapeHtml(game.state.players[state.rerolling].name)}'s glowing dice in the last roll.
                <button class="btn-small" data-reroll-cancel>Cancel</button></div>`;
        }
        return `<div class="play-reroll-bar"><i class="fa-solid fa-rotate" aria-hidden="true"></i> Command Re-roll a die of the last roll:
            ${players.map(p => `<button class="btn-small" data-reroll-player="${p}">${escapeHtml(game.state.players[p].name)} (1 CP)</button>`).join(' ')}</div>`;
    }

    function wireReroll() {
        const log = $('dice-log');
        log.querySelectorAll('[data-reroll-player]').forEach(b => b.addEventListener('click', () => {
            state.rerolling = Number(b.dataset.rerollPlayer);
            renderLog();
        }));
        log.querySelectorAll('[data-reroll-cancel]').forEach(b => b.addEventListener('click', () => {
            state.rerolling = null;
            renderLog();
        }));
        log.querySelectorAll('[data-reroll]').forEach(die => {
            const go = async () => {
                const [roll, index] = die.dataset.reroll.split(':').map(Number);
                const player = state.rerolling;
                if (await send({ type: 'command_reroll', player, roll, die: index })) {
                    state.rerolling = null;
                    const event = state.game.events[state.game.events.length - 1];
                    const after = event.rolls[roll] && event.rolls[roll].dice[index];
                    message(`Command Re-roll: ${event.rerolled.from} became ${after ? after.face : '?'}.`);
                    renderLog();
                }
            };
            die.addEventListener('click', go);
            die.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
        });
    }

    /** The icon for a battle log line that isn't an attack. */
    function logIcon(event) {
        const icons = { command_reroll: 'fa-rotate', heroic: 'fa-shield-halved', rapid_ingress: 'fa-parachute-box', scout: 'fa-person-running',
            embark: 'fa-truck-moving', disembark: 'fa-door-open', pass: 'fa-forward', fight: 'fa-hand-fist', charge: 'fa-person-running', advance: 'fa-person-running', stratagem: 'fa-bolt', rule: 'fa-book',
            effect: 'fa-wand-magic-sparkles', roll_off: 'fa-dice', roll_objectives: 'fa-dice', roll_mirrored: 'fa-dice',
            choose_role: 'fa-chess-board', choose_secondaries: 'fa-layer-group', tactical: 'fa-layer-group', condemn: 'fa-gavel',
            start_action: 'fa-flag', place_markers: 'fa-location-dot', beacon: 'fa-tower-broadcast', tempting: 'fa-bullseye',
            correction: 'fa-scale-balanced', accept: 'fa-scale-balanced', decline: 'fa-scale-balanced' };
        const icon = icons[event.action.type]
            || ((event.battle_shock || []).length ? 'fa-triangle-exclamation' : (event.hazards || []).length ? 'fa-skull'
                : (event.coherency || []).length ? 'fa-link-slash'
                : (event.vp || []).length ? 'fa-trophy' : event.drawn || (event.discards || []).length ? 'fa-layer-group' : 'fa-dice');
        return `fa-solid ${icon}`;
    }

    function actionName(key) {
        return { consecrate: 'Consecrate', 'booby-trap': 'Booby Trap', decoy: 'Decoy', 'sensor-sweep': 'Sensor Sweep',
                 triangulate: 'Triangulate', surveil: 'Surveil the Foe', 'extract-intelligence': 'Extract Intelligence',
                 'secure-asset': 'Secure Asset', 'maintain-control': 'Maintain Control', 'vanguard-operation': 'Vanguard Operation',
                 sabotage: 'Sabotage' }[key] || key;
    }

    /** A reaction's standing order choices ([value, label]), the player's current one selected. */
    function reactionOrderOptions(player, kind, choices) {
        const mode = ((((player.orders || {}).reactions || {})[kind]) || { mode: 'ask' }).mode;
        return choices.map(([value, label]) => `<option value="${value}" ${value === mode ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('');
    }

    /** The Fire Overwatch standing order's choices: ask, never, or always with one of the player's units. */
    function overwatchOrderOptions(player, units) {
        const order = ((player.orders || {}).reactions || {}).overwatch || { mode: 'ask' };
        const chosen = order.mode === 'unit' ? `unit|${order.unit}` : order.mode;
        const option = (value, label) => `<option value="${escapeHtml(value)}" ${value === chosen ? 'selected' : ''}>${escapeHtml(label)}</option>`;
        return option('ask', 'ask me') + option('never', 'never') +
            units.filter(u => u.models.some(m => m.alive)).map(u => option(`unit|${u.id}`, `always, with ${u.name}`)).join('');
    }

    /** Markers an event removed, for its log line. */
    function markersRemoved(event) {
        return (event.markers_removed || []).map(r => `<br>Removed ${r.markers.map(m =>
            `${escapeHtml(state.game.state.players[m.player].name)}'s ${escapeHtml(Core.playMarker(m.tag).label.toLowerCase())}`).join(', ')} (${escapeHtml(r.why)})`).join('');
    }

    /** One line of the dice log for an event that isn't an attack. */
    function logLine(event, name, latest = false) {
        const a = event.action;
        const dice = (roll, g = 0) => `<span class="play-dice">${roll.dice.map((d, k) => dieHtml(d.face,
            roll.need == null ? 'value' : d.critical ? 'critical' : d.success ? 'success' : 'fail', k, d.rerolled_from, latest ? `${g}:${k}` : null)).join('')}</span>`;
        const player = p => escapeHtml(state.game.state.players[p].name);
        switch (a.type) {
            case 'charge':
            case 'advance':
                return `${escapeHtml(name(a.unit))} ${a.type === 'charge' ? 'charge' : 'Advance'} roll: ${dice(event.rolls[0])} = <strong>${event.rolls[0].total}"</strong>`;
            case 'stratagem':
                return `<span class="play-effect stratagem">${player(event.player)} used ${escapeHtml(event.stratagem)} (${Number(event.cp)} CP)</span> on ${escapeHtml(name(event.unit))}`;
            case 'rule':
                return `<span class="play-effect rule">${player(event.player)}: ${escapeHtml(event.rule)}</span> on ${escapeHtml(name(event.unit))}`;
            case 'effect':
                return `<span class="play-effect manual">Manual effect: ${escapeHtml(event.effect)}</span> on ${escapeHtml(name(event.unit))}`;
            case 'pass':
                return `${player(event.player)} passed on ${escapeHtml(REACTION_NAMES[event.passed] || event.passed)}`;
            case 'heroic':
                return `<span class="play-effect stratagem">${player(event.player)} used Heroic Intervention (${escapeHtml(HEROIC_MODES[event.heroic])}, ${Number(event.cp)} CP)</span>:
                    ${escapeHtml(name(event.unit))} charge roll ${dice(event.rolls[0])} = <strong>${event.roll}"</strong>${event.failed ? ': nothing in reach, it fails' : ''}`;
            case 'embark':
                return `${escapeHtml(name(a.unit))} embarked in ${escapeHtml(name(a.transport))}`;
            case 'disembark': {
                const hz = (event.hazards || [])[0];
                return `${escapeHtml(name(a.unit))} disembarked from ${escapeHtml(name(event.transport))} (${escapeHtml(DISEMBARK_NAMES[event.mode])})`
                    + (hz ? `: hazard rolls ${dice(event.rolls[0])}, ${hz.killed.length} lost` : '');
            }
            case 'scout':
                return `${escapeHtml(name(a.unit))} ${event.how === 'set_up' ? 'was set up from reserves' : 'made a scout move'} (Scouts)`;
            case 'rapid_ingress':
                return `<span class="play-effect stratagem">${player(event.player)} used Rapid Ingress (${Number(event.cp)} CP)</span>:
                    ${escapeHtml(name(event.unit))} arrived from reserves`;
            case 'command_reroll': {
                const roll = event.rolls.find(r => r.purpose === 'charge' || r.purpose === 'advance');
                return `<span class="play-effect stratagem">${player(event.player)} used Command Re-roll (${Number(event.cp)} CP)</span>:
                    ${escapeHtml(name(event.unit))} ${roll ? `${roll.purpose === 'charge' ? 'charge' : 'Advance'} roll ${dice(roll, event.rolls.indexOf(roll))} = <strong>${roll.total}"</strong>` : ''}`;
            }
            case 'fight':  // Passed: an overrun fight with no enemy in reach
                return `<strong>${escapeHtml(name(a.unit))}</strong> had no enemy within reach, so it didn't fight`;
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
                return `<strong>${player(event.chose.player)}</strong> chose to be the ${escapeHtml(event.chose.role)}`;
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
                    lines.push(`<strong>${escapeHtml(b.name)}</strong> battle-shock test${b.reason ? ` (${escapeHtml(b.reason)})` : ''}: ${dice(event.rolls[b.rolls[0]])}
                        = ${b.total} (needs ${b.leadership}+): ${outcome}`);
                }
                for (const c of event.empty_transports || []) {
                    lines.push(`<strong>${escapeHtml(c.name)}</strong> carried no unit when deployment ended: lost (a Dedicated Transport must carry one)`);
                }
                for (const c of event.reserves_lost || []) {
                    lines.push(`<strong>${escapeHtml(c.name)}</strong> was still in reserves at the end of battle round 3: lost`);
                }
                for (const c of event.coherency || []) {
                    lines.push(`<strong>${escapeHtml(c.name)}</strong> was out of coherency: ${c.removed.length} model${c.removed.length === 1 ? '' : 's'} removed`);
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
                    lines.push(`<strong>${escapeHtml(state.game.state.players[d.player].name)}</strong> discarded ${escapeHtml(d.cards.join(', '))} for ${Number(d.cp)}CP`);
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
        // The game's battle mat, dimmed so everything drawn on it stays readable
        const mat = Core.playMat(game && game.uid);
        svgEl('image', { href: `/static/img/mats/${mat.file}`, x: 0, y: 0, width: W, height: D, preserveAspectRatio: 'none', class: 'play-mat' }, svg);
        svgEl('rect', { x: 0, y: 0, width: W, height: D, class: 'play-mat-dim', opacity: 1 - Core.PLAY_MAT_STRENGTH }, svg);
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
        drawIngress(svg, D);
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
            drawBorder(g, unit, placed, D);
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
            for (const model of placed) {  // Under the models: grabbing the unit's border area grabs the unit
                padded(g, unit, model, D, BORDER_PAD, { class: 'play-unit-hit', 'data-unit': unit.id, 'data-model': model.id,
                                                       'data-follow': model.id, 'data-turn': 1 });
            }
            for (const model of placed) drawModel(g, unit, model, D);
            const label = svgEl('text', { x: (left + right) / 2, y: top - 0.3, class: 'play-label' }, g);
            const carrying = game.state.units.filter(o => o.status && o.status.embarked === unit.id).length;
            label.textContent = unit.name + (carrying ? ` (${carrying} unit${carrying === 1 ? '' : 's'} aboard)` : '');
        }
        // Wounds badges on top of every model, so a neighbouring base never hides one
        const badges = svgEl('g', { class: 'play-badges' }, svg);
        svg.querySelectorAll('.play-wounds').forEach(b => badges.appendChild(b));
        drawMarkers(svg, D);  // Operation markers above the models, so a unit standing on one doesn't hide it
        drawMeasure();
    }

    // --- Turning in the Movement phase: shown at once, sent as one move after a short pause ---

    const TURN_SEND_DELAY = 600;  // ms

    function canTurn(unit) {
        const turn = state.game && state.game.state.turn;
        return !!turn && turn.phase === 'movement' && unit.player === turn.player && !state.working
            && unit.models.some(m => m.alive && m.position);
    }

    function startTurning(unit) {
        if (state.turning && state.turning.unitId !== unit.id) flushTurn();
        if (!state.turning) {
            const original = unit.models.filter(m => m.alive && m.position).map(m => ({ id: m.id, ...m.position }));
            state.turning = { unitId: unit.id, original, degrees: 0, spins: {}, timer: null };
        }
        return state.turning;
    }

    function previewTurn() {
        const t = state.turning, unit = unitById(t.unitId);
        const { spots } = Core.playTurn(t.original, t.degrees, t.spins);
        const byId = Object.fromEntries(spots.map(p => [p.id, p]));
        unit.models.forEach(m => { if (byId[m.id]) m.position = { x: byId[m.id].x, y: byId[m.id].y, angle: byId[m.id].angle }; });
        drawBoard();
        clearTimeout(t.timer);
        t.timer = setTimeout(flushTurn, TURN_SEND_DELAY);
        const parts = [t.degrees && `${Math.abs(t.degrees)}° ${t.degrees > 0 ? 'anticlockwise' : 'clockwise'}`,
                       Object.keys(t.spins).length && 'spinning models on the spot'].filter(Boolean);
        message(`Turning ${unit.name}: ${parts.join(', ') || 'back where it was'}…`);
    }

    function turnUnit(unit, degrees) {
        if (!canTurn(unit)) return;
        startTurning(unit).degrees += degrees;
        previewTurn();
    }

    function spinModel(unit, modelId, degrees) {
        startTurning(unit).spins[modelId] = (state.turning.spins[modelId] || 0) + degrees;
        previewTurn();
    }

    /** Send the turn as a move carrying on from where the unit stands, each model's arc as its route. */
    async function flushTurn() {
        const t = state.turning;
        if (!t) return;
        state.turning = null;
        clearTimeout(t.timer);
        const unit = unitById(t.unitId);
        const { spots, routes } = Core.playTurn(t.original, t.degrees, t.spins);
        const same = (p, i) => p.x === t.original[i].x && p.y === t.original[i].y && p.angle === (t.original[i].angle || 0);
        if (spots.every(same)) {
            message('');
            return;
        }
        const models = spots.map(p => Object.assign({ id: p.id, x: p.x, y: p.y, angle: p.angle },
                                                    routes[p.id].length ? { route: routes[p.id] } : {}));
        const mode = state.selected === unit.id ? state.moveMode : 'normal';
        const ok = await send(Object.assign({ type: 'move', unit: unit.id, mode, models, waypoint: true }, flying(unit) ? { skies: true } : {}));
        if (!ok) {  // Put it back as it was
            const before = Object.fromEntries(t.original.map(p => [p.id, p]));
            unitById(t.unitId).models.forEach(m => {
                if (before[m.id]) m.position = { x: before[m.id].x, y: before[m.id].y, angle: before[m.id].angle || 0 };
            });
            render();
        }
    }

    // --- The hover card: a unit's datasheet (and its leaders') after a short pause over one of its models ---

    const CARD_DELAY = 500;  // ms
    const abilityCache = {};  // 'bsdata|unit' -> /api/play/abilities answer (or its promise)
    let cardTimer = null, cardUnit = null;

    function hoverModel(event) {
        const target = event.target.closest('[data-model]');
        if (!target || state.drag || state.pan || state.measure) return;
        const unitId = target.dataset.unit;
        if (unitId === cardUnit) return;
        hideCard();
        const at = { x: event.clientX, y: event.clientY };
        cardTimer = setTimeout(() => showCard(unitId, at), CARD_DELAY);
    }

    function hideCard() {
        clearTimeout(cardTimer);
        cardTimer = null;
        cardUnit = null;
        const card = $('play-card');
        if (card) card.hidden = true;
    }

    async function showCard(unitId, at) {
        const unit = unitById(unitId);
        if (!unit || state.drag || state.pan || state.measure) return;
        cardUnit = unitId;
        let card = $('play-card');
        if (!card) {
            card = document.createElement('div');
            card.id = 'play-card';
            card.className = 'play-card';
            card.setAttribute('role', 'tooltip');
            document.body.appendChild(card);
        }
        const key = part => `${part.bsdata_id || ''}|${part.unit_id || ''}`;
        const draw = () => {
            if (cardUnit !== unitId) return;
            card.classList.toggle('wide', unit.parts.length > 1);  // Its leaders beside it, not below
            card.innerHTML = Core.playUnitCard(unit, unit.parts.map(p => abilityCache[key(p)] instanceof Promise ? undefined : abilityCache[key(p)]));
            card.hidden = false;
            // Beside the pointer, kept on screen
            const box = card.getBoundingClientRect(), gap = 16;
            const left = at.x + gap + box.width > innerWidth ? Math.max(8, at.x - gap - box.width) : at.x + gap;
            card.style.left = `${left}px`;
            card.style.top = `${Math.max(8, Math.min(at.y - 20, innerHeight - box.height - 8))}px`;
        };
        draw();
        const missing = unit.parts.filter(p => !(key(p) in abilityCache));
        missing.forEach(p => {
            abilityCache[key(p)] = fetch(`/api/play/abilities?bsdata=${encodeURIComponent(p.bsdata_id || '')}&unit=${p.unit_id || ''}`)
                .then(r => r.json()).catch(() => ({ found: false, abilities: [], rules: [] }))
                .then(answer => { abilityCache[key(p)] = answer; });
        });
        await Promise.all(unit.parts.map(p => abilityCache[key(p)]).filter(v => v instanceof Promise));
        draw();
    }

    // --- The Measure tool: two clicks, the distance between them (from a model's base edge if one is clicked) ---

    function toggleMeasure(on = !state.measure) {
        hideCard();
        state.measure = on ? { from: null, to: null, distance: null, fixed: false } : null;
        $('measure-btn').classList.toggle('active', on);
        $('play-board').classList.toggle('measuring', on);
        if (on) message('Measure: click a point or a model, then another. Esc or the ruler button to stop.');
        else message('');
        drawMeasure();
    }

    /** What's under the pointer, as a Measure end: a model's base (by its outline) or a point on the board. */
    function measureEnd(event) {
        const target = event.target.closest('[data-model]');
        if (target) {
            const unit = unitById(target.dataset.unit), model = unit.models.find(m => m.id === target.dataset.model);
            if (model && model.position) {
                return { x: model.position.x, y: model.position.y, angle: model.position.angle || 0,
                         outline: state.game.outlines[unit.id][model.part], name: unit.name };
            }
        }
        const p = boardPoint(event);
        return { x: Math.round(p.x * 100) / 100, y: Math.round(p.y * 100) / 100 };
    }

    function measureClick(event) {
        const end = measureEnd(event), m = state.measure;
        if (!m.from || m.fixed) {
            Object.assign(m, { from: end, to: null, distance: null, fixed: false });
            drawMeasure();
        } else {
            m.fixed = true;
            measureTo(end);
        }
    }

    /** Measure to `end` and show it; only the latest answer is kept, and one request at a time goes to the server. */
    async function measureTo(end) {
        const m = state.measure;
        m.to = end;
        drawMeasure();
        if (m.asking) { m.again = true; return; }
        m.asking = true;
        try {
            do {
                m.again = false;
                const ends = [m.from, m.to].map(e => ({ x: e.x, y: e.y, angle: e.angle, outline: e.outline }));
                const resp = await fetch('/api/play/measure', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ends }) });
                if (state.measure !== m) return;
                const data = await resp.json();
                if (!m.again) m.distance = resp.ok ? data.distance : null;
            } while (m.again);
        } finally {
            m.asking = false;
        }
        drawMeasure();
    }

    function drawMeasure() {
        const svg = $('play-board');
        const old = svg.querySelector('.play-measure');
        if (old) old.remove();
        const m = state.measure;
        if (!m || !m.from || !state.game) return;
        const D = state.game.board.depth;
        const g = svgEl('g', { class: 'play-measure' }, svg);
        svgEl('circle', { cx: m.from.x, cy: D - m.from.y, r: 0.15 }, g);
        if (!m.to) return;
        svgEl('line', { x1: m.from.x, y1: D - m.from.y, x2: m.to.x, y2: D - m.to.y }, g);
        svgEl('circle', { cx: m.to.x, cy: D - m.to.y, r: 0.15 }, g);
        if (m.distance == null) return;
        const bases = m.from.outline && m.to.outline ? ' base to base' : m.from.outline || m.to.outline ? ' from the base' : '';
        const text = svgEl('text', { x: (m.from.x + m.to.x) / 2, y: D - (m.from.y + m.to.y) / 2 - 0.4 }, g);
        text.textContent = `${m.distance.toFixed(1)}"${bases}`;
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

    const BORDER_PAD = 0.4;  // Inches round each base
    let borderCount = 0;

    /** A model's base grown by `pad` inches, as an SVG shape in `parent` placed and turned like the model. */
    function padded(parent, unit, model, depth, pad, attrs) {
        const o = state.game.outlines[unit.id][model.part];
        const transform = `translate(${model.position.x} ${depth - model.position.y}) rotate(${-(model.position.angle || 0)})`;
        if (o.shape === 'rect') {
            return svgEl('rect', { ...attrs, transform, x: -o.length / 2 - pad, y: -o.width / 2 - pad,
                                   width: o.length + 2 * pad, height: o.width + 2 * pad, rx: pad }, parent);
        }
        return svgEl('ellipse', { ...attrs, transform, rx: o.length / 2 + pad, ry: o.width / 2 + pad }, parent);
    }

    /**
     * A unit's border: round each model's base, BORDER_PAD out, joined where they meet (a split unit shows as
     * islands). Each padded base is outlined, and a mask hides the outline wherever it runs inside the unit's
     * padded bases, so only the edge of them all is left (the outline's outer half: the styles draw it double width).
     */
    /** The unit arriving from reserves, if any: being set up now, or chosen to arrive. */
    function arrivingUnit() {
        const turn = state.game.state.turn;
        if (state.arriving) return unitById(state.arriving.unitId);
        if (state.placing && state.placing.kind) return unitById(state.placing.unitId);
        const unit = state.selected && unitById(state.selected);
        return unit && turn.phase === 'movement' && unit.player === turn.player && unit.activity.moved === 'ingress' ? unit : null;
    }

    /** Shade where a unit arriving from reserves can be set up (the server's ingress rules: game.ingress). */
    function drawIngress(svg, depth) {
        const turn = state.game.state.turn;
        const placingUnit = state.placing && turn.phase === 'deployment' && unitById(state.placing.unitId);
        if (placingUnit && (state.game.infiltrators || []).includes(placingUnit.id)) return drawInfiltrate(svg, depth, placingUnit);
        const unit = arrivingUnit();
        if (unit && state.placing && state.placing.kind === 'scout') return;  // Its own zone, as in deployment
        const leaving = unit && (state.game.disembark || {})[unit.id];
        if (leaving && ((state.arriving && state.arriving.kind === 'disembark') || (state.placing && state.placing.kind === 'disembark'))) {
            return drawDisembark(svg, depth, unit, leaving);
        }
        const rules = unit && (state.game.ingress || [])[unit.player];
        if (!rules) return;
        const W = state.game.board.width, edge = rules.edge;
        const id = 'play-ingress-mask';
        const mask = svgEl('mask', { id, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: W, height: depth }, svg);
        svgEl('rect', { x: 0, y: 0, width: W, height: depth, fill: 'white' }, mask);
        if (!rules.deep_strike.includes(unit.id)) {  // Only the band along the edges
            svgEl('rect', { x: edge, y: edge, width: W - 2 * edge, height: depth - 2 * edge, fill: 'black' }, mask);
        }
        if (rules.enemy_zone) {
            svgEl('polygon', { points: rules.enemy_zone.map(([x, y]) => `${x},${depth - y}`).join(' '), fill: 'black' }, mask);
        }
        for (const enemy of state.game.state.units.filter(u => u.player !== unit.player)) {
            for (const model of enemy.models.filter(m => m.alive && m.position)) padded(mask, enemy, model, depth, rules.gap, { fill: 'black' });
        }
        svgEl('rect', { x: 0, y: 0, width: W, height: depth, class: 'play-ingress', mask: `url(#${id})` }, svg);
    }

    /** Shade where a unit can disembark: within the move's reach of its transport, out of engagement range (but a
     * Combat disembark may engage what the transport is engaged with: not shaded out). */
    function drawDisembark(svg, depth, unit, leaving) {
        const W = state.game.board.width;
        const id = 'play-ingress-mask';
        const mask = svgEl('mask', { id, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: W, height: depth }, svg);
        const transport = unitById(leaving.transport);
        for (const model of transport.models.filter(m => m.alive && m.position)) padded(mask, transport, model, depth, leaving.reach, { fill: 'white' });
        if (leaving.mode !== 'combat') {
            for (const enemy of state.game.state.units.filter(u => u.player !== unit.player)) {
                for (const model of enemy.models.filter(m => m.alive && m.position)) padded(mask, enemy, model, depth, 2, { fill: 'black' });
            }
        }
        svgEl('rect', { x: 0, y: 0, width: W, height: depth, class: 'play-ingress', mask: `url(#${id})` }, svg);
    }

    /** Shade where an Infiltrators unit can be set up outside its zone: more than 8" from the enemy zone and enemy units. */
    function drawInfiltrate(svg, depth, unit) {
        const W = state.game.board.width, gap = 8;
        const id = 'play-ingress-mask';
        const mask = svgEl('mask', { id, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: W, height: depth }, svg);
        svgEl('rect', { x: 0, y: 0, width: W, height: depth, fill: 'white' }, mask);
        // The enemy zone grown by 8": its polygon filled, with a round-cornered 16"-wide outline
        svgEl('polygon', { points: state.game.zones[1 - unit.player].map(([x, y]) => `${x},${depth - y}`).join(' '),
                           fill: 'black', stroke: 'black', 'stroke-width': 2 * gap, 'stroke-linejoin': 'round' }, mask);
        for (const enemy of state.game.state.units.filter(u => u.player !== unit.player)) {
            for (const model of enemy.models.filter(m => m.alive && m.position)) padded(mask, enemy, model, depth, gap, { fill: 'black' });
        }
        svgEl('rect', { x: 0, y: 0, width: W, height: depth, class: 'play-ingress', mask: `url(#${id})` }, svg);
    }

    function drawBorder(g, unit, placed, depth) {
        const id = `play-border-${++borderCount}`;
        const W = state.game.board.width, margin = 20;
        const area = { x: -margin, y: -margin, width: W + 2 * margin, height: depth + 2 * margin };
        const mask = svgEl('mask', { id, maskUnits: 'userSpaceOnUse', ...area }, g);
        svgEl('rect', { ...area, fill: 'white' }, mask);
        const border = svgEl('g', { class: 'play-unit-border', mask: `url(#${id})` }, g);
        for (const model of placed) {
            padded(mask, unit, model, depth, BORDER_PAD, { fill: 'black' });
            padded(border, unit, model, depth, BORDER_PAD, { class: 'play-unit-outline' });
        }
    }

    function drawModel(g, unit, model, depth) {
        const outline = state.game.outlines[unit.id][model.part];
        const x = model.position.x, y = depth - model.position.y;
        const angle = -(model.position.angle || 0);  // Anticlockwise on the board is clockwise in SVG
        const leader = unit.parts[model.part].role === 'leader';
        const attrs = { class: `play-model${outline.estimated ? ' estimated' : ''}${leader ? ' leader' : ''}`,
                        'data-unit': unit.id, 'data-model': model.id, transform: `translate(${x} ${y}) rotate(${angle})` };
        const base = (a, pad = 0) => outline.shape === 'rect'
            ? svgEl('rect', { ...a, x: -outline.length / 2 - pad, y: -outline.width / 2 - pad,
                              width: outline.length + 2 * pad, height: outline.width + 2 * pad }, g)
            : svgEl('ellipse', { ...a, rx: outline.length / 2 + pad, ry: outline.width / 2 + pad }, g);
        if (leader) {  // The leader: a gold ring round its base, under it
            base({ class: 'play-leader-ring', 'data-follow': model.id, 'data-turn': 1, transform: attrs.transform }, 0.16);
        }
        const el = base(attrs);
        if (leader) {
            const title = svgEl('title', {}, el);
            title.textContent = `${unit.parts[model.part].name}, leading ${unit.name}`;
            // ...and a crown on it, kept upright
            const size = Math.min(outline.length, outline.width) * 0.28;
            const crown = [[-1, 0.6], [-1, -0.45], [-0.5, 0.1], [0, -0.7], [0.5, 0.1], [1, -0.45], [1, 0.6]];
            svgEl('polygon', { class: 'play-leader-crown', 'data-follow': model.id, transform: `translate(${x} ${y})`,
                               points: crown.map(([cx, cy]) => `${cx * size},${cy * size}`).join(' ') }, g);
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
        if (!state.game) return;
        hideCard();
        const target = event.target.closest('[data-model]');
        if (state.measure && event.button === 0) return measureClick(event);
        if (event.button === 1 || event.button === 2 || (event.button === 0 && !target)) {
            // Pan: left-drag on empty board, or middle- or right-drag anywhere. A left click without dragging still places.
            if (!state.view) fitView();
            state.pan = { x: event.clientX, y: event.clientY, view: Object.assign({}, state.view), click: event.button === 0,
                point: boardPoint(event), moved: false };
            $('play-board').setPointerCapture(event.pointerId);
            return;
        }
        if (event.button !== 0) return;
        if (state.turning) return flushTurn();  // Send the turn before anything else moves
        const unit = unitById(target.dataset.unit);
        const modelIds = event.shiftKey ? [target.dataset.model] : unit.models.filter(m => m.alive && m.position).map(m => m.id);
        state.drag = { unitId: unit.id, modelIds, start: boardPoint(event), dx: 0, dy: 0, trail: [{ x: 0, y: 0 }] };
        $('play-board').setPointerCapture(event.pointerId);
    }

    function panTo(event) {
        const pan = state.pan;
        const px = event.clientX - pan.x, py = event.clientY - pan.y;
        if (!pan.moved && Math.hypot(px, py) < 4) return;  // Not yet: it may be a click
        pan.moved = true;
        const svg = $('play-board'), box = svg.getBoundingClientRect();
        const scale = Math.max(pan.view.w / box.width, pan.view.h / box.height);  // SVG units per pixel
        state.view = Core.playPan(pan.view, -px * scale, -py * scale, state.game.board);
        svg.setAttribute('viewBox', `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`);
        svg.classList.add('panning');
    }

    function pointerMove(event) {
        if (state.pan) return panTo(event);
        if (state.measure && !state.measure.fixed && state.measure.from) return measureTo(measureEnd(event));
        if (!state.drag) return;
        const p = boardPoint(event);
        state.drag.dx = p.x - state.drag.start.x;
        state.drag.dy = p.y - state.drag.start.y;
        const trail = state.drag.trail, last = trail[trail.length - 1];
        if (Math.hypot(state.drag.dx - last.x, state.drag.dy - last.y) >= 0.1) trail.push({ x: state.drag.dx, y: state.drag.dy });
        const unit = unitById(state.drag.unitId);
        const bends = dragBends(state.drag);
        // The farthest any dragged model has come from where it started the phase, against its allowance
        let label = null;
        for (const id of state.drag.modelIds) {
            const model = unit.models.find(m => m.id === id);
            const reach = allowance(unit, model);
            if (reach == null) continue;
            const onward = waypointing(unit);  // Measured along the path so far, then on from here
            const from = onward ? model.position : startOf(unit, model);
            const before = onward ? Core.playPathLength(pathOf(unit, model)) : 0;
            const route = routeOf(model, bends);
            const via = route.length ? before + Core.playPathLength([from, ...route]) : before;
            const bend = route.length ? route[route.length - 1] : from;
            const here = Core.playMoveLabel(model.position.x + state.drag.dx - bend.x, model.position.y + state.drag.dy - bend.y, reach, via);
            if (!label || parseFloat(here.text) > parseFloat(label.text) || here.over) label = here;
        }
        showReadout(event, label);
        if (state.drag.modelIds.length === unit.models.filter(m => m.alive && m.position).length) {
            // The whole unit: its outline and name go with it (SVG y runs down)
            const group = $('play-board').querySelector(`.play-unit-group[data-unit="${unit.id}"]`);
            group.querySelectorAll('.play-unit-border, .play-label').forEach(el =>
                el.setAttribute('transform', `translate(${state.drag.dx} ${-state.drag.dy})`));
        }
        for (const id of state.drag.modelIds) {
            const el = $('play-board').querySelector(`.play-model[data-model="${id}"]`);
            const model = unitById(state.drag.unitId).models.find(m => m.id === id);
            const x = model.position.x + state.drag.dx, y = state.game.board.depth - (model.position.y + state.drag.dy);
            el.setAttribute('transform', `translate(${x} ${y}) rotate(${-(model.position.angle || 0)})`);
            $('play-board').querySelectorAll(`[data-follow="${id}"]`).forEach(mark =>  // The leader's ring and crown
                mark.setAttribute('transform', `translate(${x} ${y})${mark.dataset.turn ? ` rotate(${-(model.position.angle || 0)})` : ''}`));
            const badge = $('play-board').querySelector(`[data-badge="${id}"]`);
            if (badge) {
                const o = state.game.outlines[unit.id][model.part];
                badge.setAttribute('transform', `translate(${x + o.length * 0.36} ${y - o.width * 0.36})`);
            }
        }
    }

    /** The bends of the path dragged so far (offsets from where the drag began), in the phases where moves follow it. */
    function dragBends(drag) {
        const phase = state.game.state.turn.phase;
        if (phase !== 'movement' && phase !== 'charge' && phase !== 'prebattle') return [];  // Pile in and Consolidate go in a straight line
        return Core.playRouteBends([...drag.trail, { x: drag.dx, y: drag.dy }]);
    }

    /** A dragged model's route: the drag's bends from where it stood. */
    function routeOf(model, bends) {
        const round = v => Math.round(v * 100) / 100;
        return bends.map(b => ({ x: round(model.position.x + b.x), y: round(model.position.y + b.y) }));
    }

    async function pointerUp() {
        if (state.pan) {
            const pan = state.pan;
            state.pan = null;
            $('play-board').classList.remove('panning');
            if (!pan.moved && pan.click && state.arriving) placeArrivalAt(pan.point);
            else if (!pan.moved && pan.click && state.placing && state.placing.kind) {  // Moved to where it was clicked
                const unit = unitById(state.placing.unitId);
                state.arriving = { unitId: unit.id, kind: state.placing.kind, player: state.placing.player };
                const { original } = state.placing;
                unit.models.forEach(m => { m.position = original[m.id] || null; });
                state.placing = null;
                placeArrivalAt(pan.point);
            } else if (!pan.moved && pan.click && state.selected && state.game.state.turn.phase === 'deployment') placeSelectedAt(pan.point);
            return;
        }
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
        const settingUp = state.placing && state.placing.unitId === unit.id;  // Deployment, or arriving from reserves
        if (turn.phase === 'movement' && !settingUp && unit.activity.moved === 'ingress' && unit.player === turn.player) {
            // Arrived this phase: set it up again where it was dragged (the server checks the ingress rules again)
            await send({ type: 'ingress', unit: unit.id, models: unit.models.filter(m => m.alive).map(m => ({
                id: m.id, angle: m.position.angle || 0,
                x: round(m.position.x + (moved.has(m.id) ? drag.dx : 0)), y: round(m.position.y + (moved.has(m.id) ? drag.dy : 0)) })) });
            return;
        }
        if (turn.phase !== 'deployment' && !settingUp) {
            const bends = dragBends(drag);
            const models = unit.models.filter(m => m.alive).map(m => Object.assign({
                id: m.id, x: round(m.position.x + (moved.has(m.id) ? drag.dx : 0)),
                y: round(m.position.y + (moved.has(m.id) ? drag.dy : 0)), angle: m.position.angle || 0,
            }, moved.has(m.id) && bends.length ? { route: routeOf(m, bends) } : {}));
            if (turn.phase === 'prebattle') {
                if (await send({ type: 'scout', unit: unit.id, models })) message(`${unit.name} made a scout move.`);
            } else if (turn.phase === 'movement') {
                const mode = state.selected === unit.id ? state.moveMode : 'normal';
                await send(Object.assign({ type: 'move', unit: unit.id, mode, models }, waypointing(unit) ? { waypoint: true } : {},
                    flying(unit) ? { skies: true } : {}));
            } else if (turn.phase === 'charge') {
                const targets = state.charge && state.charge.unit === unit.id ? state.chargeTargets : [];
                if (await send(Object.assign({ type: 'charge_move', unit: unit.id, targets, models }, flying(unit) ? { skies: true } : {}))) loadOptions();
            } else if (turn.phase === 'fight' && (turn.step === 'pile_in' || turn.step === 'consolidate')) {
                await send({ type: turn.step, unit: unit.id, models });
            } else if (turn.phase === 'fight' && turn.step === 'fight') {  // An overrun fight's pile-in
                if (await send({ type: 'overrun', unit: unit.id, models })) {
                    clearOptions();
                    loadOptions();
                }
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
        const over = event.shiftKey && event.target.closest('[data-model]');
        if (over) {  // Shift + wheel spins a model on the spot (Shift turns the wheel sideways in some browsers)
            const unit = unitById(over.dataset.unit);
            if (unit && canTurn(unit)) spinModel(unit, over.dataset.model, (event.deltaY || event.deltaX) > 0 ? -15 : 15);
            return;
        }
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
