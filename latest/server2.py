from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Send a response header
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        # Send the response content
        if self.path == '/index.html':
            response_message = 'Welcome!\nThis was handled by server2.\nWe have two types of data: dataA and dataB.\nTo access dataA go to http://localhost:8000/dataA and to access dataB go to http://localhost:8000/dataB\n'
            self.wfile.write(response_message.encode('utf-8'))

        elif self.path.startswith('/dataB'):
            response_message = 'This is dataB.\nThis was handled by server2.\n'
            self.wfile.write(response_message.encode('utf-8'))

        else:
            response_message = 'Error: This is not a valid path.\nThis was handled by server1.\n'
            self.wfile.write(response_message.encode('utf-8'))


    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

def run(server_class=HTTPServer, handler_class=SimpleHandler):
    server_address = ('127.0.0.1', 82)
    httpd = server_class(server_address, handler_class)
    httpd.serve_forever()

if __name__ == '__main__':
    run()
