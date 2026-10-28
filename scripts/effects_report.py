"""
Which rule text the app can't read yet, for the next effects batch (docs/play-mode-design.md, "Effects batches").

Runs the stratagems and rules of the factions in your saved armies (and the core stratagems) through the rule-text
reader (rules_text.parse_effects) and ranks the sentences it skipped, grouped into patterns (numbers and KEYWORDS
replaced), most common first, with which armies' factions use each.

    python scripts/effects_report.py --db <a COPY of combat_sim.db> [--bsdata DIR] [--wahapedia DIR] [--top 40]

The app runs on scratch copies: the database you name is copied first and the app data JSON is a temporary one,
so nothing of yours is written.
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))


def pattern(sentence):
    """A skipped sentence as a pattern: numbers become N, words in capitals (keywords) become KW."""
    text = re.sub(r'\*\*|\^\^', '', sentence)
    text = re.sub(r'\b[A-Z][A-Z\'-]{2,}(?:\s+[A-Z][A-Z\'-]{2,})*\b', 'KW', text)
    text = re.sub(r'\d+', 'N', text)
    return re.sub(r'\s+', ' ', text).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--db', required=True, help='a combat_sim.db with your saved armies (it is copied first)')
    parser.add_argument('--bsdata', default=str(ROOT / 'data' / 'bsdata'))
    parser.add_argument('--wahapedia', default=None, help="Wahapedia's export folder (default: next to --db)")
    parser.add_argument('--top', type=int, default=40)
    args = parser.parse_args(argv)

    scratch = Path(tempfile.mkdtemp(prefix='effects-report-'))
    source = Path(args.db)
    shutil.copy(source, scratch / 'combat_sim.db')
    for name in ('bsdata_cache.json',):  # Saves re-importing BSData, if it's there
        if (source.parent / name).exists():
            shutil.copy(source.parent / name, scratch / name)
    shutil.copy(ROOT / 'data' / 'app_data.json', scratch / 'app_data.json')
    os.environ.update(DATA_JSON=str(scratch / 'app_data.json'), DATABASE_URL=f'sqlite:///{(scratch / "combat_sim.db").as_posix()}',
                      BSDATA_DIR=args.bsdata, WAHAPEDIA_DIR=args.wahapedia or str(source.parent / 'wahapedia'))

    from combat_sim.app import create_app
    from combat_sim.models import Faction, FactionRule, SavedArmy, Stratagem
    from combat_sim.rules_text import parse_effects

    app = create_app()
    with app.app_context():
        armies = SavedArmy.query.all()
        factions = {a.faction_id: (_faction_name(a.faction_id))
                    for a in armies}
        records = [(r, factions[r.faction_id]) for model in (Stratagem, FactionRule)
                   for r in model.query.filter(model.faction_id.in_(list(factions))) if not r.superseded]
        records += [(r, 'Core') for r in Stratagem.query.filter(Stratagem.faction_id.is_(None)) if not r.superseded]
        groups = defaultdict(lambda: {'count': 0, 'factions': set(), 'example': None, 'rules': set()})
        read = unread = 0
        for record, faction in records:
            text = record.text or record.notes or ''
            if not text:
                continue
            found = parse_effects(text if isinstance(record, FactionRule) else _effect(text))
            read += bool(found['buffs'])
            unread += not found['buffs']
            for sentence in found['skipped']:
                g = groups[pattern(sentence)]
                g['count'] += 1
                g['factions'].add(faction)
                g['rules'].add(record.name)
                g['example'] = g['example'] or sentence
        print(f'{len(armies)} saved armies; factions: {", ".join(sorted(set(factions.values())))}')
        print(f'{len(records)} stratagems and rules: {read} with an effect read, {unread} with none\n')
        ranked = sorted(groups.items(), key=lambda kv: (-kv[1]['count'], kv[0]))
        for n, (key, g) in enumerate(ranked[:args.top], 1):
            print(f'{n:>3}. x{g["count"]} [{", ".join(sorted(g["factions"]))}] {key}')
            print(f'      e.g. {g["example"][:220]}')
            print(f'      in: {", ".join(sorted(g["rules"]))[:220]}')
    shutil.rmtree(scratch, ignore_errors=True)


def _faction_name(faction_id):
    from combat_sim.models import Faction, db
    faction = db.session.get(Faction, faction_id)
    return faction.name if faction else str(faction_id)


def _effect(text):
    from combat_sim.wahapedia import effect_part
    return effect_part(text)


if __name__ == '__main__':
    main()
