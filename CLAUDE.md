# 40K Combat Simulator

Flask app in `src/combat_sim/`. Entry point: `python run.py` (port 5000).

## Database

SQLite at `data/combat_sim.db`. Models in `src/combat_sim/models.py`.

**Schema changes**: No migrations configured. After adding columns to models, manually ALTER TABLE:
```python
import sqlite3
conn = sqlite3.connect('data/combat_sim.db')
conn.execute('ALTER TABLE tablename ADD COLUMN colname TYPE')
conn.commit()
```

**Foreign keys**: Weapons reference units via `unit_id`. If IDs mismatch (weapons point to nonexistent units), the matrix simulation crashes.

## Excel Source

`40KCombatSimulator.xlsx` layout (for migration scripts):
- Units: B-H (row 3+)
- Ranged weapons: I-S
- Melee weapons: V-AB
- Rules reference: AE-AH

## Routes

- `/` - Single combat simulator
- `/matchups` - N×M matrix comparison
- `/scenarios` - Saved scenarios
- `/data` - CRUD for factions/units/weapons

API routes in `app.py` under `register_routes()`.
