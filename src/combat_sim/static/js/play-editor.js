/**
 * Play page, Battlefields tab: the battlefield editor. Built-in deployment maps are shown read-only (save a
 * copy to change one); saved battlefields are edited and saved through /api/play/battlefields, which checks
 * them and makes objectives under terrain into terrain objectives. Terrain pieces and objectives are dragged
 * on the board, snapping to half inches; the side panel adds, turns and changes pieces.
 */

(function() {
    'use strict';

    const { escapeHtml } = Core;
    const SVG = 'http://www.w3.org/2000/svg';
    const W = 60, D = 44, SNAP = 0.5;
    const CATEGORIES = ['exposed', 'light', 'dense'];
    const KIND_LETTER = { home: 'H', central: 'C', expansion: 'E' };

    const editor = {
        list: [],  // /api/play/battlefields
        footprints: {},  // name -> its corners from its own corner (/api/play/footprints)
        key: null,  // The battlefield open ('builtin:...' or 'saved:...')
        data: null,  // Its editable copy
        source: null,  // 'builtin' or 'custom'
        dirty: false,
        selected: null,  // Index of the selected terrain piece
        drag: null,  // {kind: 'terrain'|'objective', index, start, origin}
    };
    const $ = id => document.getElementById(id);

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        document.querySelectorAll('.play-tabs [data-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
        $('bf-import').addEventListener('change', importFile);
        const board = $('bf-board');
        board.addEventListener('pointerdown', pointerDown);
        board.addEventListener('pointermove', pointerMove);
        board.addEventListener('pointerup', pointerUp);
        editor.footprints = await fetch('/api/play/footprints').then(r => r.json()).catch(() => ({}));
        await loadList();
        draw();
    }

    function showTab(tab) {
        document.querySelectorAll('.play-tabs [data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
        $('tab-games').hidden = tab !== 'games';
        $('tab-battlefields').hidden = tab !== 'battlefields';
        if (tab === 'battlefields' && !editor.key && editor.list.length) open(editor.list[0].key);
    }

    async function loadList() {
        editor.list = await fetch('/api/play/battlefields').then(r => r.json()).catch(() => []);
        const groups = Core.playBattlefieldGroups(editor.list);
        const button = b => `<button class="play-game-open ${b.key === editor.key ? 'active' : ''}" data-key="${b.key}">${escapeHtml(b.label)}
            ${b.kind === 'saved' && b.terrain ? `<small>${b.terrain} terrain</small>` : ''}</button>`;
        const layouts = groups.filter(g => g.kind === 'layout');
        const expanded = layouts.some(g => g.items.some(b => b.key === editor.key));
        const plain = kind => groups.filter(g => g.kind === kind).map(g =>
            `<h4 class="play-bf-group">${escapeHtml(g.label)}</h4>${g.items.map(button).join('')}`).join('');
        $('bf-list').innerHTML = plain('map')
            + (layouts.length ? `<details class="play-bf-layouts" ${expanded ? 'open' : ''}><summary>Event Companion layouts</summary>
                ${layouts.map(g => `<div class="play-bf-pairing"><span>${escapeHtml(g.label)}</span>
                    <span class="play-bf-letters">${g.items.map(b => `<button class="btn-small ${b.key === editor.key ? 'active' : ''}"
                        data-key="${b.key}" title="${escapeHtml(b.name)}">${escapeHtml(b.label.replace('Layout ', ''))}</button>`).join('')}</span></div>`).join('')}
            </details>` : '')
            + plain('saved');
        $('bf-list').querySelectorAll('[data-key]').forEach(b => b.addEventListener('click', () => open(b.dataset.key)));
        document.dispatchEvent(new CustomEvent('battlefields-changed', { detail: editor.list }));
    }

    async function open(key) {
        if (editor.dirty && !confirm('Discard the changes to this battlefield?')) return;
        const resp = await fetch(`/api/play/battlefields/${key}`);
        if (!resp.ok) return;
        const data = await resp.json();
        editor.key = key;
        editor.source = data.source;
        editor.data = { name: data.name, zones: data.zones, divider: data.divider, objectives: data.objectives, terrain: data.terrain,
                        fixed_objectives: !!data.fixed_objectives };
        editor.dirty = false;
        editor.selected = null;
        message('');
        await loadList();
        render();
    }

    // --- Saving ---------------------------------------------------------------------------------------

    async function save(asCopy) {
        const builtin = editor.source === 'builtin';
        const body = { ...editor.data };
        if (asCopy || builtin) body.name = $('bf-name').value.trim() || `Copy of ${editor.data.name}`;
        else body.name = $('bf-name').value.trim();
        const creating = asCopy || builtin;
        const resp = await fetch(creating ? '/api/play/battlefields' : `/api/play/battlefields/${editor.key}`, {
            method: creating ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) return message(data.error || 'It could not be saved.', true);
        editor.dirty = false;
        await loadList();
        await open(data.key);
        message('Saved.');
    }

    async function remove() {
        if (!confirm(`Delete ${editor.data.name}? Games played on it keep their own copy.`)) return;
        await fetch(`/api/play/battlefields/${editor.key}`, { method: 'DELETE' });
        editor.key = null;
        editor.data = null;
        editor.dirty = false;
        await loadList();
        render();
    }

    function exportFile() {
        const blob = new Blob([JSON.stringify(editor.data, null, 2)], { type: 'application/json' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `${editor.data.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase() || 'battlefield'}.json`;
        link.click();
        URL.revokeObjectURL(link.href);
    }

    async function importFile(event) {
        const file = event.target.files[0];
        event.target.value = '';
        if (!file) return;
        let body;
        try {
            body = JSON.parse(await file.text());
        } catch (e) {
            return message('That file isn\'t a battlefield (not JSON).', true);
        }
        const resp = await fetch('/api/play/battlefields', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) return message(data.error || 'That battlefield could not be imported.', true);
        showTab('battlefields');
        await open(data.key);
        message(`Imported ${data.name}.`);
    }

    // --- Changing it ----------------------------------------------------------------------------------

    function changed() {
        editor.dirty = true;
        render();
    }

    function addPiece() {
        const piece = { id: `t${Date.now() % 100000}`, footprint: $('bf-new-footprint').value, x: 27, y: 20, angle: 0,
                        category: $('bf-new-category').value, height: Number($('bf-new-height').value) || 0 };
        editor.data.terrain.push(piece);
        editor.selected = editor.data.terrain.length - 1;
        changed();
    }

    /** A footprint's name ('triangle' is the polygon's name in battlefields saved before it). */
    function shapeOf(name) {
        return name === 'triangle' ? 'polygon' : name;
    }

    function setPiece(field, value) {
        const piece = editor.data.terrain[editor.selected];
        piece[field] = ['x', 'y', 'angle', 'height'].includes(field) ? Number(value) || 0 : field === 'flip' ? !!value : value;
        changed();
    }

    // --- Drawing ---------------------------------------------------------------------------------------

    function render() {
        renderTools();
        draw();
        $('bf-title').textContent = editor.data ? `${editor.data.name}${editor.dirty ? ' (not saved)' : ''}` : 'Choose a battlefield';
    }

    function renderTools() {
        const tools = $('bf-tools');
        if (!editor.data) {
            tools.innerHTML = '<p class="empty-message">No battlefield open</p>';
            return;
        }
        const builtin = editor.source === 'builtin';
        const piece = editor.selected != null ? editor.data.terrain[editor.selected] : null;
        const option = (values, current) => values.map(v => `<option value="${v}" ${v === current ? 'selected' : ''}>${v}</option>`).join('');
        tools.innerHTML = `
            <h3>Battlefield</h3>
            <div class="form-group"><label for="bf-name">Name</label>
                <input type="text" id="bf-name" class="filter-input" maxlength="200" value="${escapeHtml(builtin ? `Copy of ${editor.data.name}` : editor.data.name)}"></div>
            <div class="play-editor-row">
                ${builtin ? '' : '<button class="btn btn-primary" id="bf-save"><i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Save</button>'}
                <button class="btn-small" id="bf-copy"><i class="fa-solid fa-copy" aria-hidden="true"></i> Save as a copy</button>
                <button class="btn-small" id="bf-export"><i class="fa-solid fa-download" aria-hidden="true"></i> Export</button>
                ${builtin ? '' : '<button class="btn-small btn-danger" id="bf-delete">Delete</button>'}
            </div>
            <h3>Add terrain</h3>
            <div class="play-editor-grid">
                <label>Footprint <select id="bf-new-footprint" class="filter-select">${option(Object.keys(editor.footprints), 'medium')}</select></label>
                <label>Category <select id="bf-new-category" class="filter-select">${option(CATEGORIES, 'dense')}</select></label>
                <label>Height (") <input type="number" id="bf-new-height" class="filter-input" min="0" max="30" step="0.5" value="4"></label>
            </div>
            <button class="btn-small" id="bf-add"><i class="fa-solid fa-plus" aria-hidden="true"></i> Add terrain</button>
            ${piece ? `<h3>Selected terrain</h3>
            <div class="play-editor-grid">
                <label>x <input type="number" class="filter-input" step="0.5" data-piece="x" value="${piece.x}"></label>
                <label>y <input type="number" class="filter-input" step="0.5" data-piece="y" value="${piece.y}"></label>
                <label>Angle <input type="number" class="filter-input" step="15" data-piece="angle" value="${piece.angle}"></label>
                <label>Height <input type="number" class="filter-input" step="0.5" data-piece="height" value="${piece.height}"></label>
                <label>Category <select class="filter-select" data-piece="category">${option(CATEGORIES, piece.category)}</select></label>
                <label>Footprint <select class="filter-select" data-piece="footprint">${option(Object.keys(editor.footprints), piece.footprint)}</select></label>
            </div>
            <div class="play-editor-row">
                <button class="btn-small" data-turn="-15"><i class="fa-solid fa-rotate-left" aria-hidden="true"></i> 15°</button>
                <button class="btn-small" data-turn="15"><i class="fa-solid fa-rotate-right" aria-hidden="true"></i> 15°</button>
                ${shapeOf(piece.footprint) === 'polygon' ? `<button class="btn-small ${piece.flip ? 'active' : ''}" id="bf-flip"
                    title="Turn the footprint over"><i class="fa-solid fa-arrows-up-down" aria-hidden="true"></i> Turn over</button>` : ''}
                <button class="btn-small btn-danger" id="bf-remove">Remove</button>
            </div>` : '<p class="play-hint">Click a terrain piece to change it.</p>'}
            <p class="play-hint">${editor.data.terrain.length} terrain piece${editor.data.terrain.length === 1 ? '' : 's'} ·
                ${editor.data.objectives.filter(o => o.terrain).length} terrain objective(s)</p>`;
        $('bf-name').addEventListener('input', () => { if (!builtin) { editor.data.name = $('bf-name').value; editor.dirty = true; } });
        if ($('bf-save')) $('bf-save').addEventListener('click', () => save(false));
        $('bf-copy').addEventListener('click', () => save(true));
        $('bf-export').addEventListener('click', exportFile);
        if ($('bf-delete')) $('bf-delete').addEventListener('click', remove);
        $('bf-add').addEventListener('click', addPiece);
        tools.querySelectorAll('[data-piece]').forEach(el => el.addEventListener('change', () => setPiece(el.dataset.piece, el.value)));
        tools.querySelectorAll('[data-turn]').forEach(b => b.addEventListener('click', () =>
            setPiece('angle', (editor.data.terrain[editor.selected].angle + Number(b.dataset.turn) + 360) % 360)));
        if ($('bf-flip')) $('bf-flip').addEventListener('click', () => setPiece('flip', !piece.flip));
        if ($('bf-remove')) {
            $('bf-remove').addEventListener('click', () => {
                editor.data.terrain.splice(editor.selected, 1);
                editor.selected = null;
                changed();
            });
        }
    }

    function el(name, attrs, parent) {
        const node = document.createElementNS(SVG, name);
        Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
        if (parent) parent.appendChild(node);
        return node;
    }

    const flip = ([x, y]) => `${x},${D - y}`;

    function draw() {
        const svg = $('bf-board');
        svg.setAttribute('viewBox', `-1 -1 ${W + 2} ${D + 2}`);
        svg.innerHTML = '';
        el('rect', { x: 0, y: 0, width: W, height: D, class: 'play-field' }, svg);
        const data = editor.data;
        if (!data) return;
        el('polygon', { points: data.zones.attacker.map(flip).join(' '), class: 'play-zone play-p1' }, svg);
        el('polygon', { points: data.zones.defender.map(flip).join(' '), class: 'play-zone play-p2' }, svg);
        const grid = el('g', { class: 'play-grid' }, svg);
        for (let x = 1; x < W; x++) el('line', { x1: x, y1: 0, x2: x, y2: D, class: x % 6 ? '' : 'major' }, grid);
        for (let y = 1; y < D; y++) el('line', { x1: 0, y1: y, x2: W, y2: y, class: y % 6 ? '' : 'major' }, grid);
        const [a, b] = data.divider;
        el('line', { x1: a[0], y1: D - a[1], x2: b[0], y2: D - b[1], class: 'play-divider' }, svg);
        data.terrain.forEach((piece, i) => {
            const corners = editor.footprints[shapeOf(piece.footprint)] || [[0, 0], [6, 0], [6, 4], [0, 4]];
            const points = Core.playFootprintPoints(corners, piece.x, piece.y, piece.angle, piece.flip);
            el('polygon', { points: points.map(flip).join(' '), class: `play-terrain ${piece.category} ${i === editor.selected ? 'selected' : ''}`,
                            'data-terrain': i }, svg);
        });
        data.objectives.forEach((o, i) => {
            const g = el('g', { class: `play-objective neutral ${o.terrain ? 'on-terrain' : ''}`, transform: `translate(${o.x} ${D - o.y})`,
                                'data-objective': i }, svg);
            el('circle', { r: 3 + 20 / 25.4, class: 'play-objective-range' }, g);
            el('circle', { r: 20 / 25.4, class: 'play-objective-marker' }, g);
            const letter = el('text', { class: 'play-objective-letter', y: 0.02 }, g);
            letter.textContent = KIND_LETTER[o.kind] || 'O';
            const title = el('title', {}, g);
            title.textContent = `${o.kind}${o.role ? ` (${o.role})` : ''} objective${o.terrain ? ', a terrain objective' : ''}`;
        });
    }

    // --- Dragging pieces and objectives ------------------------------------------------------------------

    function point(event) {
        const svg = $('bf-board');
        const pt = svg.createSVGPoint();
        pt.x = event.clientX;
        pt.y = event.clientY;
        const p = pt.matrixTransform(svg.getScreenCTM().inverse());
        return { x: p.x, y: D - p.y };
    }

    function pointerDown(event) {
        if (!editor.data || editor.source === 'builtin') return;
        const terrain = event.target.closest('[data-terrain]');
        const objective = event.target.closest('[data-objective]');
        if (!terrain && !objective) {
            editor.selected = null;
            render();
            return;
        }
        const index = Number((terrain || objective).dataset[terrain ? 'terrain' : 'objective']);
        const item = terrain ? editor.data.terrain[index] : editor.data.objectives[index];
        editor.drag = { kind: terrain ? 'terrain' : 'objective', index, start: point(event), origin: { x: item.x, y: item.y } };
        if (terrain) editor.selected = index;
        $('bf-board').setPointerCapture(event.pointerId);
    }

    function pointerMove(event) {
        const drag = editor.drag;
        if (!drag) return;
        const p = point(event);
        const item = drag.kind === 'terrain' ? editor.data.terrain[drag.index] : editor.data.objectives[drag.index];
        item.x = Math.min(W, Math.max(0, Core.playSnap(drag.origin.x + p.x - drag.start.x, SNAP)));
        item.y = Math.min(D, Math.max(0, Core.playSnap(drag.origin.y + p.y - drag.start.y, SNAP)));
        editor.dirty = true;
        draw();
    }

    function pointerUp() {
        if (!editor.drag) return;
        editor.drag = null;
        render();
    }

    function message(text, isError = false) {
        $('bf-message').textContent = text || '';
        $('bf-message').classList.toggle('error', isError);
    }
})();
