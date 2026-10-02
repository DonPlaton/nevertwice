#!/usr/bin/env python3
"""PREREG-V3 §4.6 (K87), plan step A2.8: the recording-vs-raw-forward A/B tolerance rule, checked at its edges.

* Each widening at its boundary: calls +/- 5 %, tokens +/- 10 %, lost_share +/- 1 pp, items +/- 10 %, wall time
  + 10 % plus 20 ms per call (a faster recording run passes); a hair beyond each edge fails, by name.
* client_abandoned > 0 fails; the added time to first byte is judged by percentiles - p50 21 ms or p95 101 ms fail,
  a mean under the bound does not rescue a p95 over it.
* The rule wants exactly two runs of each leg.
* The hop microbenchmark runs against a local echo and reports added p50/p95 for raw-forward and recording.

    python tests/_test_v3_ab_rule.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite

_spec = importlib.util.spec_from_file_location("v3_ab_rule", ROOT / "research" / "v3" / "ab_rule.py")
A = importlib.util.module_from_spec(_spec)
sys.modules["v3_ab_rule"] = A
_spec.loader.exec_module(A)

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


RAW = [{"calls": 10.0, "tokens_in": 1000.0, "tokens_out": 200.0, "lost_share": 0.02, "items": 8.0, "wall_s": 5.0},
       {"calls": 12.0, "tokens_in": 1100.0, "tokens_out": 220.0, "lost_share": 0.04, "items": 10.0, "wall_s": 6.0}]
OK_TTFB = [5.0] * 95 + [50.0] * 5


def verdict(rec_change: dict, **kw):
    rec = [dict(RAW[0]), dict(RAW[1], **rec_change)]
    return A.ab_verdict(RAW, rec, client_abandoned=kw.get("abandoned", 0), ttfb_added_ms=kw.get("ttfb", OK_TTFB))


check("identical legs are in tolerance", verdict({})["in_tolerance"], str(verdict({})["failures"]))
edges = {"calls": (12.0 * 1.05, 10.0 * 0.95), "tokens_in": (1100 * 1.10, 1000 * 0.90), "tokens_out": (220 * 1.10, 200 * 0.90),
         "lost_share": (0.04 + 0.01, 0.02 - 0.01), "items": (10 * 1.10, 8 * 0.90)}
EPS = 1e-6
for m, (hi, lo) in edges.items():
    check(f"{m}: exactly at the upper edge ({hi:.6g}) passes", verdict({m: hi})["in_tolerance"],
          str(verdict({m: hi})["failures"]))
    check(f"{m}: just above the upper edge fails, by name", any(m in f for f in verdict({m: hi + max(EPS, abs(hi) * 1e-6)})["failures"]))
    check(f"{m}: exactly at the lower edge ({lo:.6g}) passes", verdict({m: lo})["in_tolerance"], str(verdict({m: lo})["failures"]))
    check(f"{m}: just below the lower edge fails, by name", any(m in f for f in verdict({m: lo - max(EPS, abs(lo) * 1e-6)})["failures"]))
wall_hi = 6.0 * 1.10 + 0.020 * 12.0
check("wall time: + 10 % plus 20 ms per call is the upper edge", verdict({"wall_s": wall_hi})["in_tolerance"])
check("wall time: just above it fails", any("wall_s" in f for f in verdict({"wall_s": wall_hi + 1e-6})["failures"]))
check("wall time: a faster recording run passes (no lower bound)", verdict({"wall_s": 0.5})["in_tolerance"])
check("client_abandoned > 0 fails, by name", any("client_abandoned" in f for f in verdict({}, abandoned=1)["failures"]))
check("added TTFB p50 21 ms fails", any("p50" in f for f in verdict({}, ttfb=[21.0] * 100)["failures"]))
check("added TTFB p50 20 ms passes", verdict({}, ttfb=[20.0] * 100)["in_tolerance"])
tail = [1.0] * 90 + [101.0] * 10
check("a p95 of 101 ms fails even though the mean is under 20 ms", any("p95" in f for f in verdict({}, ttfb=tail)["failures"])
      and sum(tail) / len(tail) < 20)
try:
    A.ab_verdict(RAW[:1], RAW, client_abandoned=0, ttfb_added_ms=OK_TTFB)
    check("the rule wants exactly two runs of each leg", False)
except ValueError:
    check("the rule wants exactly two runs of each leg", True)
_added = getattr(A, "added_over", None)
check("Q-AB-2: added_over - each sample over the baseline's MEDIAN (not its mean), never below 0",
      _added is not None and _added([5.0, 12.0, 2.0, 1.0], [1.0, 2.0, 30.0]) == [3.0, 10.0, 0.0, 0.0],
      str(_added([5.0, 12.0, 2.0, 1.0], [1.0, 2.0, 30.0]) if _added else None))
try:
    _added([1.0], [])
    check("Q-AB-2: added_over refuses an empty baseline (no median, no figure)", False)
except (ValueError, TypeError) as e:
    check("Q-AB-2: added_over refuses an empty baseline (no median, no figure)", _added is not None, repr(e))
_calls: list = []
_real_added = A.added_over


def _spy(samples, baseline):
    _calls.append((samples, baseline))
    return _real_added(samples, baseline)


A.added_over = _spy
try:
    A.hop_benchmark(n=10)
finally:
    A.added_over = _real_added
check("HOP-4 (the auditor): hop_benchmark takes each mode's added time over the DIRECT samples - one baseline list for "
      "both modes, never the mode's own samples", len(_calls) == 2 and _calls[0][1] is _calls[1][1]
      and all(s is not b and len(s) == 10 and len(b) == 10 for s, b in _calls), str([(len(s), len(b)) for s, b in _calls]))
hop = A.hop_benchmark(n=40)
check("the hop microbenchmark reports added p50/p95 for raw-forward and recording",
      all(k in hop and hop[k]["n"] == 40 and hop[k]["p50_ms"] >= 0 and hop[k]["p95_ms"] >= hop[k]["p50_ms"]
          for k in ("raw", "record")), str(hop))
print(f"       measured hop (informational): {hop}")



print("\n- the hop fixture cleans up only after a complete drain -")
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

_FAKE_PROXY = "from types import SimpleNamespace\nimport os\n\nArmConfig = ProxyConfig = SimpleNamespace\nSTARTS = []\nSTOPS = []\nACTIVE = set()\n\ndef read_key(path):\n    return None\n\nclass Proxy:\n    def __init__(self, cfg, key, log):\n        self.mode = cfg.arms[0].mode\n\n    def start(self):\n        STARTS.append(self.mode)\n        ACTIVE.add(self.mode)  # partial startup can leave a writer even when start raises\n        if os.environ['HOP_CLEANUP_CASE'] == 'start-error':\n            raise RuntimeError('START-ERROR')\n        return {'arms': {'hop': {'write': 12345}}}\n\n    def stop(self):\n        STOPS.append(self.mode)\n        case = os.environ['HOP_CLEANUP_CASE']\n        if case == 'stop-error' and self.mode == 'record':\n            raise RuntimeError('STOP-ERROR')\n        if case == 'drain-' + self.mode:\n            return {'hop': 1}\n        ACTIVE.discard(self.mode)\n        return {}\n"
_CLEANUP_CHILD = "import importlib.util\nimport json\nimport os\nimport sys\nimport tempfile\nfrom pathlib import Path\n\nbase, case, module_path, fake_path = sys.argv[1:]\nbase = Path(base)\nos.environ['HOP_CLEANUP_CASE'] = case\nspec = importlib.util.spec_from_file_location('hop_cleanup_probe', module_path)\nA = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(A)\ncreated = []\nreal_mkdtemp = tempfile.mkdtemp\n\ndef allocate(*args, **kwargs):\n    path = real_mkdtemp(*args, **kwargs)\n    created.append(path)\n    return path\n\ntempfile.mkdtemp = allocate\n\necho_created = echo_closed = 0\n\nclass Echo:\n    port = 12345\n    def __init__(self):\n        global echo_created\n        echo_created += 1\n    def close(self):\n        global echo_closed\n        echo_closed += 1\n\nA._Echo = Echo\nreads = 0\n\ndef samples(port, token, n):\n    global reads\n    reads += 1\n    if case == 'request-error' and reads == 2:\n        raise RuntimeError('REQUEST-ERROR')\n    return [1.0] * n\n\nA._ttfb = samples\nreal_write = Path.write_bytes\n\ndef write(path, value):\n    if case == 'write-error' and path.name == 'k.env':\n        raise OSError('WRITE-ERROR')\n    return real_write(path, value)\n\nPath.write_bytes = write\nreport = {'result': None, 'error': None}\ntry:\n    report['result'] = A.hop_benchmark(n=3, proxy_module_path=Path(fake_path))\nexcept Exception as error:\n    report['error'] = str(error)\nreport['directories'] = created\nproxy_module = sys.modules['v3_ab_proxy']\nreport.update(starts=proxy_module.STARTS, stops=proxy_module.STOPS, active=sorted(proxy_module.ACTIVE),\n              echo_created=echo_created, echo_closed=echo_closed)\n(base / 'report.json').write_text(json.dumps(report), encoding='utf-8')\n"
for _case, _error, _retained in (
    ("ok", None, False),
    ("drain-raw", "HOP-DRAIN", True),
    ("drain-record", "HOP-DRAIN", True),
    ("stop-error", "STOP-ERROR", True),
    ("write-error", "WRITE-ERROR", False),
    ("start-error", "START-ERROR", False),
    ("request-error", "REQUEST-ERROR", False),
):
    with tempfile.TemporaryDirectory(prefix="nvt_hop_cleanup_reg_") as _base:
        _base_path = Path(_base)
        _fake = _base_path / "fake_proxy.py"
        _driver = _base_path / "driver.py"
        _fake.write_text(_FAKE_PROXY, encoding="utf-8")
        _driver.write_text(_CLEANUP_CHILD, encoding="utf-8")
        _temp_root = _base_path / "owned_temp"
        _temp_root.mkdir()
        _env = dict(os.environ, TEMP=str(_temp_root), TMP=str(_temp_root), TMPDIR=str(_temp_root))
        _proc = subprocess.run([sys.executable, "-B", str(_driver), _base, _case,
                                str(ROOT / "research/v3/ab_rule.py"), str(_fake)],
                               env=_env, capture_output=True, text=True, timeout=30)
        _report_path = _base_path / "report.json"
        _report = json.loads(_report_path.read_text(encoding="utf-8")) if _report_path.exists() else {}
        _dirs = [Path(p) for p in _report.get("directories", [])]
        _owned = len(_dirs) == 1 and all(p.resolve().is_relative_to(_temp_root.resolve()) for p in _dirs)
        _lifetime = _owned and all(p.exists() is _retained for p in _dirs)
        _expected_modes = [] if _case == "write-error" else (
            ["raw", "record"] if _case in ("ok", "drain-record", "stop-error") else ["raw"])
        _expected_active = {"drain-raw": ["raw"], "drain-record": ["record"], "stop-error": ["record"]}.get(_case, [])
        _echo_count = 0 if _case == "write-error" else 1
        _lifecycle = (_report.get("starts") == _expected_modes and _report.get("stops") == _expected_modes
                      and _report.get("active") == _expected_active
                      and _report.get("echo_created") == _report.get("echo_closed") == _echo_count)
        _got_error = _report.get("error")
        _behavior = (_got_error is None and _report.get("result") == {
            "raw": {"p50_ms": 0.0, "p95_ms": 0.0, "n": 3},
            "record": {"p50_ms": 0.0, "p95_ms": 0.0, "n": 3}, "direct_p50_ms": 1.0
        }) if _error is None else (isinstance(_got_error, str) and _error in _got_error
                                  and _report.get("result") is None)
        check(f"HOP-CLEANUP-{_case}: owned lifetime and outcome match the drain",
              _proc.returncode == 0 and _lifetime and _lifecycle and _behavior,
              f"exit={_proc.returncode}, report={_report}, stderr={_proc.stderr[-300:]}")



print("\n- EOF and TCP fragments cannot become valid hop samples -")
from unittest import mock  # noqa: E402


class BoundedSocket:
    """No OS socket; a second read after EOF is an independent named test error."""
    def __init__(self, parts, responses=None, clock=None):
        self.parts = list(parts)
        self.responses = [list(parts) for parts in responses] if responses is not None else None
        self.clock = clock
        self.first_read = True
        self.eof_reads = 0
        self.sent = []
        self.closed = False

    def recv(self, limit):
        if self.clock is not None:
            self.clock[0] += 0.002 if self.first_read else 0.500
        self.first_read = False
        chunk = self.parts.pop(0) if self.parts else b""
        if not chunk:
            self.eof_reads += 1
            if self.eof_reads > 1:
                raise AssertionError("RECV-PAST-EOF: fixture read after connection closure")
        return chunk

    def sendall(self, data):
        if self.responses is not None:
            if self.parts:
                raise AssertionError("SEND-BEFORE-FRAME: next request preceded complete response consumption")
            if not self.responses:
                raise AssertionError("SEND-WITHOUT-FIXTURE: unexpected request")
            self.parts = self.responses.pop(0)
            self.first_read = True
        self.sent.append(data)

    def settimeout(self, value):
        pass

    def close(self):
        self.closed = True


def framed_echo(body):
    return b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


def hop_socket_case(name, parts, expected_error=None, *, n=1):
    clock = [0.0]
    sock = BoundedSocket([], responses=[parts for _ in range(n)], clock=clock)
    result, error = None, None
    with mock.patch.object(A.socket, "create_connection", return_value=sock), \
            mock.patch.object(A.time, "perf_counter", side_effect=lambda: clock[0]):
        try:
            result = A._ttfb(12345, None, n)
        except Exception as caught:
            error = caught
    if expected_error is None:
        # Each first recv costs 2 ms; every later fragment costs 500 ms and must not enter TTFB.
        behavior = error is None and isinstance(result, list) and len(result) == n and all(abs(t - 2.0) < 1e-9 for t in result)
        consumed = not sock.parts and not sock.responses and sock.eof_reads == 0 and len(sock.sent) == n
    else:
        behavior = isinstance(error, ConnectionError) and expected_error in str(error) and result is None
        consumed = sock.eof_reads <= 1
    check(f"HOP-IO-{name}: complete frame or named refusal, then socket close",
          behavior and consumed and sock.closed,
          f"result={result}, error={type(error).__name__}: {error}, unread={len(sock.parts)}, eof={sock.eof_reads}, closed={sock.closed}")


hop_socket_case("empty", [], "HOP-EOF")
hop_socket_case("header-eof", [b"HTTP/1.1 200 OK\r\nCont"], "HOP-EOF")
hop_socket_case("body-eof", [b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\n{}"], "HOP-EOF")
_whole = framed_echo(A._Echo.BODY)
_model_offset = _whole.index(b',"model":')
_body_offset = _whole.index(b"\r\n\r\n") + 4
# The earlier fragment already has >=3 braces, but the response's final model field is still unread.
hop_socket_case("fragmented", [_whole[:8], _whole[8:_body_offset - 1], _whole[_body_offset - 1:_model_offset],
                               _whole[_model_offset:_model_offset + 6], _whole[_model_offset + 6:]], n=2)
hop_socket_case("single-frame", [_whole])
hop_socket_case("duplicate-length", [b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx"],
                "HOP-FRAME")
hop_socket_case("bad-length", [b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n"], "HOP-FRAME")

for _tag, _parts, _complete in (
    ("truncated-request", [b"POST / HTTP/1.1\r\nContent-Length: 3\r\n\r\nx"], False),
    ("fragmented-request", [b"POST / HTTP/1.1\r\nContent-Len", b"gth: 3\r\n\r\nx", b"yz"], True),
):
    _sock = BoundedSocket(_parts)
    _error = None
    try:
        object.__new__(A._Echo)._conn(_sock)
    except Exception as _caught:
        _error = _caught
    _reply_ok = (len(_sock.sent) == 1 and _sock.sent[0].endswith(A._Echo.BODY)) if _complete else not _sock.sent
    check(f"HOP-IO-{_tag}: EOF closes the owned connection, complete requests still receive the echo",
          _error is None and _sock.closed and _sock.eof_reads == 1 and not _sock.parts and _reply_ok,
          f"error={_error}, replies={len(_sock.sent)}, eof={_sock.eof_reads}, closed={_sock.closed}")

print(f"\nv3 ab rule: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
