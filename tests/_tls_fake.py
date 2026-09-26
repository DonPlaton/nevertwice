"""Test-only TLS fakes for the v3 instruments: a throwaway certificate made at test time (never committed), a local
TLS HTTP server with fixed routes, and a loopback CONNECT hop that tunnels to it. Standard library, plus
``cryptography`` or the ``openssl`` CLI for the certificate when either is available."""
from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path


def make_test_cert(d: Path, host: str, extra_hosts: tuple = ()):
    """A self-signed certificate for ``host`` (CN, and SAN with ``extra_hosts``): (cert, key, how), or None with
    neither tool present."""
    d.mkdir(parents=True, exist_ok=True)
    cert, keyf = d / f"{host}.pem", d / f"{host}.key"
    try:
        import datetime  # noqa: PLC0415

        from cryptography import x509  # noqa: PLC0415
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
        from cryptography.x509.oid import NameOID  # noqa: PLC0415
    except ImportError:
        x509 = None
    if x509 is not None:
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
        now = datetime.datetime.now(datetime.timezone.utc)
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
             .not_valid_after(now + datetime.timedelta(days=1))
             .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in (host, *extra_hosts)]), critical=False)
             .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
             .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                          data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                          crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(k.public_key()), critical=False)
             .sign(k, hashes.SHA256()))
        keyf.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
        return cert, keyf, "cryptography"
    exe = shutil.which("openssl")
    if exe:
        env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
        r = subprocess.run([exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", f"/CN={host}",
                            "-addext", "subjectAltName=" + ",".join(f"DNS:{h}" for h in (host, *extra_hosts)),
                            "-keyout", str(keyf), "-out", str(cert)],
                           capture_output=True, timeout=120, env=env)
        if r.returncode == 0 and cert.is_file() and keyf.is_file():
            return cert, keyf, "openssl"
    return None


class TlsHttpServer:
    """HTTP/1.1 over TLS on loopback, keep-alive, one fixed response per path: routes[path] = (status, headers, body).
    Counts completed handshakes and records every request head."""

    def __init__(self, cert: Path, keyf: Path, routes: dict):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(str(cert), str(keyf))
        self.routes = routes
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.handshakes = 0
        self.heads: list[bytes] = []
        self.errors: list[str] = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _conn(self, c):
        c.settimeout(10)
        try:
            s = self.ctx.wrap_socket(c, server_side=True)
        except (ssl.SSLError, OSError) as e:
            self.errors.append(type(e).__name__)
            c.close()
            return
        self.handshakes += 1
        buf = bytearray()
        try:
            while True:
                while b"\r\n\r\n" not in buf:
                    chunk = s.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                end = buf.index(b"\r\n\r\n") + 4
                head = bytes(buf[:end])
                del buf[:end]
                self.heads.append(head)
                path = head.split(b" ", 2)[1].decode("latin-1")
                status, headers, body = self.routes.get(path, (404, [], b""))
                chunked = any(k.lower() == "transfer-encoding" for k, _ in headers)   # a route may ask for chunking
                lines = [f"HTTP/1.1 {status} X", *(f"{k}: {v}" for k, v in headers)]
                if not chunked:
                    lines.append(f"Content-Length: {len(body)}")
                payload = body
                if chunked:
                    payload = (b"%x\r\n%s\r\n" % (len(body), body) if body else b"") + b"0\r\n\r\n"
                s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + payload)
        except (ssl.SSLError, OSError) as e:
            self.errors.append(type(e).__name__)
        finally:
            s.close()

    def close(self):
        self.sock.close()


class TunnelHop:
    """A loopback HTTP proxy: answers CONNECT with ``reply`` (200 by default), then pipes bytes to ``target_port``."""

    def __init__(self, target_port: int, reply: bytes = b"HTTP/1.1 200 Connection established\r\n\r\n"):
        self.target_port, self.reply = target_port, reply
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.connects: list[bytes] = []
        self.seen = bytearray()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _pipe(self, a, b, record: bool):
        try:
            while True:
                chunk = a.recv(65536)
                if not chunk:
                    break
                if record:
                    self.seen.extend(chunk)
                b.sendall(chunk)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _conn(self, c):
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = c.recv(4096)
            if not chunk:
                c.close()
                return
            buf += chunk
        self.connects.append(bytes(buf).split(b"\r\n", 1)[0])
        self.seen.extend(buf)
        c.sendall(self.reply)
        if not self.reply.split(b" ", 2)[1:2] == [b"200"]:
            c.close()
            return
        t = socket.create_connection(("127.0.0.1", self.target_port))
        a = threading.Thread(target=self._pipe, args=(c, t, True), daemon=True)
        b = threading.Thread(target=self._pipe, args=(t, c, False), daemon=True)
        a.start()
        b.start()
        a.join(30)
        b.join(30)
        c.close()
        t.close()

    def close(self):
        self.sock.close()
