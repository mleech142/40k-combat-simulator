#!/usr/bin/env python
"""
40K Combat Simulator - Entry Point

Run this script to start the application:
    python run.py

Then open http://localhost:5000 in your browser.
"""

import os
import sys
import webbrowser
from threading import Timer

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from combat_sim.app import create_app

def open_browser():
    """Open the browser after a short delay."""
    webbrowser.open('http://localhost:5000')

if __name__ == '__main__':
    # Ensure data directory exists
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(data_dir, exist_ok=True)

    app = create_app()

    print("\n" + "="*60)
    print("40K Combat Simulator")
    print("="*60)
    print("\nStarting server...")
    print("Open in browser: http://localhost:5000")
    print("\nPress Ctrl+C to stop.\n")

    # Open browser after 1.5 seconds
    Timer(1.5, open_browser).start()

    # Run the app
    app.run(debug=True, port=5000, use_reloader=False)
