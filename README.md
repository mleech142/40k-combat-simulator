# 40K Combat Simulator

A local web application for simulating Warhammer 40,000 11th Edition combat. Calculate expected damage, compare weapon effectiveness, and analyze army matchups with both analytical probability calculations and Monte Carlo simulations.

## Features

- **Single Combat Simulation**: Calculate damage output for any attacker vs defender matchup
- **Matrix Comparison (Matchups)**: Compare N attackers vs M defenders in a heat map grid
- **Data Management**: Full CRUD for factions, units, and weapons
- **Scenario Saving**: Save and compare simulation results
- **Analytical & Monte Carlo**: Choose between fast probability calculations or detailed distributions
- **11th Edition Rules**: Supports Lethal Hits, Sustained Hits, Devastating Wounds, Anti-X, Blast, Melta, and more

## Installation

### Prerequisites
- Python 3.8+
- pip

### Setup

1. Clone the repository:
```bash
git clone https://github.com/YOUR_USERNAME/40k-combat-simulator.git
cd 40k-combat-simulator
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the application:
```bash
python run.py
```

4. Open in browser: http://localhost:5000

### Quick Start (Windows)
Double-click `launcher.bat`

### Quick Start (Linux/Mac)
```bash
chmod +x launcher.sh
./launcher.sh
```

## Usage

### Simulator Page
1. Search and select an attacker unit and weapon
2. Search and select a defender unit
3. Set model count and battle conditions
4. Click "Run Simulation" to see results

### Matchups Page
1. Select weapon type filter (Shooting/Both/Melee)
2. Search and add attacker units (adds all their weapons)
3. Search and add defender units with model counts
4. Set battle conditions
5. Click "Run Matrix" to generate the comparison grid
6. Click any cell for detailed Monte Carlo breakdown

### Data Management
- Add/edit/delete factions, units, and weapons
- Import/export data as JSON for backup or sharing

## Project Structure

```
40k-combat-simulator/
├── src/
│   └── combat_sim/
│       ├── app.py           # Flask application and routes
│       ├── models.py        # SQLAlchemy database models
│       ├── simulation.py    # Combat calculation engine
│       ├── templates/       # Jinja2 HTML templates
│       └── static/          # CSS and JavaScript
├── data/                    # SQLite database location
├── scripts/                 # Migration and utility scripts
├── run.py                   # Application entry point
├── requirements.txt         # Python dependencies
└── README.md
```

## Supported Abilities

- **Lethal Hits**: Critical hits (6s) auto-wound
- **Sustained Hits X**: Critical hits generate X extra hits
- **Devastating Wounds**: Critical wounds deal mortal wounds
- **Anti-X Y+**: +1 to wound vs keyword X, or auto-wound on Y+
- **Blast**: +1 attack per 5 models in target unit
- **Melta X**: +X damage at half range
- **Twin-linked**: Re-roll wounds
- **Lance**: +1 to wound on charge
- **Heavy**: +1 to hit when stationary
- **Assault**: Can advance and shoot
- **Rapid Fire X**: +X attacks at half range
- **Torrent**: Auto-hit
- **Ignores Cover**: Target doesn't benefit from cover
- **Feel No Pain X+**: Ignore wounds on X+

## Tech Stack

- **Backend**: Flask, SQLAlchemy, SQLite
- **Frontend**: Vanilla JavaScript, Plotly.js for charts
- **Styling**: Custom CSS with dark theme

## License

MIT License - feel free to use and modify for your own gaming needs.

## Contributing

Contributions welcome! Please feel free to submit issues and pull requests.

---

*This is a fan project and is not affiliated with Games Workshop. Warhammer 40,000 is a trademark of Games Workshop Ltd.*
