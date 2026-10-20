#!/bin/bash

echo "============================================"
echo "   40K Combat Simulator - Launcher"
echo "============================================"
echo

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo "Please install Python 3.10+ using your package manager."
    exit 1
fi

# The virtual environment needs the venv module (Debian/Ubuntu: sudo apt install python3-venv)
if ! python3 -c "import venv, ensurepip" &> /dev/null; then
    echo "ERROR: Python's venv module is missing."
    echo "On Debian/Ubuntu: sudo apt install python3-venv"
    exit 1
fi

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if venv exists, create if not
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to create virtual environment."
        exit 1
    fi
fi

# Activate venv
source venv/bin/activate

# Install/upgrade dependencies
echo "Checking dependencies..."
pip install -q -r requirements.txt

# The game data (BSData units and Wahapedia rules) is downloaded from the app's Setup page
if ! ls data/bsdata/*.json >/dev/null 2>&1 || [ ! -f data/wahapedia/Stratagems.csv ]; then
    echo
    echo "First run? The app opens its Setup page: press both Download buttons there"
    echo "(units and points, then rules and stratagems)."
fi

# Start the application
echo
echo "Starting 40K Combat Simulator..."
echo
python run.py
