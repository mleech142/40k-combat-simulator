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

# Unit data comes from BSData: the catalogue .json files go in data/bsdata
if ! ls data/bsdata/*.json >/dev/null 2>&1; then
    echo
    echo "No BSData catalogues in data/bsdata - download the .json files from"
    echo "https://github.com/BSData/wh40k-11e into that folder to get factions and units."
fi

# Start the application
echo
echo "Starting 40K Combat Simulator..."
echo
python run.py
