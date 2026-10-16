"""Fixed stdlib-only one-operation child, started with Python -I -S.

This module deliberately imports no product code and launches no descendants.
The parent owns the wall deadline, leases, permission and process-group cleanup.
No source-page credential is supported. Only fixed GCS operations may read the
dedicated storage token from the explicitly inherited private pipe.
"""

import base64
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import sys
import time
from urllib.parse import quote, urlencode, urlsplit

MAX_CONTENT = 512_000
MAX_INPUT = 750_000
MAX_JSON = 65_536
MAX_OUTPUT = 750_000
MEDIA = frozenset({"text/html", "application/xhtml+xml"})
REDIRECTS = frozenset({301, 302, 303, 307, 308})
_BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]\Z")
_KEY = re.compile(r"tenants/[0-9a-f-]{36}/native-intake-retrieval/[0-9a-f-]{36}/[1-9][0-9]*/[1-9][0-9]*/[0-9a-f-]{36}/raw\Z")
_KMS = re.compile(r"projects/[A-Za-z0-9_-]{1,128}/locations/asia-east1/keyRings/[A-Za-z0-9_-]{1,63}/cryptoKeys/[A-Za-z0-9_-]{1,63}\Z")


class ChildRejected(ValueError):
    def __init__(self, code, status=None):
        self.code, self.status = code, status


def _remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ChildRejected("NATIVE_IO_DEADLINE_EXPIRED")
    return seconds


def _public_address(value):
    address = ipaddress.ip_address(value)
    return (address.is_global and not address.is_multicast and not address.is_reserved
        and not address.is_loopback and not address.is_link_local and not address.is_unspecified
        and not (address.version == 6 and (address.ipv4_mapped or address.sixtofour or address.teredo)))


def _addresses(host, port):
    found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not found or any(not _public_address(row[4][0]) for row in found):
        raise ChildRejected("NATIVE_HTTP_ADDRESS_REJECTED")
    return list(dict.fromkeys((row[0], row[4]) for row in found))


def _connection(host, port, secure, deadline):
    # Resolve exactly once, reject mixed public/private answers, and connect
    # directly to one of those numeric sockaddr values. HTTPConnection never
    # gets a chance to resolve a hostname again.
    addresses = _addresses(host, port)
    last_error = None
    for family, sockaddr in addresses:
        connection = None
        raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        try:
            raw.settimeout(_remaining(deadline))
            raw.connect(sockaddr)
            if secure:
                wrapped = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
                raw = wrapped
            connection = http.client.HTTPConnection(host, port, timeout=_remaining(deadline))
            connection.sock = raw
            return connection
        except (OSError, ssl.SSLError) as error:
            last_error = error
            if connection is not None:
                connection.close()
            raw.close()
    raise ChildRejected("NATIVE_IO_CONNECTION_FAILED") from last_error


def _content(response, connection, deadline, cap):
    encoding = response.getheader("Content-Encoding")
    if encoding is not None and encoding.strip().lower() != "identity":
        raise ChildRejected("NATIVE_IO_ENCODING_REJECTED")
    length = response.getheader("Content-Length")
    if length is not None and (not re.fullmatch(r"0|[1-9][0-9]*", length) or int(length) > cap):
        raise ChildRejected("NATIVE_IO_LENGTH_REJECTED")
    result = bytearray()
    while True:
        remaining = _remaining(deadline)
        # HTTPConnection may detach a Connection:close response while its
        # buffered response stream still owns the socket. The parent always
        # enforces the whole-operation wall deadline, including that stream.
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        chunk = response.read(min(16_384, cap + 1 - len(result)))
        if not chunk:
            break
        result.extend(chunk)
        if len(result) > cap:
            raise ChildRejected("NATIVE_IO_LENGTH_REJECTED")
    if length is not None and len(result) != int(length):
        raise ChildRejected("NATIVE_IO_LENGTH_REJECTED")
    return bytes(result)


def http_hop(request, deadline):
    if set(request) != {"operation", "timeout_seconds", "url"}:
        raise ChildRejected("NATIVE_IO_PROTOCOL_REJECTED")
    url = request["url"]
    if (not isinstance(url, str) or not 1 <= len(url) <= 4096 or not url.isascii()
            or any(ord(c) < 33 or ord(c) == 127 for c in url)):
        raise ChildRejected("NATIVE_HTTP_URL_REJECTED")
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ChildRejected("NATIVE_HTTP_URL_REJECTED")
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port != (443 if parsed.scheme == "https" else 80) or "%" in host or "\\" in url:
        raise ChildRejected("NATIVE_HTTP_URL_REJECTED")
    target = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    connection = _connection(host, port, parsed.scheme == "https", deadline)
    try:
        host_header = "[" + host + "]" if ":" in host else host
        connection.request("GET", target, headers={"Host": host_header, "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity", "User-Agent": "ODay-Native-Intake/1", "Connection": "close"})
        connection.sock.settimeout(_remaining(deadline))
        response = connection.getresponse()
        status = response.status
        location = response.getheader("Location") if status in REDIRECTS else None
        if location is not None and (len(location) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in location)):
            raise ChildRejected("NATIVE_HTTP_REDIRECT_REJECTED")
        media_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if status != 200:
            return {"status": status, "location": location, "media_type": None, "body_base64": "", "auth_wall": status in {401, 403}}
        if media_type not in MEDIA:
            raise ChildRejected("NATIVE_HTTP_MEDIA_TYPE_REJECTED")
        body = _content(response, connection, deadline, MAX_CONTENT)
        auth_wall = bool(re.search(rb"<input\b[^>]{0,2048}\btype\s*=\s*['\"]?password\b", body, re.I))
        return {"status": status, "location": None, "media_type": media_type,
            "body_base64": "" if auth_wall else base64.b64encode(body).decode("ascii"), "auth_wall": auth_wall}
    finally:
        connection.close()


def _storage_token(descriptor):
    if type(descriptor) is not int or descriptor < 3:
        raise ChildRejected("NATIVE_GCS_CREDENTIAL_UNAVAILABLE")
    try:
        chunks, size = [], 0
        while True:
            block = os.read(descriptor, 8193 - size)
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > 8192:
                raise ChildRejected("NATIVE_GCS_CREDENTIAL_UNAVAILABLE")
        token = b"".join(chunks).decode("ascii")
        if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ChildRejected("NATIVE_GCS_CREDENTIAL_UNAVAILABLE")
        return token
    finally:
        os.close(descriptor)


def _generation(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ChildRejected("NATIVE_GCS_GENERATION_REJECTED")
    return value


def gcs_operation(request, deadline, descriptor):
    operation = request["operation"]
    common = {"operation", "timeout_seconds", "bucket"}
    extra = {"gcs_bucket": set(), "gcs_upload": {"key", "body_base64", "media_type", "kms_key_name"},
        "gcs_head": {"key", "generation"}, "gcs_download": {"key", "generation"}, "gcs_delete": {"key", "generation"}}
    if operation not in extra or set(request) != common | extra[operation]:
        raise ChildRejected("NATIVE_IO_PROTOCOL_REJECTED")
    bucket = request["bucket"]
    if not isinstance(bucket, str) or not _BUCKET.fullmatch(bucket) or ".." in bucket:
        raise ChildRejected("NATIVE_GCS_BINDING_REJECTED")
    path, query, method, body = "/storage/v1/b/" + quote(bucket, safe=""), {}, "GET", None
    media_type = "application/json"
    if operation != "gcs_bucket":
        key = request["key"]
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ChildRejected("NATIVE_GCS_BINDING_REJECTED")
        path += "/o/" + quote(key, safe="")
        if operation == "gcs_upload":
            if request["media_type"] not in MEDIA or not isinstance(request["kms_key_name"], str) or not _KMS.fullmatch(request["kms_key_name"]):
                raise ChildRejected("NATIVE_GCS_BINDING_REJECTED")
            body = base64.b64decode(request["body_base64"], validate=True)
            if len(body) > MAX_CONTENT:
                raise ChildRejected("NATIVE_IO_LENGTH_REJECTED")
            path, method, media_type = "/upload/storage/v1/b/" + quote(bucket, safe="") + "/o", "POST", request["media_type"]
            query = {"uploadType": "media", "name": key, "ifGenerationMatch": "0", "kmsKeyName": request["kms_key_name"]}
        elif request["generation"] is not None:
            query["generation"] = str(_generation(request["generation"]))
        elif operation != "gcs_head":
            raise ChildRejected("NATIVE_GCS_GENERATION_REJECTED")
        if operation == "gcs_download":
            query["alt"] = "media"
        elif operation == "gcs_delete":
            method = "DELETE"
            query["ifGenerationMatch"] = str(_generation(request["generation"]))
    token = _storage_token(descriptor)
    connection = _connection("storage.googleapis.com", 443, True, deadline)
    try:
        target = path + ("?" + urlencode(query) if query else "")
        connection.request(method, target, body=body, headers={"Authorization": "Bearer " + token,
            "Accept-Encoding": "identity", "Content-Type": media_type, "Connection": "close"})
        connection.sock.settimeout(_remaining(deadline))
        response = connection.getresponse()
        if response.status not in ({204} if operation == "gcs_delete" else {200}):
            raise ChildRejected("NATIVE_GCS_HTTP_REJECTED", response.status)
        if operation == "gcs_delete":
            return {}
        raw = _content(response, connection, deadline, MAX_CONTENT if operation == "gcs_download" else MAX_JSON)
        if operation == "gcs_download":
            actual = response.getheader("x-goog-generation")
            if actual is not None and actual != str(request["generation"]):
                raise ChildRejected("NATIVE_GCS_GENERATION_REJECTED")
            return {"body_base64": base64.b64encode(raw).decode("ascii")}
        metadata = json.loads(raw)
        if not isinstance(metadata, dict):
            raise ChildRejected("NATIVE_GCS_METADATA_REJECTED")
        fields = ("kind", "name", "location", "locationType") if operation == "gcs_bucket" else (
            "kind", "name", "bucket", "generation", "size", "contentType", "contentEncoding", "kmsKeyName")
        return {key: metadata[key] for key in fields if key in metadata}
    finally:
        connection.close()


def main():
    try:
        descriptor = None
        if len(sys.argv) == 3 and sys.argv[1] == "--credential-fd":
            descriptor = int(sys.argv[2])
        elif len(sys.argv) != 1:
            raise ChildRejected("NATIVE_IO_PROTOCOL_REJECTED")
        raw = sys.stdin.buffer.readline(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT or not raw.endswith(b"\n") or sys.stdin.buffer.read(1):
            raise ChildRejected("NATIVE_IO_PROTOCOL_REJECTED")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ChildRejected("NATIVE_IO_PROTOCOL_REJECTED")
        timeout = request.get("timeout_seconds")
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise ChildRejected("NATIVE_IO_DEADLINE_REJECTED")
        deadline = time.monotonic() + timeout
        if request.get("operation") == "http_hop" and descriptor is None:
            result = http_hop(request, deadline)
        elif isinstance(request.get("operation"), str) and request["operation"].startswith("gcs_"):
            result = gcs_operation(request, deadline, descriptor)
        else:
            raise ChildRejected("NATIVE_IO_PROTOCOL_REJECTED")
        reply = {"ok": True, "result": result}
    except ChildRejected as error:
        reply = {"ok": False, "code": error.code, "status": error.status}
    except Exception:
        # Never serialize exception text, URLs, headers, bytes or credentials.
        reply = {"ok": False, "code": "NATIVE_IO_CHILD_FAILED", "status": None}
    encoded = json.dumps(reply, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(encoded) > MAX_OUTPUT:
        encoded = b'{"ok":false,"code":"NATIVE_IO_LENGTH_REJECTED","status":null}\n'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
