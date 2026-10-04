"""Offline Supermemory boundaries using owned fixtures and synthetic loopback upstreams."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
import _env_guard  # noqa: F401,E402


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


P = load('v3_sm_probe_under_test', 'research/v3/probe_sm.py')
SS = load('v3_sm_server_for_core_test', 'research/v3/sm_server.py')
L = load('v3_launch_for_sm_core_test', 'research/v3/launch.py')
ROWS = []


def check(name: str, fn) -> None:
    try:
        good = fn() is True
    except Exception as exc:
        good = False
        print('control-exception', name, type(exc).__name__)
    ROWS.append((name, good))
    print(('  ok   ' if good else '  FAIL ') + name)


def refused(fn, exception) -> bool:
    try:
        fn()
        return False
    except exception:
        return True


def fw_fixture(program: Path) -> dict:
    return {'program': str(program), 'profiles': [{'name': name, 'enabled': 'True', 'default_inbound': 'Block'}
            for name in ('Domain', 'Private', 'Public')], 'override_allow': [], 'rules': [{
                'name': 'owned-fw-control', 'enabled': 'True', 'direction': 'Inbound', 'action': 'Block',
                'profile': 'Any', 'protocol': 'Any', 'local_port': ['Any'], 'remote_address': ['Any'],
                'override_block': 'False', 'local_address': ['Any'], 'remote_port': ['Any'],
                'interface_alias': ['Any'], 'interface_type': 'Any', 'service': 'Any', 'package': 'Any',
                'authentication': 'NotRequired', 'encryption': 'NotRequired',
                'local_user': 'Any', 'remote_user': 'Any', 'remote_machine': 'Any',
                'dynamic_transport': '0', 'owner': '', 'platforms': [],
                'remote_dynamic_keyword_addresses': [], 'policy_app_id': '', 'package_family_name': '',
                'enforcement': [1], 'primary_status': 1}]}


def firewall_controls():
    program = Path('C:/owned/fixture/never-executed.exe')
    value = fw_fixture(program)
    if P.firewall_problems(value, program=program) != []:
        return False
    negatives = []
    for profile in range(3):
        changed = copy.deepcopy(value)
        changed['profiles'][profile]['enabled'] = 'False'
        negatives.append(changed)
    changed = copy.deepcopy(value)
    changed['profiles'].pop()
    negatives.append(changed)
    changed = copy.deepcopy(value)
    changed['override_allow'] = [{'name': 'owned-override', 'enabled': 'True', 'direction': 'Inbound', 'action': 'Allow'}]
    negatives.append(changed)
    for field, invalid in (('enabled', True), ('enabled', 'unknown'), ('direction', None), ('action', 'unknown')):
        changed = copy.deepcopy(value)
        changed['override_allow'] = [{'name': 'owned-override', 'enabled': 'True', 'direction': 'Inbound', 'action': 'Allow'}]
        changed['override_allow'][0][field] = invalid
        negatives.append(changed)
    changed = copy.deepcopy(value)
    changed['rules'][0]['profile'] = 'Domain, Private'
    negatives.append(changed)
    return all(P.firewall_problems(item, program=program) for item in negatives)


def firewall_scopes():
    program = Path('C:/owned/fixture/never-executed.exe')
    for field, scoped in (('local_address', ['127.0.0.1']), ('remote_port', ['443']),
                          ('interface_alias', ['scoped-interface']), ('interface_type', 'Wireless'),
                          ('service', 'scoped-service'), ('package', 'scoped-package'),
                          ('authentication', 'Required'), ('encryption', 'Required'),
                          ('local_user', 'scoped-user'), ('remote_user', 'scoped-user'),
                          ('remote_machine', 'scoped-machine'), ('dynamic_transport', '1'),
                          ('owner', 'scoped-owner'), ('platforms', ['scoped-platform']),
                          ('remote_dynamic_keyword_addresses', ['scoped-dynamic-id']),
                          ('policy_app_id', 'scoped-policy-app'), ('package_family_name', 'scoped-family'),
                          ('enforcement', [16]), ('enforcement', [True]), ('primary_status', 3),
                          ('primary_status', True)):
        value = fw_fixture(program)
        value['rules'][0][field] = scoped
        if 'firewall-inbound-block-coverage' not in P.firewall_problems(value, program=program):
            return False
        del value['rules'][0][field]
        if 'firewall-inbound-block-coverage' not in P.firewall_problems(value, program=program):
            return False
    return True


def request_controls():
    calls = []
    held = SS.HeldKey(b'sm_' + b'A' * 30)
    class Connection:
        def __init__(self, host, port, *, timeout):
            calls.append({'host': host, 'port': port, 'timeout': timeout})
        def request(self, method, path, **kwargs):
            calls[-1].update(method=method, path=path, headers=kwargs['headers'])
        def connect(self):
            calls[-1]['connected'] = True
        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda size: b'{"owned":true}')
        def close(self):
            calls[-1]['closed'] = True
    if not refused(lambda: P.sm_request(43123, 'POST', '/v4/memories/list', connection_factory=Connection), P.SmRequestRefused):
        return False
    if calls:
        return False
    for port in (0, 65536, True, '43123'):
        if not refused(lambda: P.sm_request(port, 'GET', '/', key=held, connection_factory=Connection), P.SmRequestRefused):
            return False
    for path in ('https://example.invalid/private', '//example.invalid/private', '/\r\nX:bad'):
        if not refused(lambda: P.sm_request(43123, 'GET', path, key=held, connection_factory=Connection), P.SmRequestRefused):
            return False
    if calls:
        return False
    result = P.sm_request(43123, 'POST', '/v4/memories/list', key=held, foreign_host=True, body={}, connection_factory=Connection)
    return (result['status'] == 200 and len(calls) == 1 and calls[0]['host'] == '127.0.0.1'
            and calls[0]['port'] == 43123 and calls[0]['headers']['Host'] == 'example.invalid'
            and calls[0]['headers'][SS.KEY_HEADER] == held.header()[SS.KEY_HEADER] and calls[0]['closed'] is True)


def auth_controls():
    if P.auth_problems(200, 401, 401, 200):
        return False
    return (P.auth_problems(200, 200, 401, 200) == ['auth-foreign-host']
            and P.auth_problems(200, 401, 200, 200) == ['auth-wrong-key']
            and P.auth_problems(200, 401, 401, None) == ['blocked:auth-control'])


def request_deadline_controls():
    # Continuous fake fragments evade a per-read timeout; no socket is opened.
    held = SS.HeldKey(b'sm_' + b'D' * 30)
    for phase in ('request', 'headers', 'body'):
        released = threading.Event()
        instances, deadlines = [], []
        class Socket:
            def shutdown(self, how):
                released.set()
        def fragmented():
            limit = time.monotonic() + 2.0
            while not released.wait(0.002):
                if time.monotonic() >= limit:
                    raise TimeoutError('owned fragmented fake exceeded its safety ceiling')
            raise OSError('owned fake interrupted; no error text may escape the deadline')
        class Connection:
            def __init__(self, *args, **kwargs):
                self.sock = Socket()
                self.closed = False
                instances.append(self)
            def request(self, *args, **kwargs):
                if phase == 'request':
                    fragmented()
            def connect(self):
                pass
            def getresponse(self):
                if phase == 'headers':
                    fragmented()
                # Mimic HTTPConnection detaching its socket on Connection: close.
                self.sock = None
                return SimpleNamespace(status=200, read=lambda _: fragmented())
            def close(self):
                self.closed = True
        original = P._RequestDeadline
        def deadline_factory(*args):
            instance = original(*args)
            deadlines.append(instance)
            return instance
        started = time.monotonic()
        with mock.patch.object(P, '_RequestDeadline', side_effect=deadline_factory):
            try:
                P.sm_request(43123, 'GET', '/', key=held, timeout_s=0.05, connection_factory=Connection)
                return False
            except P.SmRequestRefused as exc:
                if str(exc) != 'request-deadline':
                    return False
        if (time.monotonic() - started >= 1.5 or not released.is_set()
                or len(instances) != 1 or not instances[0].closed
                or len(deadlines) != 1 or deadlines[0].thread.is_alive()):
            return False
    return True


def deadline_edge_controls():
    held = SS.HeldKey(b'sm_' + b'E' * 30)
    sent = []
    class SlowConnect:
        def __init__(self, *args, **kwargs):
            self.sock = None
        def connect(self):
            threading.Event().wait(0.08)
            self.sock = SimpleNamespace(shutdown=lambda _: None)
        def request(self, *args, **kwargs):
            sent.append(True)
        def close(self):
            self.sock = None
    if not refused(lambda: P.sm_request(43123, 'GET', '/', key=held, timeout_s=0.03,
                                       connection_factory=SlowConnect), P.SmRequestRefused) or sent:
        return False
    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = None
        def connect(self):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda _: b'{}')
        def close(self):
            pass
    original = P.json.loads
    def slow_parser(*args, **kwargs):
        threading.Event().wait(0.08)
        return original(*args, **kwargs)
    with mock.patch.object(P.json, 'loads', side_effect=slow_parser):
        if not refused(lambda: P.sm_request(43123, 'GET', '/', key=held, timeout_s=0.03,
                                           connection_factory=Connection), P.SmRequestRefused):
            return False
    # Deterministic independent proof of the configured duration, not a loose wall threshold.
    for configured in (0.4, 0.9):
        with mock.patch.object(P.time, 'monotonic', return_value=10.0):
            deadline = P._RequestDeadline(Connection(), configured)
            with mock.patch.object(deadline.done, 'wait', return_value=False) as wait:
                deadline._watch()
            if wait.call_count != 1 or abs(wait.call_args.args[0] - configured) > 1e-12:
                return False
        if not deadline.expired.is_set():
            return False
    return True


def owned_search_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-owned-roots-') as directory:
        root = Path(directory).resolve()
        arm = root / '_a8/control/supermemory-local'
        data = arm / 'data'
        data.mkdir(parents=True)
        (root / '_launch').mkdir()
        (root / '_witness').mkdir()
        contract = SimpleNamespace(runs_root=root)
        launch = L
        scanner = mock.Mock(return_value={'files': 0})
        digest = mock.Mock(return_value={'owned.bin': 'a' * 64})
        server = SimpleNamespace(key_search=scanner, data_digest=digest)
        if P.owned_key_search(contract, launch, server, run='control', held='owned', data_dir=data) != {'files': 0}:
            return False
        if scanner.call_args.args[0] != {'run': arm.parent, 'launch': root / '_launch', 'witness': root / '_witness'}:
            return False
        if scanner.call_args.kwargs['data_dir'] != data:
            return False
        if P.owned_data_digest(contract, launch, server, run='control', data_dir=data) != {'owned.bin': 'a' * 64}:
            return False
        for blocked_path in (root, root / '_a8', arm, data, root / '_launch', root / '_witness'):
            scanner.reset_mock()
            with mock.patch.object(launch, '_is_link', side_effect=lambda path: Path(path) == blocked_path):
                if not refused(lambda: P.owned_key_search(contract, launch, server, run='control', held='owned', data_dir=data), P.PreflightRefused):
                    return False
            if scanner.called:
                return False
        digest.reset_mock()
        with mock.patch.object(launch, '_is_link', side_effect=lambda path: Path(path) == root / '_a8'):
            if not refused(lambda: P.owned_data_digest(contract, launch, server, run='control', data_dir=data), P.PreflightRefused) or digest.called:
                return False
        scanner.reset_mock()
        if not refused(lambda: P.owned_key_search(contract, launch, server, run='control', held='owned', data_dir=root.parent / 'outside'), P.PreflightRefused) or scanner.called:
            return False
        for incomplete in ({'.': 'link'}, {'.': 'unreadable: PermissionError'}, {'a': None}):
            digest.return_value = incomplete
            if not refused(lambda: P.owned_data_digest(contract, launch, server, run='control', data_dir=data), P.PreflightRefused):
                return False
        held = SS.HeldKey(b'sm_' + b'F' * 30)
        value = held.header()[SS.KEY_HEADER].encode('ascii')
        (data / 'api-key').write_bytes(value)
        sibling = arm.parent / 'fw-unit'
        sibling.mkdir()
        (sibling / 'leak.txt').write_bytes(value)
        classified = P.owned_key_search(contract, launch, SS, run='control', held=held, data_dir=data)
        return (classified['files'] == 2 and classified['unreadable'] == [] and classified['links'] == []
                and classified['product_stored'] == [{'root': 'run', 'path': 'supermemory-local/data/api-key', 'encodings': ['utf-8']}]
                and classified['leaks'] == [{'root': 'run', 'path': 'fw-unit/leak.txt', 'encodings': ['utf-8']}])


def tree_controls():
    row = {'pid': 123, 'create_time': 7.5, 'exe': 'C:/owned/fake.exe', 'sha256': 'a' * 64}
    args = dict(pid=123, birth_time=7.5, binary_sha256='a' * 64)
    return (P.tree_problems([row], **args) == [] and bool(P.tree_problems([row, dict(row, pid=124)], **args))
            and bool(P.tree_problems([], **args)) and bool(P.tree_problems([dict(row, create_time=8)], **args))
            and bool(P.tree_problems([dict(row, sha256=None)], **args)))


def knob_controls():
    legal = {'NVT3_FAKE_LOCAL_BY': 'host'}
    return (P.check_fake_knobs(legal, test_only=True) is None
            and refused(lambda: P.check_fake_knobs(legal, test_only=False), P.PreflightRefused)
            and refused(lambda: P.check_fake_knobs({'NVT3_FAKE_UNKNOWN': 'x'}, test_only=True), P.PreflightRefused)
            and refused(lambda: P.check_fake_knobs({'OPENAI_API_KEY': 'owned'}, test_only=True), P.PreflightRefused))


def verdict_controls():
    fields = {name: {'ok': True} for name in P.FIELDS}
    check = [('owned', {'complete': True, 'native_hits': 0, 'fs_hits': 0, 'process_errors': 0, 'ancestor_found': 0})]
    if P.sm_verdict(fields, problems=[], checks=check, catcher_lines=0) != ('pass', []):
        return False
    fields['sm_openapi'] = {'ok': False, 'blocked': 'blocked:no-openapi'}
    if P.sm_verdict(fields, problems=[], checks=check, catcher_lines=0)[0] != 'blocked:no-openapi':
        return False
    for forged in ('pass', 'blocked:schema-missing:undeclared', ['pass'], True, 7):
        changed = copy.deepcopy(fields)
        changed['sm_openapi']['blocked'] = forged
        if P.sm_verdict(changed, problems=[], checks=check, catcher_lines=0)[0] != 'fail':
            return False
    for name in P.BOUNDARY:
        changed = copy.deepcopy(fields)
        changed[name] = {'ok': False}
        if P.sm_verdict(changed, problems=[], checks=check, catcher_lines=0)[0] != 'fail':
            return False
    for check in ([], [('owned', {'complete': False, 'native_hits': 0, 'fs_hits': 0})],
                  [('owned', {'complete': True, 'native_hits': False, 'fs_hits': 0})]):
        if P.sm_verdict(fields, problems=[], checks=check, catcher_lines=0)[0] != 'fail':
            return False
    fields.pop('sm_rss')
    return P.sm_verdict(fields, problems=[], checks=[('owned', {'complete': True, 'native_hits': 0, 'fs_hits': 0})], catcher_lines=0)[0] == 'fail'


def record_controls():
    held = SS.HeldKey(b'sm_' + b'B' * 30)
    sentinel = held.header()[SS.KEY_HEADER]
    with tempfile.TemporaryDirectory(prefix='nvt-sm-core-') as directory:
        root = Path(directory)
        dest = root / '_a8/control/supermemory-local/probe.json'
        launch = SimpleNamespace(_within=lambda path, parent: path.resolve().is_relative_to(parent.resolve()),
                                 _link_in_chain=lambda *args: False)
        other_tokens = ['sm_' + 'C' * length for length in (8, 15, 23, 30)]
        record = {'outcome': 'fail', 'fields': {'sm_key_search': {'ok': False}}, 'external': sentinel,
                  'non_held': other_tokens, 'external_keys': {token: 'value' for token in other_tokens},
                  sentinel: 'untrusted-key-name', 'nested': [sentinel]}
        P.write_sm_probe(dest, record, launch=launch, runs_root=root, held_keys=[held])
        raw = dest.read_bytes()
        parsed = json.loads(raw)
        if any(needle in raw for _, needle in held.needles()) or 'sm_key_search' not in parsed['fields']:
            return False
        if any(token.encode() in raw for token in other_tokens):
            return False
        if not refused(lambda: P.write_sm_probe(dest, record, launch=launch, runs_root=root, held_keys=[held]), FileExistsError):
            return False
        return dest.read_bytes() == raw


def actual_child_env_controls():
    bundle = load('v3_sm_bundle_for_actual_env_test', 'research/v3/sm_bundle.py')
    defaults = {name: {'default': None} for name in P.SERVER_ENV_NAMES}
    defaults.update({'SUPERMEMORY_EMBEDDING_PROVIDER': {'default': 'local'},
        'SUPERMEMORY_EMBEDDING_BASE_URL': {'default': 'OPENAI_BASE_URL'},
        'OPENAI_FAST_MODEL': {'default': 'OPENAI_MODEL'}, 'OPENAI_TEXT_MODEL': {'default': 'OPENAI_MODEL'},
        'OPENAI_MODEL': {'default': 'gpt-5.1'}, 'PORT': {'default': 'SUPERMEMORY_PORT'}})
    env_rec = {'reads': defaults, 'must_be_absent': ['ANTHROPIC_API_KEY', 'GROQ_API_KEY',
        'WORKERS_AI_API_KEY', 'CLOUDFLARE_ACCOUNT_ID', 'GOOGLE_VERTEX_PROJECT_ID', 'GOOGLE_VERTEX_LOCATION', 'GEMINI_API_KEY']}
    declared = SS.server_env(env_rec, port=43123, data_dir='D:/owned/data',
        llm_base_url='http://127.0.0.1:43124/u/control.u1/v1', llm_model='deepseek-flash',
        embed_base_url='http://127.0.0.1:43125/u/control.u1/v1', embed_model='nvt3-bge-m3', token='owned-proxy-token')
    actual = {**declared, **bundle.SPAWN_ENV, 'HOME': 'owned-home-sentinel'}
    child = SimpleNamespace(spawn_id='owned-control-spawn')
    spy = mock.Mock(return_value=child)
    launch = SimpleNamespace(spawn=spy)
    argv = ('D:/owned/never-executed.exe',)
    guard = P.GuardedLaunch(launch, bundle, env_rec, run='control', argv=argv, expected_by_unit={'u1': declared})
    record = {'role': 'server', 'stand': '_a8', 'run': 'control', 'arm': P.ARM, 'unit': 'u1'}
    if guard.spawn('owned-contract', argv, env=actual, record=record) is not child:
        return False
    if (spy.call_args.kwargs['env'] is not actual or len(guard.observed) != 1
            or guard.observed[0]['spawn_id'] != child.spawn_id):
        return False
    forbidden_values = [declared[name] for name in ('OPENAI_API_KEY', 'SUPERMEMORY_DATA_DIR',
                        'OPENAI_BASE_URL', 'SUPERMEMORY_EMBEDDING_BASE_URL')] + ['owned-home-sentinel']
    if any(value in json.dumps(guard.observed) for value in forbidden_values):
        return False
    negatives = []
    for name, value in (('OPENAI_BASE_URL', ''), ('OPENAI_MODEL', 'untrusted-model'),
            ('SUPERMEMORY_EMBEDDING_MODEL', 'untrusted-embedder'), ('SUPERMEMORY_EMBEDDING_DIMENSIONS', '1536'),
            ('anthropic_api_key', 'owned-alternate-token'), ('google_undocumented', 'owned-alternate-token'),
            ('GEMINI_API_KEY', 'owned-alternate-token'), ('PORT', '43199'), ('port', '43199'),
            ('OPENAI_FAST_MODEL', 'deepseek-flash'), ('WORKFLOW_ENGINE', 'rivet'),
            ('SUPERMEMORY_NO_OPEN', '0'), ('openai_base_url', declared['OPENAI_BASE_URL'])):
        negatives.append(dict(actual, **{name: value}))
    for bad in negatives:
        spy.reset_mock()
        if not refused(lambda: guard.spawn('owned-contract', argv, env=bad, record=record), P.PreflightRefused) or spy.called:
            return False
    for changed in (dict(record, run='other'), dict(record, arm='other'), dict(record, unit='unknown')):
        spy.reset_mock()
        if not refused(lambda: guard.spawn('owned-contract', argv, env=actual, record=changed), P.PreflightRefused) or spy.called:
            return False
    changed = dict(record, role='other', arm='other')
    for other_argv in ((argv[0], '--owned-extra-option'), (argv[0].upper(),), ('D:/owned/./never-executed.exe',)):
        spy.reset_mock()
        if not refused(lambda: guard.spawn('owned-contract', other_argv, env=actual, record=changed), P.PreflightRefused) or spy.called:
            return False
    with mock.patch.object(P.os.path, 'samefile', return_value=True):
        spy.reset_mock()
        if not refused(lambda: guard.spawn('owned-contract', ('D:/owned/alias.exe',), env=actual, record=changed), P.PreflightRefused) or spy.called:
            return False
    for returned in (None, SimpleNamespace()):
        spy.return_value = returned
        before = copy.deepcopy(guard.observed)
        if not refused(lambda: guard.spawn('owned-contract', argv, env=actual, record=record), P.PreflightRefused) or guard.observed != before:
            return False
    spy.side_effect = RuntimeError('owned delegate refusal sentinel')
    before = copy.deepcopy(guard.observed)
    if not refused(lambda: guard.spawn('owned-contract', argv, env=actual, record=record), RuntimeError) or guard.observed != before:
        return False
    return True


check('A8S-ACTUAL-ENV-1: actual child env is checked before spawn and forwarded unchanged; no values in observation', actual_child_env_controls)

check('A8S-FW-1: enabled profile coverage and overriding allow independently enforced', firewall_controls)
check('A8S-FW-SCOPE-1: scoped or absent address/port/interface/service/security evidence cannot prove blanket block', firewall_scopes)
check('A8S-AUTH-1: held-key refusal precedes connection and foreign Host still connects only127.0.0.1', request_controls)
check('A8S-AUTH-2: foreign host/wrong key cannot inherit functional control blockage', auth_controls)
check('A8S-DEADLINE-1: fragmented write/headers/body share one deadline, detach-safe shutdown and drained watchdog', request_deadline_controls)
check('A8S-DEADLINE-EDGE-1: late connect cannot send, late parse cannot pass, configured deadline has independent proof', deadline_edge_controls)
check('A8S-OWNED-ROOTS-1: fixed owned roots reject ancestor links/escapes before scanning and incomplete seals before comparison', owned_search_controls)
check('A8S-TREE-1: extra tree member or changed birth/binary cannot pass', tree_controls)
check('A8S-NOREAL-1: fake knobs closed and unavailable to real dependency mode', knob_controls)
check('A8S-ORDER-1: boundary/missing/incomplete witness evidence outranks functional blocked', verdict_controls)
check('A8S-REC-1: redact values and keys before single exclusive final write', record_controls)
def named_preflight_refusal(fn, reason):
    try:
        fn()
        return False
    except P.PreflightRefused as exc:
        return str(exc) == reason


def tool_baseline_controls():
    expected = frozenset({'searchMemories', 'readChunks', 'searchDocumentChunks', 'getMemoryDetails',
                          'linkMemories', 'forgetMemory', 'CreateMemory'})
    if L.TOOLS_ALLOWED_BASELINE.get('supermemory-local') != expected or frozenset(P.TOOLS) != expected:
        return False
    with tempfile.TemporaryDirectory(prefix='nvt-sm-tools-baseline-') as directory:
        root = Path(directory).resolve()
        contract = SimpleNamespace(runs_root=root)
        for changed in (frozenset(), expected - {'CreateMemory'}, expected | {'shell'},
                        frozenset(name.lower() for name in expected)):
            with mock.patch.dict(L.TOOLS_ALLOWED_BASELINE, {'supermemory-local': changed}):
                with mock.patch.object(P, 'bundle_record') as bundle_read:
                    if not named_preflight_refusal(lambda: P.preflight_static(contract, L, SimpleNamespace(),
                            run='baseline', d1_run='d1', fake_knobs={}, test_only=False), 'tools-baseline'):
                        return False
                    if bundle_read.called or list(root.iterdir()):
                        return False
    return True


check('A8S-TOOLS-BASELINE-1: committed seven-name baseline and preflight refusal precede input reads and labels', tool_baseline_controls)


class PreflightReadSpy:
    def __init__(self, stream, reads):
        self.stream, self.reads = stream, reads
    def __enter__(self):
        self.stream.__enter__()
        return self
    def __exit__(self, *args):
        return self.stream.__exit__(*args)
    def read(self, size=-1):
        self.reads.append(size)
        return self.stream.read(size)


def bundle_bound_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-bundle-bound-') as directory:
        root = Path(directory).resolve()
        path = root / '_facts/a8-sm-bundle/f2/bundle_facts.json'
        path.parent.mkdir(parents=True)
        # Otherwise-valid JSON: removing only the limit yields a different refusal.
        path.write_bytes(b'{}' + b' ' * P.RECORD_MAX_BYTES)
        original_open, reads, opened = Path.open, [], []
        def bounded_open(target, *args, **kwargs):
            opened.append(target)
            return PreflightReadSpy(original_open(target, *args, **kwargs), reads)
        with mock.patch.object(Path, 'open', bounded_open):
            good = named_preflight_refusal(lambda: P.bundle_record(SimpleNamespace(runs_root=root), L), 'bundle-record-limit')
        return good and opened == [path] and reads == [P.RECORD_MAX_BYTES + 1]


def embed_preflight_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-embed-preflight-') as directory:
        root = Path(directory).resolve()
        contract = SimpleNamespace(runs_root=root)
        path = root / '_d1tag/d1/record.json'
        path.parent.mkdir(parents=True)
        valid = {'run': 'd1', 'problems': [], 'tag': {'name': 'nvt3-bge-m3-d1:latest',
                 'digest': '561ce53b3730a15c5b08676cc985d4a0805d95353c6576e5ef982bc5cc3c5a65'}}
        raw = json.dumps(valid).encode('utf-8')
        path.write_bytes(raw)
        original_open = Path.open
        opened = []
        def recording_open(target, *args, **kwargs):
            opened.append(target)
            return original_open(target, *args, **kwargs)
        with mock.patch.object(Path, 'open', recording_open):
            pin = P.embed_record(contract, L, d1_run='d1')
        if opened != [path] or pin != {'tag': valid['tag']['name'], 'digest': valid['tag']['digest'],
                                      'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}:
            return False
        for value in (dict(valid, problems=['failed']), dict(valid, run='another'),
                      dict(valid, tag=dict(valid['tag'], digest='a' * 64)),
                      dict(valid, tag=dict(valid['tag'], name='another-model:latest'))):
            path.write_bytes(json.dumps(value).encode('utf-8'))
            if not refused(lambda: P.embed_record(contract, L, d1_run='d1'), P.PreflightRefused):
                return False
        path.write_bytes(raw)
        for linked in (root, root / '_d1tag', path.parent, path):
            opened.clear()
            with mock.patch.object(L, '_is_link', side_effect=lambda target: Path(target) == linked):
                with mock.patch.object(Path, 'open', recording_open):
                    if not refused(lambda: P.embed_record(contract, L, d1_run='d1'), P.PreflightRefused):
                        return False
            if opened:
                return False
        path.write_bytes(raw + b' ' * P.RECORD_MAX_BYTES)
        reads = []
        def bounded_open(target, *args, **kwargs):
            return PreflightReadSpy(original_open(target, *args, **kwargs), reads)
        with mock.patch.object(Path, 'open', bounded_open):
            good = named_preflight_refusal(lambda: P.embed_record(contract, L, d1_run='d1'), 'embed-record-limit')
        if not good or reads != [P.RECORD_MAX_BYTES + 1]:
            return False
        path.write_bytes(b'[' * 2000 + b'0' + b']' * 2000)
        if not refused(lambda: P.embed_record(contract, L, d1_run='d1'), P.PreflightRefused):
            return False
        # Parser recursion thresholds differ by Python version; exercise the exact
        # exceptional branch deterministically as well as the real nested input.
        with mock.patch.object(P.json, 'loads', side_effect=RecursionError):
            if not named_preflight_refusal(lambda: P.embed_record(contract, L, d1_run='d1'), 'embed-record-unreadable:RecursionError'):
                return False
        return all(refused(lambda name=name: P.embed_record(contract, L, d1_run=name), P.PreflightRefused)
                   for name in ('../d1', 'd1.other', '', None))


def static_preflight_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-static-preflight-') as directory:
        root = Path(directory).resolve()
        contract = SimpleNamespace(runs_root=root)
        # Fake pinned facts only; no actual binary, sockets or model lookup.
        facts = {'pin': {'binary': str(root / 'never-executed.exe')},
                 'workflow': {'default': 'direct', 'platform': 'win32', 'env': 'WORKFLOW_ENGINE'}}
        switches = {'SUPERMEMORY_NO_UPDATE_CHECK': '1', 'SUPERMEMORY_DISABLE_TELEMETRY': '1', 'SUPERMEMORY_NO_OPEN': '1'}
        bundle = SimpleNamespace(SPAWN_ENV=switches)
        baseline = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
        with mock.patch.object(P, 'bundle_record', return_value=facts) as bundle_read:
            with mock.patch.object(P, 'embed_record', return_value={'tag': 'pinned'}) as embed_read:
                prepared = P.preflight_static(contract, L, bundle, run='control', d1_run='d1', fake_knobs={}, test_only=False)
                if prepared['data_dir'] != root / '_a8/control/supermemory-local/data' or bundle_read.call_count != 1 or embed_read.call_count != 1:
                    return False
                if sorted(str(path.relative_to(root)) for path in root.rglob('*')) != baseline:
                    return False
                for changed in (dict(facts, workflow=dict(facts['workflow'], default='rivet')),
                                dict(facts, workflow=dict(facts['workflow'], platform='linux'))):
                    bundle_read.return_value = changed
                    if not refused(lambda: P.preflight_static(contract, L, bundle, run='control', d1_run='d1', fake_knobs={}, test_only=False), P.PreflightRefused):
                        return False
                bundle_read.return_value = facts
                for changed in (dict(switches, SUPERMEMORY_DISABLE_TELEMETRY='0'),
                                {'SUPERMEMORY_NO_UPDATE_CHECK': '1', 'SUPERMEMORY_DISABLE_TELEMETRY': '1'}):
                    if not refused(lambda: P.preflight_static(contract, L, SimpleNamespace(SPAWN_ENV=changed), run='control', d1_run='d1', fake_knobs={}, test_only=False), P.PreflightRefused):
                        return False
                bundle_read.reset_mock()
                if not refused(lambda: P.preflight_static(contract, L, bundle, run='control', d1_run='d1',
                                fake_knobs={'NVT3_FAKE_TOOLS': 'owned'}, test_only=False), P.PreflightRefused) or bundle_read.called:
                    return False
        return sorted(str(path.relative_to(root)) for path in root.rglob('*')) == baseline


check('A8S-EMBED-PRE-1: one bounded pinned embed-record read rejects ancestor links before opening any bytes', embed_preflight_controls)
check('A8S-BUNDLE-PRE-1: f2 bound rejects oversized valid JSON with one bounded read before hashing', bundle_bound_controls)
check('A8S-STATIC-PRE-1: workflow, switches and real-mode knob refusals precede live-label consumption', static_preflight_controls)

class BootClock:
    def __init__(self):
        self.now = 0.0
    def clock(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class BootJobs:
    def __init__(self):
        self.jobs = {701: 81}
        self.live = {81: [701], 99: [999]}
        self.killed = []
        self.broken = False
        self._handles_lock = threading.RLock()
        self._job_leases = {handle: SimpleNamespace(handle=handle, closed=False, uncertain=False)
                            for handle in self.live}
        self._root_leases = {701: self._job_leases[81]}
    def capture_root_lease(self, root):
        with self._handles_lock:
            lease = self._root_leases.get(root)
            if lease is None or self.jobs.get(root) != lease.handle or not self.active_lease(lease):
                raise P.PreflightRefused('declared root Job generation unavailable')
            return lease
    def capture_lease(self, handle):
        lease = self._job_leases.get(handle)
        if lease is None or not self.active_lease(lease):
            raise P.PreflightRefused('declared Job generation unavailable')
        return lease
    def active_lease(self, lease):
        with self._handles_lock:
            return self._job_leases.get(lease.handle) is lease and not lease.closed and not lease.uncertain
    def members_lease(self, lease):
        with self._handles_lock:
            if self.broken or not self.active_lease(lease):
                raise OSError('declared Job generation query refused')
            return list(self.live[lease.handle])
    def terminate_lease(self, lease):
        with self._handles_lock:
            if not self.active_lease(lease):
                return False
            self.killed.append(lease.handle)
            self.live[lease.handle] = []
            return True
    def drain_lease(self, lease):
        with self._handles_lock:
            if not self.active_lease(lease) or self.members_lease(lease):
                return False
            lease.closed = True
            self._job_leases.pop(lease.handle)
            for root, assigned in list(self._root_leases.items()):
                if assigned is lease:
                    del self._root_leases[root]
                    if self.jobs.get(root) == lease.handle:
                        del self.jobs[root]
            return True
    def members(self, pid):
        if self.broken:
            raise OSError('owned fake failure')
        return list(self.live[self.jobs[pid]])
    def terminate(self, pid):
        handle = self.jobs[pid]
        self.killed.append(handle)
        self.live[handle] = []
        return True


class DeclaredAcquisitionJobs(BootJobs, L.WinJobs):
    """Synthetic handles, using the actual acquisition/assignment control flow."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        BootJobs.__init__(self)
        self.ct, self.wt = ctypes, wintypes
        self._handles_lock = threading.RLock()
        self._owned_handles = {}
        self._job_leases = {}
        self._root_leases = {}
        self._capture_local = threading.local()
        self._handle_generations = {}
        self._uncertain_closes = {}
        self._resource_errors = 0
        self.k32 = SimpleNamespace(CreateJobObjectW=lambda *args: 111,
            SetInformationJobObject=lambda *args: True,
            AssignProcessToJobObject=lambda job, handle: True,
            CloseHandle=lambda handle: True)
    def _new_job(self):
        return L.WinJobs._new_job(self)


def boot_fixture(root, *, start_failure=False, post_spawn_failure=False):
    run, unit, port = 'boot-control', 'u1', 43199
    arm = root / '_a8' / run / P.ARM
    data = arm / 'data'
    # Exact launch.make_unit_dirs layout: sibling cwd and fake home.
    home, cwd = arm / (unit + '.home'), arm / unit
    home.mkdir(parents=True)
    cwd.mkdir()
    binary = root / 'bin/server.exe'
    job = BootJobs()
    native = SimpleNamespace(jobs=job, registered_roots={701: 25.0}, _identity=lambda pid: 25.0)
    child = SimpleNamespace(spawn_id='owned-spawn', process=SimpleNamespace(
        pid=701, stdout=object(), poll=mock.Mock(return_value=None), wait=mock.Mock(return_value=0)))
    boots, events = [], []
    guard = SimpleNamespace(argv=[str(binary)], expected={unit: {
        'SUPERMEMORY_PORT': str(port), 'SUPERMEMORY_DATA_DIR': str(data)}}, observed=[], children=[])
    dirs = SimpleNamespace(home=home, cwd=cwd)
    class Pump:
        def __init__(self, stream, path):
            self.error = None
            self.key = None
        def start(self):
            events.append('pump')
            if not boots or boots[-1].pump is not self:
                raise AssertionError('resource not retained before pump thread starts')
            if start_failure:
                raise OSError('owned fake pump start failure')
            return self
        def join(self, timeout):
            events.append('join')
            return True
    server = SimpleNamespace(RedactingPump=Pump, launch_spec=lambda *a, **kw: SimpleNamespace())
    def spawn(build, **kwargs):
        build(dirs)
        events.append('spawn')
        guard.children.append(child)
        if post_spawn_failure:
            raise P.PreflightRefused('actual-child-unobserved')
        guard.observed.append({'unit': unit, 'spawn_id': child.spawn_id})
        return child, dirs
    scheduler = SimpleNamespace(launch=guard, witnesses=SimpleNamespace(native=native, _check='owned-check'),
                                spawn_child=spawn)
    contract = SimpleNamespace(runs_root=root, polygon_root=root)
    args = (contract, L, server, SimpleNamespace(), scheduler, guard)
    options = dict(run=run, unit=unit, port=port, data_dir=data, boots=boots)
    return SimpleNamespace(**locals())


def retained_job_controls():
    jobs = BootJobs()
    retained = P.RetainedJob(SimpleNamespace(jobs=jobs), 701)
    jobs.jobs[701] = 99
    if retained.same_binding() or retained.members() != [701] or retained.terminate() is not True:
        return False
    return jobs.killed == [81] and jobs.live[99] == [999]


def boot_cleanup_controls():
    jobs = BootJobs()
    native = SimpleNamespace(jobs=jobs, _identity=lambda pid: 25.0)
    retained = P.RetainedJob(native, 701)
    jobs.broken = True
    process = SimpleNamespace(pid=701, poll=mock.Mock(return_value=None), wait=mock.Mock(side_effect=OSError))
    pump = SimpleNamespace(error='write-error', join=mock.Mock(return_value=False))
    boot = P.OwnedBoot(SimpleNamespace(process=process), pump, retained, native=native,
                       birth_time=25.0, port=43199, dirs=None)
    result = boot.close(SimpleNamespace(), graceful=False)
    return (jobs.killed == [81] and process.wait.call_count == 1 and pump.join.call_count == 1
            and result['complete'] is False and len(result['problems']) >= 3)


def boot_drain_deadline_controls():
    timing = BootClock()
    jobs = BootJobs()
    native = SimpleNamespace(jobs=jobs, _identity=lambda pid: 25.0)
    process = SimpleNamespace(pid=701, poll=lambda: 0)
    process.wait = lambda timeout: timing.sleep(min(6.0, timeout))
    retained = P.RetainedJob(native, 701)
    pump_times = []
    def joined(timeout):
        pump_times.append(timeout)
        timing.sleep(timeout)
        return True
    pump = SimpleNamespace(error=None, join=joined)
    boot = P.OwnedBoot(SimpleNamespace(process=process), pump, retained, native=native,
                       birth_time=25.0, port=43199, dirs=None)
    result = boot.close(SimpleNamespace(), graceful=False, drain_s=10,
                        clock=timing.clock, sleep=timing.sleep)
    return result['complete'] is True and timing.now <= 10.001 and len(pump_times) == 1


def boot_resource_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-boot-resources-') as directory:
        f = boot_fixture(Path(directory).resolve(), start_failure=True)
        if not refused(lambda: P.start_boot(*f.args, **f.options), OSError):
            return False
        if len(f.boots) != 1 or f.boots[0].pump is None or f.events != ['spawn', 'pump']:
            return False
        f.child.process.poll.return_value = 0
        result = f.boots[0].close(f.server, graceful=False)
        return result['complete'] is True and f.job.killed == [81] and f.events[-1] == 'join'


def boot_post_spawn_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-boot-post-spawn-') as directory:
        f = boot_fixture(Path(directory).resolve(), post_spawn_failure=True)
        if not refused(lambda: P.start_boot(*f.args, **f.options), P.PreflightRefused):
            return False
        if len(f.boots) != 1 or f.boots[0].child is not f.child:
            return False
        f.child.process.poll.return_value = 0
        result = f.boots[0].close(f.server, graceful=False)
        return result['complete'] is False and 'server-pump-unavailable' in result['problems'] and f.job.killed == [81]


def boot_declaration_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-boot-declaration-') as directory:
        f = boot_fixture(Path(directory).resolve())
        for changed in (dict(f.options, port=43200), dict(f.options, data_dir=f.data.parent / 'other')):
            if not refused(lambda: P.start_boot(*f.args, **changed), P.PreflightRefused) or f.events:
                return False
        f.dirs.home = f.root / 'foreign-home'
        if not refused(lambda: P.start_boot(*f.args, **f.options), P.PreflightRefused) or f.events or f.boots:
            return False
        return True


def boot_ready_controls():
    clock = BootClock()
    boot = SimpleNamespace(child=SimpleNamespace(process=SimpleNamespace(poll=lambda: None)),
                           identity_matches=lambda: True, port=43199)
    limits = []
    def late_request(*args, **kwargs):
        limits.append(kwargs['timeout_s'])
        clock.sleep(2.0)
        return {'status': 200}
    result = P.ready_boot(boot, ceiling_s=1.0, request=late_request, clock=clock.clock, sleep=clock.sleep)
    if result['ready'] is not False or limits != [1.0]:
        return False
    boot.identity_matches = lambda: False
    request = mock.Mock()
    result = P.ready_boot(boot, ceiling_s=1.0, request=request, clock=clock.clock, sleep=clock.sleep)
    if result['ready'] is not False or request.called:
        return False
    for mutate in ('exit', 'identity'):
        boot.identity_matches = lambda: True
        boot.child.process.poll = lambda: None
        def changing_request(*args, **kwargs):
            if mutate == 'exit':
                boot.child.process.poll = lambda: 0
            else:
                boot.identity_matches = lambda: False
            return {'status': 200}
        result = P.ready_boot(boot, ceiling_s=1.0, request=changing_request, clock=clock.clock, sleep=clock.sleep)
        if result['ready'] is not False:
            return False
    boot.identity_matches = lambda: True
    boot.child.process.poll = lambda: None
    return P.ready_boot(boot, ceiling_s=1.0, request=lambda *a, **kw: {'status': 200},
                        clock=clock.clock, sleep=clock.sleep)['ready'] is True


def boot_key_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-boot-key-') as directory:
        root = Path(directory).resolve()
        data = root / '_a8/key-control' / P.ARM / 'data'
        data.mkdir(parents=True)
        path = data / 'api-key'
        held = SS.HeldKey(b'sm_OWNED_TEST_KEY_123456')
        path.write_bytes(b'sm_OWNED_TEST_KEY_123456\n')
        contract = SimpleNamespace(runs_root=root)
        good = P.owned_key_file(contract, L, run='key-control', data_dir=data, held=held)
        if good != {'matched': True, 'sha12': held.sha12, 'form': held.form}:
            return False
        opened = mock.Mock(side_effect=AssertionError('must refuse nonregular before open'))
        with mock.patch.object(Path, 'is_file', return_value=False), mock.patch.object(Path, 'open', opened):
            if not refused(lambda: P.owned_key_file(contract, L, run='key-control', data_dir=data, held=held), P.PreflightRefused) or opened.called:
                return False
        path.write_bytes(b'x' * 513)
        return refused(lambda: P.owned_key_file(contract, L, run='key-control', data_dir=data, held=held), P.PreflightRefused)


def boot_grace_controls():
    timing = BootClock()
    jobs = BootJobs()
    native = SimpleNamespace(jobs=jobs, _identity=lambda pid: 25.0)
    process = SimpleNamespace(pid=701, poll=lambda: None)
    def wait(timeout):
        timing.sleep(timeout)
        raise P.subprocess.TimeoutExpired('owned-fake', timeout)
    process.wait = wait
    pump = SimpleNamespace(error=None, join=lambda timeout: True)
    boot = P.OwnedBoot(SimpleNamespace(process=process), pump, P.RetainedJob(native, 701),
                       native=native, birth_time=25.0, port=43199, dirs=None)
    legacy_stop = mock.Mock(side_effect=AssertionError('legacy descendant PID kill forbidden'))
    with mock.patch.object(P, '_signal_retained_root') as signal_root:
        result = boot.close(SimpleNamespace(stop=legacy_stop), graceful=True, drain_s=1,
                            clock=timing.clock, sleep=timing.sleep)
    return (signal_root.call_count == 1 and not legacy_stop.called and jobs.killed == [81]
            and result['how'] == 'killed' and result['complete'] is False and timing.now <= 1.001)


def boot_missing_job_controls():
    alive = [True]
    process = SimpleNamespace(pid=701, poll=lambda: None if alive[0] else 1,
                              wait=mock.Mock(return_value=1))
    pump = SimpleNamespace(error=None, join=mock.Mock(return_value=True))
    boot = P.OwnedBoot(SimpleNamespace(process=process), pump, None,
        native=SimpleNamespace(), birth_time=None, port=43199, dirs=None)
    def retained_kill(captured):
        if captured is not process:
            raise AssertionError('wrong retained root')
        alive[0] = False
    with mock.patch.object(P, '_kill_retained_root', side_effect=retained_kill) as killed:
        result = boot.close(SimpleNamespace(), graceful=False)
    if killed.call_count != 1 or result['root_exited'] is not True or result['complete'] is not False or result['members'] is not None:
        return False
    # A foreign/unknown object cannot use numeric PID termination in the real fallback.
    foreign = SimpleNamespace(pid=999, kill=mock.Mock())
    with mock.patch.object(P, '_owned_windows_process', return_value=False):
        return refused(lambda: P._kill_retained_root(foreign), P.PreflightRefused) and not foreign.kill.called


check('A8S-JOB-HANDLE-1: PID-map replacement cannot redirect captured Job termination', retained_job_controls)
check('A8S-BOOT-CLEANUP-1: membership failure cannot skip owned termination, wait or pump failure evidence', boot_cleanup_controls)
check('A8S-BOOT-DRAIN-1: wait, Job and pump drains share one monotonic ceiling', boot_drain_deadline_controls)
check('A8S-BOOT-RESOURCE-1: pump retained before thread start failure for outer cleanup', boot_resource_controls)
check('A8S-BOOT-POST-SPAWN-1: returned child retained even when guard rejects post-spawn evidence', boot_post_spawn_controls)
check('A8S-BOOT-DECLARATION-1: mismatched port/data and foreign output path refuse before spawn', boot_declaration_controls)
check('A8S-BOOT-READY-1: late response and changed identity cannot establish readiness', boot_ready_controls)
check('A8S-BOOT-KEY-1: bounded regular owned key-file comparison returns metadata only', boot_key_controls)
check('A8S-BOOT-GRACE-1: graceful wait shares deadline and escalation cannot call legacy descendant kills', boot_grace_controls)
check('A8S-BOOT-MISSING-JOB-1: missing Job still stops exact retained root while descendant evidence stays incomplete', boot_missing_job_controls)

def protocol_fixture(root, *, admission=True, authority=True, leak=False, stop_error=False, setup_error=False,
                     second_gate_refusal=False, late_key=False, pump_failure=False, key_wait=0.01,
                     process_errors=0, ancestor_found=0):
    contract = SimpleNamespace(runs_root=root, polygon_root=root)
    binary = root / 'bin/server.exe'
    facts = {'pin': {'binary': str(binary)},
             'workflow': {'default': 'direct', 'platform': 'win32', 'env': 'WORKFLOW_ENGINE'}}
    switches = {'SUPERMEMORY_NO_UPDATE_CHECK': '1', 'SUPERMEMORY_DISABLE_TELEMETRY': '1', 'SUPERMEMORY_NO_OPEN': '1'}
    bundle = SimpleNamespace(SPAWN_ENV=switches)
    events, acquired, state = [], [], {}
    def setup(contract, launch, prepared, run, resources):
        def closed(name, bad=False):
            def action():
                events.append('close:' + name)
                if bad:
                    raise OSError('owned injected cleanup failure')
                return True
            return action
        resources.append(('fake', closed('fake'), lambda result: result is True))
        resources.append(('proxy', closed('proxy', stop_error), lambda result: result is True))
        if setup_error:
            raise OSError('owned setup failure')
        f = boot_fixture(root)
        state['fixture'] = f
        for name in ('_launch', '_witness'):
            (root / name).mkdir(exist_ok=True)
        class Pump:
            def __init__(self, stream, path):
                self.key = SS.HeldKey(b'sm_OWNED_TEST_KEY_123456')
                self.error = None
                self.key_seen = threading.Event()
                self.key_seen.set()
            def start(self):
                events.append('pump')
                f.data.mkdir()
                (f.data / 'api-key').write_bytes(b'sm_OWNED_TEST_KEY_123456\n')
                if late_key:
                    self.key = None
                    self.key_seen.clear()
                if leak:
                    (f.home / 'leak.txt').write_bytes(b'sm_OWNED_TEST_KEY_123456')
                return self
            def join(self, timeout):
                events.append('pump-drained')
                if late_key:
                    self.key = SS.HeldKey(b'sm_OWNED_TEST_KEY_123456')
                    (f.home / 'late-leak.txt').write_bytes(b'sm_OWNED_TEST_KEY_123456')
                return not pump_failure
        def stop(child):
            events.append('stop')
            if f.scheduler.witnesses._check is None:
                raise AssertionError('server shutdown has no active witness')
            f.child.process.poll.return_value = 0
            f.job.live[81] = []
            return {'how': 'ctrl_break', 'alive': False, 'reused': [], 'unverified': []}
        def begin(cid):
            events.append('begin:' + cid)
            f.scheduler.witnesses._check = cid
        def end(cid):
            events.append('end:' + cid)
            f.scheduler.witnesses._check = None
            return {'complete': True, 'native': {name: (process_errors if name == 'process_errors' else 0)
                for name in ('hits', 'loopback_hits', 'listen_nonloopback', 'undetermined_loopback', 'process_errors')},
                'fs': {'fs_hits': 0}, 'ancestor_claude': {'found': ancestor_found}}
        f.scheduler.witnesses.begin_check = begin
        f.scheduler.witnesses.end_check = end
        f.server.RedactingPump, f.server.stop, f.server.HeldKey, f.server.key_search = Pump, stop, SS.HeldKey, SS.key_search
        acquired.append(f.server)
        resources.append(('gate', closed('gate'), lambda result: result is True))
        def request(port, method, path, **options):
            events.append('request')
            if path == '/':
                return {'status': 200}
            if options.get('keyed') is False and not options.get('foreign_host'):
                return {'status': 200}
            return {'status': 401 if options.get('foreign_host') or options.get('key') != state.get('key', f.current_key) else 200}
        # Match the synthetic held key independently of proxy/server record objects.
        f.current_key = SS.HeldKey(b'sm_OWNED_TEST_KEY_123456')
        gate_calls = []
        def admits():
            gate_calls.append(True)
            if len(gate_calls) > 1 and f.scheduler.witnesses._check is None:
                raise AssertionError('live server phase transition has no active witness')
            return admission and not (second_gate_refusal and len(gate_calls) > 1)
        runtime = SimpleNamespace(
            gate=SimpleNamespace(admits_new_unit=admits),
            read_firewall=lambda unit, binary: {'complete': True, 'problems': [], 'facts': fw_fixture(binary)},
            policy_authority=lambda fw: authority, witnesses=f.scheduler.witnesses,
            scheduler_module=SimpleNamespace(), scheduler=f.scheduler, guarded=f.guard, port=f.port,
            ready_ceiling_s=1.0, key_wait_s=key_wait, request=request,
            declare_listener=lambda boot, fw: events.append('declare') is None,
            listeners=lambda boot: [{'pid': boot.pid, 'host': '0.0.0.0', 'port': boot.port, 'complete': True}],
            tree=lambda pid: [{'pid': pid, 'create_time': 25.0, 'sha256': P.BINARY_SHA256, 'exe': str(binary)}],
            version=lambda boot: P.sm_field('0.0.8', ok=True), check_summary=lambda result:
                {'complete': result['complete'], 'native_hits': result['native']['hits'], 'fs_hits': result['fs']['fs_hits']},
            auth_target=lambda: ('POST', '/owned-pinned-auth-control', {}), catcher_lines=lambda: 0)
        state['runtime'] = runtime
        return runtime
    # The driver uses this dispatcher, so the actual OwnedBoot/start_boot implementations run.
    class ServerDispatch:
        HeldKey = SS.HeldKey
        key_search = staticmethod(SS.key_search)
        def launch_spec(self, *args, **kwargs):
            return acquired[-1].launch_spec(*args, **kwargs)
        def RedactingPump(self, *args, **kwargs):
            return acquired[-1].RedactingPump(*args, **kwargs)
        def stop(self, *args, **kwargs):
            return acquired[-1].stop(*args, **kwargs)
    deps = SimpleNamespace(test_only=True, owner_home=root / 'owner-metadata', setup=setup)
    def run_probe():
        with mock.patch.object(P, 'bundle_record', return_value=facts), mock.patch.object(P, 'embed_record', return_value={'tag': 'owned', 'digest': 'a'*64}):
            with mock.patch.object(P, '_signal_retained_root', side_effect=lambda boot: acquired[-1].stop(boot.child)):
                return P.run_sm_p0p2(contract, L, bundle, ServerDispatch(), run='boot-control', d1_run='d1', deps=deps)
    return SimpleNamespace(**locals())


def protocol_refusal_controls():
    for admission, authority, reason in ((False, True, 'incident-gate'), (True, False, 'firewall-evidence-gap')):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-refusal-') as directory:
            f = protocol_fixture(Path(directory).resolve(), admission=admission, authority=authority)
            result = f.run_probe()
            dest = f.root / '_a8/boot-control' / P.ARM / 'probe.json'
            if result['outcome'] != 'refused:' + reason or not dest.is_file() or 'pump' in f.events:
                return False
            if not refused(f.run_probe, P.PreflightRefused):
                return False
            if f.events[-3:] != ['close:gate', 'close:proxy', 'close:fake']:
                return False
    return True


def protocol_connected_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-connected-') as directory:
        f = protocol_fixture(Path(directory).resolve())
        result = f.run_probe()
        if any(result['fields'][name]['ok'] is not True for name in P.P0P2_FIELDS):
            return False
        if result['outcome'] != 'fail' or not any(reason.startswith('missing-fields:') for reason in result['reasons']):
            return False
        if result['fields']['sm_stop']['ok'] is not True or result['fields']['sm_key_search']['ok'] is not True:
            return False
        if f.events.index('pump') >= f.events.index('declare') or f.events.index('declare') >= f.events.index('request'):
            return False
        if not f.events.index('stop') < f.events.index('pump-drained') < f.events.index('close:gate') < f.events.index('close:proxy') < f.events.index('close:fake'):
            return False
        raw = (f.root / '_a8/boot-control' / P.ARM / 'probe.json').read_bytes()
        return b'sm_OWNED_TEST_KEY_123456' not in raw and f.acquired[-1].RedactingPump is not None


def protocol_cleanup_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-cleanup-') as directory:
        f = protocol_fixture(Path(directory).resolve(), admission=False, stop_error=True)
        result = f.run_probe()
        if result['outcome'] != 'fail' or not any('resource-finalization:proxy:OSError' == problem for problem in result['problems']):
            return False
        if f.events[-1] != 'close:fake':
            return False
    with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-setup-') as directory:
        f = protocol_fixture(Path(directory).resolve(), setup_error=True)
        result = f.run_probe()
        return result['outcome'] == 'fail' and f.events == ['close:proxy', 'close:fake']


def protocol_leak_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-leak-') as directory:
        f = protocol_fixture(Path(directory).resolve(), leak=True)
        result = f.run_probe()
        return (result['outcome'] == 'fail' and result['fields']['sm_key_search']['ok'] is False
                and 'key-search-incomplete-or-leak' in result['problems']
                and f.events[-1] == 'close:fake')


def protocol_write_failure_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-write-') as directory:
        f = protocol_fixture(Path(directory).resolve())
        captured = []
        original_start = P.start_boot
        def tracked_start(*args, **kwargs):
            boot = original_start(*args, **kwargs)
            captured.append(boot)
            return boot
        with mock.patch.object(P, 'start_boot', tracked_start), mock.patch.object(P, 'write_sm_probe', side_effect=OSError):
            if not refused(f.run_probe, OSError):
                return False
        return len(captured) == 1 and captured[0].pump.key is None and captured[0].closed is True and f.events[-1] == 'close:fake'


check('A8S-LIVE-REFUSAL-1: gate/authority refusal writes one consumed-label record and closes all owned resources', protocol_refusal_controls)
check('A8S-PROTOCOL-1: connected scheduler/guard/pump/auth/stop/search path cannot pass absent P3-P5', protocol_connected_controls)
check('A8S-PROTOCOL-CLEANUP-1: partial setup and failing proxy close cannot skip other cleanup or become refusal success', protocol_cleanup_controls)
check('A8S-PROTOCOL-LEAK-1: final actual SF31 scan detects owned-home leak after all drains', protocol_leak_controls)
check('A8S-PROTOCOL-WRITE-1: final write failure still closes owned resources and drops held keys', protocol_write_failure_controls)


def protocol_transition_controls():
    for refused_gate in (False, True):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-transition-') as directory:
            f = protocol_fixture(Path(directory).resolve(), second_gate_refusal=refused_gate)
            result = f.run_probe()
            check = result['fields'].get('sm_egress_boot')
            if not check or check['ok'] is not True or check['value']['coverage'] != 'boot-auth-stop':
                return False
            if result['fields']['sm_stop']['ok'] is not True or any('AssertionError' in issue for issue in result['problems']):
                return False
            ends = [index for index, event in enumerate(f.events) if event.startswith('end:')]
            if len(ends) != 1 or f.events.index('pump-drained') >= ends[0]:
                return False
            if refused_gate and not any(reason == 'live-refusal:incident-gate' for reason in result['reasons']):
                return False
    return True


def protocol_late_key_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-late-key-') as directory:
        f = protocol_fixture(Path(directory).resolve(), late_key=True)
        result = f.run_probe()
        scan = result['fields']['sm_key_search']
        if scan['ok'] is not False or not scan['value']['findings'] or not scan['value']['findings'][0]['leaks']:
            return False
    with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-undrained-') as directory:
        f = protocol_fixture(Path(directory).resolve(), pump_failure=True)
        result = f.run_probe()
        scan = result['fields']['sm_key_search']
        return scan['ok'] is False and scan['value']['writers_complete'] is False and result['outcome'] == 'fail'


def protocol_partial_label_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-label-') as directory:
        f = protocol_fixture(Path(directory).resolve())
        arm = f.root / '_a8/boot-control' / P.ARM
        original = Path.mkdir
        faults = []
        def mkdir(path, *args, **kwargs):
            if path == arm and not faults:
                faults.append(True)
                raise OSError('owned injected arm creation failure')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'mkdir', mkdir):
            result = f.run_probe()
        return result['outcome'] == 'fail' and (arm / 'probe.json').is_file() and not f.events and faults == [True]


def protocol_key_wait_controls():
    for value in (None, True, -1, 0, 11, float('inf'), float('nan')):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-protocol-key-wait-') as directory:
            f = protocol_fixture(Path(directory).resolve(), key_wait=value)
            result = f.run_probe()
            if result['outcome'] != 'refused:server-key-wait-ceiling' or 'pump' in f.events:
                return False
    return True


check('A8S-PROTOCOL-TRANSITION-1: same active witness covers gate transition, auth and stop including second-gate refusal', protocol_transition_controls)
check('A8S-PROTOCOL-LATE-KEY-1: post-drain key harvest finds late leak and incomplete writers cannot pass SF31', protocol_late_key_controls)
check('A8S-PROTOCOL-LABEL-1: partial live-label creation still attempts terminal owned FAIL record', protocol_partial_label_controls)
check('A8S-PROTOCOL-KEY-WAIT-1: None/bool/nonfinite/unbounded key wait refuses before product spawn', protocol_key_wait_controls)

def pipe_pump_controls():
    import io
    raw = b'owned-readonly-output' * 6000
    pump = P.BoundedPipePump(io.BytesIO(raw), limit=17)
    pump.start()
    if not pump.join(1) or bytes(pump.raw) != raw[:17] or pump.count != len(raw) or pump.error != 'output-limit':
        return False
    if pump.digest.hexdigest() != hashlib.sha256(raw).hexdigest():
        return False
    broken = SimpleNamespace(read=mock.Mock(side_effect=OSError('owned failure')))
    pump = P.BoundedPipePump(broken, limit=17)
    pump.start()
    return pump.join(1) is True and pump.error == 'OSError' and pump.count == 0


def readonly_runtime_controls():
    import io
    binary = Path('D:/owned/readonly-fixture/server.exe')
    for broken in ('none', 'witness', 'output'):
        jobs = BootJobs()
        jobs.live[81] = []
        native = SimpleNamespace(jobs=jobs, registered_roots={701: 25.0})
        raw = json.dumps(fw_fixture(binary)).encode('utf-8')
        if broken == 'output':
            raw = b'{'  # rc0 alone cannot admit invalid output.
        process = SimpleNamespace(pid=701, stdout=io.BytesIO(raw), wait=lambda timeout: 0, poll=lambda: 0)
        events, resources, captured = [], [], []
        def end(cid):
            events.append('end')
            return {'complete': True, 'native': {name: (1 if broken == 'witness' and name == 'hits' else 0)
                for name in ('hits', 'loopback_hits', 'listen_nonloopback', 'undetermined_loopback', 'process_errors')},
                'fs': {'fs_hits': 0}, 'ancestor_claude': {'found': 0}}
        witnesses = SimpleNamespace(native=native, begin_check=lambda cid: events.append('begin'), end_check=end)
        def spawn(build, **kwargs):
            captured.append((build(SimpleNamespace()), kwargs))
            return SimpleNamespace(process=process), SimpleNamespace()
        scheduler = SimpleNamespace(witnesses=witnesses, spawn_child=spawn, launch=SimpleNamespace())
        modules = SimpleNamespace(SC=SimpleNamespace(LaunchSpec=lambda **kwargs: SimpleNamespace(**kwargs)))
        result = P.read_firewall_real(SimpleNamespace(), L, modules, scheduler, run='owned-read', unit='fw-p0', binary=binary, resources=resources)
        spec, who = captured[0]
        if (who != {'role': L.FW_READ_ROLE, 'stand': '_a8', 'run': 'owned-read', 'arm': 'fw-read', 'unit': 'fw-p0'}
                or spec.argv != (L.FW_READ_BINARY, *L.FW_READ_ARGV) or spec.requirement != 'required'
                or spec.stdio != 'server' or spec.declared != {L.FW_READ_PROGRAM_ENV: str(binary)}):
            return False
        if result['complete'] is not (broken == 'none') or events != ['begin', 'end'] or jobs.killed != [81]:
            return False
        if len(resources) != 1 or resources[0][2](resources[0][1]()) is not True:
            return False
    return True


def native_listener_controls():
    boot = SimpleNamespace(pid=701, port=43199, identity_matches=lambda: True)
    row = (701, '0.0.0.0', 43199, None, None, 'LISTEN')
    sampler = SimpleNamespace(connections=lambda pids: ([row], 0), listeners=lambda: {('tcp', 43199): {701}})
    native = SimpleNamespace(sampler=sampler, _lock=threading.RLock())
    good = P.native_listeners(native, boot)
    if P.listener_problems(good, pid=701, port=43199):
        return False
    sampler.listeners = lambda: {('tcp', 43199): {701, 999}}
    if not P.listener_problems(P.native_listeners(native, boot), pid=701, port=43199):
        return False
    sampler.listeners = lambda: None
    if not refused(lambda: P.native_listeners(native, boot), P.PreflightRefused):
        return False
    sampler.listeners = lambda: {('tcp', 43199): {701}}
    identity = iter((True, False))
    boot.identity_matches = lambda: next(identity)
    return refused(lambda: P.native_listeners(native, boot), P.PreflightRefused)


def listener_snapshot_controls():
    cache = {('tcp', 43199): {701}}
    state = {'held': False}
    class Lock:
        def __enter__(self):
            state['held'] = True
        def __exit__(self, *args):
            state['held'] = False
            # Model the ticker replacing the same cached owner's set just after unlock.
            cache[('tcp', 43199)].clear()
            cache[('tcp', 43199)].add(999)
    def connections(pids):
        if not state['held']:
            cache[('tcp', 43199)] = {999}
        return [(701, '0.0.0.0', 43199, None, None, 'LISTEN')], 0
    boot = SimpleNamespace(pid=701, port=43199, identity_matches=lambda: True)
    native = SimpleNamespace(_lock=Lock(), sampler=SimpleNamespace(connections=connections, listeners=lambda: cache))
    rows = P.native_listeners(native, boot)
    return not P.listener_problems(rows, pid=701, port=43199) and cache[('tcp', 43199)] == {999}


def runtime_cli_controls():
    argv = ['--run', 'owned-control', '--proxy-python', 'D:/owned/never-executed/python.exe']
    entry = mock.Mock(return_value={'outcome': 'pass'})
    if P.main(argv, entry=entry) != 2 or entry.called:
        return False
    if P.main(argv + ['--go', '--bundle-run', 'other'], entry=entry) != 2 or entry.called:
        return False
    for outcome, expected in (('pass', 0), ('refused:firewall-evidence-gap', 2), ('fail', 1),
                              ('blocked:auth-control', 1), ('PASS', 1), ('passed', 1), (None, 1)):
        entry.return_value = {'outcome': outcome}
        if P.main(argv + ['--go'], entry=entry) != expected:
            return False
    with mock.patch.dict(P.os.environ, {'NEVERTWICE_SANDBOX': '1'}):
        if P.main(argv + ['--go']) != 2:
            return False
    return True


check('A8S-RUNTIME-PIPE-1: bounded retained bytes still drain/hash entire helper output and preserve read errors', pipe_pump_controls)
check('A8S-RUNTIME-FW-1: exact fw-read role/env/argv and Job drain require complete output/witness evidence', readonly_runtime_controls)
check('A8S-RUNTIME-LISTENER-1: actual witness sampler refuses unknown/shared owner tables and changed identity', native_listener_controls)
check('A8S-RUNTIME-SNAPSHOT-1: shared witness lock and immutable owner snapshot resist bounded cache interleaving', listener_snapshot_controls)
check('A8S-RUNTIME-CLI-1: explicit GO, f2 and sandbox refusal precede production entry; only exact pass is rc0', runtime_cli_controls)


def runtime_factory_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-runtime-factory-') as directory:
        root = Path(directory).resolve()
        arm = root / '_a8/factory-control' / P.ARM
        arm.mkdir(parents=True)
        contract = SimpleNamespace(runs_root=root)
        bundle = load('v3_sm_bundle_for_factory_test', 'research/v3/sm_bundle.py')
        defaults = {name: {'default': None} for name in P.SERVER_ENV_NAMES}
        defaults.update({'SUPERMEMORY_EMBEDDING_PROVIDER': {'default': 'local'},
            'SUPERMEMORY_EMBEDDING_BASE_URL': {'default': 'OPENAI_BASE_URL'},
            'OPENAI_FAST_MODEL': {'default': 'OPENAI_MODEL'}, 'OPENAI_TEXT_MODEL': {'default': 'OPENAI_MODEL'},
            'OPENAI_MODEL': {'default': 'gpt-5.1'}, 'PORT': {'default': 'SUPERMEMORY_PORT'}})
        env_rec = {'reads': defaults, 'must_be_absent': ['ANTHROPIC_API_KEY', 'GROQ_API_KEY',
            'WORKERS_AI_API_KEY', 'CLOUDFLARE_ACCOUNT_ID', 'GOOGLE_VERTEX_PROJECT_ID', 'GOOGLE_VERTEX_LOCATION', 'GEMINI_API_KEY']}
        resources, events, upstreams = [], [], []
        class Fake:
            def __init__(self, *args, **kwargs):
                self.port = 44000 + len(upstreams)
                self._thread = SimpleNamespace(join=lambda seconds: None, is_alive=lambda: False)
                upstreams.append(self)
                kwargs['resources'].append(('fake-owned', self.close, lambda row: row.get('complete') is True))
            def close(self):
                events.append('fake-close')
                return {'complete': True}
        native = SimpleNamespace(jobs=BootJobs())
        launch = SimpleNamespace(NativeEgressWitness=lambda: native, FsWitness=lambda watched: object(),
            _within=L._within, _link_in_chain=L._link_in_chain,
            watched_set=lambda contract: [], Witnesses=lambda contract, **kwargs: SimpleNamespace(native=kwargs['native']),
            make_unit_dirs=L.make_unit_dirs, spawn_proxy=mock.Mock(side_effect=AssertionError('no actual spawn')))
        proxy = SimpleNamespace(control=SimpleNamespace(counters=lambda: {}, _auth={'Authorization': 'Bearer owned-control'}), run_dir=arm / '_proxy/run',
            ports={'scheduler': 44010, 'control': 44015, 'j3': 44016,
                'arms': {P.ARM: {'write': 44011, 'reader': 44012, 'ollama': 44013, 'catcher': 44014},
                         'owned-catcher': {'catcher': 44017}}},
            tokens={'scheduler': 'owned-scheduler-token', P.ARM: 'owned-proxy-token'})
        def config(arms, **kwargs):
            if kwargs['test_upstream']['host'] != '127.0.0.1' or kwargs['test_upstream']['tls'] is not False or kwargs['test_ollama_upstream'][0] != '127.0.0.1':
                raise AssertionError('nonloopback upstream')
            return kwargs
        def proxy_start(*args, **kwargs):
            resources.append(('proxy-owned', lambda: {'complete': True}, lambda row: row.get('complete') is True))
            return proxy
        rp = SimpleNamespace(PINNED_MODEL='deepseek-flash', PROVIDER_TRANSPORT='openai-compatible',
            build_config=config, build_secrets=lambda *a, **kw: {}, start=proxy_start,
            stop=lambda handle: {'rc': 0, 'killed': False, 'shutdown_error': None},
            url=lambda h, arm, run, unit, role='write', suffix='':
                f'http://127.0.0.1:{h.ports["arms"][arm][role]}/u/{run}.{unit}{suffix}',
            catcher_url=lambda h: 'http://127.0.0.1:44014', post=lambda *a: None)
        class Driver:
            def __init__(self, *a, **kw):
                self.problems = []
                self.poll_s = 1.0
            def poll(self):
                events.append('gate-poll')
            def halt_kind(self):
                return None
            def start(self):
                events.append('gate-start')
            def stop(self):
                events.append('gate-stop')
        class Gate:
            def __init__(self, stop, driver):
                self.stop, self.driver = stop, driver
            def admits_new_unit(self):
                return True
        modules = SimpleNamespace(U=SimpleNamespace(FakeDeepSeek=Fake, FakeOllama=Fake), RP=rp,
            RV=SimpleNamespace(boundary_canaries=lambda *a: {'canaries': [], 'proxy': [],
                'scheduler': {'canaries': [], 'home_canaries': None}}, probe=lambda *a, **kw: lambda: {'model': 'deepseek-flash'}),
            SC=SimpleNamespace(SystemClock=lambda: object(), Scheduler=lambda *a, **kw:
                SimpleNamespace(launch=a[3], witnesses=kw['witnesses'])),
            SL=load('v3_status_for_factory_test', 'research/v3/status_log.py'), G=SimpleNamespace(GateDriver=Driver),
            IN=SimpleNamespace(IncidentGate=lambda: object()), AB=SimpleNamespace(StopGate=lambda *a: object()),
            IV=SimpleNamespace(check_summary=lambda result: result), AC=SimpleNamespace(load_proxy=lambda path: SimpleNamespace(catcher=[])))
        probe = SimpleNamespace(probe_modules=lambda: modules, ProbeGate=Gate,
                                _mod=lambda *args: load('v3_rest_for_factory_fixture', 'research/v3/arms/_rest.py'))
        server = SimpleNamespace(server_env=SS.server_env, free_port=lambda: 44020, tree=lambda pid: [])
        deps = P.real_sm_deps(contract, launch, probe, bundle, server, environ={},
            proxy_python=root / 'never-executed-python.exe', owner_home=root / 'owner-metadata')
        if deps.test_only is not False or upstreams or events:
            return False
        static = {'arm_dir': arm, 'binary': root / 'never-executed-server.exe', 'data_dir': arm / 'data',
                  'embed': {'tag': P.EMBED_TAG, 'digest': P.EMBED_DIGEST}, 'bundle': {'env': env_rec}}
        try:
            with mock.patch.object(P, 'OwnedUpstream', Fake):
                runtime = deps.setup(contract, launch, static, 'factory-control', resources)
            declared = runtime.guarded.expected['u1']
            if (runtime.scheduler.launch is not runtime.guarded or declared['SUPERMEMORY_EMBEDDING_DIMENSIONS'] != '1024'
                    or declared['OPENAI_BASE_URL'] != 'http://127.0.0.1:44011/u/factory-control.u1/v1'
                    or declared['SUPERMEMORY_EMBEDDING_BASE_URL'] != 'http://127.0.0.1:44013/u/factory-control.u1/v1'
                    or runtime.policy_authority({'complete': True}) is not False):
                return False
            if (runtime.auth_target() != ('POST', '/v4/memories/list',
                    {'containerTags': ['factory-control_u1'], 'limit': 1})
                    or runtime.version(None)['ok'] is not False):
                return False
            for collided in (*range(44010, 44018), 44020):
                server.free_port = lambda: collided
                if not refused(runtime.prepare_boot2, P.PreflightRefused) or set(runtime.guarded.expected) != {'u1'}:
                    return False
            server.free_port = lambda: 44021
            if runtime.prepare_boot2() != 44021 or runtime.guarded.expected['u1-boot2'] != dict(declared, SUPERMEMORY_PORT='44021'):
                return False
        finally:
            results = [accepted(close()) for _, close, accepted in reversed(resources)]
        raw = (root / '_a8/factory-control/STATUS').read_text(encoding='utf-8')
        return (results == [True] * 6 and events == ['gate-poll', 'fake-close', 'fake-close']
                and ' ABORT ' in raw and ' BLOCK _a8/p0p2 END ' in raw and ' STAND _a8 END ' in raw
                and not launch.spawn_proxy.called)


check('A8S-RUNTIME-FACTORY-1: deferred production wiring keeps actual9 env/hl8/embed guard, pinned auth body, and authority refusal', runtime_factory_controls)


def borrowed_firewall_fixture(*, changed=False, restarted=False, ended=False):
    import io
    binary = Path('D:/owned/borrowed-firewall/server.exe')
    jobs = BootJobs()
    jobs.live[81] = []
    native = SimpleNamespace(jobs=jobs, registered_roots={701: 25.0})
    native._thread = SimpleNamespace(is_alive=lambda: True)
    native._stop, native._t0 = threading.Event(), 5.0
    raw = json.dumps(fw_fixture(binary)).encode('utf-8')
    process = SimpleNamespace(pid=701, stdout=io.BytesIO(raw), wait=lambda timeout: 0, poll=lambda: 0)
    events, resources = [], []
    witnesses = SimpleNamespace(native=native, containers=(), _check='owned-parent', _before={}, _before_rows={}, _tags={},
        begin_check=lambda cid: events.append('begin:' + cid),
        end_check=lambda cid: events.append('end:' + cid))
    def spawn(build, **kwargs):
        build(SimpleNamespace())
        events.append('spawn')
        if changed:
            witnesses._check = 'unexpected-parent'
        if restarted in {True, 'baseline'}:
            witnesses._before = {}
        if restarted in {True, 'thread'}:
            native._thread = SimpleNamespace(is_alive=lambda: True)
        if restarted == 'time':
            native._t0 = 6.0
        if ended:
            native._stop.set()
        return SimpleNamespace(process=process), SimpleNamespace()
    scheduler = SimpleNamespace(witnesses=witnesses, spawn_child=spawn, launch=SimpleNamespace())
    modules = SimpleNamespace(SC=SimpleNamespace(LaunchSpec=lambda **kwargs: SimpleNamespace(**kwargs)))
    def invoke(**options):
        return P.read_firewall_real(None, L, modules, scheduler, run='borrowed-control', unit='fw-p4',
            binary=binary, resources=resources, **options)
    return SimpleNamespace(**locals())


def borrowed_firewall_controls():
    f = borrowed_firewall_fixture()
    result = f.invoke(parent_check='owned-parent')
    if (result['complete'] is not True or result['check'] is not None or f.events != ['spawn']
            or f.witnesses._check != 'owned-parent' or f.jobs.killed != [81]):
        return False
    if result['coverage'] != {'parent_check': 'owned-parent', 'pending_parent_verdict': True, 'complete': False}:
        return False
    return len(f.resources) == 1 and f.resources[0][2](f.resources[0][1]()) is True


def borrowed_firewall_refusal_controls():
    for bad in ('other-parent', '', True, 'x' * 257):
        f = borrowed_firewall_fixture()
        if not refused(lambda: f.invoke(parent_check=bad), P.PreflightRefused) or f.events or f.resources:
            return False
    f = borrowed_firewall_fixture()
    f.native._stop.set()
    if not refused(lambda: f.invoke(parent_check='owned-parent'), P.PreflightRefused) or f.events or f.resources:
        return False
    f = borrowed_firewall_fixture()
    f.native._thread = SimpleNamespace(is_alive=lambda: False)
    if not refused(lambda: f.invoke(parent_check='owned-parent'), P.PreflightRefused) or f.events or f.resources:
        return False
    f = borrowed_firewall_fixture()
    if not refused(f.invoke, P.PreflightRefused) or f.events or f.resources:
        return False
    for options in ({'changed': True}, {'restarted': 'baseline'}, {'restarted': 'thread'},
                    {'restarted': 'time'}, {'ended': True}):
        f = borrowed_firewall_fixture(**options)
        result = f.invoke(parent_check='owned-parent')
        if not (result['complete'] is False and result['problems'] == ['firewall-parent-check-changed']
                and result['check'] is None and result['coverage']['complete'] is False
                and f.events == ['spawn'] and len(f.resources) == 1
                and f.resources[0][2](f.resources[0][1]()) is True):
            return False
    return True


check('A8S-RUNTIME-FW-PARENT-1: retained readonly helper borrows explicit parent without restarting or finalizing continuous coverage', borrowed_firewall_controls)
check('A8S-RUNTIME-FW-PARENT-REFUSAL-1: wrong or changed parent refuses without I/O or manufactured complete coverage', borrowed_firewall_refusal_controls)

def full_projection_controls():
    raw = {'complete': True, 'native': {name: 0 for name in
        ('hits', 'loopback_hits', 'listen_nonloopback', 'undetermined_loopback', 'process_errors')},
        'fs': {'fs_hits': 0}, 'ancestor_claude': {'found': 0}}
    fields = {name: {'ok': True} for name in P.FIELDS}
    if P.sm_verdict(fields, problems=[], checks=[('owned', P.c6_check_summary(raw))], catcher_lines=0) != ('pass', []):
        return False
    for group, name in [('native', name) for name in raw['native']] + [('fs', 'fs_hits'), ('ancestor_claude', 'found')]:
        for bad in (1, -1, None, False, '0'):
            changed = copy.deepcopy(raw)
            changed[group][name] = bad
            summary = P.c6_check_summary(changed)
            if P.sm_verdict(fields, problems=[], checks=[('owned', summary)], catcher_lines=0)[0] != 'fail':
                return False
        changed = copy.deepcopy(raw)
        del changed[group][name]
        if P.clean_check(P.c6_check_summary(changed)) is not False:
            return False
    return True


def protocol_raw_error_controls():
    for kwargs in ({'process_errors': 1}, {'ancestor_found': 1}):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-raw-projection-') as directory:
            f = protocol_fixture(Path(directory).resolve(), **kwargs)
            result = f.run_probe()
            if result['fields']['sm_egress_boot']['ok'] is not False or result['outcome'] != 'fail':
                return False
    return True


def resource_writer_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-resource-writer-') as directory:
        f = protocol_fixture(Path(directory).resolve(), stop_error=True)
        result = f.run_probe()
        scan = result['fields']['sm_key_search']
        return (result['fields']['sm_stop']['ok'] is True and scan['value']['findings']
                and not scan['value']['findings'][0]['leaks'] and scan['ok'] is False
                and scan['value']['writers_complete'] is False and f.events[-1] == 'close:fake')


def created_process_controls():
    import io
    for fault in ('none', 'before-register', 'after-adopt'):
        class Jobs(DeclaredAcquisitionJobs):
            def adopt(self, pid, handle=None):
                return L.WinJobs.adopt(self, pid, handle)
        jobs = Jobs()
        jobs.jobs.clear()
        jobs.live[111] = [701]
        jobs.created_at = lambda handle: 25.0
        process = SimpleNamespace(pid=701, _handle=501, stdout=io.BytesIO(b'owned output'),
            poll=lambda: 0, wait=lambda timeout: 0)
        class Native(SimpleNamespace):
            def register(self, pid, handle=None, label=None):
                events.append('register')
                if fault == 'before-register':
                    raise OSError('owned registration fault')
                birth = L.NativeEgressWitness.register(self, pid, handle=handle, label=label)
                if fault == 'after-adopt':
                    self.jobs.jobs[pid] = 99
                    raise OSError('owned registration fault after assignment and map replacement')
                return birth
        native = Native(jobs=jobs, registered_roots={}, _lock=threading.Lock(), _identity=lambda pid: 25.0,
                        tracked={}, root_of={}, label_of={})
        events, owned = [], []
        def spawn(contract, argv, **kwargs):
            child_process = kwargs['popen'](argv)
            if not owned or owned[0].child.process is not process:
                raise AssertionError('process missing before register')
            kwargs['witnesses'].native.register(process.pid, handle=501, label='owned-spawn')
            return SimpleNamespace(process=child_process, spawn_id='owned-spawn')
        guard = P.GuardedLaunch(SimpleNamespace(spawn=spawn), SimpleNamespace(), {}, run='owned',
            argv=['D:/owned/server.exe'], expected_by_unit={'u1': {name: 'owned' for name in P.SERVER_ENV_NAMES}})
        def created(child):
            events.append('created')
            boot = P.OwnedBoot(child, None, None, native=native, birth_time=None, port=1, dirs=None)
            owned.append(boot)
            boot.pump = P.BoundedPipePump(process.stdout)
            boot.pump.start()
            return boot
        guard.creation_callback = created
        action = lambda: guard.spawn(None, ['D:/owned/firewall.exe'], record={'role': 'fw-read'},
            popen=lambda *a, **kw: process, witnesses=SimpleNamespace(native=native))
        if fault == 'none':
            child = action()
            if owned[0].child is not child:
                return False
        elif not refused(action, OSError):
            return False
        if len(owned) != 1 or len(guard.children) != 1 or events != ['created', 'register']:
            return False
        if fault == 'before-register':
            if owned[0].job is not None:
                return False
        else:
            if owned[0].job is None or owned[0].job.handle != 111:
                return False
            jobs.jobs[701] = 99  # A later PID-map change cannot redirect termination.
            result = owned[0].close(None, graceful=False)
            if result['complete'] is not True or jobs.killed != [111] or jobs.live[99] != [999]:
                return False
    return True


def gate_stop_controls():
    entered, release = threading.Event(), threading.Event()
    polls = []
    def poll():
        polls.append(True)
        entered.set()
        release.wait(1.0)
    driver = SimpleNamespace(poll=poll, poll_s=0.001, problems=[], _blind=lambda error: None,
        stop=mock.Mock(side_effect=AssertionError('legacy stop forbidden')))
    owner = P.OwnedGate(driver)
    owner.start()
    try:
        if not entered.wait(1):
            return False
        t0 = time.monotonic()
        result = owner.close(drain_s=0.02)
        if result['complete'] is not False or time.monotonic() - t0 > 0.5 or len(polls) != 1:
            return False
        release.set()
        result = owner.close(drain_s=1.0)
        return (result['complete'] is True and len(polls) == 2
                and all(not worker.is_alive() for worker in owner.workers) and not driver.stop.called)
    finally:
        release.set()
        for worker in owner.workers:
            worker.join(1.0)


def gate_final_poll_controls():
    entered, release = threading.Event(), threading.Event()
    def poll():
        entered.set()
        release.wait(1.0)
    driver = SimpleNamespace(poll=poll, poll_s=100, problems=[], _blind=lambda error: None)
    owner = P.OwnedGate(driver)
    try:
        t0 = time.monotonic()
        row = owner.close(drain_s=0.02)
        return row['complete'] is False and time.monotonic() - t0 < 0.5 and entered.is_set()
    finally:
        release.set()
        for worker in owner.workers:
            worker.join(1.0)


def owned_fake_controls():
    upstream = load('v3_upstream_for_owned_fake', 'research/v3/probe_upstream.py')
    import socket
    for kind in ('deepseek', 'ollama'):
        resources = []
        fake = P.OwnedUpstream(upstream, kind, resources=resources, tag='owned-tag', digest='a'*64)
        stream = sender = None
        entered = threading.Event()
        errors = []
        original_body = upstream._Handler._body
        def observed_body(handler):
            entered.set()
            return original_body(handler)
        try:
            if fake.host != '127.0.0.1' or len(resources) != 1:
                return False
            with mock.patch.object(upstream._Handler, '_body', observed_body):
                stream = socket.create_connection((fake.host, fake.port), timeout=1)
                stream.sendall(b'POST /v1/embeddings HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 1000000\r\n\r\n{')
                def progress():
                    # Keep the partial request active until cancellation starts,
                    # preserving the actual handler timeout and blocking socket read.
                    while not fake.stop_requested.wait(0.02):
                        try:
                            stream.sendall(b' ')
                        except OSError as exc:
                            if not fake.stop_requested.is_set():
                                errors.append(type(exc).__name__)
                            return
                sender = threading.Thread(target=progress, name='sm-partial-body-client', daemon=True)
                sender.start()
                if not entered.wait(1) or not fake.workers or not fake.sockets:
                    return False
                row = fake.close(drain_s=1)
            if (row['complete'] is not True or row['active_sockets'] != 0 or row['live_workers'] != 0
                    or any(worker.is_alive() for worker in fake.workers) or errors):
                print('owned-drain-diagnostic', kind, row, errors)
                return False
        finally:
            fake.close(drain_s=1)
            if stream is not None:
                stream.close()
            if sender is not None:
                sender.join(1)
                if sender.is_alive():
                    raise AssertionError('owned partial-body sender must drain')
    return True


def fake_partial_bind_controls():
    upstream = load('v3_upstream_for_partial_fake', 'research/v3/probe_upstream.py')
    from http.server import ThreadingHTTPServer
    resources = []
    with mock.patch.object(ThreadingHTTPServer, 'server_bind', side_effect=OSError('owned bind failure')):
        if not refused(lambda: P.OwnedUpstream(upstream, 'deepseek', resources=resources), OSError):
            return False
    return len(resources) == 1 and resources[0][2](resources[0][1]()) is True


def status_lifecycle_controls():
    status_module = load('v3_status_for_owned_lifecycle', 'research/v3/status_log.py')
    with tempfile.TemporaryDirectory(prefix='nvt-sm-status-') as directory:
        status = status_module.StatusLog(Path(directory) / 'STATUS')
        owner = P.OwnedStatus(status, run='owned-status', model='deepseek-flash')
        owner.start()
        result = owner.close()
        raw = (Path(directory) / 'STATUS').read_text(encoding='utf-8')
        if result['complete'] is not True or status._state.live or status._state.open_block:
            return False
        events = [' STAND _a8 START ', ' BLOCK _a8/p0p2 START ', ' START _a8/p0p2/',
                  ' ABORT _a8/p0p2/', ' BLOCK _a8/p0p2 END ', ' STAND _a8 END ']
        positions = [raw.index(event) for event in events]
        return positions == sorted(positions) and owner.close() is result


def status_fault_controls():
    calls = []
    def action(name, fail=False, result=None):
        def do(*args, **kwargs):
            calls.append(name)
            if fail:
                raise OSError('owned status write failure')
            return result
        return do
    status = SimpleNamespace(stand=action('stand'), block_start=action('block-start'),
        start=action('start', result='_a8/p0p2/owned-status/' + P.ARM),
        abort=action('abort', fail=True), end=action('end'), block_end=action('block-end', fail=True))
    owner = P.OwnedStatus(status, run='owned-status', model='deepseek-flash')
    owner.start()
    result = owner.close()
    return (result['complete'] is False and calls == ['stand', 'block-start', 'start', 'abort', 'end', 'block-end', 'stand']
            and len(result['problems']) == 2)


def proxy_partial_start_controls():
    import io
    if P.os.name != 'nt':
        # The production path refuses before Popen outside the required Windows Job host.
        return True
    rp = load('v3_proxy_for_owned_partial', 'research/v3/run_v3_proxy.py')
    faults = ('none', 'adopt-before', 'adopt-after', 'resume', 'stdin', 'ready',
              'ports-missing', 'ports-sha', 'ports-json', 'listener', 'control')
    for fault in faults:
        with tempfile.TemporaryDirectory(prefix='nvt-sm-proxy-partial-') as directory:
            root = Path(directory).resolve()
            run_dir = root / 'proxy-run'
            run_dir.mkdir()
            ports = {'control': 44177}
            data = b'{' if fault == 'ports-json' else json.dumps(ports).encode()
            if fault != 'ports-missing':
                (run_dir / 'ports.json').write_bytes(data)
            digest = 'b' * 64 if fault == 'ports-sha' else hashlib.sha256(data).hexdigest()
            ready = b'NO\n' if fault == 'ready' else ('READY ' + digest + '\n').encode()
            process = SimpleNamespace(pid=701, _handle=501, stdout=io.BytesIO(ready), stderr=io.BytesIO(b''),
                stdin=io.BytesIO(), poll=lambda: 0, wait=lambda timeout: 0)
            if fault == 'stdin':
                process.stdin = SimpleNamespace(write=mock.Mock(side_effect=OSError), close=lambda: None)
            class Jobs(DeclaredAcquisitionJobs):
                def adopt(self, pid, handle=None):
                    if fault == 'adopt-before':
                        raise OSError('owned adoption failure')
                    birth = L.WinJobs.adopt(self, pid, handle)
                    if fault == 'adopt-after':
                        self.jobs[pid] = 99
                        raise OSError('owned post-adoption and replaced-map failure')
                    return birth
            jobs = Jobs()
            jobs.jobs.clear()
            jobs.live[111] = []
            jobs.identity = lambda pid: 25.0
            jobs.created_at = lambda handle: 25.0
            jobs.resume = lambda handle: fault != 'resume'
            calls = []
            def popen(*args, **kwargs):
                calls.append(kwargs)
                return process
            def spawn(contract, argv, **kwargs):
                proc = kwargs['popen'](argv)
                return SimpleNamespace(process=proc, spawn_id='owned-proxy-spawn')
            launch = SimpleNamespace(WinJobs=lambda: jobs, CREATE_SUSPENDED=4, _within=L._within,
                _link_in_chain=L._link_in_chain, build_env=lambda *a, **kw: {}, spawn=spawn)
            native = SimpleNamespace(allow_listener=lambda *a: fault != 'listener')
            resources = []
            config = {'run_dir': str(run_dir)}
            cfg = run_dir / 'proxy_config.json'
            cfg.write_text(json.dumps(config), encoding='utf-8')
            arguments = dict(launch=launch, resources=resources, script=root / 'owned-proxy.py', config_path=cfg,
                key_file=root / 'owned-sentinel.env', stdin_secrets={'control_token': 'OWNED_CONTROL_17'},
                unit=SimpleNamespace(cwd=root / 'unit'), parent_env={}, witnesses=SimpleNamespace(native=native), popen=popen)
            def action():
                if fault == 'control':
                    with mock.patch.object(rp, '_ctl', return_value=SimpleNamespace(ProxyControl=mock.Mock(side_effect=OSError))):
                        return rp.start(SimpleNamespace(runs_root=root), python=root / 'owned-python.exe',
                            key_file=arguments['key_file'], config=config, secrets=arguments['stdin_secrets'],
                            unit=arguments['unit'], parent_env={}, witnesses=arguments['witnesses'],
                            spawn=lambda c, python, **kw: P.spawn_sm_proxy(c, python, **arguments))
                return P.spawn_sm_proxy(SimpleNamespace(runs_root=root), root / 'owned-python.exe', **arguments)
            try:
                action()
                if fault != 'none':
                    return False
            except Exception as exc:
                if fault == 'none' or not isinstance(exc, (OSError, P.PreflightRefused, UnicodeError, json.JSONDecodeError)):
                    return False
            if len(calls) != 1 or not calls[0].get('creationflags', 0) & 4 or len(resources) != 1:
                return False
            with mock.patch.object(P, 'bounded_proxy_post', return_value=(200, {})):
                result = resources[0][1]()
            if fault == 'adopt-before':
                if result['complete'] is not False:
                    return False
            elif jobs.killed != [111]:
                return False
            if process.stdout.closed or process.stderr.closed:
                return False  # Pumps own reads; no unrelated stream is closed by a PID lookup.
    return True


def missing_created_job_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-missing-job-') as directory:
        f = boot_fixture(Path(directory).resolve())
        f.guard._spawn_owned = object()  # Production marker, with no creation-time Job capture.
        f.job.jobs[701] = 99  # Foreign Job currently in the PID map.
        try:
            P.start_boot(*f.args, **f.options)
            return False
        except P.PreflightRefused as exc:
            if str(exc) != 'server-created-job-unretained':
                return False
        f.child.process.poll.return_value = 0
        result = f.boots[0].close(f.server, graceful=False)
        return result['complete'] is False and f.job.killed == [] and f.job.live[99] == [999]


def firewall_exact_count_controls():
    import io
    binary = Path('D:/owned/firewall-count/server.exe')
    for group, name, bad in [('fs', 'fs_hits', False), ('fs', 'fs_hits', 0.0),
                             ('ancestor_claude', 'found', False), ('ancestor_claude', 'found', 0.0)]:
        jobs = BootJobs()
        jobs.live[81] = []
        native = SimpleNamespace(jobs=jobs, registered_roots={701: 25.0})
        process = SimpleNamespace(pid=701, stdout=io.BytesIO(json.dumps(fw_fixture(binary)).encode()),
                                  wait=lambda timeout: 0, poll=lambda: 0)
        raw = {'complete': True, 'native': {key: 0 for key in
            ('hits', 'loopback_hits', 'listen_nonloopback', 'undetermined_loopback', 'process_errors')},
            'fs': {'fs_hits': 0}, 'ancestor_claude': {'found': 0}}
        raw[group][name] = bad
        witnesses = SimpleNamespace(native=native, begin_check=lambda cid: None, end_check=lambda cid: raw)
        scheduler = SimpleNamespace(witnesses=witnesses, launch=SimpleNamespace(),
            spawn_child=lambda build, **kw: (SimpleNamespace(process=process), build(SimpleNamespace())))
        modules = SimpleNamespace(SC=SimpleNamespace(LaunchSpec=lambda **kw: SimpleNamespace(**kw)))
        result = P.read_firewall_real(None, L, modules, scheduler, run='owned-count', unit='fw-p0', binary=binary, resources=[])
        if result['complete'] is not False or 'firewall-witness-incomplete-or-hit' not in result['problems']:
            return False
    return True


def status_durable_start_controls():
    status_module = load('v3_status_for_durable_start', 'research/v3/status_log.py')
    for nth in (1, 2, 3):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-status-fsync-') as directory:
            path = Path(directory) / 'STATUS'
            status = status_module.StatusLog(path)
            owner = P.OwnedStatus(status, run='owned-status', model='deepseek-flash')
            original = status_module.os.fsync
            writes = []
            def fail(fd):
                writes.append(True)
                if len(writes) == nth:
                    raise OSError('owned fsync failure after bytes written')
                return original(fd)
            with mock.patch.object(status_module.os, 'fsync', fail):
                if not refused(owner.start, OSError):
                    return False
            result = owner.close()
            if (not path.is_file() or len(path.read_bytes().splitlines()) != nth or result['complete'] is not False
                    or not any(problem == 'status-start:OSError' for problem in result['problems'])
                    or not all((owner.stand_open, owner.block_open, owner.run_open)[:nth])):
                return False
    return True


def status_provider_halt_controls():
    status_module = load('v3_status_for_provider_halt', 'research/v3/status_log.py')
    for halt in ('401', '402', '403'):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-status-provider-') as directory:
            path = Path(directory) / 'STATUS'
            status = status_module.StatusLog(path)
            owner = P.OwnedStatus(status, run='owned-status', model='deepseek-flash')
            owner.start()
            status.incident('owned-provider-halt', 'START', arms=[P.ARM], kind=halt)
            owner.halt_kind = lambda: halt
            result = owner.close()
            if result['complete'] is not True or 'halt=' + halt not in path.read_text(encoding='utf-8'):
                return False
    return True


def fake_close_fault_controls():
    events = []
    class Stream:
        def __init__(self, name, broken):
            self.name, self.broken = name, broken
        def shutdown(self, how):
            events.append('shutdown:' + self.name)
        def close(self):
            events.append('close:' + self.name)
            if self.broken:
                raise OSError('owned close failure')
    def worker(name):
        return SimpleNamespace(ident=1, join=lambda seconds: events.append('join:' + name), is_alive=lambda: False)
    owner = P.OwnedUpstream.__new__(P.OwnedUpstream)
    owner.stop_requested, owner._lock = threading.Event(), threading.Lock()
    owner.closing, owner.errors = False, []
    owner.sockets = {Stream('broken', True), Stream('good', False)}
    owner._thread, owner.shutdown_worker, owner.workers = worker('accept'), None, [worker('handler')]
    owner.httpd = SimpleNamespace(server_close=lambda: events.append('server-close'))
    result = owner.close(drain_s=1)
    return (result['complete'] is False and 'fake-socket-close:OSError' in result['problems']
            and all(name in events for name in ('close:broken', 'close:good', 'server-close', 'join:accept', 'join:handler')))


def proxy_config_read_controls():
    import io
    with tempfile.TemporaryDirectory(prefix='nvt-sm-proxy-config-') as directory:
        parent = Path(directory).resolve()
        root = parent / 'owned'
        run_dir = root / 'proxy-run'
        run_dir.mkdir(parents=True)
        path = run_dir / 'proxy_config.json'
        raw = json.dumps({'run_dir': str(run_dir)}).encode()
        path.write_bytes(raw)
        contract = SimpleNamespace(runs_root=root)
        for target, linked in ((parent / 'proxy_config.json', False), (path, True), (Path('proxy_config.json'), False)):
            launch = SimpleNamespace(_within=L._within, _link_in_chain=lambda *a: linked)
            with mock.patch.object(Path, 'open', side_effect=AssertionError('unowned path opened')) as opened:
                try:
                    P.proxy_config_record(contract, launch, target)
                    return False
                except P.PreflightRefused as exc:
                    if str(exc) != 'proxy-config-read-path' or opened.called:
                        return False
        oversized = json.dumps({'run_dir': str(run_dir), 'owned_padding': 'x' * (1 << 20)}).encode()
        reads = []
        class Stream(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)
        with mock.patch.object(Path, 'open', return_value=Stream(oversized)):
            try:
                P.proxy_config_record(contract, L, path)
                return False
            except P.PreflightRefused as exc:
                if str(exc) != 'proxy-config-size' or reads != [(1 << 20) + 1]:
                    return False
        good = P.proxy_config_record(contract, L, path)
        if good != {'run_dir': run_dir, 'sha256': hashlib.sha256(raw).hexdigest()}:
            return False
        with mock.patch.object(P.json, 'loads', side_effect=RecursionError):
            try:
                P.proxy_config_record(contract, L, path)
                return False
            except P.PreflightRefused as exc:
                return str(exc) == 'proxy-config-json:RecursionError'


def proxy_config_write_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-proxy-write-') as directory:
        root = Path(directory).resolve()
        proxy_dir = root / 'proxy'
        proxy_dir.mkdir()
        contract = SimpleNamespace(runs_root=root)
        want = (proxy_dir / 'run', proxy_dir / 'run/proxy_config.json')
        if P.owned_proxy_config_paths(contract, L, proxy_dir) != want:
            return False
        for linked in want:
            launch = SimpleNamespace(_within=L._within, _link_in_chain=lambda base, target: target == linked)
            if not refused(lambda: P.owned_proxy_config_paths(contract, launch, proxy_dir), P.PreflightRefused):
                return False
        want[0].mkdir()
        marker = want[1]
        marker.write_bytes(b'owned-unused-marker')
        if not refused(lambda: P.owned_proxy_config_paths(contract, L, proxy_dir), P.PreflightRefused):
            return False
        return marker.read_bytes() == b'owned-unused-marker'


check('A8S-PROXY-CONFIG-READ-1: outside/link/relative config refuses before open; valid oversized JSON uses one bounded read', proxy_config_read_controls)
check('A8S-PROXY-CONFIG-WRITE-1: RP.start fresh config/run targets guarded before its writes; existing target untouched', proxy_config_write_controls)
check('A8S-CREATED-JOB-MISSING-1: absent production capture refuses without adopting or terminating foreign PID-map Job', missing_created_job_controls)
check('A8S-FW-EXACT-COUNTS-1: fresh readonly FW check refuses bool/float FS and ancestor zeros', firewall_exact_count_controls)
check('A8S-STATUS-DURABLE-1: real fsync failure after durable START bytes is retained at every level and cannot pass cleanup', status_durable_start_controls)
check('A8S-STATUS-HALT-1: actual open401/402/403 incidents retain their literal provider halt on STAND END', status_provider_halt_controls)
check('A8S-FAKE-CLOSE-FAULT-1: failing owned socket close cannot skip remaining sockets, server close or handler joins', fake_close_fault_controls)
check('A8S-PROXY-PARTIAL-1: suspended Popen/Job/pumps retained across adopt/resume/stdin/READY/ports/control faults', proxy_partial_start_controls)
check('A8S-RAW-PROJECTION-1: every raw native/FS/process-error/ancestor count is typed and retained in final verdict', full_projection_controls)
check('A8S-PROTOCOL-RAW-1: complete=True cannot hide process errors or ancestor findings in connected driver', protocol_raw_error_controls)
check('A8S-RESOURCE-WRITER-1: clean server/scan cannot certify SF31 while another owned resource failed drain', resource_writer_controls)
check('A8S-CREATED-PROCESS-1: Popen capture precedes registration faults and pins newly assigned immutable Job', created_process_controls)
check('A8S-GATE-STOP-1: blocked worker refuses within shared ceiling without legacy stop or concurrent final poll', gate_stop_controls)
check('A8S-GATE-FINAL-1: final poll is separately retained and bounded even when its send blocks', gate_final_poll_controls)
check('A8S-FAKE-DRAIN-1: partial body connection and every owned HTTP handler drain on loopback-only fake close', owned_fake_controls)
check('A8S-FAKE-PARTIAL-1: bind failure retains server resource before acquisition and safely closes it', fake_partial_bind_controls)
check('A8S-STATUS-1: actual debug stand/block/run START and terminal ABORT close every STATUS level', status_lifecycle_controls)
check('A8S-STATUS-FAULT-1: failed abort/write still attempts END, block and stand independently and cannot pass', status_fault_controls)

def api_tag_controls():
    if P.SM_OPENAPI_PATHS != ('/v3/openapi', '/v4/openapi'):
        return False
    for run in ('unit', 'A8-sm-29', 'a' * 97):
        tag = P.sm_container_tag(run)
        if tag != run + '_u1' or len(tag) > 100 or '.' in tag:
            return False
    for run in (None, True, '', '../unit', 'unit.other', 'unit:other', 'a' * 98, 'a' * 128):
        if not refused(lambda run=run: P.sm_container_tag(run), P.PreflightRefused):
            return False
    with tempfile.TemporaryDirectory(prefix='nvt-sm-api-tag-') as directory:
        root = Path(directory).resolve()
        contract = SimpleNamespace(runs_root=root)
        # Refuse before any input read or live label consumption. No candidate binary is opened.
        with mock.patch.object(P, 'bundle_record') as bundle_read:
            if not named_preflight_refusal(lambda: P.preflight_static(contract, L, SimpleNamespace(),
                    run='a' * 98, d1_run='d1', fake_knobs={}, test_only=False), 'container-tag-limit'):
                return False
            return not bundle_read.called and list(root.iterdir()) == []


def api_auth_body_controls():
    connection = mock.Mock(side_effect=AssertionError('pure body constructor must not connect'))
    with mock.patch.object(P.http.client, 'HTTPConnection', connection):
        result = P.sm_auth_target('owned_control')
    if result != ('POST', '/v4/memories/list', {'containerTags': ['owned_control_u1'], 'limit': 1}) or connection.called:
        return False
    method, path, body = result
    raw = json.dumps(body, allow_nan=False).encode('utf-8')
    if json.loads(raw) != body or type(body['limit']) is not int or body['limit'] != 1:
        return False
    body['containerTags'].append('modified-fixture')
    return P.sm_auth_target('owned_control')[2]['containerTags'] == ['owned_control_u1']


check('A8S-API-TAG-1: pinned paths and exact underscore tag limit refuse before input reads or labels', api_tag_controls)
check('A8S-API-AUTH-BODY-1: pinned memory-list body has required containerTags and isolated numeric limit', api_auth_body_controls)

REST_SM = load('v3_sm_operation_rest_under_test', 'research/v3/arms/_rest.py')


def sm_operations_schema_fixture():
    # Independent source-shaped OpenAPI fixture, not generated from the candidate declaration.
    string = {'type': 'string'}
    number = {'type': 'number'}
    def obj(properties, required=()):
        return {'type': 'object', 'properties': properties, 'required': list(required)}
    def array(items):
        return {'type': 'array', 'items': items}
    def op(response, body=None, required=()):
        value = {'responses': {'200': {'content': {'application/json': {'schema': response}}}}}
        if body is not None:
            value['requestBody'] = {'required': True, 'content': {'application/json': {'schema': obj(body, required)}}}
        return value
    pagination = obj({'currentPage': number, 'limit': number, 'totalItems': number, 'totalPages': number})
    return {'openapi': '3.1.0', 'paths': {
        '/v3/documents': {'post': op(obj({'id': string, 'status': string}),
            {'content': string, 'containerTag': string}, ('content',))},
        '/v3/documents/{id}': {'get': {**op(obj({'id': string, 'status': string, 'createdAt': string,
            'memories': array(obj({'id': string, 'memory': string}))})),
            'parameters': [{'name': 'id', 'in': 'path', 'required': True, 'schema': string}]}},
        '/v4/search': {'post': op(obj({'results': array(obj({'id': string, 'memory': string, 'similarity': number})),
            'total': number, 'timing': number}), {'q': string, 'containerTags': array(string), 'limit': number}, ('q',))},
        '/v4/memories/list': {'post': op(obj({'memoryEntries': array(obj({'id': string, 'memory': string,
            'documentIds': array(string)})), 'pagination': pagination}),
            {'containerTags': array(string), 'limit': number, 'page': number}, ('containerTags',))},
    }}


def sm_operation_schema_replies(doc):
    raw = json.dumps(doc, sort_keys=True, allow_nan=False).encode('utf-8')
    return [{'status': 200, 'json': doc, 'sha256': hashlib.sha256(raw).hexdigest()} for _ in range(2)]


def named_api_refusal(fn, reason):
    try:
        fn()
    except P.SmApiRefused as exc:
        return str(exc) == reason
    return False


def api_operation_declarations_controls():
    calls = P.sm_api_calls(REST_SM)
    if set(calls) != {'add_document', 'get_document', 'search', 'list_memories'}:
        return False
    signatures = {name: (c.method, c.path, c.body, c.response) for name, c in calls.items()}
    expected = {
        'add_document': ('POST', '/v3/documents', ('content', 'containerTag'), ('id', 'status')),
        'get_document': ('GET', '/v3/documents/{id}', (), ('id', 'status', 'createdAt', 'memories[].id', 'memories[].memory')),
        'search': ('POST', '/v4/search', ('q', 'containerTags[]', 'limit'),
                   ('results[].id', 'results[].memory', 'results[].similarity', 'total', 'timing')),
        'list_memories': ('POST', '/v4/memories/list', ('containerTags[]', 'limit', 'page'),
            ('memoryEntries[].id', 'memoryEntries[].memory', 'memoryEntries[].documentIds[]',
             'pagination.currentPage', 'pagination.limit', 'pagination.totalPages', 'pagination.totalItems')),
    }
    return signatures == expected and REST_SM.conformance(calls, sm_operations_schema_fixture()) == []


def api_observed_schema_controls():
    doc = sm_operations_schema_fixture()
    replies = sm_operation_schema_replies(doc)
    result = P.sm_openapi_contract(replies, REST_SM)
    if (result.get('conformant') is not True or result.get('runtime_admission') is not False
            or result.get('value_constraints_verified') is not False
            or len(result['observed']) != 2 or result['observed'][0]['path'] != '/v3/openapi'
            or result['observed'][1]['path'] != '/v4/openapi'):
        return False
    for count in (0, 1, 3):
        if not named_api_refusal(lambda count=count: P.sm_openapi_contract(replies[:count] if count < 2 else replies + replies[:1], REST_SM), 'blocked:no-openapi'):
            return False
    for path, name in (('/v3/documents', 'add_document'), ('/v3/documents/{id}', 'get_document'),
                       ('/v4/search', 'search'), ('/v4/memories/list', 'list_memories')):
        changed = json.loads(json.dumps(doc))
        del changed['paths'][path]
        if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:' + name):
            return False
    changed = json.loads(json.dumps(doc))
    schema = changed['paths']['/v3/documents']['post']['requestBody']['content']['application/json']['schema']
    schema['required'].append('undeclared')
    if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:add_document'):
        return False
    changed = json.loads(json.dumps(doc))
    changed['components'] = {'schemas': {'LocalString': {'type': 'string'}}}
    changed['paths']['/v3/documents']['post']['requestBody']['content']['application/json']['schema']['properties']['content'] = {'$ref': '#/components/schemas/LocalString'}
    changed['paths']['/v4/memories/list']['post']['requestBody']['content']['application/json']['schema']['properties']['limit'] = {'anyOf': [{'type': 'string'}, {'type': 'integer'}]}
    if P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM)['conformant'] is not True:
        return False
    # A small repeated-reference DAG is larger to expand than to serialize. Budget it before the legacy checker.
    changed = json.loads(json.dumps(doc))
    original_body = changed['paths']['/v3/documents']['post']['requestBody']['content']['application/json']['schema']
    branches = {'Branch0': original_body}
    for index in range(1, 10):
        ref = {'$ref': '#/components/schemas/Branch' + str(index - 1)}
        branches['Branch' + str(index)] = {'type': 'object', 'allOf': [ref, dict(ref)]}
    changed['components'] = {'schemas': branches}
    changed['paths']['/v3/documents']['post']['requestBody']['content']['application/json']['schema'] = {'$ref': '#/components/schemas/Branch9'}
    with mock.patch.object(REST_SM, 'conformance', side_effect=AssertionError('DAG exceeded the declared analysis budget')):
        if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:add_document'):
            return False
    changed = json.loads(json.dumps(doc))
    schema = changed['paths']['/v3/documents/{id}']['get']['responses']['200']['content']['application/json']['schema']
    del schema['properties']['memories']
    if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:get_document'):
        return False
    changed = json.loads(json.dumps(doc))
    changed['paths']['/v4/search']['post']['requestBody']['content']['application/json']['schema'] = {'$ref': 'https://example.invalid/schema'}
    if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:search'):
        return False
    changed = json.loads(json.dumps(doc))
    pagination = changed['paths']['/v4/memories/list']['post']['responses']['200']['content']['application/json']['schema']['properties']['pagination']
    del pagination['properties']['limit']
    if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:list_memories'):
        return False
    for leaf in ({'type': 'boolean'}, {'$ref': 'https://example.invalid/leaf'}, {'$ref': '#/components/schemas/Cycle'}):
        changed = json.loads(json.dumps(doc))
        changed['components'] = {'schemas': {'Cycle': {'$ref': '#/components/schemas/Cycle'}}}
        changed['paths']['/v3/documents']['post']['requestBody']['content']['application/json']['schema']['properties']['content'] = leaf
        if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:add_document'):
            return False
    changed = json.loads(json.dumps(doc))
    changed['paths']['/v3/documents']['post']['responses']['200']['content']['application/json']['schema']['properties']['id'] = {'type': 'object'}
    if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:add_document'):
        return False
    changed = json.loads(json.dumps(doc))
    changed['paths']['/v3/documents/{id}']['get']['parameters'][0]['schema'] = {'type': 'number'}
    if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:get_document'):
        return False
    # A recursive allOf graph must be bounded before calling the legacy checker.
    changed = json.loads(json.dumps(doc))
    changed['components'] = {'schemas': {'Recursive': {'type': 'object', 'allOf': [{'$ref': '#/components/schemas/Recursive'}]}}}
    changed['paths']['/v3/documents']['post']['requestBody']['content']['application/json']['schema'] = {'$ref': '#/components/schemas/Recursive'}
    with mock.patch.object(REST_SM, 'conformance', side_effect=AssertionError('recursive graph reached unbounded checker')):
        if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:add_document'):
            return False
    for invalid in ({'status': True, 'json': doc}, {'status': 404, 'json': doc}, {'status': 200, 'json': []}):
        if not named_api_refusal(lambda invalid=invalid: P.sm_openapi_contract([invalid, replies[1]], REST_SM), 'blocked:no-openapi'):
            return False
    for path, field, name in (('/v3/documents', 'id', 'add_document'),
                              ('/v4/memories/list', 'pagination', 'list_memories')):
        changed = json.loads(json.dumps(doc))
        changed['openapi'] = '3.0.3'
        leaf = changed['paths'][path]['post']['responses']['200']['content']['application/json']['schema']['properties'][field]
        if field == 'pagination':
            leaf = leaf['properties']['limit']
        leaf['nullable'] = True
        if not named_api_refusal(lambda: P.sm_openapi_contract(sm_operation_schema_replies(changed), REST_SM), 'blocked:schema-missing:' + name):
            return False
    replies[0]['sha256'] = 'unobserved'
    return named_api_refusal(lambda: P.sm_openapi_contract(replies, REST_SM), 'openapi-observation-hash')


def api_operation_targets_controls():
    if P.sm_api_target('add_document', 'unit', content='  kept content  ') != ('POST', '/v3/documents', {'content': '  kept content  ', 'containerTag': 'unit_u1'}):
        return False
    if P.sm_api_target('get_document', 'unit', document_id='a/b?c%20') != ('GET', '/v3/documents/a%2Fb%3Fc%2520', None):
        return False
    if P.sm_api_target('search', 'unit', query='  question  ') != ('POST', '/v4/search', {'q': 'question', 'containerTags': ['unit_u1'], 'limit': 10}):
        return False
    expected = ('POST', '/v4/memories/list', {'containerTags': ['unit_u1'], 'limit': 10, 'page': 2})
    if P.sm_api_target('list_memories', 'unit', page=2) != expected:
        return False
    for bad in (None, True, {}, 'undeclared'):
        if not named_api_refusal(lambda bad=bad: P.sm_api_target(bad, 'unit'), 'undeclared-operation'):
            return False
    for bad in (None, True, '', '.', '..', 'a\n', 'a\x00', 'a' * 256):
        if not named_api_refusal(lambda bad=bad: P.sm_api_target('get_document', 'unit', document_id=bad), 'document-id'):
            return False
    for bad in (0, True, -1, 1101, 1.5):
        if not named_api_refusal(lambda bad=bad: P.sm_api_target('list_memories', 'unit', page=bad), 'memory-page'):
            return False
    return named_api_refusal(lambda: P.sm_api_target('add_document', 'unit', content=' '), 'document-content') and named_api_refusal(lambda: P.sm_api_target('search', 'unit', query=' '), 'search-query')


def api_memory_response_controls():
    reply = {'status': 200, 'json': {'memoryEntries': [
        {'id': 'memory1', 'memory': 'source-backed content', 'documentIds': ['doc1']}],
        'pagination': {'currentPage': 1, 'limit': 10, 'totalItems': 1, 'totalPages': 1}}}
    result = P.sm_memory_page(reply, document_id='doc1', page=1)
    if result['memory_found'] is not True or len(result['selected']) != 1:
        return False
    if P.sm_memory_page(reply, document_id='foreign_doc', page=1)['memory_found'] is not False:
        return False
    empty = {'status': 200, 'json': {'memoryEntries': [], 'pagination': {'currentPage': 1, 'limit': 10, 'totalItems': 0, 'totalPages': 0}}}
    if P.sm_memory_page(empty, document_id='doc1', page=1)['memory_found'] is not False:
        return False
    changed = json.loads(json.dumps(reply))
    changed['json']['memories'] = changed['json'].pop('memoryEntries')
    if not named_api_refusal(lambda: P.sm_memory_page(changed, document_id='doc1', page=1), 'memory-response'):
        return False
    for name, value in (('currentPage', True), ('currentPage', 2), ('limit', 0), ('limit', 1101), ('totalItems', 0), ('totalPages', 0)):
        changed = json.loads(json.dumps(reply))
        changed['json']['pagination'][name] = value
        if not named_api_refusal(lambda: P.sm_memory_page(changed, document_id='doc1', page=1), 'memory-pagination'):
            return False
    unlinked = json.loads(json.dumps(reply))
    unlinked['json']['memoryEntries'][0]['documentIds'] = []
    if P.sm_memory_page(unlinked, document_id='doc1', page=1)['memory_found'] is not False:
        return False
    for text in ('', ' '):
        blank = json.loads(json.dumps(reply))
        blank['json']['memoryEntries'][0]['memory'] = text
        if P.sm_memory_page(blank, document_id='doc1', page=1)['memory_found'] is not False:
            return False
    for field, value in (('id', ''), ('memory', None), ('documentIds', None), ('documentIds', [True])):
        changed = json.loads(json.dumps(reply))
        changed['json']['memoryEntries'][0][field] = value
        if not named_api_refusal(lambda: P.sm_memory_page(changed, document_id='doc1', page=1), 'memory-entry'):
            return False
    changed = json.loads(json.dumps(reply))
    changed['json']['memoryEntries'] *= 2
    changed['json']['pagination']['totalItems'] = 2
    return named_api_refusal(lambda: P.sm_memory_page(changed, document_id='doc1', page=1), 'memory-entry')


def api_document_poll_controls():
    class Clock:
        def __init__(self): self.now = 0.0
        def __call__(self): return self.now
        def sleep(self, seconds): self.now += seconds
    clock = Clock()
    replies = iter([{'status': 200, 'json': {'id': 'doc1', 'status': state}} for state in ('queued', 'processing', 'done')])
    sends, admissions = [], []
    key = object()
    def request(*args, **kwargs):
        sends.append((args, kwargs, clock.now))
        clock.now += 0.1
        return next(replies)
    def admit():
        admissions.append(clock.now)
        return True
    result = P.poll_sm_document(1234, 'unit', 'doc1', key=key, request=request, admit=admit,
        ceiling_s=0.75, clock=clock, sleep=clock.sleep)
    if result['polls'] != 3 or result['processing_complete'] is not True or result['memory_success_proven'] is not False:
        return False
    if len(admissions) != 3 or any(args != (1234, 'GET', '/v3/documents/doc1') or kwargs['key'] is not key
        or kwargs['body'] is not None or not 0 < kwargs['timeout_s'] <= 0.75 - when for args, kwargs, when in sends):
        return False
    clock = Clock()
    def late(*args, **kwargs):
        clock.now = 1
        return {'status': 200, 'json': {'id': 'doc1', 'status': 'done'}}
    if not named_api_refusal(lambda: P.poll_sm_document(1234, 'unit', 'doc1', key=key, request=late, admit=lambda: True,
            ceiling_s=0.5, clock=clock, sleep=clock.sleep), 'document-poll-deadline'):
        return False
    blocked_send = mock.Mock(side_effect=AssertionError('admission must precede request'))
    clock = Clock()
    if not named_api_refusal(lambda: P.poll_sm_document(1234, 'unit', 'doc1', key=key, request=blocked_send, admit=lambda: False,
            clock=clock, sleep=clock.sleep), 'incident-gate') or blocked_send.called:
        return False
    # Admission itself consumes time; recompute the allowance before touching the socket.
    clock = Clock()
    def slow_admit():
        clock.now = 1
        return True
    if not named_api_refusal(lambda: P.poll_sm_document(1234, 'unit', 'doc1', key=key, request=blocked_send,
            admit=slow_admit, ceiling_s=0.5, clock=clock, sleep=clock.sleep), 'document-poll-deadline') or blocked_send.called:
        return False
    # No admission is consulted after the initial deadline has already elapsed.
    ticks = iter((0.0, 1.0))
    untouched_admission = mock.Mock(return_value=True)
    if not named_api_refusal(lambda: P.poll_sm_document(1234, 'unit', 'doc1', key=key, request=blocked_send,
            admit=untouched_admission, ceiling_s=0.5, clock=lambda: next(ticks)), 'document-poll-deadline'):
        return False
    if untouched_admission.called or blocked_send.called:
        return False
    for state in ('error', 'unknown'):
        clock = Clock()
        reply = {'status': 200, 'json': {'id': 'doc1', 'status': state}}
        if not named_api_refusal(lambda: P.poll_sm_document(1234, 'unit', 'doc1', key=key, request=lambda *a, **k: reply,
                admit=lambda: True, clock=clock, sleep=clock.sleep), 'document-processing-status'):
            return False
    reply = {'status': 200, 'json': {'id': 'foreign_doc', 'status': 'done'}}
    return named_api_refusal(lambda: P.sm_document_response(reply, document_id='doc1'), 'document-response')


check('A8S-API-CALLS-1: exactly four source-backed operation declarations carry used request and response fields', api_operation_declarations_controls)
check('A8S-API-SCHEMA-1: both observed specs carry every declared field or name the blocked operation', api_observed_schema_controls)
check('A8S-API-TARGETS-1: exact versioned targets, whole quoted ids, fresh tags and bounded inputs', api_operation_targets_controls)
check('A8S-API-MEMORIES-1: only memory entries tied to the created document establish nonempty memory', api_memory_response_controls)
check('A8S-API-POLL-1: each send has fresh admission and remaining deadline, late done cannot pass or prove memory', api_document_poll_controls)

import re


def state_fixture_entry(index, source='doc1'):
    return {'id': 'm' + str(index), 'memory': 'memory ' + str(index), 'documentIds': [source]}


def state_collection_controls():
    class Clock:
        now = 0.0
        def __call__(self): return self.now
    clock = Clock()
    requests, admissions = [], []
    key = object()
    def request(port, method, path, **kwargs):
        requests.append((port, method, path, kwargs))
        clock.now += 0.1
        page = kwargs['body']['page']
        entries = [state_fixture_entry(i) for i in (range(10) if page == 1 else range(10, 12))]
        return {'status': 200, 'json': {'memoryEntries': entries, 'pagination': {
            'currentPage': page, 'limit': 10, 'totalItems': 12, 'totalPages': 2}}}
    result = P.collect_sm_memories(1234, 'unit', 'doc1', key=key, request=request,
        admit=lambda: admissions.append(clock.now) or True, clock=clock)
    if (result['complete'] is not True or len(result['entries']) != 12 or len(result['selected']) != 12
            or len(requests) != 2 or len(admissions) != 2 or result['memory_found'] is not True):
        return False
    if [(entry['id'], entry['memory']) for entry in result['entries']] != [('m' + str(i), 'memory ' + str(i)) for i in range(12)]:
        return False
    if any(method != 'POST' or path != '/v4/memories/list' or kw['key'] is not key
            or kw['body']['containerTags'] != ['unit_u1'] for _, method, path, kw in requests):
        return False
    def inconsistent(*args, **kwargs):
        reply = request(*args, **kwargs)
        if kwargs['body']['page'] == 2:
            reply['json']['pagination']['totalItems'] = 13
        return reply
    if not named_api_refusal(lambda: P.collect_sm_memories(1234, 'unit', 'doc1', key=key,
            request=inconsistent, admit=lambda: True, clock=clock), 'memory-pagination-changed'):
        return False
    def duplicate(*args, **kwargs):
        reply = request(*args, **kwargs)
        if kwargs['body']['page'] == 2:
            reply['json']['memoryEntries'][0]['id'] = 'm0'
        return reply
    if not named_api_refusal(lambda: P.collect_sm_memories(1234, 'unit', 'doc1', key=key,
            request=duplicate, admit=lambda: True, clock=clock), 'memory-duplicate-across-pages'):
        return False
    def truncated(*args, **kwargs):
        reply = request(*args, **kwargs)
        if kwargs['body']['page'] == 2:
            reply['json']['memoryEntries'].pop()
        return reply
    if not named_api_refusal(lambda: P.collect_sm_memories(1234, 'unit', 'doc1', key=key,
            request=truncated, admit=lambda: True, clock=clock), 'memory-pagination-incomplete'):
        return False
    empty = {'status': 200, 'json': {'memoryEntries': [], 'pagination': {'currentPage': 1, 'limit': 10, 'totalItems': 0, 'totalPages': 0}}}
    result = P.collect_sm_memories(1234, 'unit', 'doc1', key=key, request=lambda *a, **k: empty,
        admit=lambda: True, clock=clock)
    if result['complete'] is not True or result['entries'] != [] or result['memory_found'] is not False or result['atomic_snapshot_proven'] is not False:
        return False
    clock = Clock()
    clock.now = 0.0
    untouched = mock.Mock(side_effect=AssertionError('expired allowance must not send'))
    def delayed_admission():
        clock.now = 1.0
        return True
    if not named_api_refusal(lambda: P.collect_sm_memories(1234, 'unit', 'doc1', key=key, request=untouched,
            admit=delayed_admission, ceiling_s=0.5, clock=clock), 'state-read-deadline') or untouched.called:
        return False
    clock.now = 0.0
    def late_reply(*args, **kwargs):
        clock.now = 1.0
        return empty
    if not named_api_refusal(lambda: P.collect_sm_memories(1234, 'unit', 'doc1', key=key, request=late_reply,
            admit=lambda: True, ceiling_s=0.5, clock=clock), 'state-read-deadline'):
        return False
    clock.now = 0.0
    original_parse = P.sm_memory_page
    def delayed_parse(*args, **kwargs):
        result = original_parse(*args, **kwargs)
        clock.now = 1.0
        return result
    with mock.patch.object(P, 'sm_memory_page', side_effect=delayed_parse):
        if not named_api_refusal(lambda: P.collect_sm_memories(1234, 'unit', 'doc1', key=key, request=lambda *a, **k: empty,
                admit=lambda: True, ceiling_s=0.5, clock=clock), 'state-read-deadline'):
            return False
    return True


def state_search_controls():
    entry = state_fixture_entry(1)
    reply = {'status': 200, 'json': {'results': [{'id': 'm1', 'memory': 'memory 1', 'similarity': 0.8}], 'total': 1, 'timing': 10}}
    parsed = P.sm_search_response(reply, memories=[entry])
    if parsed['found'] is not True or parsed['results'][0]['id'] != 'm1':
        return False
    empty = {'status': 200, 'json': {'results': [], 'total': 0, 'timing': 1}}
    if P.sm_search_response(empty, memories=[entry])['found'] is not False:
        return False
    blank_memory = dict(entry, memory='')
    blank_reply = json.loads(json.dumps(reply))
    blank_reply['json']['results'][0]['memory'] = ''
    if P.sm_search_response(blank_reply, memories=[blank_memory])['found'] is not False:
        return False
    for field, value in (('id', 'foreign'), ('memory', 'changed'), ('similarity', True), ('similarity', float('nan'))):
        changed = json.loads(json.dumps(reply))
        changed['json']['results'][0][field] = value
        if not named_api_refusal(lambda: P.sm_search_response(changed, memories=[entry]), 'blocked:search-shape'):
            return False
    changed = json.loads(json.dumps(reply))
    changed['json']['results'][0]['chunk'] = changed['json']['results'][0].pop('memory')
    return named_api_refusal(lambda: P.sm_search_response(changed, memories=[entry]), 'blocked:search-shape')


def state_created_at_controls():
    doc = {'id': 'doc1', 'status': 'done', 'createdAt': '2026-10-03T15:00:00+03:00'}
    if P.sm_created_at(doc)['utc'] != '2026-10-03T12:00:00+00:00':
        return False
    for value in (None, True, 1, '2026-10-03', '2026-10-03T12:00:00', '2026-02-30T12:00:00Z', '2026-10-03T12:00:00+00:60'):
        if not named_api_refusal(lambda value=value: P.sm_created_at(dict(doc, createdAt=value)), 'document-created-at'):
            return False
    if P.sm_created_at(dict(doc, createdAt='2026-10-03T15:00:00.123456789+03:00'))['utc'] != '2026-10-03T12:00:00.123456789+00:00':
        return False
    return P.sm_created_at(dict(doc, createdAt='2026-10-03T12:00:00Z'))['source'] == 'get_document.createdAt'


def state_seal_controls():
    documents = [{'id': 'doc1', 'status': 'done', 'createdAt': '2026-10-03T12:00:00Z'}]
    memories = [state_fixture_entry(1)]
    search = {'results': [{'id': 'm1', 'memory': 'memory 1', 'similarity': 0.8}], 'total': 1}
    seal = P.sm_state_seal(documents=documents, memories=memories, search=search)
    if set(seal) != {'documents', 'memories', 'search'} or not all(re.fullmatch(r'[0-9a-f]{64}', value) for value in seal.values()):
        return False
    changed = dict(search, results=[dict(search['results'][0], similarity=0.9)])
    altered = P.sm_state_seal(documents=documents, memories=memories, search=changed)
    if altered['search'] == seal['search'] or altered['documents'] != seal['documents'] or altered['memories'] != seal['memories']:
        return False
    changed_doc = [dict(documents[0], metadata={'probe_marker': 2})]
    doc_seal = P.sm_state_seal(documents=changed_doc, memories=memories, search=search)
    if doc_seal['documents'] == seal['documents'] or any(doc_seal[name] != seal[name] for name in ('memories', 'search')):
        return False
    changed_memory = [dict(memories[0], metadata={'probe_marker': 2})]
    memory_seal = P.sm_state_seal(documents=documents, memories=changed_memory, search=search)
    if memory_seal['memories'] == seal['memories'] or any(memory_seal[name] != seal[name] for name in ('documents', 'search')):
        return False
    if P.sm_state_seal(documents=documents, memories=memories, search=dict(search, timing=123)) != seal:
        return False
    if not named_api_refusal(lambda: P.sm_state_seal(documents=documents, memories=[], search=search), 'state-seal-input'):
        return False
    return P.sm_state_seal(documents=documents, memories=memories, search=search) == seal


check('A8S-STATE-PAGES-1: exhaustive stable memory pages retain every unique row and reject changed totals', state_collection_controls)
check('A8S-STATE-SEARCH-1: memory-mode search ties results to complete known memory and finite scores', state_search_controls)
check('A8S-STATE-TIME-1: createdAt is an observed timezone-aware document timestamp', state_created_at_controls)
check('A8S-STATE-SEAL-1: exactly three complete semantic state parts remain independently comparable', state_seal_controls)

ROUTE_PARSER = load('v3_actual_tee_parser_for_sm_routes', 'research/_llm_proxy.py')


def route_chat_fixture():
    names = ['searchMemories', 'readChunks', 'searchDocumentChunks', 'getMemoryDetails', 'linkMemories', 'forgetMemory', 'CreateMemory']
    return {'path': '/chat/completions', 'body': {'model': 'deepseek-flash', 'max_tokens': 12000,
        'messages': [{'role': 'user', 'content': 'diagnostic'}],
        'tools': [{'type': 'function', 'function': {'name': name, 'parameters': {'type': 'object'}}} for name in names]}}


def route_llm_fixture(observed=None):
    body = (observed or route_chat_fixture())['body']
    raw = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    parser = ROUTE_PARSER.TeeParser()
    parser.feed(json.dumps({'model': 'deepseek-flash', 'choices': [{'message': {'role': 'assistant', 'content': '',
        'tool_calls': [{'id': 'create', 'type': 'function', 'function': {'name': 'CreateMemory', 'arguments': '{}'}}]}}]}).encode('utf-8'))
    parsed = parser.result('application/json', 'v1', None)
    return {**parsed, 'arm': 'supermemory-local', 'unit': 'unit.u1', 'port_role': 'write', 'endpoint': 'v1',
            'method': 'POST', 'stage': 'write', 'requested_model': 'deepseek-flash', 'response_model': 'deepseek-flash',
            'status': 200, 'complete': True, 'client_abandoned': False, 'tool_violation': False,
            'refused': None, 'temperature': None, 'max_tokens': 12000, 'thinking_sent': None,
            'request_key': hashlib.sha256(raw + b'\0supermemory-local').hexdigest(),
            'thinking_injected': 0, 'tools_offered': ['searchMemories', 'readChunks', 'searchDocumentChunks',
                'getMemoryDetails', 'linkMemories', 'forgetMemory', 'CreateMemory'], 'tools_called': ['CreateMemory']}


def route_embed_fixture():
    leg = {'arm': 'supermemory-local', 'unit': 'unit.u1', 'path': '/v1/embeddings', 'stage': 'write',
           'is_embed': True, 'is_llm': False, 'fallback_local': False, 'client_auth': 'bearer',
           'status': 200, 'error': None}
    upstream = {'method': 'POST', 'path': '/v1/embeddings', 'body': {'model': 'bge-m3:latest', 'input': ['diagnostic']},
                'header_names': ['accept', 'connection', 'content-length', 'content-type', 'host']}
    return leg, upstream


def route_llm_controls():
    row = route_llm_fixture()
    observed = [route_chat_fixture()]
    result = P.sm_llm_route_fields([dict(row, arm='scheduler'), row], observed, run='unit', model='deepseek-flash')
    if (row['content_empty'] is not False or row['parse_ok'] is not True or row['tools_called'] != ['CreateMemory']
            or result['calls'] != 1 or result['unit'] != 'unit.u1' or result['runtime_schema_digests_observed'] is not False):
        return False
    if not named_api_refusal(lambda: P.sm_llm_route_fields([dict(row, arm='scheduler')], observed, run='unit', model='deepseek-flash'), 'blocked:no-llm-call'):
        return False
    for field, value, reason in (
        ('unit', 'other.u1', 'route-attribution'), ('port_role', 'reader', 'llm-route-record'),
        ('requested_model', 'another', 'llm-route-record'), ('response_model', 'another', 'llm-route-record'),
        ('complete', False, 'llm-route-record'), ('client_abandoned', True, 'llm-route-record'),
        ('parse_ok', False, 'llm-response-unusable'), ('json_ok', False, 'llm-response-unusable'),
        ('content_empty', True, 'llm-response-unusable'),
        ('status', True, 'llm-route-record'), ('status', 503, 'llm-route-record'),
        ('tools_offered', row['tools_offered'][:-1], 'llm-body-tools'),
        ('tools_offered', row['tools_offered'] + ['CreateMemory'], 'llm-body-tools'),
        ('tools_offered', row['tools_offered'][:-1] + ['foreign'], 'llm-body-tools'),
        ('tools_called', ['foreign'], 'llm-tools-baseline'), ('max_tokens', 12001, 'llm-parameters'),
        ('max_tokens', True, 'llm-parameters'), ('temperature', 0.5, 'llm-parameters'),
        ('tool_violation', True, 'blocked:tools-refused')):
        if not named_api_refusal(lambda field=field, value=value: P.sm_llm_route_fields([dict(row, **{field: value})], observed,
                run='unit', model='deepseek-flash'), reason):
            return False
    missing = dict(row)
    missing.pop('temperature')
    if not named_api_refusal(lambda: P.sm_llm_route_fields([missing], observed, run='unit', model='deepseek-flash'), 'llm-parameters'):
        return False
    for digest in (None, '0' * 64, 'not-a-digest'):
        if not named_api_refusal(lambda digest=digest: P.sm_llm_route_fields([dict(row, request_key=digest)], observed,
                run='unit', model='deepseek-flash'), 'llm-body-observation'):
            return False
    missing_digest = dict(row)
    missing_digest.pop('request_key')
    mismatched = route_chat_fixture()
    mismatched['body']['messages'][0]['content'] = 'another request'
    if (not named_api_refusal(lambda: P.sm_llm_route_fields([missing_digest], observed, run='unit', model='deepseek-flash'), 'llm-body-observation')
            or not named_api_refusal(lambda: P.sm_llm_route_fields([row], [mismatched], run='unit', model='deepseek-flash'), 'llm-body-observation')
            or not named_api_refusal(lambda: P.sm_llm_route_fields([row], [], run='unit', model='deepseek-flash'), 'llm-body-observation')):
        return False
    explicit_null = route_chat_fixture()
    explicit_null['body']['temperature'] = None
    if not named_api_refusal(lambda: P.sm_llm_route_fields([route_llm_fixture(explicit_null)], [explicit_null],
            run='unit', model='deepseek-flash'), 'llm-parameters'):
        return False
    if not named_api_refusal(lambda: P.sm_llm_route_fields([dict(row, content_empty=True, tools_called=[])], observed,
            run='unit', model='deepseek-flash'), 'llm-response-unusable'):
        return False
    auxiliary = route_chat_fixture()
    auxiliary['body'].pop('tools')
    auxiliary['body']['max_tokens'] = 64
    aux_row = dict(route_llm_fixture(auxiliary), tools_offered=[], tools_called=[], content_empty=False, max_tokens=64)
    result = P.sm_llm_route_fields([row, aux_row], observed + [auxiliary], run='unit', model='deepseek-flash')
    if result['calls'] != 2 or result['memory_agent_candidates'] != 1 or result['parameters'][1]['max_tokens'] != 64:
        return False
    return named_api_refusal(lambda: P.sm_llm_route_fields([aux_row], [auxiliary], run='unit', model='deepseek-flash'), 'blocked:no-llm-call')


def route_embed_controls():
    leg, upstream = route_embed_fixture()
    result = P.sm_embed_route_fields([leg], [upstream], run='unit', embed_model='bge-m3:latest')
    if result['calls'] != 1 or result['upstream_calls'] != 1 or result['auth_forwarded'] is not False:
        return False
    if not named_api_refusal(lambda: P.sm_embed_route_fields([], [upstream], run='unit', embed_model='bge-m3:latest'), 'blocked:no-embed-call'):
        return False
    for field, value, reason in (
        ('unit', 'other.u1', 'route-attribution'), ('is_embed', False, 'embed-route-record'),
        ('is_llm', True, 'embed-route-record'), ('fallback_local', True, 'embed-route-record'),
        ('client_auth', 'none', 'embed-route-record'), ('status', 503, 'embed-route-record'),
        ('error', 'late', 'embed-route-record'), ('path', '/api/generate', 'embed-route-record')):
        if not named_api_refusal(lambda field=field, value=value: P.sm_embed_route_fields([dict(leg, **{field: value})],
                [upstream], run='unit', embed_model='bge-m3:latest'), reason):
            return False
    for names in (None, ['host', 'content-length', 'authorization'], ['host', 'content-length', 'cookie'],
                  ['host', 'content-length', 'X-Api-Key'], ['host', 'host', 'content-length'], ['content-length']):
        if not named_api_refusal(lambda names=names: P.sm_embed_route_fields([leg], [dict(upstream, header_names=names)],
                run='unit', embed_model='bge-m3:latest'), 'embedding-header-boundary'):
            return False
    if not named_api_refusal(lambda: P.sm_embed_route_fields([leg], [dict(upstream, body={'model': 'other'})],
            run='unit', embed_model='bge-m3:latest'), 'embed-upstream-model'):
        return False
    for value in (None, [], '', [''], [1]):
        if not named_api_refusal(lambda value=value: P.sm_embed_route_fields([leg],
                [dict(upstream, body={'model': 'bge-m3:latest', 'input': value})],
                run='unit', embed_model='bge-m3:latest'), 'embed-upstream-input'):
            return False
    listing_leg = dict(leg, path='/api/tags', is_embed=False, client_auth='none', stage=None)
    listing = dict(upstream, path='/api/tags', method='GET', body={})
    mixed = P.sm_embed_route_fields([listing_leg, leg], [listing, upstream], run='unit', embed_model='bge-m3:latest')
    if mixed['calls'] != 1 or mixed['listing_calls'] != 1:
        return False
    if not named_api_refusal(lambda: P.sm_embed_route_fields([listing_leg], [listing],
            run='unit', embed_model='bge-m3:latest'), 'blocked:no-embed-call'):
        return False
    return named_api_refusal(lambda: P.sm_embed_route_fields([leg], [upstream, upstream],
        run='unit', embed_model='bge-m3:latest'), 'embed-witness-count')


def functional_fixture(run='unit', embed_model='bge-m3:latest'):
    fixture = SimpleNamespace(key=object(), now=0.0, requests=[], admissions=[], polls=0,
                              dot_status=400, empty=False, foreign_search=False, schema=sm_operations_schema_fixture())
    leg, upstream = route_embed_fixture()
    fixture.witness = {'complete': True, 'problems': [], 'calls': [route_llm_fixture()],
                       'ollama': [leg], 'upstream_requests': [upstream], 'chat_requests': [route_chat_fixture()]}
    fixture.witness['calls'][0]['unit'] = leg['unit'] = run + '.u1'
    upstream['body']['model'] = embed_model
    fixture.entry = {'id': 'm1', 'memory': 'Rina keeps the cobalt telescope in locker 42.', 'documentIds': ['doc1']}
    fixture.document = {'id': 'doc1', 'status': 'done', 'createdAt': '2026-10-03T12:00:00.123456789Z'}
    def request(port, method, path, **kwargs):
        fixture.requests.append((port, method, path, kwargs))
        fixture.now += 0.01
        if path in ('/v3/openapi', '/v4/openapi'):
            return sm_operation_schema_replies(fixture.schema)[0]
        if path == '/v4/memories/list' and kwargs['body']['containerTags'] == [run + '.u1']:
            return {'status': fixture.dot_status, 'json': {'error': 'invalid tag'}}
        if path == '/v3/documents':
            return {'status': 200, 'json': {'id': 'doc1', 'status': 'queued'}}
        if path == '/v3/documents/doc1':
            fixture.polls += 1
            return {'status': 200, 'json': dict(fixture.document, status='processing' if fixture.polls == 1 else 'done')}
        if path == '/v4/memories/list':
            return {'status': 200, 'json': {'memoryEntries': [] if fixture.empty else [fixture.entry],
                'pagination': {'currentPage': 1, 'limit': 10, 'totalItems': 0 if fixture.empty else 1, 'totalPages': 0 if fixture.empty else 1}}}
        if path == '/v4/search':
            return {'status': 200, 'json': {'results': [{'id': 'foreign' if fixture.foreign_search else 'm1',
                'memory': fixture.entry['memory'], 'similarity': 0.9}], 'total': 1, 'timing': 1}}
        raise AssertionError('undeclared request')
    def admit():
        fixture.admissions.append(fixture.now)
        return True
    def sleep(seconds):
        fixture.now += seconds
    fixture.request, fixture.admit, fixture.sleep = request, admit, sleep
    def invoke(**changes):
        options = {'key': fixture.key, 'rest': REST_SM, 'request': fixture.request, 'admit': fixture.admit,
                   'prepare_routes': lambda **kwargs: True,
                   'observe_routes': lambda **kwargs: fixture.witness, 'model': 'deepseek-flash', 'embed_model': embed_model,
                   'clock': lambda: fixture.now, 'sleep': fixture.sleep}
        options.update(changes)
        return P.run_sm_p3(1234, run, **options)
    fixture.run = invoke
    return fixture


def functional_sequence_controls():
    f = functional_fixture()
    result = f.run()
    expected = [('GET', '/v3/openapi'), ('GET', '/v4/openapi'), ('POST', '/v4/memories/list'),
                ('POST', '/v3/documents'), ('GET', '/v3/documents/doc1'), ('GET', '/v3/documents/doc1'),
                ('POST', '/v4/memories/list'), ('POST', '/v4/search')]
    if [(method, path) for _, method, path, _ in f.requests] != expected or len(f.admissions) != len(expected) + 1:
        return False
    if any(port != 1234 or options.get('key') is not f.key or not 0 < options['timeout_s'] <= 5
            for port, _, _, options in f.requests):
        return False
    if (f.requests[3][3]['body']['containerTag'] != 'unit_u1' or f.requests[-1][3]['body']['containerTags'] != ['unit_u1']
            or result['processing'] != {'polls': 2, 'status': 'done', 'memories': 1}
            or result['created_at']['utc'] != '2026-10-03T12:00:00.123456789+00:00'):
        return False
    return (set(result['seal']) == {'documents', 'memories', 'search'} and result['search']['found'] is True
            and result['llm']['calls'] == result['embeddings']['calls'] == 1
            and result['schema']['value_constraints_verified'] is False and result['runtime_admission'] is False
            and result['writers_quiescent_proven'] is False and result['atomic_snapshot_proven'] is False)


def functional_refusal_controls():
    f = functional_fixture()
    del f.schema['paths']['/v3/documents']['post']['requestBody']['content']['application/json']['schema']['properties']['content']
    if not named_api_refusal(f.run, 'blocked:schema-missing:add_document') or any(path == '/v3/documents' for _, _, path, _ in f.requests):
        return False
    f = functional_fixture()
    f.dot_status = 200
    if not named_api_refusal(f.run, 'container-tag-control') or any(path == '/v3/documents' for _, _, path, _ in f.requests):
        return False
    f = functional_fixture()
    f.empty = True
    f.witness['calls'][0]['tool_violation'] = True
    if not named_api_refusal(f.run, 'blocked:tools-refused') or any(path == '/v4/search' for _, _, path, _ in f.requests):
        return False
    f = functional_fixture()
    f.empty = True
    if not named_api_refusal(f.run, 'memory-agent-empty'):
        return False
    f = functional_fixture()
    f.foreign_search = True
    if not named_api_refusal(f.run, 'blocked:search-shape'):
        return False
    f = functional_fixture()
    f.witness['complete'] = False
    if not named_api_refusal(f.run, 'route-observation-incomplete'):
        return False
    f = functional_fixture()
    f.witness['problems'] = ['partial writer']
    return named_api_refusal(f.run, 'route-observation-incomplete')


def functional_deadline_controls():
    f = functional_fixture()
    if not named_api_refusal(lambda: f.run(key=None), 'held-key-required') or f.requests:
        return False
    f = functional_fixture()
    def late_admit():
        f.now = 1
        return True
    if not named_api_refusal(lambda: f.run(admit=late_admit, ceiling_s=0.5), 'state-read-deadline') or f.requests:
        return False
    f = functional_fixture()
    def late_reply(*args, **kwargs):
        reply = f.request(*args, **kwargs)
        f.now = 1
        return reply
    if not named_api_refusal(lambda: f.run(request=late_reply, ceiling_s=0.5), 'state-read-deadline'):
        return False
    f = functional_fixture()
    def late_observer(**kwargs):
        f.now = 180
        return f.witness
    if not named_api_refusal(lambda: f.run(observe_routes=late_observer), 'state-read-deadline'):
        return False
    f = functional_fixture()
    original_seal = P.sm_state_seal
    def late_seal(**kwargs):
        result = original_seal(**kwargs)
        f.now = 180
        return result
    with mock.patch.object(P, 'sm_state_seal', side_effect=late_seal):
        return named_api_refusal(f.run, 'state-read-deadline')


check('A8S-ROUTE-LLM-1: attributed complete calls retain pinned parameters and exact offered tools', route_llm_controls)
check('A8S-ROUTE-EMBED-1: attributed embeddings pair with positive independently observed stripped upstream headers', route_embed_controls)
check('A8S-P3-SEQUENCE-1: one keyed diagnostic sequence composes schema polling memory search and routes', functional_sequence_controls)
check('A8S-P3-REFUSAL-1: schema dot control actual tools refusal empty memory and incomplete observations cannot pass', functional_refusal_controls)
check('A8S-P3-DEADLINE-1: all sends observations parsing and seals share one finite allowance', functional_deadline_controls)


def route_reader_fixture(root):
    arm = root / '_a8/unit' / 'supermemory-local'
    proxy_dir = arm / '_proxy/run'
    proxy_dir.mkdir(parents=True)
    contract = SimpleNamespace(runs_root=root)
    leg, entry = route_embed_fixture()
    (proxy_dir / 'calls.jsonl').write_text(json.dumps(route_llm_fixture()) + '\n', encoding='utf-8')
    (proxy_dir / 'ollama.jsonl').write_text(json.dumps(leg) + '\n', encoding='utf-8')
    upstream = SimpleNamespace(_lock=threading.Lock(), closing=False, errors=[], traps=[], unknown=[], requests=[entry])
    chat = SimpleNamespace(_lock=threading.Lock(), closing=False, errors=[], traps=[], unknown=[], requests=[route_chat_fixture()])
    fixture = SimpleNamespace(now=0.0, controls=[], count=1, open=0)
    def control(**kwargs):
        fixture.controls.append(kwargs['timeout_s'])
        fixture.now += 0.001
        return {'status': 200, 'json': {'supermemory-local': {'forward_open': fixture.open, 'records': fixture.count}}}
    def sleep(seconds):
        fixture.now += seconds
    fixture.control = control
    def run(**options):
        kwargs = {'run': 'unit', 'proxy_dir': proxy_dir, 'upstream': upstream, 'chat_upstream': chat, 'control': fixture.control,
                  'clock': lambda: fixture.now, 'sleep': sleep}
        kwargs.update(options)
        return P.read_sm_routes(contract, L, **kwargs)
    fixture.run, fixture.directory, fixture.upstream, fixture.chat, fixture.contract = run, proxy_dir, upstream, chat, contract
    return fixture


def route_reader_shape_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-route-reader-') as directory:
        f = route_reader_fixture(Path(directory).resolve())
        result = f.run()
        if (result['complete'] is not True or len(result['calls']) != 1 or len(result['ollama']) != 1
                or len(result['upstream_requests']) != 1 or len(f.controls) != 2
                or result['writers_quiescent_proven'] is not False):
            return False
        bad = SimpleNamespace(_within=L._within, _link_in_chain=lambda base, target: str(target).endswith('calls.jsonl'))
        with mock.patch.object(Path, 'open', side_effect=AssertionError('invalid path must not open')):
            if not named_api_refusal(lambda: P.read_sm_routes(f.contract, bad, run='unit', proxy_dir=f.directory,
                    upstream=f.upstream, chat_upstream=f.chat, control=f.control), 'route-log-path'):
                return False
            if not named_api_refusal(lambda: f.run(proxy_dir=f.directory.parent), 'route-log-path'):
                return False
        (f.directory / 'calls.jsonl').write_bytes(b'{"arm":"supermemory-local"}')
        if not named_api_refusal(f.run, 'route-log-incomplete'):
            return False
        (f.directory / 'calls.jsonl').write_bytes(b'{}\n')
        if not named_api_refusal(f.run, 'route-record-count'):
            return False
        (f.directory / 'calls.jsonl').write_text(json.dumps(route_llm_fixture()) + '\n', encoding='utf-8')
        f.upstream.errors.append('incomplete handler')
        return named_api_refusal(f.run, 'route-upstream-incomplete')


def route_reader_deadline_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-route-drain-') as directory:
        f = route_reader_fixture(Path(directory).resolve())
        f.open = 1
        if not named_api_refusal(lambda: f.run(timeout_s=0.05), 'state-read-deadline'):
            return False
        if not f.controls or any(not 0 < value <= 0.05 for value in f.controls):
            return False
        f.open, f.now, f.controls = 0, 0.0, []
        original = f.control
        def changing(**kwargs):
            result = original(**kwargs)
            f.count += 1
            return result
        if not named_api_refusal(lambda: f.run(control=changing, timeout_s=0.05), 'state-read-deadline'):
            return False
        f.count, f.now, f.controls = 1, 0.0, []
        def late(**kwargs):
            result = original(**kwargs)
            if len(f.controls) == 2:
                f.now = 0.05
            return result
        if not named_api_refusal(lambda: f.run(control=late, timeout_s=0.05), 'state-read-deadline'):
            return False
        f.now, f.controls = 0.0, []
        f.count = True
        if not named_api_refusal(f.run, 'route-counter-observation'):
            return False
        f.count, f.now, f.controls = 1, 0.0, []
        def decreasing(**kwargs):
            result = original(**kwargs)
            f.count = 0
            return result
        if not named_api_refusal(lambda: f.run(control=decreasing), 'route-counter-regressed'):
            return False
        class Busy:
            def __init__(self):
                self.timeouts = []
            def acquire(self, *, timeout):
                self.timeouts.append(timeout)
                return False
            def release(self):
                raise AssertionError('an unacquired lock must not be released')
        busy = Busy()
        blocked = SimpleNamespace(_lock=busy)
        if not named_api_refusal(lambda: P.bounded_sm_upstream_snapshot(blocked, P._SmReadDeadline(0.05, lambda: 0)), 'route-upstream-lock'):
            return False
        if busy.timeouts != [0.05]:
            return False
        f.upstream.requests = [{'body': 'x' * (2 << 20)}]
        with mock.patch.object(P.json, 'dumps', side_effect=AssertionError('oversize must refuse before serialization')):
            if not named_api_refusal(lambda: P.bounded_sm_upstream_snapshot(f.upstream, P._SmReadDeadline(1, time.monotonic)), 'route-upstream-ceiling'):
                return False
        return f.upstream._lock.acquire(blocking=False) and (f.upstream._lock.release() is None)


def connected_p3_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-p3-controller-') as directory:
        f = protocol_fixture(Path(directory).resolve())
        diagnostic = functional_fixture(run='boot-control', embed_model='owned')
        previous_setup = f.deps.setup
        def setup(*args):
            runtime = previous_setup(*args)
            original_request = runtime.request
            def request(port, method, path, **options):
                if path in ('/', '/owned-pinned-auth-control'):
                    return original_request(port, method, path, **options)
                return diagnostic.request(port, method, path, **options)
            runtime.request, runtime.p3_enabled, runtime.rest = request, True, REST_SM
            runtime.model = 'deepseek-flash'
            runtime.prepare_routes = lambda **options: True
            runtime.observe_routes = lambda **options: diagnostic.witness
            return runtime
        f.deps.setup = setup
        result = f.run_probe()
        fields = result['fields']
        if (result['phase'] != 'P0-P3' or 'functional' not in result or fields['sm_llm_params']['ok'] is not True
                or fields['sm_embed_route']['ok'] is not True or fields['sm_leg_auth']['ok'] is not True
                or fields['sm_schema']['ok'] is not False or fields['sm_schema']['value']['value_constraints_verified'] is not False):
            return False
        methods = [(method, path) for _, method, path, _ in diagnostic.requests]
        keys = [options.get('key') for _, _, _, options in diagnostic.requests]
        return (methods[0:2] == [('GET', '/v3/openapi'), ('GET', '/v4/openapi')]
                and len(keys) >= 7 and all(key == SS.HeldKey(b'sm_OWNED_TEST_KEY_123456') for key in keys)
                and result['outcome'] == 'fail' and result['fields']['sm_key_search']['ok'] is True
                and result['fields']['sm_stop']['ok'] is True and 'stop' in f.events
                and f.events[-1] == 'close:fake')


check('A8S-ROUTE-READER-1: exact owned bounded logs and immutable upstream snapshots reject links partial lines and missing counts', route_reader_shape_controls)
check('A8S-ROUTE-READER-TIME-1: idle stable counters and every observer action share one finite allowance', route_reader_deadline_controls)
check('A8S-P3-CONTROLLER-1: live controller connects P3 through held key and keeps partial schema proof unsuccessful', connected_p3_controls)


def owned_stage_controls():
    import socket
    proxy_module = load('v3_actual_control_for_sm_stage', 'research/_llm_proxy.py')
    producer = SimpleNamespace(config=SimpleNamespace(control_token='owned-stage-token'),
                               stage={'block': None, 'stage': None})
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(4)
    listener.settimeout(0.05)
    stop, errors = threading.Event(), []
    def serve():
        while not stop.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not stop.is_set():
                    errors.append('accept')
                break
            with client:
                client.settimeout(1)
                try:
                    proxy_module.Proxy._control(producer, client)
                except Exception as exc:
                    errors.append(type(exc).__name__)
    worker = threading.Thread(target=serve, name='sm-control-producer', daemon=True)
    worker.start()
    resources = []
    held = SimpleNamespace(header=lambda: {'Authorization': 'Bearer owned-stage-token'})
    stage = P.OwnedProxyStage(listener.getsockname()[1], held, resources)
    try:
        if len(resources) != 1 or stage.prepare(timeout_s=1) is not True:
            return False
        if producer.stage != {'block': '_a8/p0p2', 'stage': 'write'}:
            return False
        row = resources[0][1]()
        if not resources[0][2](row) or producer.stage != {'block': None, 'stage': None} or stage.close() is not row:
            return False
        if not named_api_refusal(lambda: stage.prepare(timeout_s=1), 'proxy-stage-bounds'):
            return False
        sends = []
        def ambiguous(*args, **kwargs):
            sends.append(kwargs['body'])
            if kwargs['body']['stage'] is not None:
                raise TimeoutError('synthetic late acknowledgment')
            return {'status': 200, 'json': {'ok': True}}
        ambiguous_stage = P.OwnedProxyStage(1234, held, [], request=ambiguous)
        if not refused(lambda: ambiguous_stage.prepare(timeout_s=1), TimeoutError):
            return False
        if ambiguous_stage.close()['complete'] is not True or sends[-1] != {'block': None, 'stage': None}:
            return False
        failed_reset = P.OwnedProxyStage(1234, held, [], request=lambda *a, **k: {'status': 503, 'json': {}})
        if failed_reset.prepare(timeout_s=1) is not False or failed_reset.close()['complete'] is not False:
            return False
        f = functional_fixture()
        if not named_api_refusal(lambda: f.run(prepare_routes=lambda **kwargs: False), 'proxy-write-stage') or f.requests:
            return False
        f = functional_fixture()
        def late_prepare(**kwargs):
            f.now = 180
            return True
        if not named_api_refusal(lambda: f.run(prepare_routes=late_prepare), 'state-read-deadline') or f.requests:
            return False
        return not errors
    finally:
        stage.close()
        stop.set()
        listener.close()
        worker.join(1)
        if worker.is_alive():
            raise AssertionError('owned control worker must drain')


def declared_chat_policy_controls():
    upstream = load('v3_actual_upstream_for_sm_policy', 'research/v3/probe_upstream.py')
    resources = []
    fake = P.OwnedUpstream(upstream, 'deepseek', resources=resources, script_policy=P.SM_CHAT_POLICY)
    try:
        agent = route_chat_fixture()['body']
        auxiliary = {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': 'scheduler'}], 'max_tokens': 64}
        def request(body):
            status, reply = P.bounded_proxy_post(fake.port, '/chat/completions', body, 'owned-test', timeout=1)
            if status != 200:
                raise AssertionError('declared fake must answer')
            return reply['choices'][0]['message']
        if request(auxiliary).get('content') != '{}':
            return False
        first = request(agent)
        calls = first.get('tool_calls', [])
        if len(calls) != 1 or calls[0]['function']['name'] != 'CreateMemory':
            return False
        arguments = json.loads(calls[0]['function']['arguments'])
        if arguments != {'memory': 'Rina keeps the cobalt telescope in locker 42 at the Seoul observatory.', 'temporalContext': {}}:
            return False
        if request(auxiliary).get('content') != '{}' or request(agent) != first:
            return False
        completed = copy.deepcopy(agent)
        completed['messages'] += [{'role': 'assistant', **first},
            {'role': 'tool', 'tool_call_id': calls[0]['id'], 'content': '{"ok":true}'}]
        final = request(completed)
        if final.get('tool_calls') or final['content'] != 'Diagnostic memory recorded.':
            return False
        if request(auxiliary).get('content') != '{}':
            return False
        reply = P.sm_chat_script_entry(fake.script, 200, agent)
        reply['tool_calls'][0]['function']['arguments'] = '{}'
        if P.sm_chat_script_entry(fake.script, 200, agent)['tool_calls'][0]['function']['arguments'] == '{}':
            return False
        return len(fake.requests) == 6 and not fake.errors
    finally:
        result = fake.close(drain_s=1)
        if not resources[0][2](result) or result['active_sockets'] or result['live_workers']:
            raise AssertionError('declared fake must drain')


check('A8S-P3-STAGE-1: actual control interface writes and resets stage including uncertain acknowledgment and pre-API refusal', owned_stage_controls)
check('A8S-P3-SCRIPT-1: actual HTTP handler chooses declared CreateMemory with valid arguments independently of interleaved calls', declared_chat_policy_controls)

def restart_read_fixture():
    f = functional_fixture()
    f.polls = 1  # The already completed diagnostic document; this is a read-only sequence.
    def run(**changes):
        options = {'key': f.key, 'request': f.request, 'admit': f.admit, 'clock': lambda: f.now}
        options.update(changes)
        return P.read_sm_restart_state(1234, 'unit', 'doc1', **options)
    f.read = run
    return f


def restart_read_controls():
    f = restart_read_fixture()
    result = f.read()
    expected = [('GET', '/v3/documents/doc1'), ('POST', '/v4/memories/list'), ('POST', '/v4/search')]
    return ([(method, path) for _, method, path, _ in f.requests] == expected
            and len(f.admissions) == 3 and all(options['key'] is f.key and 0 < options['timeout_s'] <= 5
                for _, _, _, options in f.requests)
            and result['complete'] is True and result['document_id'] == 'doc1'
            and result['seal'] == P.sm_state_seal(**result['state'])
            and set(result['seal']) == {'documents', 'memories', 'search'}
            and result['atomic_snapshot_proven'] is False and result['writers_quiescent_proven'] is False)


def restart_read_refusal_controls():
    f = restart_read_fixture()
    if not named_api_refusal(lambda: f.read(key=None), 'held-key-required') or f.requests:
        return False
    f = restart_read_fixture()
    f.polls = 0
    if not named_api_refusal(f.read, 'restart-document-incomplete') or len(f.requests) != 1:
        return False
    f = restart_read_fixture()
    f.empty = True
    if not named_api_refusal(f.read, 'memory-agent-empty') or len(f.requests) != 2:
        return False
    f = restart_read_fixture()
    f.foreign_search = True
    if not named_api_refusal(f.read, 'blocked:search-shape'):
        return False
    f = restart_read_fixture()
    original_request = f.request
    unlinked = {'id': 'm2', 'memory': 'An unrelated memory.', 'documentIds': []}
    def known_unlinked_request(port, method, path, **options):
        reply = original_request(port, method, path, **options)
        if path == '/v4/memories/list':
            reply['json']['memoryEntries'].append(unlinked)
            reply['json']['pagination']['totalItems'] = 2
        if path == '/v4/search':
            reply['json']['results'] = [{'id': 'm2', 'memory': unlinked['memory'], 'similarity': 0.9}]
        return reply
    if not named_api_refusal(lambda: f.read(request=known_unlinked_request), 'blocked:search-shape') or len(f.requests) != 3:
        return False
    f = restart_read_fixture()
    def late_admit():
        f.now = 60
        return True
    if not named_api_refusal(lambda: f.read(admit=late_admit), 'state-read-deadline') or f.requests:
        return False
    f = restart_read_fixture()
    original = P.sm_state_seal
    def late_seal(**state):
        seal = original(**state)
        f.now = 60
        return seal
    with mock.patch.object(P, 'sm_state_seal', side_effect=late_seal):
        return named_api_refusal(f.read, 'state-read-deadline')


def restart_comparison_controls():
    first = restart_read_fixture().read()
    same = P.compare_sm_restart_states(first, copy.deepcopy(first))
    if same['equal'] is not True or same['parts'] != {'documents': True, 'memories': True, 'search': True}:
        return False
    for part in ('documents', 'memories', 'search'):
        second = copy.deepcopy(first)
        if part == 'documents':
            second['state']['documents'][0]['createdAt'] = '2026-10-03T12:00:01.123456789Z'
        elif part == 'memories':
            second['state']['memories'].append({'id': 'm2', 'memory': 'another memory', 'documentIds': []})
        else:
            second['state']['search']['results'][0]['similarity'] = 0.8
        second['seal'] = P.sm_state_seal(**second['state'])
        result = P.compare_sm_restart_states(first, second)
        if result['equal'] is not False or result['parts'] != {name: name != part for name in ('documents', 'memories', 'search')}:
            return False
    stale = copy.deepcopy(first)
    stale['state']['documents'][0]['createdAt'] = '2026-10-03T12:00:01.123456789Z'
    if not named_api_refusal(lambda: P.compare_sm_restart_states(first, stale), 'restart-state-seal-mismatch'):
        return False
    incomplete = copy.deepcopy(first)
    incomplete['complete'] = False
    if not named_api_refusal(lambda: P.compare_sm_restart_states(first, incomplete), 'restart-state-shape'):
        return False
    missing = copy.deepcopy(first)
    missing['seal'].pop('memories')
    if not named_api_refusal(lambda: P.compare_sm_restart_states(first, missing), 'restart-state-shape'):
        return False
    processing = copy.deepcopy(first)
    processing['state']['documents'][0]['status'] = 'processing'
    # Retain the old seal: the completion guard must refuse before recomputation.
    if not named_api_refusal(lambda: P.compare_sm_restart_states(first, processing), 'restart-document-incomplete'):
        return False
    now = [0.0]
    original = P.sm_state_seal
    def late(**state):
        seal = original(**state)
        now[0] = 5
        return seal
    with mock.patch.object(P, 'sm_state_seal', side_effect=late):
        if not named_api_refusal(lambda: P.compare_sm_restart_states(first, first, clock=lambda: now[0]), 'state-read-deadline'):
            return False
    return same['atomic_snapshot_proven'] is False and same['writers_quiescent_proven'] is False


check('A8S-RESTART-READ-1: three keyed reads use fresh admission complete memory and known search with exact state seals', restart_read_controls)
check('A8S-RESTART-REFUSAL-1: missing key incomplete document empty memory foreign search and late parsing refuse', restart_read_refusal_controls)
check('A8S-RESTART-COMPARE-1: recomputed document memory and search seals compare independently without claiming atomicity', restart_comparison_controls)

def boot2_ports_fixture():
    return {'scheduler': 43101, 'control': 43102, 'j3': 43103,
            'arms': {P.ARM: {'catcher': 43104, 'write': 43105, 'reader': 43106, 'ollama': 43107},
                     'owned-catcher': {'catcher': 43108}}}


def boot2_port_inventory_controls():
    ports = boot2_ports_fixture()
    expected = frozenset(range(43101, 43109))
    if P.sm_proxy_port_inventory(ports) != expected:
        return False
    allocate = mock.Mock(return_value=43110)
    negatives = [None, {}, dict(ports, control=True), dict(ports, control=43101),
                 dict(ports, extra=43109), dict(ports, arms={}), dict(ports, arms=[])]
    for roles in ({}, {'catcher': False}, {'catcher': 43108}, {'catcher': 43104, 'unknown': 43109}):
        negatives.append(dict(ports, arms={**ports['arms'], P.ARM: roles}))
    negatives.append(dict(ports, arms={**ports['arms'], '': {'catcher': 43111}}))
    for changed in negatives:
        if (not refused(lambda: P.allocate_sm_server_port(changed, allocate),
                        (P.PreflightRefused, P.SmRequestRefused)) or allocate.called):
            return False
    return True


def boot2_port_allocation_controls():
    ports = boot2_ports_fixture()
    for collided in (*range(43101, 43109), 43109):
        allocator = mock.Mock(return_value=collided)
        if not refused(lambda: P.allocate_sm_server_port(ports, allocator, prior_port=43109), P.PreflightRefused):
            return False
        if allocator.call_count != 1:
            return False
    allocator = mock.Mock(return_value=43110)
    if P.allocate_sm_server_port(ports, allocator, prior_port=43109) != 43110:
        return False
    allocator.reset_mock()
    if not refused(lambda: P.allocate_sm_server_port(ports, allocator, prior_port=True), P.SmRequestRefused) or allocator.called:
        return False
    def changed():
        ports['control'] = 43110
        return 43111
    if not refused(lambda: P.allocate_sm_server_port(ports, changed), P.PreflightRefused):
        return False
    return refused(lambda: P.allocate_sm_server_port(boot2_ports_fixture(), None), P.PreflightRefused)


def boot2_guard_fixture():
    bundle = load('v3_sm_bundle_for_boot2_env_test', 'research/v3/sm_bundle.py')
    defaults = {name: {'default': None} for name in P.SERVER_ENV_NAMES}
    defaults.update({'SUPERMEMORY_EMBEDDING_PROVIDER': {'default': 'local'},
        'SUPERMEMORY_EMBEDDING_BASE_URL': {'default': 'OPENAI_BASE_URL'},
        'OPENAI_FAST_MODEL': {'default': 'OPENAI_MODEL'}, 'OPENAI_TEXT_MODEL': {'default': 'OPENAI_MODEL'},
        'OPENAI_MODEL': {'default': 'gpt-5.1'}, 'PORT': {'default': 'SUPERMEMORY_PORT'}})
    env_rec = {'reads': defaults, 'must_be_absent': ['ANTHROPIC_API_KEY', 'GROQ_API_KEY',
        'WORKERS_AI_API_KEY', 'CLOUDFLARE_ACCOUNT_ID', 'GOOGLE_VERTEX_PROJECT_ID', 'GOOGLE_VERTEX_LOCATION', 'GEMINI_API_KEY']}
    declared = SS.server_env(env_rec, port=43109, data_dir='D:/owned/data',
        llm_base_url='http://127.0.0.1:43105/u/control.u1/v1', llm_model='deepseek-flash',
        embed_base_url='http://127.0.0.1:43107/u/control.u1/v1', embed_model='nvt3-bge-m3', token='owned-proxy-token')
    child = SimpleNamespace(spawn_id='owned-boot2-spawn')
    spy = mock.Mock(return_value=child)
    argv = ('D:/owned/never-executed.exe',)
    guard = P.GuardedLaunch(SimpleNamespace(spawn=spy), bundle, env_rec,
        run='control', argv=argv, expected_by_unit={'u1': declared})
    return SimpleNamespace(**locals())


def boot2_env_declaration_controls():
    ports = frozenset(range(43101, 43109))
    f = boot2_guard_fixture()
    original = copy.deepcopy(f.declared)
    f.guard.declare_restart(43110, occupied_ports=ports)
    expected = dict(original, SUPERMEMORY_PORT='43110')
    if f.guard.expected != {'u1': original, 'u1-boot2': expected} or f.declared != original:
        return False
    actual = {**expected, **f.bundle.SPAWN_ENV, 'HOME': 'owned-second-home'}
    record = {'role': 'server', 'stand': '_a8', 'run': 'control', 'arm': P.ARM, 'unit': 'u1-boot2'}
    if f.guard.spawn('owned-contract', f.argv, env=actual, record=record) is not f.child:
        return False
    if f.spy.call_args.kwargs['env'] is not actual or f.guard.observed[-1]['unit'] != 'u1-boot2':
        return False
    for name in P.SERVER_ENV_NAMES:
        changed = dict(actual, **{name: 'owned-wrong-value'})
        f.spy.reset_mock()
        if not refused(lambda: f.guard.spawn('owned-contract', f.argv, env=changed, record=record), P.PreflightRefused) or f.spy.called:
            return False
    for change in (dict(record, unit='u1-boot3'), dict(record, run='other')):
        if not refused(lambda: f.guard.spawn('owned-contract', f.argv, env=actual, record=change), P.PreflightRefused) or f.spy.called:
            return False
    if not refused(lambda: f.guard.declare_restart(43111, occupied_ports=ports), P.PreflightRefused):
        return False
    for occupied in (set(ports), frozenset(), frozenset({True}), frozenset({65536})):
        f = boot2_guard_fixture()
        before = copy.deepcopy(f.guard.expected)
        if not refused(lambda: f.guard.declare_restart(43110, occupied_ports=occupied), P.PreflightRefused) or f.guard.expected != before:
            return False
    for collided in (*ports, 43109):
        f = boot2_guard_fixture()
        if not refused(lambda: f.guard.declare_restart(collided, occupied_ports=ports), P.PreflightRefused) or set(f.guard.expected) != {'u1'}:
            return False
    for name in P.SERVER_ENV_NAMES:
        f = boot2_guard_fixture()
        f.guard.expected['u1'][name] = 'owned-mutated-original'
        if not refused(lambda: f.guard.declare_restart(43110, occupied_ports=ports), P.PreflightRefused) or 'u1-boot2' in f.guard.expected:
            return False
    return True


check('A8S-BOOT2-PORT-INVENTORY-1: every special and arm listener is validated before allocating a server port', boot2_port_inventory_controls)
check('A8S-BOOT2-PORT-ALLOCATE-1: collisions prior port and changed proxy inventory refuse without reusing a listener', boot2_port_allocation_controls)
check('A8S-BOOT2-ENV-1: exactly one restart clones original data key models and logical unit routes with only a fresh port', boot2_env_declaration_controls)

def p4p5_protocol_fixture(root, *, change=None, fail=None):
    f = protocol_fixture(root)
    diagnostic = functional_fixture(run='boot-control', embed_model='owned')
    previous_setup = f.deps.setup
    state = {'spawned': [], 'firewalls': [], 'port_checks': []}
    def setup(*args):
        runtime = previous_setup(*args)
        owned = f.state['fixture']
        state['owned'] = owned
        native = owned.scheduler.witnesses.native
        birth = {701: 25.0, 702: 26.0}
        native._identity = lambda pid: birth.get(pid)
        native.registered_roots[702] = 26.0
        native._thread = SimpleNamespace(is_alive=lambda: True)
        native._stop = threading.Event()
        native._t0 = 0.0
        runtime.witnesses.containers = []
        runtime.witnesses._before, runtime.witnesses._before_rows, runtime.witnesses._tags = {}, {}, {}
        second = SimpleNamespace(spawn_id='owned-second-spawn', process=SimpleNamespace(
            pid=702, stdout=object(), poll=mock.Mock(return_value=None), wait=mock.Mock(return_value=0)))
        original_spawn = owned.scheduler.spawn_child
        def spawn(build, **who):
            state['spawned'].append(who['unit'])
            if who['unit'] == 'u1':
                return original_spawn(build, **who)
            if who['unit'] != 'u1-boot2' or owned.child.process.poll() is None or owned.job.live[81]:
                raise AssertionError('second server precedes drained first server')
            dirs = SimpleNamespace(home=owned.arm / 'u1-boot2.home', cwd=owned.arm / 'u1-boot2')
            dirs.home.mkdir()
            dirs.cwd.mkdir()
            build(dirs)
            owned.job.jobs[702], owned.job.live[82] = 82, [702]
            owned.job._job_leases[82] = SimpleNamespace(handle=82, closed=False, uncertain=False)
            owned.job._root_leases[702] = owned.job._job_leases[82]
            owned.guard.children.append(second)
            owned.guard.observed.append({'unit': 'u1-boot2', 'spawn_id': second.spawn_id})
            state['dirs2'] = dirs
            return second, dirs
        owned.scheduler.spawn_child = spawn
        previous_pump = owned.server.RedactingPump
        class Pump(previous_pump):
            def __init__(self, stream, path):
                super().__init__(stream, path)
                self.second = path.parent.name == 'u1-boot2.home'
                self.joins = 0
                if self.second and fail == 'key2':
                    self.key = SS.HeldKey(b'sm_OWNED_TEST_KEY_OTHER123')
            def start(self):
                if not self.second:
                    return super().start()
                f.events.append('pump2')
                if not owned.data.is_dir() or (owned.data / 'api-key').read_bytes() != b'sm_OWNED_TEST_KEY_123456\n':
                    raise AssertionError('second server did not reuse original data/key')
                return self
            def join(self, timeout):
                self.joins += 1
                if fail == 'drain1' and not self.second and self.joins == 1:
                    return False
                return super().join(timeout)
        owned.server.RedactingPump = Pump
        def stopped(child):
            if owned.scheduler.witnesses._check is None:
                raise AssertionError('shutdown has no continuous witness')
            f.events.append('stop:' + str(child.process.pid))
            child.process.poll.return_value = 0
            if not (fail == 'killed1' and child.process.pid == 701):
                owned.job.live[owned.job.jobs[child.process.pid]] = []
            if fail == 'epoch-stop1' and child.process.pid == 701:
                native._thread = SimpleNamespace(is_alive=lambda: True)
            return {'how': 'ctrl_break', 'alive': False, 'reused': [], 'unverified': []}
        owned.server.stop = stopped
        original_request = runtime.request
        def request(port, method, path, **options):
            if path in ('/', '/owned-pinned-auth-control'):
                return original_request(port, method, path, **options)
            reply = diagnostic.request(port, method, path, **options)
            if fail == 'epoch-state1' and port == owned.port and 'fw-p4' in state['firewalls'] and not state.get('epoch_changed'):
                runtime.witnesses._before = {}
                state['epoch_changed'] = True
            if fail == 'epoch-state2search' and port == 43200 and path == '/v4/search':
                native._stop.set()
            if port == 43200:
                if change == 'documents' and path == '/v3/documents/doc1':
                    reply['json']['createdAt'] = '2026-10-03T12:00:00.123456788Z'
                if change == 'memories' and path == '/v4/memories/list':
                    reply['json']['memoryEntries'].append({'id': 'm2', 'memory': 'Unrelated addition.', 'documentIds': []})
                    reply['json']['pagination']['totalItems'] = 2
                if change == 'search' and path == '/v4/search':
                    reply['json']['results'][0]['similarity'] = 0.8
            return reply
        runtime.request, runtime.p3_enabled, runtime.p4_enabled, runtime.rest = request, True, True, REST_SM
        runtime.model = 'deepseek-flash'
        runtime.prepare_routes = lambda **options: True
        runtime.observe_routes = lambda **options: diagnostic.witness
        original_admit = runtime.gate.admits_new_unit
        def admits():
            if fail == 'gate1' and owned.child.process.poll() is not None:
                return False
            return original_admit()
        runtime.gate.admits_new_unit = admits
        def firewall(unit, binary, parent_check=None):
            state['firewalls'].append(unit)
            if unit != 'fw-p0':
                if parent_check is None or parent_check != runtime.witnesses._check:
                    raise AssertionError('phase firewall changed continuous parent')
                if unit == 'fw-p5' and (owned.job.live[81] or owned.job.live.get(82)):
                    raise AssertionError('P5 read precedes both drained servers')
            if fail == 'epoch-fw5' and unit == 'fw-p5':
                runtime.witnesses._tags = {}
            return {'complete': not (fail == 'fw4' and unit == 'fw-p4' or fail == 'fw5' and unit == 'fw-p5'),
                    'problems': [], 'facts': fw_fixture(binary)}
        runtime.read_firewall = firewall
        def prepare():
            if owned.child.process.poll() is None or owned.job.live[81]:
                raise AssertionError('new declaration precedes first stop')
            owned.guard.expected['u1-boot2'] = {'SUPERMEMORY_PORT': '43200', 'SUPERMEMORY_DATA_DIR': str(owned.data)}
            if fail == 'epoch-prepare2':
                runtime.witnesses._before_rows = {}
            return 43200
        runtime.prepare_boot2 = prepare
        def refused_port(port, **options):
            state['port_checks'].append((port, options))
            return fail != 'port1' and port == owned.port and owned.child.process.poll() is not None and not owned.job.live[81]
        runtime.port_refused = refused_port
        runtime.tree = lambda pid: [{'pid': pid, 'create_time': birth[pid] + (1 if fail == 'tree2' and pid == 702 else 0),
                                    'sha256': P.BINARY_SHA256, 'exe': str(f.binary)}]
        original_listeners = runtime.listeners
        def listeners(boot):
            rows = original_listeners(boot)
            if fail == 'bind2' and boot.pid == 702:
                rows.append({'pid': 999, 'host': '0.0.0.0', 'port': boot.port, 'complete': True})
            return rows
        runtime.listeners = listeners
        return runtime
    f.deps.setup = setup
    return SimpleNamespace(**locals())


def p4p5_connected_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-p4p5-connected-') as directory:
        f = p4p5_protocol_fixture(Path(directory).resolve())
        result = f.f.run_probe()
        fields = result['fields']
        stops = fields['sm_stop']['value']
        if (result['phase'] != 'P0-P5-preparation' or result['restart']['complete'] is not True
                or f.state['spawned'] != ['u1', 'u1-boot2'] or f.state['firewalls'] != ['fw-p0', 'fw-p4', 'fw-p5']
                or len(stops) != 2 or any(row['how'] != 'ctrl_break' or row['complete'] is not True for row in stops)
                or fields['sm_stop']['ok'] is not True or fields['sm_key_search']['ok'] is not True):
            return False
        if (fields['sm_durability']['ok'] is not True or fields['sm_key_reprint']['ok'] is not True
                or any(result['restart']['comparison']['parts'][part] is not True for part in ('documents', 'memories', 'search'))
                or result['restart']['atomic_snapshot_proven'] is not False
                or result['restart']['writers_quiescent_proven'] is not False):
            return False
        homes = f.state['owned'].home, f.state['dirs2'].home
        return (homes[0] != homes[1] and f.state['owned'].cwd != f.state['dirs2'].cwd
                and f.state['owned'].job.killed == [81, 82] and len(f.state['port_checks']) == 1
                and result['outcome'] == 'fail' and fields['sm_schema']['ok'] is False
                and not any('already-closed' in issue or 'server-finalization' in issue for issue in result['problems']))


def p4p5_independent_state_controls():
    for changed in ('documents', 'memories', 'search'):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-p4p5-state-') as directory:
            f = p4p5_protocol_fixture(Path(directory).resolve(), change=changed)
            result = f.f.run_probe()
            comparison = result['restart'].get('comparison')
            if (result['outcome'] != 'fail' or not comparison or comparison['equal'] is not False
                    or comparison['parts'] != {part: part != changed for part in ('documents', 'memories', 'search')}
                    or result['fields']['sm_durability']['ok'] is not False
                    or result['restart']['complete'] is not False or 'fw-p5' in f.state['firewalls']
                    or result['fields']['sm_stop']['ok'] is not True
                    or len(result['fields']['sm_stop']['value']) != 2):
                return False
    return True


def p4p5_first_stop_refusal_controls():
    for failure in ('fw4', 'gate1', 'port1', 'drain1', 'killed1'):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-p4p5-first-stop-') as directory:
            f = p4p5_protocol_fixture(Path(directory).resolve(), fail=failure)
            result = f.f.run_probe()
            stops = result['fields']['sm_stop']['value']
            if (result['outcome'] != 'fail' or f.state['spawned'] != ['u1']
                    or 'fw-p5' in f.state['firewalls'] or not stops
                    or not any(reason.startswith('live-refusal:') for reason in result['reasons'])):
                return False
            if failure == 'drain1' and (stops[0]['complete'] is not False or result['fields']['sm_stop']['ok'] is not False
                    or 'server-pump-not-complete' not in stops[0]['problems'] or stops[0]['pump_drained'] is not False
                    or len(stops) < 2):
                return False
            if failure == 'killed1' and stops[0]['how'] != 'killed':
                return False
    return True


def p4p5_second_boundary_controls():
    for failure in ('key2', 'bind2', 'tree2', 'fw5'):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-p4p5-second-boundary-') as directory:
            f = p4p5_protocol_fixture(Path(directory).resolve(), fail=failure)
            result = f.f.run_probe()
            if (result['outcome'] != 'fail' or f.state['spawned'] != ['u1', 'u1-boot2']
                    or len(result['fields']['sm_stop']['value']) != 2
                    or result['restart']['complete'] is not False
                    or result['fields']['sm_stop']['ok'] is not True):
                return False
            field = {'key2': 'sm_key_reprint', 'bind2': 'sm_bind', 'tree2': 'sm_tree', 'fw5': 'sm_firewall'}[failure]
            if result['fields'][field]['ok'] is not False:
                return False
    return True


def p4p5_port_refusal_controls():
    import errno
    for error, expected in ((None, False), (ConnectionRefusedError(errno.ECONNREFUSED, 'owned refusal'), True),
                            (TimeoutError('owned timeout'), False), (OSError('owned unknown error'), False)):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.connect.side_effect = error
        with mock.patch.object(P.socket, 'socket', return_value=connection):
            if P.sm_port_refused(43199, ceiling_s=1.0, clock=lambda: 0.0) is not expected:
                return False
        if connection.connect.call_args.args != (('127.0.0.1', 43199),) or connection.settimeout.call_args.args != (1.0,):
            return False
    now = [0.0]
    connection = mock.MagicMock()
    connection.__enter__.return_value = connection
    def late(*args):
        now[0] = 1.0
        raise ConnectionRefusedError(errno.ECONNREFUSED, 'owned late refusal')
    connection.connect.side_effect = late
    with mock.patch.object(P.socket, 'socket', return_value=connection):
        if P.sm_port_refused(43199, ceiling_s=1.0, clock=lambda: now[0]) is not False:
            return False
        return refused(lambda: P.sm_port_refused(43199, ceiling_s=6), P.PreflightRefused)


def p4p5_parent_epoch_controls():
    for failure in ('epoch-state1', 'epoch-stop1', 'epoch-prepare2', 'epoch-state2search', 'epoch-fw5'):
        with tempfile.TemporaryDirectory(prefix='nvt-sm-p4p5-parent-epoch-') as directory:
            f = p4p5_protocol_fixture(Path(directory).resolve(), fail=failure)
            result = f.f.run_probe()
            if (result['outcome'] != 'fail' or result['restart']['complete'] is not False
                    or 'witness-parent-epoch-changed' not in result['problems']
                    or result['fields']['sm_egress_boot']['ok'] is not False):
                return False
            if failure in {'epoch-state1', 'epoch-stop1', 'epoch-prepare2'} and f.state['spawned'] != ['u1']:
                return False
            if failure == 'epoch-state1' and any(path == '/v4/memories/list' for _, _, path, _ in f.diagnostic.requests[9:]):
                return False  # The next keyed send refuses after the first changed-epoch response.
    return True


check('A8S-P4P5-CONTROLLER-1: two retained boots reuse data key and seals with continuous witnesses and preserved separate stops', p4p5_connected_controls)
check('A8S-P4P5-STATE-1: changed document memory or search separately fails durability and both owned boots still drain', p4p5_independent_state_controls)
check('A8S-P4P5-FIRST-STOP-1: firewall gate port refusal incomplete or killed stop forbids a second boot and retains failure', p4p5_first_stop_refusal_controls)
check('A8S-P4P5-SECOND-BOUNDARY-1: second key listener tree or P5 firewall refusal retains both stop records', p4p5_second_boundary_controls)
check('A8S-P4P5-PORT-REFUSAL-1: only timely explicit TCP refusal proves observed absence; live timeout or unknown error cannot', p4p5_port_refusal_controls)
check('A8S-P4P5-PARENT-EPOCH-1: same-label FS baseline ticker and stop changes refuse across both boots and each state send', p4p5_parent_epoch_controls)


def p4p5_windows_refusal_deadline_controls():
    # The real owned socket126 returned WSAECONNREFUSED after2.034s, beyond the old1s limit.
    now = [0.0]
    connection = mock.MagicMock()
    connection.__enter__.return_value = connection
    def refusal(*args):
        now[0] = 2.1
        raise ConnectionRefusedError(10061, 'owned Windows refusal')
    connection.connect.side_effect = refusal
    with mock.patch.object(P.socket, 'socket', return_value=connection):
        if P.sm_port_refused(43199, clock=lambda: now[0]) is not True:
            return False
        if connection.settimeout.call_args.args != (5.0,):
            return False
        now[0] = 0.0
        if P.sm_port_refused(43199, ceiling_s=1.0, clock=lambda: now[0]) is not False:
            return False
        def late(*args):
            now[0] = 5.0
            raise ConnectionRefusedError(10061, 'owned late Windows refusal')
        connection.connect.side_effect = late
        now[0] = 0.0
        if P.sm_port_refused(43199, clock=lambda: now[0]) is not False:
            return False
        return refused(lambda: P.sm_port_refused(43199, ceiling_s=6), P.PreflightRefused)

check('A8S-P4P5-PORT-WINDOWS-1: explicit Windows refusal fits a bounded five-second deadline; timeout and late refusal never prove absence', p4p5_windows_refusal_deadline_controls)


def p4p5_windows_refusal_caller_controls():
    with tempfile.TemporaryDirectory(prefix='nvt-sm-p4p5-refusal-caller-') as directory:
        f = p4p5_protocol_fixture(Path(directory).resolve())
        result = f.f.run_probe()
        return (result['restart']['complete'] is True and len(f.state['port_checks']) == 1
                and f.state['port_checks'][0][1] == {'ceiling_s': 5.0})

check('A8S-P4P5-PORT-CALLER-1: connected restart gives the actual refusal helper the bounded Windows allowance', p4p5_windows_refusal_caller_controls)

def sm_exact_json_precision_controls():
    from decimal import Decimal
    raw = b'{"whole":1,"almost":1.0000000000000001,"tiny":1e-324,"flag":true,"text":"\\u00e9","array":[null,0.1]}'
    row = P.sm_parse_api_json(raw)
    value = row['value']
    return (type(value['whole']) is int and value['almost'] == Decimal('1.0000000000000001')
        and value['almost'] != value['whole'] and value['tiny'] == Decimal('1e-324') and value['tiny'] != 0
        and type(value['flag']) is bool and value['text'] == '\u00e9'
        and value['array'] == [None, Decimal('0.1')] and row['bytes'] == len(raw)
        and row['sha256'] == hashlib.sha256(raw).hexdigest() and row['exact_numbers'] is True
        and row['schema_values_verified'] is False and row['runtime_admission'] is False)


def sm_exact_json_ambiguity_controls():
    for raw, reason in ((b'{"id":"a","id":"b"}', 'api-json-duplicate-member'),
        (b'{"outer":{"name":1,"\\u006eame":2}}', 'api-json-duplicate-member'),
        (b'NaN', 'api-json-nonfinite'), (b'Infinity', 'api-json-nonfinite'), (b'-Infinity', 'api-json-nonfinite'),
        (b'{"x":01}', 'api-json-syntax'), (b'"\\ud800"', 'api-json-unicode'), (b'"\xff"', 'api-json-syntax')):
        if not named_api_refusal(lambda raw=raw: P.sm_parse_api_json(raw), reason):
            return False
    return P.sm_parse_api_json(b'{"a":{"id":"x"},"b":{"id":"y"}}')['unique_object_keys'] is True


def sm_exact_json_bounds_controls():
    for raw, reason in (('', 'api-json-size'), (b'', 'api-json-size'),
        (b' ' * (2 * 1024 * 1024 + 1), 'api-json-size'), (b'1' * 1001, 'api-json-number-ceiling'),
        (b'1e10001', 'api-json-number-ceiling'), (b'1e-10001', 'api-json-number-ceiling'),
        (b'[' * 34 + b'0' + b']' * 34, 'api-json-structure-ceiling'),
        (b'[' + b','.join([b'0'] * 20000) + b']', 'api-json-structure-ceiling')):
        if not named_api_refusal(lambda raw=raw: P.sm_parse_api_json(raw), reason):
            return False
    return (P.sm_parse_api_json(b'[' * 32 + b'0' + b']' * 32)['value'] is not None
        and P.sm_parse_api_json(b'1e10000')['exact_numbers'] is True)


def sm_exact_json_deadline_controls():
    original = P.json.loads
    now = [0.0]
    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] = 1.0
        return result
    with mock.patch.object(P.json, 'loads', side_effect=delayed):
        if not named_api_refusal(lambda: P.sm_parse_api_json(b'{}', clock=lambda: now[0]), 'state-read-deadline'):
            return False
    for ceiling in (True, 0, 6, float('nan'), 10 ** 1000):
        if not named_api_refusal(lambda ceiling=ceiling: P.sm_parse_api_json(b'{}', ceiling_s=ceiling), 'api-json-ceiling'):
            return False
    return P.sm_parse_api_json(b'{}', ceiling_s=5)['schema_values_verified'] is False


check('A8S-JSON-PRECISION-1: actual UTF8 bytes retain exact decimals and cannot upgrade schema admission', sm_exact_json_precision_controls)
check('A8S-JSON-AMBIGUITY-1: duplicate decoded members nonfinite values malformed JSON and invalid Unicode refuse', sm_exact_json_ambiguity_controls)
check('A8S-JSON-BOUNDS-1: input bytes and decoded depth node digit exponent ceilings bound accepted values', sm_exact_json_bounds_controls)
check('A8S-JSON-DEADLINE-1: parsing and validation complete within one deadline and reject invalid ceilings', sm_exact_json_deadline_controls)


def sm_exact_json_cli_entry_controls():
    import ast
    tree = ast.parse(Path(P.__file__).read_text(encoding='utf-8'))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    main.body = [ast.Return(value=ast.Call(func=ast.Name(id='sm_parse_api_json', ctx=ast.Load()),
        args=[ast.Constant(value=b'{}')], keywords=[]))]
    tree = ast.fix_missing_locations(tree)
    namespace = {'__name__': '__main__', '__file__': P.__file__}
    try:
        exec(compile(tree, P.__file__, 'exec'), namespace)
    except SystemExit as exc:
        return (isinstance(exc.code, dict) and exc.code['value'] == {}
                and exc.code['schema_values_verified'] is False and exc.code['runtime_admission'] is False)
    return False

check('A8S-JSON-CLI-1: the actual module entry can call exact parsing before SystemExit without product actions', sm_exact_json_cli_entry_controls)

def api_value_case(schema, raw, *, valid=True, reason='api-schema-value', pointer=''):
    schema_raw = json.dumps(schema, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if not valid:
        return named_api_refusal(lambda: P.sm_validate_api_value(schema_raw, raw, schema_pointer=pointer), reason)
    row = P.sm_validate_api_value(schema_raw, raw, schema_pointer=pointer)
    return (row['validated'] is True and row['runtime_admission'] is False and row['format_assertions'] is False
            and row['schema_raw_sha256'] == hashlib.sha256(schema_raw).hexdigest()
            and row['value_raw_sha256'] == hashlib.sha256(raw).hexdigest() and row['value_bytes'] == len(raw))


def sm_schema_numeric_controls():
    cases = [({'type': 'integer'}, b'1.0', True), ({'type': 'integer'}, b'1.0000000000000001', False),
        ({'type': 'number'}, b'true', False), ({'type': ['integer', 'null']}, b'null', True),
        ({'minimum': 0, 'maximum': 1}, b'0', True), ({'minimum': 0, 'maximum': 1}, b'1', True), ({'minimum': 0, 'maximum': 1}, b'1.0000000000000001', False),
        ({'minimum': 0}, b'-1e-324', False), ({'exclusiveMinimum': 0}, b'1e-324', True),
        ({'exclusiveMinimum': 0}, b'0', False), ({'exclusiveMaximum': 1}, b'1', False),
        ({'exclusiveMaximum': 1}, b'0.999999999999999999999999999999999999', True),
        ({'multipleOf': 0.1}, b'0.3', True), ({'multipleOf': 0.1}, b'0.30000000000000000000000000001', False),
        ({'minimum': 1}, b'"annotation only on numbers"', True)]
    return all(api_value_case(schema, raw, valid=valid) for schema, raw, valid in cases)


def sm_schema_equality_controls():
    cases = [({'enum': [True]}, b'1', False), ({'const': 1}, b'true', False),
        ({'const': 1}, b'1.0', True), ({'const': 1}, b'1.0000000000000001', False),
        ({'const': {'a': [1, False], 'b': None}}, b'{"b":null,"a":[1.0,false]}', True),
        ({'enum': [[1, True]]}, b'[1.0,true]', True), ({'enum': [[1, True]]}, b'[true,1]', False),
        ({'uniqueItems': True}, b'[1,1.0]', False), ({'uniqueItems': True}, b'[1,true]', True),
        ({'uniqueItems': True}, b'[{"a":1,"b":2},{"b":2,"a":1.0}]', False)]
    return all(api_value_case(schema, raw, valid=valid) for schema, raw, valid in cases)


def sm_schema_object_array_controls():
    schema = {'type': 'object', 'required': ['id', 'rows'], 'additionalProperties': False,
        'minProperties': 2, 'maxProperties': 2,
        'properties': {'id': {'type': 'string', 'minLength': 1},
            'rows': {'type': 'array', 'minItems': 1, 'maxItems': 2, 'items': {'type': 'integer', 'minimum': 0}}}}
    for raw, valid in [(b'{"id":"a","rows":[0]}', True), (b'{"id":"a","rows":[0,1]}', True),
        (b'{"id":"","rows":[0]}', False), (b'{"rows":[0]}', False), (b'{"id":"a","rows":[]}', False),
        (b'{"id":"a","rows":[0,1,2]}', False), (b'{"id":"a","rows":[true]}', False),
        (b'{"id":"a","rows":[0],"extra":1}', False)]:
        if not api_value_case(schema, raw, valid=valid):
            return False
    cases = [({'prefixItems': [{'type': 'string'}, {'type': 'integer'}], 'items': False}, b'["a",1]', True),
        ({'prefixItems': [{'type': 'string'}, {'type': 'integer'}], 'items': False}, b'["a",1,2]', False),
        ({'prefixItems': [{'type': 'string'}, {'type': 'integer'}], 'items': False}, b'[1,"a"]', False),
        ({'additionalProperties': {'type': 'integer'}}, b'{"a":1,"b":true}', False),
        ({'required': ['a']}, b'1', True), ({'required': ['a']}, b'{}', False),
        ({'minProperties': 2}, b'{"a":1}', False), ({'minProperties': 2}, b'{"a":1,"b":2}', True),
        ({'maxProperties': 2}, b'{"a":1,"b":2,"c":3}', False), ({'type': 'object'}, b'[]', False),
        ({'type': 'array'}, b'{}', False), ({'type': 'boolean'}, b'1', False),
        ({'type': 'null'}, b'false', False), ({'type': 'boolean'}, b'true', True)]
    return all(api_value_case(schema, raw, valid=valid) for schema, raw, valid in cases)


def sm_schema_string_controls():
    cases = [({'minLength': 1, 'maxLength': 1}, '"\U0001f600"'.encode('utf-8'), True),
        ({'maxLength': 1}, '"e\u0301"'.encode('utf-8'), False),
        ({'pattern': '^[a-zA-Z0-9_:-]+$'}, b'"run_u1:A-2"', True),
        ({'pattern': '^[a-zA-Z0-9_:-]+$'}, b'"run.u1"', False),
        ({'pattern': '^[a-zA-Z0-9_:-]+$'}, b'"run_u1\\n"', False),
        ({'pattern': '^[a-zA-Z0-9_-]+$'}, b'"run:u1"', False),
        ({'type': 'string', 'format': 'date-time'}, b'"format remains annotation"', True)]
    return all(api_value_case(schema, raw, valid=valid) for schema, raw, valid in cases)


def sm_schema_composition_controls():
    cases = [({'allOf': [{'minimum': 1}, {'maximum': 2}]}, b'1', True),
        ({'allOf': [{'minimum': 1}, {'maximum': 2}]}, b'0', False),
        ({'anyOf': [{'type': 'string'}, {'type': 'integer'}]}, b'null', False),
        ({'anyOf': [{'type': 'string'}, {'type': 'integer'}]}, b'1', True),
        ({'oneOf': [{'type': 'number'}, {'type': 'integer'}]}, b'1', False),
        ({'oneOf': [{'type': 'number'}, {'type': 'integer'}]}, b'1.1', True),
        ({'not': {'type': 'integer'}}, b'1', False), ({'not': {'type': 'integer'}}, b'"a"', True),
        (False, b'0', False), (True, b'0', True),
        ({'$defs': {'x': {'minimum': 1}}, '$ref': '#/$defs/x', 'maximum': 2}, b'0', False),
        ({'$defs': {'x': {'minimum': 1}}, '$ref': '#/$defs/x', 'maximum': 2}, b'3', False),
        ({'$defs': {'x': {'minimum': 1}}, '$ref': '#/$defs/x', 'maximum': 2}, b'2', True)]
    return all(api_value_case(schema, raw, valid=valid) for schema, raw, valid in cases)


def sm_schema_unsupported_controls():
    cases = [({'anyOf': [True, {'contains': {'type': 'integer'}}]}, 'api-schema-unsupported-keyword'),
        ({'oneOf': [True, {'unknownAssertion': False}]}, 'api-schema-unsupported-keyword'),
        ({'$defs': {'unused': {'pattern': '(a+)+$'}}, 'type': 'string'}, 'api-schema-pattern-unsupported'),
        ({'$id': 'https://example.invalid/schema', 'type': 'integer'}, 'api-schema-unsupported-keyword'),
        ({'$vocabulary': {'https://json-schema.org/draft/2020-12/vocab/format-assertion': True}}, 'api-schema-unsupported-keyword'),
        ({'pattern': '(a+)+$'}, 'api-schema-pattern-unsupported'),
        ({'readOnly': True}, 'api-schema-direction-unsupported'),
        ({'$schema': 'https://json-schema.org/draft/2019-09/schema'}, 'api-schema-dialect'),
        ({'$ref': 'https://example.invalid/schema'}, 'api-schema-reference'),
        ({'$defs': {'x': {'$ref': '#/$defs/x'}}, '$ref': '#/$defs/x'}, 'api-schema-reference')]
    return all(api_value_case(schema, b'1', valid=False, reason=reason) for schema, reason in cases)


def sm_schema_keyword_controls():
    cases = [{'type': []}, {'type': ['integer', 'integer']}, {'type': 'unknown'}, {'type': True},
        {'enum': []}, {'multipleOf': 0}, {'minimum': True}, {'exclusiveMinimum': True},
        {'minLength': -1}, {'maxItems': 1.1}, {'uniqueItems': 1}, {'items': []},
        {'required': ['a', 'a']}, {'properties': []}, {'prefixItems': []}, {'allOf': []},
        {'deprecated': 1}, {'format': False}, {'examples': {}}, {'$defs': []}]
    return all(api_value_case(schema, b'1', valid=False, reason='api-schema-keyword') for schema in cases)


def sm_schema_pointer_controls():
    doc = {'openapi': '3.1.0', 'components': {'schemas': {'a/b~c': {'type': 'integer'}}}}
    if not api_value_case(doc, b'1', pointer='/components/schemas/a~1b~0c'):
        return False
    for path in ('/components/schemas/a~2b', '/components/schemas/a%2Fb', '/missing', 'relative'):
        if not api_value_case(doc, b'1', valid=False, reason='api-schema-pointer', pointer=path):
            return False
    for doc in ({'openapi': '3.0.3', 's': True}, {'openapi': '3.1.0', 'jsonSchemaDialect': [], 's': True},
        {'$schema': 'https://json-schema.org/draft/2019-09/schema', 's': True}):
        if not api_value_case(doc, b'1', valid=False, reason='api-schema-dialect', pointer='/s'):
            return False
    for path in ('/' * 4097, '/' * 65, '/' + '%' * 4096):
        if not api_value_case(True, b'1', valid=False, reason='api-schema-pointer-ceiling', pointer=path):
            return False
    if not api_value_case({'a' * 4096: True}, b'1', pointer='/' + 'a' * 4096,
            valid=False, reason='api-schema-pointer-ceiling'):
        return False
    if not api_value_case({'a' * 4095: True}, b'1', pointer='/' + 'a' * 4095):
        return False
    if not api_value_case({'$ref': '#/' + 'a' * 4096}, b'1', valid=False, reason='api-schema-pointer-ceiling'):
        return False
    return api_value_case({'$id': 'https://example.invalid/a', 's': True}, b'1', valid=False,
        reason='api-schema-unsupported-keyword', pointer='/s')


def sm_schema_bounds_controls():
    # Accepted bounds and the first rejected depth are independent of implementation.
    for raw in (b'"' + b'a' * (2 * 1024 * 1024 - 2) + b'"',
        b'[' + b','.join([b'0'] * 19999) + b']', b'1' * 1000,
        b'[' * 32 + b'0' + b']' * 32):
        if P.sm_parse_api_json(raw)['exact_numbers'] is not True:
            return False
    if not named_api_refusal(lambda: P.sm_parse_api_json(b'[' * 33 + b'0' + b']' * 33), 'api-json-structure-ceiling'):
        return False
    if not named_api_refusal(lambda: P.sm_parse_api_json(b'"' + b'a' * (2 * 1024 * 1024 - 1) + b'"'), 'api-json-size'):
        return False
    if not api_value_case({'allOf': [True] * 4093}, b'1'):
        return False
    if not api_value_case({'allOf': [True] * 4094}, b'1', valid=False, reason='api-schema-compilation-ceiling'):
        return False
    schema_raw = json.dumps({'allOf': [{'items': True}] * 6}).encode('utf-8')
    value_raw = b'[' + b','.join([b'0'] * 19999) + b']'
    return named_api_refusal(lambda: P.sm_validate_api_value(schema_raw, value_raw, clock=lambda: 0.0),
        'api-schema-evaluation-ceiling')


def sm_schema_deadline_controls():
    now = [0.0]
    original = P.sm_parse_api_json
    def delayed(*args, **options):
        result = original(*args, **options)
        now[0] = 2.0
        return result
    with mock.patch.object(P, 'sm_parse_api_json', side_effect=delayed):
        if not named_api_refusal(lambda: P.sm_validate_api_value(b'true', b'1', clock=lambda: now[0]), 'state-read-deadline'):
            return False
    for ceiling in (True, 0, 6, float('nan'), 10 ** 1000):
        if not named_api_refusal(lambda ceiling=ceiling: P.sm_validate_api_value(b'true', b'1', ceiling_s=ceiling), 'api-schema-ceiling'):
            return False
    return api_value_case(True, b'1')


check('A8S-VALUE-NUMBER-1: exact integer inclusive exclusive and multiple constraints cannot round across boundaries', sm_schema_numeric_controls)
check('A8S-VALUE-EQUALITY-1: numeric equality is exact while booleans arrays and unordered objects remain distinct', sm_schema_equality_controls)
check('A8S-VALUE-STRUCTURE-1: required extra properties nested items tuple prefixes and size constraints apply correctly', sm_schema_object_array_controls)
check('A8S-VALUE-STRING-1: Unicode code points and only reviewed anchored ASCII patterns are evaluated; format is annotation', sm_schema_string_controls)
check('A8S-VALUE-COMPOSITION-1: every supported composition boolean schema and local reference sibling applies', sm_schema_composition_controls)
check('A8S-VALUE-UNSUPPORTED-1: unknown assertion dialect direction reference scope pattern or unused branch refuses before use', sm_schema_unsupported_controls)
check('A8S-VALUE-KEYWORD-1: malformed assertion and annotation shapes never produce a partial pass', sm_schema_keyword_controls)
check('A8S-VALUE-POINTER-1: exact observed document and escaped local pointer retain dialect and scope semantics', sm_schema_pointer_controls)
check('A8S-VALUE-BOUNDS-1: exact parser acceptance limits first rejected depth and compiled schema ceiling are controlled', sm_schema_bounds_controls)
check('A8S-VALUE-DEADLINE-1: schema and instance parsing compilation evaluation share one finite deadline', sm_schema_deadline_controls)


def sm_http_observation_fixture(response_raw=b'{"items":[0.1,1.0],"text":"ok"}'):
    events = []
    class Connection:
        def __init__(self, host, port, timeout):
            events.append(('constructed', host, port))
            self.sock = None
        def connect(self):
            events.append(('connected',))
        def request(self, method, path, **options):
            events.append(('sent', method, path, options['body']))
        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda bound: response_raw)
        def close(self):
            events.append(('closed',))
    return Connection, events


def sm_http_actual_byte_controls():
    Connection, events = sm_http_observation_fixture()
    observations = []
    raw_schema = b'{"type":"object","properties":{"items":{"type":"array","items":{"type":"number"}}}}'
    def observer(direction, **options):
        observations.append((direction, dict(options)))
        if direction == 'request':
            return options['raw'] == '{"text":"\u041f\u0440\u0438\u0432\u0435\u0442","limit":1}'.encode('utf-8')
        return P.sm_validate_api_value(raw_schema, options['raw'], ceiling_s=options['ceiling_s'])['validated'] is True
    result = P.sm_request(43199, 'POST', '/v4/search', keyed=False, body={'text': '\u041f\u0440\u0438\u0432\u0435\u0442', 'limit': 1},
        connection_factory=Connection, json_observer=observer)
    if [kind for kind, _ in observations] != ['request', 'response']:
        return False
    request_row, response_row = observations[0][1], observations[1][1]
    sent = next(event for event in events if event[0] == 'sent')
    return (request_row['raw'] is sent[3] and request_row['status'] is None
        and response_row['status'] == 200 and response_row['raw'] == b'{"items":[0.1,1.0],"text":"ok"}'
        and all(row['method'] == 'POST' and row['path'] == '/v4/search' and 0 < row['ceiling_s'] <= 5
            and set(row) == {'method', 'path', 'raw', 'status', 'ceiling_s'} for _, row in observations)
        and result['json'] == {'items': [0.1, 1], 'text': 'ok'}
        and type(result['json']['items'][1]) is int and result['sha256'] == hashlib.sha256(response_row['raw']).hexdigest())


def sm_http_pre_send_refusal_controls():
    for outcome in (False, None, 1):
        Connection, events = sm_http_observation_fixture()
        if not refused(lambda: P.sm_request(43199, 'POST', '/v4/search', keyed=False, body={},
            connection_factory=Connection, json_observer=lambda *args, **options: outcome), P.SmRequestRefused):
            return False
        if any(event[0] in {'connected', 'sent'} for event in events) or events[-1] != ('closed',):
            return False
    Connection, events = sm_http_observation_fixture()
    def schema_refusal(*args, **options):
        raise P.SmApiRefused('api-schema-value')
    if not named_api_refusal(lambda: P.sm_request(43199, 'POST', '/v4/search', keyed=False, body={},
            connection_factory=Connection, json_observer=schema_refusal), 'api-schema-value'):
        return False
    if any(event[0] in {'connected', 'sent'} for event in events):
        return False
    Connection, events = sm_http_observation_fixture()
    observer = mock.Mock(return_value=True)
    if not named_api_refusal(lambda: P.sm_request(43199, 'POST', '/', keyed=False,
            body={'x': float('nan')}, connection_factory=Connection, json_observer=observer), 'api-json-nonfinite'):
        return False
    if observer.called or any(event[0] in {'connected', 'sent'} for event in events):
        return False
    Connection, events = sm_http_observation_fixture()
    return (refused(lambda: P.sm_request(43199, 'GET', '/', keyed=False, connection_factory=Connection,
        json_observer=True), P.SmRequestRefused) and events == [])


def sm_http_precision_projection_controls():
    cases = [(b'{"x":1.0000000000000001}', 'api-json-projection-loss'),
        (b'{"x":1e-324}', 'api-json-projection-loss'), (b'{"x":1,"x":2}', 'api-json-duplicate-member'),
        (b'NaN', 'api-json-nonfinite'), (b'"\\ud800"', 'api-json-unicode')]
    for raw, reason in cases:
        Connection, events = sm_http_observation_fixture(raw)
        if not named_api_refusal(lambda: P.sm_request(43199, 'GET', '/', keyed=False,
                connection_factory=Connection, json_observer=lambda *args, **options: True), reason):
            return False
        if events[-1] != ('closed',):
            return False
    value = P.sm_api_json_projection(b'{"x":[0.10,1e2,0.0001,-0.0],"b":true}')
    return (value == {'x': [0.1, 100, 0.0001, 0], 'b': True} and type(value['x'][1]) is int
        and type(value['b']) is bool)


def sm_http_observer_deadline_controls():
    Connection, events = sm_http_observation_fixture()
    def late(*args, **options):
        time.sleep(0.08)
        return True
    if not refused(lambda: P.sm_request(43199, 'GET', '/', keyed=False, timeout_s=0.03,
            connection_factory=Connection, json_observer=late), P.SmRequestRefused):
        return False
    return (not any(event[0] in {'connected', 'sent'} for event in events)
            and not any(thread.name == 'sm-request-deadline' and thread.is_alive() for thread in threading.enumerate()))


check('A8S-HTTP-BYTES-1: observer sees exactly transmitted UTF8 bytes received bytes method path and status with no credentials', sm_http_actual_byte_controls)
check('A8S-HTTP-BEFORE-1: refused incomplete or malformed observers cannot connect or send and owned connection closes', sm_http_pre_send_refusal_controls)
check('A8S-HTTP-PROJECTION-1: exact decoded values reach existing consumers only without lost decimal digits or JSON ambiguity', sm_http_precision_projection_controls)
check('A8S-HTTP-DEADLINE-1: an observer returning after the request allowance cannot send and watchdog drains', sm_http_observer_deadline_controls)

def sm_http_projection_expansion_controls():
    for raw, reason in ((b'1e10000', 'api-json-projection-integer-ceiling'),
        (b'1e1000', 'api-json-projection-integer-ceiling'), (b'-1e999', 'api-json-projection-integer-ceiling'),
        (b'[' + b','.join([b'1e999'] * 2096) + b']', 'api-json-projection-size')):
        if not named_api_refusal(lambda raw=raw: P.sm_api_json_projection(raw), reason):
            return False
    positive = P.sm_api_json_projection(b'1e999')
    negative = P.sm_api_json_projection(b'-1e998')
    if positive != 10 ** 999 or negative != -(10 ** 998):
        return False
    if len(json.dumps(positive).encode('utf-8')) != 1000 or len(json.dumps(negative).encode('utf-8')) != 1000:
        return False
    near_bound = P.sm_api_json_projection(b'[' + b','.join([b'1e999'] * 2095) + b']')
    encoded = json.dumps(near_bound, separators=(',', ':')).encode('utf-8')
    if len(encoded) != 2097096:
        return False
    exact_bound = P.sm_api_json_projection(b'"' + b'a' * (2 * 1024 * 1024 - 2) + b'"')
    if len(json.dumps(exact_bound, ensure_ascii=False).encode('utf-8')) != 2 * 1024 * 1024:
        return False
    Connection, events = sm_http_observation_fixture(b'{"x":1e10000}')
    return (P.sm_api_json_projection(b'0e10000') == 0
        and named_api_refusal(lambda: P.sm_request(43199, 'GET', '/', keyed=False,
            connection_factory=Connection, json_observer=lambda *args, **options: True), 'api-json-projection-integer-ceiling')
        and events[-1] == ('closed',))

check('A8S-HTTP-PROJECTION-BOUNDS-1: compact exponents refuse before unsupported integer or total JSON expansion; accepted boundaries serialize', sm_http_projection_expansion_controls)


def sm_http_response_refusal_controls():
    for outcome in (False, None, 1, 'schema-exception'):
        Connection, events = sm_http_observation_fixture()
        def observer(direction, **options):
            if direction == 'request':
                return True
            if outcome == 'schema-exception':
                raise P.SmApiRefused('api-schema-value')
            return outcome
        def operation():
            return P.sm_request(43199, 'GET', '/', keyed=False, connection_factory=Connection,
                json_observer=observer)
        if outcome == 'schema-exception':
            if not named_api_refusal(operation, 'api-schema-value'):
                return False
        else:
            try:
                operation()
            except P.SmRequestRefused as exc:
                if str(exc) != 'json-observation-incomplete':
                    return False
            else:
                return False
        if events[-1] != ('closed',) or sum(event[0] == 'sent' for event in events) != 1:
            return False
    return True


def sm_http_real_socket_controls():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    stop = threading.Event()
    accepted = threading.Event()
    partial = None
    requests, observations, errors = [], [], []
    held = SS.HeldKey(b'sm_' + b'R' * 30)
    replies = [b'{"x":0.1}', b'{"x":1.0000000000000001}']
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(0.25)
            super().setup()
            accepted.set()
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.connection.settimeout(0.25)
            raw = self.rfile.read(int(self.headers['Content-Length']))
            requests.append({'path': self.path, 'raw': raw,
                'own_key': self.headers[SS.KEY_HEADER] == held.header()[SS.KEY_HEADER]})
            body = replies.pop(0)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    # Retain before bind; a single bounded worker owns every accepted handler.
    server = HTTPServer(('127.0.0.1', 0), Handler, bind_and_activate=False)
    worker = None
    good = False
    try:
        server.server_bind()
        server.server_activate()
        server.timeout = 0.05
        def serve():
            try:
                while not stop.is_set():
                    server.handle_request()
            except Exception as exc:
                errors.append(type(exc).__name__)
        worker = threading.Thread(target=serve, name='sm-observer-owned-http', daemon=True)
        worker.start()
        port = server.server_address[1]
        def observer(direction, **options):
            observations.append((direction, options['raw']))
            schema = (b'{"type":"object","required":["q"],"properties":{"q":{"type":"string"}}}'
                if direction == 'request' else b'{"type":"object","properties":{"x":{"type":"number","maximum":1}}}')
            if set(options) != {'method', 'path', 'raw', 'status', 'ceiling_s'}:
                return False
            return P.sm_validate_api_value(schema, options['raw'], ceiling_s=options['ceiling_s'])['validated'] is True
        result = P.sm_request(port, 'POST', '/observed', key=held, body={'q': '\u041f\u0440\u0438\u0432\u0435\u0442'},
            timeout_s=3.0, json_observer=observer)
        if result['json'] != {'x': 0.1} or len(requests) != 1 or not requests[0]['own_key']:
            return False
        first = requests[0]['raw']
        if first != '{"q":"\u041f\u0440\u0438\u0432\u0435\u0442"}'.encode('utf-8') or observations != [('request', first), ('response', b'{"x":0.1}')]:
            return False
        if not named_api_refusal(lambda: P.sm_request(port, 'POST', '/observed', key=held, body={'q': 1},
                timeout_s=3.0, json_observer=observer), 'api-schema-value') or len(requests) != 1:
            return False
        if not named_api_refusal(lambda: P.sm_request(port, 'POST', '/observed', key=held, body={'q': 'second'},
                timeout_s=3.0, json_observer=observer), 'api-schema-value'):
            return False
        accepted.clear()
        partial = P.socket.create_connection(('127.0.0.1', port), timeout=0.5)
        partial.sendall(b'POST /observed HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Partial:')
        if not accepted.wait(0.5):
            return False
        good = (len(requests) == 2 and all(row['path'] == '/observed' and row['own_key'] for row in requests)
            and replies == [] and len(observations) == 5)
    finally:
        stop.set()
        if worker is not None and worker.ident is not None:
            worker.join(1.0)
        if worker is not None and worker.is_alive():
            good = False  # A cleanup retry cannot erase the failed initial drain.
        server.server_close()
        if partial is not None:
            try:
                partial.shutdown(P.socket.SHUT_RDWR)
            except OSError:
                pass
            partial.close()
        if worker is not None and worker.is_alive():
            worker.join(0.5)
    return good and errors == [] and worker is not None and not worker.is_alive()

check('A8S-HTTP-RESPONSE-1: response False None nonboolean or schema refusal cannot become consumed JSON and the connection closes', sm_http_response_refusal_controls)
check('A8S-HTTP-REAL-1: owned literal127 socket binds actual UTF8 bytes and own-key headers; invalid request never arrives and invalid response refuses', sm_http_real_socket_controls)

def api_session_fixture(*, docs=None, clock=time.monotonic):
    # JSON observations have independent leaves; the earlier fixture reuses Python aliases.
    doc = json.loads(json.dumps(sm_operations_schema_fixture()))
    for path, item in doc['paths'].items():
        for verb, op in item.items():
            if verb not in {'get', 'post'}:
                continue
            if 'requestBody' in op:
                schema = op['requestBody']['content']['application/json']['schema']
                schema['additionalProperties'] = False
                for name, leaf in schema['properties'].items():
                    if name in {'page', 'limit'}:
                        leaf.update(type='integer', minimum=1, maximum=1100)
                    if name == 'containerTag':
                        leaf.update(pattern='^[a-zA-Z0-9_:-]+$', minLength=1, maxLength=100)
                    if name == 'containerTags':
                        leaf.update(minItems=1)
                        leaf['items'].update(pattern='^[a-zA-Z0-9_:-]+$', minLength=1, maxLength=100)
            if path == '/v3/documents/{id}':
                op['parameters'][0]['schema'].update(pattern='^[A-Za-z0-9_-]+$', minLength=1, maxLength=10)
            if path == '/v4/search':
                leaf = op['responses']['200']['content']['application/json']['schema']['properties']['results']['items']['properties']['similarity']
                leaf.update(minimum=0, maximum=1)
    docs = [doc, copy.deepcopy(doc)] if docs is None else docs
    session = P.SmApiValueSession('unit', clock=clock)
    raw_docs = [json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8') for value in docs]
    replies = [{'status': 200, 'json': value, 'sha256': hashlib.sha256(raw).hexdigest()} for value, raw in zip(docs, raw_docs)]
    def observe(direction, method, path, raw=None, status=None, **options):
        return session.observer(direction, method=method, path=path, raw=raw, status=status,
            ceiling_s=options.get('ceiling_s', 1.0))
    def pair(method, path, request, response, status=200):
        return observe('request', method, path, request) is True and observe('response', method, path, response, status) is True
    def schemas():
        return all(pair('GET', path, None, raw) for path, raw in zip(P.SM_OPENAPI_PATHS, raw_docs))
    return SimpleNamespace(session=session, docs=docs, raw_docs=raw_docs, replies=replies,
        observe=observe, pair=pair, schemas=schemas)


def api_session_complete(f):
    if not f.schemas() or not f.pair('POST', '/v4/memories/list', b'{"containerTags":["unit.u1"],"limit":1}', b'{"error":"invalid tag"}', 400):
        return False
    for method, path, request, response in (
        ('POST', '/v3/documents', b'{"content":"owned diagnostic","containerTag":"unit_u1"}', b'{"id":"doc1","status":"queued"}'),
        ('GET', '/v3/documents/doc1', None, b'{"id":"doc1","status":"done","createdAt":"2026-10-04T12:00:00Z","memories":[]}'),
        ('POST', '/v4/memories/list', b'{"containerTags":["unit_u1"],"limit":10,"page":1}', b'{"memoryEntries":[],"pagination":{"currentPage":1,"limit":10,"totalItems":0,"totalPages":0}}'),
        ('POST', '/v4/search', b'{"q":"owned diagnostic","containerTags":["unit_u1"],"limit":1}', b'{"results":[{"id":"m1","memory":"owned diagnostic","similarity":0.1}],"total":1,"timing":1}'),
    ):
        if not f.pair(method, path, request, response):
            return False
    return True


def api_session_positive_controls():
    f = api_session_fixture()
    if not api_session_complete(f):
        return False
    contract = P.sm_openapi_contract(f.replies, REST_SM)
    result = f.session.summary(contract)
    if (result['value_constraints_verified'] is not True or result['runtime_admission'] is not False
            or result['operations_completed'] != 4 or len(result['traces']) != 7):
        return False
    negative = result['traces'][2]
    if (negative['intentional_negative_control'] is not True or negative['status'] != 400
            or negative['request_proofs'] != [] or negative['response_proofs'] != []):
        return False
    for trace in result['traces'][3:]:
        if trace['runtime_admission'] is not False or trace['intentional_negative_control'] is not False:
            return False
        for direction in ('request', 'response'):
            proofs = trace[direction + '_proofs']
            if len(proofs) != 2 or {p['origin'] for p in proofs} != {'/v3/openapi', '/v4/openapi'}:
                return False
            for proof, raw_schema in zip(proofs, f.raw_docs):
                if (proof['validated'] is not True or proof['runtime_admission'] is not False
                        or proof['schema_raw_sha256'] != hashlib.sha256(raw_schema).hexdigest()):
                    return False
                if trace['operation'] == 'get_document' and direction == 'request':
                    expected = hashlib.sha256(b'"doc1"').hexdigest()
                    pointer = '/paths/~1v3~1documents~1{id}/get/parameters/0/schema'
                else:
                    expected = trace[direction + '_sha256']
                    declared = {'add_document': '~1v3~1documents', 'get_document': '~1v3~1documents~1{id}',
                        'list_memories': '~1v4~1memories~1list', 'search': '~1v4~1search'}[trace['operation']]
                    suffix = '/requestBody' if direction == 'request' else '/responses/200'
                    pointer = '/paths/' + declared + '/' + trace['method'].lower() + suffix + '/content/application~1json/schema'
                if proof['value_raw_sha256'] != expected or proof['schema_pointer'] != pointer:
                    return False
    result['traces'][3]['request_proofs'].clear()
    contract['observed'][0]['raw_sha256'] = '0' * 64
    fresh = f.session.summary(P.sm_openapi_contract(f.replies, REST_SM))
    return fresh['value_constraints_verified'] is True and len(fresh['traces'][3]['request_proofs']) == 2 and 'owned diagnostic' not in json.dumps(fresh)


def api_session_request_controls():
    cases = (
        ('POST', '/v4/memories/list', b'{"containerTags":["unit_u1"],"limit":0}', 'api-schema-value'),
        ('POST', '/v3/documents', b'{"content":1,"containerTag":"unit_u1"}', 'api-schema-value'),
        ('POST', '/v4/search', b'{"q":"q","containerTags":["unit_u1"],"limit":1,"extra":1}', 'api-schema-value'),
        ('GET', '/v3/documents/doc.1', None, 'api-schema-value'),
        ('GET', '/v3/documents/%64oc1', None, 'api-operation-route'),
        ('GET', '/v3/documents/..', None, 'api-operation-route'),
        ('GET', '/v3/documents/doc1', b'{}', 'api-operation-body'),
        ('POST', '/v3/documents/doc1', b'{}', 'api-operation-route'),
    )
    for method, path, raw, reason in cases:
        f = api_session_fixture()
        if not f.schemas() or not named_api_refusal(lambda: f.observe('request', method, path, raw), reason):
            return False
        if f.session.summary(P.sm_openapi_contract(f.replies, REST_SM))['value_constraints_verified'] is not False:
            return False
    f = api_session_fixture()
    f.docs[1]['paths']['/v3/documents']['post']['requestBody']['content']['application/json']['schema']['properties']['content']['maxLength'] = 2
    f = api_session_fixture(docs=f.docs)
    return f.schemas() and named_api_refusal(lambda: f.observe('request', 'POST', '/v3/documents', b'{"content":"long","containerTag":"unit_u1"}'), 'api-schema-value')


def api_session_response_controls():
    for method, path, status, raw, reason in (
        ('POST', '/v4/search', 200, b'{"results":[{"similarity":1.0000000000000001}],"total":1,"timing":1}', 'api-schema-value'),
        ('POST', '/v4/search', 201, b'{}', 'api-operation-status'),
        ('POST', '/v4/memories/list', 200, b'{}', 'api-operation-order'),
        ('GET', '/v4/search', 200, b'{}', 'api-operation-order'),
        ('POST', '/v4/search', True, b'{}', 'api-operation-order'),
    ):
        f = api_session_fixture()
        if not f.schemas() or f.observe('request', 'POST', '/v4/search', b'{"q":"q","limit":1}') is not True:
            return False
        if not named_api_refusal(lambda: f.observe('response', method, path, raw, status), reason):
            return False
        if not f.session.faulted or len(f.session.traces) != 2:
            return False
    f = api_session_fixture()
    if not f.schemas() or f.observe('request', 'POST', '/v3/documents', b'{"content":"q"}') is not True:
        return False
    replies = []
    worker = threading.Thread(target=lambda: replies.append(named_api_refusal(lambda: f.observe('response', 'POST', '/v3/documents', b'{}', 200), 'api-operation-order')))
    worker.start()
    worker.join(1.0)
    return not worker.is_alive() and replies == [True]


def api_session_reference_drift_controls():
    f = api_session_fixture()
    for doc in f.docs:
        op = doc['paths']['/v3/documents']['post']
        doc['components'] = {'requestBodies': {'Owned/Body': op['requestBody']}, 'responses': {'Owned~Response': op['responses']['200']}}
        op['requestBody'] = {'$ref': '#/components/requestBodies/Owned~1Body'}
        op['responses']['200'] = {'$ref': '#/components/responses/Owned~0Response'}
    f = api_session_fixture(docs=f.docs)
    if not f.schemas() or not f.pair('POST', '/v3/documents', b'{"content":"q"}', b'{"id":"doc1","status":"queued"}'):
        return False
    row = f.session.traces[-1]
    if (row['request_proofs'][0]['schema_pointer'] != '/components/requestBodies/Owned~1Body/content/application~1json/schema'
            or row['response_proofs'][0]['schema_pointer'] != '/components/responses/Owned~0Response/content/application~1json/schema'):
        return False
    f.observe('request', 'GET', '/v3/openapi')
    if not named_api_refusal(lambda: f.observe('response', 'GET', '/v3/openapi', f.raw_docs[0] + b' ', 200), 'api-operation-schema-drift'):
        return False
    for ref in ('https://example.invalid/schema', '#/paths/~1v3~1documents/post/requestBody'):
        f = api_session_fixture()
        f.docs[0]['paths']['/v3/documents']['post']['requestBody'] = {'$ref': ref}
        f = api_session_fixture(docs=f.docs)
        if not f.schemas() or not named_api_refusal(lambda: f.observe('request', 'POST', '/v3/documents', b'{"content":"q"}'), 'api-operation-reference'):
            return False
    return True


def api_session_order_negative_controls():
    f = api_session_fixture()
    if not named_api_refusal(lambda: f.observe('request', 'POST', '/v3/documents', b'{"content":"q"}'), 'api-operation-schemas-unobserved'):
        return False
    f = api_session_fixture()
    if not f.schemas() or f.observe('request', 'POST', '/v3/documents', b'{"content":"q"}') is not True:
        return False
    if f.session.summary(P.sm_openapi_contract(f.replies, REST_SM))['value_constraints_verified'] is not False:
        return False
    if not named_api_refusal(lambda: f.observe('request', 'POST', '/v3/documents', b'{"content":"q"}'), 'api-operation-order'):
        return False
    for status, raw in ((200, b'{}'), (400, b'{"x":1,"x":2}')):
        f = api_session_fixture()
        if not f.schemas() or f.observe('request', 'POST', '/v4/memories/list', f.session.negative_body) is not True:
            return False
        reason = 'api-operation-negative-status' if status == 200 else 'api-json-duplicate-member'
        if not named_api_refusal(lambda: f.observe('response', 'POST', '/v4/memories/list', raw, status), reason):
            return False
    f = api_session_fixture()
    if not api_session_complete(f):
        return False
    if not named_api_refusal(lambda: f.observe('request', 'POST', '/v4/memories/list', f.session.negative_body), 'api-operation-negative-repeated'):
        return False
    return f.session.summary(P.sm_openapi_contract(f.replies, REST_SM))['value_constraints_verified'] is False


def api_session_bounds_controls():
    for ceiling in (0, -1, True, float('inf'), float('nan'), 10**1000):
        f = api_session_fixture()
        if not named_api_refusal(lambda: f.observe('request', 'GET', '/v3/openapi', ceiling_s=ceiling), 'api-operation-ceiling'):
            return False
    f = api_session_fixture()
    f.session.lock.acquire()
    try:
        if not named_api_refusal(lambda: f.observe('request', 'GET', '/v3/openapi', ceiling_s=0.01), 'api-operation-lock'):
            return False
    finally:
        f.session.lock.release()
    f = api_session_fixture()
    f.session.traces = [{}] * 2048
    if not named_api_refusal(lambda: f.observe('request', 'GET', '/v3/openapi'), 'api-operation-session'):
        return False
    f = api_session_fixture()
    f.observe('request', 'GET', '/v3/openapi')
    f.session.trace_bytes = 2 * 1024 * 1024
    if not named_api_refusal(lambda: f.observe('response', 'GET', '/v3/openapi', f.raw_docs[0], 200), 'api-operation-trace-size'):
        return False
    ticks = iter((0.0, 0.0, 1.0))
    f = api_session_fixture(clock=lambda: next(ticks))
    return named_api_refusal(lambda: f.observe('request', 'GET', '/v3/openapi'), 'state-read-deadline')


check('A8S-API-SESSION-POSITIVE-1: completed exact pairs bind both raw schemas and actual body/path values; public proof is isolated and runtime remains false', api_session_positive_controls)
check('A8S-API-SESSION-REQUEST-1: actual outgoing bodies and canonical path ids obey each observed schema before use', api_session_request_controls)
check('A8S-API-SESSION-RESPONSE-1: incoming exact values, method/path/status and thread must match the pending request', api_session_response_controls)
check('A8S-API-SESSION-REFERENCE-1: local escaped reference targets preserve source pointers; remote/cyclic containers and byte drift refuse', api_session_reference_drift_controls)
check('A8S-API-SESSION-ORDER-1: unobserved, incomplete, overlapping or repeated negative calls cannot yield a positive summary', api_session_order_negative_controls)
check('A8S-API-SESSION-BOUNDS-1: finite ceilings, shared deadline, lock, trace count and serialized trace bytes are bounded', api_session_bounds_controls)

def api_session_observed_request(f, session):
    """Synthetic transport honors exact observation; real sockets are covered separately."""
    def request(port, method, path, **options):
        observer = options.pop('json_observer')
        body = options.get('body')
        raw = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8') if body is not None else None
        observer('request', method=method, path=path, raw=raw, status=None, ceiling_s=min(1.0, options['timeout_s']))
        reply = f.request(port, method, path, **options)
        # Fixture schemas serialize in the independent contract's declared order.
        response = json.dumps(reply['json'], sort_keys=True, allow_nan=False).encode('utf-8')
        observer('response', method=method, path=path, raw=response, status=reply['status'], ceiling_s=min(1.0, options['timeout_s']))
        return dict(reply, sha256=hashlib.sha256(response).hexdigest())
    return request


def api_session_send_binding_controls():
    f = api_session_fixture()
    if not api_session_complete(f):
        return False
    request = lambda *a, **k: {'status': 200, 'json': {}, 'sha256': '0' * 64}
    if not named_api_refusal(lambda: f.session.send(request, 1234, 'GET', '/v3/openapi', timeout_s=1.0), 'api-operation-unobserved-return'):
        return False
    for fault, reason in (('hash', 'api-operation-return-binding'), ('status', 'api-operation-return-binding'),
                          ('value', 'api-operation-return-value')):
        f = api_session_fixture()
        def observed_request(port, method, path, **options):
            raw = f.raw_docs[0]
            observer = options['json_observer']
            observer('request', method=method, path=path, raw=None, status=None, ceiling_s=1.0)
            observer('response', method=method, path=path, raw=raw, status=200, ceiling_s=1.0)
            reply = {'status': 200, 'json': f.docs[0], 'sha256': hashlib.sha256(raw).hexdigest()}
            if fault == 'hash':
                reply['sha256'] = '0' * 64
            elif fault == 'status':
                reply['status'] = 201
            else:
                reply['json'] = dict(reply['json'], extra='different consumed value')
            return reply
        if not named_api_refusal(lambda: f.session.send(observed_request, 1234, 'GET', '/v3/openapi', timeout_s=1.0), reason):
            return False
        if not f.session.faulted:
            return False
    return True


def api_session_p3_p4_controls():
    f = functional_fixture()
    session = P.SmApiValueSession('unit', clock=lambda: f.now)
    observed_request = api_session_observed_request(f, session)
    p3 = f.run(request=observed_request, value_session=session)
    if p3['schema']['value_constraints_verified'] is not True or p3['runtime_admission'] is not False:
        return False
    if len(p3['schema']['traces']) != len(f.requests) or len(f.admissions) != len(f.requests) + 1:
        return False
    for port in (1234, 1235):
        start = len(f.requests)
        state = P.read_sm_restart_state(port, 'unit', 'doc1', key=f.key, request=observed_request,
            admit=f.admit, rest=REST_SM, value_session=session, clock=lambda: f.now)
        if (state['complete'] is not True or state['schema']['value_constraints_verified'] is not True
                or state['atomic_snapshot_proven'] is not False or state['writers_quiescent_proven'] is not False):
            return False
        calls = f.requests[start:]
        if [(m, p) for _, m, p, _ in calls] != [('GET', '/v3/openapi'), ('GET', '/v4/openapi'),
                ('GET', '/v3/documents/doc1'), ('POST', '/v4/memories/list'), ('POST', '/v4/search')]:
            return False
        if any(p != port for p, _, _, _ in calls):
            return False
    if len(session.traces) != len(f.requests) or len(f.admissions) != len(f.requests) + 1:
        return False
    stale = p3['schema']
    f.schema = dict(f.schema, info={'title': 'changed after restart'})
    if not named_api_refusal(lambda: P.read_sm_restart_state(1235, 'unit', 'doc1', key=f.key,
            request=observed_request, admit=f.admit, rest=REST_SM, value_session=session, clock=lambda: f.now), 'api-operation-schema-drift'):
        return False
    return session.summary(stale)['value_constraints_verified'] is False


check('A8S-API-RETURN-1: stale traces, wrong response hashes/statuses or different consumed values cannot certify a returned request', api_session_send_binding_controls)
check('A8S-API-P3-P4-1: P3 and both restart snapshots observe every actual value and freshly reobserve both pinned schemas under their shared admission/deadline', api_session_p3_p4_controls)

def api_session_contract_binding_controls():
    f = api_session_fixture()
    if not api_session_complete(f):
        return False
    original = P.sm_openapi_contract(f.replies, REST_SM)
    for fault in ('canonical', 'conformant', 'duplicate', 'missing', 'operations'):
        contract = copy.deepcopy(original)
        if fault == 'canonical':
            contract['observed'][0]['canonical_sha256'] = '0' * 64
        elif fault == 'conformant':
            contract['conformant'] = 1
        elif fault == 'duplicate':
            contract['observed'].append(copy.deepcopy(contract['observed'][0]))
        elif fault == 'missing':
            del contract['observed'][1]['canonical_sha256']
        else:
            contract['operations'] = ('add_document',)
        if f.session.summary(contract)['value_constraints_verified'] is not False:
            return False
    changed = copy.deepcopy(f.docs[0])
    changed['info'] = {'title': 'Different projected document under the actual raw hash'}
    misleading = [dict(reply, json=changed) for reply in f.replies]
    unrelated = P.sm_openapi_contract(misleading, REST_SM)
    return f.session.summary(unrelated)['value_constraints_verified'] is False and f.session.summary(original)['value_constraints_verified'] is True


def api_session_parameter_semantics_controls():
    for options in ({'style': 'matrix'}, {'style': 'label'}, {'content': {'application/json': {'schema': {'type': 'string'}}}},
                    {'explode': 1}, {'allowReserved': True}, {'allowEmptyValue': True}):
        f = api_session_fixture()
        f.docs[0]['paths']['/v3/documents/{id}']['get']['parameters'][0].update(options)
        f = api_session_fixture(docs=f.docs)
        if not f.schemas() or not named_api_refusal(lambda: f.observe('request', 'GET', '/v3/documents/doc1'), 'api-operation-parameter-semantics'):
            return False
    f = api_session_fixture()
    for doc in f.docs:
        doc['paths']['/v3/documents/{id}']['get']['parameters'][0].update(style='simple', explode=True)
    f = api_session_fixture(docs=f.docs)
    if not f.schemas() or not f.pair('GET', '/v3/documents/doc1', None, b'{"id":"doc1","status":"done"}'):
        return False
    # Full existing255-codepoint input contract: 3-byte BMP and4-byte scalar URL expansion.
    for identifier in (chr(0x4E00) * 171, chr(0x1F642) * 255):
        _, path, body = P.sm_api_target('get_document', 'unit', document_id=identifier)
        if f.session._operation('GET', path) != ('get_document', identifier) or body is not None:
            return False
    return True


def api_session_entry_fault_controls():
    for fault in ('ceiling', 'clock', 'lock'):
        f = api_session_fixture()
        if not api_session_complete(f):
            return False
        contract = P.sm_openapi_contract(f.replies, REST_SM)
        if f.session.summary(contract)['value_constraints_verified'] is not True:
            return False
        options, reason = {}, 'api-operation-ceiling'
        if fault == 'ceiling':
            options['ceiling_s'] = 0
        elif fault == 'clock':
            f.session.clock = lambda: float('nan')
            reason = 'state-read-clock'
        else:
            f.session.lock.acquire()
            options['ceiling_s'] = 0.01
            reason = 'api-operation-lock'
        try:
            if not named_api_refusal(lambda: f.observe('request', 'GET', '/v3/openapi', **options), reason):
                return False
        finally:
            if fault == 'lock':
                f.session.lock.release()
            f.session.clock = time.monotonic
        if f.session.summary(contract)['value_constraints_verified'] is not False or not f.session.faulted:
            return False
    return True


check('A8S-API-CONTRACT-BINDING-1: raw and canonical observations must identify the same two documents and literal complete contract', api_session_contract_binding_controls)
check('A8S-API-PARAMETER-SEMANTICS-1: unsupported path serialization refuses; simple primitive ids preserve the existing255-codepoint route ceiling', api_session_parameter_semantics_controls)
check('A8S-API-ENTRY-FAULT-1: an observation refused at ceiling clock or lock entry invalidates a formerly successful session', api_session_entry_fault_controls)

def api_session_concurrent_send_controls():
    f = api_session_fixture()
    entered, other_done = threading.Event(), threading.Event()
    shared, outcomes, errors = {}, [], []
    def caller_a(port, method, path, **options):
        entered.set()
        if not other_done.wait(0.5):
            raise AssertionError('owned competing send did not finish')
        return shared.get('reply', {'status': 200, 'json': {}, 'sha256': '0' * 64})
    def caller_b(port, method, path, **options):
        raw = f.raw_docs[0]
        observer = options['json_observer']
        observer('request', method=method, path=path, raw=None, status=None, ceiling_s=0.1)
        observer('response', method=method, path=path, raw=raw, status=200, ceiling_s=0.1)
        reply = {'status': 200, 'json': f.docs[0], 'sha256': hashlib.sha256(raw).hexdigest()}
        shared['reply'] = reply
        return reply
    def invoke(name, caller, ceiling):
        try:
            f.session.send(caller, 1234, 'GET', '/v3/openapi', timeout_s=ceiling)
            outcomes.append((name, 'PASS'))
        except P.SmApiRefused as exc:
            outcomes.append((name, str(exc)))
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            if name == 'B':
                other_done.set()
    a = threading.Thread(target=invoke, args=('A', caller_a, 1.0))
    b = threading.Thread(target=invoke, args=('B', caller_b, 0.05))
    a.start()
    try:
        if not entered.wait(0.5):
            return False
        b.start()
        b.join(1.0)
    finally:
        other_done.set()
        a.join(1.0)
        if b.ident is not None:
            b.join(1.0)
    return (not a.is_alive() and not b.is_alive() and errors == [] and len(outcomes) == 2
        and all(result != 'PASS' for _, result in outcomes) and ('B', 'api-operation-send-lock') in outcomes)


def api_session_outbound_binding_controls():
    f = api_session_fixture()
    for doc in f.docs:
        doc['paths']['/v4/memories/list']['post']['requestBody']['content']['application/json']['schema']['properties']['limit']['maximum'] = 1
    f = api_session_fixture(docs=f.docs)
    if not f.schemas():
        return False
    transmitted = []
    def request(port, method, path, **options):
        observer = options['json_observer']
        observer('request', method=method, path=path, raw=b'{"containerTags":["unit_u1"],"limit":1}', status=None, ceiling_s=1.0)
        transmitted.append(options['body'])
        raw = b'{}'
        observer('response', method=method, path=path, raw=raw, status=200, ceiling_s=1.0)
        return {'status': 200, 'json': {}, 'sha256': hashlib.sha256(raw).hexdigest()}
    return (named_api_refusal(lambda: f.session.send(request, 1234, 'POST', '/v4/memories/list', timeout_s=1.0,
        body={'containerTags': ['unit_u1'], 'limit': 10}), 'api-operation-request-binding') and transmitted == [])


def api_session_return_allocation_controls():
    for count, reason in ((20000, 'api-json-encode-structure'), (1000, 'api-json-encode-size')):
        f = api_session_fixture()
        alias = ['x' * (2 * 1024 * 1024 - 4)] * count
        original = P.json.dumps
        def guarded_dumps(value, *args, **options):
            if value is alias:
                raise AssertionError('unbounded whole-object allocation attempted')
            return original(value, *args, **options)
        def request(port, method, path, **options):
            raw = f.raw_docs[0]
            options['json_observer']('request', method=method, path=path, raw=None, status=None, ceiling_s=1.0)
            options['json_observer']('response', method=method, path=path, raw=raw, status=200, ceiling_s=1.0)
            return {'status': 200, 'json': alias, 'sha256': hashlib.sha256(raw).hexdigest()}
        with mock.patch.object(P.json, 'dumps', side_effect=guarded_dumps):
            if not named_api_refusal(lambda: f.session.send(request, 1234, 'GET', '/v3/openapi', timeout_s=1.0), reason):
                return False
    return True


def api_json_encoding_bounds_controls():
    values = ({'b': [0.1, True, None, '\n"\\' + chr(0x4E00)], 'a': 10**999}, ['x' * 4095 + '\n' + chr(0x1F642)], -10**998)
    for value in values:
        for ordered in (False, True):
            expected = json.dumps(value, sort_keys=ordered, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
            if P.sm_encode_api_json(value, sort_keys=ordered) != expected:
                return False
    accepted = 'x' * (2 * 1024 * 1024 - 2)
    if len(P.sm_encode_api_json(accepted)) != 2 * 1024 * 1024:
        return False
    for value, reason in ((accepted + 'x', 'api-json-encode-size'), (10**1000, 'api-json-encode-number'),
                         (float('nan'), 'api-json-encode-number'), ({1: 'x'}, 'api-json-encode-type'),
                         ('\ud800', 'api-json-encode-unicode'), (['x'] * 20000, 'api-json-encode-structure')):
        if not named_api_refusal(lambda: P.sm_encode_api_json(value), reason):
            return False
    cycle = []
    cycle.append(cycle)
    if not named_api_refusal(lambda: P.sm_encode_api_json(cycle), 'api-json-encode-structure'):
        return False
    ticks = iter((0.0, 0.0, 1.0))
    return named_api_refusal(lambda: P.sm_encode_api_json('x', clock=lambda: next(ticks)), 'state-read-deadline')


check('A8S-API-SEND-OWNERSHIP-1: a waiting caller cannot borrow another thread complete pair as its own request', api_session_concurrent_send_controls)
check('A8S-API-OUTBOUND-BINDING-1: observed request bytes must equal this invocation body before a collaborator can send', api_session_outbound_binding_controls)
check('A8S-API-RETURN-ALLOCATION-1: aliased huge consumer objects refuse before unbounded whole-object JSON allocation', api_session_return_allocation_controls)
check('A8S-API-ENCODING-BOUNDS-1: exact compact built-in encoding respects bytes nodes cycles numbers Unicode and one deadline', api_json_encoding_bounds_controls)

def api_session_p3_p4_real_http_controls():
    """Exercise complete value wiring over owned sockets; not child durability or C6."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    f = functional_fixture()
    f.key = SS.HeldKey(b'sm_' + b'V' * 30)
    f.schema = json.loads(json.dumps(f.schema))
    f.schema['paths']['/v4/search']['post']['responses']['200']['content']['application/json']['schema']['properties']['results']['items']['properties']['similarity'].update(maximum=1)
    session = P.SmApiValueSession('unit')
    owners, accepted, errors = [], [], []
    poison = [False]
    good = False
    def start():
        stop = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(0.25)
                super().setup()
            def log_message(self, *args):
                pass
            def respond(self):
                try:
                    own_key = self.headers.get(SS.KEY_HEADER) == f.key.header()[SS.KEY_HEADER]
                    if not own_key:
                        raise AssertionError('own fixture key not received')
                    body = None
                    if self.command == 'POST':
                        count = int(self.headers['Content-Length'])
                        if not 0 < count <= 2 * 1024 * 1024:
                            raise AssertionError('unexpected own body size')
                        raw = self.rfile.read(count)
                        body = json.loads(raw)
                    accepted.append((self.server.server_address[1], self.command, self.path, own_key))
                    reply = f.request(self.server.server_address[1], self.command, self.path, body=body, key=f.key)
                    raw = json.dumps(reply['json'], sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
                    if poison[0] and self.path == '/v4/search':
                        if raw.count(b'"similarity": 0.9') != 1:
                            raise AssertionError('exact owned response witness missing')
                        raw = raw.replace(b'"similarity": 0.9', b'"similarity": 1.0000000000000001')
                    self.send_response(reply['status'])
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except Exception as exc:
                    errors.append(type(exc).__name__)
                    self.close_connection = True
            do_GET = respond
            do_POST = respond
        server = HTTPServer(('127.0.0.1', 0), Handler, bind_and_activate=False)
        owner = SimpleNamespace(server=server, stop=stop, worker=None, initially_drained=False)
        owners.append(owner)  # Retain the object before acquisition can fail.
        server.server_bind()
        server.server_activate()
        server.timeout = 0.05
        def serve():
            try:
                while not stop.is_set():
                    server.handle_request()
            except Exception as exc:
                errors.append(type(exc).__name__)
        owner.worker = threading.Thread(target=serve, name='owned-sm-value-http', daemon=True)
        owner.worker.start()
        return owner, server.server_address[1]
    def close(owner):
        owner.stop.set()
        if owner.worker is not None and owner.worker.ident is not None:
            owner.worker.join(1.0)
            owner.initially_drained = not owner.worker.is_alive()
        owner.server.server_close()
        if owner.worker is not None and owner.worker.is_alive():
            owner.worker.join(0.5)
        return owner.initially_drained
    try:
        owner1, port1 = start()
        p3 = P.run_sm_p3(port1, 'unit', key=f.key, rest=REST_SM, request=P.sm_request, admit=f.admit,
            prepare_routes=lambda **k: True, observe_routes=lambda **k: f.witness, model='deepseek-flash',
            embed_model='bge-m3:latest', ceiling_s=15.0, sleep=lambda seconds: time.sleep(min(seconds, 0.001)), value_session=session)
        first = P.read_sm_restart_state(port1, 'unit', 'doc1', key=f.key, request=P.sm_request,
            admit=f.admit, rest=REST_SM, value_session=session, ceiling_s=10.0)
        if not close(owner1):
            return False
        # An OS may legitimately reuse a just-closed ephemeral port; retry own allocations only.
        for _ in range(3):
            owner2, port2 = start()
            if port2 != port1:
                break
            if not close(owner2):
                return False
        else:
            return False
        second = P.read_sm_restart_state(port2, 'unit', 'doc1', key=f.key, request=P.sm_request,
            admit=f.admit, rest=REST_SM, value_session=session, ceiling_s=10.0)
        if (p3['schema']['value_constraints_verified'] is not True or len(p3['schema']['traces']) != 8
                or first['schema']['value_constraints_verified'] is not True or len(first['schema']['traces']) != 13
                or second['schema']['value_constraints_verified'] is not True or len(second['schema']['traces']) != 18
                or first['seal'] != second['seal'] or p3['runtime_admission'] is not False):
            return False
        poison[0] = True
        if not named_api_refusal(lambda: P.read_sm_restart_state(port2, 'unit', 'doc1', key=f.key,
                request=P.sm_request, admit=f.admit, rest=REST_SM, value_session=session, ceiling_s=10.0), 'api-schema-value'):
            return False
        final = session.summary(second['schema'])
        text = json.dumps(final, sort_keys=True)
        good = (len(accepted) == 23 and len(session.traces) == 22 and final['value_constraints_verified'] is False
            and all(own for _, _, _, own in accepted) and f.key.header()[SS.KEY_HEADER] not in text
            and 'Rina keeps' not in text and errors == [])
    finally:
        for owner in owners:
            if not owner.initially_drained and not close(owner):
                good = False
    return good and all(owner.initially_drained and not owner.worker.is_alive() for owner in owners)


check('A8S-API-P3-P4-HTTP-1: full P3 and two fresh-schema snapshot reads use owned keyed literal127 sockets; exact invalid inbound value refuses and all workers drain', api_session_p3_p4_real_http_controls)

def api_session_controller_case(*, drift=False):
    with tempfile.TemporaryDirectory(prefix='nvt-sm-schema-controller-') as directory:
        fixture = p4p5_protocol_fixture(Path(directory).resolve())
        previous = fixture.f.deps.setup
        captured = {}
        def setup(*args):
            runtime = previous(*args)
            session = P.SmApiValueSession('boot-control')
            captured['session'] = session
            original = runtime.request
            def request(port, method, path, **options):
                observer = options.pop('json_observer', None)
                if observer is None:
                    return original(port, method, path, **options)
                body = options.get('body')
                raw = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8') if body is not None else None
                observer('request', method=method, path=path, raw=raw, status=None, ceiling_s=min(1.0, options['timeout_s']))
                reply = original(port, method, path, **options)
                response = json.dumps(reply['json'], sort_keys=True, allow_nan=False).encode('utf-8')
                if drift and port == 43200 and path == '/v4/openapi':
                    response += b' '
                observer('response', method=method, path=path, raw=response, status=reply['status'], ceiling_s=min(1.0, options['timeout_s']))
                return dict(reply, sha256=hashlib.sha256(response).hexdigest())
            runtime.request, runtime.value_session = request, session
            return runtime
        fixture.f.deps.setup = setup
        result = fixture.f.run_probe()
        fields, session = result['fields'], captured['session']
        stops = fields['sm_stop']['value']
        if (fixture.state['spawned'] != ['u1', 'u1-boot2'] or len(stops) != 2
                or any(row['complete'] is not True or row['how'] != 'ctrl_break' for row in stops)
                or fields['sm_key_search']['ok'] is not True or result['outcome'] != 'fail'):
            return False
        if drift:
            return (result['functional_refusal'] == 'api-operation-schema-drift' and session.faulted
                and fields['sm_schema']['ok'] is False and fields['sm_schema']['value']['value_constraints_verified'] is False
                and result['restart']['complete'] is False and fixture.state['firewalls'] == ['fw-p0', 'fw-p4'])
        schema = fields['sm_schema']['value']
        return (result['restart']['complete'] is True and fields['sm_schema']['ok'] is True
            and schema['value_constraints_verified'] is True and schema['runtime_admission'] is False
            and len(schema['traces']) == len(session.traces) == 18
            and result['restart']['schema_before'] == result['restart']['schema_after'] == schema['observed']
            and fields['sm_durability']['ok'] is True and fixture.state['firewalls'] == ['fw-p0', 'fw-p4', 'fw-p5'])


check('A8S-API-CONTROLLER-VALUES-1: connected P0-P5 controller propagates actual session through both snapshots while full admission stays refused', lambda: api_session_controller_case())
check('A8S-API-CONTROLLER-DRIFT-1: boot2 schema drift refreshes a former schema pass to fail and still gracefully drains both retained boots', lambda: api_session_controller_case(drift=True))

def api_session_local_receipts_controls():
    f = api_session_fixture()
    def foreign(port, method, path, **options):
        raw = f.raw_docs[0]
        # The caller ignores its passed observer and uses the public session directly.
        f.session.observer('request', method=method, path=path, raw=None, status=None, ceiling_s=1.0)
        f.session.observer('response', method=method, path=path, raw=raw, status=200, ceiling_s=1.0)
        return {'status': 200, 'json': f.docs[0], 'sha256': hashlib.sha256(raw).hexdigest()}
    if not named_api_refusal(lambda: f.session.send(foreign, 1234, 'GET', '/v3/openapi', timeout_s=1.0), 'api-operation-unobserved-return'):
        return False
    f = api_session_fixture()
    saved = []
    def proper(port, method, path, **options):
        raw, observer = f.raw_docs[0], options['json_observer']
        saved.append(observer)
        observer('request', method=method, path=path, raw=None, status=None, ceiling_s=1.0)
        observer('response', method=method, path=path, raw=raw, status=200, ceiling_s=1.0)
        return {'status': 200, 'json': f.docs[0], 'sha256': hashlib.sha256(raw).hexdigest()}
    f.session.send(proper, 1234, 'GET', '/v3/openapi', timeout_s=1.0)
    return (named_api_refusal(lambda: saved[0]('request', method='GET', path='/v3/openapi', raw=None,
        status=None, ceiling_s=1.0), 'api-operation-observation-lifetime') and f.session.faulted)


def api_json_encoding_structure_boundaries_controls():
    accepted = 'leaf'
    for _ in range(32):
        accepted = [accepted]
    if P.sm_encode_api_json(accepted) != json.dumps(accepted, separators=(',', ':')).encode():
        return False
    if not named_api_refusal(lambda: P.sm_encode_api_json([accepted]), 'api-json-encode-structure'):
        return False
    nodes = [None] * 19999
    if P.sm_encode_api_json(nodes) != json.dumps(nodes, separators=(',', ':')).encode():
        return False
    if not named_api_refusal(lambda: P.sm_encode_api_json(nodes + [None]), 'api-json-encode-structure'):
        return False
    return True


check('A8S-API-LOCAL-RECEIPTS-1: a send requires its own successful callback pair; direct foreign traces and retained callbacks cannot certify it', api_session_local_receipts_controls)
check('A8S-API-ENCODING-STRUCTURE-1: exact depth32 and20000-node values encode; the next depth or repeated node refuses', api_json_encoding_structure_boundaries_controls)

def api_session_callback_baseexit_controls():
    for exception in (KeyboardInterrupt, SystemExit):
        f = api_session_fixture()
        callbacks = []
        def aborted(port, method, path, **options):
            callbacks.append(options['json_observer'])
            raise exception('owned fixture interruption')
        try:
            f.session.send(aborted, 1234, 'GET', '/v3/openapi', timeout_s=1.0)
            return False
        except exception:
            pass
        if not named_api_refusal(lambda: callbacks[0]('request', method='GET', path='/v3/openapi',
                raw=None, status=None, ceiling_s=1.0), 'api-operation-observation-lifetime'):
            return False
        if not f.session.faulted or f.session.pending is not None or not f.session.send_lock.acquire(blocking=False):
            return False
        f.session.send_lock.release()
    return True


check('A8S-API-CALLBACK-BASEEXIT-1: interrupted calls propagate KeyboardInterrupt/SystemExit and close their retained callback before any future observation', api_session_callback_baseexit_controls)

failed = sum(not good for _, good in ROWS)
print(f'v3 sm core: {len(ROWS) - failed} passed, {failed} failed')
sys.exit(1 if failed else 0)
