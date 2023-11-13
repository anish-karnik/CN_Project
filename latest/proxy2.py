import socket
import threading
import select
import time, re
import logging

from cryptography.fernet import Fernet

encryption_key = Fernet.generate_key()
fernet = Fernet(encryption_key)

class TinyLFUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.frequency = {}
        self.lock = threading.Lock()
        self.multiplier = 1
        self.alpha = 0.25

    def get(self, key):
        with self.lock:
            self.multiplier = (1 + self.alpha) * self.multiplier
            if key in self.cache:
                self.frequency[key] += self.multiplier
                return self.cache[key]
        return None

    def put(self, key, value):
        with self.lock:
            self.multiplier = (1 + self.alpha) * self.multiplier
            if self.capacity <= 0:
                return

            if key in self.cache:
                self.cache[key] = value
                self.frequency[key] += self.multiplier
            else:
                if len(self.cache) >= self.capacity:
                    self.evict()
                self.cache[key] = value
                self.frequency[key] = self.multiplier

    def evict(self):
        min_key = min(self.frequency, key=lambda k: self.frequency[k])
        del self.cache[min_key]
        del self.frequency[min_key]

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(process)s] [%(levelname)s] %(message)s")
logg = logging.getLogger(__name__)

Queue_Total = 50
Total_Threads = 200
Blocked_List = ['www.netflix.com']
max_size = 16 * 1024

class StaticResponse:
    connection_established = b"HTTP/1.1 200 Connection Established\r\n\r\n"
    block_response = b'HTTP/1.1 200 OK\r\nPragma: no-cache\r\nCache-Control: no-cache\r\nContent-Type: text/html\r\nDate: Sat, 15 Feb 2020 07:04:42 GMT\r\nConnection: close\r\n\r\n<html><head><title>ISP ERROR</title></head><body><p style="text-align: center;">&nbsp;</p><p style="text-align: center;">&nbsp;</p><p style="text-align: center;">&nbsp;</p><p style="text-align: center;">&nbsp;</p><p style="text-align: center;">&nbsp;</p><p style="text-align: center;">&nbsp;</p><p style="text-align: center;"><span><strong>*YOU ARE NOT AUTHORIZED TO ACCESS THIS WEB PAGE | YOUR PROXY SERVER HAS BLOCKED THIS DOMAIN</strong></span></p><p style="text-align: center;"><span><strong>**CONTACT YOUR PROXY ADMINISTRATOR*</strong></span></p></body></html>'

class Error:
    status_503= "Service Unavailable"
    status_505 = "HTTP Version Not Supported"

for key in filter(lambda x: x.startswith("STATUS"), dir(Error)):
    _, code = key.split("_")
    value = getattr(Error, f"STATUS_{code}")
    setattr(Error, f"STATUS_{code}", f"HTTP/1.1 {code} {value}\r\n\r\n".encode())

class Method:
    get = "GET"
    put = "PUT"
    head = "HEAD"
    post = "POST"
    patch = "PATCH"
    delete = "DELETE"
    options = "OPTIONS"
    connect = "CONNECT"

class Protocol:
    http10 = "HTTP/1.0"
    http11 = "HTTP/1.1"
    http20 = "HTTP/2.0"

#function to change the request path to the correct path
def change_path(raw_request, curr_path, to_add, function):
    if function == "suffix":
        raw_request = re.sub(b' .*? ', (' ' + curr_path + to_add + ' ').encode('utf-8'), raw_request, count = 1)
    elif function == "prefix":
        raw_request = re.sub(b' .*? ', (' ' + to_add + curr_path + ' ').encode('utf-8'), raw_request, count = 1)
    return raw_request

class Request:
    def __init__(self, raw:bytes):
        self.raw = raw
        self.data_split = raw.split(b"\r\n")
        self.log = self.data_split[0].decode()

        self.method, self.path, self.protocol = self.log.split(" ")

        raw_host = re.findall(rb"host: (.*?)\r\n", raw.lower())

        # http protocol 1.1
        if raw_host:
            raw_host = raw_host[0].decode()
            if raw_host.find(":") != -1:
                self.host, self.port = raw_host.split(":")
                self.port = int(self.port)
            else:
                self.host = raw_host

        # http protocol 1.0 and below
        if "://" in self.path:
            Path_List = self.path.split("/")
            if Path_List[0] == "http:":
                self.port = 80
            if Path_List[0] == "https:":
                self.port = 443

            host_n_port = Path_List[2].split(":")
            if len(host_n_port) == 1:
                self.host = host_n_port[0]

            if len(host_n_port) == 2:
                self.host, self.port = host_n_port
                self.port = int(self.port)

            self.path = f"/{'/'.join(Path_List[3:])}"

        elif self.path.find(":") != -1:
            self.host, self.port =  self.path.split(":")
            self.port = int(self.port)


    def header(self):
        data_split = self.data_split[1:]
        Request_Header = dict()
        for line in data_split:
            if not line:
                continue
            broken_line = line.decode().split(":")
            Request_Header[broken_line[0].lower()] = ":".join(broken_line[1:])

        return Request_Header

class Response:
    def __init__(self, raw:bytes):
        self.raw = raw
        self.data_split = raw.split(b"\r\n")
        self.log = self.data_split[0]

        try:
            self.protocol, self.status, self.status_str = self.log.decode().split(" ")
        except Exception as e:
            self.protocol, self.status, self.status_str = ("", "", "")

get_cache = TinyLFUCache(100)
head_cache = TinyLFUCache(100)
# post_cache = TinyLFUCache(100)
turn = 0


class ConnectionHandle(threading.Thread):
    def __init__(self, connection, client_addr):
        super().__init__()
        self.client_conn = connection
        self.client_addr = client_addr
        
    def encrypt_headers(self, headers):
        # Convert headers to bytes
        headers_bytes = headers.encode('utf-8')

        # Encrypt the headers
        encrypted_headers = fernet.encrypt(headers_bytes)
        
        return encrypted_headers
    
    def decrypt_headers(self, encrypted_headers):
        # Decrypt the headers
        decrypted_headers = fernet.decrypt(encrypted_headers)

        # Convert bytes back to string
        headers = decrypted_headers.decode('utf-8')

        return headers

    def run(self):
        global turn
        raw_request = self.client_conn.recv(max_size)
        if not raw_request:
            return

        # Intercept and decrypt the request headers
        decrypted_request = self.decrypt_headers(raw_request)

        request = Request(decrypted_request)

        if request.protocol == Protocol.http20:
            self.client_conn.send(Error.status_505)
            self.client_conn.close()
            return
        if str(request.host) in Blocked_List:
            self.client_conn.send(StaticResponse.block_response)
            self.client_conn.close()
            logg.info(f"{request.method:<8} {request.path} {request.protocol} BLOCKED")
            return

        response_headers = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\nEncryption Performed"
        encrypted_response = self.encrypt_headers(response_headers)

        # Forward the encrypted response to the client
        self.client_conn.send(encrypted_response)

        if request.method == Method.get:
            try:
                if get_cache.get(request.path):
                    print("Present in the CACHE !")
                    cached_response = get_cache.get(request.path)
                    self.client_conn.send(cached_response)
                    self.client_conn.close()
                    logg.info(f"{request.method:<8} {request.path} {request.protocol} SERVED FROM CACHE")
                    return

            except ConnectionAbortedError as e:
                print(f"ConnectionAbortedError: {e}")
                return

        if request.method == Method.head:
            try:
                if head_cache.get(request.path):
                    print("Present in the CACHE !")
                    cached_response = head_cache.get(request.path)
                    self.client_conn.send(cached_response)
                    self.client_conn.close()
                    logg.info(f"{request.method:<8} {request.path} {request.protocol} SERVED FROM CACHE")
                    return

            except ConnectionAbortedError as e:
                print(f"ConnectionAbortedError: {e}")
                return

        print("CACHE MISS !")

        if request.path == "/":
            raw_request = change_path(raw_request, request.path, 'index.html',  function = "suffix")
            if turn == 0:
                self.server_conn1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                try:
                    self.server_conn1.connect(('127.0.0.1', 81))
                except:
                    self.client_conn.send(Error.status_503)
                    self.client_conn.close()
                    return
                turn = 1
                self.server_conn1.send(raw_request)
                self.server_conn1.settimeout(5)
                data = self.server_conn1.recv(max_size)
                self.server_conn1.close()

            elif turn == 1:
                self.server_conn2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                try:
                    self.server_conn2.connect(('127.0.0.1', 82))
                except:
                    self.client_conn.send(Error.status_503)
                    self.client_conn.close()
                    return
                turn = 0
                self.server_conn2.send(raw_request)
                self.server_conn2.settimeout(5)
                data = self.server_conn2.recv(max_size)
                self.server_conn2.close()


            if not data:
                return


            if request.method == Method.get:
                get_cache.put(request.path, data)
            if request.method == Method.head:
                head_cache.put(request.path, data)


            self.client_conn.send(data)
            self.client_conn.close()
            logg.info(f"{request.method:<8} {request.path} {request.protocol} SERVED FROM SERVER")
            return

        elif request.path == "/dataA" or request.path == "/dataA/":
            self.server_conn1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.server_conn1.connect(('127.0.0.1', 81))
            except:
                self.client_conn.send(Error.status_503.encode('utf-8'))
                self.client_conn.close()
                return
            self.server_conn1.send(raw_request)
            self.server_conn1.settimeout(5)
            data = self.server_conn1.recv(max_size)
            self.server_conn1.close()
            if not data:
                return
            if request.method == Method.get:
                get_cache.put(request.path, data)
            if request.method == Method.head:
                head_cache.put(request.path, data)
            self.client_conn.send(data)
            self.client_conn.close()
            logg.info(f"{request.method:<8} {request.path} {request.protocol} SERVED FROM SERVER")
            return

        elif request.path == "/dataB" or request.path == "/dataB/":
            self.server_conn2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.server_conn2.connect(('127.0.0.1', 82))
            except:
                self.client_conn.send(Error.status_503)
                self.client_conn.close()
                return
            self.server_conn2.send(raw_request)
            self.server_conn2.settimeout(5)
            data = self.server_conn2.recv(max_size)
            self.server_conn2.close()
            if not data:
                return
            if request.method == Method.get:
                get_cache.put(request.path, data)
            if request.method == Method.head:
                head_cache.put(request.path, data)
            self.client_conn.send(data)
            self.client_conn.close()
            logg.info(f"{request.method:<8} {request.path} {request.protocol} SERVED FROM SERVER")
            return

        #else send error message to client and close connection. do something here if needed
        else:
            self.client_conn.send('not a valid path'.encode('utf-8'))
            self.client_conn.close()
            return



    def __del__(self):
        if hasattr(self, "server_conn"):
            self.server_conn.close()
        self.client_conn.close()


class Proxy_Server:
    def __init__(self, host:str, port:int):
        logg.info(f"Proxy server starting")
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((host, port))
        self.server_socket.listen(Queue_Total)
        logg.info(f"Listening at: http://{host}:{port}")

    def thread_check(self):
        while True:
            if threading.active_count() >= Total_Threads:
                time.sleep(1)
            else:
                return

    def start(self):
        while True:
            conn, client_addr = self.server_socket.accept()
            self.thread_check()
            s = ConnectionHandle(conn, client_addr)
            s.start()

    def __del__(self):
        self.server_socket.close()


if __name__ == '__main__':
    ser = Proxy_Server(host="localhost", port=8000)
    ser.start()