"""
Wargear: choosing a unit's options, checked and priced by BSData's own rules.

The importer keeps each unit's option tree (bsdata.freeze: its models, weapon options and
groups, with their constraints, modifiers and pts costs; `k` numbers the nodes). This module
rebuilds a selection from it with the same code the importer prices units with:

- **Choices** are {k: count} for the members of the unit's groups (a model variant: how many;
  an option: 0 or 1). Groups without choices take BSData's defaults, as the importer does.
- **Size**: the selection is fitted to the unit's size, adding or removing models of each
  group's default variant first (so choosing two plasma gunners takes two boltgunners out).
- **Checks**: each entry's min/max under its parent, with BSData's modifiers to those limits,
  and BSData's own error messages ("Max 1 {this} per 5 models") when their conditions hold.
  Anything this can't evaluate is skipped, never reported as a problem.
- **Points** are bsdata.points over the selection (option costs and size modifiers included).
- **Loadout**: each weapon and how many models carry it, for Analytics.

form() describes the choices for the Armies page's popup: each group with its members' counts
and limits, nested under the models that own them.
"""

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
                sel.children.append(_select(child, choices, sel, n))
    return sel


def _select_group(sel, group, choices):
    members = group.children()
    group_sel = bsdata.Selection(group, 1, sel)
    if any(m.k in choices for m in members if m.kind != 'group'):
        for member in members:
            if member.kind == 'group':
                _select_group(group_sel, member, choices)
            elif choices.get(member.k, 0) > 0:
                group_sel.children.append(_select(member, choices, group_sel, choices[member.k]))
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
                group_sel.children.append(_select(member, choices, group_sel, low))
        need = (group.bound('min') or 0) - sum(s.count for s in group_sel.children if s.entry.kind != 'group')
        for member in ordered:
            if need <= 0:
                break
            if member.kind == 'group':
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
    out = {}

    def visit(node_sel):
        for child in node_sel.entry.children():
            if child.kind == 'group':
                visit_group(node_sel, child)
            else:
                chosen = [s for s in node_sel.children if s.entry is child]
                if chosen:
                    visit(chosen[0])

    def visit_group(parent_sel, group):
        group_sel = next((s for s in parent_sel.children if s.entry is group), None)
        for member in group.children():
            if member.kind == 'group':
                if group_sel is not None:
                    visit_group(group_sel, member)
                continue
            chosen = [s for s in (group_sel.children if group_sel else []) if s.entry is member]
            out[member.k] = sum(s.count for s in chosen)
            if chosen:
                visit(chosen[0])
    visit(sel)
    return out


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


def limits(entry, context, sel, catalogue):
    """(min, max) of an entry under its parent, BSData's modifiers to those limits applied (None = no limit)."""
    out = {'min': None, 'max': None}
    for constraint in entry.merged('constraints'):
        if constraint.get('scope') != 'parent' or constraint.get('type') not in out:
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

    def visit(node_sel, path):
        for child in node_sel.entry.children():
            if child.kind == 'group':
                visit_group(node_sel, child, path)
                continue
            chosen = next((s for s in node_sel.children if s.entry is child), None)
            low, high = limits(child, chosen or node_sel, sel, catalogue)
            if not low and high and child.node.get('weapons'):  # An optional extra on its own
                sections.append({'k': child.k, 'title': ' › '.join(path + [child.name]), 'kind': 'optional',
                                 'min': 0, 'max': high, 'total': chosen.count if chosen else 0,
                                 'members': [{'k': child.k, 'name': child.name, 'count': chosen.count if chosen else 0,
                                              'max': high, 'default': False}]})
            if chosen is not None:
                visit(chosen, path + ([child.name] if child.kind != 'unit' else []))

    def visit_group(parent_sel, group, path):
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
        if members and not fixed:
            one = high == 1 and all((e['max'] or 1) <= 1 for e in entries)
            sections.append({'k': group.k, 'title': ' › '.join(path + [group.name]), 'kind': 'one' if one else 'counts',
                             'min': low, 'max': high, 'total': sum(e['count'] for e in entries), 'members': entries})
        for sub in group.children():
            if sub.kind == 'group' and group_sel is not None:
                visit_group(group_sel, sub, path + [group.name])
        for s in (group_sel.children if group_sel else []):
            if s.entry.kind != 'group':
                visit(s, path + [s.entry.name])
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


def evaluate(tree, choices, models, catalogue_id):
    """
    Everything the Armies page needs for a unit's wargear: {'models', 'points', 'problems',
    'choices' (the full state), 'loadout', 'form', 'summary'}.
    """
    catalogue = SimpleNamespace(id=catalogue_id)
    sel = build(tree, choices, models, catalogue)
    sections = form(sel, catalogue)
    points, _ = bsdata.points(sel, catalogue)
    return {'models': sel.models(), 'points': points, 'problems': check(sel, catalogue), 'choices': choices_of(sel),
            'loadout': loadout(sel), 'form': sections, 'summary': summary(sections)}
