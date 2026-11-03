import argparse
import functools
import socket
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description='Menu game recorder and fixture tools')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('gui', help='Open Windows recording controls')
    commands.add_parser('windows', help='List recordable Windows handles')
    rec = commands.add_parser('record', help='Record a foreground game client area')
    rec.add_argument('--hwnd', type=int, required=True)
    rec.add_argument('--output', required=True)
    rec.add_argument('--goal', required=True)
    rec.add_argument('--completion', required=True)
    rec.add_argument('--fps', type=float, default=2)
    rec.add_argument('--duration', type=float, default=300)
    serve = commands.add_parser('demo', help='Serve the synthetic menu fixture on localhost')
    serve.add_argument('--port', type=int, default=8765)
    check = commands.add_parser('validate', help='Check a scenario and its evidence')
    check.add_argument('scenario')
    build = commands.add_parser('build-graph', help='Build a pixel workflow from completed recordings')
    build.add_argument('recordings', nargs='+')
    build.add_argument('--output', required=True)
    build.add_argument('--success-last', action='store_true', help='Explicitly label each final screen as success')
    args = parser.parse_args()
    if args.command == 'gui':
        from .gui import run
        run()
    elif args.command in ('windows', 'record'):
        from . import win32
        win32.dpi_aware()
        if args.command == 'windows':
            for hwnd, title in win32.windows(): print(f'{hwnd}\t{title}')
        else:
            from .recorder import record
            record(args.hwnd, args.output, args.goal, args.completion, args.fps, args.duration, status=print)
    elif args.command == 'demo':
        directory = Path(__file__).resolve().parent.parent / 'examples' / 'menu_demo'
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
        print(f'Open http://127.0.0.1:{args.port} (Ctrl+C to stop)', flush=True)
        class LocalServer(ThreadingHTTPServer):
            allow_reuse_address = False
            def server_bind(self):
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()
        with LocalServer(('127.0.0.1', args.port), handler) as server:
            try: server.serve_forever()
            except KeyboardInterrupt: pass
    elif args.command == 'build-graph':
        from .builder import build_graph
        graph = build_graph(args.recordings, args.output, args.success_last)
        print(f'Created {len(graph["nodes"])} nodes and {len(graph["edges"])} edges')
    else:
        from .scenario import validate
        result = validate(Path(args.scenario))
        print(result)
        if result: raise SystemExit(1)

if __name__ == '__main__': main()
