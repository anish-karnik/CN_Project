import socket
import threading
import select
import time, re
import logging

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

    def delete(self, key):
        del self.cache[key]
        del self.frequency[key]

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


	def header(self):
		data_split = self.data_split[1:]
		Response_Header = dict()
		for line in data_split:
			if not line:
				continue
			broken_line = line.decode().split(":")
			Response_Header[broken_line[0].lower()] = ":".join(broken_line[1:])

		return Response_Header


get_cache = TinyLFUCache(100)
head_cache = TinyLFUCache(100)
# post_cache = TinyLFUCache(100)
turn = 0


class DeletegetCacheEntry(threading.Thread):
    def __init__(self, entry, timeout):
        super().__init__()
        self.entry = entry
        self.timeout = timeout

    def run(self):
        time.sleep(self.timeout)
        get_cache.delete(self.entry)

class ConnectionHandle(threading.Thread):
	def __init__(self, connection, client_addr):
		super().__init__()
		self.client_conn = connection
		self.client_addr = client_addr

	def run(self):
		global turn
		raw_request = self.client_conn.recv(max_size)
		if not raw_request:
			return

		request = Request(raw_request)

		if request.protocol == Protocol.http20:
			self.client_conn.send(Error.status_505.encode('utf-8'))
			self.client_conn.close()
			return
		if str(request.host) in Blocked_List:
			self.client_conn.send(StaticResponse.block_response)
			self.client_conn.close()
			logg.info(f"{request.method:<8} {request.path} {request.protocol} BLOCKED")
			return

		request_headers = request.header()

		if ('accept-language' in request_headers):
			decoded_raw_request=raw_request.decode('utf-8')

			# pattern to match accept-language and its value
			pattern = re.compile(r'accept-language:[^\r\n]*\r\n', flags=re.IGNORECASE)

			# removing the matched pattern
			modified_raw_request_str = re.sub(pattern, '', decoded_raw_request)

			# converting the modified string back to bytes
			modified_raw_request = modified_raw_request_str.encode('utf-8')

			raw_request=modified_raw_request

		language = request_headers.get('accept-language', ' en')
		language = ' en' if language.startswith(' en') else language
		print(language, '?????????????????????????????>>>>>>>>>>>>>>>>>>>>')
		#handling cache-control request header field
		cached_response = True
		if 'cache-control' in request_headers:
			cache_control_parameters = request_headers['cache-control'].split(',')
			for parameter in cache_control_parameters:
				print(parameter)
				if parameter.startswith('max-age'):
					max_age = int(parameter.split('=')[1])
				elif parameter == ' no-cache':
					cached_response = False

		if request.method == Method.get and cached_response:
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

		if request.method == Method.head and cached_response:
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

		if request.path == "/" and language == ' en':
			raw_request = change_path(raw_request, request.path, 'index.html',  function = "suffix")
			if turn == 0:
				self.server_conn1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

				try:
					self.server_conn1.connect(('127.0.0.1', 81))
				except:
					self.client_conn.send(Error.status_503.encode('utf-8'))
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
					self.client_conn.send(Error.status_503.encode('utf-8'))
					self.client_conn.close()
					return
				turn = 0
				self.server_conn2.send(raw_request)
				self.server_conn2.settimeout(5)
				data = self.server_conn2.recv(max_size)
				self.server_conn2.close()
			if not data:
				return



		elif language == ' en' and (request.path == "/dataA" or request.path == "/dataA/"):
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

		elif (request.path == "/dataB" or request.path == "/dataB/") and language == ' en':
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

		elif (not language == ' en') and (request.path == "/" or request.path == "/dataA" or request.path == "/dataB" or request.path == "/dataA/" or request.path == "/dataB/"):
			self.server_conn3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			try:
				self.server_conn3.connect(('127.0.0.1', 83))
			except:
				self.client_conn.send(Error.status_503)
				self.client_conn.close()
				return
			self.server_conn3.send(raw_request)
			self.server_conn3.settimeout(5)
			data = self.server_conn3.recv(max_size)
			self.server_conn3.close()
			if not data:
				return

		#else send error message to client and close connection. do something here if needed
		else:
			self.client_conn.send('not a valid path'.encode('utf-8'))
			self.client_conn.close()
			return

		response = Response(data)
		response_headers = response.header()
		store_response = True
		max_age = 0
		if 'cache-control' in response_headers:
			cache_control_parameters = response_headers['cache-control'].split(',')
			for parameter in cache_control_parameters:
				if parameter.startswith(' max-age'):
					max_age = int(parameter.split('=')[1])
				elif parameter == ' no-store':
					store_response = False
		if request.method == Method.get and store_response:
			get_cache.put(request.path, data)
		if request.method == Method.head and store_response:
			head_cache.put(request.path, data)
		if max_age > 0:
			t = DeletegetCacheEntry(request.path, max_age)
			t.start()
		self.client_conn.send(data)
		self.client_conn.close()
		logg.info(f"{request.method:<8} {request.path} {request.protocol} SERVED FROM SERVER")
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
