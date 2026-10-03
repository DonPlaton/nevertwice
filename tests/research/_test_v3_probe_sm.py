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
    def members(self, pid):
        if self.broken:
            raise OSError('owned fake failure')
        return list(self.live[self.jobs[pid]])
    def terminate(self, pid):
        handle = self.jobs[pid]
        self.killed.append(handle)
        self.live[handle] = []
        return True


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
        proxy = SimpleNamespace(control=SimpleNamespace(counters=lambda: {}), run_dir=arm / '_proxy/run',
            ports={'scheduler': 44010, 'arms': {P.ARM: {'write': 44011, 'reader': 44012, 'ollama': 44013}}},
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
        probe = SimpleNamespace(probe_modules=lambda: modules, ProbeGate=Gate)
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
        finally:
            results = [accepted(close()) for _, close, accepted in reversed(resources)]
        raw = (root / '_a8/factory-control/STATUS').read_text(encoding='utf-8')
        return (results == [True] * 5 and events == ['gate-poll', 'fake-close', 'fake-close']
                and ' ABORT ' in raw and ' BLOCK _a8/p0p2 END ' in raw and ' STAND _a8 END ' in raw
                and not launch.spawn_proxy.called)


check('A8S-RUNTIME-FACTORY-1: deferred production wiring keeps actual9 env/hl8/embed guard, pinned auth body, and authority refusal', runtime_factory_controls)

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
        class Jobs(BootJobs, L.WinJobs):
            def _new_job(self):
                return 111
            def adopt(self, pid, handle=None):
                return L.WinJobs.adopt(self, pid, handle)
        jobs = Jobs()
        jobs.k32 = SimpleNamespace(AssignProcessToJobObject=lambda job, handle: True)
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
        stream = None
        try:
            if fake.host != '127.0.0.1' or len(resources) != 1:
                return False
            stream = socket.create_connection((fake.host, fake.port), timeout=1)
            stream.sendall(b'POST /v1/embeddings HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 100\r\n\r\n{')
            until = time.monotonic() + 1
            while not fake.workers and time.monotonic() < until:
                time.sleep(0.005)
            if not fake.workers or not fake.sockets:
                return False
            row = fake.close(drain_s=1.0)
            if (row['complete'] is not True or row['active_sockets'] != 0 or row['live_workers'] != 0
                    or any(worker.is_alive() for worker in fake.workers)):
                return False
        finally:
            if stream is not None:
                stream.close()
            fake.close(drain_s=1)
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
            class Jobs(BootJobs, L.WinJobs):
                def _new_job(self):
                    return 111
                def adopt(self, pid, handle=None):
                    if fault == 'adopt-before':
                        raise OSError('owned adoption failure')
                    birth = L.WinJobs.adopt(self, pid, handle)
                    if fault == 'adopt-after':
                        self.jobs[pid] = 99
                        raise OSError('owned post-adoption and replaced-map failure')
                    return birth
            jobs = Jobs()
            jobs.k32 = SimpleNamespace(AssignProcessToJobObject=lambda job, handle: True)
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
    for field, value in (('id', ''), ('memory', ' '), ('documentIds', []), ('documentIds', [True])):
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

failed = sum(not good for _, good in ROWS)
print(f'v3 sm core: {len(ROWS) - failed} passed, {failed} failed')
sys.exit(1 if failed else 0)
