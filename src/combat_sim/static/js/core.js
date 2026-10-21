/**
 * Core - pure helpers shared by the pages (no DOM access, no fetch), so they can be unit-tested
 * in Node: `node --test tests/js`.
 *
 * Loaded on every page before the page scripts (window.Core); in Node, require() it.
 * Game rules (points by unit size, default weapon ticks, keyword parsing) are NOT duplicated here:
 * the server computes them (/api/units/<id> sends points_by_model_count and default_use,
 * /api/keywords/check checks keyword text).
 */
(function (root, factory) {
    const Core = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = Core;
    } else {
        root.Core = Core;
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // -------------------------------------------------------------------------
    // Text and formatting
    // -------------------------------------------------------------------------

    /** Escape text for HTML (element content and quoted attribute values). */
    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function truncate(str, maxLen) {
        str = String(str ?? '');
        return str.length <= maxLen ? str : str.substring(0, maxLen - 3) + '...';
    }

    /** Matrix cell text for a metric. */
    function formatMetricValue(value, metric) {
        value = value || 0;
        switch (metric) {
            case 'kill_probability':
            case 'pct_destroyed':
                return `${value}%`;
            case 'pct_per_100':
                return value.toFixed(2);
            case 'damage_per_point':
                return value.toFixed(3);
            default:
                return value.toFixed(2);
        }
    }

    /** Heat-map colour: red (min) -> yellow -> green (max). */
    function getHeatColor(value, min, max) {
        if (max === min) return 'rgba(255, 235, 59, 0.5)';
        const ratio = Math.min(1, Math.max(0, (value - min) / (max - min)));
        let r, g, b;
        if (ratio < 0.5) {
            const t = ratio * 2;
            r = 239;
            g = Math.round(68 + (235 - 68) * t);
            b = 68;
        } else {
            const t = (ratio - 0.5) * 2;
            r = Math.round(255 - (255 - 76) * t);
            g = Math.round(235 - (235 - 175) * t);
            b = Math.round(59 + (80 - 59) * t);
        }
        return `rgba(${r}, ${g}, ${b}, 0.7)`;
    }

    /** HTML for a /api/keywords/check result ({unknown, capitals}). Empty when all is well. */
    function keywordHintHtml(result) {
        const unknown = result?.unknown || [];
        const capitals = result?.capitals || [];
        let html = '';
        if (unknown.length) {
            html += `<span class="kw-hint-warn"><i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i> Not recognised, so it won't change the results: ${unknown.map(escapeHtml).join(', ')}. Check the Data → Keywords tab.</span>`;
        }
        if (capitals.length) {
            html += `<span class="kw-hint-info"><i class="fa-solid fa-circle-info" aria-hidden="true"></i> Works, but the capitals differ: ${capitals.map(([entry, name]) => `${escapeHtml(entry)} → ${escapeHtml(name)}`).join(', ')}</span>`;
        }
        return html;
    }

    // -------------------------------------------------------------------------
    // Units, weapons and points
    // -------------------------------------------------------------------------

    /** Unit name with "#n" appended when the same unit appears earlier in the list. */
    function copyLabel(list, index) {
        const entry = list[index];
        let copy = 0;
        for (let i = 0; i <= index; i++) {
            if (list[i].unit_id === entry.unit_id && list[i].unit_name === entry.unit_name) copy++;
        }
        return copy > 1 ? `${entry.unit_name} #${copy}` : entry.unit_name;
    }

    function defenderLabel(defenders, index) {
        return `${copyLabel(defenders, index)} (x${defenders[index].model_count})`;
    }

    /** Does a weapon pass the Shooting / Both / Melee filter ('ranged' | 'melee' | 'both')? */
    function weaponVisible(weapon, filter) {
        return filter === 'both' || weapon.weapon_type === filter;
    }

    /** Points from the server's table (index = model count), or null. */
    function pointsFor(pointsTable, models) {
        if (!pointsTable || !models) return null;
        return pointsTable[models] ?? null;
    }

    function pointsLabel(pointsTable, models) {
        const points = pointsFor(pointsTable, models);
        return points == null ? '' : `${points} pts`;
    }

    /**
     * Set an attacker unit's size, scaling each weapon's model count from its loadout quantity
     * (Excel's "model scale"). Mutates and returns the unit entry.
     */
    function scaleUnitSize(atk, size) {
        size = Math.max(1, size || atk.default_models || 1);
        if (atk.default_models) {
            const currentSize = atk.unit_size || atk.default_models;
            for (const w of atk.weapons) {
                if (w.leader) continue;  // The leader is one model whatever the unit's size
                // Entries saved before loadout quantities were kept: work the loadout quantity back out
                // from the current size once, so repeated resizing doesn't drift
                if (w.base_qty == null) w.base_qty = (w.models || 1) * atk.default_models / currentSize;
                w.models = Math.max(1, Math.round(w.base_qty * size / atk.default_models));
            }
        }
        atk.unit_size = size;
        return atk;
    }

    /**
     * Simulator: a unit's weapons for one run, from /api/units/<id>: its loadout (ticked as the
     * server's default_use says) followed by its wargear options (unticked, Qty 1, option: true).
     */
    function simulatorWeapons(unitData) {
        const weapons = {};
        for (const type of ['ranged', 'melee']) {
            const loadout = (unitData[`${type}_weapons`] || []).map(w => ({ ...w, qty: w.qty || 1, use: w.default_use ?? true }));
            const options = (unitData[`${type}_options`] || []).map(w => ({ ...w, qty: 1, use: false, option: true }));
            weapons[type] = loadout.concat(options);
        }
        return weapons;
    }

    /** Analytics: one weapon of an attacker unit (options start excluded, at Qty 1). */
    function matchupWeaponEntry(w, option = false) {
        return {
            weapon_id: w.id,
            weapon_type: w.type,
            name: w.name,
            range: w.range ?? null,
            attacks: w.attacks,
            skill: w.skill,
            strength: w.strength,
            ap: w.ap,
            damage: w.damage,
            abilities: w.abilities || [],
            models: option ? 1 : (w.qty || 1),  // Models in the unit carrying this weapon
            base_qty: option ? 1 : (w.qty || 1),  // ... at the datasheet unit size
            include: option ? false : (w.default_use ?? true),  // Server's default: only the first "Weapon - Profile" variant
            option,
        };
    }

    /** Analytics: all of a unit's weapon entries, loadout first, then wargear options. */
    function matchupWeapons(unitData) {
        const loadout = [...(unitData.ranged_weapons || []), ...(unitData.melee_weapons || [])];
        const options = [...(unitData.ranged_options || []), ...(unitData.melee_options || [])];
        return loadout.map(w => matchupWeaponEntry(w)).concat(options.map(w => matchupWeaponEntry(w, true)));
    }

    // -------------------------------------------------------------------------
    // Leaders
    // -------------------------------------------------------------------------

    const PHASE_LABEL = { Shooting: 'shooting', Fighting: 'melee', Both: '' };

    /** "Lethal Hits (shooting), Hit Modifier +1 · as a target: Invulnerable Save 4+" ('' if nothing). */
    function leaderBuffSummary(buffs) {
        const text = side => (buffs || []).filter(b => b.applies_to === side)
            .map(b => PHASE_LABEL[b.phase] ? `${b.modifier} (${PHASE_LABEL[b.phase]})` : b.modifier).join(', ');
        const attacker = text('Attacker'), target = text('Target');
        return [attacker, target && `as a target: ${target}`].filter(Boolean).join(' · ');
    }

    /** Leader weapons go after the unit's own loadout and before its wargear options. */
    function insertLeaderWeapons(own, added) {
        const at = own.findIndex(w => w.option);
        return at < 0 ? own.concat(added) : [...own.slice(0, at), ...added, ...own.slice(at)];
    }

    /** Simulator: the unit's weapons with its leader's loadout added (replacing a previous leader's). */
    function withLeaderWeapons(weapons, leader) {
        const result = {};
        for (const type of ['ranged', 'melee']) {
            const own = (weapons[type] || []).filter(w => !w.leader);
            const added = leader
                ? (leader[`${type}_weapons`] || []).map(w => ({ ...w, qty: w.qty || 1, use: w.default_use ?? true,
                                                                  leader: true, leader_name: leader.name }))
                : [];
            result[type] = insertLeaderWeapons(own, added);
        }
        return result;
    }

    /**
     * Analytics: an attacker unit entry from /api/units/<id> data, at `models` models (weapon counts
     * scaled), with `leader` (that leader's /api/units data, or null) attached.
     */
    /**
     * Analytics weapon entries set to a chosen loadout ([{weapon_id, weapon_type, qty, default_use}] from an
     * army's wargear): those weapons included with their counts, the others left out. `leaderOnly`:
     * the leader's entries (or, false, the unit's own).
     */
    function withLoadout(entries, loadout, leaderOnly = false) {
        if (!loadout) return entries;
        const rows = new Map(loadout.map(r => [`${r.weapon_type}:${r.weapon_id}`, r]));
        return entries.map(w => {
            if (!!w.leader !== leaderOnly) return w;
            const row = rows.get(`${w.weapon_type}:${w.weapon_id}`);
            return row ? { ...w, models: row.qty, base_qty: row.qty, include: row.default_use !== false }
                       : { ...w, include: false };
        });
    }

    function attackerEntry(unit, models = null, leader = null, enhancements = [], loadout = null, leaderLoadout = null) {
        const entry = {
            unit_id: unit.id,
            unit_name: unit.name,
            faction: unit.faction,
            faction_id: unit.faction_id,
            default_models: unit.default_models || null,
            unit_size: unit.default_models || null,
            points_table: unit.points_by_model_count || [],
            weapons: matchupWeapons(unit),  // Loadout, then wargear options (not included)
            leaders: unit.leaders || [],
            leader_id: null,
            enhancements: enhancements || [],  // From an army: [{id, bearer: 'unit'|'leader', name}]
        };
        if (loadout) {  // Chosen wargear: its weapons and counts, at the size it was chosen for
            entry.weapons = withLoadout(entry.weapons, loadout);
            entry.unit_size = models || entry.unit_size;
            entry.default_models = models || entry.default_models;
        } else if (models && unit.default_models) {
            scaleUnitSize(entry, models);
        }
        if (leader) {
            const choice = entry.leaders.find(l => l.id === leader.id);
            entry.weapons = withLoadout(withLeaderEntries(entry.weapons, leader), leaderLoadout, true);
            entry.leader_id = leader.id;
            entry.leader_name = leader.name;
            entry.leader_points = choice?.points ?? null;
        }
        return entry;
    }

    /** Analytics: a defender unit entry from /api/units/<id> data, with a leader's id or null (and an army's enhancements). */
    function defenderEntry(unit, modelCount, leaderId = null, enhancements = []) {
        return {
            leaders: unit.leaders || [],
            leader_id: leaderId,
            unit_id: unit.id,
            unit_name: unit.name,
            faction: unit.faction,
            faction_id: unit.faction_id,
            points_table: unit.points_by_model_count || [],
            toughness: unit.toughness,
            save: unit.save,
            wounds: unit.wounds,
            invulnerable_save: unit.invulnerable_save,
            feel_no_pain: unit.feel_no_pain,
            model_count: modelCount,
            enhancements: enhancements || [],
        };
    }

    /** Armies: Detachment Points spent against the battle size's, as {text, over} (an Incursion's single 3 DP detachment is checked by the server). */
    function armyDetachmentPoints(spent, limit) {
        return { text: limit != null ? `${spent} / ${limit} DP` : `${spent} DP`, over: limit != null && spent > limit };
    }

    /** Armies: an army's points against its limit, as {text, over}. */
    function armyPoints(total, limit) {
        const fmt = n => Number(n || 0).toLocaleString('en-GB');
        return { text: limit ? `${fmt(total)} / ${fmt(limit)} pts` : `${fmt(total)} pts`, over: !!limit && total > limit };
    }

    /** Analytics: an attacker's weapon entries with its leader's loadout added (replacing a previous leader's). */
    function withLeaderEntries(entries, leader) {
        const own = (entries || []).filter(e => !e.leader);
        if (!leader) return own;
        const loadout = [...(leader.ranged_weapons || []), ...(leader.melee_weapons || [])];
        return insertLeaderWeapons(own, loadout.map(w => ({ ...matchupWeaponEntry(w), leader: true, leader_name: leader.name })));
    }

    /**
     * <option>s for a detachment dropdown: "Whole faction only", then the detachments (grouped
     * by faction when there are several). The value is the detachment's BSData key.
     */
    function detachmentOptionsHtml(detachments, selectedKey) {
        const option = d => `<option value="${escapeHtml(d.key)}"${d.key === selectedKey ? ' selected' : ''}>${escapeHtml(d.name)}</option>`;
        let html = `<option value=""${selectedKey ? '' : ' selected'}>No detachment (faction-wide only)</option>`;
        const factions = [...new Set((detachments || []).map(d => d.faction))];
        if (factions.length <= 1) return html + (detachments || []).map(option).join('');
        for (const faction of factions) {
            html += `<optgroup label="${escapeHtml(faction)}">${detachments.filter(d => d.faction === faction).map(option).join('')}</optgroup>`;
        }
        return html;
    }

    /** Chosen detachment keys from saved state: a list, or one key as first saved, or nothing. */
    function detachmentKeys(value) {
        if (Array.isArray(value)) return [...new Set(value.filter(Boolean).map(String))];
        return value ? [String(value)] : [];
    }

    /**
     * The saved unit among `units`: by its ID, else by name within its faction, else by name
     * (scenarios saved before the ID). The same name can be in several factions (Harlequins).
     */
    function findSavedUnit(units, config) {
        const named = (units || []).filter(u => u.name === config.unit_name);
        return (units || []).find(u => config.unit_id != null && u.id === config.unit_id)
            || named.find(u => config.faction_id != null && u.faction_id === config.faction_id)
            || named[0] || null;
    }

    /** "No detachment", one name, or "A + B" for the chosen detachments. */
    function detachmentSummary(detachments, chosenKeys) {
        const names = (detachments || []).filter(d => (chosenKeys || []).includes(d.key)).map(d => d.name);
        return names.length ? names.join(' + ') : 'No detachment (faction-wide only)';
    }

    /**
     * A detachment checklist (inside a <details class="detachment-picker">): its summary line and
     * a checkbox per detachment with its Detachment Points, grouped by faction when there are several.
     */
    function detachmentPickerHtml(detachments, chosenKeys) {
        const chosen = new Set(chosenKeys || []);
        const box = d => `<label class="detachment-option">
            <input type="checkbox" data-key="${escapeHtml(d.key)}"${chosen.has(d.key) ? ' checked' : ''}>
            <span>${escapeHtml(d.name)}</span>${d.dp != null ? `<span class="detachment-dp">${d.dp} DP</span>` : ''}</label>`;
        const factions = [...new Set((detachments || []).map(d => d.faction))];
        const body = factions.length <= 1 ? (detachments || []).map(box).join('')
            : factions.map(f => `<div class="detachment-faction">${escapeHtml(f || '')}</div>` +
                detachments.filter(d => d.faction === f).map(box).join('')).join('');
        return `<summary title="Detachments: offer their rules and stratagems too">${escapeHtml(detachmentSummary(detachments, chosenKeys))}</summary>
            <div class="detachment-options">${body}</div>`;
    }

    // -------------------------------------------------------------------------
    // BSData rule text
    // -------------------------------------------------------------------------

    const LIST_ITEM = /^\s*[-▪■•]\s+/;

    /**
     * One line of BSData text as HTML: **bold**, ^^keyword^^ (in any mix with **, e.g.
     * ^^**Character^^** or **^^Monster^^ or ^^Vehicle^^**), *italic* and <ins>. Markers are read
     * in order, closing and reopening tags at each one, so the HTML is always well formed;
     * anything left open closes at the end of the line.
     */
    function formatRuleLine(line) {
        let bold = false, keyword = false, html = '';
        const open = () => (bold ? '<strong>' : '') + (keyword ? '<span class="rule-keyword">' : '');
        const close = () => (keyword ? '</span>' : '') + (bold ? '</strong>' : '');
        for (const part of escapeHtml(line).split(/(\^\^\*\*|\*\*\^\^|\^\^|\*\*)/)) {
            if (part === '^^**' || part === '**^^') {
                html += close(); bold = !bold; keyword = !keyword; html += open();
            } else if (part === '^^') {
                html += close(); keyword = !keyword; html += open();
            } else if (part === '**') {
                html += close(); bold = !bold; html += open();
            } else {
                html += part
                    .replace(/(^|[^\w*])\*(?=\S)([^*]*?\S)\*(?![\w*])/g, '$1<em>$2</em>')
                    .replace(/(^|\s)\*+(?=\s|$)|(^|\s)\*+(?=\S)(?![^*]*\*)|\*+$/g, '$1$2');  // Stray markers (BSData typos)
            }
        }
        html += close();
        return html
            .replace(/&lt;ins&gt;(.*?)&lt;\/ins&gt;/g, '<ins>$1</ins>')
            .replace(/<strong><\/strong>|<span class="rule-keyword"><\/span>/g, '');
    }

    /**
     * BSData rule or ability text as safe HTML: paragraphs at blank lines, line breaks, bullet
     * lists (lines starting "- ", "▪", "■" or "•"), "a | b" tables and the inline markup of
     * formatRuleLine. The text is escaped first, so nothing in it can add HTML of its own.
     * labels: bold a short "Name:" starting a paragraph (unit abilities are stored as "Name: text").
     */
    function formatRuleText(text, { labels = false } = {}) {
        const blocks = String(text ?? '').replace(/\r/g, '').split(/\n{2,}/).filter(b => b.trim());
        return blocks.map(block => {
            const lines = block.split('\n').filter(l => l.trim());
            if (lines.filter(l => l.includes('|')).length >= 2) {
                const rows = lines.map(l => l.split('|').map(cell => formatRuleLine(cell.trim())));
                const [head, ...body] = rows;
                return '<table class="rule-table"><thead><tr>' + head.map(c => `<th>${c}</th>`).join('') +
                    '</tr></thead><tbody>' + body.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('') +
                    '</tbody></table>';
            }
            let html = '', paragraph = [], items = [];
            const flush = () => {
                if (paragraph.length) html += `<p>${paragraph.join('<br>')}</p>`;
                if (items.length) html += `<ul>${items.map(i => `<li>${i}</li>`).join('')}</ul>`;
                paragraph = [];
                items = [];
            };
            for (const line of lines) {
                if (LIST_ITEM.test(line)) {
                    if (paragraph.length) flush();
                    items.push(formatRuleLine(line.replace(LIST_ITEM, '')));
                } else {
                    if (items.length) flush();
                    const label = labels && !paragraph.length && !html && line.match(/^([^:.*^]{1,60}):\s+(.*)$/);
                    paragraph.push(label
                        ? `<strong class="rule-label">${escapeHtml(label[1])}:</strong> ${formatRuleLine(label[2])}`
                        : formatRuleLine(line.trim()));
                }
            }
            flush();
            return html;
        }).join('');
    }

    /** Simulator: ticked weapons of the current phase. */
    function usedWeapons(weapons, weaponType) {
        return (weapons[weaponType] || []).filter(w => w.use);
    }

    /** Simulator: one weapon as /api/simulate expects it. */
    function weaponPayload(w, fallbackType) {
        return {
            name: w.name,
            type: w.type || fallbackType,
            attacks: w.attacks,
            skill: w.skill,
            strength: w.strength,
            ap: w.ap,
            damage: w.damage,
            abilities: w.abilities || [],
            models: w.qty || 1,
        };
    }

    // -------------------------------------------------------------------------
    // Rules and stratagems
    // -------------------------------------------------------------------------

    const PHASES_FOR_FILTER = {
        ranged: ['Shooting', 'Both'],
        melee: ['Fighting', 'Both'],
        both: ['Shooting', 'Fighting', 'Both'],
    };

    /** Rules usable with a weapon type or the Analytics filter ('ranged' | 'melee' | 'both'). */
    function rulesForFilter(entries, filter) {
        const phases = PHASES_FOR_FILTER[filter] || PHASES_FOR_FILTER.both;
        return (entries || []).filter(e => phases.includes(e.phase));
    }

    /** Tooltip of a rule/stratagem in a tick list: whether it changes the maths, and any conditions left to the player. */
    function ruleRowTitle(entry) {
        const title = entry.has_effect ? 'Changes the calculations' : 'No effect on the calculations (listed for reference)';
        return entry.conditions?.length ? `${title}. Tick it only when: ${entry.conditions.join('; ')}` : title;
    }

    /**
     * Plotly shapes and annotations marking the target's total wounds on a damage distribution
     * chart (bars on a category axis: bar i spans i - 0.5 to i + 0.5). Runs at or right of the
     * line dealt at least that much damage. Empty when the distribution doesn't say.
     */
    function woundsMarker(distribution, color) {
        const starts = distribution?.starts, wounds = distribution?.unit_wounds;
        if (!starts?.length || !wounds) return { shapes: [], annotations: [] };
        const width = starts.length > 1 ? starts[1] - starts[0] : 1;
        const x = (wounds - starts[0]) / width - 0.5;
        if (x > starts.length - 0.5) return { shapes: [], annotations: [] };  // No run reached it
        return {
            shapes: [{ type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x, y0: 0, y1: 1,
                       line: { color, width: 2, dash: 'dash' } }],
            annotations: [{ xref: 'x', yref: 'paper', x, y: 1, yanchor: 'bottom', showarrow: false,
                            text: `Unit's wounds (${wounds})`, font: { color, size: 10 } }],
        };
    }

    /** {rule_ids, stratagem_ids} from tick keys like 'rule:3' / 'stratagem:7'. */
    function rulesSelection(tickedKeys) {
        const selection = { rule_ids: [], stratagem_ids: [] };
        for (const key of tickedKeys || []) {
            const [kind, id] = String(key).split(':');
            (kind === 'rule' ? selection.rule_ids : selection.stratagem_ids).push(parseInt(id, 10));
        }
        return selection;
    }

    // -------------------------------------------------------------------------
    // Analytics request
    // -------------------------------------------------------------------------

    /**
     * The /api/matrix/simulate request for the Analytics page's state:
     * {attackers, defenders, conditions, weaponTypeFilter, defenderMode, includedProfiles,
     *  attackerTicked, defenderTicked}
     */
    /** An Analytics entry's enhancements that apply: its own, and its leader's while that leader leads it. */
    function activeEnhancements(entry) {
        return (entry.enhancements || []).filter(e => e.bearer !== 'leader' || (entry.leader_id && entry.leader_id === e.unit_id));
    }

    /** An entry's enhancements as the server takes them ([{id, bearer}]). */
    function enhancementRefs(entry) {
        return activeEnhancements(entry).map(e => ({ id: e.id, bearer: e.bearer }));
    }

    function buildMatrixRequest(page) {
        const filter = page.weaponTypeFilter || 'both';
        const attackers = page.attackers.map((atk, a) => ({
            label: copyLabel(page.attackers, a),
            unit_id: atk.unit_id,
            leader_id: atk.leader_id || null,
            enhancements: enhancementRefs(atk),
            weapons: atk.weapons
                .filter(w => w.include && weaponVisible(w, filter))
                .map(w => ({ weapon_id: w.weapon_id, weapon_type: w.weapon_type, models: w.models,
                             label: w.leader ? `${w.name} (${w.leader_name})` : w.name,
                             unit_id: atk.unit_id, leader_id: atk.leader_id || null,
                             leader: !!w.leader, enhancements: enhancementRefs(atk) })),
            unit_size: atk.unit_size || atk.default_models || null,
        })).filter(atk => atk.weapons.length > 0);

        const defenders = page.defenderMode === 'generic'
            ? (page.includedProfiles || []).map(t => ({ profile_id: t.id, model_count: t.models, label: t.name }))
            : page.defenders.map((d, idx) => ({
                unit_id: d.unit_id,
                model_count: d.model_count,
                label: defenderLabel(page.defenders, idx) + (d.leader_id && d.leader_name ? ` + ${d.leader_name}` : ''),
                extra_modifiers: d.extra_modifiers || '',
                leader_id: d.leader_id || null,
                enhancements: enhancementRefs(d),
            }));

        return {
            attackers,
            defenders,
            conditions: { ...(page.conditions || {}) },
            weapon_filter: filter,
            attacker_rules: rulesSelection(page.attackerTicked),
            defender_rules: rulesSelection(page.defenderTicked),
        };
    }

    // -------------------------------------------------------------------------
    // Army Analysis panel (HTML from the server's analysis)
    // -------------------------------------------------------------------------

    function statTile(value, label, note = '') {
        return `<div class="analysis-stat">
            <span class="analysis-value">${value}</span>
            <span class="analysis-label">${label}</span>
            ${note ? `<span class="analysis-note">${note}</span>` : ''}
        </div>`;
    }

    function renderAnalysisSummary(analysis) {
        const s = analysis.summary;
        const weighted = s.weighted ? 'weighted by profile' : 'all targets equal';
        let html = `<h3>My army</h3><div class="analysis-stats">
            ${statTile(s.units, 'Units')}
            ${statTile(s.points, 'Points')}
            ${statTile(`${s.wiped} of ${s.targets}`, 'Enemy units wiped in one turn')}
            ${statTile(`${s.avg_pct_destroyed}%`, 'Avg % of each target destroyed', weighted)}
            ${statTile(s.pct_per_100 != null ? s.pct_per_100.toFixed(2) : '-', 'Avg % per 100 pts')}
            ${statTile(s.best_unit ? escapeHtml(s.best_unit.label) : '-', 'Best unit (% per 100 pts)',
                       s.best_unit ? `${s.best_unit.pct_per_100.toFixed(2)} % per 100 pts` : '')}
        </div>`;
        const r = analysis.reverse?.summary;
        if (r) {
            html += `<h3>Opponent</h3><div class="analysis-stats">
                ${statTile(r.points ?? '-', 'Points')}
                ${statTile(`${r.wiped} of ${r.units}`, 'My units they wipe in one turn')}
                ${statTile(`${r.avg_pct_destroyed}%`, 'Avg % of my units destroyed')}
                ${statTile(r.biggest_threat ? escapeHtml(r.biggest_threat) : '-', 'Biggest threat')}
            </div>`;
        }
        return html;
    }

    function renderGaps(analysis) {
        if (!analysis.gaps.length) {
            return '<p class="analysis-empty"><i class="fa-solid fa-check" aria-hidden="true"></i> Your army can wipe every target in one turn.</p>';
        }
        return `<p class="analysis-intro">Targets your whole army can't wipe out in one turn, most survivors first.</p>
            <table class="breakdown-table analysis-table">
                <thead><tr><th>Target</th><th>Models</th><th>Survivors</th><th>% destroyed</th></tr></thead>
                <tbody>${analysis.gaps.map(g => `<tr>
                    <td>${escapeHtml(g.label)}</td><td>${g.models}</td><td>${g.survivors}</td><td>${g.pct_destroyed}%</td>
                </tr>`).join('')}</tbody>
            </table>`;
    }

    function renderRoleScores(analysis) {
        if (!analysis.roles.length) {
            return '<p class="analysis-empty">No role benchmarks in the data file.</p>';
        }
        return `<p class="analysis-intro">% of each benchmark unit your whole army destroys in one turn (100% = wiped; above 100% is spare killing power).</p>
            <div class="analysis-columns">
                <table class="breakdown-table analysis-table">
                    <thead><tr><th>Role</th><th>Models</th><th>Army kills</th><th>Score</th></tr></thead>
                    <tbody>${analysis.roles.map(r => `<tr>
                        <td>${escapeHtml(r.name)}</td><td>${r.models}</td><td>${r.kills}</td>
                        <td class="${r.score >= 100 ? 'score-good' : ''}">${r.score}%</td>
                    </tr>`).join('')}</tbody>
                </table>
                <div id="roles-chart" class="roles-chart"></div>
            </div>`;
    }

    function renderCoverage(analysis) {
        const c = analysis.coverage;
        const values = c.damage.flat();
        const min = Math.min(...values), max = Math.max(...values);
        return `<p class="analysis-intro">Expected damage from your whole army against each Toughness and Save
            (no invulnerable save, Feel No Pain or keywords; damage not capped by wounds).</p>
            <div class="matrix-scroll"><table class="matrix-table coverage-table">
                <thead><tr><th class="matrix-corner">T ↓ Sv →</th>${c.saves.map(sv => `<th class="matrix-header-cell">${sv}+</th>`).join('')}</tr></thead>
                <tbody>${c.toughness.map((t, i) => `<tr><th class="matrix-row-header">T${t}</th>
                    ${c.damage[i].map(v => `<td class="matrix-cell coverage-cell" style="background-color: ${getHeatColor(v, min, max)}">${v.toFixed(1)}</td>`).join('')}
                </tr>`).join('')}</tbody>
            </table></div>`;
    }

    function renderReverse(analysis) {
        const r = analysis.reverse;
        if (!r) {
            return '<p class="analysis-empty">Reverse durability needs real defender units: switch Defenders to <strong>Real units</strong> and run the matrix again.</p>';
        }
        if (!r.rows.length) return '<p class="analysis-empty">No attacker units to check.</p>';
        return `<p class="analysis-intro">% of each of your units the opponent destroys in one turn (their default loadouts,
            same conditions and phase filter, no opponent rules).</p>
            <div class="matrix-scroll"><table class="matrix-table">
                <thead><tr><th class="matrix-corner">My unit ↓ Enemy →</th>
                    ${r.enemies.map(e => `<th class="matrix-header-cell" title="${escapeHtml(e)}">${escapeHtml(truncate(e, 20))}</th>`).join('')}
                    <th class="matrix-header-cell">Total threat</th></tr></thead>
                <tbody>${r.rows.map(row => `<tr><th class="matrix-row-header">${escapeHtml(truncate(row.label, 28))}</th>
                    ${row.cells.map(v => `<td class="matrix-cell" style="background-color: ${getHeatColor(100 - v, 0, 100)}">${v}%</td>`).join('')}
                    <td class="matrix-cell matrix-total-cell">${row.total}%</td>
                </tr>`).join('')}</tbody>
            </table></div>`;
    }

    // -------------------------------------------------------------------------
    // Page state across tab switches
    // -------------------------------------------------------------------------

    /**
     * Saved page setup that survives switching tabs but not restarting the app: kept in the
     * given storage (sessionStorage in the browser) and tagged with the server's boot ID.
     */
    function createPageState(storage, bootId) {
        return {
            bootId,
            load(key) {
                try {
                    const saved = JSON.parse(storage.getItem(key));
                    return saved && saved.bootId === bootId ? saved.data : null;
                } catch (err) {
                    return null;
                }
            },
            save(key, data) {
                try {
                    storage.setItem(key, JSON.stringify({ bootId, data }));
                } catch (err) {
                    // Storage unavailable or full: the page just won't be restored
                }
            },
        };
    }

    // -------------------------------------------------------------------------
    // Play mode: board layout (inches; the server checks every placement)
    // -------------------------------------------------------------------------

    /**
     * Where a unit's models go when it is dropped at (cx, cy): rows of up to 5, bases almost touching
     * (0.1" apart, spaced by the biggest base), the rows centred on the point. `sizes` are base lengths in inches.
     * The first row faces the enemy and the last (where an attached leader goes) is at the back: lower y for
     * the player on the bottom edge, higher y with `topEdge`.
     */
    function playFormation(sizes, cx, cy, topEdge = false) {
        const spacing = Math.max(...sizes) + 0.1;
        const rows = Math.ceil(sizes.length / 5);
        const round = v => Math.round(v * 100) / 100;
        return sizes.map((_, i) => {
            const row = Math.floor(i / 5);
            const inRow = Math.min(5, sizes.length - row * 5);
            return {
                x: round(cx + ((i % 5) - (inRow - 1) / 2) * spacing),
                y: round(cy + ((rows - 1) / 2 - row) * spacing * (topEdge ? -1 : 1)),
            };
        });
    }

    /**
     * A formation moved (as little as possible) to lie wholly inside `area` ({left, bottom, right, top}),
     * each model reaching `reach[i]` inches from its centre. Left where it is if it can't fit either way.
     */
    function playFit(positions, reach, area) {
        const round = v => Math.round(v * 100) / 100;
        const shift = (low, high, min, max) => {
            if (high - low > max - min) return 0;
            if (low < min) return min - low;
            if (high > max) return max - high;
            return 0;
        };
        const lows = (axis) => Math.min(...positions.map((p, i) => p[axis] - reach[i]));
        const highs = (axis) => Math.max(...positions.map((p, i) => p[axis] + reach[i]));
        const dx = shift(lows('x'), highs('x'), area.left, area.right);
        const dy = shift(lows('y'), highs('y'), area.bottom, area.top);
        return positions.map(p => ({ x: round(p.x + dx), y: round(p.y + dy) }));
    }

    /** The distance a model has been dragged ({text: '5.0" of 6"', over}), against how far it may move. */
    function playMoveLabel(dx, dy, allowance, before = 0) {
        const distance = before + Math.hypot(dx, dy);
        return { text: `${distance.toFixed(1)}" of ${allowance}"`, over: distance > allowance + 1e-6 };
    }

    /** Play: a model's path this phase, from where it started by way of its waypoints to where it stands. */
    function playPath(start, via, now) {
        const points = [start, ...(via || [])];
        const last = points[points.length - 1];
        return last.x === now.x && last.y === now.y ? points : [...points, now];
    }

    /** Play: how long a path ([{x, y}, ...]) is, in inches. */
    function playPathLength(points) {
        return points.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - points[i].x, p.y - points[i].y), 0);
    }

    /**
     * An attack's dice for the dice log: the event's rolls from `range` ([first, last)) as groups
     * {label, need ('3+' or null), note, dice: [{face, kind, rerolled_from}]}, kind being 'critical',
     * 'success', 'fail' or 'value' (a rolled amount such as damage). Rolls of the same kind in a row
     * (saves are rolled one wound at a time) show as one group.
     */
    function playDiceGroups(rolls, range) {
        const label = purpose => purpose.replace(/\b\w/g, c => c.toUpperCase());
        const groups = [];
        for (const roll of rolls.slice(range[0], range[1])) {
            const value = roll.need == null;
            const dice = roll.dice.map(d => ({
                face: d.face, rerolled_from: d.rerolled_from ?? null,
                kind: value ? 'value' : d.critical ? 'critical' : d.success ? 'success' : 'fail',
            }));
            const need = value ? null : `${roll.need}+`;
            const note = value ? `${roll.dice.length > 1 ? roll.dice.length : ''}D${roll.size}${roll.modifier ? (roll.modifier > 0 ? '+' : '') + roll.modifier : ''} = ${roll.total}` : null;
            const last = groups[groups.length - 1];
            if (last && last.purpose === roll.purpose && last.need === need) {
                last.dice.push(...dice);
                if (note) last.note = last.note ? `${last.note}, ${note.split(' = ')[1]}` : note;
            } else {
                groups.push({ purpose: roll.purpose, label: label(roll.purpose), need, note, dice });
            }
        }
        return groups.map(({ purpose, ...group }) => group);
    }

    /** One line saying what an attack did ('Bolter (2 models): 4 attacks, 3 hits, 2 wounds, 1 model killed'). */
    function playAttackSummary(attack) {
        const count = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
        const killed = attack.killed.length ? count(attack.killed.length, 'model') + ' killed' : 'none killed';
        if (attack.name === 'Hazardous') return `Hazardous: ${attack.models} rolled, ${killed}`;
        const who = `${count(attack.models, 'model')}${attack.half_range ? ', half range' : ''}${attack.cover ? ', target in cover' : ''}`;
        const parts = [count(attack.attacks, 'attack'), count(attack.hits, 'hit'), count(attack.wounds, 'wound')];
        if (attack.mortal) parts.push(count(attack.mortal, 'mortal wound'));
        return `${attack.name} (${who}): ${parts.join(', ')}, ${killed}`;
    }

    /**
     * Who shot (or fought) whom since the current phase began (the last event that changed the turn): one
     * {from, to} per shooting unit and target, in order. Hazardous rolls aren't shots.
     */
    function playShotLines(events) {
        const lines = [];
        for (let i = events.length - 1; i >= 0 && !events[i].turn; i--) {
            const event = events[i];
            if (event.action.type !== 'shoot' && event.action.type !== 'fight') continue;
            const targets = [...new Set((event.attacks || []).filter(a => a.name !== 'Hazardous').map(a => a.target))];
            lines.unshift(...targets.map(to => ({ from: event.action.unit, to })));
        }
        return lines;
    }

    /**
     * A terrain footprint's corners ([x, y], inches): its shape `corners` (from the server's
     * /api/play/footprints, measured from its own corner) placed with that corner at (x, y), turned over
     * first if `flip`, then turned `angle` degrees anticlockwise about the corner, as geometry.footprint does.
     */
    function playFootprintPoints(corners, x, y, angle, flip = false) {
        const c = Math.cos(angle * Math.PI / 180), s = Math.sin(angle * Math.PI / 180);
        return corners.map(([px, py]) => [px, flip ? -py : py]).map(([px, py]) => [x + px * c - py * s, y + px * s + py * c]);
    }

    /**
     * Play: battlefields (/api/play/battlefields) in groups for lists: the deployment maps, the Event
     * Companion layouts by pairing of dispositions ('X vs Y: Layout A' -> group 'X vs Y', item 'Layout A'),
     * then saved battlefields. [{label, kind, items: [{...battlefield, label}]}]
     */
    function playBattlefieldGroups(list) {
        const groups = [];
        const group = (label, kind) => {
            let found = groups.find(g => g.label === label && g.kind === kind);
            if (!found) groups.push(found = { label, kind, items: [] });
            return found;
        };
        group('Deployment maps', 'map');
        for (const b of list.filter(b => b.kind === 'map')) group('Deployment maps', 'map').items.push({ ...b, label: b.name });
        for (const b of list.filter(b => b.kind === 'layout')) {
            const [pairing, letter] = b.name.split(': ');
            group(pairing, 'layout').items.push({ ...b, label: letter || b.name });
        }
        const saved = list.filter(b => b.kind !== 'map' && b.kind !== 'layout');
        if (saved.length) group('Saved battlefields', 'saved').items.push(...saved.map(b => ({ ...b, label: b.name })));
        return groups.filter(g => g.items.length);
    }

    /** A value rounded to the nearest `step` (the editor's snapping). */
    function playSnap(value, step) {
        return Math.round(value / step) * step;
    }

    /** The box round some points ([x, y]): {left, bottom, right, top}. */
    function playBounds(points) {
        const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
        return { left: Math.min(...xs), bottom: Math.min(...ys), right: Math.max(...xs), top: Math.max(...ys) };
    }

    /** The middle of some positions ({x, y}), or null when there are none. */
    function playCentre(positions) {
        if (!positions.length) return null;
        return { x: positions.reduce((n, p) => n + p.x, 0) / positions.length,
                 y: positions.reduce((n, p) => n + p.y, 0) / positions.length };
    }

    /**
     * The stratagems and rules to offer in a phase: those for it ('Shooting', 'Fighting' or 'Both'),
     * and those the app has no phase for (it reads phases only for shooting and fighting).
     */
    function playUsableNow(entries, phase) {
        const BATTLE = ['command', 'movement', 'shooting', 'charge', 'fight'];
        if (!BATTLE.includes(phase)) return [];
        const fits = { shooting: ['Shooting', 'Both'], fight: ['Fighting', 'Both'] }[phase] || [];
        return entries.filter(e => !e.phase || fits.includes(e.phase));
    }

    /**
     * The phase track above the board: each phase of a player's turn with its icon and whether it is
     * 'done', 'current' or 'next' this turn ('step' names the Fight phase's step while in it).
     */
    function playPhaseTrack(turn) {
        const PHASES = [['command', 'Command', 'crown'], ['movement', 'Movement', 'person-running'],
                        ['shooting', 'Shooting', 'crosshairs'], ['charge', 'Charge', 'bolt'], ['fight', 'Fight', 'hand-fist']];
        const STEPS = { pile_in: 'Pile in', fight: 'Fights', consolidate: 'Consolidate' };
        const at = PHASES.findIndex(([key]) => key === turn.phase);
        return PHASES.map(([key, label, icon], i) => ({
            key, label, icon,
            state: turn.phase === 'ended' ? 'done' : at < 0 ? 'next' : i < at ? 'done' : i === at ? 'current' : 'next',
            step: i === at && turn.step ? STEPS[turn.step] : null,
        }));
    }

    /** 'Game over: Blue wins, 120 to 60' once the battle has ended, else null. */
    /**
     * Play: what the setup bar says and offers in a game's setup phase (the mission sequence), or null
     * once it's over. Buttons carry the action to send.
     */
    function playSetupPrompt(turn, players) {
        if (!turn || turn.phase !== 'setup') return null;
        if (turn.step === 'objectives') {
            return { text: 'Set up the battlefield: roll a D6 for the central objective (on a 6 there are two).',
                buttons: [{ action: { type: 'roll_objectives' }, label: 'Roll for the central objective', icon: 'dice' }] };
        }
        if (turn.step === 'roll_off') {
            return { text: 'Roll off: the winner chooses to be the attacker or the defender. The defender deploys first.',
                buttons: [{ action: { type: 'roll_off' }, label: 'Roll off', icon: 'dice' }] };
        }
        const name = players[turn.player].name;
        return { text: `${name} won the roll-off: attacker or defender?`,
            buttons: ['attacker', 'defender'].map(role => ({ action: { type: 'choose_role', role },
                label: `${name} ${role === 'attacker' ? 'attacks' : 'defends'}`, icon: role === 'attacker' ? 'khanda' : 'shield-halved' })) };
    }

    /** Play: a roll-off for the log, re-rolled ties and all. */
    function playRollOff(result, players) {
        const rounds = result.rolls.map(([a, b]) => `${players[0].name} ${a}, ${players[1].name} ${b}${a === b ? ' (tie)' : ''}`);
        return `${rounds.join(', then ')}: ${players[result.winner].name} wins`;
    }

    function playResult(players, score, turn) {
        if (!turn || turn.phase !== 'ended') return null;
        if (score[0] === score[1]) return `Game over: a draw, ${score[0]} each`;
        const winner = score[0] > score[1] ? 0 : 1;
        return `Game over: ${players[winner].name} wins, ${score[winner]} to ${score[1 - winner]}`;
    }

    /** The board view ({x, y, w, h} in inches) zoomed by `factor` about the point (px, py), 10" to 72" wide. */
    function playZoom(view, factor, px, py) {
        const w = Math.min(72, Math.max(10, view.w * factor));
        const scale = w / view.w;
        return { x: px - (px - view.x) * scale, y: py - (py - view.y) * scale, w, h: view.h * scale };
    }

    return {
        playFormation, playFit, playMoveLabel, playPath, playPathLength, playZoom, playUsableNow, playResult, playSetupPrompt, playRollOff, playPhaseTrack, playDiceGroups, playAttackSummary, playShotLines, playCentre, playBounds, playFootprintPoints, playBattlefieldGroups, playSnap,
        escapeHtml, truncate, formatMetricValue, getHeatColor, keywordHintHtml,
        copyLabel, defenderLabel, weaponVisible, pointsFor, pointsLabel, scaleUnitSize,
        simulatorWeapons, matchupWeaponEntry, matchupWeapons, detachmentOptionsHtml, detachmentKeys, detachmentSummary,
        detachmentPickerHtml, findSavedUnit, formatRuleLine, formatRuleText,
        leaderBuffSummary, withLeaderWeapons, withLeaderEntries, withLoadout, attackerEntry, defenderEntry, activeEnhancements, enhancementRefs, armyPoints, armyDetachmentPoints,
        usedWeapons, weaponPayload, rulesForFilter, ruleRowTitle, rulesSelection, woundsMarker, buildMatrixRequest,
        statTile, renderAnalysisSummary, renderGaps, renderRoleScores, renderCoverage, renderReverse,
        createPageState,
    };
});
