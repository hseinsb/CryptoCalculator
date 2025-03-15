from http.server import BaseHTTPRequestHandler
from app import app
from werkzeug.serving import WSGIRequestHandler
from io import BytesIO
import sys

class VercelRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.run_wsgi()

    def do_POST(self):
        self.run_wsgi()

    def run_wsgi(self):
        environ = {
            'wsgi.input': BytesIO(self.rfile.read(int(self.headers.get('content-length', 0)))),
            'wsgi.errors': sys.stderr,
            'wsgi.version': (1, 0),
            'wsgi.multithread': False,
            'wsgi.multiprocess': False,
            'wsgi.run_once': False,
            'wsgi.url_scheme': 'https',
            'SERVER_SOFTWARE': 'Vercel',
            'REQUEST_METHOD': self.command,
            'PATH_INFO': self.path,
            'QUERY_STRING': '',
            'CONTENT_TYPE': self.headers.get('content-type', ''),
            'CONTENT_LENGTH': self.headers.get('content-length', ''),
            'REMOTE_ADDR': self.client_address[0],
            'SERVER_NAME': 'vercel',
            'SERVER_PORT': '443',
            'SERVER_PROTOCOL': self.request_version
        }

        for header in self.headers:
            key = header.upper().replace('-', '_')
            if key not in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
                environ['HTTP_' + key] = self.headers[header]

        result = []
        def start_response(status, headers):
            self.send_response(int(status.split()[0]))
            for header, value in headers:
                self.send_header(header, value)
            self.end_headers()
            return result.append

        body = app(environ, start_response)
        try:
            for data in body:
                if data:
                    result.append(data)
            self.wfile.write(b''.join(result))
        finally:
            if hasattr(body, 'close'):
                body.close()

def handler(event, context):
    return VercelRequestHandler() 