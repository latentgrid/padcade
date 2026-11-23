"""Runs the board on its hardware; with --simulator, on any computer, in the browser."""
import argparse
import getpass
import os
import signal
import sys
from pathlib import Path

from . import NAME, SLUG, config
from .board import Board
from .https import certificate
from .web import BrowserSound, Runner, serve


def default_home():
    """Use the existing installation when upgrading; new installs keep data outside the source checkout."""
    legacy = Path.home() / SLUG
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / SLUG
    for name in ('config.toml', 'config.broken', 'owner.toml'):
        for folder in (data, legacy):
            if (folder / name).is_file():
                return folder
    return data


def main():
    parser = argparse.ArgumentParser(prog=f'python3 -m {__package__}', description=f'{NAME}. {__doc__}')
    parser.add_argument('--simulator', action='store_true', help='no hardware: the management page is the board')
    parser.add_argument('--home', type=Path, default=default_home(),
                        help='the folder with settings, plugins and their data (default: %(default)s)')
    parser.add_argument('--require-owner', action='store_true', help='require local pairing even on a loopback simulator, e.g. behind an HTTPS proxy')
    access = parser.add_mutually_exclusive_group()
    access.add_argument('--set-admin-password', action='store_true', help='set a management password and leave the LAN page open for playing')
    access.add_argument('--set-password', action='store_true', help='set the owner password locally and exit')
    parser.add_argument('--port', type=int, default=8080, help='the management page\'s port (default: 8080)')
    parser.add_argument('--https-port', type=int, metavar='PORT',
                        help='also answer securely on this port, with a certificate of the board\'s own (default: 443 on the board '
                             'when the page is on port 80; not in the simulator; 0 for none)')
    parser.add_argument('--host', help='the address it listens on (default: this computer only for the simulator, '
                                       'the whole network on the board)')
    args = parser.parse_args()
    if args.set_password or args.set_admin_password:
        if os.environ.get('SUDO_USER') and os.geteuid() == 0:
            parser.exit(1, 'Set the password as the service user so that the board can read its owner file.\n')
        password = getpass.getpass('New owner password: ')
        if not password or password != getpass.getpass('Confirm owner password: '):
            parser.exit(1, 'Enter the same nonempty password twice.\n')
        try:
            config.set_password(args.home, password, admin_only=args.set_admin_password)
        except OSError as error:
            parser.exit(1, f'Could not set the owner password in {args.home}: {error}\n')
        print(f'Owner password set in {args.home}.')
        return
    if args.simulator:
        board = Board(args.home, sound=BrowserSound())
        board.parts = {'Launchpad Pro': 'simulated on this page', 'Speaker': 'this browser'}
        runner = Runner(board)
    else:
        from . import device
        board = Board(args.home, sound=device.Speaker())
        board.parts = {'Launchpad Pro': 'waiting for USB', 'Speaker': 'the case\'s amplifier'}
        runner = Runner(board, simulator=False)
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(stop_signal, lambda *_: runner.stop())
    # A browser lends its microphone only to a page opened securely: the board also answers that way, for recording clips.
    secure_port = args.https_port if args.https_port is not None else 443 if not args.simulator and args.port == 80 else 0
    host = args.host or ('127.0.0.1' if args.simulator else '0.0.0.0')
    server = serve(runner, host, args.port, require_owner=args.require_owner,
                   secure_port=secure_port or None, certificate=secure_port and certificate(args.home))
    if secure_port and not server.secure:
        print('No secure address (no certificate could be made, or its port is taken): clips cannot be recorded '
              'from another device\'s browser.', file=sys.stderr, flush=True)
    where = "localhost" if args.simulator else server.server_address[0]
    secure = f' and https://{where}:{server.secure.server_address[1]}/' if server.secure else ''
    print(f'{NAME} ({args.home}): http://{where}:{server.server_address[1]}/{secure}', flush=True)
    try:
        runner.run() if args.simulator else device.run(board, runner)
    finally:
        if server.secure:
            server.secure.shutdown()
            server.secure.server_close()
        server.shutdown()
        for run in list(board.running.values()):
            board.discard(run)
        server.server_close()


if __name__ == '__main__':
    main()
