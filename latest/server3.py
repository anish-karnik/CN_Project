from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Send a response header
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        # Send the response content
        self.wfile.write(b'Hi, this is server 3!')

def run(server_class=HTTPServer, handler_class=SimpleHandler):
    server_address = ('127.0.0.1', 83)
    httpd = server_class(server_address, handler_class)
    httpd.serve_forever()

if __name__ == '__main__':
    run()