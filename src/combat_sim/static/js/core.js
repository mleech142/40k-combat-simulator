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
            html += `<span class="kw-hint-warn">⚠ Not recognised, so it won't change the results: ${unknown.map(escapeHtml).join(', ')}. Check the Data → Keywords tab.</span>`;
        }
        if (capitals.length) {
            html += `<span class="kw-hint-info">ℹ Works, but the capitals differ: ${capitals.map(([entry, name]) => `${escapeHtml(entry)} → ${escapeHtml(name)}`).join(', ')}</span>`;
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

    /** Matchups: one weapon of an attacker unit (options start excluded, at Qty 1). */
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

    /** Matchups: all of a unit's weapon entries, loadout first, then wargear options. */
    function matchupWeapons(unitData) {
        const loadout = [...(unitData.ranged_weapons || []), ...(unitData.melee_weapons || [])];
        const options = [...(unitData.ranged_options || []), ...(unitData.melee_options || [])];
        return loadout.map(w => matchupWeaponEntry(w)).concat(options.map(w => matchupWeaponEntry(w, true)));
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

    /** Rules usable with a weapon type or the Matchups filter ('ranged' | 'melee' | 'both'). */
    function rulesForFilter(entries, filter) {
        const phases = PHASES_FOR_FILTER[filter] || PHASES_FOR_FILTER.both;
        return (entries || []).filter(e => phases.includes(e.phase));
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
    // Matchups request
    // -------------------------------------------------------------------------

    /**
     * The /api/matrix/simulate request for the Matchups page's state:
     * {attackers, defenders, conditions, weaponTypeFilter, defenderMode, includedProfiles,
     *  attackerTicked, defenderTicked}
     */
    function buildMatrixRequest(page) {
        const filter = page.weaponTypeFilter || 'both';
        const attackers = page.attackers.map((atk, a) => ({
            label: copyLabel(page.attackers, a),
            unit_id: atk.unit_id,
            weapons: atk.weapons
                .filter(w => w.include && weaponVisible(w, filter))
                .map(w => ({ weapon_id: w.weapon_id, weapon_type: w.weapon_type, models: w.models, label: w.name,
                             unit_id: atk.unit_id })),
            unit_size: atk.unit_size || atk.default_models || null,
        })).filter(atk => atk.weapons.length > 0);

        const defenders = page.defenderMode === 'generic'
            ? (page.includedProfiles || []).map(t => ({ profile_id: t.id, model_count: t.models, label: t.name }))
            : page.defenders.map((d, idx) => ({
                unit_id: d.unit_id,
                model_count: d.model_count,
                label: defenderLabel(page.defenders, idx),
                extra_modifiers: d.extra_modifiers || '',
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
            return '<p class="analysis-empty">✓ Your army can wipe every target in one turn.</p>';
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

    return {
        escapeHtml, truncate, formatMetricValue, getHeatColor, keywordHintHtml,
        copyLabel, defenderLabel, weaponVisible, pointsFor, pointsLabel, scaleUnitSize,
        simulatorWeapons, matchupWeaponEntry, matchupWeapons, detachmentOptionsHtml, formatRuleLine, formatRuleText,
        usedWeapons, weaponPayload, rulesForFilter, rulesSelection, buildMatrixRequest,
        statTile, renderAnalysisSummary, renderGaps, renderRoleScores, renderCoverage, renderReverse,
        createPageState,
    };
});
