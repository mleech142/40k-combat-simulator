#!/usr/bin/env python
"""
40K Combat Simulator - Entry Point

    python run.py            # serves the app with waitress and opens the browser
    python run.py --debug    # Flask's development server with the debugger (never ship this)

The packaged exe (see combat_sim.spec) starts here too. The app only listens on this
computer (127.0.0.1); closing the window stops it.
"""

import argparse
import os
import socket
import sys
import webbrowser
from threading import Timer

# Add src to path (from source; the packaged exe has the package bundled)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from combat_sim import __version__  # noqa: E402
from combat_sim.app import create_app  # noqa: E402

HOST = '127.0.0.1'
PREFERRED_PORT = 5000


def pick_port(host=HOST, preferred=PREFERRED_PORT):
    """The preferred port if it is free, otherwise any free port the system offers."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError('No free port available')


def main(argv=None):
    parser = argparse.ArgumentParser(description='40K Combat Simulator')
    parser.add_argument('--debug', action='store_true', help="Flask's development server with the debugger")
    parser.add_argument('--port', type=int, default=PREFERRED_PORT, help='port to try first')
    parser.add_argument('--no-browser', action='store_true', help="don't open the browser")
    args = parser.parse_args(argv)

    app = create_app()
    port = pick_port(preferred=args.port)
    url = f'http://{HOST}:{port}'

    print('\n' + '=' * 60)
    print(f'40K Combat Simulator v{__version__}')
    print('=' * 60)
    print(f"\nData file: {app.config['DATA_JSON']}")
    status = app.config.get('BSDATA_STATUS') or {}
    print(f"BSData:    {app.config['BSDATA_DIR']}" +
          ('' if status.get('loaded') else '  (not loaded yet: open Setup in the app)'))
    print(f'Open in browser: {url}')
    print('\nClose this window (or press Ctrl+C) to stop.\n')

    if not args.no_browser:
        Timer(1.0, webbrowser.open, args=(url,)).start()

    if args.debug:
        app.run(host=HOST, port=port, debug=True, use_reloader=False)
    else:
        from waitress import serve
        serve(app, host=HOST, port=port, threads=8)
    return 0


if __name__ == '__main__':
    sys.exit(main())
