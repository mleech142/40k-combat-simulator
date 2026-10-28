#!/usr/bin/env python
"""
40K Combat Simulator - Entry Point

    python run.py            # serves the app with waitress and opens the browser
    python run.py --debug    # Flask's development server with the debugger (never ship this)
    python run.py --hosted   # a self-hosted copy for other people's browsers (docs/self-hosting.md)

The packaged exe (see combat_sim.spec) starts here too. The app only listens on this
computer (127.0.0.1); closing the window stops it. Hosted mode (also COMBAT_SIM_HOSTED=1, as in
the Docker image) listens on every network address (--host to change it) and its exact port,
opens no browser and refuses --debug.
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
from combat_sim.paths import HOSTED_ENV, is_hosted  # noqa: E402
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
    parser.add_argument('--hosted', action='store_true', help="a self-hosted copy for other people's browsers")
    parser.add_argument('--host', help='address to listen on (default 127.0.0.1, hosted 0.0.0.0)')
    args = parser.parse_args(argv)

    if args.hosted:
        os.environ[HOSTED_ENV] = '1'  # create_app() reads it
    hosted = is_hosted()
    if hosted and args.debug:
        parser.error("--debug can't be used in hosted mode: its debugger would let anyone run code")
    host = args.host or ('0.0.0.0' if hosted else HOST)

    app = create_app()
    # Hosted: the exact port (Docker maps it), never another one
    port = args.port if hosted else pick_port(host, preferred=args.port)
    url = f'http://{"127.0.0.1" if host == "0.0.0.0" else host}:{port}'

    print('\n' + '=' * 60)
    print(f'40K Combat Simulator v{__version__}')
    print('=' * 60)
    print(f"\nData file: {app.config['DATA_JSON']}")
    from combat_sim import reference
    loaded = reference.current(app)
    status = loaded.bsdata_status
    print(f"BSData:    {loaded.bsdata_dir}" +
          ('' if status.get('loaded') else '  (not loaded yet: open Setup in the app)'))
    if app.config.get('SETUP_CODE'):
        print(f"\nSetup code: {app.config['SETUP_CODE']}  (the Welcome page asks for it to create the host)")
    print(f'Open in browser: {url}' + ("  (hosted mode: other computers use this one's address)" if hosted else ''))
    print('\nClose this window (or press Ctrl+C) to stop.\n')

    if not (args.no_browser or hosted):
        Timer(1.0, webbrowser.open, args=(url,)).start()

    if args.debug:
        app.run(host=host, port=port, debug=True, use_reloader=False)
    else:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    return 0


if __name__ == '__main__':
    sys.exit(main())
