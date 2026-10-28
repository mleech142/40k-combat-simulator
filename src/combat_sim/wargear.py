"""
Wargear: choosing a unit's options, checked and priced by BSData's own rules.

The importer keeps each unit's option tree (bsdata.freeze: its models, weapon options and
groups, with their constraints, modifiers and pts costs, and what options without weapons do:
their abilities, the keywords they add and their stat changes; `k` numbers the nodes). This
module rebuilds a selection from it with the same code the importer prices units with:

- **Choices** are {k: count} for the members of the unit's groups (a model variant: how many;
  an option: how many models take it, so 1 of 4 Scions' guns can be a plasma gun: each model
  of an entry ×4 chooses on its own, see choice_slots and _selections). Groups without choices
  take BSData's defaults, as the importer does.
- **Size**: the selection is fitted to the unit's size, adding or removing models of each
  group's default variant first (so choosing two plasma gunners takes two boltgunners out).
- **Checks**: each entry's min/max under its parent and in the whole unit ("1 of each special
  weapon per unit"), with BSData's modifiers to those limits, and BSData's own error messages
  ("Max 1 {this} per 5 models") when their conditions hold. Anything this can't evaluate is
  skipped, never reported as a problem.
- **Effects** (effects()): keywords the options add, their abilities and the stat changes of the
  datasheet's model against the defaults, for the army checks and the Match view.
- **Points** are bsdata.points over the selection (option costs and size modifiers included).
- **Loadout**: each weapon and how many models carry it, for Analytics.

form() describes the choices for the Armies page's popup: each group with its members' counts
and limits, nested under the models that own them.
"""

import re
from collections import Counter
from types import SimpleNamespace

from . import bsdata


class FrozenEntry(bsdata.Entry):
    """A bsdata.Entry over a frozen tree node (bsdata.freeze), so the selection and points code runs on it."""

    def __init__(self, node):
        object.__setattr__(self, 'node', node)
        object.__setattr__(self, 'cat', None)
        object.__setattr__(self, 'link', None)
        object.__setattr__(self, '_children', None)

    @property
    def id(self):
        return self.node['id']

    @property
    def ids(self):
        return {self.node['id'], *self.node.get('ids', [])}

    @property
    def kind(self):
        return self.node.get('type', 'group')

    @property
    def hidden(self):
        return False

    @property
    def k(self):
        return self.node['k']

    def merged(self, key):
        return list(self.node.get(key, []))

    def children(self, report=None):
        if self._children is None:
            object.__setattr__(self, '_children', [FrozenEntry(c) for c in self.node.get('children', [])])
        return self._children

    def is_default_of(self, group):
        default = group.node.get('defaultSelectionEntryId')
        return bool(default) and default in self.ids


# ---------------------------------------------------------------------------
# Building a selection from choices
# ---------------------------------------------------------------------------

def _select(entry, choices, parent=None, count=1):
    """The selection below `entry`: chosen counts where there are choices, else BSData's minimums and defaults."""
    sel = bsdata.Selection(entry, count, parent)
    for child in entry.children():
        if child.kind == 'group':
            _select_group(sel, child, choices)
        else:
            n = choices.get(child.k)
            n = (child.bound('min') or 0) if n is None else n
            if n > 0:
                sel.children.extend(_selections(child, choices, sel, n))
    return sel


def choice_slots(model):
    """
    The choices each model makes on its own: [(slot, members, optional, path)] for a model entry, the
    groups below it (through groups only) where each model picks one option, and the optional
    extras below it (one per model at most); `path`: the names of the groups between. Other models
    below it are left out.
    """
    slots = []

    def visit(entry, path, in_slot=False):
        for child in entry.children():
            if child.kind == 'group':
                members = [m for m in child.children() if m.kind != 'group']
                slot = bool(members) and child.bound('max') == 1 and all(m.kind == 'upgrade' for m in members)
                if slot:
                    slots.append((child, members, False, path))
                visit(child, path + [child.name], slot)
            elif not in_slot and child.kind == 'upgrade' and not child.bound('min') and child.bound('max') == 1:
                slots.append((child, [child], True, path))  # An optional extra (a pick-one group's options aren't)
    visit(model, [])
    return slots


def _selections(entry, choices, parent, count):
    """
    The selections of `count` copies of an entry. A model's own choices (choice_slots) count
    models: {plasma gun: 1} with four Scions gives one Scion with a plasma gun and three with
    the default, as separate selections (the default ones first, so resizing adds those).
    """
    if entry.kind != 'model' or count <= 1:
        return [_select(entry, choices, parent, count)]
    copies = [{} for _ in range(count)]
    for slot, members, optional, _ in choice_slots(entry):
        keys = [m.k for m in members]
        if not any(k in choices for k in keys):
            continue  # Nothing chosen: every model takes BSData's default
        if optional:
            taken = min(max(0, choices.get(keys[0], 0)), count)
            for i in range(count):
                copies[i][keys[0]] = 1 if i >= count - taken else 0
            continue
        default = next((m for m in members if m.is_default_of(slot)), None)
        picked = [m for m in members if m is not default for _ in range(max(0, choices.get(m.k, 0)))][:count]
        rest = default or (picked[0] if picked else None)
        assigned = [rest] * (count - len(picked)) + picked
        for i, member in enumerate(assigned):
            for m in members:
                copies[i][m.k] = 1 if m is member else 0
    groups = {}
    for override in copies:
        key = tuple(sorted(override.items()))
        groups[key] = groups.get(key, 0) + 1
    return [_select(entry, {**choices, **dict(key)}, parent, n) for key, n in groups.items()]


def _select_group(sel, group, choices):
    members = group.children()
    group_sel = bsdata.Selection(group, 1, sel)
    if any(m.k in choices for m in members if m.kind != 'group'):
        for member in members:
            if member.kind == 'group':
                _select_group(group_sel, member, choices)
            elif choices.get(member.k, 0) > 0:
                group_sel.children.extend(_selections(member, choices, group_sel, choices[member.k]))
    else:  # BSData's defaults, as bsdata._select_group (with choices further down kept)
        ordered = sorted(members, key=lambda m: 0 if m.is_default_of(group) else 1)
        room = group.bound('max')
        for member in ordered:
            if member.kind == 'group':
                _select_group(group_sel, member, choices)
                continue
            low = member.bound('min') or 0
            if room is not None:
                low = min(low, room)
                room -= low
            if low > 0:
                group_sel.children.extend(_selections(member, choices, group_sel, low))
        need = (group.bound('min') or 0) - group_sel.total(lambda s: s.entry.kind != 'group', direct_only=True)
        for member in ordered:
            if need <= 0:
                break
            if member.kind == 'group':  # Options in a group inside it: its first one
                need -= _fill_subgroup(group_sel, member, choices)
                continue
            existing = next((s for s in group_sel.children if s.entry is member), None)
            have = existing.count if existing else 0
            add = need if member.bound('max') is None else max(0, min(need, member.bound('max') - have))
            if add:
                if existing:
                    existing.count += add
                else:
                    group_sel.children.append(_select(member, choices, group_sel, add))
                need -= add
    if group_sel.children:
        sel.children.append(group_sel)


def _fill_subgroup(group_sel, group, choices):
    """Select the first option of a group inside a group that needs one; how many were added (0 or 1)."""
    first = next((m for m in group.children() if m.kind != 'group'), None)
    if first is None:
        return 0
    sub_sel = next((s for s in group_sel.children if s.entry is group), None)
    if sub_sel is None:
        sub_sel = bsdata.Selection(group, 1, group_sel)
        group_sel.children.append(sub_sel)
    if any(s.entry.kind != 'group' for s in sub_sel.children):
        return 0
    sub_sel.children.append(_select(first, choices, sub_sel, 1))
    return 1


def _model_groups(sel):
    """Selected groups holding models, and the group entries' model members."""
    return [node for node in sel.walk() if node.entry.kind == 'group' and any(m.kind == 'model' for m in node.entry.children())]


def _model_moves(sel, step):
    """
    Ways to add (step 1) or remove (step -1) one model: [(selection node or None, group selection,
    member entry)], each group's default variant first, then the others.
    """
    moves = []
    for group_sel in _model_groups(sel):
        members = [m for m in group_sel.entry.children() if m.kind == 'model']
        members.sort(key=lambda m: 0 if m.is_default_of(group_sel.entry) else 1)
        for member in members:
            existing = next((s for s in group_sel.children if s.entry is member), None)
            if step < 0 and existing is None:
                continue
            moves.append((existing, group_sel, member))
    for node in sel.walk():  # Models with a count of their own, outside groups
        if node.entry.kind == 'model' and node is not sel and node.parent.entry.kind != 'group':
            moves.append((node, None, node.entry))
    return moves


def _apply_move(move, step):
    """Apply a move; returns its undo."""
    existing, group_sel, member = move
    if existing is None:
        added = _select(member, {}, group_sel, 1)
        group_sel.children.append(added)
        return lambda: group_sel.children.remove(added)
    existing.count += step
    if existing.count == 0 and group_sel is not None:
        group_sel.children.remove(existing)
        return lambda: (setattr(existing, 'count', 1), group_sel.children.append(existing))
    return lambda: setattr(existing, 'count', existing.count - step)


def _fit(sel, models, catalogue):
    """
    Bring the selection to `models` models one at a time, by the first move (default variants
    first) that adds no problem; if every move would, BSData's plain growth (or any removal).
    """
    for _ in range(200):
        current = sel.models()
        if current == models:
            return
        step = 1 if current < models else -1
        before = set(check(sel, catalogue))
        fallback = None
        for move in _model_moves(sel, step):
            undo = _apply_move(move, step)
            if sel.models() == current + step and set(check(sel, catalogue)) <= before:
                break
            undo()
            fallback = fallback or move
        else:
            if step > 0 and bsdata.grow(sel, None):
                continue
            if step < 0 and fallback is not None:
                _apply_move(fallback, step)
                continue
            return


def build(tree, choices=None, models=None, catalogue=None):
    """The selection for a unit's frozen tree with `choices`, fitted to `models` models when given."""
    choices = {int(k): max(0, int(v)) for k, v in (choices or {}).items()}
    sel = _select(FrozenEntry(tree), choices)
    if models:
        _fit(sel, models, catalogue or SimpleNamespace(id=None))
    return sel


def choices_of(sel):
    """{k: count} for every member of every group in the tree under selected nodes (the full state of the choices)."""
    out = Counter()

    def visit(node_sel, weight):
        # Below a model, counts are per model: a model's options count the models taking them
        inner = weight * (node_sel.count if node_sel.entry.kind == 'model' and node_sel is not sel else 1)
        for child in node_sel.entry.children():
            if child.kind == 'group':
                visit_group(node_sel, child, inner)
                continue
            chosen = [s for s in node_sel.children if s.entry is child]
            if child.kind != 'model' and not child.bound('min'):  # An optional extra
                out[child.k] += inner * sum(s.count for s in chosen)
            for s in chosen:
                visit(s, inner)

    def visit_group(parent_sel, group, weight):
        group_sel = next((s for s in parent_sel.children if s.entry is group), None)
        for member in group.children():
            if member.kind == 'group':
                if group_sel is not None:
                    visit_group(group_sel, member, weight)
                continue
            chosen = [s for s in (group_sel.children if group_sel else []) if s.entry is member]
            out[member.k] += weight * sum(s.count for s in chosen)
            for s in chosen:
                visit(s, weight)
    visit(sel, 1)
    return dict(out)


# ---------------------------------------------------------------------------
# Limits and checks
# ---------------------------------------------------------------------------

def _evaluate(modifier, context, sel, catalogue):
    """(holds, times) for a modifier, or (False, 0) when it can't be evaluated."""
    try:
        if not bsdata._conditions_hold(modifier, context, sel, catalogue):
            return False, 0
        return True, bsdata._repeats(modifier, context, sel)
    except bsdata.Unevaluable:
        return False, 0


def limits(entry, context, sel, catalogue, scope='parent'):
    """
    (min, max) of an entry under its parent (`scope` 'unit': in the whole unit), BSData's modifiers
    to those limits applied (None = no limit).
    """
    out = {'min': None, 'max': None}
    for constraint in entry.merged('constraints'):
        if constraint.get('scope') != scope or constraint.get('type') not in out:
            continue
        value = constraint.get('value') or 0
        for modifier in entry.merged('modifiers'):
            if modifier.get('field') == constraint.get('id'):
                holds, times = _evaluate(modifier, context, sel, catalogue)
                if holds:
                    try:
                        value = bsdata._apply(value, modifier, times)
                    except bsdata.Unevaluable:
                        pass
        kind = constraint['type']
        out[kind] = value if out[kind] is None else (max(out[kind], value) if kind == 'min' else min(out[kind], value))
    if out['max'] is not None and out['max'] < 0:
        out['max'] = None  # -1: no limit
    return out['min'], out['max']


def _count(parent_sel, entry):
    """How many of `entry` are selected directly under `parent_sel` (a group: its members' total)."""
    found = [s for s in parent_sel.children if s.entry is entry]
    if entry.kind == 'group':  # Its members, those of groups inside it included
        return sum(g.total(lambda s: s.entry.kind != 'group', direct_only=True) for g in found)
    return sum(s.count for s in found)


def check(sel, catalogue):
    """Problems with a selection: limits broken and BSData's error messages."""
    problems = []

    def visit(node_sel):
        for child in node_sel.entry.children():
            context = next((s for s in node_sel.children if s.entry is child), node_sel)
            low, high = limits(child, context, sel, catalogue)
            count = _count(node_sel, child)
            if low is not None and count < low:
                problems.append(f'{child.name}: {count} chosen; at least {low:g}.')
            if high is not None and count > high:
                problems.append(f'{child.name}: {count} chosen; at most {high:g}.')
        for child_sel in node_sel.children:
            for modifier in child_sel.entry.merged('modifiers'):
                if modifier.get('field') == 'error' and _evaluate(modifier, child_sel, sel, catalogue)[0]:
                    message = str(modifier.get('value') or 'Not allowed').replace('{this}', child_sel.entry.name)
                    if message not in problems:
                        problems.append(message + ('' if message.endswith('.') else '.'))
            visit(child_sel)
    visit(sel)
    # Limits for the whole unit ("1 of each special weapon per unit"), over every model's options
    seen = set()
    for node in sel.walk():
        entry = node.entry
        if node is sel or entry.k in seen or entry.kind == 'group':
            continue
        seen.add(entry.k)
        _, high = limits(entry, node, sel, catalogue, scope='unit')
        count = sel.total(lambda s, e=entry: s.entry is e)
        if high is not None and count > high:
            problems.append(f'{entry.name}: {count} in the unit; at most {high:g}.')
    return problems


# ---------------------------------------------------------------------------
# What the pages get
# ---------------------------------------------------------------------------

def default_used(names):
    """
    Which weapons are used by default: all, except later profiles of a "Weapon - Profile" family
    (alternative profiles of one gun). Same rule as the Simulator and Analytics pages.
    """
    families, used = set(), []
    for name in names:
        family = name.split(' - ')[0].strip().lower() if ' - ' in name else None
        used.append(not family or family not in families)
        if family:
            families.add(family)
    return used


def loadout(sel):
    """[(weapon type, weapon ID, models carrying it)] for a selection."""
    counts = Counter()
    for node in sel.walk():
        carriers = node.count * node.multiplier() if node is not sel else 1
        for kind, weapon_id in node.entry.node.get('weapons', []):
            counts[(kind, weapon_id)] += carriers
    return [(kind, weapon_id, qty) for (kind, weapon_id), qty in counts.items()]


def form(sel, catalogue):
    """
    The choices for the popup: [{'k', 'title', 'kind' ('one'|'counts'|'optional'), 'min', 'max',
    'total', 'members': [{'k', 'name', 'count', 'max', 'default'}]}], groups with a single fixed
    member left out; the choices inside a chosen model or option follow it.
    """
    sections = []

    def matters(entry):
        node = entry.node
        return bool(node.get('weapons') or node.get('abilities') or node.get('keywords') or node.get('stats'))

    def visit(node_sel, path, skip=frozenset()):
        for child in node_sel.entry.children():
            if child.kind == 'group':
                visit_group(node_sel, child, path, skip)
                continue
            chosen = [s for s in node_sel.children if s.entry is child]
            low, high = limits(child, chosen[0] if chosen else node_sel, sel, catalogue)
            count = sum(s.count for s in chosen)
            if child.k not in skip and not low and high and matters(child):  # An optional extra on its own
                sections.append({'k': child.k, 'title': ' › '.join(path + [child.name]), 'kind': 'optional',
                                 'min': 0, 'max': high, 'total': count,
                                 'members': [{'k': child.k, 'name': child.name, 'count': count, 'max': high, 'default': False}]})
            visit_chosen(chosen, child, path + ([child.name] if child.kind != 'unit' else []))

    def visit_chosen(chosen, entry, path):
        """The choices inside a chosen entry: those of several models as counts (visit_models)."""
        if not chosen:
            return
        if entry.kind == 'model' and sum(s.count for s in chosen) > 1 and choice_slots(entry):
            visit_models(chosen, entry, path)
        else:
            visit(chosen[0], path)

    def visit_group(parent_sel, group, path, skip=frozenset()):
        group_sel = next((s for s in parent_sel.children if s.entry is group), None)
        low, high = limits(group, group_sel or parent_sel, sel, catalogue)
        members = [m for m in group.children() if m.kind != 'group']
        entries = []
        for member in members:
            chosen = [s for s in (group_sel.children if group_sel else []) if s.entry is member]
            m_low, m_high = limits(member, chosen[0] if chosen else (group_sel or parent_sel), sel, catalogue)
            entries.append({'k': member.k, 'name': member.name, 'count': sum(s.count for s in chosen),
                            'max': m_high, 'default': member.is_default_of(group)})
        fixed = len(members) == 1 and low is not None and low == high
        if members and not fixed and group.k not in skip:
            one = high == 1 and all((e['max'] or 1) <= 1 for e in entries)
            sections.append({'k': group.k, 'title': ' › '.join(path + [group.name]), 'kind': 'one' if one else 'counts',
                             'min': low, 'max': high, 'total': sum(e['count'] for e in entries), 'members': entries})
        for sub in group.children():
            if sub.kind == 'group' and group_sel is not None:
                visit_group(group_sel, sub, path + [group.name], skip)
        selected = [s for s in (group_sel.children if group_sel else []) if s.entry.kind != 'group']
        for entry in {id(s.entry): s.entry for s in selected}.values():
            visit_chosen([s for s in selected if s.entry is entry], entry, path + [entry.name])

    def carries(copy, member):
        """Does one model's selection take this option (not counting other models below it)?"""
        stack = list(copy.children)
        while stack:
            node = stack.pop()
            if node.entry is member:
                return True
            if node.entry.kind != 'model':
                stack.extend(node.children)
        return False

    def visit_models(copies, model, path):
        """Several models' own choices, each option counting the models taking it ("1× Plasma gun")."""
        total = sum(c.count for c in copies)
        skip = set()
        for slot, members, optional, between in choice_slots(model):
            skip.add(slot.k)
            entries = []
            for member in members:
                _, cap = limits(member, copies[0], sel, catalogue, scope='unit')  # "1 of each per unit"
                entries.append({'k': member.k, 'name': member.name, 'max': total if cap is None else min(total, cap),
                                'count': sum(c.count for c in copies if carries(c, member)),
                                'default': not optional and member.is_default_of(slot)})
            title = ' › '.join(path + between + [slot.name])
            if optional:
                sections.append({'k': slot.k, 'title': title, 'kind': 'optional', 'min': 0, 'max': entries[0]['max'],
                                 'total': entries[0]['count'], 'members': entries})
            elif len(members) > 1:
                low = total if slot.bound('min') else 0
                sections.append({'k': slot.k, 'title': title, 'kind': 'counts', 'min': low, 'max': total,
                                 'total': sum(e['count'] for e in entries), 'members': entries})
        visit(copies[0], path, frozenset(skip))  # Anything else they choose (as the first models have it)
    visit(sel, [])
    return sections


def summary(sections):
    """A short line of the non-default choices: '2× Plague Marine w/ meltagun, Power fist'."""
    parts = []
    for section in sections:
        for member in section['members']:
            if member['count'] and not member['default']:
                parts.append(f"{member['count']}× {member['name']}" if member['count'] > 1 else member['name'])
    return ', '.join(dict.fromkeys(parts))


INVULNERABLE_TEXT = re.compile(r'^the bearer has a (\d)\+ invulnerable save\.?$', re.I)


def _main_model(sel):
    """The most numerous model's selection (the one whose stat line the datasheet shows), or None."""
    counts = Counter()
    nodes = {}
    for node in sel.walk():
        if node.entry.kind == 'model':  # Models alike: the same entry with the same stat changes
            key = (node.entry.id, tuple(sorted(str(c) for c in _carried(node, 'stats'))))
            counts[key] += node.count * node.multiplier()
            nodes.setdefault(key, node)
    return nodes[counts.most_common(1)[0][0]] if counts else None


def _carried(model_sel, key):
    """What the options a model carries give (`key`: 'abilities' or 'stats'), not those of other models below it."""
    out = []

    def visit(node):
        for child in node.children:
            if child.entry.kind == 'model':
                continue
            if child.entry.kind == 'upgrade':
                out.extend(child.entry.node.get(key, []))
            visit(child)
    if model_sel is not None:
        visit(model_sel)
    return out


def effects(sel, default_sel):
    """
    What the chosen options change on the datasheet, against BSData's defaults (which the imported
    unit already shows): {'keywords' (added by any option), 'abilities' ([name, text] of the options
    taken), 'dropped_abilities' (names of default options' abilities no longer taken), 'stats'
    ({'W': -1}: the shown model's characteristics against the defaults), 'invulnerable_save'
    (from a taken option's text on that model, or None)}.
    """
    def options(s):
        return [n for n in s.walk() if n.entry.kind == 'upgrade']

    keywords = list(dict.fromkeys(k for n in options(sel) for k in n.entry.node.get('keywords', [])))
    abilities = list({a[0]: a for n in options(sel) for a in n.entry.node.get('abilities', [])}.values())
    taken = {a[0] for a in abilities}
    dropped = list(dict.fromkeys(a[0] for n in options(default_sel) for a in n.entry.node.get('abilities', [])
                                 if a[0] not in taken))
    stats = Counter()
    for sign, s in ((1, sel), (-1, default_sel)):
        for name, kind, value in _carried(_main_model(s), 'stats'):
            if kind in ('increment', 'decrement'):
                stats[name] += sign * (1 if kind == 'increment' else -1) * int(value or 0)
    invulnerable = None
    for name, text in _carried(_main_model(sel), 'abilities'):
        m = INVULNERABLE_TEXT.match(bsdata.plain_text(text))
        if m:
            invulnerable = int(m.group(1))
    return {'keywords': keywords, 'abilities': abilities, 'dropped_abilities': dropped,
            'stats': {k: v for k, v in stats.items() if v}, 'invulnerable_save': invulnerable}


def has_keywords(tree):
    """True if any option in a frozen tree adds keywords (so the army check needs the chosen ones)."""
    return bool(tree.get('keywords')) or any(has_keywords(c) for c in tree.get('children', []))


def evaluate(tree, choices, models, catalogue_id):
    """
    Everything the Armies page needs for a unit's wargear: {'models', 'points', 'problems',
    'choices' (the full state), 'loadout', 'form', 'summary', 'effects' (see effects())}.
    """
    catalogue = SimpleNamespace(id=catalogue_id)
    sel = build(tree, choices, models, catalogue)
    sections = form(sel, catalogue)
    points, _ = bsdata.points(sel, catalogue)
    return {'models': sel.models(), 'points': points, 'problems': check(sel, catalogue), 'choices': choices_of(sel),
            'loadout': loadout(sel), 'form': sections, 'summary': summary(sections),
            'effects': effects(sel, build(tree, None, sel.models(), catalogue))}
