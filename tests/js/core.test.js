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

test('attackerEntry builds an Analytics unit at a size with its leader', () => {
    const unit = {
        id: 1, name: 'Warriors', faction: 'Starfarers', faction_id: 3, default_models: 5,
        points_by_model_count: [null, 16, 32, 48, 64, 80, 160, 160, 160, 160, 160],
        ranged_weapons: [{ id: 10, type: 'ranged', name: 'Rifle', qty: 5, default_use: true }], melee_weapons: [],
        leaders: [{ id: 2, name: 'Captain', points: 70, leader_buffs: [] }],
    };
    const captain = { id: 2, name: 'Captain', ranged_weapons: [{ id: 11, type: 'ranged', name: 'Pistol', qty: 1 }], melee_weapons: [] };
    const entry = Core.attackerEntry(unit, 10, captain);
    assert.equal(entry.unit_size, 10);
    assert.deepEqual(entry.weapons.map(w => [w.name, w.models, !!w.leader]), [['Rifle', 10, false], ['Pistol', 1, true]]);
    assert.deepEqual([entry.leader_id, entry.leader_name, entry.leader_points], [2, 'Captain', 70]);
    const plain = Core.attackerEntry(unit);
    assert.equal(plain.unit_size, 5);
    assert.equal(plain.leader_id, null);
});

test('attackerEntry takes an army loadout: chosen weapons and counts, the rest left out', () => {
    const unit = {
        id: 1, name: 'Warriors', faction: 'Starfarers', faction_id: 3, default_models: 5, points_by_model_count: [],
        ranged_weapons: [{ id: 10, type: 'ranged', name: 'Rifle', qty: 4, default_use: true },
                         { id: 12, type: 'ranged', name: 'Pistol', qty: 1, default_use: true }],
        melee_weapons: [], ranged_options: [{ id: 11, type: 'ranged', name: 'Heavy laser' }], melee_options: [],
        leaders: [{ id: 2, name: 'Captain', points: 70, leader_buffs: [] }],
    };
    const captain = { id: 2, name: 'Captain', ranged_weapons: [{ id: 13, type: 'ranged', name: 'Plasma pistol', qty: 1 }], melee_weapons: [] };
    const entry = Core.attackerEntry(unit, 8, captain, [],
        [{ weapon_id: 10, weapon_type: 'ranged', qty: 6 }, { weapon_id: 11, weapon_type: 'ranged', qty: 1 }],
        [{ weapon_id: 13, weapon_type: 'ranged', qty: 1, default_use: false }]);
    const byName = Object.fromEntries(entry.weapons.map(w => [w.name, [w.include, w.models]]));
    assert.deepEqual(byName, { Rifle: [true, 6], Pistol: [false, 1], 'Heavy laser': [true, 1], 'Plasma pistol': [false, 1] });
    assert.equal(entry.unit_size, 8);
    Core.scaleUnitSize(entry, 4);  // Resizing scales from the chosen counts
    assert.equal(entry.weapons.find(w => w.name === 'Rifle').models, 3);
});

test('defenderEntry carries the unit stats, size and leader', () => {
    const unit = { id: 1, name: 'Warriors', faction: 'Starfarers', faction_id: 3, toughness: 4, save: 3, wounds: 2,
                   invulnerable_save: null, feel_no_pain: 6, leaders: [] };
    const entry = Core.defenderEntry(unit, 10, 2);
    assert.deepEqual([entry.unit_id, entry.toughness, entry.model_count, entry.leader_id, entry.feel_no_pain], [1, 4, 10, 2, 6]);
});

test('detachmentKeys reads a list, one key as first saved, or nothing', () => {
    assert.deepEqual(Core.detachmentKeys(['a', 'b', 'a', '']), ['a', 'b']);
    assert.deepEqual(Core.detachmentKeys('a'), ['a']);
    assert.deepEqual(Core.detachmentKeys(''), []);
    assert.deepEqual(Core.detachmentKeys(undefined), []);
});

test('findSavedUnit picks the saved copy of a name found in several factions', () => {
    const units = [
        { id: 1, name: 'Death Jester', faction_id: 10 },
        { id: 2, name: 'Death Jester', faction_id: 20 },
        { id: 3, name: 'Death Jesters', faction_id: 20 },
    ];
    assert.equal(Core.findSavedUnit(units, { unit_name: 'Death Jester', unit_id: 2, faction_id: 10 }).id, 2);
    assert.equal(Core.findSavedUnit(units, { unit_name: 'Death Jester', faction_id: 20 }).id, 2);
    assert.equal(Core.findSavedUnit(units, { unit_name: 'Death Jester', unit_id: 99, faction_id: 20 }).id, 2);
    assert.equal(Core.findSavedUnit(units, { unit_name: 'Death Jester' }).id, 1);  // Older scenarios: first by name
    assert.equal(Core.findSavedUnit(units, { unit_name: 'Solitaire' }), null);
});

test('detachmentPickerHtml ticks the chosen detachments and shows their DP', () => {
    const detachments = [{ key: 'g', name: 'Gladius', dp: 3, faction: 'SM' }, { key: 'p', name: 'Phobos', dp: 1, faction: 'SM' }];
    const html = Core.detachmentPickerHtml(detachments, ['p']);
    assert.match(html, /<summary[^>]*>Phobos<\/summary>/);
    assert.match(html, /data-key="p" checked/);
    assert.doesNotMatch(html, /data-key="g" checked/);
    assert.match(html, /3 DP/);
    assert.equal(Core.detachmentSummary(detachments, ['g', 'p']), 'Gladius + Phobos');
    assert.equal(Core.detachmentSummary(detachments, []), 'No detachment (faction-wide only)');
});

test('armyDetachmentPoints shows the DP spent against the budget', () => {
    assert.deepEqual(Core.armyDetachmentPoints(3, 3), { text: '3 / 3 DP', over: false });
    assert.deepEqual(Core.armyDetachmentPoints(4, 3), { text: '4 / 3 DP', over: true });
    assert.deepEqual(Core.armyDetachmentPoints(2, null), { text: '2 DP', over: false });
});

test('armyPoints shows the total against the limit', () => {
    assert.deepEqual(Core.armyPoints(1995, 2000), { text: '1,995 / 2,000 pts', over: false });
    assert.deepEqual(Core.armyPoints(2010, 2000), { text: '2,010 / 2,000 pts', over: true });
    assert.deepEqual(Core.armyPoints(450, null), { text: '450 pts', over: false });
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
        { unit_id: 2, model_count: 5, label: 'Hellblasters (x5)', extra_modifiers: 'Stealth', leader_id: null, enhancements: [] },
        { unit_id: 2, model_count: 10, label: 'Hellblasters #2 (x10)', extra_modifiers: '', leader_id: null, enhancements: [] },
    ]);
    assert.deepEqual(request.attacker_rules, { rule_ids: [1], stratagem_ids: [] });
    assert.deepEqual(request.defender_rules, { rule_ids: [], stratagem_ids: [2] });
    assert.equal(request.weapon_filter, 'both');

    request.conditions.cover = false;
    assert.equal(page.conditions.cover, true);  // the request has its own copy
});

test('enhancements reach the matrix request: the unit own ones, and its leader ones while it leads', () => {
    const page = matchupsPage();
    page.attackers[0].enhancements = [{ id: 7, name: 'Blade', bearer: 'leader', unit_id: 30 },
                                      { id: 8, name: 'Sights', bearer: 'unit', unit_id: 1 }];
    let request = Core.buildMatrixRequest(page);
    assert.deepEqual(request.attackers[0].enhancements, [{ id: 8, bearer: 'unit' }]);  // No leader yet
    page.attackers[0].leader_id = 30;
    request = Core.buildMatrixRequest(page);
    assert.deepEqual(request.attackers[0].enhancements, [{ id: 7, bearer: 'leader' }, { id: 8, bearer: 'unit' }]);
    assert.deepEqual(request.attackers[0].weapons[0].enhancements, request.attackers[0].enhancements);
    assert.equal(request.attackers[0].weapons[0].leader, false);
    page.attackers[0].leader_id = 31;  // Another leader: not the bearer
    assert.deepEqual(Core.buildMatrixRequest(page).attackers[0].enhancements, [{ id: 8, bearer: 'unit' }]);
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

// ---------------------------------------------------------------------------
// Play mode: board layout
// ---------------------------------------------------------------------------

test('playFormation sets a unit out in rows of up to 5, bases 0.1" apart, centred where it is dropped', () => {
    // 32mm bases (1.26"): 1.36" from centre to centre
    const five = Core.playFormation(Array(5).fill(1.26), 20, 6);
    assert.deepEqual(five.map(p => p.x), [17.28, 18.64, 20, 21.36, 22.72]);
    assert.ok(five.every(p => p.y === 6));
    const seven = Core.playFormation(Array(7).fill(1.26), 20, 6);
    assert.deepEqual(seven.map(p => [p.x, p.y]), [
        [17.28, 6.68], [18.64, 6.68], [20, 6.68], [21.36, 6.68], [22.72, 6.68], [19.32, 5.32], [20.68, 5.32]]);
    // Mixed sizes (a leader on a bigger base) space out by the biggest
    assert.deepEqual(Core.playFormation([1, 2], 10, 10).map(p => p.x), [8.95, 11.05]);
});

test('playMoveLabel shows how far a model has been dragged against how far it may go', () => {
    assert.deepEqual(Core.playMoveLabel(3, 4, 6), { text: '5.0" of 6"', over: false });
    assert.deepEqual(Core.playMoveLabel(0, 6.04, 6), { text: '6.0" of 6"', over: true });
    assert.deepEqual(Core.playMoveLabel(0, 0, 10), { text: '0.0" of 10"', over: false });
});

test('playZoom zooms about the pointer and keeps the view between 10" and the board plus a margin', () => {
    const view = { x: 0, y: 0, w: 60, h: 44 };
    assert.deepEqual(Core.playZoom(view, 0.5, 30, 22), { x: 15, y: 11, w: 30, h: 22 });
    assert.deepEqual(Core.playZoom(view, 0.5, 0, 0), { x: 0, y: 0, w: 30, h: 22 });  // The corner stays put
    assert.equal(Core.playZoom(view, 0.01, 30, 22).w, 10);
    assert.equal(Core.playZoom(view, 10, 30, 22).w, 72);
});

test('playFit shifts a dropped formation to lie wholly inside an area when it can', () => {
    const area = { left: 0, bottom: 0, right: 60, top: 12 };
    // Two models 1" across (0.5" from centre to edge) dropped too close to the zone's top edge
    assert.deepEqual(Core.playFit([{ x: 10, y: 11.8 }, { x: 12, y: 11.8 }], [0.5, 0.5], area),
                     [{ x: 10, y: 11.5 }, { x: 12, y: 11.5 }]);
    assert.deepEqual(Core.playFit([{ x: 0.2, y: 6 }], [0.5], area), [{ x: 0.5, y: 6 }]);  // Off the left edge
    assert.deepEqual(Core.playFit([{ x: 10, y: 6 }], [0.5], area), [{ x: 10, y: 6 }]);  // Already inside
    // Too deep to fit: left where it was dropped (the server says why)
    assert.deepEqual(Core.playFit([{ x: 10, y: 1 }, { x: 10, y: 14 }], [0.5, 0.5], area), [{ x: 10, y: 1 }, { x: 10, y: 14 }]);
});

test('playDiceGroups turns an attack\'s rolls into labelled groups, joining one-at-a-time saves', () => {
    const die = (face, success, critical = false, rerolled_from = null) => ({ face, success, critical, rerolled_from });
    const rolls = [
        { purpose: 'advance', size: 6, dice: [die(4, true)], modifier: 0, total: 4 },  // Not this attack's
        { purpose: 'hit', need: 3, crit_on: 6, dice: [die(6, true, true), die(2, false), die(5, true, false, 1)] },
        { purpose: 'wound', need: 4, crit_on: 6, dice: [die(4, true), die(3, false)] },
        { purpose: 'save', need: 3, crit_on: 7, dice: [die(1, false)] },
        { purpose: 'damage', size: 3, dice: [die(2, true)], modifier: 1, total: 3 },
    ];
    const groups = Core.playDiceGroups(rolls, [1, 5]);
    assert.deepEqual(groups.map(g => [g.label, g.need]), [['Hit', '3+'], ['Wound', '4+'], ['Save', '3+'], ['Damage', null]]);
    assert.deepEqual(groups[0].dice.map(d => d.kind), ['critical', 'fail', 'success']);
    assert.equal(groups[0].dice[2].rerolled_from, 1);
    assert.deepEqual(groups[3].dice.map(d => [d.face, d.kind]), [[2, 'value']]);
    assert.equal(groups[3].note, 'D3+1 = 3');
    // Two saves in a row show as one group
    const saves = [{ purpose: 'save', need: 3, crit_on: 7, dice: [die(1, false)] }, { purpose: 'save', need: 3, crit_on: 7, dice: [die(6, true)] }];
    assert.deepEqual(Core.playDiceGroups(saves, [0, 2]).map(g => g.dice.length), [2]);
    // Saves and Feel No Pain rolled in turns, one wound at a time, show as one group each
    const fnp = face => ({ purpose: 'feel no pain', need: 5, crit_on: 7, dice: [die(face, face >= 5)] });
    const turns = Core.playDiceGroups([saves[0], fnp(5), saves[1], saves[0], fnp(1)], [0, 5]);
    assert.deepEqual(turns.map(g => [g.label, g.dice.map(d => d.face)]), [['Save', [1, 6, 1]], ['Feel No Pain', [5, 1]]]);
});

test('playAttackSummary says what an attack did in one line', () => {
    assert.equal(Core.playAttackSummary({ name: 'Bolter', models: 2, attacks: 4, hits: 3, wounds: 2, mortal: 0, killed: ['a'] }),
                 'Bolter (2 models): 4 attacks, 3 hits, 2 wounds, 1 model killed');
    assert.equal(Core.playAttackSummary({ name: 'Melta', models: 1, attacks: 1, hits: 1, wounds: 0, mortal: 1, killed: [], half_range: true }),
                 'Melta (1 model, half range): 1 attack, 1 hit, 0 wounds, 1 mortal wound, none killed');
    assert.equal(Core.playAttackSummary({ name: 'Hazardous', models: 2, killed: ['a'] }), 'Hazardous: 2 rolled, 1 model killed');
    assert.equal(Core.playAttackSummary({ name: 'Rifle', models: 1, attacks: 1, hits: 0, wounds: 0, mortal: 0, killed: [], cover: true }),
                 'Rifle (1 model, target in cover): 1 attack, 0 hits, 0 wounds, none killed');
});

test('playShotLines lists who shot (or fought) whom since the phase began, one line per shooter and target', () => {
    const events = [
        { action: { type: 'shoot', unit: 'old' }, attacks: [{ name: 'Gun', target: 'x' }] },  // An earlier phase
        { action: { type: 'next_phase' }, turn: { phase: 'shooting' }, attacks: undefined },
        { action: { type: 'shoot', unit: 'a' }, attacks: [{ name: 'Gun', target: 'x' }, { name: 'Gun', target: 'x' },
                                                         { name: 'Cannon', target: 'y' }, { name: 'Hazardous', target: 'a' }] },
        { action: { type: 'orders' } },
        { action: { type: 'shoot', unit: 'b' }, attacks: [{ name: 'Gun', target: 'x' }] },
        { action: { type: 'fight', unit: 'c' }, attacks: [{ name: 'Sword', target: 'z' }] },
    ];
    assert.deepEqual(Core.playShotLines(events), [{ from: 'a', to: 'x' }, { from: 'a', to: 'y' }, { from: 'b', to: 'x' },
                                                  { from: 'c', to: 'z' }]);
    assert.deepEqual(Core.playShotLines(events.slice(0, 2)), []);
});

test('playCentre is the middle of a unit\'s placed models', () => {
    assert.deepEqual(Core.playCentre([{ x: 10, y: 6 }, { x: 14, y: 8 }]), { x: 12, y: 7 });
    assert.equal(Core.playCentre([]), null);
});

test('playFormation puts the last row (leaders) at the back for a player on the top edge too', () => {
    // Player 1 deploys at the bottom: rows run from the front (higher y) back to lower y
    assert.deepEqual(Core.playFormation([1, 1, 1, 1, 1, 2], 20, 6).map(p => p.y), [7.05, 7.05, 7.05, 7.05, 7.05, 4.95]);
    // Player 2 deploys at the top: the same rows mirrored, so the leader is at the higher y (their back)
    assert.deepEqual(Core.playFormation([1, 1, 1, 1, 1, 2], 20, 38, true).map(p => p.y), [36.95, 36.95, 36.95, 36.95, 36.95, 39.05]);
});

test('playUsableNow offers stratagems for the current phase (any phase when the app has none for it)', () => {
    const entries = [{ name: 'Aim', phase: 'Shooting' }, { name: 'Strike', phase: 'Fighting' },
                     { name: 'Both', phase: 'Both' }, { name: 'Anytime', phase: null }];
    assert.deepEqual(Core.playUsableNow(entries, 'shooting').map(e => e.name), ['Aim', 'Both', 'Anytime']);
    assert.deepEqual(Core.playUsableNow(entries, 'fight').map(e => e.name), ['Strike', 'Both', 'Anytime']);
    assert.deepEqual(Core.playUsableNow(entries, 'movement').map(e => e.name), ['Anytime']);
    assert.deepEqual(Core.playUsableNow(entries, 'deployment'), []);
});

test('playLineVp says what a mission line is worth', () => {
    assert.deepEqual(Core.playLineVp({ vp: 3, each: false, turn_cap: null }), { amount: '+3', each: false, note: '3VP' });
    assert.deepEqual(Core.playLineVp({ vp: 2, each: true, turn_cap: null }), { amount: '+2', each: true, note: '2VP each' });
    assert.deepEqual(Core.playLineVp({ vp: 4, each: true, turn_cap: 5 }), { amount: '+4', each: true, note: '4VP each, up to 5VP a turn' });
});

test('playLogTurns gives the turn each event was made in (before any change it made)', () => {
    const shooting = { round: 1, player: 0, phase: 'shooting' }, charge = { round: 1, player: 0, phase: 'charge' };
    const events = [{ action: { type: 'place' } }, { action: { type: 'end_deployment' }, turn: shooting },
                    { action: { type: 'shoot' } }, { action: { type: 'next_phase' }, turn: charge }, { action: { type: 'charge' } }];
    assert.deepEqual(Core.playLogTurns(events), [null, null, shooting, shooting, charge]);
});

test("playDieFace puts a d6 face's pips on a 3 by 3 grid", () => {
    assert.deepEqual(Core.playDieFace(1), [4]);
    assert.deepEqual(Core.playDieFace(4), [0, 2, 6, 8]);
    assert.deepEqual(Core.playDieFace(6), [0, 2, 3, 5, 6, 8]);
    assert.equal(Core.playDieFace(7), null);  // Not a d6 face: shown as a number
});

test('playResult says who won once the battle is over', () => {
    const players = [{ name: 'Red' }, { name: 'Blue' }];
    assert.equal(Core.playResult(players, [60, 120], { phase: 'ended' }), 'Game over: Blue wins, 120 to 60');
    assert.equal(Core.playResult(players, [30, 30], { phase: 'ended' }), 'Game over: a draw, 30 each');
    assert.equal(Core.playResult(players, [30, 0], { phase: 'shooting' }), null);
});

test('playUnitTags gives a unit row its badges: destroyed, ready, battle-shocked, objectives in range, effects', () => {
    const players = [{ name: 'Red' }, { name: 'Blue' }];
    const objectives = [{ id: 'home-attacker', kind: 'home', owner: 1 }, { id: 'central-1', kind: 'central' }];
    const unit = { models: [{ alive: true }], status: { shocked: true }, effects: [{}, {}] };
    const tags = Core.playUnitTags(unit, { ready: true, inRange: ['home-attacker', 'central-1'], objectives, players });
    assert.deepEqual(tags.map(t => [t.kind, t.label]), [['ready', 'Ready'], ['shocked', 'Shocked'], ['objective', 'H C'], ['effects', '2']]);
    assert.equal(tags[2].title, "Within range of Blue's home objective, a central objective");
    assert.deepEqual(Core.playUnitTags({ models: [{ alive: true }] }, { inRange: [], objectives, players }), []);
    const dead = Core.playUnitTags({ models: [{ alive: false }], status: { shocked: true } }, { ready: true, inRange: [], objectives, players });
    assert.deepEqual(dead.map(t => t.kind), ['destroyed']);
});

test('playPhaseTrack marks the phases done, current and to come this turn', () => {
    const states = turn => Core.playPhaseTrack(turn).map(p => `${p.key}:${p.state}`);
    assert.deepEqual(states({ phase: 'shooting' }),
        ['command:done', 'movement:done', 'shooting:current', 'charge:next', 'fight:next']);
    const fight = Core.playPhaseTrack({ phase: 'fight', step: 'consolidate' });
    assert.equal(fight[4].state, 'current');
    assert.equal(fight[4].step, 'Consolidate');
    assert.deepEqual(states({ phase: 'deployment' }).every(s => s.endsWith(':next')), true);
    assert.deepEqual(states({ phase: 'ended' }).every(s => s.endsWith(':done')), true);
    assert.deepEqual(Core.playPhaseTrack({ phase: 'command' }).map(p => p.label),
        ['Command', 'Movement', 'Shooting', 'Charge', 'Fight']);
});

test('playBounds is the box round some points', () => {
    assert.deepEqual(Core.playBounds([[0, 0], [20, 0], [20, 22], [12, 22], [12, 44], [0, 44]]),
                     { left: 0, bottom: 0, right: 20, top: 44 });
});

test('playFootprintPoints places a footprint by its corner and turns it about that corner', () => {
    const round = pts => pts.map(([x, y]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]);
    const medium = [[0, 0], [6, 0], [6, 4], [0, 4]], polygon = [[0, 0], [11.5, 0], [11.5, 2], [0, 8]];
    assert.deepEqual(round(Core.playFootprintPoints(medium, 10, 10, 0)), [[10, 10], [16, 10], [16, 14], [10, 14]]);
    assert.deepEqual(round(Core.playFootprintPoints(medium, 10, 10, 90)), [[10, 10], [10, 16], [6, 16], [6, 10]]);
    assert.deepEqual(round(Core.playFootprintPoints(polygon, 0, 0, 0, true)), [[0, 0], [11.5, 0], [11.5, -2], [0, -8]]);
});

test('playSnap rounds to the nearest step', () => {
    assert.equal(Core.playSnap(10.26, 0.5), 10.5);
    assert.equal(Core.playSnap(10.24, 0.5), 10);
    assert.equal(Core.playSnap(-0.2, 0.5), -0);
});

test('playSetupPrompt walks the mission sequence: central objective, roll-off, then a choice', () => {
    const players = [{ name: 'Red' }, { name: 'Blue' }];
    const step = s => Core.playSetupPrompt({ phase: 'setup', step: s, player: 1 }, players);
    assert.deepEqual(step('objectives').buttons, [{ action: { type: 'roll_objectives' }, label: 'Roll for the central objective', icon: 'dice' }]);
    assert.match(step('roll_off').text, /winner chooses/);
    const choose = step('choose_role');
    assert.match(choose.text, /^Blue won the roll-off/);
    assert.deepEqual(choose.buttons.map(b => b.action), [{ type: 'choose_role', role: 'attacker' }, { type: 'choose_role', role: 'defender' }]);
    assert.equal(Core.playSetupPrompt({ phase: 'deployment', player: 0 }, players), null);
});

test('playSeatNotice says whose move it is when this app plays one side', () => {
    const game = (seat, waiting, extra = {}) => ({ seat, waiting_on: waiting, state: { players: [{ name: 'Red' }, { name: 'Blue' }] }, ...extra });
    assert.equal(Core.playSeatNotice(game(null, [0])), null);  // Hot-seat
    assert.deepEqual(Core.playSeatNotice(game(0, [0])), { text: 'You play Red · your move', mine: true, send: false, invite: null });
    assert.deepEqual(Core.playSeatNotice(game(0, [1])), { text: 'You play Red · waiting for Blue: send them the game file', mine: false, send: true, invite: null });
    const online = joined => ({ online: { code: 'ABCDEFGHJK', joined } });
    assert.deepEqual(Core.playSeatNotice(game(0, [0], online(false))),
        { text: 'You play Red online · send Blue the invite code to join: ABCDE-FGHJK', mine: false, send: false, invite: 'ABCDEFGHJK' });
    assert.deepEqual(Core.playSeatNotice(game(0, [1], online(true))), { text: 'You play Red online · waiting for Blue', mine: false, send: false, invite: null });
    const named = { online: { code: 'ABCDEFGHJK', joined: true, names: ['Sam', 'Alex'] } };
    assert.equal(Core.playSeatNotice(game(0, [1], named)).text, 'You play Red online · waiting for Blue (Alex)');
    assert.equal(Core.playSeatNotice(game(1, [0, 1])).mine, true);  // Both choosing secondaries
    assert.match(Core.playSeatNotice(game(1, [])).text, /battle is over/);
    assert.match(Core.playSeatNotice(game(1, [1], { needs_update: true, app_version: '9.0.0' })).text, /version 9\.0\.0: update the app/);
});

test('playRollOff writes a roll-off and its re-rolls as one line', () => {
    const players = [{ name: 'Red' }, { name: 'Blue' }];
    assert.equal(Core.playRollOff({ rolls: [[3, 3], [2, 5]], winner: 1 }, players), 'Red 3, Blue 3 (tie), then Red 2, Blue 5: Blue wins');
});

test('playMoveLabel adds the path already travelled (waypoints) to the new leg', () => {
    assert.deepEqual(Core.playMoveLabel(3, 4, 12, 6), { text: '11.0" of 12"', over: false });
    assert.deepEqual(Core.playMoveLabel(3, 4, 10, 6), { text: '11.0" of 10"', over: true });
});

test('playPath gives a model its path this phase: start, waypoints, where it stands', () => {
    const start = { x: 10, y: 14 }, via = [{ x: 14, y: 17 }, { x: 14, y: 23 }], now = { x: 10, y: 26 };
    assert.deepEqual(Core.playPath(start, via, now), [start, ...via, now]);
    assert.equal(Core.playPathLength(Core.playPath(start, via, now)), 16);
    assert.deepEqual(Core.playPath(start, undefined, start), [start]);  // Not moved
});

test('playUnitCard shows the datasheet of each part, its leader marked, abilities once loaded', () => {
    const stats = { move: '6', toughness: 4, save: 3, wounds: 2, leadership: 6, oc: 1, invulnerable_save: 4, feel_no_pain: null };
    const unit = { name: 'Squad', parts: [
        { name: 'Squad', role: 'bodyguard', models: 5, stats, keywords: ['Infantry'], enhancement: null, warlord: false,
          weapons: [{ name: 'Bolter', type: 'ranged', range: 24, attacks: '2', skill: 3, strength: 4, ap: 1, damage: '1', qty: 5, abilities: ['Rapid Fire 1'] }] },
        { name: 'Captain <b>', role: 'leader', models: 1, stats, keywords: ['Character'], enhancement: { name: 'Relic' }, warlord: true,
          weapons: [{ name: 'Sword', type: 'melee', range: null, attacks: '5', skill: 2, strength: 5, ap: 2, damage: '2', qty: 1, abilities: [] }] },
    ] };
    const loading = Core.playUnitCard(unit);
    assert.match(loading, /Loading abilities/);
    assert.match(loading, /Bolter <span class="play-card-qty">×5<\/span>/);
    assert.match(loading, /<td>24"<\/td><td>2<\/td><td>3\+<\/td><td>4<\/td><td>-1<\/td><td>1<\/td>/);
    assert.match(loading, /<td>Melee<\/td>/);
    assert.match(loading, /<b>INV<\/b> 4\+/);
    assert.match(loading, /Captain &lt;b&gt;/);  // Escaped
    assert.match(loading, /<span class="play-card-tag">Leader<\/span><span class="play-card-tag">Warlord<\/span>/);
    assert.match(loading, /Enhancement:<\/b> Relic/);
    const loaded = Core.playUnitCard(unit, [{ found: true, abilities: [{ name: 'Oath', text: 'Re-roll **hits**.' }], rules: ['Deep Strike'] },
                                            { found: false, abilities: [], rules: [] }]);
    assert.match(loaded, /<b>Oath:<\/b> <p>Re-roll <strong>hits<\/strong>.<\/p>/);
    assert.match(loaded, /Deep Strike/);
    assert.match(loaded, /aren't in this PC's data/);
});

test('playTurn pivots the formation along arcs and spins single models for free', () => {
    const spots = [{ id: 'a', x: 0, y: 0, angle: 0 }, { id: 'b', x: 4, y: 0, angle: 0 }];  // Centre (2, 0)
    const half = Core.playTurn(spots, 180);
    assert.deepEqual(half.spots.map(p => [p.id, p.x, p.y, p.angle]), [['a', 4, 0, 180], ['b', 0, 0, 180]]);
    assert.equal(half.routes.a.length, 17);  // 18 pieces of 10 degrees
    assert.deepEqual(half.routes.a[8], { x: 2, y: -2 });  // Halfway round: below the centre
    const length = Core.playPathLength([spots[0], ...half.routes.a, half.spots[0]]);
    assert.ok(Math.abs(length - 2 * Math.PI) < 0.05, length);  // About the half circle's length, not its 4" chord
    const spin = Core.playTurn(spots, 0, { b: -15 });
    assert.deepEqual(spin.spots[1], { id: 'b', x: 4, y: 0, angle: 345 });
    assert.deepEqual(spin.routes, { a: [], b: [] });
});

test('playLuck notices rolls that were 1 in 20 or rarer', () => {
    const dice = (kinds) => kinds.split('').map(k => ({ face: 3, kind: { s: 'success', c: 'critical', f: 'fail' }[k], rerolled_from: null }));
    // 10 hits from 10 dice on 4+: (1/2)^10, a 1-in-1,024 result
    const hot = Core.playLuck([{ label: 'Hit', need: '4+', dice: dice('ssssssssss') }]);
    assert.deepEqual(hot, { lucky: true, odds: 1024, text: 'Hot dice: 10 of 10 hit rolls on 4+, a 1-in-1,024 result' });
    // 0 saves from 6 on 3+: (1/3)^6, 1 in 729
    const cold = Core.playLuck([{ label: 'Save', need: '3+', dice: dice('ffffff') }]);
    assert.equal(cold.lucky, false);
    assert.equal(cold.odds, 729);
    assert.equal(Core.playLuck([{ label: 'Hit', need: '4+', dice: dice('sssfff') }]), null);  // Ordinary
    assert.equal(Core.playLuck([{ label: 'Hit', need: '4+', dice: dice('ss') }]), null);  // Too few dice
    const rerolled = dice('ssssssssss');
    rerolled[0].rerolled_from = 1;
    assert.equal(Core.playLuck([{ label: 'Hit', need: '4+', dice: rerolled }]), null);
    assert.equal(Core.playLuck([{ label: 'Damage', need: null, dice: dice('sss') }]), null);  // Values, not successes
    // The rarer of two groups wins
    const both = Core.playLuck([{ label: 'Hit', need: '4+', dice: dice('ssssssssss') }, { label: 'Wound', need: '2+', dice: dice('cfffff') }]);
    assert.match(both.text, /Cold dice: 1 of 6 wound rolls on 2\+/);
});

test('playPan moves the view but keeps its centre over the board', () => {
    const board = { width: 60, depth: 44 }, view = { x: 10, y: 10, w: 20, h: 15 };
    assert.deepEqual(Core.playPan(view, 5, -3, board), { x: 15, y: 7, w: 20, h: 15 });
    assert.deepEqual(Core.playPan(view, -100, 0, board), { x: -10, y: 10, w: 20, h: 15 });  // Centre at the left edge
    assert.deepEqual(Core.playPan(view, 0, 100, board), { x: 10, y: 36.5, w: 20, h: 15 });  // Centre at the bottom edge
});

test('playRouteBends keeps the bends of a dragged path and drops its wobbles', () => {
    const corner = [{ x: 0, y: 0 }, { x: 1, y: 0.05 }, { x: 2, y: 0 }, { x: 2.04, y: 1 }, { x: 2, y: 2 }];
    assert.deepEqual(Core.playRouteBends(corner), [{ x: 2, y: 0 }]);
    assert.deepEqual(Core.playRouteBends([{ x: 0, y: 0 }, { x: 1, y: 0.08 }, { x: 3, y: 0 }]), []);  // Straight enough
    assert.deepEqual(Core.playRouteBends([{ x: 0, y: 0 }, { x: 1, y: 1 }]), []);
    const zigzag = [{ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 0 }, { x: 3, y: 1 }];
    assert.deepEqual(Core.playRouteBends(zigzag), [{ x: 1, y: 1 }, { x: 2, y: 0 }]);
});

test('playBattlefieldGroups groups maps, Event layouts by pairing, then saved battlefields', () => {
    const list = [
        { key: 'builtin:dawn-of-war', name: 'Dawn of War', kind: 'map' },
        { key: 'builtin:th-a', name: 'Take and Hold vs Disruption: Layout A', kind: 'layout' },
        { key: 'builtin:th-b', name: 'Take and Hold vs Disruption: Layout B', kind: 'layout' },
        { key: 'builtin:pf-a', name: 'Purge the Foe vs Disruption: Layout A', kind: 'layout' },
        { key: 'saved:3', name: 'My table', kind: 'saved' },
    ];
    const groups = Core.playBattlefieldGroups(list);
    assert.deepEqual(groups.map(g => [g.label, g.kind, g.items.map(i => i.label)]), [
        ['Deployment maps', 'map', ['Dawn of War']],
        ['Take and Hold vs Disruption', 'layout', ['Layout A', 'Layout B']],
        ['Purge the Foe vs Disruption', 'layout', ['Layout A']],
        ['Saved battlefields', 'saved', ['My table']],
    ]);
    assert.equal(groups[1].items[0].key, 'builtin:th-a');
});

test('playVpLine writes one scoring line for the log, saying when a cap cut it', () => {
    const players = [{ name: 'Red' }, { name: 'Blue' }];
    assert.equal(Core.playVpLine({ player: 0, mission: 'Battlefield Dominance', text: 'Each objective you hold', how: '2 objectives × 3VP', vp: 6, asked: 6 }, players),
        'Red · Battlefield Dominance: Each objective you hold (2 objectives × 3VP): 6VP');
    assert.equal(Core.playVpLine({ player: 1, mission: 'Outmanoeuvre', text: 'Round 1: each objective', how: '4 objectives × 4VP', vp: 5, asked: 16 }, players),
        'Blue · Outmanoeuvre: Round 1: each objective (4 objectives × 4VP): 5VP (16VP, cut by the cap)');
});

test('playVpCaps sums a player\'s primary VP this round and in all against the caps', () => {
    const vp = { primary: 23, rounds: { 1: 8, 2: 15 }, battle: 0, adjust: 2 };
    assert.deepEqual(Core.playVpCaps(vp, 2, { round: 15, game: 45 }),
        { total: 25, round: 15, roundCap: 15, primary: 23, gameCap: 45, roundFull: true });
    assert.equal(Core.playVpCaps(vp, 3, { round: 15, game: 45 }).round, 0);
});

test('playSetupPrompt asks both players to choose their secondary missions', () => {
    const prompt = Core.playSetupPrompt({ phase: 'setup', step: 'secondaries', player: 1 }, [{ name: 'Red' }, { name: 'Blue' }]);
    assert.equal(prompt.secondaries, true);
    assert.deepEqual(prompt.buttons, []);
    assert.match(prompt.text, /secondary missions/);
});

test('playSecondaryCaps sums secondary VP this round and in all against the caps', () => {
    const vp = { secondary: 18, secondary_rounds: { 2: 15 }, cards: { assassination: 12 } };
    assert.deepEqual(Core.playSecondaryCaps(vp, 2, { round: 15, game: 45, card: 20 }),
        { total: 18, round: 15, roundCap: 15, gameCap: 45, roundFull: true, cardCap: 20 });
});

test('playMarker gives an operation marker its letter and what it marks', () => {
    assert.deepEqual(Core.playMarker('decoyed'), { letter: 'D', label: 'Decoyed' });
    assert.deepEqual(Core.playMarker('asset'), { letter: 'A', label: 'Locate and Deny marker' });
    assert.deepEqual(Core.playMarker('something'), { letter: 'M', label: 'Operation marker' });
});

test('playSetupPrompt rolls for Mirrored World\'s primary mission', () => {
    const prompt = Core.playSetupPrompt({ phase: 'setup', step: 'mirrored', player: 0 }, [{ name: 'Red' }, { name: 'Blue' }]);
    assert.deepEqual(prompt.buttons.map(b => b.action), [{ type: 'roll_mirrored' }]);
    assert.match(prompt.text, /Mirrored World/);
});

test('playPivot turns a formation about its centre, turning each base with it', () => {
    const spots = [{ id: 'a', x: 9, y: 10, angle: 0 }, { id: 'b', x: 11, y: 10, angle: 0 }];
    const turned = Core.playPivot(spots, 90).map(s => ({ ...s, x: Math.round(s.x * 100) / 100, y: Math.round(s.y * 100) / 100 }));
    assert.deepEqual(turned, [{ id: 'a', x: 10, y: 9, angle: 90 }, { id: 'b', x: 10, y: 11, angle: 90 }]);
    assert.equal(Core.playPivot([{ id: 'a', x: 5, y: 5, angle: 350 }], 15)[0].angle, 5);  // Angles stay within 0-360
});

test('playWaitingNotice gives the invite code and the choices made when they join', () => {
    const game = { seat: 0, players: ['Red', null], online: { code: 'ABCDEFGHJK' },
                   options: { battlefield: "Their pairing's Event Companion layout", twist: 'Random' } };
    assert.deepEqual(Core.playWaitingNotice(game), {
        text: 'You play Red online · send the other player the invite code to join with their army: ABCDE-FGHJK',
        detail: "Battlefield: Their pairing's Event Companion layout. Twist: Random." });
    assert.equal(Core.playWaitingNotice({ ...game, options: null }).detail, '');
});

test('playInviteText says who invited you and what the game will be', () => {
    const invite = { opponent: { army: 'Red', faction: 'Starfarers', disposition: 'Purge the Foe', player: 'Sam' },
                     options: { battlefield: 'The default map', twist: null } };
    assert.equal(Core.playInviteText(invite), 'Sam plays Red (Starfarers, Purge the Foe). Battlefield: The default map.');
    assert.equal(Core.playInviteText({ opponent: { army: 'Red' } }), 'Red.');
});

test('playMat picks a game its battle mat from its ID, the same every time, and every mat is bundled', () => {
    const fs = require('node:fs');
    const mats = [{ key: 'a' }, { key: 'b' }, { key: 'c' }];
    assert.equal(Core.playMat('abc', mats), Core.playMat('abc', mats));  // Both players, every redraw
    const picked = new Set(Array.from({ length: 60 }, (_, i) => Core.playMat(`game-${i}`, mats).key));
    assert.deepEqual([...picked].sort(), ['a', 'b', 'c']);  // Spread across them
    assert.ok(Core.playMat(undefined));  // No game open yet
    assert.ok(Core.PLAY_MAT_STRENGTH > 0 && Core.PLAY_MAT_STRENGTH < 1);
    for (const mat of Core.PLAY_MATS) {
        assert.ok(fs.existsSync(path.join(__dirname, '..', '..', 'src', 'combat_sim', 'static', 'img', 'mats', mat.file)), mat.file);
    }
});

// ---------------------------------------------------------------------------
// Match view
// ---------------------------------------------------------------------------

test('matchStatLine writes a datasheet\'s characteristics as the cards do', () => {
    const line = Core.matchStatLine({ move: '5', toughness: 7, save: 2, wounds: 4, leadership: 6, oc: 1 });
    assert.deepEqual(line.map(x => `${x.label} ${x.value}`), ['M 5"', 'T 7', 'SV 2+', 'W 4', 'LD 6+', 'OC 1']);
    assert.equal(Core.matchStatLine({ move: '20+' })[0].value, '20+"');
    assert.deepEqual(Core.matchStatLine({}).map(x => x.value), ['-', '-', '-', '-', '-', '-']);
});

test('matchWeaponCells writes range, skill and AP as datasheets do', () => {
    assert.deepEqual(Core.matchWeaponCells({ range: 24, attacks: 'D6', skill: 3, strength: 4, ap: 1, damage: '1' }, 'ranged'),
        { range: '24"', attacks: 'D6', skill: '3+', strength: '4', ap: '-1', damage: '1' });
    const torrent = Core.matchWeaponCells({ range: 12, attacks: 'D6', skill: null, strength: 3, ap: 0, damage: '1' }, 'ranged');
    assert.equal(torrent.skill, 'N/A');
    assert.equal(torrent.ap, '0');
    assert.equal(Core.matchWeaponCells({ attacks: 4, skill: 2, strength: 8, ap: 2, damage: '2' }, 'melee').range, 'Melee');
});

test('matchStratagemShown filters stratagems by phase, turn and words', () => {
    const shoot = { name: 'Void Strike', phases: ['Shooting phase'], turn: 'Your turn', text: 'Lethal Hits' };
    const either = { name: 'Command Re-roll', phases: [], turn: 'Either player’s turn', text: '' };
    const theirs = { name: 'Smokescreen', phases: ['Shooting phase'], turn: 'Opponent’s turn', text: 'Stealth' };
    assert.ok(Core.matchStratagemShown(shoot));
    assert.ok(Core.matchStratagemShown(shoot, { phase: 'Shooting phase', turn: 'yours' }));
    assert.ok(!Core.matchStratagemShown(shoot, { phase: 'Fight phase' }));
    assert.ok(!Core.matchStratagemShown(shoot, { phase: 'other' }));
    assert.ok(Core.matchStratagemShown(either, { phase: 'Fight phase', turn: 'theirs' }));
    assert.ok(Core.matchStratagemShown(either, { phase: 'other' }));
    assert.ok(!Core.matchStratagemShown(theirs, { turn: 'yours' }));
    assert.ok(Core.matchStratagemShown(theirs, { turn: 'theirs', search: 'stealth' }));
    assert.ok(!Core.matchStratagemShown(theirs, { search: 'lethal' }));
});
