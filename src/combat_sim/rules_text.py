"""
Rule text: tidying it and reading the effects the maths models out of it.

Rule text (from BSData, or Wahapedia converted by wahapedia.py) keeps its markup for display
(`**bold**`, `*italic*`, `^^keyword^^`, bullets; see Core.formatRuleText) with its whitespace
tidied (tidy_text). Everything parsed from it reads the plain words (plain_text).

Effects become rows like faction rules: {'applies_to': 'Attacker'|'Target', 'phase':
'Shooting'|'Fighting'|'Both', 'modifier': keyword text}. Only sentences that say something the
maths models become rows; the rest are returned as skipped, so they can be reported.

- parse_leader_buffs: the "While this model is leading a unit, ..." part of a leader's ability.
- parse_effects: any rule's text (a stratagem's EFFECT, a detachment or army rule). It reads
  the same sentences as the leader parser after rewording "your unit" as "that unit", plus
  the shorter 11th edition wording ("Your unit's ranged attacks have [LETHAL HITS]", "Your
  unit has a 4+ invulnerable save", "Attacks that target your unit have -1 AP"). Lead-ins
  that only say how long it lasts are dropped; conditions ("If your unit is within range of
  an objective marker, ...") are dropped too but returned, since ticking a rule is the
  player saying it applies.
"""

import re


TIDY_CHARACTERS = str.maketrans({'\u00a0': ' ', '\u2011': '-', '\u2010': '-', '\u2013': '-', '\u2019': "'"})


def tidy_text(text):
    """BSData text with non-breaking spaces and hyphens made ordinary, repeated spaces collapsed
    and trailing spaces removed; its markup is kept."""
    lines = [re.sub(r' {2,}', ' ', line).rstrip() for line in (text or '').translate(TIDY_CHARACTERS).split('\n')]
    return '\n'.join(lines).strip()


# BSData's text markup: **bold**, *italic*, ^^keyword^^, <ins>changed</ins>, footnote daggers
MARKUP = re.compile(r'\*+|\^\^|</?ins>|[†‡]')


def plain_text(text):
    """The words of BSData text without its markup, for parsing (bullets and line breaks kept)."""
    return re.sub(r' {2,}', ' ', MARKUP.sub('', tidy_text(text)))


# ---------------------------------------------------------------------------
# Leader buffs: "While this model is leading a unit, ..." as modifier rows
# ---------------------------------------------------------------------------

LEADING = re.compile(r'while this model is leading a unit,?\s*', re.I)
PHASE_OF = {'ranged': 'Shooting', 'melee': 'Fighting', None: 'Both'}
UNIT = r"(?:models in )?(?:that|this model's) unit"


def _ability_name(text):
    """'[SUSTAINED HITS 1]' -> 'Sustained Hits 1', 'ANTI-INFANTRY 4+' -> 'Anti-Infantry 4+'."""
    return text.strip(' []').lower().title()


def _buff(applies_to, kind, modifier):
    return {'applies_to': applies_to, 'phase': PHASE_OF[kind.lower() if kind else None], 'modifier': modifier}


def _attack_effects(kind, effect):
    """Buffs from the end of "each time a model in that unit makes a [ranged|melee] attack, ..."."""
    found = []
    for m in re.finditer(r'add (\d) to the (hit|wound) roll', effect, re.I):
        found.append(_buff('Attacker', kind, f'{m.group(2).title()} Modifier +{m.group(1)}'))
    for m in re.finditer(r're-roll (?:the|a) (hit|wound) roll(?: of (1))?', effect, re.I):
        name = 'Reroll Hits' if m.group(1).lower() == 'hit' else 'Reroll Wounds'
        found.append(_buff('Attacker', kind, f'{name} 1' if m.group(2) else name))
    m = (re.search(r'unmodifi?ed (hit|wound) roll of (\d)\+ scores a critical', effect, re.I)  # "unmodifed" too
         or re.search(r'critical (hit|wound) is scored on an unmodified (?:hit|wound) roll of (\d)\+', effect, re.I))
    if m:
        found.append(_buff('Attacker', kind, f'Critical {m.group(1).title()}s {m.group(2)}+'))
    m = re.search(r'improve the (.+?) characteristics? of that attack by (\d)', effect, re.I)
    if m:
        names = [IMPROVED.get(n.strip().lower()) for n in re.split(r',| and ', m.group(1)) if n.strip()]
        found += [_buff('Attacker', kind, f'{name}{m.group(2)}') for name in names if name]
    for m in re.finditer(r'add (\d) to the (strength|attacks|damage) characteristic of that attack', effect, re.I):
        found.append(_buff('Attacker', kind, f'{m.group(2).title()} Modifier +{m.group(1)}'))
    for ability in re.findall(r'that attack has the (\[[^\]]+\])', effect, re.I):
        found.append(_buff('Attacker', kind, _ability_name(ability)))
    return found


# "Improve the X characteristic ... by N": the modifier for each characteristic (better = lower for AP and skills)
IMPROVED = {'armour penetration': 'AP Modifier -', 'armor penetration': 'AP Modifier -', 'strength': 'Strength Modifier +',
            'attacks': 'Attacks Modifier +', 'damage': 'Damage Modifier +', 'ballistic skill': 'BS Modifier -',
            'weapon skill': 'WS Modifier -'}


def _sentence_buffs(sentence):
    """The buffs one sentence grants, or [] if it says nothing the maths models (or adds a condition)."""
    text = sentence.strip().rstrip('.').strip()
    text = re.sub(r'^in addition,?\s*', '', text, flags=re.I)

    # Weapons gain abilities: "[ranged|melee] weapons equipped by models in that unit have the [X] (and [Y]) abilities"
    m = re.match(r'^(?:(ranged|melee) )?weapons equipped by ' + UNIT + r' have the ((?:\[[^\]]+\](?:,? and |, )?)+) abili?t(?:y|ies)$', text, re.I)  # "abilties": a BSData typo
    if m:
        return [_buff('Attacker', m.group(1), _ability_name(a)) for a in re.findall(r'\[[^\]]+\]', m.group(2))]

    # Weapon characteristics: "improve the Armour Penetration characteristic of melee weapons ... by 1",
    # "add 1 to the Strength characteristic of melee weapons equipped by models in that unit"
    m = re.match(r'^improve the (.+?) characteristics? of (?:(ranged|melee) )?weapons equipped by ' + UNIT + r' by (\d)$', text, re.I)
    if m:
        names = [IMPROVED.get(n.strip().lower()) for n in re.split(r',| and ', m.group(1)) if n.strip()]
        if names and all(names):
            return [_buff('Attacker', m.group(2), f'{name}{m.group(3)}') for name in names]
    m = re.match(r'^add (\d) to the (strength|attacks|damage) characteristic of (?:(ranged|melee) )?weapons equipped by ' + UNIT + '$', text, re.I)
    if m:
        return [_buff('Attacker', m.group(3), f'{m.group(2).title()} Modifier +{m.group(1)}')]

    # "Each time a model in that unit makes a [ranged|melee] attack, ..." (no extra condition after "attack")
    m = re.match(r'^each time a model in ' + UNIT.replace('(?:models in )?', '') + r' makes an? (?:(ranged|melee) )?attack, (.+)$', text, re.I)
    if m:
        return _attack_effects(m.group(1), m.group(2))

    # Defensive: invulnerable save, Feel No Pain (unconditional only), Stealth, -1 to be hit or wounded
    m = re.match(r'^models in that unit have an? (\d)\+ invulnerable save$', text, re.I)
    if m:
        return [_buff('Target', None, f'Invulnerable Save {m.group(1)}+')]
    m = re.match(r'^models in that unit have the feel no pain (\d)\+ ability$', text, re.I)
    if m:
        return [_buff('Target', None, f'Feel No Pain {m.group(1)}+')]
    if re.match(r'^models in that unit have the stealth ability$', text, re.I):
        return [_buff('Target', 'ranged', 'Stealth')]
    m = re.match(r'^each time an? (?:(ranged|melee) )?attack targets that unit, subtract (\d) from the (hit|wound) roll$', text, re.I)
    if m:
        return [_buff('Target', m.group(1), f'{m.group(3).title()} Modifier -{m.group(2)}')]
    m = re.match(r'^each time an attack is allocated to a model in that unit, subtract (\d) from the damage characteristic of that attack$', text, re.I)
    if m:
        return [_buff('Target', None, f'Damage Modifier -{m.group(1)}')]
    return []


def parse_leader_buffs(text):
    """
    (buffs, sentences not modelled) from an ability's text, plain or marked up. Only the part
    after "While this model is leading a unit," counts; text without it gives ([], []).
    """
    plain = plain_text(text)
    m = LEADING.search(plain)
    if not m:
        return [], []
    buffs, skipped = [], []
    body = plain[m.end():].split('\n\n')[0].replace('\n', ' ')
    for sentence in re.split(r'(?<=\.)\s+(?=[A-Z])', body):
        if not sentence.strip():
            continue
        found = _sentence_buffs(sentence)
        if found:
            buffs.extend(b for b in found if b not in buffs)
        else:
            skipped.append(sentence.strip())
    return buffs, skipped


# ---------------------------------------------------------------------------
# Any rule's effects: stratagems, detachment rules, army rules
# ---------------------------------------------------------------------------

# "your unit" (a stratagem's TARGET) reads as the leader parser's "that unit"
# (keywords in capitals can come between: "your SMOKE unit")
YOUR_UNIT = [(re.compile(r"\b[Yy]our (?:[A-Z][A-Z'-]+ )*(?:unit|model)'s|\b[Yy]our units'"), "that unit's"),
             (re.compile(r"\b(?:[Yy]our|[Tt]he selected) (?:[A-Z][A-Z'-]+ )*(?:unit|model)\b"), 'that unit')]
# Lead-ins that only say how long an effect lasts
DURATION = re.compile(r"^until (?:the end of (?:the|this|that|your opponent's next) (?:phase|turn|battle round|battle|fight)"
                      r"|the attacking unit has finished making (?:its|all of its) attacks"
                      r"|the start of your next (?:command phase|turn)),?\s*", re.I)
TRAILING_DURATION = re.compile(r',? until (?:the end of the (?:phase|turn|battle round|battle)'
                               r'|that (?:enemy|attacking) unit has (?:attacked|finished making its attacks|resolved its attacks))$',
                               re.I)
RULE_REFERENCE = re.compile(r'\s*\(\d+(?:\.\d+)+\)')  # "(13.08)": a section of the core rules
# Lead-ins that set a condition: dropped, but kept as notes
CONDITION = re.compile(r'^((?:if|while) [^,]{3,200}),\s*', re.I)
SIGNED = r'([+-]\d)'
STATS = r'(ap|to hit|to wound|damage|strength|attacks|a|s)'


def _signed(value, name):
    return f'{name} {value}'


def _attacker_items(kind, rest):
    """Buffs from "... attacks have [LETHAL HITS] and +1 AP" (None if any part isn't understood)."""
    found = []
    for ability in re.findall(r'\[([^\]]+)\]', rest):
        if ':' in ability or ability.lower().startswith('anti-non'):  # Only against some targets
            return None
        found.append(_buff('Attacker', kind, _ability_name(ability)))
    for value, what in re.findall(SIGNED + ' ' + STATS + r'\b', rest, re.I):
        what = what.lower()
        if what == 'ap':  # +1 AP improves it: the keyword counts down
            found.append(_buff('Attacker', kind, f'AP Modifier {"-" if value[0] == "+" else "+"}{value[1:]}'))
        else:
            name = {'to hit': 'Hit Modifier', 'to wound': 'Wound Modifier', 'a': 'Attacks Modifier', 's': 'Strength Modifier'}.get(
                what, f'{what.title()} Modifier')
            found.append(_buff('Attacker', kind, _signed(value, name)))
    leftover = re.sub(r'\[[^\]]+\]|' + SIGNED + ' ' + STATS + r'\b|\band\b|[,.:;]', '', rest, flags=re.I)
    return found if found and not leftover.strip() else None


def _target_effect(kind, effect):
    """A defensive effect after "each time an attack targets that unit, ..." ([] if not understood)."""
    m = re.match(r'^subtract (\d) from the (hit|wound) roll$', effect, re.I)
    if m:
        return [_buff('Target', kind, f'{m.group(2).title()} Modifier -{m.group(1)}')]
    m = re.match(r'^worsen the armou?r penetration characteristic of that attack by (\d)$', effect, re.I)
    if m:
        return [_buff('Target', kind, f'AP Modifier +{m.group(1)}')]
    m = re.match(r'^subtract (\d) from the damage characteristic of that attack$', effect, re.I)
    if m:
        return [_buff('Target', kind, f'Damage Modifier -{m.group(1)}')]
    return []


def _effect_buffs(text):
    """(buffs, condition or None) from one plain sentence already reworded to "that unit"."""
    # 11th edition: "that unit's [ranged|melee] attacks [that target X] have [A] and +1 AP"
    m = re.match(r"^that unit's (?:(ranged|melee) )?attacks(?P<cond> \([^)]*\)| that target [^,:]+?| against [^,:]+?)?:?"
                 r" (?:have|gain):? (?P<rest>.+)$", text, re.I)
    if m:
        found = _attacker_items(m.group(1), m.group('rest'))
        return found or [], (m.group('cond') or '').strip() or None
    # "that unit's attacks can re-roll hit rolls of 1 and wound rolls of 1" / "can: Re-roll wound rolls of 1"
    m = re.match(r"^that unit's (?:(ranged|melee) )?attacks(?P<cond> that target [^,:]+?)?:? can:? re-?roll (?P<rolls>"
                 r"(?:(?:the )?(?:hit|wound) rolls?(?: of 1)?(?: and |, )?)+)$", text, re.I)
    if m:
        found = []
        for roll, ones in re.findall(r'(hit|wound) rolls?( of 1)?', m.group('rolls'), re.I):
            name = 'Reroll Hits' if roll.lower() == 'hit' else 'Reroll Wounds'
            found.append(_buff('Attacker', m.group(1), f'{name} 1' if ones else name))
        return found, (m.group('cond') or '').strip() or None

    # Defensive: "that unit has a 4+ invulnerable save / 4+ InSv / Feel No Pain 5+ / Stealth"
    m = re.match(r'^(?:that unit|models in that unit) (?:has|have) (?:an? )?(\d)\+ (?:insv|invulnerable save)$', text, re.I)
    if m:
        return [_buff('Target', None, f'Invulnerable Save {m.group(1)}+')], None
    m = re.match(r'^(?:that unit|models in that unit) (?:has|have) (?:the )?feel no pain (\d)\+(?: ability)?$', text, re.I)
    if m:
        return [_buff('Target', None, f'Feel No Pain {m.group(1)}+')], None
    if re.match(r'^(?:that unit|models in that unit) (?:has|have) (?:the )?stealth(?: ability)?$', text, re.I):
        return [_buff('Target', 'ranged', 'Stealth')], None
    # The benefit of cover: the maths models it as Stealth (cover against ranged attacks)
    m = re.match(r'^(?:(?:that unit|models in that unit) (?:has|have)|each time an? (?:ranged )?attack targets (?:either )?that unit\b'
                 r'(?P<cond>.*), the target has) the benefit of cover(?: against that attack)?$', text, re.I)
    if m:
        return [_buff('Target', 'ranged', 'Stealth')], (m.group('cond') or '').strip(' ,') or None
    if re.match(r'^that unit shoots using snap shooting$', text, re.I):
        return [_buff('Attacker', 'ranged', 'Snap Shooting')], None
    # "that model's melee weapons have the [PRECISION] ability" (one model of the unit)
    m = re.match(r"^that (?:model|unit)'s (?:(ranged|melee) )?weapons have the ((?:\[[^\]]+\](?:,? and |, )?)+) abilit(?:y|ies)$",
                 text, re.I)
    if m:
        return [_buff('Attacker', m.group(1), _ability_name(a)) for a in re.findall(r'\[[^\]]+\]', m.group(2))], None
    # "[ranged|melee] attacks that target that unit have -1 AP / -1 to hit / -1 damage"
    m = re.match(r'^(?:(ranged|melee) )?attacks that target that unit(?P<cond> [^,]+?)? have ' + SIGNED +
                 r' (ap|to hit|to wound|damage)(?: rolls?)?(?P<after> .+)?$', text, re.I)
    if m:
        value, what = m.group(3), m.group(4).lower()
        if what == 'ap':  # -1 AP worsens it: the keyword counts up
            modifier = f'AP Modifier {"+" if value[0] == "-" else "-"}{value[1:]}'
        else:
            modifier = _signed(value, {'to hit': 'Hit Modifier', 'to wound': 'Wound Modifier'}.get(what, 'Damage Modifier'))
        cond = ' '.join(c.strip() for c in (m.group('cond'), m.group('after')) if c)
        return [_buff('Target', m.group(1), modifier)], cond or None
    # "each time an attack targets that unit[, if ...], subtract 1 from the Hit roll / worsen the AP ..."
    # "[ranged|melee] weapons equipped by models in that unit have the [X] ability while targeting ..."
    m = re.match(r'^(?:(ranged|melee) )?weapons equipped by models in that unit(?: \([^)]*\))? have the ((?:\[[^\]]+\](?:,? and |, )?)+) '
                 r'abilit(?:y|ies)(?P<cond> (?:while|when|against|if) .+)?$', text, re.I)
    if m and m.group('cond'):
        return [_buff('Attacker', m.group(1), _ability_name(a)) for a in re.findall(r'\[[^\]]+\]', m.group(2))], \
            m.group('cond').strip()
    m = re.match(r'^each time an? (?:(ranged|melee) )?attack (?:targets |is allocated to )(?:a model in )?that unit'
                 r'(?P<cond>(?: [^,]+)?(?:, (?:if|while) [^,]+)?), (?P<effect>.+)$', text, re.I)
    if m:
        found = _target_effect(m.group(1), m.group('effect'))
        return found, (m.group('cond').strip(' ,') or None) if found else None
    # "each time a model in that unit makes an attack that targets X, ..." (the leader parser allows no condition)
    m = re.match(r'^each time a model in that unit makes an? (?:(ranged|melee) )?attack(?P<cond> (?:that targets|with) [^,]+)?'
                 r', (?:(?P<cond2>(?:if|while) [^,]+), )?(?P<effect>.+)$', text, re.I)
    if m:
        found = _attack_effects(m.group(1), m.group('effect'))
        cond = ' '.join(c.strip() for c in (m.group('cond'), m.group('cond2')) if c)
        return found, (cond or None) if found else None
    return _sentence_buffs(text), None


def sentences(plain):
    """A rule's plain text in sentences (bullets and line breaks end one too)."""
    found, header = [], None
    for line in plain.split('\n'):
        line = re.sub(r'^\s*[-▪■•]\s+', '', line).strip()
        if not line:
            continue
        if line.lower().startswith('or:'):  # An alternative to the lines above: a choice the maths can't make
            header = None
        elif header:  # "Your unit's ranged attacks:" / "Can re-roll wound rolls of 1." / "Have [IGNORES COVER]."
            found.append(f'{header} {line}')
            continue
        if line.endswith(':') and not line.lower().startswith('or:'):
            header = line
            continue
        found += [s.strip() for s in re.split(r'(?<=\.)\s+(?=[A-Z\[])', line) if s.strip()]
    return found


def parse_effects(text):
    """
    {'buffs', 'skipped', 'conditions'} from a rule's text (plain or marked up): rows for the
    sentences the maths models, the sentences it doesn't, and the conditions dropped on the way.
    """
    buffs, skipped, conditions = [], [], []
    for sentence in sentences(plain_text(text)):
        reworded = TRAILING_DURATION.sub('', RULE_REFERENCE.sub('', sentence).rstrip('.').strip())
        # A label ("Doctrine: Each time ..."), not a list's opening ("Your unit's ranged attacks: Have [LANCE]")
        reworded = re.sub(r"^(?!(?i:your|that|each|models?|ranged|melee|weapons)\b)[A-Z][\w' -]{0,40}:\s+(?=[A-Z])", '', reworded)
        reworded = re.sub(r'^in addition,?\s*', '', reworded, flags=re.I)
        condition = None
        while True:  # Duration and condition lead-ins come in either order
            before = reworded
            reworded = DURATION.sub('', reworded)
            m = CONDITION.match(reworded)
            if m:
                condition = m.group(1)  # In the rule's own words ("your unit")
                reworded = reworded[m.end():]
            if reworded == before:
                break
        for pattern, replacement in YOUR_UNIT:
            reworded = pattern.sub(replacement, reworded)
        reworded = re.sub(r'\beach time (?:that unit|a model from that unit) makes', 'each time a model in that unit makes',
                          reworded, flags=re.I)
        found, inner = _effect_buffs(reworded[:1].upper() + reworded[1:])
        if found:
            buffs.extend(b for b in found if b not in buffs)
            conditions += [c for c in (condition, inner) if c]
        else:
            skipped.append(sentence)
    return {'buffs': buffs, 'skipped': skipped, 'conditions': conditions}


# ---------------------------------------------------------------------------
# Enhancements: effects on the bearer, or on the unit it is in
# ---------------------------------------------------------------------------

# "ADEPTUS ASTARTES model only." / "SWORD BRETHREN SQUAD unit only.": who can take it (BSData checks that)
RESTRICTION = re.compile(r"^[\w' ,/&-]+ (?:model|unit)s? only\.?$", re.I)


def parse_enhancement(text):
    """
    {'buffs', 'skipped', 'conditions'} for an enhancement's text, each buff with a 'scope':
    'unit' when it is for the unit the bearer is in ("This unit...", "the bearer's unit...", "While
    the bearer is leading a unit, ...") and 'bearer' when for the bearer only ("This model's melee
    attacks have [CLEAVE 1]", "The bearer has the Feel No Pain 5+ ability").
    """
    plain = plain_text(text)
    buffs, skipped, conditions = [], [], []
    leading, _ = parse_leader_buffs(re.sub(r'\bthe bearer\b', 'this model', plain, flags=re.I))
    buffs += [dict(b, scope='unit') for b in leading]
    for sentence in sentences(plain):
        if RESTRICTION.match(sentence) or LEADING.search(re.sub(r'\bthe bearer\b', 'this model', sentence, flags=re.I)):
            continue
        if re.search(r'\bfriendly\b.*\bwithin\b', sentence, re.I):  # An aura: other units nearby, not this one
            skipped.append(sentence)
            continue
        scope = 'unit' if re.search(r"\bthis unit\b|\bthe bearer's unit\b|\bthat unit\b", sentence, re.I) else 'bearer'
        reworded = re.sub(r"\b(?:the bearer|this model|this unit)'s\b", "that unit's", sentence, flags=re.I)
        reworded = re.sub(r"\b(?:the bearer|this model|this unit)\b", 'that unit', reworded, flags=re.I)
        found = parse_effects(reworded)
        buffs += [dict(b, scope=scope) for b in found['buffs'] if dict(b, scope=scope) not in buffs]
        skipped += [sentence] if found['skipped'] else []
        conditions += found['conditions']
    return {'buffs': buffs, 'skipped': skipped, 'conditions': conditions}
