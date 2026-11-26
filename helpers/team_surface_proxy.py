"""HTTP proxy for project Browser sessions. The viewer can reach public web addresses."""

from http.client import HTTPResponse
import ipaddress
import json
import os
import selectors
import socket
import socketserver
import threading
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


_SYNTHETIC_DNS_RANGE = ipaddress.ip_network("198.18.0.0/15")


def _resolve_public_dns(host, port, resolver):
    if urlsplit(resolver).scheme != "https":
        raise ValueError("The public DNS endpoint must use HTTPS.")
    opener = build_opener(ProxyHandler({}))
    for query_type, record_type in (("A", 1), ("AAAA", 28)):
        query = urlencode({"name": host, "type": query_type})
        request = Request(f"{resolver}?{query}", headers={"Accept": "application/dns-json"})
        with opener.open(request, timeout=5) as response:
            payload = json.loads(response.read(65536))
        records = payload.get("Answer", ()) if payload.get("Status") == 0 else ()
        if addresses := [entry["data"] for entry in records if entry.get("type") == record_type]:
            return [endpoint for address in addresses for endpoint in socket.getaddrinfo(
                address, port, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST,
            )]
    return []


def _is_hostname(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return "." in value.rstrip(".")
    return False


def public_address(host, port, resolver=""):
    if not host or port not in {80, 443}:
        raise ValueError("Only ordinary public web destinations are supported.")
    endpoints = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if resolver and endpoints and _is_hostname(host) and all(
        ipaddress.ip_address(endpoint[4][0]) in _SYNTHETIC_DNS_RANGE for endpoint in endpoints
    ):
        endpoints = _resolve_public_dns(host, port, resolver)
    for family, kind, protocol, _, address in endpoints:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip, "ipv4_mapped", None):
            raise ValueError("This network destination is unavailable.")
    if not endpoints:
        raise ValueError("The site address could not be resolved.")
    return endpoints[0]


def relay_streams(client, upstream):
    with selectors.DefaultSelector() as ready:
        ready.register(client, selectors.EVENT_READ, upstream)
        ready.register(upstream, selectors.EVENT_READ, client)
        while events := ready.select(timeout=90):
            for key, _ in events:
                data = key.fileobj.recv(65_536)
                if not data:
                    return
                key.data.sendall(data)


def _connect_tunnel(sock, address):
    ip, port = address[:2]
    host = f"[{ip}]" if ":" in ip else ip
    authority = f"{host}:{port}"
    sock.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("ascii"))
    with HTTPResponse(sock) as response:
        response.begin()
        if response.status != 200:
            raise OSError("Public web proxy could not connect.")


class WebProxy(socketserver.StreamRequestHandler):
    timeout = 20
    rbufsize = 0

    def public_socket(self, host, port):
        family, kind, protocol, _, address = public_address(host, port, self.server.resolver)
        upstream_socket = self.server.upstream_socket
        upstream = socket.socket(socket.AF_UNIX) if upstream_socket else socket.socket(family, kind, protocol)
        try:
            upstream.settimeout(15)
            upstream.connect(upstream_socket or address)
            if upstream_socket:
                _connect_tunnel(upstream, address)
            return upstream
        except BaseException:
            upstream.close()
            raise

    def handle(self):
        try:
            method, target, version = self.rfile.readline(8193).decode("ascii").strip().split()
            connect = method == "CONNECT"
            url = urlsplit("//" + target if connect else target)
            port = url.port or (443 if connect else 80)
            if url.username or url.password or (not connect and url.scheme != "http"):
                raise ValueError("Unsupported web request.")
            headers = []
            total_size = 0
            while True:
                line = self.rfile.readline(8193)
                total_size += len(line)
                if total_size > 32768 or not line:
                    raise ValueError("Invalid request headers.")
                if line == b"\r\n":
                    break
                key, _, _ = line.partition(b":")
                if key.lower() not in {b"connection", b"proxy-connection", b"proxy-authorization", b"upgrade"}:
                    headers.append(line)
            with self.public_socket(url.hostname, port) as upstream:
                if connect:
                    self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    path = url.path or "/"
                    if url.query:
                        path += "?" + url.query
                    upstream.sendall(f"{method} {path} {version}\r\n".encode("ascii") + b"".join(headers) + b"Connection: close\r\n\r\n")
                relay_streams(self.connection, upstream)
        except (OSError, ValueError, UnicodeError):
            try:
                self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass


class TerminalProxy(WebProxy):
    def _read_exact(self, size):
        result = bytearray()
        while len(result) < size:
            chunk = self.rfile.read(size - len(result))
            if not chunk:
                raise ValueError("Incomplete SOCKS request.")
            result.extend(chunk)
        return bytes(result)

    def handle(self):
        try:
            socks = self.connection.recv(1, socket.MSG_PEEK) == b"\x05"
        except OSError:
            return
        if not socks:
            return super().handle()
        try:
            _, count = self._read_exact(2)
            if 0 not in self._read_exact(count):
                self.wfile.write(b"\x05\xff")
                return
            self.wfile.write(b"\x05\x00")
            version, command, reserved, address_type = self._read_exact(4)
            if (version, command, reserved) != (5, 1, 0):
                raise ValueError("Only SOCKS CONNECT is supported.")
            if address_type == 3:
                host = self._read_exact(self._read_exact(1)[0]).decode("ascii")
            elif address_type in {1, 4}:
                host = str(ipaddress.ip_address(self._read_exact(4 if address_type == 1 else 16)))
            else:
                raise ValueError("Unsupported SOCKS address.")
            port = int.from_bytes(self._read_exact(2), "big")
            upstream = self.public_socket(host, port)
        except (OSError, ValueError, UnicodeError):
            try:
                self.wfile.write(b"\x05\x01\x00\x01" + bytes(6))
            except OSError:
                pass
            return
        with upstream:
            try:
                self.wfile.write(b"\x05\x00\x00\x01" + bytes(6))
                relay_streams(self.connection, upstream)
            except OSError:
                pass


class _ProjectProxy:
    daemon_threads = True

    def __init__(self, address, handler=WebProxy):
        self.upstream_socket = os.environ.get("A0_TEAM_BROWSER_PROXY_SOCKET", "")
        self.resolver = os.environ.get("A0_TEAM_BROWSER_DNS_HTTPS", "") if self.upstream_socket else ""
        super().__init__(address, handler)
        threading.Thread(target=self.serve_forever, daemon=True, name="project-browser-proxy").start()


class ProjectWebProxy(_ProjectProxy, socketserver.ThreadingTCPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0))


class ProjectUnixWebProxy(_ProjectProxy, socketserver.ThreadingUnixStreamServer):
    def __init__(self, socket_path):
        super().__init__(socket_path, TerminalProxy)
