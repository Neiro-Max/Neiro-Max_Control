import os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

port = int(os.environ.get('PORT', '8000'))
ThreadingHTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler).serve_forever()
