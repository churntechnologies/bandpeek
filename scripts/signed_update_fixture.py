#!/usr/bin/env python3
"""Loopback-only HTTPS fixtures. Uses owner-installed normal macOS TLS trust.

Contains no updater signing material. Run only for disposable validation apps.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / '.validation/signed-updater'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path.endswith('/latest.json') or path == '/latest.json':
            case = path.strip('/').split('/')[0]
            archive, signature = 'BandPeek.app.tar.gz', 'BandPeek.app.tar.gz.sig'
            if case == 'bad-signature':
                signature = 'corrupted.sig'
            elif case == 'bad-archive':
                archive = 'corrupted.app.tar.gz'
            elif case == 'install-failure':
                archive = 'install-failure.app.tar.gz'
                signature = archive + '.sig'
            version = '0.1.0-beta.0' if case in ('no-update', 'latest.json') else '0.1.0-beta.1'
            if case == 'sqlite-flush' and not (FIXTURES / 'sqlite-flush-ready').exists():
                version = '0.1.0-beta.0'
            body = json.dumps({
                'version': version,
                'notes': 'LOCAL-VALIDATION-ONLY matching-key exercise',
                'pub_date': '2026-09-29T00:00:00Z',
                'platforms': {'darwin-aarch64': {
                    'url': f'https://localhost:{self.server.server_port}/assets/{archive}',
                    'signature': (FIXTURES / signature).read_text().strip(),
                }},
            }).encode()
            self.respond(body, 'application/json')
        elif path.startswith('/assets/'):
            name = path.removeprefix('/assets/')
            if name not in ('BandPeek.app.tar.gz', 'corrupted.app.tar.gz', 'install-failure.app.tar.gz'):
                self.send_error(404)
                return
            self.respond((FIXTURES / name).read_bytes(), 'application/gzip')
        elif path == '/traffic.bin':
            self.respond(b'x' * (16 * 1024 * 1024), 'application/octet-stream')
        else:
            self.send_error(404)

    def respond(self, body, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18443)
    args = parser.parse_args()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(FIXTURES / 'tls/localhost.pem', FIXTURES / 'tls/localhost-key.pem')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    context_socket = context.wrap_socket(server.socket, server_side=True)
    server.socket = context_socket
    print(f'Fixture bound only to 127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
