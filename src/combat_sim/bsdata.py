"""
Importer for BSData catalogues (https://github.com/BSData/wh40k-11e).

BSData publishes BattleScribe data as JSON: one game system file (`Warhammer 40,000.json`)
and one catalogue per faction or library, linked to each other by ID. `import_folder()`
reads a folder of those files and returns the reference sections of the data JSON
(factions, units, ranged and melee weapons, in the format described in data_store.py)
plus an import report. Every record also carries its BSData `bsdata_id`.

How BattleScribe data maps onto the app:

- **Factions** are the non-library catalogues. A faction's units are those it defines, plus
  root entries it links or imports (`importRootEntries`) that carry one of its own
  "Faction: X" categories (see home_categories), so Blood Angels get the Space Marines
  units but not allied Agents of the Imperium or Imperial Knights. A unit it links whose
  "Faction: X" is no catalogue's own is not an ally either: Aeldari and Drukhari both get
  the Harlequins from the Aeldari Library. Library root entries no
  faction takes join the faction that took most of that library, or else form a faction
  named after it (Unaligned Forces). Catalogues left with no units are skipped.
- **Points** are those of a lone unit: the surcharge for repeating a datasheet in an army
  never applies.
- **A unit** is a root entry of type unit or model. The importer auto-selects its minimum
  configuration (every entry's minimum, group defaults filling group minimums), which gives
  the default model count and loadout, then adds models one at a time up to the maximum
  to price every legal unit size.
- **Stats** come from the `Unit` profile of the most numerous model; units whose models
  have different stat lines are reported.
- **Points** are the sum of `pts` costs over the selection, after the cost modifiers whose
  conditions can be evaluated for a lone unit. Modifiers that can't be are skipped and
  reported. The surcharge for later copies of a datasheet is kept apart (`copy_points`, from
  `Selection.earlier_copies`) for armies to add.
- **Weapons** are the `Ranged Weapons` / `Melee Weapons` profiles, one record per faction
  and name/stat line. "➤ Plasma gun - standard" becomes "Plasma gun - standard", the app's
  "Weapon - Profile" variant naming.
- **Defensive abilities** used by the maths: InSv from the profile, Feel No Pain and
  Stealth from core rule links, and a few unconditional ability texts (see DEFENSIVE_TEXT).
  A rule link BSData hides for the unit on its own (only while a bodyguard is attached, or in
  another detachment) is left out (_hidden_alone). The stat line takes the default options'
  stat changes (a Slabshield's +1 W).
- **Rule text** keeps BSData's markup for display (`**bold**`, `*italic*`, `^^keyword^^`, bullets;
  see Core.formatRuleText) but with its whitespace tidied (tidy_text). Everything parsed from
  it (Feel No Pain, Leader lists) reads the plain words (plain_text).
- **Leader buffs**: the "While this model is leading a unit, ..." text of a leader, turned into
  rows like faction rules ({'applies_to', 'phase', 'modifier'}) where it says something the
  maths models (parse_leader_buffs); the rest is reported as not modelled.
- **Army building**: a unit's `max_in_army` is its own "max N in the army" constraint (the rule
  of three, six for Battleline, one for Epic Heroes); `can_support` lists the units its Support
  ability names, as `can_lead` does for Leader. **Enhancements** are the entries costing
  "Enhancements" in the faction's catalogues (see enhancements()).
- **Detachments** are the options of a faction's root "Detachment" entry, each with its rule
  text, taken from the faction's own catalogue and those it takes units from (so not the
  Agents of the Imperium's detachments in every Imperium army). **Army rules** are the rules
  defined in the faction's catalogue. Both are text only: the maths uses the rules and
  stratagems written in the app data.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .rules_text import tidy_text, plain_text, parse_leader_buffs, parse_enhancement  # noqa: F401 (also used by importers of bsdata)
from .simulation import check_keywords

IMPORTER_VERSION = 14  # Bump when the output changes, so cached imports are redone

PTS = 'pts'
SUPPORTED_BATTLESCRIBE_VERSIONS = ('2.',)  # Major version 2 (files are currently 2.03)
WEAPON_TYPES = {'Ranged Weapons': 'ranged', 'Melee Weapons': 'melee'}
CHILD_KEYS = ('selectionEntries', 'selectionEntryGroups', 'entryLinks')

# Unconditional ability texts the maths can use, as (pattern, unit modifier or FNP)
DEFENSIVE_TEXT = [
    (re.compile(r'^(?:this model|models in this unit|this unit) ha(?:s|ve) the feel no pain (\d)\+ ability\.?$', re.I),
     'fnp'),
    (re.compile(r'^each time an attack is allocated to (?:this model|a model in this unit), subtract 1 from the '
                r'damage characteristic of that attack\.?$', re.I),
     'Damage Modifier -1'),
]


class BSDataError(Exception):
    """The folder is missing the game system or holds a file this importer doesn't understand."""


@dataclass
class ImportResult:
    data: dict  # factions, units, ranged_weapons, melee_weapons (data JSON format)
    report: list = field(default_factory=list)  # [{'kind', 'faction', 'unit', 'detail'}]
    source: dict = field(default_factory=dict)  # Game system and catalogue revisions


# ---------------------------------------------------------------------------
# Reading the folder
# ---------------------------------------------------------------------------

class Catalogue:
    """One BSData file, with an index of every element in it that has an ID."""

    def __init__(self, path, root):
        self.path = Path(path)
        self.root = root
        self.id = root['id']
        self.name = root['name']
        self.is_game_system = root.get('type') == 'gameSystem'
        self.is_library = bool(root.get('library'))
        self.index = {}
        self._index(root)
        self.scope = []  # Catalogues searched to resolve links: self, linked catalogues, game system

    def _index(self, node):
        if isinstance(node, dict):
            if 'id' in node and node is not self.root:
                self.index.setdefault(node['id'], node)
            for value in node.values():
                self._index(value)
        elif isinstance(node, list):
            for value in node:
                self._index(value)

    def links(self):
        return self.root.get('catalogueLinks', [])

    def find(self, target_id):
        """(element, catalogue it is defined in) for an ID visible from this catalogue."""
        for cat in self.scope:
            if target_id in cat.index:
                return cat.index[target_id], cat
        return None, None


def read_folder(folder):
    """Read every BSData .json file in `folder`: (game system, {id: catalogue})."""
    folder = Path(folder)
    if not folder.is_dir():
        raise BSDataError(f'{folder} is not a folder')

    game_system, catalogues = None, {}
    for path in sorted(folder.glob('*.json')):
        try:
            with open(path, encoding='utf-8') as f:
                content = json.load(f)
        except (OSError, ValueError) as e:
            raise BSDataError(f'{path.name}: not readable JSON ({e})') from e
        root = (content.get('catalogue') or content.get('gameSystem')) if isinstance(content, dict) else None
        if not isinstance(root, dict) or 'id' not in root or 'name' not in root:
            raise BSDataError(f'{path.name}: not a BattleScribe catalogue or game system')
        version = str(root.get('battleScribeVersion', ''))
        if not version.startswith(SUPPORTED_BATTLESCRIBE_VERSIONS):
            raise BSDataError(f'{path.name}: BattleScribe version {version or "?"} is not supported')
        cat = Catalogue(path, root)
        if cat.is_game_system:
            if game_system is not None:
                raise BSDataError(f'{path.name}: a second game system file')
            game_system = cat
        else:
            catalogues[cat.id] = cat

    if game_system is None:
        raise BSDataError(f'No game system file (e.g. "Warhammer 40,000.json") in {folder}')
    for cat in catalogues.values():
        if cat.root.get('gameSystemId') != game_system.id:
            raise BSDataError(f'{cat.path.name}: belongs to a different game system')
        cat.scope = _scope(cat, catalogues) + [game_system]
    game_system.scope = [game_system]
    return game_system, catalogues


def _scope(cat, catalogues):
    """The catalogue and everything it links to, transitively, in link order."""
    order, stack = [], [cat]
    while stack:
        current = stack.pop(0)
        if current in order:
            continue
        order.append(current)
        stack.extend(catalogues[link['targetId']] for link in current.links() if link['targetId'] in catalogues)
    return order


# ---------------------------------------------------------------------------
# Entries, links and constraints
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """A selection entry or group as seen from where it is used (an entry link merged with its target)."""
    node: dict  # The target element
    cat: Catalogue  # Catalogue the target is defined in (resolves its own links)
    link: dict = None  # The entry link, if reached through one

    @property
    def id(self):
        return self.node['id']

    @property
    def ids(self):
        return {self.node['id']} | ({self.link['id']} if self.link and 'id' in self.link else set())

    @property
    def name(self):
        return (self.link or {}).get('name') or self.node.get('name', '')

    @property
    def kind(self):
        """'unit', 'model', 'upgrade' or 'group'."""
        return self.node.get('type', 'group')

    @property
    def hidden(self):
        return bool(self.node.get('hidden') or (self.link or {}).get('hidden'))

    def merged(self, key):
        return list((self.link or {}).get(key, [])) + list(self.node.get(key, []))

    def bound(self, kind):
        """Effective min/max selections under the parent (largest min, smallest max), or None."""
        values = [c['value'] for c in self.merged('constraints')
                  if c.get('type') == kind and c.get('field') == 'selections' and c.get('scope') == 'parent']
        if not values:
            return None
        return max(values) if kind == 'min' else min(values)

    def categories(self):
        return {c.get('targetId') for c in self.merged('categoryLinks')}

    def category_names(self):
        seen = []
        for c in self.merged('categoryLinks'):
            if c.get('name') and c['name'] not in seen:
                seen.append(c['name'])
        return seen

    def pts(self):
        return sum(c.get('value') or 0 for c in self.node.get('costs', []) if c.get('name') == PTS)

    def children(self, report):
        """Child entries and groups, links resolved; hidden ones left out."""
        out = []
        for key in CHILD_KEYS:
            for child in self.node.get(key, []):
                entry = resolve_link(child, self.cat, report) if key == 'entryLinks' else Entry(child, self.cat)
                if entry is not None and not entry.hidden:
                    out.append(entry)
        return out


def resolve_link(link, cat, report):
    target, target_cat = cat.find(link.get('targetId'))
    if target is None:
        report.add('missing_link', None, None, f'{cat.name}: link "{link.get("name")}" points at nothing')
        return None
    return Entry(target, target_cat, link)


# ---------------------------------------------------------------------------
# The selection: a unit's chosen entries, used for model counts, loadouts and points
# ---------------------------------------------------------------------------

@dataclass
class Selection:
    entry: Entry
    count: int = 1
    parent: 'Selection' = None
    children: list = field(default_factory=list)
    earlier_copies: int = 0  # On the unit: copies of its datasheet before it in the army (copy surcharges)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def total(self, predicate, direct_only=False):
        """
        Selections matching `predicate` below this one, multiplied by the counts above them.
        Groups aren't selections: what is inside one counts as a direct child.
        """
        total = 0
        for child in self.children:
            if predicate(child):
                total += child.count
            if child.children and (not direct_only or child.entry.kind == 'group'):
                total += child.count * child.total(predicate, direct_only)
        return total

    def models(self):
        """Models in the unit (the unit itself when it is a single model)."""
        count = self.total(lambda s: s.entry.kind == 'model')
        return count or (1 if self.entry.kind in ('model', 'unit') and self.parent is None else 0)

    def multiplier(self):
        """Copies of this selection's parent in the whole unit (its own count not included)."""
        m, node = 1, self.parent
        while node is not None:
            m *= node.count
            node = node.parent
        return m


def auto_select(entry, report, parent=None, count=1):
    """The minimum configuration below `entry`: entry minimums, then group defaults up to group minimums."""
    sel = Selection(entry, count, parent)
    for child in entry.children(report):
        if child.kind == 'group':
            _select_group(sel, child, report)
        else:
            low = child.bound('min') or 0
            if low > 0:
                sel.children.append(auto_select(child, report, sel, low))
    return sel


def _select_group(sel, group, report):
    """Select a group's members with minimums, then fill the group minimum with its default."""
    group_sel = Selection(group, 1, sel)
    members = group.children(report)
    room = group.bound('max')
    for member in _default_first(group, members):
        if member.kind == 'group':
            _select_group(group_sel, member, report)
            continue
        low = member.bound('min') or 0
        if room is not None:  # Required members never overfill the group (the default goes first)
            low = min(low, room)
            room -= low
        if low > 0:
            group_sel.children.append(auto_select(member, report, group_sel, low))
    need = (group.bound('min') or 0) - sum(s.count for s in group_sel.children if s.entry.kind != 'group')
    for member in _default_first(group, members):
        if need <= 0:
            break
        if member.kind == 'group':
            continue
        existing = next((s for s in group_sel.children if s.entry is member), None)
        have = existing.count if existing else 0
        room = need if member.bound('max') is None else max(0, min(need, member.bound('max') - have))
        if room:
            if existing:
                existing.count += room
            else:
                group_sel.children.append(auto_select(member, report, group_sel, room))
            need -= room
    if group_sel.children:
        sel.children.append(group_sel)


def _default_first(group, members):
    default = group.node.get('defaultSelectionEntryId')
    return sorted(members, key=lambda m: 0 if default and default in m.ids else 1)


def grow(sel, report):
    """Add one model to the unit, preferring group defaults. False when the unit is at its maximum."""
    for node in sel.walk():
        if node.entry.kind != 'group':
            continue
        group_max = node.entry.bound('max')
        in_group = sum(s.count for s in node.children if s.entry.kind == 'model')
        if group_max is not None and in_group >= group_max:
            continue
        members = [m for m in node.entry.children(report) if m.kind == 'model']
        for member in _default_first(node.entry, members):
            existing = next((s for s in node.children if s.entry.node is member.node), None)
            have = existing.count if existing else 0
            limit = member.bound('max')
            if limit is None and group_max is None:
                continue  # Unbounded: never grow
            if limit is not None and have >= limit:
                continue
            if existing:
                existing.count += 1
            else:
                node.children.append(auto_select(member, report, node, 1))
            return True
    for node in list(sel.walk()):
        if node.entry.kind == 'model' and node is not sel and node.parent.entry.kind != 'group':
            limit = node.entry.bound('max')
            if limit is not None and node.count < limit:
                node.count += 1
                return True
    return False


# ---------------------------------------------------------------------------
# Wargear: a unit's option tree, kept for wargear.py to rebuild selections from
# ---------------------------------------------------------------------------

# Groups left out of the option tree: Crusade- and Boarding Actions-only, and enhancements (armies.py handles those)
SKIPPED_GROUPS = re.compile(r'^(crusade( relic upgrades)?|weapon modifications|weapon upgrades|boarding act\w* upgrades'
                            r'|battle scars|battle honours|enhancements?)$', re.I)
MAX_TREE_DEPTH = 8


# Keys of constraints, modifiers and conditions the selection code reads (the rest is left out)
KEPT_KEYS = {'id', 'field', 'scope', 'type', 'value', 'childId', 'includeChildSelections', 'percentValue', 'roundUp',
             'repeats', 'conditions', 'conditionGroups', 'localConditionGroups'}


def _slim(value):
    """BSData constraints/modifiers/conditions with only the keys the selection code reads."""
    if isinstance(value, list):
        return [_slim(v) for v in value]
    if isinstance(value, dict):
        return {k: _slim(v) for k, v in value.items() if k in KEPT_KEYS and v is not None and v is not False and v != []}
    return value


def freeze(entry, report, depth=0, counter=None, keep=False):
    """
    A unit's option tree as plain data (wargear.py rebuilds selections from it): each node keeps
    what the selection, constraint and points code reads (its IDs, type, constraints, modifiers,
    pts cost, categories, default), its weapon profiles' keys (`weapons`, (kind, stat key), turned
    into weapon IDs by import_folder) and its children, links resolved. `k` numbers the nodes.
    """
    counter = counter if counter is not None else [0]
    counter[0] += 1
    constraint_ids = {c.get('id') for c in entry.merged('constraints')}
    pts_types = {c.get('typeId') for c in entry.node.get('costs', []) if c.get('name') == PTS} | {PTS_TYPE_ID}
    kept = [m for m in entry.merged('modifiers') + _group_modifiers(entry)
            if m.get('field') in pts_types or m.get('field') == 'error' or m.get('field') in constraint_ids]
    node = {
        'k': counter[0],
        'id': entry.id,
        'ids': sorted(entry.ids - {entry.id}),
        'name': entry.name,
        'type': entry.kind,
        'constraints': _slim([c for c in entry.merged('constraints') if c.get('field') == 'selections']),
        'modifiers': _slim(kept),
        'costs': [{'name': PTS, 'typeId': c.get('typeId'), 'value': c.get('value') or 0}
                  for c in entry.node.get('costs', []) if c.get('name') == PTS],
        'categoryLinks': [{'targetId': c.get('targetId')} for c in entry.merged('categoryLinks')],
        'defaultSelectionEntryId': entry.node.get('defaultSelectionEntryId'),
        'weapons': [[WEAPON_TYPES[p['typeName']], list(_stat_key(weapon_record(p, WEAPON_TYPES[p['typeName']])))]
                    for p in profiles(entry, report) if p.get('typeName') in WEAPON_TYPES],
        # An option's own abilities, the keywords it adds and its changes to the bearer's characteristics
        'abilities': [[p['name'], ability_text(p)] for p in profiles(entry, report) if p.get('typeName') == 'Abilities']
        if entry.kind == 'upgrade' else [],
        'keywords': added_keywords(entry),
        'stats': stat_changes(entry),
        'children': [],
    }
    if depth < MAX_TREE_DEPTH:
        for child in entry.children(report):
            if child.kind == 'group' and SKIPPED_GROUPS.match(child.name.strip()):
                continue
            if any(c.get('typeId') == ENHANCEMENTS_TYPE_ID and c.get('value') for c in child.node.get('costs', [])):
                continue
            frozen = freeze(child, report, depth + 1, counter)
            if frozen is not None:
                node['children'].append(frozen)
        if entry.kind == 'group' and node['children']:
            # A kept group keeps all its options: one that doesn't matter alone is still a choice
            kept_ids = {c['id'] for c in node['children']}
            node['children'] = [next((c for c in node['children'] if c['id'] == child.id), None)
                                or freeze(child, report, depth + 1, counter, keep=True)
                                for child in entry.children(report)
                                if not (child.kind == 'group' and SKIPPED_GROUPS.match(child.name.strip()))
                                and not any(c.get('typeId') == ENHANCEMENTS_TYPE_ID and c.get('value')
                                            for c in child.node.get('costs', []))
                                and (child.id in kept_ids or child.kind != 'group')]
    # Only what matters to the maths or the points: models, weapons, pts costs (Crusade upgrades and the like go)
    matters = (node['type'] in ('unit', 'model') or node['weapons'] or node['children']
               or node['abilities'] or node['keywords'] or node['stats']
               or any(c['value'] for c in node['costs']) or any(m.get('field') in pts_types for m in node['modifiers']))
    if depth and not matters and not keep:
        return None
    return {key: value for key, value in node.items() if value not in (None, []) or key in ('children', 'k')}


def _unconditional(modifier):
    return not (modifier.get('conditions') or modifier.get('conditionGroups') or modifier.get('localConditionGroups')
                or modifier.get('repeats'))


def added_keywords(entry):
    """Keywords an option adds to its unit (BSData's unconditional "add category"), e.g. a Daemon Prince's KHORNE."""
    names = []
    for modifier in entry.merged('modifiers'):
        if modifier.get('type') == 'add' and modifier.get('field') == 'category' and _unconditional(modifier):
            category, _ = entry.cat.find(modifier.get('value')) if entry.cat else (None, None)
            name = (category or {}).get('name')
            if name and not name.startswith('Faction:') and name not in names:
                names.append(name)
    return names


UNIT_STATS = ('M', 'T', 'Sv', 'W', 'LD', 'OC')


def stat_changes(entry):
    """
    An option's unconditional changes to its bearer's characteristics: [['W', 'increment', 1]] (a
    Slabshield). BSData names the characteristic by its type's ID.
    """
    out = []
    for modifier in entry.merged('modifiers'):
        if modifier.get('type') not in ('increment', 'decrement', 'set') or not _unconditional(modifier):
            continue
        stat, _ = entry.cat.find(modifier.get('field')) if entry.cat else (None, None)
        if stat and stat.get('name') in UNIT_STATS and 'typeId' not in stat and 'characteristics' not in stat:
            out.append([stat['name'], modifier['type'], modifier.get('value')])
    return out


def model_stat_changes(model_sel):
    """The stat changes of the options a model carries (not those of other models below it)."""
    changes = []

    def visit(node):
        for child in node.children:
            if child.entry.kind == 'model':
                continue
            if child.entry.kind == 'upgrade':
                changes.extend(stat_changes(child.entry))
            visit(child)
    visit(model_sel)
    return changes


def apply_stats(characteristics_, changes):
    """A Unit profile's characteristics ({'W': '3', ...}) with stat changes applied ('3' + 1 -> '4')."""
    out = dict(characteristics_)
    for name, kind, value in changes:
        current = out.get(name)
        if kind == 'set':
            out[name] = str(value)
            continue
        m = re.match(r'^(\d+)(.*)$', current or '')
        if m:
            number = int(m.group(1)) + (1 if kind == 'increment' else -1) * int(value or 0)
            out[name] = f'{number}{m.group(2)}'
    return out


def _key_weapons(tree, weapon_ids):
    """Replace a frozen tree's weapon keys by weapon IDs ([kind, id]); keys without a weapon are dropped."""
    tree['weapons'] = [[kind, weapon_ids[(kind, tuple(key))]['id']] for kind, key in tree.get('weapons', [])
                       if (kind, tuple(key)) in weapon_ids]
    if not tree['weapons']:
        del tree['weapons']
    for child in tree.get('children', []):
        _key_weapons(child, weapon_ids)


def has_choices(tree):
    """True if a frozen tree has anything to choose: a group with more than one member, or an optional entry."""
    for child in tree.get('children', []):
        if child.get('type', 'group') == 'group' and len(child.get('children', [])) > 1:
            return True
        lows = [c['value'] for c in child.get('constraints', []) if c.get('type') == 'min' and c.get('scope') == 'parent']
        if child.get('type', 'group') != 'group' and not any(lows) and child.get('weapons'):
            return True
        if has_choices(child):
            return True
    return False


# ---------------------------------------------------------------------------
# Points: costs and the cost modifiers that can be evaluated for one unit on its own
# ---------------------------------------------------------------------------

class Unevaluable(Exception):
    pass


def points(sel, faction_cat):
    """Total points of the selection, and whether any cost modifier had to be skipped."""
    skipped = False
    total = 0
    for node in sel.walk():
        if node.entry.kind == 'group':
            continue
        cost = node.entry.pts()
        for modifier in node.entry.merged('modifiers') + _group_modifiers(node.entry):
            if not _is_pts_modifier(modifier, node.entry):
                continue
            try:
                if not _conditions_hold(modifier, node, sel, faction_cat):
                    continue
                times = _repeats(modifier, node, sel)
            except Unevaluable:
                skipped = True
                continue
            cost = _apply(cost, modifier, times)
        total += cost * node.count * node.multiplier()
    return total, skipped


def _group_modifiers(entry):
    """Modifiers inside modifierGroups (their own conditions are folded into each modifier)."""
    out = []
    for group in entry.merged('modifierGroups'):
        for modifier in group.get('modifiers', []):
            combined = dict(modifier)
            combined['conditions'] = group.get('conditions', []) + modifier.get('conditions', [])
            combined['conditionGroups'] = group.get('conditionGroups', []) + modifier.get('conditionGroups', [])
            combined['localConditionGroups'] = (group.get('localConditionGroups', [])
                                                + modifier.get('localConditionGroups', []))
            out.append(combined)
    return out


def _is_pts_modifier(modifier, entry):
    type_ids = {c.get('typeId') for c in entry.node.get('costs', []) if c.get('name') == PTS}
    return modifier.get('field') in type_ids or modifier.get('field') == PTS_TYPE_ID


PTS_TYPE_ID = '51b2-306e-1021-d207'  # The game system's "pts" cost type


def _apply(cost, modifier, times=1):
    value = modifier.get('value') or 0
    kind = modifier.get('type')
    if kind == 'set':
        return value
    if kind == 'increment':
        return cost + value * times
    if kind == 'decrement':
        return cost - value * times
    if kind == 'multiply':
        return cost * value
    raise Unevaluable(kind)


def _repeats(modifier, node, root):
    repeats = modifier.get('repeats') or []
    if not repeats:
        return 1
    times = 0
    for r in repeats:
        count = _count(r, node, root)
        per = r.get('value') or 1
        steps = math.ceil(count / per) if r.get('roundUp') else count // per
        times += steps * (r.get('repeats') or 1)
    return times


def _conditions_hold(modifier, node, root, faction_cat):
    checks = [_condition(c, node, root, faction_cat) for c in modifier.get('conditions', [])]
    checks += [_group_holds(g, node, root, faction_cat) for g in modifier.get('conditionGroups', [])]
    checks += [_local_group(g, node, root) for g in modifier.get('localConditionGroups', [])]
    return all(checks)


def _group_holds(group, node, root, faction_cat):
    checks = [_condition(c, node, root, faction_cat) for c in group.get('conditions', [])]
    checks += [_group_holds(g, node, root, faction_cat) for g in group.get('conditionGroups', [])]
    checks += [_local_group(g, node, root) for g in group.get('localConditionGroups', [])]
    if not checks:
        return True
    return any(checks) if group.get('type') == 'or' else all(checks)


def _local_group(group, node, root):
    """
    "At least N matching selections in scope", e.g. the surcharge for a third copy of a datasheet:
    in the unit's force (scope parent), selections before it that are that datasheet. Those count
    the unit's `earlier_copies` (set when pricing it in an army; 0 for a lone unit); other
    selections outside the unit count 0; counts inside the unit aren't modelled.
    """
    outside = group.get('scope') in ('force', 'roster') or (group.get('scope') == 'parent' and node is root)
    if not outside or group.get('field') != 'selections' or group.get('type') not in COMPARE:
        raise Unevaluable('localConditionGroups')
    tests = {c.get('type'): c for c in group.get('conditions', [])}
    count = 0
    if (group.get('scope') == 'parent' and set(tests) == {'before', 'instanceOf'}
            and tests['instanceOf'].get('scope') == 'self' and tests['instanceOf'].get('childId') in root.entry.ids):
        count = root.earlier_copies
    return COMPARE[group['type']](count, group.get('value') or 0)


MAX_COPIES = 6  # The most copies of a datasheet an army may take (Battleline)


def copy_surcharges(sel, faction_cat):
    """
    Extra points for later copies of a datasheet: [0, 0, 15] means the third and later copies cost
    15 more (index: copies before it in the army; past the end, the last value). [] when none.
    """
    base, _ = points(sel, faction_cat)
    extra = []
    for earlier in range(MAX_COPIES):
        sel.earlier_copies = earlier
        extra.append(points(sel, faction_cat)[0] - base)
    sel.earlier_copies = 0
    while len(extra) > 1 and extra[-1] == extra[-2]:
        extra.pop()
    return extra if any(extra) else []


COMPARE = {
    'atLeast': lambda a, b: a >= b,
    'greaterThan': lambda a, b: a > b,
    'atMost': lambda a, b: a <= b,
    'lessThan': lambda a, b: a < b,
    'equalTo': lambda a, b: a == b,
    'notEqualTo': lambda a, b: a != b,
}


def _condition(c, node, root, faction_cat):
    kind = c.get('type')
    if kind in ('instanceOf', 'notInstanceOf'):
        result = _instance_of(c, node, root, faction_cat)
        return result if kind == 'instanceOf' else not result
    if kind not in COMPARE:
        raise Unevaluable(kind)
    if c.get('percentValue'):
        raise Unevaluable('percentValue')
    return COMPARE[kind](_count(c, node, root), c.get('value') or 0)


def _instance_of(c, node, root, faction_cat):
    scope, child = c.get('scope'), c.get('childId')
    if scope in ('force', 'roster'):
        return False  # A lone unit has no force or roster entries (e.g. "Crusade Force")
    if scope == 'primary-catalogue':
        return child == faction_cat.id
    target = _scope_node(scope, node, root)
    return child in target.entry.ids or child in target.entry.categories()


def _count(c, node, root):
    """Value of a condition's field over its scope: selection counts, or a cost such as pts."""
    if c.get('field') == 'associations':
        return 0  # A unit on its own: nothing attached to it
    if c.get('field') != 'selections':
        raise Unevaluable(c.get('field'))
    scope = c.get('scope')
    if scope in ('force', 'roster', 'primary-catalogue', 'root-entry', 'unit'):
        target = root  # A roster holding just this unit
    else:
        target = _scope_node(scope, node, root)
    child = c.get('childId')
    groups = [n for n in target.walk() if n.entry.kind == 'group' and child in n.entry.ids]
    if groups:  # Selections in a group, e.g. the models in "Intercessors"
        return sum(g.total(lambda s: s.entry.kind != 'group', direct_only=True) * g.multiplier() for g in groups)
    return target.total(_matcher(child), direct_only=not c.get('includeChildSelections'))


def _scope_node(scope, node, root):
    if scope == 'self':
        return node
    if scope == 'parent':
        parent = node.parent or root
        while parent.entry.kind == 'group' and parent.parent is not None:
            parent = parent.parent
        return parent
    current = node
    while current is not None:
        if scope in current.entry.ids:
            return current
        current = current.parent
    if scope in ('unit', 'root-entry', 'model-or-unit'):
        return root
    raise Unevaluable(f'scope {scope}')


def _matcher(child):
    if child in (None, 'any'):
        return lambda s: s.entry.kind != 'group'
    if child in ('model', 'unit', 'upgrade'):
        return lambda s: s.entry.kind == child
    return lambda s: child in s.entry.ids or child in s.entry.categories()


# ---------------------------------------------------------------------------
# Profiles, rules and abilities
# ---------------------------------------------------------------------------

def profiles(entry, report):
    """Profiles on an entry: its own, linked ones and those in info groups."""
    out = list(entry.merged('profiles'))
    for group in entry.merged('infoGroups'):
        out.extend(group.get('profiles', []))
    for link in entry.merged('infoLinks'):
        if link.get('type') in ('profile', 'infoGroup'):
            target, _ = entry.cat.find(link.get('targetId'))
            if target is None:
                report.add('missing_link', None, None, f'{entry.cat.name}: profile link "{link.get("name")}" points at nothing')
            elif link['type'] == 'profile':
                out.append(target)
            else:
                out.extend(target.get('profiles', []))
    return [p for p in out if not p.get('hidden')]


def rule_links(entry, node=None, root=None, faction_cat=None):
    """
    Core rules linked to an entry, with unconditional name appends: ['Feel No Pain 5+', 'Deep Strike'].
    With the entry's selection (`node` in the unit `root`), a link BSData hides for the unit on its
    own is left out, e.g. a Feel No Pain only while an Ogryn Bodyguard is attached (_hidden_alone).
    """
    names = []
    for link in entry.merged('infoLinks'):
        if link.get('type') != 'rule' or link.get('hidden'):
            continue
        if node is not None and _hidden_alone(link, node, root, faction_cat):
            continue
        name = link.get('name', '')
        for modifier in link.get('modifiers', []):
            if (modifier.get('field') == 'name' and modifier.get('type') == 'append'
                    and not modifier.get('conditions') and not modifier.get('conditionGroups')):
                name = f'{name} {modifier.get("value")}'.strip()
        names.append(name)
    for rule in entry.merged('rules'):
        if not rule.get('hidden'):
            names.append(rule.get('name', ''))
    return names


def _hidden_alone(element, node, root, faction_cat):
    """
    True if one of the element's conditional "hidden" modifiers holds for the unit on its own: no
    unit attached to it (associations count 0) and nothing else in the army. A condition that can't
    be evaluated never hides it.
    """
    for modifier in element.get('modifiers', []):
        if modifier.get('field') != 'hidden' or str(modifier.get('value')).lower() != 'true':
            continue
        if not (modifier.get('conditions') or modifier.get('conditionGroups')):
            continue
        try:
            if _conditions_hold(modifier, node, root, faction_cat):
                return True
        except Unevaluable:
            continue
    return False


def characteristics(profile):
    return {c.get('name'): (c.get('$text') or '').strip() for c in profile.get('characteristics', [])}


def ability_text(profile):
    chars = characteristics(profile)
    return tidy_text(chars.get('Description') or next(iter(chars.values()), ''))


# Characters BSData's text uses for layout that read as ordinary ones
def _int(text):
    """'5"' -> 5, '3+' -> 3, '-2' -> -2; None when not a number ('N/A', '-', '*')."""
    m = re.match(r'^\s*(-?\d+)', text or '')
    return int(m.group(1)) if m else None


def _plus(text):
    """'4+' -> 4; '' or '-' -> None."""
    return _int(text) if text and '+' in text else None


def parse_base(text):
    """
    A model's Base characteristic ('32mm', '120x92mm', '60mm Flying', '~40mm') as
    {'shape', 'length_mm', 'width_mm', 'text'}, plus 'flying' and 'approx' when they apply.
    'Hull' (a model with no base) has no size; anything else unreadable ('-', 'Unique') is None.
    """
    text = (text or '').strip()
    if text == 'Hull':
        return {'shape': 'hull', 'text': text}
    m = re.match(r'^(~?)(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?mm( Oval)?( Flying)?$', text)
    if not m:
        return None
    approx, length, width, _, flying = m.groups()
    length, width = _mm(length), _mm(width or length)
    base = {'shape': 'round' if length == width else 'oval', 'length_mm': length, 'width_mm': width}
    if flying:
        base['flying'] = True
    if approx:
        base['approx'] = True
    return {**base, 'text': text}


def _mm(value):
    """'32' -> 32, '28.5' -> 28.5."""
    size = float(value)
    return int(size) if size.is_integer() else size


def weapon_record(profile, kind):
    chars = characteristics(profile)
    name = re.sub(r'^[➤>\s]+', '', profile.get('name', '')).strip()
    keywords = chars.get('Keywords') or ''
    abilities = [k.strip() for k in keywords.split(',') if k.strip() and k.strip() not in ('-', '–')]
    skill = _plus(chars.get('BS') if kind == 'ranged' else chars.get('WS'))
    record = {
        'bsdata_id': profile['id'],
        'name': name,
        'attacks': chars.get('A') or '0',
        'strength': _int(chars.get('S')),
        'armor_penetration': abs(_int(chars.get('AP')) or 0),
        'damage': chars.get('D') or '0',
        'abilities': abilities,
    }
    if kind == 'ranged':
        record['ballistic_skill'] = skill
        record['range_inches'] = _int(chars.get('Range'))
    else:
        record['weapon_skill'] = skill
    return record


def _stat_key(record):
    return tuple((k, json.dumps(v)) for k, v in sorted(record.items()) if k != 'bsdata_id')


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.items = []
        self._seen = set()

    def add(self, kind, faction, unit, detail):
        key = (kind, faction, unit, detail)
        if key not in self._seen:
            self._seen.add(key)
            self.items.append({'kind': kind, 'faction': faction, 'unit': unit, 'detail': detail})


def build_unit(entry, faction_cat, report, known_keywords):
    """A unit record (without IDs) and its weapon profiles, or None if it has no usable stat line."""
    faction = faction_name(faction_cat)
    sel = auto_select(entry, report)
    default_models = sel.models()

    # Stat line: the Unit profile of the most numerous model
    stat_lines = Counter()
    unit_profile = next((p for p in profiles(entry, report) if p.get('typeName') == 'Unit'), None)
    for node in sel.walk():
        if node.entry.kind != 'model' and node is not sel:
            continue
        own = next((p for p in profiles(node.entry, report) if p.get('typeName') == 'Unit'), None)
        profile = own or unit_profile
        if profile is not None:
            weight = (node.count * node.multiplier()) if node is not sel else (1 if node.entry.kind == 'model' else 0)
            stats_ = apply_stats(characteristics(profile), model_stat_changes(node))  # E.g. a Slabshield's +1 W
            stat_lines[json.dumps(stats_, sort_keys=True)] += weight
    if not stat_lines and unit_profile is not None:
        stat_lines[json.dumps(characteristics(unit_profile), sort_keys=True)] += 1
    if not stat_lines:
        report.add('skipped_unit', faction, entry.name, 'no Unit profile')
        return None
    if len(stat_lines) > 1:
        report.add('mixed_profile', faction, entry.name,
                   f'{len(stat_lines)} different stat lines; using the most numerous model\'s')
    stats = json.loads(stat_lines.most_common(1)[0][0])
    toughness, save, wounds = _int(stats.get('T')), _plus(stats.get('Sv')), _int(stats.get('W'))
    if None in (toughness, save, wounds):
        report.add('skipped_unit', faction, entry.name, f'unreadable T/Sv/W: {stats}')
        return None
    base = parse_base(stats.get('Base'))
    if base is None:
        text = stats.get('Base')
        report.add('base_size', faction, entry.name, f'unreadable base size "{text}"' if text else 'no base size')

    # Abilities, rules and the defensive values the maths uses
    ability_lines, rules, modifiers, fnp = [], [], [], None
    sources = [(entry, sel)] + [(n.entry, n) for n in sel.walk() if n.entry.kind in ('model', 'upgrade') and n.entry is not entry]
    seen_abilities = set()
    for source, source_sel in sources:
        for profile in profiles(source, report):
            if profile.get('typeName') in ('Abilities',) and profile['name'] not in seen_abilities:
                seen_abilities.add(profile['name'])
                text = ability_text(profile)
                ability_lines.append(f'{profile["name"]}: {text}')
                for pattern, effect in DEFENSIVE_TEXT:
                    m = pattern.match(plain_text(text))
                    if m and effect == 'fnp':
                        fnp = min(fnp or 7, int(m.group(1)))
                    elif m and effect not in modifiers:
                        modifiers.append(effect)
            elif profile.get('typeName') == 'Transport' and 'Transport' not in seen_abilities:
                # A transport's capacity in its own profile (most catalogues): kept as an ability line, as others write it
                capacity = (characteristics(profile).get('Capacity') or '').strip()
                if capacity:
                    seen_abilities.add('Transport')
                    ability_lines.append(f'Transport: {capacity}')
        for rule in rule_links(source, source_sel, sel, faction_cat):
            if rule not in rules:
                rules.append(rule)
    for rule in rules:
        m = re.match(r'^Feel No Pain (\d)\+', rule)
        if m:
            fnp = min(fnp or 7, int(m.group(1)))
        if rule == 'Stealth' and 'Stealth' not in modifiers:
            modifiers.append('Stealth')

    can_lead, can_support = [], []
    for line in ability_lines:
        if line.startswith('Leader:'):
            can_lead.extend(_leader_targets(line))
        elif line.startswith('Support:'):
            can_support.extend(_leader_targets(line))
    limits = [c['value'] for c in entry.merged('constraints')
              if c.get('type') == 'max' and c.get('field') == 'selections' and c.get('scope') in ('force', 'roster')]

    leader_buffs = []
    if can_lead:
        for line in ability_lines:
            buffs, skipped = parse_leader_buffs(line)
            leader_buffs.extend(b for b in buffs if b not in leader_buffs)
            for sentence in skipped:
                report.add('leader_buff', faction, entry.name, f'not modelled: "{sentence[:120]}"')

    # Loadout at the default size: weapon profiles carried by each selected entry
    loadout = {'ranged': Counter(), 'melee': Counter()}
    weapons = {}
    for node in sel.walk():
        if node.entry.kind == 'group':
            continue
        carriers = node.count * node.multiplier() if node is not sel else 1
        for profile in profiles(node.entry, report):
            kind = WEAPON_TYPES.get(profile.get('typeName'))
            if kind:
                record = weapon_record(profile, kind)
                key = (kind, _stat_key(record))
                weapons.setdefault(key, record)
                loadout[kind][key] += carriers

    options = {'ranged': [], 'melee': []}
    for profile in _all_weapon_profiles(entry, report):
        kind = WEAPON_TYPES[profile['typeName']]
        record = weapon_record(profile, kind)
        key = (kind, _stat_key(record))
        weapons.setdefault(key, record)
        if key not in loadout[kind] and key not in options[kind]:
            options[kind].append(key)

    for key, record in weapons.items():  # Reported once per faction and weapon, not per unit
        if record['strength'] is None:
            report.add('weapon_stats', faction, None, f'{record["name"]}: unreadable strength, skipped')
        if known_keywords is not None and record['abilities']:
            unknown = check_keywords(', '.join(record['abilities']), known_keywords)['unknown']
            for keyword in unknown:
                report.add('unknown_keyword', faction, None, f'{record["name"]}: "{keyword}"')
    weapons = {k: r for k, r in weapons.items() if r['strength'] is not None}

    copy_points = copy_surcharges(sel, faction_cat)

    # Points for every legal size, from the minimum configuration up
    sizes, skipped = [], False
    current = sel
    while True:
        cost, was_skipped = points(current, faction_cat)
        skipped = skipped or was_skipped
        models = current.models()
        if not sizes or models > sizes[-1]['models']:
            sizes.append({'models': models, 'points': cost})
        if not grow(current, report) or current.models() > 100:
            break
    if skipped:
        report.add('points', faction, entry.name, 'some cost modifiers could not be evaluated; base costs used')

    name = entry.name
    unit = {
        'bsdata_id': entry.id,
        'name': name,
        'legends': '[Legends]' in name,
        'toughness': toughness,
        'save': save,
        'wounds': wounds,
        'invulnerable_save': _plus(stats.get('InSv')),
        'feel_no_pain': fnp,
        'move': (stats.get('M') or '').replace('"', '') if re.search(r'\d', stats.get('M') or '') else None,
        'oc': _int(stats.get('OC')),
        'leadership': _plus(stats.get('LD')),
        'base': base,
        'points_cost': sizes[0]['points'],
        'default_models': default_models,
        'points_by_size': sizes,
        'keywords': [n for n in entry.category_names() if not n.startswith('Faction:')],
        'modifiers': modifiers,
        'can_lead': can_lead,
        'can_support': can_support,
        'max_in_army': int(min(limits)) if limits else None,
        'copy_points': copy_points,
        'leader_buffs': leader_buffs,
        'abilities': '\n\n'.join(ability_lines + [r for r in rules if r != 'Leader']),
        'wargear': freeze(entry, report),
        '_loadout': {kind: [(key, qty) for key, qty in loadout[kind].items() if key in weapons] for kind in loadout},
        '_options': {kind: [key for key in options[kind] if key in weapons] for kind in options},
    }
    return unit, weapons


def _all_weapon_profiles(entry, report, seen=None):
    """Every weapon profile reachable below an entry (the unit's wargear options)."""
    seen = seen if seen is not None else set()
    out = []
    if entry.id in seen:
        return out
    seen.add(entry.id)
    for profile in profiles(entry, report):
        if profile.get('typeName') in WEAPON_TYPES:
            out.append(profile)
    for child in entry.children(report):
        out.extend(_all_weapon_profiles(child, report, seen))
    return out


def _leader_targets(line):
    """Unit names from 'Leader: This model can be attached to the following unit(s): X, Y'."""
    m = re.search(r'attached to the following units?:\s*(.+)', plain_text(line), re.I | re.S)
    if not m:
        return []
    text = m.group(1).split('\n\n')[0]
    names = []
    for part in re.split(r'[\n,■•▪]+', text):
        part = re.sub(r'^\s*-\s+', '', part).strip(' .')  # '- NAME' bullets (Yvraine)
        if part and not part.lower().startswith(('can ', 'this ', 'if ', 'note')):
            names.append(part)
    return names


# ---------------------------------------------------------------------------
# Factions
# ---------------------------------------------------------------------------

def root_entries(cat, report):
    """Unit and model root entries of a catalogue (not those it imports)."""
    out = []
    for link in cat.root.get('entryLinks', []):
        entry = resolve_link(link, cat, report)
        if entry is not None and not entry.hidden and entry.kind in ('unit', 'model'):
            out.append(entry)
    for node in cat.root.get('selectionEntries', []):
        entry = Entry(node, cat)
        if not entry.hidden and entry.kind in ('unit', 'model'):
            out.append(entry)
    return out


def faction_categories(entry):
    return {n for n in entry.category_names() if n.startswith('Faction:')}


def home_categories(cat, own, imported, catalogues):
    """
    The catalogue's own 'Faction: X' categories: those on at least half of the units it
    defines itself; when it defines none, of its root entries from libraries (Aeldari,
    Astra Militarum); when those are all allies too (Chaos Knights links Chaos Space Marines
    units), of the imported library whose name best matches its own. Units from other
    armies' catalogues are allies.
    """
    basis = [e for e in own if e.cat is cat] or [e for e in own if e.cat.is_library]
    if not basis:
        libraries = {e.cat for e in imported if e.cat.is_library}
        best = max(libraries, key=lambda lib: _name_match(cat, lib, catalogues), default=None)
        basis = [e for e in imported if e.cat is best]
    counts = Counter(c for e in basis for c in faction_categories(e))
    return {c for c, n in counts.items() if n * 2 >= len(basis)}


def _name_match(cat, library, catalogues):
    """Words shared by two catalogue names, rarer words counting more ('Daemons' over 'Chaos')."""
    def words(c):
        return set(re.findall(r"[a-z']+", faction_name(c).lower())) - {'library'}
    frequency = Counter(w for c in catalogues.values() for w in words(c))
    return sum(1 / frequency[w] for w in words(cat) & words(library))


def faction_members(catalogues, report):
    """[(faction catalogue, [unit entries])] following the rules in the module docstring."""
    own = {cid: root_entries(cat, report) for cid, cat in catalogues.items()}
    imported, homes = {}, {}
    for cid, cat in catalogues.items():
        if not cat.is_library:
            imported[cid] = [e for link in cat.links() if link.get('importRootEntries') and link['targetId'] in catalogues
                             for e in own[link['targetId']]]
            homes[cid] = home_categories(cat, own[cid], imported[cid], catalogues)
    any_home = set().union(*homes.values())
    claimed = set()
    factions = []
    for cid, cat in catalogues.items():
        if cat.is_library:
            continue
        home = homes[cid]
        # Units it defines itself, plus linked and imported ones that belong to its army, plus
        # those it links whose faction is no catalogue's own (Aeldari Harlequins and Ynnari)
        members = _unique([e for e in own[cid] + imported[cid] if e.cat is cat or faction_categories(e) & home]
                          + [e for e in own[cid] if faction_categories(e) and not faction_categories(e) & any_home])
        if not members:
            report.add('skipped_faction', faction_name(cat), None, 'no units of its own')
            continue
        claimed.update(id(e.node) for e in members)
        factions.append((cat, members))
    # Libraries' root entries no faction took: they join the faction that took the most of
    # that library, or else form a faction of their own (Unaligned Forces' fortifications)
    for cid, cat in catalogues.items():
        if not cat.is_library:
            continue
        rest = _unique([e for e in own[cid] if id(e.node) not in claimed])
        if not rest:
            continue
        taken = Counter()
        for index, (_, members) in enumerate(factions):
            taken[index] = sum(1 for e in members if e.cat is cat)
        best, count = taken.most_common(1)[0] if taken else (None, 0)
        if count:
            factions[best][1].extend(rest)
        else:
            factions.append((cat, rest))
    return sorted(factions, key=lambda f: f[0].name.lower())


def _unique(entries):
    seen, out = set(), []
    for e in entries:
        if id(e.node) not in seen:
            seen.add(id(e.node))
            out.append(e)
    return out


def faction_name(cat):
    """'Imperium - Adeptus Astartes - Blood Angels' -> 'Blood Angels'; 'Library - Titans' -> 'Titans'."""
    name = re.sub(r'\s*-?\s*Library$', '', cat.name)
    return name.rsplit(' - ', 1)[-1]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def import_folder(folder, known_keywords=None):
    """
    Read a BSData folder into the data JSON's reference sections (with integer IDs and BSData IDs).

    `known_keywords`: keyword names to check weapon keywords against (unknown ones are
    reported); None skips the check.
    """
    game_system, catalogues = read_folder(folder)
    report = Report()
    data = {'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [], 'detachments': [], 'enhancements': []}

    imported = []  # (faction ID, catalogue, member entries) for the enhancements, once every detachment is known
    for faction_id, (cat, members) in enumerate(faction_members(catalogues, report), start=1):
        name = faction_name(cat)
        data['factions'].append({'id': faction_id, 'bsdata_id': cat.id, 'name': name,
                                 'army_rules': [_rule_text(r) for r in cat.root.get('rules', []) + cat.root.get('sharedRules', [])
                                                if not r.get('hidden')]})
        for detachment in detachments(cat, members, catalogues, report):
            data['detachments'].append(dict(detachment, id=len(data['detachments']) + 1, faction_id=faction_id))
        imported.append((faction_id, cat, members))
        weapon_ids = {}  # (kind, stat key) -> weapon record id within this faction
        for entry in sorted(members, key=lambda e: e.name.lower()):
            built = build_unit(entry, cat, report, known_keywords)
            if built is None:
                continue
            unit, weapons = built
            for key, record in weapons.items():
                kind = key[0]
                if key not in weapon_ids:
                    table = data[f'{kind}_weapons']
                    record = dict(record, id=len(table) + 1, faction_id=faction_id)
                    table.append(record)
                    weapon_ids[key] = record
                elif record['bsdata_id'] < weapon_ids[key]['bsdata_id']:
                    weapon_ids[key]['bsdata_id'] = record['bsdata_id']  # Lowest ID: stable whatever the order
            loadout, options = unit.pop('_loadout'), unit.pop('_options')
            _key_weapons(unit['wargear'], weapon_ids)
            if not has_choices(unit['wargear']):
                unit['wargear'] = None  # Nothing to choose: no tree to keep
            unit.update(id=len(data['units']) + 1, faction_id=faction_id)
            for kind in ('ranged', 'melee'):
                unit[f'{kind}_weapons'] = [{'weapon_id': weapon_ids[key]['id'], 'qty': qty} for key, qty in loadout[kind]]
                unit[f'{kind}_options'] = [weapon_ids[key]['id'] for key in options[kind]]
            data['units'].append(unit)
        _check_leaders(data, faction_id, name, report)

    detachment_ids = {d['bsdata_id'] for d in data['detachments']}
    names = {i: element.get('name') for c in [game_system, *catalogues.values()] for i, element in c.index.items()
             if element.get('name')}
    for faction_id, cat, members in imported:
        for enhancement in enhancements(cat, members, detachment_ids, names):
            data['enhancements'].append(dict(enhancement, id=len(data['enhancements']) + 1, faction_id=faction_id))

    source = {
        'game_system': {'name': game_system.name, 'revision': game_system.root.get('revision')},
        'catalogues': {cat.name: cat.root.get('revision') for cat in catalogues.values()},
    }
    return ImportResult(data=data, report=report.items, source=source)


def _rule_text(rule):
    return {'name': rule.get('name', ''), 'text': tidy_text(rule.get('description'))}


ENHANCEMENTS_TYPE_ID = 'f759-1bc4-cb3a-f0d2'  # The game system's "Enhancements" cost type (1 per enhancement)


def _condition_tree(node, names):
    """
    A node's "hidden" modifiers as a tree to evaluate later (armies.enhancement_hidden): a list of
    modifiers (hidden if any holds), each {'op': 'and', 'items': [...]} of its conditions and
    groups; a group is {'op': 'and'|'or', 'items': [...]}, a condition {'test', 'scope', 'value',
    'id', 'name'} (`name` is what the ID names in any catalogue (`names`), e.g. a keyword, unit or
    detachment).
    """
    def name_of(child_id):
        return names.get(child_id) or child_id

    def condition(c):
        return {'test': c.get('type'), 'scope': c.get('scope'), 'value': c.get('value'),
                'id': c.get('childId'), 'name': name_of(c.get('childId'))}

    def group(g, op):
        return {'op': op, 'items': [condition(c) for c in g.get('conditions', [])] +
                [group(sub, sub.get('type', 'and')) for sub in g.get('conditionGroups', [])]}

    return [group(m, 'and') for m in node.get('modifiers', [])
            if m.get('field') == 'hidden' and str(m.get('value')).lower() == 'true']


def _tree_ids(trees):
    """Every condition's ID in condition trees."""
    out = []
    for item in trees:
        if 'items' in item:
            out += _tree_ids(item['items'])
        elif item.get('id'):
            out.append(item['id'])
    return out


def enhancements(cat, members, detachment_ids, names):
    """
    [{'bsdata_id', 'name', 'points', 'text', 'buffs', 'detachment_keys', 'hidden_when', 'unit_bsdata_id',
    'bearer_bsdata_ids', 'max_taken', 'counts_once'}] for a faction: the entries costing "Enhancements" in its own catalogue and those it takes units
    from. BSData hides each unless conditions hold (its detachment is in the army, the bearer is a
    Character, Infantry, a given unit...); `hidden_when` keeps those conditions, with the enclosing
    groups', as trees (_condition_tree) that armies.py evaluates for a bearer and the chosen
    detachments (`names`: element names by ID in every file). `detachment_keys` are the detachments they name (`detachment_ids`: every
    faction's, since some enhancements belong to another faction's detachment). One defined
    inside a unit's own entry is an Upgrade for that unit (`unit_bsdata_id`). One that only
    non-Character units' options link to (a group shared by Warptide's Battleline units) goes on
    those units (`bearer_bsdata_ids`) instead of Characters. `buffs` are its effects the maths
    models (rules_text.parse_enhancement: each for the bearer or for its whole unit).
    """
    sources = [cat] + [c for c in dict.fromkeys(e.cat for e in members) if c is not cat]
    found, seen = [], set()
    memo = {}
    reach = {e.id: _linked_ids(e.node, e.cat, memo) for e in members if e.kind in ('unit', 'model')}
    characters = {e.id for e in members if 'Character' in e.category_names()}

    def bearers(path):
        """Units it is offered to when only non-Characters link to it (Warptide's Battleline enhancements); else []."""
        linking = sorted(uid for uid, ids in reach.items() if ids & set(path))
        return linking if linking and not characters & set(linking) else []

    def visit(node, owner, inherited, unit_id, path=()):
        trees = inherited + _condition_tree(node, names)
        path = path + (node.get('id'),)
        if node.get('type') in ('unit', 'model') and unit_id is None:
            unit_id = node.get('id')
        costs = {c.get('typeId'): c.get('value') or 0 for c in node.get('costs', [])}
        if costs.get(ENHANCEMENTS_TYPE_ID) and node.get('id') not in seen and not node.get('hidden'):
            seen.add(node['id'])
            texts = [ability_text(p) for p in node.get('profiles', []) if p.get('typeName') == 'Abilities']
            text = '\n\n'.join(t for t in texts if t)
            found.append({
                'bsdata_id': node['id'],
                'name': node.get('name', ''),
                'points': int(costs.get(PTS_TYPE_ID) or 0),
                'text': text,
                'buffs': parse_enhancement(text)['buffs'],
                'detachment_keys': list(dict.fromkeys(i for i in _tree_ids(trees) if i in detachment_ids)),
                'hidden_when': trees,
                'unit_bsdata_id': unit_id,
                'bearer_bsdata_ids': bearers(path) if unit_id is None else [],
                # How many an army may take (BSData's own limit), and whether those copies share one
                # "Enhancements" between them (Bane-forged weapons: up to 3, counting as 1)
                'max_taken': int(min(limits)) if (limits := [c['value'] for c in node.get('constraints', [])
                                                              if c.get('type') == 'max' and c.get('field') == 'selections'
                                                              and c.get('scope') in ('force', 'roster')]) else None,
                'counts_once': any(m.get('type') == 'divide' and m.get('field') == ENHANCEMENTS_TYPE_ID
                                   for m in node.get('modifiers', []) + [m for g in node.get('modifierGroups', [])
                                                                         for m in g.get('modifiers', [])]),
            })
            return
        for key in ('selectionEntries', 'selectionEntryGroups'):
            for child in node.get(key, []):
                visit(child, owner, trees, unit_id, path)

    for source in sources:
        for key in ('sharedSelectionEntries', 'sharedSelectionEntryGroups', 'selectionEntries'):
            for node in source.root.get(key, []):
                visit(node, source, [], None)
    return found


def _linked_ids(node, cat, memo, depth=0):
    """IDs of the entries and groups an entry's options link to, through links inside linked ones too."""
    out = set()
    if depth > 12:
        return out
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(current)
            continue
        if not isinstance(current, dict):
            continue
        target = current.get('targetId')
        if target and current.get('type') in ('selectionEntry', 'selectionEntryGroup'):
            out.add(target)
            key = (cat.id, target)
            if key not in memo:
                memo[key] = set()  # Guards against links that loop
                element, owner = cat.find(target)
                memo[key] = _linked_ids(element, owner, memo, depth + 1) if element is not None else set()
            out |= memo[key]
        for key in ('selectionEntries', 'selectionEntryGroups', 'entryLinks'):
            stack.extend(current.get(key, []))
    return out


DP_TYPE_ID = '82ae-1066-5107-6ae0'  # The game system's "Detachment Points" cost type
# A detachment's categories: these are force dispositions; "3DP Detachment" marks a 3 DP cost; any
# other is a Unique Tag (no two detachments in an army may share one, e.g. "Phobos Detachment")
FORCE_DISPOSITIONS = ('Take and Hold', 'Purge the Foe', 'Disruption', 'Reconnaissance', 'Priority Assets')


def detachments(cat, members, catalogues, report):
    """
    [{'bsdata_id', 'name', 'rules': [{'name', 'text'}], 'dp', 'dispositions', 'tags'}] for a faction
    (see the module docstring). `dp` is its Detachment Points cost (None if it has none);
    `dispositions` and `tags` (Unique Tags) come from its categories (see FORCE_DISPOSITIONS).
    """
    sources = {cat} | {e.cat for e in members}
    roots = [(cat, node) for node in cat.root.get('entryLinks', []) + cat.root.get('selectionEntries', [])]
    for link in cat.links():
        if link.get('importRootEntries') and link['targetId'] in catalogues:
            linked = catalogues[link['targetId']]
            roots += [(linked, node) for node in linked.root.get('entryLinks', []) + linked.root.get('selectionEntries', [])]

    found, seen = [], set()
    for owner, node in roots:
        entry = resolve_link(node, owner, report) if 'targetId' in node else Entry(node, owner)
        if entry is None or entry.name not in ('Detachment', 'Detachments') or entry.cat not in sources:
            continue
        for group in entry.children(report):
            if group.kind != 'group':
                continue
            for option in group.children(report):
                if option.kind == 'group' or option.id in seen:
                    continue
                seen.add(option.id)
                rules = [_rule_text(r) for r in option.merged('rules') if not r.get('hidden')]
                for info in option.merged('infoLinks'):
                    if info.get('type') == 'rule':
                        target, _ = option.cat.find(info.get('targetId'))
                        if target is not None:
                            rules.append(_rule_text(target))
                rules += [{'name': p['name'], 'text': ability_text(p)} for p in profiles(option, report)
                          if p.get('typeName') == 'Abilities']
                costs = [c for c in option.merged('costs') if c.get('typeId') == DP_TYPE_ID]
                categories = [c for c in option.category_names() if not re.fullmatch(r'\d+ ?DP Detachment', c)]
                found.append({'bsdata_id': option.id, 'name': option.name, 'rules': rules,
                              'dp': int(costs[-1].get('value') or 0) if costs else None,
                              'dispositions': [c for c in categories if c in FORCE_DISPOSITIONS],
                              'tags': [c for c in categories if c not in FORCE_DISPOSITIONS]})
    return found


def _check_leaders(data, faction_id, faction, report):
    """Match Leader and Support targets to the faction's unit names (case-insensitively); report the rest."""
    names = {u['name'].lower(): u['name'] for u in data['units'] if u['faction_id'] == faction_id}
    for unit in data['units']:
        if unit['faction_id'] != faction_id:
            continue
        for key, verb in (('can_lead', 'lead'), ('can_support', 'support')):
            matched = []
            for target in unit.get(key, []):
                canonical = names.get(target.lower())
                if canonical:
                    matched.append(canonical)
                else:
                    report.add('leader', faction, unit['name'], f'can {verb} "{target}", which is not in this faction')
            unit[key] = matched
