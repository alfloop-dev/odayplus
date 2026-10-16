"""Real owned children and isolated HTTP/provider protocol boundaries; no live IO."""

import base64
import json
import os
import socket
import sys
import time
from types import SimpleNamespace

import pytest

from modules.external_data.application import native_intake_io_child as child
from modules.external_data.application.native_intake_process import (
    NativeIntakeProcessRunner,
    NativeIORejected,
    NativeIOUnreaped,
    _kill_and_reap,
)
from modules.external_data.application.native_snapshot_process_transport import (
    NativeHttpProcessTransport,
)


class SyntheticRunner(NativeIntakeProcessRunner):
    """Only tests select a child; production construction always uses fixed code."""

    def __init__(self, script):
        self.script = str(script)

    def _command(self, credential_fd):
        result = [sys.executable, "-I", "-S", self.script]
        return result if credential_fd is None else result + [str(credential_fd)]


def runner(tmp_path, code):
    script = tmp_path / "owned_child.py"
    script.write_text(code)
    return SyntheticRunner(script)


def test_default_child_is_fixed_isolated_and_rejects_private_http_without_network(monkeypatch):
    monkeypatch.setenv("ODP_NATIVE_INTAKE_IO_CHILD", "/arbitrary/command")
    instance = NativeIntakeProcessRunner()
    command = instance._command(None)
    assert command[:3] == [sys.executable, "-I", "-S"]
    assert command[3].endswith("/modules/external_data/application/native_intake_io_child.py")
    with pytest.raises(NativeIORejected, match="NATIVE_HTTP_ADDRESS_REJECTED"):
        instance.run({"operation": "http_hop", "url": "http://127.0.0.1/"},
                     deadline=time.monotonic() + 2, guard=lambda: None)


def test_real_child_gets_no_inherited_secrets_or_unrelated_fds(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sensitive-db")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "sensitive-gcp")
    monkeypatch.setenv("ODP_NATIVE_INTAKE_RETRIEVAL_SIGNING_KEY", "sensitive-hmac")
    extra = os.open(tmp_path / "unrelated", os.O_CREAT | os.O_RDWR, 0o600)
    os.set_inheritable(extra, True)
    instance = runner(tmp_path, """
import json,os,sys
request=json.loads(sys.stdin.readline())
result={'environment':dict(os.environ),'isolated':sys.flags.isolated,'no_site':sys.flags.no_site}
try: os.fstat(""" + str(extra) + """); result['unrelated_fd_open']=True
except OSError: result['unrelated_fd_open']=False
print(json.dumps({'ok':True,'result':result}))
""")
    try:
        result = instance.run({"operation": "http_hop"}, deadline=time.monotonic() + 2, guard=lambda: None)
    finally:
        os.close(extra)
    assert result["isolated"] == result["no_site"] == 1
    assert result["unrelated_fd_open"] is False
    assert set(result["environment"]) <= {"LANG", "LC_ALL", "PYTHONUNBUFFERED"}


def test_real_private_credential_pipe_carries_full_8k_without_env_or_argv(tmp_path):
    instance = runner(tmp_path, """
import json,os,sys
request=json.loads(sys.stdin.readline()); fd=int(sys.argv[1]); raw=bytearray()
while True:
 block=os.read(fd,1024)
 if not block: break
 raw.extend(block)
os.close(fd)
print(json.dumps({'ok':True,'result':{'length':len(raw),'argv':sys.argv,'env':dict(os.environ)}}))
""")
    token = "private-only-" + "x" * (8192 - 13)
    result = instance.run({"operation": "gcs_bucket"}, credential=token,
                          deadline=time.monotonic() + 2, guard=lambda: None)
    assert result["length"] == len(token)
    assert token not in json.dumps(result)
    assert len(result["argv"]) == 2 and result["argv"][1].isdigit()


@pytest.mark.parametrize("phase", ["dns", "headers", "body", "gcs", "ipc"])
def test_actual_owned_child_is_killed_and_reaped_on_hard_wall_deadline(tmp_path, phase):
    pid_file = tmp_path / "pid"
    # Load the actual stdlib child, replacing only the external stage with a
    # blocking test double inside this owned process. No real network call.
    script = "import os,time,pathlib,importlib.util,sys\npathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\n"
    script += "spec=importlib.util.spec_from_file_location('child'," + repr(child.__file__) + ")\n"
    script += "c=importlib.util.module_from_spec(spec); spec.loader.exec_module(c)\n"
    script += "from types import SimpleNamespace\n"
    script += "def block(*a,**k): time.sleep(30)\n"
    if phase == "dns":
        script += "c.socket.getaddrinfo=block\n"
    elif phase in {"headers", "body", "gcs"}:
        script += "response=SimpleNamespace(status=200,getheader=lambda name,default=None: 'text/html' if name=='Content-Type' else default,read=block)\n"
        script += "connection=SimpleNamespace(sock=SimpleNamespace(settimeout=lambda v:None),request=lambda *a,**k:None,close=lambda:None,getresponse="
        script += "block)\n" if phase in {"headers", "gcs"} else "lambda:response)\n"
        script += "c._connection=lambda *a:connection\n"
    if phase == "ipc":
        script += "block()\n"
    script += "sys.argv=[sys.argv[0]] if len(sys.argv)==1 else [sys.argv[0],'--credential-fd',sys.argv[1]]\nc.main()\n"
    instance = runner(tmp_path, script)
    start = time.monotonic()
    with pytest.raises(NativeIORejected, match="NATIVE_IO_DEADLINE_EXPIRED"):
        instance.run({"operation": "gcs_bucket", "bucket": "test-native"} if phase == "gcs" else
                     {"operation": "http_hop", "url": "https://public.example/"},
                     credential="private" if phase == "gcs" else None,
                     deadline=start + 0.2, guard=lambda: None)
    assert time.monotonic() - start < 1.5
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)


def test_guard_loss_kills_child_without_waiting_for_deadline(tmp_path):
    pid_file = tmp_path / "pid"
    instance = runner(tmp_path, "import os,time,pathlib\npathlib.Path(" + repr(str(pid_file))
                      + ").write_text(str(os.getpid()))\ntime.sleep(30)\n")
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if pid_file.exists():
            raise NativeIORejected("NATIVE_IO_LEASE_LOST")

    start = time.monotonic()
    with pytest.raises(NativeIORejected, match="NATIVE_IO_LEASE_LOST"):
        instance.run({"operation": "http_hop"}, deadline=start + 5, guard=guard)
    assert calls >= 2 and time.monotonic() - start < 1.5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.parametrize("output,error", [
    ("print('not-json')", "PROTOCOL_REJECTED"),
    ("print('x'*750001)", "LENGTH_REJECTED"),
    ("import sys; sys.stderr.write('x'*65537)", "LENGTH_REJECTED"),
    ("import sys; sys.stderr.write('secret'); print('{\\\"ok\\\":true,\\\"result\\\":{}}')", "CHILD_FAILED"),
    ("print('{\\\"ok\\\":true,\\\"result\\\":{},\\\"extra\\\":true}')", "PROTOCOL_REJECTED"),
    ("raise RuntimeError('hidden')", "CHILD_FAILED"),
])
def test_real_child_protocol_bounds_and_errors_never_include_child_bytes(tmp_path, output, error):
    instance = runner(tmp_path, "import sys\nsys.stdin.read()\n" + output + "\n")
    with pytest.raises(NativeIORejected, match="NATIVE_IO_" + error) as rejected:
        instance.run({"operation": "http_hop"}, deadline=time.monotonic() + 2, guard=lambda: None)
    assert "secret" not in str(rejected.value) and "hidden" not in str(rejected.value)


@pytest.mark.parametrize("command,credential", [
    ({"operation": "http_hop"}, "not-for-http"),
    ({"operation": "gcs_bucket"}, None),
    ({"operation": "gcs_bucket"}, "x\nheader"),
    ({"operation": "gcs_bucket"}, "x" * 8193),
    ({"operation": "http_hop", "timeout_seconds": 500}, None),
])
def test_invalid_credential_or_deadline_protocol_is_rejected_before_spawn(command, credential, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("must not spawn"))
    with pytest.raises(NativeIORejected):
        NativeIntakeProcessRunner().run(command, credential=credential,
                                       deadline=time.monotonic() + 2, guard=lambda: None)


def test_unreapable_child_retains_quota_signal(monkeypatch):
    fake = SimpleNamespace(pid=987654321, poll=lambda: None, wait=lambda **k: 0)
    monkeypatch.setattr(os, "killpg", lambda *a: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(NativeIOUnreaped, match="NATIVE_IO_CHILD_UNREAPED"):
        _kill_and_reap(fake)


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "10.0.0.1", "169.254.169.254", "0.0.0.0",
    "224.0.0.1", "240.0.0.1", "192.0.2.1", "::ffff:8.8.8.8", "2002:0808:0808::1", "fe80::1"])
def test_non_global_mapped_and_tunnel_addresses_are_rejected(address):
    assert not child._public_address(address)


def test_mixed_dns_answers_rejected_before_any_connect(monkeypatch):
    answers = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))
               for ip in ["8.8.8.8", "127.0.0.1"]]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answers)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(child.ChildRejected) as error:
        child._connection("example.test", 443, True, time.monotonic() + 1)
    assert error.value.code == "NATIVE_HTTP_ADDRESS_REJECTED"


def test_dns_is_once_numeric_pin_and_tls_uses_verified_original_hostname(monkeypatch):
    calls, connected, tls = [], [], []

    def resolve(*args, **kwargs):
        calls.append(args)
        assert len(calls) == 1
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]

    raw = SimpleNamespace(settimeout=lambda value: None, connect=connected.append, close=lambda: None)
    context = SimpleNamespace(wrap_socket=lambda sock, server_hostname: tls.append(server_hostname) or sock)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *a: raw)
    monkeypatch.setattr(child.ssl, "create_default_context", lambda: context)
    result = child._connection("original.example", 443, True, time.monotonic() + 1)
    assert result.sock is raw and connected == [("8.8.8.8", 443)]
    assert tls == ["original.example"] and len(calls) == 1


class Response:
    def __init__(self, status=200, headers=None, body=b"<html>ok</html>"):
        self.status, self.headers, self.body = status, headers or {}, body
        self.read_calls = 0

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size):
        self.read_calls += 1
        value, self.body = self.body[:size], self.body[size:]
        return value


def http_fixture(monkeypatch, response):
    calls = []
    connection = SimpleNamespace(sock=SimpleNamespace(settimeout=lambda value: None),
        request=lambda *a, **k: calls.append((a, k)), getresponse=lambda: response, close=lambda: None)
    monkeypatch.setattr(child, "_connection", lambda *a: connection)
    return calls


def test_http_one_hop_never_auto_follows_redirect_or_reads_its_body(monkeypatch):
    response = Response(302, {"Location": "http://127.0.0.1/private"}, b"private")
    calls = http_fixture(monkeypatch, response)
    result = child.http_hop({"operation": "http_hop", "timeout_seconds": 1, "url": "https://public.example/x"}, time.monotonic() + 1)
    assert len(calls) == 1 and response.read_calls == 0
    assert result["location"] == "http://127.0.0.1/private" and result["body_base64"] == ""
    headers = calls[0][1]["headers"]
    assert headers["Host"] == "public.example" and headers["Accept-Encoding"] == "identity"
    assert not {"Authorization", "Cookie", "Proxy-Authorization"} & headers.keys()


@pytest.mark.parametrize("headers,body,code", [
    ({"Content-Type": "application/json"}, b"{}", "NATIVE_HTTP_MEDIA_TYPE_REJECTED"),
    ({"Content-Type": "text/html", "Content-Encoding": "gzip"}, b"zip", "NATIVE_IO_ENCODING_REJECTED"),
    ({"Content-Type": "text/html", "Content-Length": "512001"}, b"x", "NATIVE_IO_LENGTH_REJECTED"),
    ({"Content-Type": "text/html"}, b"x" * 512001, "NATIVE_IO_LENGTH_REJECTED"),
    ({"Content-Type": "text/html", "Content-Length": "99"}, b"x", "NATIVE_IO_LENGTH_REJECTED"),
])
def test_http_actual_response_caps_reject_before_snapshot(monkeypatch, headers, body, code):
    http_fixture(monkeypatch, Response(headers=headers, body=body))
    with pytest.raises(child.ChildRejected) as error:
        child.http_hop({"operation": "http_hop", "timeout_seconds": 1, "url": "https://public.example/"}, time.monotonic() + 1)
    assert error.value.code == code


@pytest.mark.parametrize("status,body", [(401, b"private"), (403, b"private"), (200, b'<input type="password">')])
def test_auth_wall_returns_no_content_and_never_becomes_source_blocked(monkeypatch, status, body):
    http_fixture(monkeypatch, Response(status, {"Content-Type": "text/html"}, body))
    result = child.http_hop({"operation": "http_hop", "timeout_seconds": 1, "url": "https://public.example/"}, time.monotonic() + 1)
    assert result["auth_wall"] is True and result["body_base64"] == ""
    assert "SOURCE_BLOCKED" not in json.dumps(result)


def test_detached_connection_close_body_is_still_bounded():
    response = Response(headers={"Content-Length": "3"}, body=b"abc")
    assert child._content(response, SimpleNamespace(sock=None), time.monotonic() + 1, 3) == b"abc"


@pytest.mark.parametrize("media", [[], {}, 12, "application/json"])
def test_untrusted_transport_reply_media_is_fail_closed(media):
    result = {"status": 200, "location": None, "media_type": media, "body_base64": base64.b64encode(b"ok").decode(), "auth_wall": False}
    transport = NativeHttpProcessTransport(SimpleNamespace(run=lambda *a, **k: result))
    with pytest.raises(NativeIORejected, match="NATIVE_IO_PROTOCOL_REJECTED"):
        transport.fetch_hop("https://public.example/", deadline=time.monotonic() + 1, guard=lambda: None)
