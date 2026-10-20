// Unit tests for the browser helpers in src/combat_sim/static/js/core.js.
// Run: node --test tests/js   (also run by `python -m pytest tests` via tests/test_javascript.py)

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const Core = require(path.join(__dirname, '..', '..', 'src', 'combat_sim', 'static', 'js', 'core.js'));

// ---------------------------------------------------------------------------
// Text and formatting
// ---------------------------------------------------------------------------

test('escapeHtml escapes markup and quotes, and tolerates null', () => {
    assert.equal(Core.escapeHtml('<b>"Tom" & \'Jerry\'</b>'), '&lt;b&gt;&quot;Tom&quot; &amp; &#39;Jerry&#39;&lt;/b&gt;');
    assert.equal(Core.escapeHtml(null), '');
    assert.equal(Core.escapeHtml(3), '3');
});

test('truncate shortens long text with an ellipsis', () => {
    assert.equal(Core.truncate('Plague Marines', 20), 'Plague Marines');
    assert.equal(Core.truncate('Deathshroud Terminators', 10), 'Deathsh...');
    assert.equal(Core.truncate(null, 5), '');
});

test('formatMetricValue formats each matrix metric', () => {
    assert.equal(Core.formatMetricValue(2.456, 'avg_damage'), '2.46');
    assert.equal(Core.formatMetricValue(45.5, 'pct_destroyed'), '45.5%');
    assert.equal(Core.formatMetricValue(12, 'kill_probability'), '12%');
    assert.equal(Core.formatMetricValue(0.01234, 'damage_per_point'), '0.012');
    assert.equal(Core.formatMetricValue(null, 'pct_per_100'), '0.00');
});

test('getHeatColor runs red to green and clamps', () => {
    assert.equal(Core.getHeatColor(0, 0, 10), 'rgba(239, 68, 68, 0.7)');
    assert.equal(Core.getHeatColor(10, 0, 10), 'rgba(76, 175, 80, 0.7)');
    assert.equal(Core.getHeatColor(99, 0, 10), Core.getHeatColor(10, 0, 10));
    assert.equal(Core.getHeatColor(-5, 0, 10), Core.getHeatColor(0, 0, 10));
    assert.equal(Core.getHeatColor(4, 4, 4), 'rgba(255, 235, 59, 0.5)');
});

test('keywordHintHtml shows unknown entries and capitals, escaped', () => {
    assert.equal(Core.keywordHintHtml({ unknown: [], capitals: [] }), '');
    assert.equal(Core.keywordHintHtml(null), '');
    const html = Core.keywordHintHtml({ unknown: ['<Lethal Hitz>'], capitals: [['lethal hits', 'Lethal Hits']] });
    assert.match(html, /kw-hint-warn/);
    assert.match(html, /&lt;Lethal Hitz&gt;/);
    assert.match(html, /lethal hits → Lethal Hits/);
});

// ---------------------------------------------------------------------------
// Units, weapons and points
// ---------------------------------------------------------------------------

const intercessors = { unit_id: 1, unit_name: 'Intercessors' };
const hellblasters = { unit_id: 2, unit_name: 'Hellblasters' };

test('copyLabel numbers repeated units', () => {
    const list = [intercessors, hellblasters, intercessors, intercessors];
    assert.deepEqual(list.map((_, i) => Core.copyLabel(list, i)),
        ['Intercessors', 'Hellblasters', 'Intercessors #2', 'Intercessors #3']);
});

test('defenderLabel adds the model count', () => {
    const defenders = [{ ...hellblasters, model_count: 5 }, { ...hellblasters, model_count: 10 }];
    assert.equal(Core.defenderLabel(defenders, 0), 'Hellblasters (x5)');
    assert.equal(Core.defenderLabel(defenders, 1), 'Hellblasters #2 (x10)');
});

test('weaponVisible follows the Shooting / Both / Melee filter', () => {
    const gun = { weapon_type: 'ranged' };
    assert.equal(Core.weaponVisible(gun, 'both'), true);
    assert.equal(Core.weaponVisible(gun, 'ranged'), true);
    assert.equal(Core.weaponVisible(gun, 'melee'), false);
});

test('pointsFor / pointsLabel read the server points table', () => {
    const table = [null, 18, 36, 54, 72, 90];  // index = model count
    assert.equal(Core.pointsFor(table, 5), 90);
    assert.equal(Core.pointsFor(table, 6), null);
    assert.equal(Core.pointsFor(null, 5), null);
    assert.equal(Core.pointsLabel(table, 3), '54 pts');
    assert.equal(Core.pointsLabel([], 3), '');
});

test('scaleUnitSize scales weapon models from the loadout', () => {
    const atk = {
        default_models: 10, unit_size: 10,
        weapons: [{ base_qty: 9, models: 9 }, { base_qty: 1, models: 1 }, { models: 4 }],
    };
    Core.scaleUnitSize(atk, 5);
    assert.equal(atk.unit_size, 5);
    assert.deepEqual(atk.weapons.map(w => w.models), [5, 1, 2]);  // 4.5 rounds up; never below 1
    Core.scaleUnitSize(atk, 20);
    assert.deepEqual(atk.weapons.map(w => w.models), [18, 2, 8]);
    Core.scaleUnitSize(atk, 0);  // invalid size falls back to the datasheet size
    assert.equal(atk.unit_size, 10);

    const noDatasheet = { weapons: [{ models: 3 }] };
    Core.scaleUnitSize(noDatasheet, 7);
    assert.equal(noDatasheet.unit_size, 7);
    assert.equal(noDatasheet.weapons[0].models, 3);  // nothing to scale from
});

test('simulatorWeapons: loadout ticked by default_use, then options unticked', () => {
    const unit = {
        ranged_weapons: [
            { id: 1, name: 'Rifle - standard', qty: 4, default_use: true },
            { id: 2, name: 'Rifle - overcharge', qty: 4, default_use: false },
        ],
        ranged_options: [{ id: 3, name: 'Heavy laser' }],
        melee_weapons: [{ id: 4, name: 'Blade' }],
    };
    const weapons = Core.simulatorWeapons(unit);
    assert.deepEqual(weapons.ranged.map(w => [w.name, w.qty, w.use, !!w.option]), [
        ['Rifle - standard', 4, true, false], ['Rifle - overcharge', 4, false, false], ['Heavy laser', 1, false, true]]);
    assert.deepEqual(weapons.melee.map(w => [w.name, w.qty, w.use]), [['Blade', 1, true]]);
});

test('matchupWeapons: loadout entries then excluded options', () => {
    const unit = {
        ranged_weapons: [{ id: 1, type: 'ranged', name: 'Rifle', qty: 5, default_use: true, abilities: ['Assault'] }],
        melee_weapons: [{ id: 2, type: 'melee', name: 'Knife', qty: 5 }],
        melee_options: [{ id: 3, type: 'melee', name: 'Power axe' }],
    };
    const entries = Core.matchupWeapons(unit);
    assert.deepEqual(entries.map(e => [e.name, e.models, e.base_qty, e.include, e.option]), [
        ['Rifle', 5, 5, true, false], ['Knife', 5, 5, true, false], ['Power axe', 1, 1, false, true]]);
    assert.deepEqual(entries[0].abilities, ['Assault']);
});

test('detachmentOptionsHtml: faction-wide first, grouped by faction when mixed, escaped', () => {
    const one = Core.detachmentOptionsHtml([{ key: 'd1', name: 'Vanguard <A>', faction: 'Starfarers' }], 'd1');
    assert.match(one, /^<option value="">No detachment/);
    assert.match(one, /<option value="d1" selected>Vanguard &lt;A&gt;<\/option>/);
    assert.doesNotMatch(one, /optgroup/);
    const mixed = Core.detachmentOptionsHtml([
        { key: 'd1', name: 'Vanguard', faction: 'Starfarers' },
        { key: 'd2', name: 'Task Force', faction: 'Allies' },
    ], '');
    assert.match(mixed, /<option value="" selected>/);
    assert.match(mixed, /<optgroup label="Starfarers"><option value="d1">Vanguard<\/option><\/optgroup>/);
    assert.match(mixed, /<optgroup label="Allies">/);
});

test('formatRuleLine: bold, keywords in every BSData order, italics, ins', () => {
    const kw = word => `<strong><span class="rule-keyword">${word}</span></strong>`;
    assert.equal(Core.formatRuleLine('While a ^^**Character^^** model leads'), `While a ${kw('Character')} model leads`);
    assert.equal(Core.formatRuleLine('**^^Transport^^** and ^^**Mounted Daemon**^^ units'),
        `${kw('Transport')} and ${kw('Mounted Daemon')} units`);
    // A bold run holding two keywords: " or " stays bold but isn't a keyword
    assert.equal(Core.formatRuleLine('**^^Monster^^ or ^^Vehicle^^**'),
        `${kw('Monster')}<strong> or </strong>${kw('Vehicle')}`);
    assert.equal(Core.formatRuleLine('a ^^Blue Horror^^ model'), 'a <span class="rule-keyword">Blue Horror</span> model');
    assert.equal(Core.formatRuleLine('has **Stealth** and [^^Lance^^]'),
        'has <strong>Stealth</strong> and [<span class="rule-keyword">Lance</span>]');
    assert.equal(Core.formatRuleLine('*Can join even if led.*'), '<em>Can join even if led.</em>');
    assert.equal(Core.formatRuleLine('<ins>Or:</ins> heal 3'), '<ins>Or:</ins> heal 3');
    assert.equal(Core.formatRuleLine('roll 2*3 then **unclosed'), 'roll 2*3 then <strong>unclosed</strong>');
    // BSData typos: lone markers are dropped (real text: "keyword.*" then "*** Designer's Note:** *This means")
    assert.equal(Core.formatRuleLine('the Character keyword.*'), 'the Character keyword.');
    assert.equal(Core.formatRuleLine("*** Designer's Note:** *This means"), '<strong> Designer&#39;s Note:</strong> This means');
});

test('formatRuleLine escapes everything else', () => {
    assert.equal(Core.formatRuleLine('<script>x</script> **<b>**'), '&lt;script&gt;x&lt;/script&gt; <strong>&lt;b&gt;</strong>');
    assert.equal(Core.formatRuleLine('<ins onclick="x">Or</ins>'), '&lt;ins onclick=&quot;x&quot;&gt;Or&lt;/ins&gt;');
});

test('formatRuleText: paragraphs, line breaks, bullet lists and tables', () => {
    assert.equal(Core.formatRuleText('First line\nsecond line\n\nNext paragraph'),
        '<p>First line<br>second line</p><p>Next paragraph</p>');
    assert.equal(Core.formatRuleText('Select one:\n▪ +1 to hit\n■ **Stealth**\n- Or: heal'),
        '<p>Select one:</p><ul><li>+1 to hit</li><li><strong>Stealth</strong></li><li>Or: heal</li></ul>');
    assert.equal(Core.formatRuleText('**Size | Units**\nIncursion | 2\nStrike Force | 3'),
        '<table class="rule-table"><thead><tr><th><strong>Size</strong></th><th>Units</th></tr></thead>' +
        '<tbody><tr><td>Incursion</td><td>2</td></tr><tr><td>Strike Force</td><td>3</td></tr></tbody></table>');
    assert.equal(Core.formatRuleText('Iron Will: This model has **Feel No Pain 5+**.\n\nDeep Strike', { labels: true }),
        '<p><strong class="rule-label">Iron Will:</strong> This model has <strong>Feel No Pain 5+</strong>.</p><p>Deep Strike</p>');
    assert.equal(Core.formatRuleText('Iron Will: as above'), '<p>Iron Will: as above</p>');  // Only when asked
    assert.equal(Core.formatRuleText(''), '');
    assert.equal(Core.formatRuleText(null), '');
});

test('leaderBuffSummary: attacker buffs with phases, then target buffs', () => {
    assert.equal(Core.leaderBuffSummary([
        { applies_to: 'Attacker', phase: 'Shooting', modifier: 'Lethal Hits' },
        { applies_to: 'Attacker', phase: 'Both', modifier: 'Hit Modifier +1' },
        { applies_to: 'Target', phase: 'Both', modifier: 'Invulnerable Save 4+' },
    ]), 'Lethal Hits (shooting), Hit Modifier +1 · as a target: Invulnerable Save 4+');
    assert.equal(Core.leaderBuffSummary([]), '');
});

test('withLeaderWeapons adds the leader loadout, replacing a previous leader', () => {
    const unit = { ranged: [{ id: 1, name: 'Rifle', qty: 5, use: true }], melee: [] };
    const captain = { name: 'Captain', ranged_weapons: [{ id: 9, name: 'Pistol', qty: 1, default_use: true }],
                      melee_weapons: [{ id: 8, name: 'Sword' }] };
    const led = Core.withLeaderWeapons(unit, captain);
    assert.deepEqual(led.ranged.map(w => [w.name, w.qty, w.use, !!w.leader]), [['Rifle', 5, true, false], ['Pistol', 1, true, true]]);
    assert.equal(led.melee[0].leader_name, 'Captain');
    const other = Core.withLeaderWeapons(led, { name: 'Lieutenant', ranged_weapons: [], melee_weapons: [{ id: 7, name: 'Axe' }] });
    assert.deepEqual(other.ranged.map(w => w.name), ['Rifle']);
    assert.deepEqual(other.melee.map(w => w.name), ['Axe']);
    assert.deepEqual(Core.withLeaderWeapons(led, null).melee, []);  // Leader removed
    // Before the unit's wargear options
    const withOptions = Core.withLeaderWeapons({ ranged: [{ name: 'Rifle' }, { name: 'Plasma', option: true }], melee: [] }, captain);
    assert.deepEqual(withOptions.ranged.map(w => w.name), ['Rifle', 'Pistol', 'Plasma']);
});

test('withLeaderEntries and scaling: the leader is never scaled with the unit', () => {
    const atk = { default_models: 5, unit_size: 5, weapons: [
        { name: 'Rifle', weapon_id: 1, models: 5, base_qty: 5, include: true }] };
    atk.weapons = Core.withLeaderEntries(atk.weapons, { name: 'Captain', ranged_weapons: [{ id: 9, type: 'ranged', name: 'Pistol', qty: 1 }] });
    Core.scaleUnitSize(atk, 10);
    assert.deepEqual(atk.weapons.map(w => [w.name, w.models, !!w.leader]), [['Rifle', 10, false], ['Pistol', 1, true]]);
    assert.deepEqual(Core.withLeaderEntries(atk.weapons, null).map(w => w.name), ['Rifle']);
});

test('buildMatrixRequest carries leaders for attackers, their weapons and defenders', () => {
    const request = Core.buildMatrixRequest({
        attackers: [{ unit_id: 1, unit_name: 'Squad', leader_id: 9, default_models: 5, weapons: [
            { weapon_id: 1, weapon_type: 'ranged', name: 'Rifle', models: 5, include: true },
            { weapon_id: 2, weapon_type: 'ranged', name: 'Pistol', models: 1, include: true, leader: true, leader_name: 'Captain' }] }],
        defenders: [{ unit_id: 3, unit_name: 'Target', model_count: 5, leader_id: 4, leader_name: 'Champion' }],
        weaponTypeFilter: 'both', defenderMode: 'real', attackerTicked: [], defenderTicked: [],
    });
    assert.equal(request.attackers[0].leader_id, 9);
    assert.deepEqual(request.attackers[0].weapons.map(w => [w.label, w.leader_id]), [['Rifle', 9], ['Pistol (Captain)', 9]]);
    assert.equal(request.defenders[0].leader_id, 4);
    assert.equal(request.defenders[0].label, 'Target (x5) + Champion');
});

test('usedWeapons and weaponPayload build the Simulator request', () => {
    const weapons = {
        ranged: [{ name: 'Boltgun', use: true, qty: 3, attacks: '2', skill: 3, strength: 4, ap: 1, damage: '1' },
                 { name: 'Plasma', use: false }],
        melee: [{ name: 'Knife', use: true }],
    };
    assert.deepEqual(Core.usedWeapons(weapons, 'ranged').map(w => w.name), ['Boltgun']);
    assert.deepEqual(Core.usedWeapons(weapons, 'psychic'), []);
    assert.deepEqual(Core.weaponPayload(weapons.ranged[0], 'ranged'), {
        name: 'Boltgun', type: 'ranged', attacks: '2', skill: 3, strength: 4, ap: 1, damage: '1', abilities: [], models: 3,
    });
    assert.equal(Core.weaponPayload({ name: 'X', type: 'melee' }, 'ranged').type, 'melee');
    assert.equal(Core.weaponPayload({ name: 'X' }, 'ranged').models, 1);
});

// ---------------------------------------------------------------------------
// Rules and stratagems
// ---------------------------------------------------------------------------

const rules = [
    { key: 'rule:1', phase: 'Shooting' },
    { key: 'rule:2', phase: 'Fighting' },
    { key: 'stratagem:3', phase: 'Both' },
    { key: 'stratagem:4', phase: 'Other' },
];

test('ruleRowTitle says whether a rule counts and what it is conditional on', () => {
    assert.equal(Core.ruleRowTitle({has_effect: true}), 'Changes the calculations');
    assert.equal(Core.ruleRowTitle({has_effect: false, conditions: []}), 'No effect on the calculations (listed for reference)');
    assert.equal(Core.ruleRowTitle({has_effect: true, conditions: ['If your unit is on an objective', 'that targets a Monster']}),
        'Changes the calculations. Tick it only when: If your unit is on an objective; that targets a Monster');
});

test('rulesForFilter keeps the phases a weapon type can use', () => {
    const keys = filter => Core.rulesForFilter(rules, filter).map(e => e.key);
    assert.deepEqual(keys('ranged'), ['rule:1', 'stratagem:3']);
    assert.deepEqual(keys('melee'), ['rule:2', 'stratagem:3']);
    assert.deepEqual(keys('both'), ['rule:1', 'rule:2', 'stratagem:3']);  // "Other" never applies
    assert.deepEqual(Core.rulesForFilter(null, 'both'), []);
});

test('woundsMarker puts a line where the target wounds fall on the damage chart', () => {
    const one = Core.woundsMarker({starts: [0, 1, 2, 3, 4, 5], unit_wounds: 3}, 'grey');
    assert.equal(one.shapes[0].x0, 2.5);  // Left edge of the "3" bar
    assert.equal(one.annotations[0].text, "Unit's wounds (3)");
    const ranges = Core.woundsMarker({starts: [0, 3, 6, 9], unit_wounds: 6}, 'grey');
    assert.equal(ranges.shapes[0].x0, 1.5);  // Left edge of the "6-8" bar
    assert.deepEqual(Core.woundsMarker({starts: [0, 1, 2], unit_wounds: 11}, 'grey'), {shapes: [], annotations: []});
    assert.deepEqual(Core.woundsMarker(null, 'grey'), {shapes: [], annotations: []});
});

test('rulesSelection splits tick keys into rule and stratagem ids', () => {
    assert.deepEqual(Core.rulesSelection(new Set(['rule:1', 'stratagem:7', 'rule:12'])),
        { rule_ids: [1, 12], stratagem_ids: [7] });
    assert.deepEqual(Core.rulesSelection(undefined), { rule_ids: [], stratagem_ids: [] });
});

// ---------------------------------------------------------------------------
// Matchups request
// ---------------------------------------------------------------------------

function matchupsPage(overrides = {}) {
    const weapon = (id, type, include = true) => ({ weapon_id: id, weapon_type: type, name: `W${id}`, models: 2, include });
    return {
        attackers: [
            { ...intercessors, default_models: 5, unit_size: 10, weapons: [weapon(1, 'ranged'), weapon(2, 'melee'), weapon(3, 'ranged', false)] },
            { ...intercessors, default_models: 5, weapons: [weapon(4, 'melee')] },
            { ...hellblasters, weapons: [] },
        ],
        defenders: [{ ...hellblasters, model_count: 5, extra_modifiers: 'Stealth' }, { ...hellblasters, model_count: 10 }],
        conditions: { cover: true },
        weaponTypeFilter: 'both',
        defenderMode: 'real',
        includedProfiles: [{ id: 9, name: 'MEQ', models: 5 }],
        attackerTicked: new Set(['rule:1']),
        defenderTicked: new Set(['stratagem:2']),
        ...overrides,
    };
}

test('buildMatrixRequest: real defenders, included weapons, labels and rules', () => {
    const page = matchupsPage();
    const request = Core.buildMatrixRequest(page);

    assert.deepEqual(request.attackers.map(a => a.label), ['Intercessors', 'Intercessors #2']);  // unit without weapons dropped
    assert.deepEqual(request.attackers[0].weapons.map(w => w.weapon_id), [1, 2]);  // unticked weapon left out
    assert.equal(request.attackers[0].weapons[0].unit_id, 1);
    assert.equal(request.attackers[0].unit_size, 10);
    assert.equal(request.attackers[1].unit_size, 5);  // datasheet size when not set

    assert.deepEqual(request.defenders, [
        { unit_id: 2, model_count: 5, label: 'Hellblasters (x5)', extra_modifiers: 'Stealth', leader_id: null },
        { unit_id: 2, model_count: 10, label: 'Hellblasters #2 (x10)', extra_modifiers: '', leader_id: null },
    ]);
    assert.deepEqual(request.attacker_rules, { rule_ids: [1], stratagem_ids: [] });
    assert.deepEqual(request.defender_rules, { rule_ids: [], stratagem_ids: [2] });
    assert.equal(request.weapon_filter, 'both');

    request.conditions.cover = false;
    assert.equal(page.conditions.cover, true);  // the request has its own copy
});

test('buildMatrixRequest: weapon filter and generic profiles', () => {
    const shooting = Core.buildMatrixRequest(matchupsPage({ weaponTypeFilter: 'ranged' }));
    assert.deepEqual(shooting.attackers.map(a => a.weapons.map(w => w.weapon_id)), [[1]]);  // melee-only unit drops out

    const generic = Core.buildMatrixRequest(matchupsPage({ defenderMode: 'generic' }));
    assert.deepEqual(generic.defenders, [{ profile_id: 9, model_count: 5, label: 'MEQ' }]);
});

// ---------------------------------------------------------------------------
// Army Analysis panel
// ---------------------------------------------------------------------------

const text = html => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

const analysis = {
    summary: { units: 2, points: 325, targets: 2, wiped: 1, avg_pct_destroyed: 85, pct_per_100: 26.15,
               best_unit: { label: 'Deathshroud <Terminators>', pct_per_100: 46.67 }, weighted: false },
    gaps: [{ label: 'Rogal Dorn (x1)', models: 1, survivors: 0.3, pct_destroyed: 70 }],
    roles: [{ name: 'Anti-Horde', models: 10, kills: 20, score: 200 }, { name: 'Anti-Tank', models: 1, kills: 0.74, score: 74 }],
    coverage: { toughness: [3, 12], saves: [2, 3, 4], damage: [[10, 20, 30], [1, 2, 3]] },
    reverse: { enemies: ['Guardsmen (x10)'], rows: [{ label: 'Plague Marines', models: 10, cells: [25], total: 25 }],
               summary: { points: 65, wiped: 0, units: 1, avg_pct_destroyed: 25, biggest_threat: 'Guardsmen (x10)' } },
};

test('renderAnalysisSummary shows both armies and escapes names', () => {
    const html = Core.renderAnalysisSummary(analysis);
    assert.match(text(html), /1 of 2 Enemy units wiped in one turn/);
    assert.match(html, /Deathshroud &lt;Terminators&gt;/);
    assert.match(text(html), /all targets equal/);
    assert.match(text(html), /Opponent .*Guardsmen \(x10\) Biggest threat/);

    const generic = Core.renderAnalysisSummary({ ...analysis, summary: { ...analysis.summary, weighted: true }, reverse: null });
    assert.match(text(generic), /weighted by profile/);
    assert.doesNotMatch(generic, /Opponent/);
});

test('renderGaps lists survivors, or says the army wipes everything', () => {
    assert.match(text(Core.renderGaps(analysis)), /Rogal Dorn \(x1\) 1 0.3 70%/);
    assert.match(Core.renderGaps({ gaps: [] }), /can wipe every target/);
});

test('renderRoleScores marks wiped benchmarks', () => {
    const html = Core.renderRoleScores(analysis);
    assert.equal((html.match(/score-good/g) || []).length, 1);  // only the 200% role
    assert.match(html, /id="roles-chart"/);
    assert.match(Core.renderRoleScores({ roles: [] }), /No role benchmarks/);
});

test('renderCoverage draws one heat cell per Toughness x Save', () => {
    const html = Core.renderCoverage(analysis);
    assert.equal((html.match(/coverage-cell/g) || []).length, 6);
    assert.match(text(html), /T3 10.0 20.0 30.0 T12 1.0 2.0 3.0/);
});

test('renderReverse needs real units and lists my units', () => {
    assert.match(text(Core.renderReverse(analysis)), /Plague Marines 25% 25%/);
    assert.match(Core.renderReverse({ reverse: null }), /Real units/);
    assert.match(Core.renderReverse({ reverse: { enemies: [], rows: [] } }), /No attacker units/);
});

// ---------------------------------------------------------------------------
// Page state across tab switches
// ---------------------------------------------------------------------------

function memoryStorage() {
    const items = new Map();
    return { getItem: k => (items.has(k) ? items.get(k) : null), setItem: (k, v) => items.set(k, String(v)) };
}

test('createPageState restores only within the same app run', () => {
    const storage = memoryStorage();
    Core.createPageState(storage, 'run-1').save('simulator', { unit: 'Intercessors' });
    assert.deepEqual(Core.createPageState(storage, 'run-1').load('simulator'), { unit: 'Intercessors' });
    assert.equal(Core.createPageState(storage, 'run-2').load('simulator'), null);  // app restarted
    assert.equal(Core.createPageState(storage, 'run-1').load('missing'), null);
});

test('createPageState survives unavailable storage', () => {
    const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
    const state = Core.createPageState(broken, 'run-1');
    assert.doesNotThrow(() => state.save('x', { a: 1 }));
    assert.equal(state.load('x'), null);
    assert.equal(Core.createPageState(null, 'run-1').load('x'), null);
});
