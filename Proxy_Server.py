import socket
import threading
import select
import time, re
import logging

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

cache = {}

class ConnectionHandle(threading.Thread):
	def __init__(self, connection, client_addr):
		super().__init__()
		self.client_conn = connection
		self.client_addr = client_addr
		print(cache.keys())

	def run(self):
		raw_request = self.client_conn.recv(max_size)
		if not raw_request:
			return

		request = Request(raw_request)

		if request.protocol == Protocol.http20:
			self.client_conn.send(Error.status_505)
			self.client_conn.close()
			return
		print(type(request.host))
		print(Blocked_List)
		print(len(Blocked_List[0]))
		print(len(request.host))
		if str(request.host) in Blocked_List:
			print("Hello")
			self.client_conn.send(StaticResponse.block_response)
			self.client_conn.close()
			logg.info(f"{request.method:<8} {request.path} {request.protocol} BLOCKED")
			return

		print(request.path)
		try:
			if request.path in cache:
				print("Present in the CACHE !")
				cached_response = cache[request.path]
				#self.client_conn.send(cached_response)
				for i in cached_response:
					self.client_conn.send(i)
				self.client_conn.close()
				logg.info(f"{request.method:<8} {request.path} {request.protocol} SERVED FROM CACHE")
			
		except ConnectionAbortedError as e:
			print(f"ConnectionAbortedError: {e}")
			return

		self.server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

		try:
			self.server_conn.connect((request.host, request.port))
		except:
			self.client_conn.send(Error.STATUS_503)
			self.client_conn.close()
			return

		if request.method == Method.connect:
			self.client_conn.send(StaticResponse.connection_established)
		else:
			self.server_conn.send(raw_request)

		res = None
		print("CACHE MISS !")
		while True:
			triple = select.select([self.client_conn, self.server_conn], [], [], 60)[0]
			if not len(triple):
				break
			try:
				if self.client_conn in triple:
					data = self.client_conn.recv(max_size)
					if not data:
						break
					self.server_conn.send(data)
				if self.server_conn in triple:
					data = self.server_conn.recv(max_size)
					if not res:
						res = Response(data)
						logg.info(f"{request.method:<8} {request.path} {request.protocol} {res.status if res else ''}")
					if not data:
						break
					print("here")
					self.client_conn.send(data)
					if(request.path in cache):
						cache[request.path].append(data)
					else:
						cache[request.path]=[]
						cache[request.path]=data
					# print(request.path)
					# print(data)
					# print(cache[request.path], "---OUTPUT---")
			except ConnectionAbortedError:
				break
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
