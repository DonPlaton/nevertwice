"""Guarded Supermemory A8 P0-P2 boundaries; later protocol phases remain incomplete."""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence

ARM = 'supermemory-local'
BUNDLE_RUN = 'f2'
BUNDLE_RECORD_SHA256 = '54d22fed690cd71f8ed90361e894d489454713838aa6829085c75baa580e093e'
BINARY_SHA256 = 'd8fb2ac0d52eeb230ad15dc8bf70dbc2ae481f0f8cfaeec70d97f7955ce71c47'
TOOLS = ('searchMemories', 'readChunks', 'searchDocumentChunks', 'getMemoryDetails',
         'linkMemories', 'forgetMemory', 'CreateMemory')
TOOLS_SHA256 = 'd8e70db9496267edb7b6ce4e692ec16e194050203403e07492ecfca143d888b4'
SCHEMAS = {'CreateMemory': 'b31256067c01cee4a05f3caa6ef92e1a6be1558aefd8f876a82caed6a7c7f451',
           'relation': '61213706cf99213d9723eab635efa554b048a0524c099d442c4f0f9e15cc5090'}
SYSTEM_SHA256 = 'fedd051ba424019504b63212d6e054ca42d650748842d496216d9f712eb602d9'
PROFILES = frozenset({'Domain', 'Private', 'Public'})
BOUNDARY = frozenset({'sm_firewall', 'sm_bind', 'sm_tree', 'sm_egress_boot', 'sm_auth',
                      'sm_keycarry', 'sm_leg_auth', 'sm_stop', 'sm_key_search'})
FIELDS = ('sm_pin', 'sm_bundle', 'sm_firewall', 'sm_switches', 'sm_workflow', 'sm_runs',
          'sm_version', 'sm_bind', 'sm_tree', 'sm_egress_boot', 'sm_key_form', 'sm_home',
          'sm_auth', 'sm_keycarry', 'sm_openapi', 'sm_schema', 'sm_ct_dot', 'sm_conformance',
          'sm_statuses', 'sm_llm_params', 'sm_embed_route', 'sm_leg_auth', 'sm_search',
          'sm_created_at', 'sm_llm_log_line', 'sm_stop', 'sm_durability', 'sm_key_reprint',
          'sm_rss', 'sm_doc_limit', 'sm_key_search')
FAKE_KNOBS = frozenset({'NVT3_FAKE_LOCAL_BY', 'NVT3_FAKE_TOOLS', 'NVT3_FAKE_PERSIST',
                        'NVT3_FAKE_REPRINT', 'NVT3_FAKE_PHONE_HOME', 'NVT3_FAKE_EXTRA_LISTENER',
                        'NVT3_FAKE_LEAK_HOME', 'NVT3_FAKE_IGNORE_BREAK'})
RECORD_TOKEN = re.compile(r'sm_[A-Za-z0-9_-]{8,}')
LABEL = re.compile(r'[A-Za-z0-9_-]{1,128}')
RECORD_MAX_BYTES = 1 << 20
BLOCKED_OUTCOMES = frozenset({'blocked:auth-control', 'blocked:no-openapi', 'blocked:tools-refused',
    'blocked:no-llm-call', 'blocked:no-embed-call', 'blocked:search-shape',
    *(f'blocked:schema-missing:{op}' for op in ('add_document', 'get_document', 'search', 'list_memories'))})
CHECK_COUNTS = ('native_hits', 'fs_hits', 'process_errors', 'ancestor_found')


def c6_check_summary(raw: Mapping[str, Any]) -> dict:
    """Keep every boundary count; incomplete or malformed projections cannot pass."""
    native, fs, ancestor = (raw.get(name) for name in ('native', 'fs', 'ancestor_claude'))
    native = native if isinstance(native, dict) else {}
    fs = fs if isinstance(fs, dict) else {}
    ancestor = ancestor if isinstance(ancestor, dict) else {}
    names = ('hits', 'loopback_hits', 'listen_nonloopback', 'undetermined_loopback')
    counts = [native.get(name) for name in names]
    hits = sum(counts) if all(type(value) is int and value >= 0 for value in counts) else None
    return {'complete': raw.get('complete') is True, 'native_hits': hits,
            'fs_hits': fs.get('fs_hits'), 'process_errors': native.get('process_errors'),
            'ancestor_found': ancestor.get('found')}


def clean_check(summary: Mapping[str, Any]) -> bool:
    return (summary.get('complete') is True and all(type(summary.get(name)) is int
            and summary[name] == 0 for name in CHECK_COUNTS))


class PreflightRefused(RuntimeError):
    """A named static refusal; never include external exception values."""


class SmRequestRefused(RuntimeError):
    """A request refused before any connection can be created."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_record(contract: Any, launch: Any) -> dict:
    """Verify the immutable f2 record, complete baseline and actual binary bytes."""
    runs_root = Path(contract.runs_root)
    path = runs_root / '_facts/a8-sm-bundle' / BUNDLE_RUN / 'bundle_facts.json'
    try:
        if not launch._within(path, runs_root) or launch._link_in_chain(runs_root, path):
            raise PreflightRefused('bundle-record-path')
        with path.open('rb') as stream:
            raw = stream.read(RECORD_MAX_BYTES + 1)
        if len(raw) > RECORD_MAX_BYTES:
            raise PreflightRefused('bundle-record-limit')
        if hashlib.sha256(raw).hexdigest() != BUNDLE_RECORD_SHA256:
            raise PreflightRefused('bundle-record-pin')
        rec = json.loads(raw)
        tools, pin = rec['tools'], rec['pin']
        if (rec['run'] != BUNDLE_RUN or rec['problems'] != [] or rec['route']['problems'] != []
                or tuple(tools['names']) != TOOLS or tools['sha256'] != TOOLS_SHA256
                or tools['schemas'] != SCHEMAS or tools['system_sha256'] != SYSTEM_SHA256
                or pin['binary_sha256'] != BINARY_SHA256):
            raise PreflightRefused('bundle-baseline')
        binary = Path(pin['binary'])
        if (not binary.is_absolute() or not launch._within(binary, contract.polygon_root)
                or launch._link_in_chain(contract.polygon_root, binary) or not binary.is_file()):
            raise PreflightRefused('binary-path')
        if binary.stat().st_size != pin['bytes'] or file_sha256(binary) != BINARY_SHA256:
            raise PreflightRefused('binary-pin')
        return rec
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise PreflightRefused('bundle-unreadable:' + type(exc).__name__) from None


def owned_arm_dir(contract: Any, launch: Any, run: str) -> Path:
    """Validate both written and resolved paths before consuming a run label."""
    if not isinstance(run, str) or LABEL.fullmatch(run) is None or run in {'.', '..'}:
        raise PreflightRefused('run-label')
    root = Path(contract.runs_root)
    dest = root / '_a8' / run / ARM
    if not launch._within(dest, root) or launch._link_in_chain(root, dest):
        raise PreflightRefused('run-path')
    if os.path.lexists(dest.parent):
        raise PreflightRefused('used-run-label')
    return dest


def owner_env_exists(owner_home: Path) -> bool:
    """Only lexists/stat metadata is authorized; never open the owner's env."""
    path = owner_home / '.supermemory/env'
    present = os.path.lexists(path)
    if present:
        path.lstat()
    return bool(present)


def check_fake_knobs(values: Mapping[str, str], *, test_only: bool) -> None:
    if values and (test_only is not True or any(name not in FAKE_KNOBS for name in values)):
        raise PreflightRefused('fake-knob-names')
    if any(not isinstance(value, str) for value in values.values()):
        raise PreflightRefused('fake-knob-values')


def firewall_problems(facts: Mapping[str, Any], *, program: Path) -> list[str]:
    """Validate the declared conservative CIM scopes; this is not a WFP policy proof."""
    problems: list[str] = []
    try:
        if facts.get('program') != str(program):
            return ['firewall-program']
        profiles = facts['profiles']
        if (not isinstance(profiles, list) or len(profiles) != 3
                or {row['name'] for row in profiles} != PROFILES
                or any(row['enabled'] != 'True' for row in profiles)):
            problems.append('firewall-profiles')
        coverage: set[str] = set()
        rules = facts['rules']
        if not isinstance(rules, list):
            return problems + ['firewall-rules-shape']
        unrestricted = {'local_address': ['Any'], 'remote_port': ['Any'], 'interface_alias': ['Any'],
                        'interface_type': 'Any', 'service': 'Any', 'package': 'Any',
                        'authentication': 'NotRequired', 'encryption': 'NotRequired',
                        'local_user': 'Any', 'remote_user': 'Any', 'remote_machine': 'Any',
                        'dynamic_transport': '0', 'owner': '', 'platforms': [],
                        'remote_dynamic_keyword_addresses': [], 'policy_app_id': '', 'package_family_name': ''}
        for rule in rules:
            if (rule['enabled'] == 'True' and rule['direction'] == 'Inbound' and rule['action'] == 'Block'
                    and rule['protocol'] in {'Any', '256'} and rule['local_port'] == ['Any']
                    and rule['remote_address'] == ['Any'] and rule['override_block'] == 'False'
                    and all(rule.get(name) == expected for name, expected in unrestricted.items())
                    and type(rule.get('primary_status')) is int and rule['primary_status'] == 1
                    and isinstance(rule.get('enforcement'), list) and rule['enforcement'] == [1]
                    and all(type(value) is int for value in rule['enforcement'])):
                named = {name.strip() for name in rule['profile'].split(',')}
                coverage.update(PROFILES if named == {'Any'} else named & PROFILES)
        if coverage != PROFILES:
            problems.append('firewall-inbound-block-coverage')
        overrides = facts['override_allow']
        if not isinstance(overrides, list):
            return problems + ['firewall-overrides-shape']
        for row in overrides:
            if (not isinstance(row, dict) or set(row) != {'name', 'enabled', 'direction', 'action'}
                    or any(type(item) is not str or not item for item in row.values())
                    or row['enabled'] not in {'True', 'False'} or row['direction'] not in {'Inbound', 'Outbound'}
                    or row['action'] not in {'Allow', 'Block'}):
                return problems + ['firewall-override-shape']
        if any(row['enabled'] == 'True' and row['direction'] == 'Inbound' and row['action'] == 'Allow'
               for row in overrides):
            problems.append('firewall-override-allow')
    except (KeyError, TypeError, AttributeError):
        problems.append('firewall-shape')
    return problems


def _port(port: int) -> int:
    if type(port) is not int or not 1 <= port <= 65535:
        raise SmRequestRefused('loopback-port')
    return port


class _RequestDeadline:
    """One deadline for the owned connection, including fragmented headers/body."""

    def __init__(self, connection: Any, timeout_s: float) -> None:
        self.connection = connection
        self.socket = None
        self.until = time.monotonic() + timeout_s
        self.done = threading.Event()
        self.expired = threading.Event()
        self.thread = threading.Thread(target=self._watch, name='sm-request-deadline', daemon=True)

    def start(self) -> None:
        self.thread.start()

    def capture_socket(self) -> None:
        # getresponse() can detach connection.sock while response.fp retains it.
        self.socket = getattr(self.connection, 'sock', None)

    def _watch(self) -> None:
        if self.done.wait(max(0.0, self.until - time.monotonic())):
            return
        self.expired.set()
        sock = self.socket or getattr(self.connection, 'sock', None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            self.connection.close()
        except OSError:
            pass

    def check(self) -> None:
        if self.expired.is_set() or time.monotonic() >= self.until:
            raise SmRequestRefused('request-deadline')

    def finish(self) -> None:
        self.done.set()
        self.thread.join(0.25)
        if self.thread.is_alive():
            raise SmRequestRefused('request-deadline-not-drained')
        self.check()


def sm_request(port: int, method: str, path: str, *, key: Any = None, keyed: bool = True,
               foreign_host: bool = False, body: Any = None, timeout_s: float = 5.0,
               connection_factory: Callable = http.client.HTTPConnection) -> dict:
    """Always connect to loopback, with no redirect/proxy/ambient credentials."""
    port = _port(port)
    if keyed is not True and keyed is not False or foreign_host is not True and foreign_host is not False:
        raise SmRequestRefused('request-flags')
    if keyed and key is None:
        raise SmRequestRefused('held-key-required')
    if method not in {'GET', 'POST'} or not isinstance(path, str) or not path.startswith('/') or path.startswith('//'):
        raise SmRequestRefused('request-route')
    if any(char in path for char in ('\r', '\n', '#')):
        raise SmRequestRefused('request-route')
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise SmRequestRefused('request-timeout')
    headers = {'Host': 'example.invalid' if foreign_host else f'127.0.0.1:{port}'}
    if keyed:
        headers.update(key.header())
    payload = None if body is None else json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if payload is not None and len(payload) > 2 * 1024 * 1024:
        raise SmRequestRefused('request-size')
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    conn = connection_factory('127.0.0.1', port, timeout=timeout_s)
    # No lazy reconnect after the watchdog closes the owned socket.
    conn.auto_open = 0
    deadline = _RequestDeadline(conn, timeout_s)
    deadline.start()
    try:
        deadline.check()
        conn.connect()
        deadline.capture_socket()
        deadline.check()
        conn.request(method, path, body=payload, headers=headers)
        deadline.capture_socket()
        deadline.check()
        response = conn.getresponse()
        deadline.check()
        raw = response.read(2 * 1024 * 1024 + 1)
        deadline.check()
        if len(raw) > 2 * 1024 * 1024:
            raise SmRequestRefused('response-size')
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        result = {'status': response.status, 'json': data, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        deadline.check()
        return result
    except Exception:
        deadline.check()
        raise
    finally:
        try:
            deadline.finish()
        finally:
            conn.close()


def _existing_arm_path(contract: Any, launch: Any, run: str) -> Path:
    """An active owned arm; this check never consumes a new run label."""
    if not isinstance(run, str) or LABEL.fullmatch(run) is None:
        raise PreflightRefused('run-label')
    root = Path(contract.runs_root)
    arm = root / '_a8' / run / ARM
    if not launch._within(arm, root) or launch._link_in_chain(root, arm):
        raise PreflightRefused('owned-run-path')
    return arm


def _owned_data_path(contract: Any, launch: Any, run: str, data_dir: Path) -> Path:
    arm = _existing_arm_path(contract, launch, run)
    data = Path(data_dir)
    if not data.is_absolute() or not launch._within(data, arm) or launch._link_in_chain(contract.runs_root, data):
        raise PreflightRefused('owned-data-path')
    return data


def owned_key_search(contract: Any, launch: Any, server: Any, *, run: str, held: Any, data_dir: Path) -> dict:
    """Only fixed owned roots, validated before the scanner can open any file."""
    arm = _existing_arm_path(contract, launch, run)
    data = _owned_data_path(contract, launch, run, data_dir)
    root = Path(contract.runs_root)
    roots = {'run': arm.parent, 'launch': root / '_launch', 'witness': root / '_witness'}
    for path in roots.values():
        if not launch._within(path, root) or launch._link_in_chain(root, path):
            raise PreflightRefused('owned-search-path')
    return server.key_search(roots, held, data_dir=data)


def owned_data_digest(contract: Any, launch: Any, server: Any, *, run: str, data_dir: Path) -> dict:
    """A matching incomplete seal cannot establish durability."""
    data = _owned_data_path(contract, launch, run, data_dir)
    seal = server.data_digest(data)
    if not isinstance(seal, dict) or any(type(name) is not str or type(value) is not str
            or re.fullmatch(r'[0-9a-f]{64}', value) is None for name, value in seal.items()):
        raise PreflightRefused('state-seal-incomplete')
    return seal


def auth_problems(local: int | None, foreign: int | None, wrong: int | None, keyed: int | None) -> list[str]:
    """Product auth facts are independent of loopback socket confinement."""
    problems = []
    if type(local) is not int or not 200 <= local < 300:
        problems.append('auth-local')
    if type(foreign) is not int or not 400 <= foreign < 500:
        problems.append('auth-foreign-host')
    if type(wrong) is not int or wrong != 401:
        problems.append('auth-wrong-key')
    if type(keyed) is not int or not 200 <= keyed < 300:
        problems.append('blocked:auth-control')
    return problems


def tree_problems(rows: Sequence[Mapping[str, Any]], *, pid: int, birth_time: float, binary_sha256: str) -> list[str]:
    if len(rows) != 1:
        return ['server-tree-member-count']
    row = rows[0]
    if (row.get('pid') != pid or row.get('create_time') != birth_time or row.get('sha256') != binary_sha256
            or row.get('error') or not row.get('exe')):
        return ['server-tree-identity']
    return []


def sm_verdict(fields: Mapping[str, Mapping[str, Any]], *, problems: Sequence[str],
               checks: Sequence[tuple[str, Mapping[str, Any]]], catcher_lines: int,
               required_fields: Sequence[str] = FIELDS) -> tuple[str, list[str]]:
    """Missing evidence and boundary failures always outrank functional blockage."""
    reasons = list(problems)
    missing = set(required_fields) - set(fields)
    if missing:
        reasons.append('missing-fields:' + ','.join(sorted(missing)))
    failed = [name for name, field in fields.items() if field.get('ok') is not True]
    reasons.extend(name + ':not-ok' for name in failed)
    boundary_failed = bool(missing or set(failed) & BOUNDARY or problems)
    for name in failed:
        blocked = fields[name].get('blocked')
        if blocked is not None and (type(blocked) is not str or blocked not in BLOCKED_OUTCOMES):
            boundary_failed = True
            reasons.append('invalid-blocked-outcome:' + name)
    if not checks:
        boundary_failed = True
        reasons.append('missing-witness-checks')
    for name, summary in checks:
        if not clean_check(summary):
            boundary_failed = True
            reasons.append('witness:' + name)
    if type(catcher_lines) is not int or catcher_lines != 0:
        boundary_failed = True
        reasons.append('catcher-lines')
    if boundary_failed:
        return 'fail', reasons
    if not failed:
        return 'pass', []
    blocked = [field.get('blocked') for field in fields.values() if field.get('ok') is not True and field.get('blocked')]
    return (blocked[0] if blocked else 'fail'), reasons


def record_clean(value: Any, *, held_keys: Sequence[Any] = (), _path: tuple[str, ...] = (),
                 _schema_key: bool = False) -> Any:
    """Redact before serialization, including values used as dictionary keys."""
    if isinstance(value, str):
        for held in held_keys:
            for encoding, needle in held.needles():
                if encoding == 'utf-8':
                    value = value.replace(needle.decode('ascii'), '<sm-key:' + held.sha12 + '>')
        return value if _schema_key and value in FIELDS else RECORD_TOKEN.sub('<sm-key:redacted>', value)
    if isinstance(value, dict):
        return {record_clean(key, held_keys=held_keys, _schema_key=_path == ('fields',) and key in FIELDS):
                record_clean(item, held_keys=held_keys, _path=(*_path, key) if isinstance(key, str) else (*_path, '?'))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [record_clean(item, held_keys=held_keys, _path=_path) for item in value]
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise PreflightRefused('record-value-type')


def write_sm_probe(dest: Path, record: Mapping[str, Any], *, launch: Any, runs_root: Path,
                   held_keys: Sequence[Any]) -> None:
    """One final redacted record, in the owned run tree, without overwriting."""
    if not launch._within(dest, runs_root) or launch._link_in_chain(runs_root, dest):
        raise PreflightRefused('probe-record-path')
    cleaned = record_clean(dict(record), held_keys=held_keys)
    raw = json.dumps(cleaned, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    for held in held_keys:
        if any(needle in raw for _, needle in held.needles()):
            raise PreflightRefused('probe-record-key-leak')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('xb') as stream:
        stream.write(raw)


SERVER_ENV_NAMES = frozenset({'SUPERMEMORY_PORT', 'SUPERMEMORY_DATA_DIR', 'SUPERMEMORY_EMBEDDING_PROVIDER',
    'SUPERMEMORY_EMBEDDING_MODEL', 'SUPERMEMORY_EMBEDDING_BASE_URL', 'SUPERMEMORY_EMBEDDING_DIMENSIONS',
    'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL'})


class GuardedLaunch:
    """Validate the scheduler's actual prepared env immediately before delegation.

    The C6 scheduler alone uses this local wrapper. Expected declarations are
    constructed from the verified bundle and pinned proxy/embed configuration.
    Never rebuild the env after validation or persist credential values.
    """

    def __init__(self, launch: Any, bundle: Any, env_rec: Mapping, *, run: str,
                 argv: Sequence[str], expected_by_unit: Mapping[str, Mapping[str, str]]) -> None:
        self.launch, self.bundle, self.env_rec = launch, bundle, env_rec
        self.run, self.argv = run, tuple(argv)
        self.expected = {unit: dict(values) for unit, values in expected_by_unit.items()}
        self.observed: list[dict] = []
        self.children: list[Any] = []  # Keep returned owned children available even when post-spawn evidence fails.
        self.creation_callback: Callable | None = None
        if not self.argv or not self.expected or any(set(values) != SERVER_ENV_NAMES
                or any(type(value) is not str or not value for value in values.values())
                for values in self.expected.values()):
            raise PreflightRefused('server-env-contract')
        self._initial_expected = {unit: dict(values) for unit, values in self.expected.items()}

    def declare_restart(self, port: int, *, occupied_ports: frozenset[int]) -> None:
        """Declare one second boot with only its listening port changed."""
        _port(port)
        if (type(occupied_ports) is not frozenset or not 1 <= len(occupied_ports) <= 256
                or any(type(value) is not int or not 1 <= value <= 65535 for value in occupied_ports)):
            raise PreflightRefused('server-restart-port-inventory')
        if (set(self._initial_expected) != {'u1'} or set(self.expected) != {'u1'}
                or self.expected['u1'] != self._initial_expected['u1']):
            raise PreflightRefused('server-restart-declaration-changed')
        original = self._initial_expected['u1']
        if port in occupied_ports or str(port) == original['SUPERMEMORY_PORT']:
            raise PreflightRefused('server-proxy-port-collision')
        restarted = dict(original)
        restarted['SUPERMEMORY_PORT'] = str(port)
        self.expected['u1-boot2'] = restarted

    def __getattr__(self, name: str) -> Any:
        return getattr(self.launch, name)

    def _server_program(self, value: str) -> bool:
        # Reserve the executable even when labels or trailing arguments change.
        expected = self.argv[0]
        normalized = lambda path: os.path.normcase(os.path.realpath(os.path.abspath(path))).casefold()
        if normalized(value) == normalized(expected):
            return True
        try:
            return os.path.samefile(value, expected)
        except OSError:
            return False

    def spawn(self, contract: Any, argv: Sequence[str], **kwargs: Any) -> Any:
        record = kwargs.get('record') or {}
        if not argv or any(type(arg) is not str for arg in argv):
            raise PreflightRefused('server-spawn-argv')
        if record.get('role') != 'server' and record.get('arm') != ARM and not self._server_program(argv[0]):
            return self._spawn_owned(contract, argv, **kwargs)
        unit = record.get('unit')
        if (record.get('role') != 'server' or record.get('stand') != '_a8'
                or record.get('run') != self.run or record.get('arm') != ARM
                or unit not in self.expected or tuple(argv) != self.argv):
            raise PreflightRefused('server-spawn-identity')
        env, expected = kwargs.get('env'), self.expected[unit]
        if not isinstance(env, dict) or any(type(name) is not str or type(value) is not str
                                           for name, value in env.items()):
            raise PreflightRefused('actual-child-env-shape')
        # Windows treats names case-insensitively; duplicate spellings are ambiguous.
        if len({name.upper() for name in env}) != len(env):
            raise PreflightRefused('actual-child-env-case-collision')
        if any(env.get(name) != value for name, value in expected.items()):
            raise PreflightRefused('actual-child-env-declaration')
        forbidden = {name.upper() for name in self.bundle.NOT_DECLARED - set(self.bundle.SPAWN_ENV)}
        extra_reads = {name.upper() for name in self.env_rec['reads']} - SERVER_ENV_NAMES - set(self.bundle.SPAWN_ENV)
        if (any(name.upper() in forbidden | extra_reads | {'PORT'} for name in env)
                or self.bundle.child_env_problems(env, self.env_rec)):
            raise PreflightRefused('actual-child-env-alternate-provider')
        if any(env.get(name) != value for name, value in self.bundle.SPAWN_ENV.items()):
            raise PreflightRefused('actual-child-env-switches')
        try:
            resolved = self.bundle.resolve(env, self.env_rec)
            llm, embed = resolved['llm'], resolved['embedding']
            good = (resolved['vendor_defaults'] == [] and llm['client'] == 'openai-compatible'
                    and llm['path'] == '/chat/completions' and llm['base_url'] == expected['OPENAI_BASE_URL']
                    and llm['fast'] == llm['text'] == expected['OPENAI_MODEL']
                    and embed['provider'] == 'openai-compatible'
                    and embed['model'] == expected['SUPERMEMORY_EMBEDDING_MODEL']
                    and embed['base_url'] == expected['SUPERMEMORY_EMBEDDING_BASE_URL']
                    and embed['dimensions'] == expected['SUPERMEMORY_EMBEDDING_DIMENSIONS'] == '1024')
        except (KeyError, TypeError, AttributeError):
            good = False
        if not good:
            raise PreflightRefused('actual-child-env-route')
        child = self._spawn_owned(contract, argv, **kwargs)
        spawn_id = getattr(child, 'spawn_id', None)
        if type(spawn_id) is not str or not spawn_id:
            raise PreflightRefused('actual-child-unobserved')
        self.observed.append({'unit': unit, 'spawn_id': spawn_id, 'checked_names': sorted(SERVER_ENV_NAMES),
                              'llm_client': 'openai-compatible', 'llm_path': '/chat/completions',
                              'pinned_models': True, 'alternate_provider_absent': True,
                              'switches_match': True, 'unchanged_env_forwarded': True})
        return child

    def _spawn_owned(self, contract: Any, argv: Sequence[str], **kwargs: Any) -> Any:
        from types import SimpleNamespace
        actual = kwargs.get('popen', subprocess.Popen)
        witnesses = kwargs.get('witnesses')
        native = getattr(witnesses, 'native', None)
        retained = []
        class NativeView:
            def __getattr__(view, name):
                return getattr(native, name)
            def register(view, *args, **options):
                if not retained or retained[0][1] is None:
                    raise PreflightRefused('created-process-unretained')
                child, boot = retained[0]
                return CapturingNative(native, child, boot).register(*args, **options)
        class WitnessView:
            native = NativeView()
            def __getattr__(view, name):
                return getattr(witnesses, name)
        def create(*args, **options):
            process = actual(*args, **options)
            child = SimpleNamespace(process=process, spawn_id=None, witness=native)
            self.children.append(child)  # Before any post-Popen operation can fail.
            retained.append((child, None))
            if self.creation_callback is None:
                raise PreflightRefused('created-process-owner-unavailable')
            boot = self.creation_callback(child)
            retained[0] = child, boot
            return process
        kwargs['popen'] = create
        if native is not None:
            kwargs['witnesses'] = WitnessView()
        child = self.launch.spawn(contract, argv, **kwargs)
        if retained:
            pending, boot = retained[0]
            pending.spawn_id = getattr(child, 'spawn_id', None)
            if boot is not None:
                boot.child = child
        elif child is not None:
            # An injected no-Popen collaborator still needs post-return ownership.
            self.children.append(child)
        return child

# The immutable f2 service mounts bu8 at /v3 and nu8 at /v4. Both register GET /openapi.
# E44's POST /list uses g24: nonempty containerTags, optional filters, limit 1..1100.
# J7 and document AZ/Nv8 reject dots and cap container tags at 100 characters.
# Source evidence is retained in offline discoveries023/025/026/027/029; no runtime is inferred.
SM_OPENAPI_PATHS = ('/v3/openapi', '/v4/openapi')
SM_CONTAINER_TAG = re.compile(r'[a-zA-Z0-9_:-]{1,100}')


def sm_container_tag(run: str) -> str:
    """Construct the declared underscore fallback; never truncate or silently rename it."""
    if type(run) is not str or LABEL.fullmatch(run) is None:
        raise PreflightRefused('container-tag-run-label')
    tag = run + '_u1'
    if SM_CONTAINER_TAG.fullmatch(tag) is None:
        raise PreflightRefused('container-tag-limit')
    return tag


def sm_auth_target(run: str) -> tuple[str, str, dict]:
    """Use the pinned read-only memory-list operation with a schema-valid minimal body."""
    return 'POST', '/v4/memories/list', {'containerTags': [sm_container_tag(run)], 'limit': 1}

# Source-backed operation declarations: offline025/026/038/039, all pinned f2.
# Schema observation and processing completion do not admit the product or prove memory success.
SM_API_OPERATIONS = {
    'add_document': ('POST', '/v3/documents', ('content', 'containerTag'), ('id', 'status')),
    'get_document': ('GET', '/v3/documents/{id}', (),
                     ('id', 'status', 'createdAt', 'memories[].id', 'memories[].memory')),
    'search': ('POST', '/v4/search', ('q', 'containerTags[]', 'limit'),
               ('results[].id', 'results[].memory', 'results[].similarity', 'total', 'timing')),
    'list_memories': ('POST', '/v4/memories/list', ('containerTags[]', 'limit', 'page'),
                      ('memoryEntries[].id', 'memoryEntries[].memory', 'memoryEntries[].documentIds[]',
                       'pagination.currentPage', 'pagination.limit', 'pagination.totalPages', 'pagination.totalItems')),
}


class SmApiRefused(RuntimeError):
    """A declared operation, observed schema or processing response cannot be trusted."""


def sm_api_calls(rest: Any) -> dict:
    """Declare exactly the source-backed calls; never infer a replacement API version."""
    return {name: rest.Call(method, path, body=body, response=response)
            for name, (method, path, body, response) in SM_API_OPERATIONS.items()}


def _sm_schema_node(node: Any, doc: dict, budget: list[int]) -> dict:
    """Resolve bounded local reference chains; never fetch or accept opaque leaves."""
    seen = set()
    while True:
        budget[0] -= 1
        if budget[0] < 0 or not isinstance(node, dict):
            raise ValueError('schema-opaque-or-limit')
        if '$ref' not in node:
            return node
        ref = node['$ref']
        if type(ref) is not str or not ref.startswith('#/') or ref in seen or len(seen) >= 32:
            raise ValueError('schema-reference')
        seen.add(ref)
        # Structural $ref siblings need a combined proof. Fail closed instead of dropping them.
        if set(node) - {'$ref', 'description', 'title', 'example', 'examples', 'deprecated'}:
            raise ValueError('schema-reference-siblings')
        node = doc
        for part in ref[2:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            if not isinstance(node, dict) or part not in node:
                raise ValueError('schema-reference')
            node = node[part]


def _sm_schema_types(node: Any, doc: dict, budget: list[int], depth: int = 0) -> set[str]:
    """A bounded type proof only; value constraints remain explicitly unverified."""
    if depth >= 32:
        raise ValueError('schema-depth')
    node = _sm_schema_node(node, doc, budget)
    if any(name in node for name in ('not', 'if', 'then', 'else')):
        raise ValueError('schema-unproved-composition')
    declared = node.get('type')
    types = None
    if declared is not None:
        declared = [declared] if type(declared) is str else declared
        if (not isinstance(declared, list) or not declared
                or any(type(value) is not str or value not in {'string', 'number', 'integer', 'boolean', 'null', 'array', 'object'} for value in declared)):
            raise ValueError('schema-types')
        types = {'number' if value == 'integer' else value for value in declared}
    for keyword in ('allOf', 'anyOf', 'oneOf'):
        if keyword not in node:
            continue
        branches = node[keyword]
        if not isinstance(branches, list) or not branches:
            raise ValueError('schema-composition')
        members = [_sm_schema_types(branch, doc, budget, depth + 1) for branch in branches]
        combined = set.intersection(*members) if keyword == 'allOf' else set.union(*members)
        types = combined if types is None else types & combined
    if not types:
        raise ValueError('schema-unproved-type')
    # OpenAPI3.0's nullable widens the declared type. Treat it conservatively in 3.1 too.
    if 'nullable' in node and type(node['nullable']) is not bool:
        raise ValueError('schema-nullable')
    if node.get('nullable') is True:
        types.add('null')
    return types


def _sm_schema_variants(node: Any, doc: dict, budget: list[int], depth: int = 0) -> list[dict]:
    if depth >= 32:
        raise ValueError('schema-depth')
    node = _sm_schema_node(node, doc, budget)
    result = [node]
    for keyword in ('allOf', 'anyOf', 'oneOf'):
        if keyword in node:
            branches = node[keyword]
            if not isinstance(branches, list) or not branches:
                raise ValueError('schema-composition')
            for branch in branches:
                result.extend(_sm_schema_variants(branch, doc, budget, depth + 1))
    return result


def _sm_used_leaf_type(schema: Any, field: str, doc: dict, *, numeric_string: bool) -> None:
    budget = [512]
    current = [schema]
    steps = field.replace('[]', '.[]').split('.')
    for step in steps:
        next_nodes = []
        for node in current:
            parent_types = _sm_schema_types(node, doc, budget)
            if parent_types != {'array' if step == '[]' else 'object'}:
                raise ValueError('schema-parent-type')
            for variant in _sm_schema_variants(node, doc, budget):
                if step == '[]' and 'items' in variant:
                    next_nodes.append(variant['items'])
                elif step != '[]':
                    props = variant.get('properties', {})
                    if not isinstance(props, dict):
                        raise ValueError('schema-properties')
                    if step in props:
                        next_nodes.append(props[step])
        if not next_nodes:
            raise ValueError('schema-field')
        current = next_nodes
    numeric = steps[-1] in {'limit', 'page', 'currentPage', 'totalPages', 'totalItems', 'similarity', 'total', 'timing'}
    expected = 'number' if numeric else 'string'
    allowed = {expected, 'string'} if numeric and numeric_string else {expected}
    for node in current:
        types = _sm_schema_types(node, doc, budget)
        if expected not in types or not types <= allowed:
            raise ValueError('schema-used-leaf-type')


def _sm_operation_types(call: Any, doc: dict) -> None:
    op = doc['paths'][call.path][call.method.lower()]
    if call.body:
        body = _sm_schema_node(op['requestBody'], doc, [512])
        schema = body['content']['application/json']['schema']
        for field in call.body:
            _sm_used_leaf_type(schema, field, doc, numeric_string=True)
    response = _sm_schema_node(op['responses']['200'], doc, [512])
    schema = response['content']['application/json']['schema']
    for field in call.response:
        _sm_used_leaf_type(schema, field, doc, numeric_string=False)
    if call.params:
        params = doc['paths'][call.path].get('parameters', []) + op.get('parameters', [])
        params = [_sm_schema_node(value, doc, [512]) for value in params]
        for name in call.params:
            matches = [value for value in params if value.get('in') == 'path' and value.get('name') == name]
            if len(matches) != 1 or _sm_schema_types(matches[0]['schema'], doc, [512]) != {'string'}:
                raise ValueError('schema-path-parameter')


def sm_openapi_contract(replies: Sequence[Mapping[str, Any]], rest: Any) -> dict:
    """Check both observations against every used field and its closed leaf type.

    Call conformance is the existing offline REST checker. It never resolves remote
    references or connects. A separate bounded type proof rejects opaque/cyclic used
    leaves. Numeric/string value constraints still need pinned source and runtime
    evidence; this helper does not claim complete JSON Schema value validation.
    """
    if not isinstance(replies, (tuple, list)) or len(replies) != len(SM_OPENAPI_PATHS):
        raise SmApiRefused('blocked:no-openapi')
    calls = sm_api_calls(rest)
    observed = []
    for path, reply in zip(SM_OPENAPI_PATHS, replies):
        if (not isinstance(reply, Mapping) or type(reply.get('status')) is not int
                or reply['status'] != 200 or not isinstance(reply.get('json'), dict)):
            raise SmApiRefused('blocked:no-openapi')
        doc = reply['json']
        if (type(doc.get('openapi')) is not str or not doc['openapi'].startswith('3.')
                or not isinstance(doc.get('paths'), dict) or not doc['paths']):
            raise SmApiRefused('blocked:no-openapi')
        try:
            # Each operation is checked alone so the refusal names the unavailable capability.
            for name, call in calls.items():
                try:
                    # Resolve used schema types first: recursive compositions have a hard budget.
                    _sm_operation_types(call, doc)
                    issues = rest.conformance({name: call}, doc)
                    if not isinstance(issues, list) or issues:
                        raise ValueError('schema-conformance')
                except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
                    raise SmApiRefused('blocked:schema-missing:' + name)
            canonical = json.dumps(doc, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False, allow_nan=False).encode('utf-8')
        except SmApiRefused:
            raise
        except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
            raise SmApiRefused('blocked:no-openapi') from None
        raw_sha = reply.get('sha256')
        if type(raw_sha) is not str or re.fullmatch(r'[0-9a-f]{64}', raw_sha) is None:
            raise SmApiRefused('openapi-observation-hash')
        observed.append({'path': path, 'raw_sha256': raw_sha,
                         'canonical_sha256': hashlib.sha256(canonical).hexdigest()})
    return {'observed': observed, 'operations': tuple(calls), 'conformant': True,
            'value_constraints_verified': False, 'runtime_admission': False}


def sm_api_target(name: str, run: str, *, content: str | None = None,
                  document_id: str | None = None, query: str | None = None, page: int = 1) -> tuple:
    """Construct a fresh schema-valid request; document IDs are one quoted path component."""
    from urllib.parse import quote
    if type(name) is not str or name not in SM_API_OPERATIONS:
        raise SmApiRefused('undeclared-operation')
    tag = sm_container_tag(run)
    method, path, _, _ = SM_API_OPERATIONS[name]
    if name == 'get_document':
        # 255 is the probe's input ceiling, not a claimed vendor GET-id limit.
        if (type(document_id) is not str or not 0 < len(document_id) <= 255
                or document_id in {'.', '..'} or any(ord(c) < 32 or ord(c) == 127 for c in document_id)):
            raise SmApiRefused('document-id')
        return method, path.replace('{id}', quote(document_id, safe='')), None
    if name == 'add_document':
        if type(content) is not str or not content.strip():
            raise SmApiRefused('document-content')
        return method, path, {'content': content, 'containerTag': tag}
    if name == 'search':
        if type(query) is not str or not query.strip():
            raise SmApiRefused('search-query')
        return method, path, {'q': query.strip(), 'containerTags': [tag], 'limit': 10}
    if type(page) is not int or not 1 <= page <= 1100:
        raise SmApiRefused('memory-page')
    return method, path, {'containerTags': [tag], 'limit': 10, 'page': page}


def sm_document_response(reply: Mapping[str, Any], *, document_id: str | None = None) -> dict:
    """Validate document identity/status; this alone never proves extracted memory exists."""
    if (not isinstance(reply, Mapping) or type(reply.get('status')) is not int or reply['status'] != 200
            or not isinstance(reply.get('json'), dict)):
        raise SmApiRefused('document-response')
    doc = reply['json']
    if (type(doc.get('id')) is not str or not doc['id'] or type(doc.get('status')) is not str
            or not doc['status'] or document_id is not None and doc['id'] != document_id):
        raise SmApiRefused('document-response')
    return doc


def sm_memory_page(reply: Mapping[str, Any], *, document_id: str, page: int) -> dict:
    """Read memory entries, not the document-list 'memories' array; preserve empty pages."""
    if (not isinstance(reply, Mapping) or type(reply.get('status')) is not int or reply['status'] != 200
            or not isinstance(reply.get('json'), dict) or type(page) is not int or page < 1
            or type(document_id) is not str or not document_id):
        raise SmApiRefused('memory-response')
    data = reply['json']
    entries, pagination = data.get('memoryEntries'), data.get('pagination')
    if not isinstance(entries, list) or not isinstance(pagination, dict):
        raise SmApiRefused('memory-response')
    counts = {name: pagination.get(name) for name in ('currentPage', 'limit', 'totalItems', 'totalPages')}
    if (any(type(value) is not int for value in counts.values()) or counts['currentPage'] != page
            or not 1 <= counts['limit'] <= 1100 or counts['totalItems'] < 0 or counts['totalPages'] < 0
            or len(entries) > counts['limit'] or len(entries) > counts['totalItems']
            or counts['totalItems'] > 0 and not 1 <= page <= counts['totalPages']
            or counts['totalItems'] == 0 and (counts['totalPages'] > 1 or page != 1 or entries)):
        raise SmApiRefused('memory-pagination')
    selected = []
    identities = set()
    for entry in entries:
        if (not isinstance(entry, dict) or type(entry.get('id')) is not str or not entry['id']
                or entry['id'] in identities or type(entry.get('memory')) is not str
                or not isinstance(entry.get('documentIds'), list)
                or any(type(value) is not str or not value for value in entry['documentIds'])):
            raise SmApiRefused('memory-entry')
        identities.add(entry['id'])
        if document_id in entry['documentIds'] and entry['memory'].strip():
            selected.append(entry)
    return {'entries': entries, 'selected': selected, 'pagination': pagination,
            'memory_found': bool(selected)}


def poll_sm_document(port: int, run: str, document_id: str, *, key: Any, request: Callable,
                     admit: Callable, ceiling_s: float = 120.0, interval_s: float = 0.1,
                     clock: Callable = time.monotonic, sleep: Callable = time.sleep) -> dict:
    """Poll document completion under one deadline and fresh admission before every send."""
    if (type(ceiling_s) not in {int, float} or not math.isfinite(ceiling_s) or not 0 < ceiling_s <= 300
            or type(interval_s) not in {int, float} or not math.isfinite(interval_s) or not 0.01 <= interval_s <= 1):
        raise SmApiRefused('document-poll-bounds')
    method, path, body = sm_api_target('get_document', run, document_id=document_id)
    start = clock()
    if type(start) not in {int, float} or not math.isfinite(start):
        raise SmApiRefused('document-poll-clock')
    until, previous, polls = start + ceiling_s, start, 0

    def remaining():
        nonlocal previous
        now = clock()
        if type(now) not in {int, float} or not math.isfinite(now) or now < previous:
            raise SmApiRefused('document-poll-clock')
        previous = now
        if now >= until:
            raise SmApiRefused('document-poll-deadline')
        return until - now

    for _ in range(math.ceil(ceiling_s / interval_s) + 1):
        remaining()
        if admit() is not True:
            raise SmApiRefused('incident-gate')
        allowance = min(5.0, remaining())
        reply = request(port, method, path, body=body, key=key, timeout_s=allowance)
        polls += 1
        remaining()  # A late 'done' response cannot turn the deadline into a pass.
        doc = sm_document_response(reply, document_id=document_id)
        if doc['status'] == 'done':
            return {'document': doc, 'polls': polls, 'processing_complete': True, 'memory_success_proven': False}
        if doc['status'] not in {'queued', 'processing'}:
            raise SmApiRefused('document-processing-status')
        sleep(min(interval_s, remaining()))
    raise SmApiRefused('document-poll-deadline')

class _SmReadDeadline:
    """One monotonic allowance for a bounded sequence of keyed read operations."""

    def __init__(self, ceiling_s: float, clock: Callable) -> None:
        if type(ceiling_s) not in {int, float} or not math.isfinite(ceiling_s) or not 0 < ceiling_s <= 300:
            raise SmApiRefused('state-read-bounds')
        self.clock = clock
        self.previous = clock()
        if type(self.previous) not in {int, float} or not math.isfinite(self.previous):
            raise SmApiRefused('state-read-clock')
        self.until = self.previous + ceiling_s

    def remaining(self) -> float:
        now = self.clock()
        if type(now) not in {int, float} or not math.isfinite(now) or now < self.previous:
            raise SmApiRefused('state-read-clock')
        self.previous = now
        if now >= self.until:
            raise SmApiRefused('state-read-deadline')
        return self.until - now

    def send(self, port: int, target: tuple, *, key: Any, request: Callable, admit: Callable) -> dict:
        self.remaining()
        if admit() is not True:
            raise SmApiRefused('incident-gate')
        method, path, body = target
        reply = request(port, method, path, body=body, key=key, timeout_s=min(5.0, self.remaining()))
        self.remaining()
        return reply


def collect_sm_memories(port: int, run: str, document_id: str, *, key: Any, request: Callable,
                        admit: Callable, ceiling_s: float = 60.0, max_pages: int = 100,
                        clock: Callable = time.monotonic) -> dict:
    """Collect all pages of one stable declared count; never claim an atomic snapshot.

    The 100-page maximum is a declared A8 probe resource ceiling, not a vendor limit
    or a benchmark configuration. Exceeding it refuses rather than truncating.
    """
    port = _port(port)
    sm_api_target('get_document', run, document_id=document_id)  # Validate before any send.
    if key is None:
        raise SmApiRefused('held-key-required')
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise SmApiRefused('memory-page-ceiling')
    deadline = _SmReadDeadline(ceiling_s, clock)
    entries, selected, pages, identities = [], [], [], set()
    expected = None
    for page in range(1, max_pages + 1):
        target = sm_api_target('list_memories', run, page=page)
        reply = deadline.send(port, target, key=key, request=request, admit=admit)
        parsed = sm_memory_page(reply, document_id=document_id, page=page)
        pagination = parsed['pagination']
        counts = (pagination['limit'], pagination['totalItems'], pagination['totalPages'])
        if counts[0] != target[2]['limit']:
            raise SmApiRefused('memory-pagination-limit')
        if expected is None:
            expected = counts
            if counts[2] > max_pages:
                raise SmApiRefused('memory-page-ceiling')
        elif counts != expected:
            raise SmApiRefused('memory-pagination-changed')
        for entry in parsed['entries']:
            if entry['id'] in identities:
                raise SmApiRefused('memory-duplicate-across-pages')
            identities.add(entry['id'])
        entries.extend(parsed['entries'])
        selected.extend(parsed['selected'])
        pages.append(dict(pagination))
        if page >= max(1, expected[2]):
            if len(entries) != expected[1]:
                raise SmApiRefused('memory-pagination-incomplete')
            deadline.remaining()  # Parsing and aggregation consume the same allowance.
            return {'complete': True, 'pagination_complete': True, 'atomic_snapshot_proven': False,
                    'entries': entries, 'selected': selected, 'pages': pages, 'memory_found': bool(selected)}
    raise SmApiRefused('memory-pagination-incomplete')


def sm_search_response(reply: Mapping[str, Any], *, memories: Sequence[Mapping[str, Any]]) -> dict:
    """Validate memory-mode results against the complete collected identity/content map."""
    if (not isinstance(reply, Mapping) or type(reply.get('status')) is not int or reply['status'] != 200
            or not isinstance(reply.get('json'), dict) or not isinstance(memories, (tuple, list))):
        raise SmApiRefused('blocked:search-shape')
    known = {}
    for entry in memories:
        if (not isinstance(entry, Mapping) or type(entry.get('id')) is not str or not entry['id']
                or type(entry.get('memory')) is not str or entry['id'] in known):
            raise SmApiRefused('blocked:search-shape')
        known[entry['id']] = entry['memory']
    data = reply['json']
    results, total, timing = data.get('results'), data.get('total'), data.get('timing')
    if (not isinstance(results, list) or type(total) not in {int, float} or not math.isfinite(total)
            or total != len(results) or type(timing) not in {int, float} or not math.isfinite(timing) or timing < 0):
        raise SmApiRefused('blocked:search-shape')
    identities = set()
    selected = []
    for item in results:
        if (not isinstance(item, dict) or type(item.get('id')) is not str or item['id'] not in known
                or item['id'] in identities or item.get('memory') != known[item['id']]
                or type(item.get('similarity')) not in {int, float} or not math.isfinite(item['similarity'])
                or not 0 <= item['similarity'] <= 1):
            raise SmApiRefused('blocked:search-shape')
        identities.add(item['id'])
        selected.append({'id': item['id'], 'memory': item['memory'], 'similarity': item['similarity']})
    return {'results': selected, 'total': len(selected), 'found': any(item['memory'].strip() for item in selected)}


def sm_created_at(document: Mapping[str, Any]) -> dict:
    """Keep the observed value and normalize an explicitly zoned ISO timestamp to UTC."""
    from datetime import datetime, timezone
    raw = document.get('createdAt') if isinstance(document, Mapping) else None
    match = re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)', raw) if type(raw) is str and len(raw) <= 64 else None
    if match is None:
        raise SmApiRefused('document-created-at')
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        # Zone shifts are whole minutes; preserve the original fractional precision.
        utc = value.astimezone(timezone.utc).isoformat(timespec='seconds').removesuffix('+00:00') + (match[1] or '') + '+00:00'
    except (ValueError, OverflowError):
        raise SmApiRefused('document-created-at') from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise SmApiRefused('document-created-at')
    return {'raw': raw, 'utc': utc, 'source': 'get_document.createdAt'}


def sm_state_seal(*, documents: Sequence[Mapping[str, Any]], memories: Sequence[Mapping[str, Any]],
                  search: Mapping[str, Any]) -> dict[str, str]:
    """Digest exactly three semantic parts; caller must separately prove writers quiescent."""
    if (not isinstance(documents, (tuple, list)) or not documents or not isinstance(memories, (tuple, list))
            or not memories or not isinstance(search, Mapping) or not isinstance(search.get('results'), list)):
        raise SmApiRefused('state-seal-input')
    doc_ids, memory_ids = [], []
    for document in documents:
        if (not isinstance(document, dict) or type(document.get('id')) is not str or not document['id']
                or document.get('status') != 'done' or document['id'] in doc_ids):
            raise SmApiRefused('state-seal-input')
        sm_created_at(document)
        doc_ids.append(document['id'])
    for memory in memories:
        if (not isinstance(memory, dict) or type(memory.get('id')) is not str or not memory['id']
                or memory['id'] in memory_ids or type(memory.get('memory')) is not str
                or not isinstance(memory.get('documentIds'), list)
                or any(type(value) is not str or not value for value in memory['documentIds'])):
            raise SmApiRefused('state-seal-input')
        memory_ids.append(memory['id'])
    # Revalidate the normalized search part; measured timing is not persistent state.
    result = sm_search_response({'status': 200, 'json': dict(search, timing=0)}, memories=memories)
    parts = {'documents': sorted(documents, key=lambda item: item['id']),
             'memories': sorted(memories, key=lambda item: item['id']),
             'search': {'results': result['results'], 'total': result['total']}}
    seal = {}
    try:
        for name, value in parts.items():
            raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(raw) > 8 << 20:
                raise SmApiRefused('state-seal-size')
            seal[name] = hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, RecursionError):
        raise SmApiRefused('state-seal-json') from None
    return seal

SM_LEG_HEADER_ALLOW = frozenset({'accept', 'accept-encoding', 'connection', 'content-length',
                                 'content-type', 'host', 'user-agent'})
SM_DIAGNOSTIC_DOCUMENT = 'Rina keeps the cobalt telescope in locker 42 at the Seoul observatory.'
SM_DIAGNOSTIC_QUERY = 'Where does Rina keep the cobalt telescope?'


class OwnedProxyStage:
    """Retain a bounded stage reset before the first potentially effective mutation."""

    def __init__(self, port: int, key: Any, resources: list, *, request: Callable = sm_request) -> None:
        self.port, self.key, self.request = _port(port), key, request
        self.touched, self.closed, self.result = False, False, None
        resources.append(('proxy-stage', self.close,
                          lambda row: isinstance(row, dict) and row.get('complete') is True))

    def prepare(self, *, timeout_s: float) -> bool:
        if self.closed or type(timeout_s) not in {int, float} or not math.isfinite(timeout_s) or not 0 < timeout_s <= 2:
            raise SmApiRefused('proxy-stage-bounds')
        self.touched = True  # A timeout can arrive after the proxy has already changed its stage.
        reply = self.request(self.port, 'POST', '/stage', body={'block': '_a8/p0p2', 'stage': 'write'},
                             key=self.key, timeout_s=timeout_s)
        return (isinstance(reply, dict) and type(reply.get('status')) is int and reply['status'] == 200
                and isinstance(reply.get('json'), dict) and reply['json'].get('ok') is True)

    def close(self) -> dict:
        if self.closed:
            return self.result
        problems = []
        if self.touched:
            try:
                reply = self.request(self.port, 'POST', '/stage', body={'block': None, 'stage': None},
                                     key=self.key, timeout_s=2.0)
                if (not isinstance(reply, dict) or type(reply.get('status')) is not int or reply['status'] != 200
                        or not isinstance(reply.get('json'), dict) or reply['json'].get('ok') is not True):
                    problems.append('proxy-stage-reset')
            except Exception as exc:
                problems.append('proxy-stage-reset:' + type(exc).__name__)
        self.closed = True
        self.result = {'complete': not problems, 'problems': problems, 'reset_requested': self.touched}
        return self.result


def _sm_route_rows(rows: Any, run: str, unit: str) -> list[dict]:
    """Select exactly one declared product unit; unknown product attribution refuses."""
    sm_container_tag(run)
    if unit != 'u1' or not isinstance(rows, (tuple, list)) or len(rows) > 2000:
        raise SmApiRefused('route-input')
    selected = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get('arm')) is not str:
            raise SmApiRefused('route-record')
        if row['arm'] != ARM:
            continue
        if row.get('unit') != run + '.' + unit:
            raise SmApiRefused('route-attribution')
        selected.append(row)
    return selected


def sm_llm_route_fields(calls: Any, upstream_requests: Any, *, run: str, model: str, unit: str = 'u1') -> dict:
    """Use actual existing proxy fields, not scheduler traffic or source-only parameters."""
    if type(model) is not str or not model:
        raise SmApiRefused('llm-model-input')
    selected = _sm_route_rows(calls, run, unit)
    if not selected:
        raise SmApiRefused('blocked:no-llm-call')
    if not isinstance(upstream_requests, (tuple, list)) or len(upstream_requests) > 2000:
        raise SmApiRefused('llm-body-observation')
    bodies = {}
    for observed in upstream_requests:
        if (not isinstance(observed, dict) or type(observed.get('path')) is not str
                or observed['path'] not in {'/chat/completions', '/v1/chat/completions'}
                or not isinstance(observed.get('body'), dict)):
            raise SmApiRefused('llm-body-observation')
        try:
            raw = json.dumps(observed['body'], sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, RecursionError):
            raise SmApiRefused('llm-body-observation') from None
        digest = hashlib.sha256(raw + b'\0' + ARM.encode('utf-8')).hexdigest()
        bodies.setdefault(digest, []).append(observed['body'])
    params = []
    agent_calls = 0
    for row in selected:
        if row.get('tool_violation') is True or row.get('refused') == 'tool_violation':
            raise SmApiRefused('blocked:tools-refused')
        if (row.get('port_role') != 'write' or row.get('endpoint') != 'v1' or row.get('method') != 'POST'
                or row.get('stage') != 'write' or row.get('requested_model') != model
                or row.get('response_model') != model or type(row.get('status')) is not int or row['status'] != 200
                or row.get('complete') is not True or row.get('client_abandoned') is not False
                or row.get('tool_violation') is not False or row.get('refused', 'missing') is not None
                or row.get('upstream_error') is not None):
            raise SmApiRefused('llm-route-record')
        offered, called = row.get('tools_offered'), row.get('tools_called')
        digest = row.get('request_key')
        if type(digest) is not str or not re.fullmatch(r'[0-9a-f]{64}', digest) or not bodies.get(digest):
            raise SmApiRefused('llm-body-observation')
        body = bodies[digest].pop()
        raw_tools = body.get('tools', [])
        if (not isinstance(raw_tools, list) or any(not isinstance(tool, dict) or tool.get('type') != 'function'
                or not isinstance(tool.get('function'), dict) or type(tool['function'].get('name')) is not str for tool in raw_tools)
                or [tool['function']['name'] for tool in raw_tools] != offered):
            raise SmApiRefused('llm-body-tools')
        if (row.get('parse_ok') is not True or row.get('json_ok') is not None and row.get('json_ok') is not True
                or row.get('content_empty') is not False):
            raise SmApiRefused('llm-response-unusable')
        if (not isinstance(offered, list) or any(type(value) is not str for value in offered)
                or offered and (len(offered) != len(TOOLS) or set(offered) != set(TOOLS))
                or not isinstance(called, list) or any(type(value) is not str or value not in TOOLS for value in called)):
            raise SmApiRefused('llm-tools-baseline')
        if ('temperature' not in row or 'max_tokens' not in row
                or row['temperature'] is not None and (type(row['temperature']) not in {int, float} or not math.isfinite(row['temperature']))
                or row['max_tokens'] is not None and (type(row['max_tokens']) is not int or row['max_tokens'] <= 0)):
            raise SmApiRefused('llm-parameters')
        if offered:
            if 'temperature' in body or row['temperature'] is not None or row['max_tokens'] != 12000:
                raise SmApiRefused('llm-parameters')
            classification = 'observed-exact-seven-tool-set'
            agent_calls += 1
        else:
            if called:
                raise SmApiRefused('llm-tools-baseline')
            classification = 'observed-tool-free-unclassified'
        if row['temperature'] != body.get('temperature') or row['max_tokens'] != body.get('max_tokens'):
            raise SmApiRefused('llm-body-parameters')
        params.append({'model': model, 'max_tokens': row['max_tokens'], 'temperature': row['temperature'],
                       'temperature_present': 'temperature' in body,
                       'tools_offered': sorted(offered), 'tools_called': list(called),
                       'classification': classification,
                       'thinking_sent': row.get('thinking_sent'), 'thinking_injected': row.get('thinking_injected')})
    if agent_calls == 0:
        raise SmApiRefused('blocked:no-llm-call')
    return {'source': 'calls.jsonl', 'unit': run + '.' + unit, 'calls': len(selected), 'parameters': params,
            'memory_agent_candidates': agent_calls, 'trace_identity_proven': False,
            'source_schema_pins': dict(SCHEMAS), 'runtime_schema_digests_observed': False}


def sm_embed_route_fields(ollama: Any, upstream_requests: Any, *, run: str, embed_model: str,
                          unit: str = 'u1') -> dict:
    """Pair positive attributed leg calls with independently observed fake-upstream headers."""
    if type(embed_model) is not str or not embed_model:
        raise SmApiRefused('embed-model-input')
    selected = _sm_route_rows(ollama, run, unit)
    if not selected:
        raise SmApiRefused('blocked:no-embed-call')
    listing_paths = {'/api/tags', '/api/show', '/api/version', '/api/ps', '/v1/models'}
    leg_counts, embedding_calls = {}, 0
    for row in selected:
        path = row.get('path')
        listing = type(path) is str and path in listing_paths
        if (path != '/v1/embeddings' and not listing or row.get('is_embed') is not (not listing)
                or row.get('is_llm') is not False or row.get('fallback_local') is not False
                or row.get('stage') not in ({None, 'write'} if listing else {'write'})
                or row.get('client_auth') not in ({'none', 'bearer'} if listing else {'bearer'})
                or type(row.get('status')) is not int or row['status'] != 200
                or row.get('error', 'missing') is not None):
            raise SmApiRefused('embed-route-record')
        leg_counts[path] = leg_counts.get(path, 0) + 1
        embedding_calls += not listing
    if not embedding_calls:
        raise SmApiRefused('blocked:no-embed-call')
    if not isinstance(upstream_requests, (tuple, list)) or not 0 < len(upstream_requests) <= 2000:
        raise SmApiRefused('embed-upstream-record')
    embeds, names_seen, upstream_counts = 0, set(), {}
    for row in upstream_requests:
        if not isinstance(row, dict):
            raise SmApiRefused('embed-upstream-record')
        path = row.get('path')
        listing = type(path) is str and path in listing_paths
        method = 'POST' if type(path) is str and path in {'/api/show', '/v1/embeddings'} else 'GET'
        if path != '/v1/embeddings' and not listing or row.get('method') != method:
            raise SmApiRefused('embed-upstream-record')
        names = row.get('header_names')
        if (not isinstance(names, list) or any(type(name) is not str or not name or name != name.lower() for name in names)
                or len(set(names)) != len(names) or not {'host', 'content-length'} <= set(names)
                or not set(names) <= SM_LEG_HEADER_ALLOW):
            raise SmApiRefused('embedding-header-boundary')
        if not isinstance(row.get('body'), dict) or (not listing or path == '/api/show') and row['body'].get('model') != embed_model:
            raise SmApiRefused('embed-upstream-model')
        if not listing:
            inputs = row['body'].get('input')
            inputs = [inputs] if type(inputs) is str else inputs
            if not isinstance(inputs, list) or not inputs or any(type(value) is not str or not value.strip() for value in inputs):
                raise SmApiRefused('embed-upstream-input')
            embeds += 1
        upstream_counts[path] = upstream_counts.get(path, 0) + 1
        names_seen.update(names)
    if upstream_counts != leg_counts:
        raise SmApiRefused('embed-witness-count')
    return {'source': 'ollama.jsonl+fake-ollama-requests', 'unit': run + '.' + unit,
            'calls': embedding_calls, 'upstream_calls': embeds, 'listing_calls': len(selected) - embedding_calls,
            'model': embed_model,
            'header_names': sorted(names_seen), 'auth_forwarded': False, 'fallback_local': False}


def run_sm_p3(port: int, run: str, *, key: Any, rest: Any, request: Callable,
              admit: Callable, prepare_routes: Callable, observe_routes: Callable, model: str, embed_model: str,
              ceiling_s: float = 180.0, clock: Callable = time.monotonic,
              sleep: Callable = time.sleep) -> dict:
    """One keyed diagnostic sequence; later quiescence/restart and full admission remain separate."""
    port = _port(port)
    tag = sm_container_tag(run)
    if key is None:
        raise SmApiRefused('held-key-required')
    if (not all(callable(value) for value in (request, admit, prepare_routes, observe_routes, clock, sleep))
            or not callable(getattr(rest, 'conformance', None))
            or type(model) is not str or not model or type(embed_model) is not str or not embed_model):
        raise SmApiRefused('functional-collaborator')
    deadline = _SmReadDeadline(ceiling_s, clock)

    def send(p, method, path, *, body=None, key=None, timeout_s=5.0):
        if p != port or key is not held or type(timeout_s) not in {int, float} or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise SmApiRefused('keyed-sequence-target')
        def bounded(p, m, path, **options):
            options['timeout_s'] = min(options['timeout_s'], timeout_s)
            return request(p, m, path, **options)
        return deadline.send(port, (method, path, body), key=held, request=bounded, admit=admit)

    held = key
    deadline.remaining()
    if admit() is not True:
        raise SmApiRefused('incident-gate')
    if prepare_routes(timeout_s=min(2.0, deadline.remaining())) is not True:
        raise SmApiRefused('proxy-write-stage')
    deadline.remaining()
    replies = [send(port, 'GET', path, key=held) for path in SM_OPENAPI_PATHS]
    schema = sm_openapi_contract(replies, rest)
    deadline.remaining()
    dot_reply = send(port, 'POST', '/v4/memories/list', body={'containerTags': [run + '.u1'], 'limit': 1}, key=held)
    if not isinstance(dot_reply, Mapping) or type(dot_reply.get('status')) is not int or dot_reply['status'] != 400:
        raise SmApiRefused('container-tag-control')
    method, path, body = sm_api_target('add_document', run, content=SM_DIAGNOSTIC_DOCUMENT)
    added = sm_document_response(send(port, method, path, body=body, key=held))
    doc_id = added['id']
    completed = poll_sm_document(port, run, doc_id, key=held, request=send, admit=lambda: True,
        ceiling_s=min(120.0, deadline.remaining()), clock=clock, sleep=sleep)
    document = completed['document']
    collection = collect_sm_memories(port, run, doc_id, key=held, request=send, admit=lambda: True,
        ceiling_s=min(60.0, deadline.remaining()), clock=clock)
    if collection['memory_found'] is not True:
        # Preserve the named tools-refused outcome only when the proxy actually saw it.
        witness = observe_routes(timeout_s=min(2.0, deadline.remaining()))
        deadline.remaining()
        if not isinstance(witness, dict) or witness.get('complete') is not True or witness.get('problems') != []:
            raise SmApiRefused('route-observation-incomplete')
        sm_llm_route_fields(witness['calls'], witness.get('chat_requests'), run=run, model=model)
        raise SmApiRefused('memory-agent-empty')
    method, path, body = sm_api_target('search', run, query=SM_DIAGNOSTIC_QUERY)
    search = sm_search_response(send(port, method, path, body=body, key=held), memories=collection['entries'])
    selected_ids = {entry['id'] for entry in collection['selected']}
    if not search['found'] or not any(item['id'] in selected_ids for item in search['results']):
        raise SmApiRefused('blocked:search-shape')
    created_at = sm_created_at(document)
    witness = observe_routes(timeout_s=min(2.0, deadline.remaining()))
    deadline.remaining()
    if (not isinstance(witness, dict) or witness.get('complete') is not True
            or witness.get('problems') != []):
        raise SmApiRefused('route-observation-incomplete')
    llm = sm_llm_route_fields(witness.get('calls'), witness.get('chat_requests'), run=run, model=model)
    embeds = sm_embed_route_fields(witness.get('ollama'), witness.get('upstream_requests'),
                                    run=run, embed_model=embed_model)
    seal = sm_state_seal(documents=[document], memories=collection['entries'], search=search)
    deadline.remaining()  # All final parsing, route checks and hashing share this allowance.
    return {'phase': 'P3', 'document_id': doc_id, 'schema': schema,
            'container_tag': {'dot_status': dot_reply['status'], 'chosen': tag, 'fallback_observed': True},
            'processing': {'polls': completed['polls'], 'status': document['status'],
                           'memories': len(collection['selected'])},
            'llm': llm, 'embeddings': embeds, 'created_at': created_at, 'search': search,
            'state': {'documents': [document], 'memories': collection['entries'], 'search': search},
            'seal': seal, 'atomic_snapshot_proven': False, 'writers_quiescent_proven': False,
            'runtime_admission': False}


SM_CHAT_SCRIPT = (
    {'content': '{}'},
    {'content': '', 'tool_calls': [{'id': 'sm-diagnostic-create', 'type': 'function', 'function': {
        'name': 'CreateMemory', 'arguments': json.dumps({'memory': SM_DIAGNOSTIC_DOCUMENT, 'temporalContext': {}})}}]},
    {'content': 'Diagnostic memory recorded.'},
)
SM_CHAT_POLICY = 'sm-p3-observed-tool-set-v1'


def sm_chat_script_entry(script: Any, index: int, body: Mapping[str, Any]) -> dict:
    """Declared body-based replies keep interleaved scheduler/gate calls independent."""
    offered = body.get('tools')
    if not isinstance(offered, list) or len(offered) != len(TOOLS):
        return dict(script[0])
    names = []
    for tool in offered:
        if (not isinstance(tool, dict) or tool.get('type') != 'function'
                or not isinstance(tool.get('function'), dict) or type(tool['function'].get('name')) is not str):
            return dict(script[0])
        names.append(tool['function']['name'])
    if set(names) != set(TOOLS):
        return dict(script[0])
    messages = body.get('messages')
    if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
        return dict(script[0])
    completed = any(message.get('role') == 'tool' and message.get('tool_call_id') == 'sm-diagnostic-create'
                    for message in messages)
    return json.loads(json.dumps(script[2 if completed else 1]))


def read_sm_routes(contract: Any, launch: Any, *, run: str, proxy_dir: Path, upstream: Any, chat_upstream: Any,
                   control: Callable, timeout_s: float = 2.0,
                   clock: Callable = time.monotonic, sleep: Callable = time.sleep) -> dict:
    """Bounded owned logs between idle/stable proxy counter observations; no atomic-state claim."""
    root = _existing_arm_path(contract, launch, run)
    proxy_dir = Path(proxy_dir)
    expected = root / '_proxy/run'
    if (not proxy_dir.is_absolute() or proxy_dir != expected
            or not launch._within(proxy_dir, root) or launch._link_in_chain(contract.runs_root, proxy_dir)):
        raise SmApiRefused('route-log-path')
    if type(timeout_s) not in {int, float} or not math.isfinite(timeout_s) or not 0 < timeout_s <= 2:
        raise SmApiRefused('route-observation-bounds')
    deadline = _SmReadDeadline(timeout_s, clock)
    previous_records = -1

    def counters():
        nonlocal previous_records
        reply = control(timeout_s=deadline.remaining())
        deadline.remaining()
        if (not isinstance(reply, dict) or type(reply.get('status')) is not int or reply['status'] != 200
                or not isinstance(reply.get('json'), dict) or not isinstance(reply['json'].get(ARM), dict)):
            raise SmApiRefused('route-counter-observation')
        value = reply['json'][ARM]
        if any(type(value.get(name)) is not int or value[name] < 0 for name in ('forward_open', 'records')):
            raise SmApiRefused('route-counter-observation')
        if value['records'] < previous_records:
            raise SmApiRefused('route-counter-regressed')
        previous_records = value['records']
        return {'forward_open': value['forward_open'], 'records': value['records']}

    def read(name):
        path = proxy_dir / (name + '.jsonl')
        if not launch._within(path, proxy_dir) or launch._link_in_chain(contract.runs_root, path):
            raise SmApiRefused('route-log-path')
        if not os.path.lexists(path):
            return []
        try:
            with path.open('rb') as stream:
                raw = stream.read((8 << 20) + 1)
            if len(raw) > 8 << 20 or raw and not raw.endswith(b'\n'):
                raise ValueError('size-or-unfinished-line')
            lines = raw.splitlines()
            if len(lines) > 2000:
                raise ValueError('record-ceiling')
            rows = [json.loads(line) for line in lines]
            if any(not isinstance(row, dict) for row in rows):
                raise ValueError('record-shape')
            return rows
        except (OSError, ValueError, RecursionError):
            raise SmApiRefused('route-log-incomplete') from None

    while True:
        before = counters()
        if before['forward_open']:
            sleep(min(0.01, deadline.remaining()))
            continue
        calls, ollama = read('calls'), read('ollama')
        requests = bounded_sm_upstream_snapshot(upstream, deadline)
        chat_requests = bounded_sm_upstream_snapshot(chat_upstream, deadline)
        after = counters()
        if after['forward_open'] or after['records'] != before['records']:
            sleep(min(0.01, deadline.remaining()))
            continue
        if sum(row.get('arm') == ARM for row in calls) != after['records']:
            raise SmApiRefused('route-record-count')
        deadline.remaining()
        return {'complete': True, 'problems': [], 'calls': calls, 'ollama': ollama,
                'upstream_requests': requests, 'chat_requests': chat_requests, 'proxy_idle_observed': True,
                'writers_quiescent_proven': False, 'atomic_snapshot_proven': False}


def bounded_sm_upstream_snapshot(upstream: Any, deadline: Any) -> list:
    """Bound lock waiting and a conservative encoding size before allocating a JSON copy."""
    acquired = upstream._lock.acquire(timeout=deadline.remaining())
    if acquired is not True:
        raise SmApiRefused('route-upstream-lock')
    try:
        deadline.remaining()
        if upstream.closing or upstream.errors or upstream.traps or upstream.unknown:
            raise SmApiRefused('route-upstream-incomplete')
        if not isinstance(upstream.requests, list) or len(upstream.requests) > 2000:
            raise SmApiRefused('route-upstream-ceiling')
        budget, nodes, active = [8 << 20], [20000], set()
        def size(value, depth=0):
            nodes[0] -= 1
            if depth > 32 or nodes[0] < 0 or budget[0] < 0:
                raise SmApiRefused('route-upstream-ceiling')
            if nodes[0] % 128 == 0:
                deadline.remaining()
            if type(value) is str:
                budget[0] -= 6 * len(value) + 2
            elif value is None or type(value) is bool:
                budget[0] -= 5
            elif type(value) is int:
                budget[0] -= 2 + value.bit_length()  # Conservative decimal-byte upper bound.
            elif type(value) is float and math.isfinite(value):
                budget[0] -= 32
            elif type(value) in {dict, list}:
                if id(value) in active or len(value) > 2000:
                    raise SmApiRefused('route-upstream-ceiling')
                active.add(id(value))
                budget[0] -= 2 + 2 * len(value)
                try:
                    if type(value) is dict:
                        for key, item in value.items():
                            if type(key) is not str:
                                raise SmApiRefused('route-upstream-ceiling')
                            size(key, depth + 1)
                            size(item, depth + 1)
                    else:
                        for item in value:
                            size(item, depth + 1)
                finally:
                    active.remove(id(value))
            else:
                raise SmApiRefused('route-upstream-ceiling')
            if budget[0] < 0:
                raise SmApiRefused('route-upstream-ceiling')
        size(upstream.requests)
        deadline.remaining()
        raw = json.dumps(upstream.requests, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        deadline.remaining()
        return json.loads(raw)
    finally:
        upstream._lock.release()


def sm_p3_fields(measured: Mapping[str, Any]) -> dict:
    """Project measured P3 facts; never upgrade partial type proof to full value proof."""
    schema = measured['schema']
    return {
        'sm_openapi': sm_field(schema['observed'], ok=True),
        'sm_schema': sm_field(schema, ok=schema['value_constraints_verified'] is True),
        'sm_ct_dot': sm_field(measured['container_tag'], ok=True),
        'sm_conformance': sm_field({'operations': schema['operations'], 'field_types_conformant': True,
                                    'runtime_admission': False}, ok=True),
        'sm_statuses': sm_field(measured['processing'], ok=True),
        'sm_llm_params': sm_field(measured['llm'], ok=True),
        'sm_embed_route': sm_field(measured['embeddings'], ok=True),
        'sm_leg_auth': sm_field({'header_names': measured['embeddings']['header_names'],
                                 'auth_forwarded': False}, ok=True),
        'sm_search': sm_field(measured['search'], ok=True),
        'sm_created_at': sm_field(measured['created_at'], ok=True),
    }

def read_sm_restart_state(port: int, run: str, document_id: str, *, key: Any,
                          request: Callable, admit: Callable, ceiling_s: float = 60.0,
                          clock: Callable = time.monotonic) -> dict:
    """Read one completed diagnostic state under a shared deadline; do not claim atomicity."""
    port = _port(port)
    document_target = sm_api_target('get_document', run, document_id=document_id)
    if key is None:
        raise SmApiRefused('held-key-required')
    if not all(callable(value) for value in (request, admit, clock)):
        raise SmApiRefused('restart-collaborator')
    deadline = _SmReadDeadline(ceiling_s, clock)
    held = key

    def send(p, method, path, *, body=None, key=None, timeout_s=5.0):
        if (p != port or key is not held or type(timeout_s) not in {int, float}
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise SmApiRefused('keyed-sequence-target')
        def bounded(p, method, path, **options):
            options['timeout_s'] = min(options['timeout_s'], timeout_s)
            return request(p, method, path, **options)
        return deadline.send(port, (method, path, body), key=held, request=bounded, admit=admit)

    method, path, body = document_target
    document = sm_document_response(send(port, method, path, body=body, key=held), document_id=document_id)
    if document['status'] != 'done':
        raise SmApiRefused('restart-document-incomplete')
    collection = collect_sm_memories(port, run, document_id, key=held, request=send, admit=lambda: True,
        ceiling_s=min(60.0, deadline.remaining()), clock=clock)
    if collection['memory_found'] is not True:
        raise SmApiRefused('memory-agent-empty')
    method, path, body = sm_api_target('search', run, query=SM_DIAGNOSTIC_QUERY)
    search = sm_search_response(send(port, method, path, body=body, key=held), memories=collection['entries'])
    selected_ids = {entry['id'] for entry in collection['selected']}
    if not search['found'] or not any(row['id'] in selected_ids for row in search['results']):
        raise SmApiRefused('blocked:search-shape')
    created_at = sm_created_at(document)
    state = {'documents': [document], 'memories': collection['entries'], 'search': search}
    seal = sm_state_seal(**state)
    deadline.remaining()
    return {'complete': True, 'document_id': document_id, 'state': state, 'seal': seal,
            'created_at': created_at, 'atomic_snapshot_proven': False, 'writers_quiescent_proven': False}


def compare_sm_restart_states(first: Mapping[str, Any], second: Mapping[str, Any], *,
                              ceiling_s: float = 5.0, clock: Callable = time.monotonic) -> dict:
    """Recompute both observed state seals before reporting each restart comparison."""
    deadline = _SmReadDeadline(ceiling_s, clock)
    parts = {'documents', 'memories', 'search'}
    seals = []
    for measured in (first, second):
        if (not isinstance(measured, Mapping) or measured.get('complete') is not True
                or not isinstance(measured.get('state'), dict) or set(measured['state']) != parts
                or not isinstance(measured.get('seal'), dict) or set(measured['seal']) != parts
                or any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None
                       for value in measured['seal'].values())):
            raise SmApiRefused('restart-state-shape')
        state = measured['state']
        if (not isinstance(state['documents'], list) or not state['documents']
                or any(not isinstance(doc, Mapping) or doc.get('status') != 'done' for doc in state['documents'])):
            raise SmApiRefused('restart-document-incomplete')
        deadline.remaining()
        actual = sm_state_seal(**state)
        deadline.remaining()
        if actual != measured['seal']:
            raise SmApiRefused('restart-state-seal-mismatch')
        seals.append(actual)
    equal = {part: seals[0][part] == seals[1][part] for part in sorted(parts)}
    deadline.remaining()
    return {'equal': all(equal.values()), 'parts': equal,
            'atomic_snapshot_proven': False, 'writers_quiescent_proven': False}

def sm_proxy_port_inventory(ports: Mapping[str, Any]) -> frozenset[int]:
    """Validate and collect every listener from the proxy's declared port table."""
    if (not isinstance(ports, Mapping) or not {'arms', 'scheduler', 'control'} <= set(ports)
            or not set(ports) <= {'arms', 'scheduler', 'control', 'j3'}):
        raise PreflightRefused('server-proxy-port-inventory')
    arms = ports['arms']
    if not isinstance(arms, Mapping) or not 1 <= len(arms) <= 32 or ARM not in arms:
        raise PreflightRefused('server-proxy-port-inventory')
    values = [_port(value) for name, value in ports.items() if name != 'arms']
    for arm, roles in arms.items():
        if (type(arm) is not str or not arm or len(arm) > 100 or not isinstance(roles, Mapping)
                or not roles or 'catcher' not in roles
                or not set(roles) <= {'catcher', 'write', 'reader', 'ollama'}):
            raise PreflightRefused('server-proxy-port-inventory')
        values.extend(_port(value) for value in roles.values())
    if len(values) > 256 or len(set(values)) != len(values):
        raise PreflightRefused('server-proxy-port-inventory')
    return frozenset(values)


def allocate_sm_server_port(ports: Mapping[str, Any], allocate: Callable[[], int], *,
                            prior_port: int | None = None) -> int:
    """Refuse a collided or changed inventory; this does not reserve the socket."""
    occupied = sm_proxy_port_inventory(ports)
    if not callable(allocate):
        raise PreflightRefused('server-port-allocator')
    if prior_port is not None:
        _port(prior_port)
    port = _port(allocate())
    if sm_proxy_port_inventory(ports) != occupied:
        raise PreflightRefused('server-proxy-port-inventory-changed')
    if port in occupied or port == prior_port:
        raise PreflightRefused('server-proxy-port-collision')
    return port

EMBED_TAG = 'nvt3-bge-m3-d1:latest'
EMBED_DIGEST = '561ce53b3730a15c5b08676cc985d4a0805d95353c6576e5ef982bc5cc3c5a65'


def embed_record(contract: Any, launch: Any, *, d1_run: str) -> dict:
    """Validate one bounded owned read; use those same bytes for its recorded hash."""
    if type(d1_run) is not str or LABEL.fullmatch(d1_run) is None:
        raise PreflightRefused('embed-run-label')
    root = Path(contract.runs_root)
    path = root / '_d1tag' / d1_run / 'record.json'
    if not launch._within(path, root) or launch._link_in_chain(root, path):
        raise PreflightRefused('embed-record-path')
    try:
        with path.open('rb') as stream:
            raw = stream.read(RECORD_MAX_BYTES + 1)
        if len(raw) > RECORD_MAX_BYTES:
            raise PreflightRefused('embed-record-limit')
        record = json.loads(raw)
        tag = record['tag']
        if (record['run'] != d1_run or record.get('problems') != [] or not isinstance(tag, dict)
                or tag.get('name') != EMBED_TAG or tag.get('digest') != EMBED_DIGEST):
            raise PreflightRefused('embed-record-pin')
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise PreflightRefused('embed-record-unreadable:' + type(exc).__name__) from None
    return {'tag': EMBED_TAG, 'digest': EMBED_DIGEST, 'sha256': hashlib.sha256(raw).hexdigest(), 'path': str(path)}


def preflight_static(contract: Any, launch: Any, bundle: Any, *, run: str, d1_run: str,
                     fake_knobs: Mapping[str, str], test_only: bool, owner_home: Path | None = None) -> dict:
    """Prepare immutable input facts without creating any live-run directories."""
    check_fake_knobs(fake_knobs, test_only=test_only)
    if launch.TOOLS_ALLOWED_BASELINE.get(ARM) != frozenset(TOOLS):
        raise PreflightRefused('tools-baseline')
    arm = owned_arm_dir(contract, launch, run)
    container_tag = sm_container_tag(run)
    facts = bundle_record(contract, launch)
    if (facts.get('workflow', {}).get('default') != 'direct'
            or facts['workflow'].get('platform') != 'win32'
            or facts['workflow'].get('env') != 'WORKFLOW_ENGINE'):
        raise PreflightRefused('workflow-baseline')
    switches = dict(bundle.SPAWN_ENV)
    if (set(switches) != {'SUPERMEMORY_NO_UPDATE_CHECK', 'SUPERMEMORY_DISABLE_TELEMETRY', 'SUPERMEMORY_NO_OPEN'}
            or any(value != '1' for value in switches.values())
            or any(launch.TELEMETRY_OFF.get(name) != value for name, value in switches.items())):
        raise PreflightRefused('switches-baseline')
    embed = embed_record(contract, launch, d1_run=d1_run)
    owner_present = None if owner_home is None else owner_env_exists(owner_home)
    return {'arm_dir': arm, 'binary': Path(facts['pin']['binary']), 'bundle': facts,
            'embed': embed, 'switches': switches, 'data_dir': arm / 'data',
            'container_tag': container_tag, 'owner_env_present': owner_present}

class RetainedJob:
    """Read and terminate only the captured Job handle, even if the PID map changes."""

    def __init__(self, native: Any, pid: int) -> None:
        base = getattr(native, 'jobs', None)
        handles = getattr(base, 'jobs', None)
        handle = handles.get(pid) if isinstance(handles, dict) else None
        if type(pid) is not int or pid <= 0 or type(handle) is not int or handle <= 0:
            raise PreflightRefused('server-owned-job-unavailable')
        self.base, self.pid, self.handle = base, pid, handle
        self.jobs = {pid: handle}

    @classmethod
    def from_handle(cls, base: Any, pid: int, handle: int) -> Any:
        if type(pid) is not int or pid <= 0 or type(handle) is not int or handle <= 0:
            raise PreflightRefused('server-created-job-handle')
        result = cls.__new__(cls)
        result.base, result.pid, result.handle = base, pid, handle
        result.jobs = {pid: handle}
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def members(self) -> list[int]:
        rows = type(self.base).members(self, self.pid)
        if (not isinstance(rows, list) or len(rows) > 4096
                or any(type(pid) is not int or pid <= 0 for pid in rows)):
            raise PreflightRefused('server-job-members-shape')
        return rows

    def terminate(self) -> bool:
        return type(self.base).terminate(self, self.pid) is True

    def same_binding(self) -> bool:
        return self.base.jobs.get(self.pid) == self.handle


def _owned_windows_process(process: Any) -> bool:
    return (os.name == 'nt' and isinstance(process, subprocess.Popen)
            and getattr(process, '_handle', None) is not None)


def _kill_retained_root(process: Any) -> None:
    """On Windows Popen.kill uses its retained process HANDLE, never a PID lookup."""
    if not _owned_windows_process(process):
        raise PreflightRefused('server-retained-root-handle-unavailable')
    process.kill()


def _signal_retained_root(boot: OwnedBoot) -> None:
    """Graceful CTRL_BREAK only to the still-live verified original process group."""
    process = boot.child.process
    if (not _owned_windows_process(process) or process.poll() is not None
            or not boot.identity_matches()):
        raise PreflightRefused('server-graceful-root-identity-unavailable')
    process.send_signal(signal.CTRL_BREAK_EVENT)


class OwnedBoot:
    """One scheduler-spawned server, its immutable identity and redacting pump."""

    def __init__(self, child: Any, pump: Any, job: RetainedJob | None, *, native: Any,
                 birth_time: float | None, port: int, dirs: Any) -> None:
        self.child, self.pump, self.job, self.native = child, pump, job, native
        self.pid = getattr(getattr(child, 'process', None), 'pid', None)
        self.birth_time, self.port, self.dirs = birth_time, port, dirs
        self.closed = False

    def identity_matches(self) -> bool:
        return (self.job is not None and self.birth_time is not None and self.job.same_binding()
                and self.native._identity(self.pid) == self.birth_time)

    def close(self, server: Any, *, graceful: bool, drain_s: float = 10.0,
              clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict:
        """Attempt every independent cleanup; no failed drain/write becomes success."""
        if self.closed:
            raise PreflightRefused('server-already-closed')
        if (type(drain_s) not in {int, float} or not math.isfinite(drain_s) or not 0 < drain_s <= 10):
            raise PreflightRefused('server-drain-ceiling')
        result = {'how': 'killed', 'problems': [], 'members': None, 'pump_drained': False, 'root_exited': False}
        until = clock() + drain_s
        remaining = lambda: max(0.0, until - clock())
        try:
            if graceful and self.child.process.poll() is None and self.identity_matches():
                _signal_retained_root(self)
                result['how'] = 'ctrl_break'
                try:
                    self.child.process.wait(timeout=remaining())
                except subprocess.TimeoutExpired:
                    pass  # The captured Job is the only whole-tree escalation below.
            elif graceful:
                result['problems'].append('server-identity-unavailable-for-graceful-stop')
        except Exception as exc:
            result['problems'].append('server-graceful-stop:' + type(exc).__name__)
        # Initial membership failure must not suppress captured-handle termination.
        members = None
        try:
            if self.job is None:
                raise PreflightRefused('server-job-unavailable')
            members = self.job.members()
        except Exception as exc:
            result['problems'].append('server-initial-members:' + type(exc).__name__)
        terminated = False
        try:
            terminated = self.job is not None and self.job.terminate() is True
            if not terminated:
                result['problems'].append('server-job-termination-refused')
            if self.child.process.poll() is None or members:
                result['how'] = 'killed'
        except Exception as exc:
            result['problems'].append('server-owned-termination:' + type(exc).__name__)
        if not terminated:
            try:
                if self.child.process.poll() is None:
                    _kill_retained_root(self.child.process)
            except Exception as exc:
                result['problems'].append('server-retained-root-stop:' + type(exc).__name__)
        try:
            self.child.process.wait(timeout=remaining())
            result['root_exited'] = self.child.process.poll() is not None
        except Exception as exc:
            result['problems'].append('server-owned-stop:' + type(exc).__name__)
        try:
            members = self.job.members()
            while members and clock() < until:
                sleep(min(0.05, remaining()))
                members = self.job.members()
            result['members'] = members
            if members:
                result['problems'].append('server-job-not-drained')
        except Exception as exc:
            result['problems'].append('server-job-drain:' + type(exc).__name__)
        try:
            if self.pump is None:
                result['problems'].append('server-pump-unavailable')
            else:
                result['pump_drained'] = self.pump.join(remaining()) is True
            if self.pump is not None and (not result['pump_drained'] or self.pump.error is not None):
                result['problems'].append('server-pump-not-complete')
        except Exception as exc:
            result['problems'].append('server-pump-drain:' + type(exc).__name__)
        self.closed = result['root_exited'] and result['members'] == [] and result['pump_drained']
        result['complete'] = self.closed and not result['problems']
        return result


def start_boot(contract: Any, launch: Any, server: Any, scheduler_module: Any, scheduler: Any,
               guarded: GuardedLaunch, *, run: str, unit: str, port: int, data_dir: Path,
               boots: list[OwnedBoot]) -> OwnedBoot:
    """Use the actual scheduler/env guard; pump precedes every readiness/listener observation."""
    _port(port)
    _owned_data_path(contract, launch, run, data_dir)
    native = getattr(scheduler.witnesses, 'native', None)
    if (scheduler.launch is not guarded or native is None or native.jobs is None
            or getattr(scheduler.witnesses, '_check', None) is None or unit not in guarded.expected):
        raise PreflightRefused('server-spawn-boundary-unavailable')
    if (not isinstance(boots, list) or type(unit) is not str or LABEL.fullmatch(unit) is None
            or guarded.expected[unit].get('SUPERMEMORY_PORT') != str(port)
            or guarded.expected[unit].get('SUPERMEMORY_DATA_DIR') != str(data_dir)):
        raise PreflightRefused('server-spawn-declaration')
    binary = Path(guarded.argv[0])
    if not launch._within(binary, contract.polygon_root) or launch._link_in_chain(contract.polygon_root, binary):
        raise PreflightRefused('server-binary-path')
    pending = len(guarded.children)
    child = None
    prepared: dict[str, Any] = {}

    def build(dirs: Any) -> Any:
        owned_arm = _existing_arm_path(contract, launch, run)
        if dirs.cwd != owned_arm / unit or dirs.home != owned_arm / (unit + '.home'):
            raise PreflightRefused('server-unit-layout')
        for path in (dirs.home, dirs.cwd, dirs.home / 'server.stdout.log'):
            if (not Path(path).is_absolute() or not launch._within(path, owned_arm)
                    or launch._link_in_chain(contract.runs_root, path)):
                raise PreflightRefused('server-output-path')
        if os.path.lexists(dirs.home / 'server.stdout.log'):
            raise PreflightRefused('server-output-path-used')
        prepared['dirs'] = dirs
        return server.launch_spec(scheduler_module, binary, pin_sha256=BINARY_SHA256,
                                  declared=guarded.expected[unit])

    def retain(returned: Any) -> OwnedBoot:
        for owned in boots:
            if owned.child.process is returned.process:
                return owned
        boot = OwnedBoot(returned, None, None, native=native, birth_time=None,
                         port=port, dirs=prepared.get('dirs'))
        boots.append(boot)
        # Only O(1) captured-handle bookkeeping precedes the pump. No tree/socket/key I/O.
        return boot

    def created(returned: Any) -> OwnedBoot:
        boot = retain(returned)
        if returned.process.stdout is None:
            raise PreflightRefused('server-output-pipe-unavailable')
        boot.pump = server.RedactingPump(returned.process.stdout, prepared['dirs'].home / 'server.stdout.log')
        boot.pump.start()
        return boot

    previous = getattr(guarded, 'creation_callback', None)
    guarded.creation_callback = created
    try:
        child, dirs = scheduler.spawn_child(build,
            role='server', stand='_a8', run=run, arm=ARM, unit=unit)
        boot = retain(child)
        if boot.job is None:
            if hasattr(guarded, '_spawn_owned'):
                raise PreflightRefused('server-created-job-unretained')
            boot.job = RetainedJob(native, boot.pid)
        if child.process.stdout is None:
            raise PreflightRefused('server-output-pipe-unavailable')
        if boot.pump is None:
            boot.pump = server.RedactingPump(child.process.stdout, prepared['dirs'].home / 'server.stdout.log')
            boot.pump.start()
        birth = native.registered_roots.get(child.process.pid)
        if type(birth) not in {int, float} or not math.isfinite(birth):
            raise PreflightRefused('server-birth-time-unavailable')
        if not any(row.get('spawn_id') == child.spawn_id and row.get('unit') == unit for row in guarded.observed):
            raise PreflightRefused('server-env-unobserved')
        boot.birth_time = birth
        if dirs is not prepared['dirs']:
            raise PreflightRefused('server-unit-dirs-changed')
        return boot
    except Exception:
        # No cleanup is hidden here. The outer finalizer retains every resource and
        # records termination, drain and write failures independently.
        for returned in guarded.children[pending:]:
            boot = retain(returned)
            if boot.job is None and not hasattr(guarded, '_spawn_owned'):
                boot.job = RetainedJob(native, boot.pid)  # Legacy injected test, not the production path.
        raise
    finally:
        guarded.creation_callback = previous


def ready_boot(boot: OwnedBoot, *, ceiling_s: float, request: Callable = sm_request,
               clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict:
    """One bounded readiness loop; each request also has the whole-request deadline."""
    if type(ceiling_s) not in {int, float} or not math.isfinite(ceiling_s) or not 0 < ceiling_s <= 120:
        raise PreflightRefused('server-ready-ceiling')
    t0, polls, status = clock(), 0, None
    until = t0 + ceiling_s
    while clock() < until:
        if boot.child.process.poll() is not None or not boot.identity_matches():
            return {'ready': False, 'polls': polls, 'status': status, 'reason': 'server-exited-or-changed'}
        polls += 1
        try:
            reply = request(boot.port, 'GET', '/', keyed=False, timeout_s=min(1.0, max(0.001, until - clock())))
            status = reply['status']
        except (OSError, http.client.HTTPException, SmRequestRefused):
            status = None
        if boot.child.process.poll() is not None or not boot.identity_matches():
            return {'ready': False, 'polls': polls, 'status': status, 'reason': 'server-exited-or-changed'}
        if clock() < until and type(status) is int and status != 503:
            return {'ready': True, 'polls': polls, 'status': status, 'reason': None}
        sleep(min(0.05, max(0.0, until - clock())))
    return {'ready': False, 'polls': polls, 'status': status, 'reason': 'server-ready-timeout'}


def owned_key_file(contract: Any, launch: Any, *, run: str, data_dir: Path, held: Any) -> dict:
    """Compare a bounded product-owned key file; no key bytes enter returned metadata."""
    data = _owned_data_path(contract, launch, run, data_dir)
    path = data / 'api-key'
    if launch._link_in_chain(contract.runs_root, path) or not path.is_file():
        raise PreflightRefused('product-key-file-path')
    try:
        with path.open('rb') as stream:
            raw = stream.read(513)
        if len(raw) > 512 or held is None or not held.matches(raw):
            raise PreflightRefused('product-key-file-mismatch')
    except OSError as exc:
        raise PreflightRefused('product-key-file-unreadable:' + type(exc).__name__) from None
    return {'matched': True, 'sha12': held.sha12, 'form': held.form}

P0P2_FIELDS = ('sm_pin', 'sm_bundle', 'sm_switches', 'sm_workflow', 'sm_firewall',
               'sm_runs', 'sm_version', 'sm_bind', 'sm_tree', 'sm_egress_boot', 'sm_key_form',
               'sm_home', 'sm_auth')


def sm_field(value: Any, *, ok: bool, blocked: str | None = None) -> dict:
    result = {'value': value, 'ok': ok is True}
    if blocked is not None:
        result['blocked'] = blocked
    return result


def listener_problems(rows: Sequence[Mapping[str, Any]], *, pid: int, port: int) -> list[str]:
    """The pinned Windows bundle binds one unspecified listener on its owned PID."""
    if not isinstance(rows, (list, tuple)) or len(rows) != 1:
        return ['server-listener-count']
    row = rows[0]
    if (not isinstance(row, dict) or row.get('host') not in {'0.0.0.0', '::'}
            or type(row.get('pid')) is not int or row['pid'] != pid
            or type(row.get('port')) is not int or row['port'] != port
            or row.get('error') or row.get('complete') is not True):
        return ['server-listener-identity']
    return []


def native_listeners(native: Any, boot: OwnedBoot) -> list[dict]:
    """Read actual sockets through the witness sampler; fallback/unknown owners refuse."""
    if not boot.identity_matches():
        raise PreflightRefused('server-listener-birth-time')
    lock = getattr(native, '_lock', None)
    if lock is None:
        raise PreflightRefused('server-listener-lock-unavailable')
    with lock:
        rows, errors = native.sampler.connections({boot.pid})
        owners = native.sampler.listeners()
        rows = tuple(rows)
        if isinstance(owners, dict):
            owners = {name: frozenset(pids) if isinstance(pids, set) else None for name, pids in owners.items()}
    if type(errors) is not int or errors != 0 or not isinstance(owners, dict):
        raise PreflightRefused('server-listener-table-incomplete')
    listeners = []
    for pid, host, port, remote_host, remote_port, status in rows:
        if status == 'LISTEN' or status == 'NONE' and remote_host is None:
            proto = 'tcp' if status == 'LISTEN' else 'udp'
            known = owners.get((proto, port))
            complete = (isinstance(known, frozenset) and known == {boot.pid} and proto == 'tcp')
            listeners.append({'pid': pid, 'host': host, 'port': port, 'complete': complete})
    if not boot.identity_matches():
        raise PreflightRefused('server-listener-birth-time')
    return listeners


def run_sm_p0p2(contract: Any, launch: Any, bundle: Any, server: Any, *, run: str,
                d1_run: str, deps: Any, fake_knobs: Mapping[str, str] | None = None) -> dict:
    """Connected C6a phase driver; setup is injected, incomplete later phases cannot pass.

    ``deps.setup`` must retain each acquired runtime resource immediately in the
    supplied list as (name, close, accepted), before another resource is started.
    Production admission remains refused while firewall projection authority is incomplete.
    """
    knobs = {} if fake_knobs is None else fake_knobs
    static = preflight_static(contract, launch, bundle, run=run, d1_run=d1_run,
                              fake_knobs=knobs, test_only=deps.test_only, owner_home=deps.owner_home)
    if type(static.get('owner_env_present')) is not bool:
        raise PreflightRefused('owner-env-metadata-unmeasured')
    arm = static['arm_dir']
    fields = {name: sm_field(value, ok=True) for name, value in (
        ('sm_pin', {'sha256': BINARY_SHA256}),
        ('sm_bundle', {'record_sha256': BUNDLE_RECORD_SHA256, 'tools_sha256': TOOLS_SHA256}),
        ('sm_switches', static['switches']), ('sm_workflow', {'engine': 'direct'}))}
    record = {'arm': ARM, 'run': run, 'fields': fields, 'embed_pin': static['embed'],
              'phase': 'P0-P2', 'authoritative': False}
    problems, checks, boots, keys, resources = [], [], [], [], []
    runtime = None
    open_check = None
    live_refusal = None
    catcher_lines = None
    consumed = False
    try:
        # Protect partial creation: every consumed label attempts its terminal record.
        try:
            arm.parent.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise PreflightRefused('used-run-label') from None
        consumed = True
        arm.mkdir()
        runtime = deps.setup(contract, launch, static, run, resources)
        if runtime.gate.admits_new_unit() is not True:
            raise PreflightRefused('incident-gate')
        fw = runtime.read_firewall('fw-p0', static['binary'])
        fw_errors = firewall_problems(fw['facts'], program=static['binary'])
        fw_ok = fw.get('complete') is True and fw.get('problems') == [] and not fw_errors
        fields['sm_firewall'] = sm_field(fw, ok=fw_ok)
        if not fw_ok:
            raise PreflightRefused('firewall-evidence')
        # CIM scopes alone do not establish all WFP conditions; a live refusal is explicit.
        if runtime.policy_authority(fw) is not True:
            raise PreflightRefused('firewall-evidence-gap')
        if (type(runtime.key_wait_s) not in {int, float} or not math.isfinite(runtime.key_wait_s)
                or not 0 < runtime.key_wait_s <= 10):
            raise PreflightRefused('server-key-wait-ceiling')
        cid = 'a8-sm-' + run + '-p1p2'
        open_check = cid  # Also finalize a partially opened check if begin_check fails.
        runtime.witnesses.begin_check(cid)
        boot = start_boot(contract, launch, server, runtime.scheduler_module, runtime.scheduler,
                          runtime.guarded, run=run, unit='u1', port=runtime.port,
                          data_dir=static['data_dir'], boots=boots)
        if runtime.declare_listener(boot, fw) is not True:
            raise PreflightRefused('server-listener-declaration')
        ready = ready_boot(boot, ceiling_s=runtime.ready_ceiling_s, request=runtime.request)
        fields['sm_runs'] = sm_field(ready, ok=ready['ready'])
        if not ready['ready']:
            raise PreflightRefused('server-not-ready')
        listeners = runtime.listeners(boot)
        bind_errors = listener_problems(listeners, pid=boot.pid, port=boot.port)
        fields['sm_bind'] = sm_field(listeners, ok=not bind_errors)
        tree = runtime.tree(boot.pid)
        tree_errors = tree_problems(tree, pid=boot.pid, birth_time=boot.birth_time, binary_sha256=BINARY_SHA256)
        fields['sm_tree'] = sm_field(tree, ok=not tree_errors)
        if bind_errors or tree_errors:
            raise PreflightRefused('server-boundary')
        if not boot.pump.key_seen.wait(runtime.key_wait_s) or boot.pump.key is None:
            raise PreflightRefused('server-key-unavailable')
        key = boot.pump.key
        keys.append(key)
        fields['sm_key_form'] = sm_field(owned_key_file(contract, launch, run=run,
            data_dir=static['data_dir'], held=key), ok=True)
        fields['sm_home'] = sm_field({'owner_env_present': static['owner_env_present']}, ok=True)
        fields['sm_version'] = runtime.version(boot)
        # Keep the same native/FS ticker active across the gate, auth and shutdown.
        # Its terminal complete check covers boot+auth+stop; no transition is unwitnessed.
        if runtime.gate.admits_new_unit() is not True:
            raise PreflightRefused('incident-gate')
        # The auth target is declared by observed/pinned API evidence in setup;
        # no guessed vendor version/path is embedded in this phase driver.
        method, path, body = runtime.auth_target()
        replies = [runtime.request(boot.port, method, path, body=body, **options) for options in (
            {'keyed': False}, {'keyed': False, 'foreign_host': True},
            {'key': server.HeldKey(b'sm_' + b'A' * 64)}, {'key': key})]
        statuses = [reply['status'] for reply in replies]
        errors = auth_problems(*statuses)
        boundary_errors = [name for name in errors if name != 'blocked:auth-control']
        fields['sm_auth'] = sm_field({'statuses': statuses}, ok=not boundary_errors)
        fields['sm_keycarry'] = sm_field({'held_key_used': True}, ok=True)
        if 'blocked:auth-control' in errors:
            fields['sm_conformance'] = sm_field({'auth_control': statuses[-1]}, ok=False,
                                                blocked='blocked:auth-control')
        problems.extend(boundary_errors)
        if getattr(runtime, 'p3_enabled', False) is True and not errors:
            measured = run_sm_p3(boot.port, run, key=key, rest=runtime.rest, request=runtime.request,
                admit=runtime.gate.admits_new_unit, prepare_routes=runtime.prepare_routes, observe_routes=runtime.observe_routes,
                model=runtime.model, embed_model=static['embed']['tag'])
            fields.update(sm_p3_fields(measured))
            record['phase'] = 'P0-P3'
            record['functional'] = {'document_id': measured['document_id'], 'seal': measured['seal'],
                                    'atomic_snapshot_proven': False, 'writers_quiescent_proven': False}
    except PreflightRefused as exc:
        live_refusal = str(exc)
    except SmApiRefused as exc:
        reason = str(exc)
        record['functional_refusal'] = reason
        if reason in BLOCKED_OUTCOMES:
            fields['sm_conformance'] = sm_field({'functional': reason}, ok=False, blocked=reason)
        else:
            problems.append('functional-refusal:' + reason)
    except Exception as exc:
        problems.append('protocol-exception:' + type(exc).__name__)
    finally:
        stops = []
        # Preserve banner keys even if a failure occurred before key-form inspection.
        for boot in boots:
            if boot.pump is not None and boot.pump.key is not None and not any(held == boot.pump.key for held in keys):
                keys.append(boot.pump.key)
            try:
                stops.append(boot.close(server, graceful=True))
            except Exception as exc:
                problems.append('server-finalization:' + type(exc).__name__)
            if boot.pump is not None and boot.pump.key is not None and not any(held == boot.pump.key for held in keys):
                keys.append(boot.pump.key)
        fields['sm_stop'] = sm_field(stops, ok=bool(stops) and all(stop['complete'] for stop in stops))
        problems.extend('server-stop:' + issue for stop in stops for issue in stop['problems'])
        if runtime is not None and open_check is not None:
            try:
                summary = c6_check_summary(runtime.witnesses.end_check(open_check))
                checks.append((open_check, summary))
                complete = clean_check(summary)
                fields['sm_egress_boot'] = sm_field({'coverage': 'boot-auth-stop', 'check': summary}, ok=complete)
            except Exception as exc:
                problems.append('witness-finalization:' + type(exc).__name__)
        resources_complete = True
        resource_results = []
        for name, close, accepted in reversed(resources):
            try:
                result = close()
                complete = accepted(result) is True
                resource_results.append({'name': name, 'complete': complete})
                if not complete:
                    resources_complete = False
                    problems.append('resource-incomplete:' + name)
            except Exception as exc:
                resources_complete = False
                resource_results.append({'name': name, 'complete': False, 'error': type(exc).__name__})
                problems.append('resource-finalization:' + name + ':' + type(exc).__name__)
        if runtime is not None:
            try:
                catcher_lines = runtime.catcher_lines()
            except Exception as exc:
                problems.append('catcher-finalization:' + type(exc).__name__)
        findings = []
        for key in keys:
            try:
                findings.append(owned_key_search(contract, launch, server, run=run,
                                                 held=key, data_dir=static['data_dir']))
            except Exception as exc:
                problems.append('key-search-finalization:' + type(exc).__name__)
        writers_complete = resources_complete and bool(stops) and all(stop.get('complete') is True for stop in stops)
        search_ok = writers_complete and bool(findings) and all(not item.get('leaks') and not item.get('links')
            and not item.get('unreadable') and type(item.get('files')) is int and item['files'] > 0
            for item in findings)
        fields['sm_key_search'] = sm_field({'findings': findings, 'writers_complete': writers_complete}, ok=search_ok)
        if keys and not search_ok:
            problems.append('key-search-incomplete-or-leak')
        # P4-P5 fields remain absent until their connected implementation lands.
        outcome, reasons = sm_verdict(fields, problems=problems, checks=checks, catcher_lines=catcher_lines)
        clean_checks = all(clean_check(summary) for _, summary in checks)
        if (live_refusal is not None and not problems and not boots and runtime is not None
                and type(catcher_lines) is int and catcher_lines == 0 and clean_checks):
            outcome, reasons = 'refused:' + live_refusal, [live_refusal]
        elif live_refusal is not None:
            reasons.append('live-refusal:' + live_refusal)
        record.update(outcome=outcome, reasons=reasons, problems=problems, checks=checks,
                      catcher_lines=catcher_lines, resources=resource_results,
                      utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        try:
            if consumed:
                write_sm_probe(arm / 'probe.json', record, launch=launch, runs_root=contract.runs_root, held_keys=keys)
        finally:
            keys.clear()
            for boot in boots:
                if boot.pump is not None:
                    boot.pump.key = None
    if not consumed:
        raise PreflightRefused(live_refusal or 'live-label-create')
    return record_clean(record)

class CapturingJobs:
    """Retain only an API-confirmed assigned Job created by this adopt operation."""

    def __init__(self, base: Any, retained: Any) -> None:
        self.base, self.retained = base, retained
        self.created: set[int] = set()
        owner = self
        class Api:
            def __getattr__(view, name):
                return getattr(base.k32, name)
            def AssignProcessToJobObject(view, job, handle):
                process = retained.child.process
                if int(job) not in owner.created or int(handle) != int(process._handle):
                    raise PreflightRefused('job-assignment-owner-mismatch')
                ok = base.k32.AssignProcessToJobObject(job, handle)
                if ok:
                    retained.job = RetainedJob.from_handle(base, process.pid, int(job))
                return ok
        self.k32 = Api()

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _new_job(self):
        handle = self.base._new_job()
        self.created.add(int(handle))
        return handle

    def adopt(self, pid, handle=None):
        return type(self.base).adopt(self, pid, handle)


class CapturingNative:
    """Capture a newly assigned Job before launch resumes the exact created process."""

    def __init__(self, native: Any, child: Any, retained: Any) -> None:
        self.native, self.child, self.retained = native, child, retained

    def __getattr__(self, name: str) -> Any:
        return getattr(self.native, name)

    def register(self, pid: int, handle: int | None = None, label: str | None = None) -> bool:
        process = self.child.process
        jobs = self.native.jobs
        if pid != process.pid or handle is None or handle != int(getattr(process, '_handle', 0)):
            raise PreflightRefused('created-process-handle-mismatch')
        native = self.native
        captured_jobs = CapturingJobs(jobs, self.retained)
        class View:
            jobs = captured_jobs
            def __getattr__(view, name):
                return getattr(native, name)
        try:
            return type(native).register(View(), pid, handle=handle, label=label)
        finally:
            if self.retained.job is not None:
                self.retained.birth_time = jobs.created_at(handle)


class OwnedGate:
    """Own all poll workers; never call the legacy unbounded stop/final-poll path."""

    def __init__(self, driver: Any) -> None:
        self.driver = driver
        self.stop_requested = threading.Event()
        self.workers: list[threading.Thread] = []
        self.errors: list[str] = []
        self.final_started = False

    def _poll(self) -> None:
        try:
            self.driver.poll()
        except Exception as exc:
            self.errors.append(type(exc).__name__)
            self.driver._blind('c6-gate-poll:' + type(exc).__name__)

    def start(self) -> None:
        def loop() -> None:
            while not self.stop_requested.wait(self.driver.poll_s):
                self._poll()
        worker = threading.Thread(target=loop, name='sm-incident-gate', daemon=True)
        self.workers.append(worker)
        worker.start()

    def close(self, *, drain_s: float = 5.0) -> dict:
        until = time.monotonic() + drain_s
        self.stop_requested.set()
        for worker in self.workers:
            if worker.ident is not None:
                worker.join(max(0.0, until - time.monotonic()))
        if any(worker.is_alive() for worker in self.workers):
            return {'complete': False, 'problems': ['gate-worker-not-drained']}
        if not self.final_started:
            self.final_started = True
            worker = threading.Thread(target=self._poll, name='sm-incident-final-poll', daemon=True)
            self.workers.append(worker)
            worker.start()
            worker.join(max(0.0, until - time.monotonic()))
        complete = not any(worker.is_alive() for worker in self.workers) and not self.errors and not self.driver.problems
        return {'complete': complete, 'problems': list(self.errors),
                'workers_drained': not any(worker.is_alive() for worker in self.workers)}


class OwnedStatus:
    """Retain the debug STATUS scope before writes and finalize every level independently."""

    def __init__(self, status: Any, *, run: str, model: str) -> None:
        self.status, self.run, self.model = status, run, model
        self.stand_open = self.block_open = self.run_open = False
        self.ident = '_a8/p0p2/' + run + '/' + ARM
        self.t0 = time.monotonic()
        self.result = None
        self.start_problems = []
        self.halt_kind = lambda: None

    def start(self) -> None:
        self.stand_open = True
        try:
            self.status.stand('_a8', 'START', model=self.model, changelog='c6a-p0p2', order=1)
            self.block_open = True
            self.status.block_start('_a8', 'p0p2', units=['u1'], arm_order=[ARM], seed=0)
            # This is the live probe driver's PID; no product PID exists during P0.
            self.run_open = True
            ident = self.status.start('_a8', 'p0p2', self.run, ARM, pid=os.getpid(), tag='debug')
        except Exception as exc:
            self.start_problems.append('status-start:' + type(exc).__name__)
            raise
        if ident != self.ident:
            raise PreflightRefused('status-run-identity')

    def close(self) -> dict:
        if self.result is not None:
            return self.result
        problems = list(self.start_problems)
        if self.run_open:
            try:
                self.status.abort(self.ident, reason='c6a-incomplete')
                self.run_open = False
            except Exception as exc:
                problems.append('status-abort:' + type(exc).__name__)
            if self.run_open:
                try:
                    self.status.end(self.ident, rc=1, wall_s=time.monotonic() - self.t0,
                                    units=0, out='probe.json')
                    self.run_open = False
                except Exception as exc:
                    problems.append('status-end:' + type(exc).__name__)
        if self.block_open:
            try:
                self.status.block_end('_a8', 'p0p2')
                self.block_open = False
            except Exception as exc:
                problems.append('status-block-end:' + type(exc).__name__)
        if self.stand_open:
            try:
                halt = self.halt_kind()
                if halt not in {None, '401', '402', '403', 'harness-error'}:
                    raise PreflightRefused('status-halt-unknown')
                self.status.stand('_a8', 'END', model=self.model, changelog='c6a-p0p2', halt=halt or 'harness-error')
                self.stand_open = False
            except Exception as exc:
                problems.append('status-stand-end:' + type(exc).__name__)
        self.result = {'complete': not problems and not (self.run_open or self.block_open or self.stand_open),
                       'problems': problems}
        return self.result


class OwnedUpstream:
    """Use the existing fake handlers while owning accepted sockets and every worker."""

    def __init__(self, modules: Any, kind: str, *, resources: list, tag: str = '', digest: str = '',
                 script_policy: str | None = None) -> None:
        self.requests, self.traps, self.unknown = [], [], []
        self.script = [{'content': ''}]
        if script_policy is not None:
            if kind != 'deepseek' or script_policy != SM_CHAT_POLICY:
                raise PreflightRefused('fake-script-policy')
            self.script = json.loads(json.dumps(SM_CHAT_SCRIPT))
            self.script_entry = lambda index, body: sm_chat_script_entry(self.script, index, body)
        self.script_sha256 = modules.canonical_sha256(self.script)
        self.tag, self.digest, self.dims = tag, digest, modules.DIMS
        self._lock = threading.Lock()
        self.workers: list[threading.Thread] = []
        self.sockets: set[Any] = set()
        self.closing = False
        self.stop_requested = threading.Event()
        self.httpd = self._thread = self.shutdown_worker = None
        self.errors = []
        self.result = None
        resources.append(('fake-' + kind, self.close, lambda row: isinstance(row, dict) and row.get('complete') is True))
        from http.server import ThreadingHTTPServer
        owner = self
        handler = modules._ChatHandler if kind == 'deepseek' else modules._OllamaHandler
        class Handler(handler):
            fake = owner
        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False
            def handle_error(self, request, client_address) -> None:
                import sys
                error = sys.exc_info()[1]
                if not (owner.closing and (isinstance(error, (BrokenPipeError, ConnectionResetError, TimeoutError))
                        or isinstance(error, OSError) and getattr(error, 'winerror', None) in {10038, 10058})):
                    owner.errors.append('handler-error:' + type(error).__name__)
            def process_request(self, request, client_address) -> None:
                with owner._lock:
                    if owner.closing:
                        self.shutdown_request(request)
                        return
                    request.settimeout(0.25)  # Bound idle/partial-body reads, including Windows makefile handles.
                    owner.sockets.add(request)
                    def work() -> None:
                        try:
                            self.process_request_thread(request, client_address)
                        finally:
                            with owner._lock:
                                owner.sockets.discard(request)
                    worker = threading.Thread(target=work, name='sm-fake-handler', daemon=True)
                    owner.workers.append(worker)
                    try:
                        worker.start()
                    except Exception:
                        owner.sockets.discard(request)
                        self.shutdown_request(request)
                        raise
        # Assignment precedes bind/listen, so even a partial bind failure is owned.
        self.httpd = Server(('127.0.0.1', 0), Handler, bind_and_activate=False)
        self.httpd.server_bind()
        self.httpd.server_activate()
        self.httpd.timeout = 0.05
        self.host, self.port = self.httpd.server_address[:2]
        def serve() -> None:
            while not self.stop_requested.is_set():
                try:
                    self.httpd.handle_request()
                except Exception as exc:
                    if not self.closing:
                        self.errors.append('accept:' + type(exc).__name__)
                    break
        self._thread = threading.Thread(target=serve, name='sm-fake-accept', daemon=True)
        self._thread.start()

    def record(self, row: dict) -> int:
        with self._lock:
            self.requests.append(row)
            return len(self.requests) - 1

    def close(self, *, drain_s: float = 5.0) -> dict:
        until = time.monotonic() + drain_s
        self.stop_requested.set()
        with self._lock:
            self.closing = True
            sockets = tuple(self.sockets)
        problems = []
        if self.httpd is not None:
            for stream in sockets:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                except Exception as exc:
                    problems.append('fake-socket-shutdown:' + type(exc).__name__)
                try:
                    stream.close()
                except Exception as exc:
                    problems.append('fake-socket-close:' + type(exc).__name__)
            try:
                self.httpd.server_close()
            except Exception as exc:
                problems.append('fake-server-close:' + type(exc).__name__)
        for worker in [self._thread, self.shutdown_worker, *self.workers]:
            if worker is not None and worker.ident is not None:
                try:
                    worker.join(max(0.0, until - time.monotonic()))
                except Exception as exc:
                    problems.append('fake-worker-join:' + type(exc).__name__)
        live = [worker for worker in [self._thread, self.shutdown_worker, *self.workers]
                if worker is not None and worker.is_alive()]
        with self._lock:
            active = len(self.sockets)
        self.result = {'complete': not live and active == 0 and not problems and not self.errors,
                       'problems': [*problems, *self.errors], 'active_sockets': active,
                       'live_workers': len(live)}
        return self.result


class ProxyPumps:
    """Drain both proxy pipes continuously, including READY, with one join ceiling."""

    def __init__(self, process: Any) -> None:
        self.out = BoundedPipePump(process.stdout, ready=True)
        self.err = BoundedPipePump(process.stderr)
        self.error = None
        self.input_worker = None
        self.input_error = None

    def start(self) -> None:
        self.out.start()
        self.err.start()

    def send_secrets(self, process: Any, raw: bytes) -> None:
        if len(raw) > 32768:
            raise PreflightRefused('proxy-stdin-size')
        def write() -> None:
            try:
                process.stdin.write(raw)
                process.stdin.close()
            except Exception as exc:
                self.input_error = type(exc).__name__
        self.input_worker = threading.Thread(target=write, name='sm-proxy-stdin', daemon=True)
        self.input_worker.start()
        self.input_worker.join(2.0)
        if self.input_worker.is_alive() or self.input_error:
            raise PreflightRefused('proxy-stdin-incomplete')

    def join(self, seconds: float) -> bool:
        until = time.monotonic() + seconds
        out = self.out.join(max(0.0, until - time.monotonic()))
        err = self.err.join(max(0.0, until - time.monotonic()))
        if self.input_worker is not None and self.input_worker.ident is not None:
            self.input_worker.join(max(0.0, until - time.monotonic()))
        self.error = self.out.error or self.err.error or self.input_error
        return out and err and (self.input_worker is None or not self.input_worker.is_alive())


def bounded_proxy_post(port: int, path: str, body: Any, token: str, *, timeout: float = 2.0) -> tuple:
    from types import SimpleNamespace
    if type(token) is not str or not token or any(char in token for char in ('\r', '\n')):
        raise PreflightRefused('proxy-token-shape')
    held = SimpleNamespace(header=lambda: {'Authorization': 'Bearer ' + token})
    result = sm_request(port, 'POST', path, body=body, key=held, timeout_s=min(2.0, timeout))
    return result['status'], result['json']


def owned_proxy_config_paths(contract: Any, launch: Any, proxy_dir: Path) -> tuple[Path, Path]:
    """Guard both fresh RP.start write targets before it can create/write anything."""
    run_dir = Path(proxy_dir) / 'run'
    config_path = run_dir / 'proxy_config.json'
    for path in (run_dir, config_path):
        if (not path.is_absolute() or not launch._within(path, contract.runs_root)
                or launch._link_in_chain(contract.runs_root, path) or os.path.lexists(path)):
            raise PreflightRefused('proxy-config-write-path')
    return run_dir, config_path


def proxy_config_record(contract: Any, launch: Any, config_path: Path) -> dict:
    """Validate before opening, then parse only the same bounded owned config bytes."""
    path = Path(config_path)
    if (not path.is_absolute() or path.name != 'proxy_config.json'
            or not launch._within(path, contract.runs_root) or launch._link_in_chain(contract.runs_root, path)):
        raise PreflightRefused('proxy-config-read-path')
    with path.open('rb') as stream:
        raw = stream.read(RECORD_MAX_BYTES + 1)
    if len(raw) > RECORD_MAX_BYTES:
        raise PreflightRefused('proxy-config-size')
    try:
        record = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise PreflightRefused('proxy-config-json:' + type(exc).__name__) from None
    if not isinstance(record, dict) or type(record.get('run_dir')) is not str:
        raise PreflightRefused('proxy-config-shape')
    run_dir = Path(record['run_dir'])
    if (not run_dir.is_absolute() or run_dir != path.parent or not launch._within(run_dir, contract.runs_root)
            or launch._link_in_chain(contract.runs_root, run_dir)):
        raise PreflightRefused('proxy-run-path')
    return {'run_dir': run_dir, 'sha256': hashlib.sha256(raw).hexdigest()}


def spawn_sm_proxy(contract: Any, python: Path, *, launch: Any, resources: list,
                   script: Path, config_path: Path, key_file: Path, stdin_secrets: Mapping,
                   unit: Any, parent_env: Mapping[str, str], witnesses: Any, popen: Callable = subprocess.Popen) -> tuple:
    """The normal proxy launch contract with creation-time ownership and bounded READY."""
    from types import SimpleNamespace
    run_dir = proxy_config_record(contract, launch, config_path)['run_dir']
    jobs = launch.WinJobs()
    native = SimpleNamespace(jobs=jobs, registered_roots={}, _identity=jobs.identity)
    state = {'boot': None, 'result': None, 'ports': None}
    def close() -> dict:
        boot = state['boot']
        if boot is None:
            return {'complete': True, 'spawned': False}
        if not boot.closed:
            shutdown_error = None
            if state['ports'] is not None:
                try:
                    status, _ = bounded_proxy_post(state['ports']['control'], '/shutdown', {},
                                                   stdin_secrets['control_token'])
                    if status != 200:
                        raise PreflightRefused('proxy-shutdown-status')
                    boot.child.process.wait(timeout=5.0)
                except Exception as exc:
                    shutdown_error = type(exc).__name__
            state['result'] = boot.close(None, graceful=False)
            if state['ports'] is not None and (shutdown_error is not None or boot.child.process.poll() != 0):
                state['result']['complete'] = False
                state['result']['problems'].append('proxy-shutdown:' + (shutdown_error or 'nonzero'))
        return state['result']
    resources.append(('proxy-process', close, lambda row: isinstance(row, dict) and row.get('complete') is True))
    def create(*args, **kwargs):
        if os.name != 'nt':
            raise PreflightRefused('proxy-required-windows-job')
        kwargs['creationflags'] = int(kwargs.get('creationflags', 0)) | launch.CREATE_SUSPENDED
        process = popen(*args, **kwargs)
        boot = OwnedBoot(SimpleNamespace(process=process), None, None, native=native,
                         birth_time=None, port=1, dirs=unit)
        state['boot'] = boot  # Before adopt, pump, stdin, READY, ports or control I/O.
        handle = int(process._handle)
        birth = CapturingJobs(jobs, boot).adopt(process.pid, handle=handle)
        if boot.job is None:
            raise PreflightRefused('proxy-created-job-unretained')
        boot.birth_time = birth
        if type(birth) not in {int, float} or not math.isfinite(birth):
            raise PreflightRefused('proxy-job-identity')
        native.registered_roots[process.pid] = birth
        boot.pump = ProxyPumps(process)
        boot.pump.start()
        if jobs.resume(handle) is not True:
            raise PreflightRefused('proxy-job-resume')
        return process
    env = launch.build_env(contract, parent_env=parent_env, unit=unit,
                           path_dirs=[Path(python).parent], declared={}, catcher_url='', proxies=False)
    child = launch.spawn(contract, [str(python), str(script), 'serve', '--config', str(config_path), '--key-file', str(key_file)],
        env=env, cwd=unit.cwd, record={'role': 'proxy'}, parent_env=parent_env, catcher_url='',
        argv_exception={1: script, 6: key_file}, popen=create, requirement='optional',
        unwitnessed_reason='the proxy is the instrument: its loopback fake upstream and calls are recorded',
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    boot = state['boot']
    boot.child = child
    # The suspended root has already been assigned to its immutable private Job.
    boot.pump.send_secrets(child.process, (json.dumps(dict(stdin_secrets)) + '\n').encode('utf-8'))
    pump = boot.pump.out
    if not pump.ready.wait(30.0) or pump.error or pump.ready_line is None:
        raise PreflightRefused('proxy-ready-incomplete')
    got = pump.ready_line.decode('ascii', 'strict').strip()
    if re.fullmatch(r'READY [0-9a-f]{64}', got) is None:
        raise PreflightRefused('proxy-ready-shape')
    ports_path = run_dir / 'ports.json'
    if launch._link_in_chain(contract.runs_root, ports_path):
        raise PreflightRefused('proxy-ports-path')
    with ports_path.open('rb') as stream:
        data = stream.read((1 << 20) + 1)
    if len(data) > 1 << 20 or hashlib.sha256(data).hexdigest() != got[6:]:
        raise PreflightRefused('proxy-ports-sha')
    ports = json.loads(data)
    if not isinstance(ports, dict):
        raise PreflightRefused('proxy-ports-shape')
    if witnesses.native.allow_listener(child.process.pid, 'the v3 proxy') is not True:
        raise PreflightRefused('proxy-listener-identity')
    state['ports'] = ports
    return child, ports

class BoundedPipePump:
    """Drain a readonly helper pipe while retaining at most the declared byte limit."""

    def __init__(self, stream: Any, *, limit: int = 4 << 20, ready: bool = False) -> None:
        self.stream, self.limit = stream, limit
        self.raw = bytearray()
        self.digest = hashlib.sha256()
        self.count = 0
        self.error = None
        self.done = threading.Event()
        self.ready = threading.Event()
        self.ready_line = None
        self.capture_ready = ready
        self.thread = threading.Thread(target=self._run, name='sm-firewall-output', daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        try:
            while True:
                read = getattr(self.stream, 'read1', self.stream.read)
                chunk = read(65536)
                if not chunk:
                    break
                self.digest.update(chunk)
                self.count += len(chunk)
                self.raw.extend(chunk[:max(0, self.limit - len(self.raw))])
                if self.capture_ready and self.ready_line is None:
                    end = self.raw.find(b'\n')
                    if end >= 0:
                        self.ready_line = bytes(self.raw[:end + 1]) if end < 256 else None
                        self.ready.set()
                    elif len(self.raw) > 256:
                        self.error = 'ready-line-limit'
                        self.ready.set()
                if self.count > self.limit:
                    self.error = 'output-limit'
        except Exception as exc:
            self.error = self.error or type(exc).__name__
        finally:
            self.done.set()

    def join(self, seconds: float) -> bool:
        self.thread.join(seconds)
        return self.done.is_set()


def firewall_parent_epoch(witnesses: Any, check_id: str) -> tuple:
    """Capture the actual ticker generation and the original FS baseline, never a stale name alone."""
    if (type(check_id) is not str or not check_id or len(check_id) > 256
            or getattr(witnesses, '_check', None) != check_id
            or not isinstance(getattr(witnesses, '_before', None), dict)
            or not isinstance(getattr(witnesses, '_before_rows', None), dict)
            or not isinstance(getattr(witnesses, '_tags', None), dict)):
        raise PreflightRefused('firewall-parent-check')
    tickers = (witnesses.native, *witnesses.containers)
    if tickers[0] is None:
        raise PreflightRefused('firewall-parent-check')
    generation = []
    for ticker in tickers:
        if ticker is None:
            continue
        thread, stop, began = (getattr(ticker, name, None) for name in ('_thread', '_stop', '_t0'))
        if (not callable(getattr(thread, 'is_alive', None)) or thread.is_alive() is not True
                or not callable(getattr(stop, 'is_set', None)) or stop.is_set() is not False
                or type(began) not in {int, float} or not math.isfinite(began)):
            raise PreflightRefused('firewall-parent-check')
        generation.append((ticker, thread, stop, began))
    return (witnesses._before, witnesses._before_rows, witnesses._tags, tuple(generation))


def same_firewall_parent_epoch(witnesses: Any, check_id: str, original: tuple) -> bool:
    try:
        current = firewall_parent_epoch(witnesses, check_id)
    except PreflightRefused:
        return False
    return (all(current[index] is original[index] for index in range(3))
            and len(current[3]) == len(original[3])
            and all(all(left[index] is right[index] for index in range(3)) and left[3] == right[3]
                    for left, right in zip(current[3], original[3])))


def firewall_ticker_active(witnesses: Any) -> bool:
    """Refuse an omitted parent if any real witness is still running."""
    for ticker in (witnesses.native, *getattr(witnesses, 'containers', ())):
        thread = getattr(ticker, '_thread', None)
        if callable(getattr(thread, 'is_alive', None)) and thread.is_alive() is True:
            return True
    return False


def read_firewall_real(contract: Any, launch: Any, modules: Any, scheduler: Any, *,
                       run: str, unit: str, binary: Path, resources: list,
                       parent_check: str | None = None) -> dict:
    """Fresh required-Job readonly child and complete check; never modify policy."""
    witnesses = scheduler.witnesses
    borrowed = parent_check is not None
    epoch = firewall_parent_epoch(witnesses, parent_check) if borrowed else None
    if not borrowed and firewall_ticker_active(witnesses):
        raise PreflightRefused('firewall-parent-required')
    cid = 'a8-sm-' + run + '-' + unit
    boot = None
    open_check = False
    facts, problems, check, rc = None, [], None, None
    output_meta = None
    cleanup_state = {'result': None}
    prepared = {}
    previous = getattr(scheduler.launch, 'creation_callback', None)
    def retain(child):
        nonlocal boot
        boot = OwnedBoot(child, None, None, native=witnesses.native, birth_time=None, port=1,
                         dirs=prepared.get('dirs'))
        def close():
            if not boot.closed:
                cleanup_state['result'] = boot.close(None, graceful=False)
            return cleanup_state['result']
        resources.append(('firewall:' + unit, close, lambda result: isinstance(result, dict) and result.get('complete') is True))
        boot.pump = BoundedPipePump(child.process.stdout)
        boot.pump.start()
        return boot
    scheduler.launch.creation_callback = retain
    def build(dirs):
        prepared['dirs'] = dirs
        return modules.SC.LaunchSpec(argv=(launch.FW_READ_BINARY, *launch.FW_READ_ARGV),
            declared={launch.FW_READ_PROGRAM_ENV: str(binary)}, stdio='server', requirement='required')
    try:
        if not borrowed:
            open_check = True  # A partially opened standalone check still needs finalization.
            witnesses.begin_check(cid)
        elif not same_firewall_parent_epoch(witnesses, parent_check, epoch):
            raise PreflightRefused('firewall-parent-check')
        child, dirs = scheduler.spawn_child(build,
            role=launch.FW_READ_ROLE, stand='_a8', run=run, arm='fw-read', unit=unit)
        if boot is None:
            boot = retain(child)  # Injected collaborator; production captures before return.
        if boot.job is None:
            if hasattr(scheduler.launch, '_spawn_owned'):
                raise PreflightRefused('firewall-created-job-unretained')
            boot.job = RetainedJob(witnesses.native, child.process.pid)
            boot.birth_time = witnesses.native.registered_roots.get(child.process.pid)
        try:
            rc = child.process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            problems.append('firewall-read-timeout')
        if rc != 0:
            problems.append('firewall-read-nonzero')
        cleanup_state['result'] = boot.close(None, graceful=False)
        problems.extend('firewall-cleanup:' + item for item in cleanup_state['result']['problems'])
        if not boot.pump.done.is_set() or boot.pump.error:
            problems.append('firewall-output-incomplete')
        elif rc == 0:
            try:
                facts = json.loads(boot.pump.raw)
                if not isinstance(facts, dict) or facts.get('program') != str(binary):
                    problems.append('firewall-output-shape')
            except (ValueError, RecursionError) as exc:
                problems.append('firewall-output-json:' + type(exc).__name__)
        output_meta = {'bytes': boot.pump.count, 'sha256': boot.pump.digest.hexdigest()}
    except Exception as exc:
        problems.append('firewall-read-exception:' + type(exc).__name__)
    finally:
        scheduler.launch.creation_callback = previous
        if boot is not None and not boot.closed:
            try:
                cleanup_state['result'] = boot.close(None, graceful=False)
                problems.extend('firewall-cleanup:' + item for item in cleanup_state['result']['problems'])
            except Exception as exc:
                problems.append('firewall-cleanup:' + type(exc).__name__)
        if open_check:
            try:
                check = witnesses.end_check(cid)
                if not clean_check(c6_check_summary(check)):
                    problems.append('firewall-witness-incomplete-or-hit')
            except Exception as exc:
                problems.append('firewall-witness-finalization:' + type(exc).__name__)
        if borrowed and not same_firewall_parent_epoch(witnesses, parent_check, epoch):
            problems.append('firewall-parent-check-changed')
    return {'facts': facts, 'complete': not problems and isinstance(facts, dict), 'problems': problems,
            'rc': rc, 'output': output_meta, 'check': check,
            'coverage': {'parent_check': parent_check, 'pending_parent_verdict': borrowed,
                         'complete': not borrowed and check is not None and clean_check(c6_check_summary(check))},
            'script_sha256': hashlib.sha256(launch.FW_READ_SCRIPT.encode('utf-8')).hexdigest()}


def real_sm_deps(contract: Any, launch: Any, probe: Any, bundle: Any, server: Any, *,
                 environ: Mapping[str, str], proxy_python: Path, owner_home: Path) -> Any:
    """Prepare production collaborators without starting threads or consuming a run."""
    from types import SimpleNamespace
    modules = probe.probe_modules()  # Before any fake/proxy/gate thread.
    rest = probe._mod('v3_rest_for_sm', 'arms/_rest.py')  # Also before the first thread.
    env = dict(environ)

    def setup(contract: Any, launch: Any, static: dict, run: str, resources: list) -> Any:
        status = modules.SL.StatusLog(contract.runs_root / '_a8' / run / 'STATUS')
        status_scope = OwnedStatus(status, run=run, model=modules.RP.PINNED_MODEL)
        resources.append(('status', status_scope.close,
                          lambda row: isinstance(row, dict) and row.get('complete') is True))
        status_scope.start()
        # Only loopback synthetic upstreams are reachable in A8; no provider key is read.
        fd = OwnedUpstream(modules.U, 'deepseek', resources=resources, script_policy=SM_CHAT_POLICY)
        fo = OwnedUpstream(modules.U, 'ollama', resources=resources,
                           tag=static['embed']['tag'], digest=static['embed']['digest'])
        native = launch.NativeEgressWitness()
        if native.jobs is None:
            raise PreflightRefused('required-windows-job')
        wiring = modules.RV.boundary_canaries(launch, contract, env)
        witnesses = launch.Witnesses(contract, native=native, fs=launch.FsWitness(launch.watched_set(contract)),
                                    canaries=wiring['canaries'])
        proxy_dir = static['arm_dir'] / '_proxy'
        proxy_dir.mkdir()
        proxy_run_dir, proxy_config_path = owned_proxy_config_paths(contract, launch, proxy_dir)
        sentinel = proxy_dir / 'sentinel.env'
        with sentinel.open('xb') as stream:
            stream.write(b'DEEPSEEK_API_KEY=nvt3-a8probe-KEYSENTINEL\n')
        cfg = modules.RP.build_config({ARM: {'llm': modules.RP.PINNED_MODEL,
            'llm_transport': modules.RP.PROVIDER_TRANSPORT, 'embeds_via_ollama': True}},
            run_dir=proxy_run_dir, test_upstream={'host': '127.0.0.1', 'port': fd.port, 'tls': False},
            test_ollama_upstream=('127.0.0.1', fo.port))
        if not isinstance(cfg, dict) or Path(cfg.get('run_dir', '')) != proxy_run_dir:
            raise PreflightRefused('proxy-config-run-path-changed')
        proxy = modules.RP.start(contract, python=proxy_python, key_file=sentinel, config=cfg,
            secrets=modules.RP.build_secrets([ARM], roles=('scheduler',), canaries=wiring['proxy']),
            unit=launch.make_unit_dirs(contract, '_a8', '_harness', 'proxy', run),
            parent_env=env, witnesses=witnesses,
            spawn=lambda *args, **options: spawn_sm_proxy(*args, launch=launch, resources=resources, **options))
        port = allocate_sm_server_port(proxy.ports, server.free_port)
        declared = server.server_env(static['bundle']['env'], port=port, data_dir=static['data_dir'],
            llm_base_url=modules.RP.url(proxy, ARM, run, 'u1', suffix='/v1'), llm_model=modules.RP.PINNED_MODEL,
            embed_base_url=modules.RP.url(proxy, ARM, run, 'u1', role='ollama', suffix='/v1'),
            embed_model=static['embed']['tag'], token=proxy.tokens[ARM])
        guarded = GuardedLaunch(launch, bundle, static['bundle']['env'], run=run,
            argv=(str(static['binary']),), expected_by_unit={'u1': declared})
        hooks = SimpleNamespace(gate=None)
        scheduler = modules.SC.Scheduler(contract, proxy.control, status, guarded, modules.SC.SystemClock(), None,
            tag='debug', witnesses=witnesses, parent_env=env, catcher_url=modules.RP.catcher_url(proxy),
            canaries=wiring['scheduler']['canaries'], home_canaries=wiring['scheduler']['home_canaries'], hooks=hooks)
        send_probe = modules.RV.probe(bounded_proxy_post, proxy.ports['scheduler'], proxy.tokens['scheduler'], timeout=2.0)
        model = send_probe().get('model')
        if model != modules.RP.PINNED_MODEL:
            raise PreflightRefused('scheduler-probe-model')
        driver = modules.G.GateDriver(modules.IN.IncidentGate(), calls_path=Path(proxy.run_dir) / 'calls.jsonl',
            status=status, send_probe=send_probe, id_prefix='a8-sm-' + run + '-inc', expected_models=[model])
        status_scope.halt_kind = lambda: driver.halt_kind()
        owned_gate = OwnedGate(driver)
        resources.append(('gate', owned_gate.close, lambda row: isinstance(row, dict) and row.get('complete') is True))
        owned_gate.start()
        hooks.gate = probe.ProbeGate(modules.AB.StopGate(proxy.control, proxy.control.counters()), driver)
        def declare(boot: OwnedBoot, fw: dict) -> bool:
            # Admission is currently refused earlier until projection authority is resolved.
            rule = next((row for row in fw['facts']['rules'] if row.get('profile') == 'Any'), None)
            if rule is None:
                return False
            summary = dict(rule, program=str(static['binary']), profiles_enabled=True,
                           override_allow=False)
            return native.declare_listener(boot.pid, boot.port, reason='supermemory-local', firewall=summary)
        def auth_target() -> tuple:
            return sm_auth_target(run)
        def version(boot: OwnedBoot) -> dict:
            return sm_field({'observed': False}, ok=False)  # Never substitute package metadata for runtime output.
        def observe_routes(*, timeout_s: float) -> dict:
            from types import SimpleNamespace
            held = SimpleNamespace(header=lambda: dict(proxy.control._auth))
            return read_sm_routes(contract, launch, run=run, proxy_dir=Path(proxy.run_dir), upstream=fo, chat_upstream=fd,
                timeout_s=timeout_s, control=lambda **options: sm_request(proxy.ports['control'], 'GET', '/counters',
                    key=held, timeout_s=options['timeout_s']))
        held_control = SimpleNamespace(header=lambda: dict(proxy.control._auth))
        stage = OwnedProxyStage(proxy.ports['control'], held_control, resources)
        def prepare_boot2() -> int:
            restarted_port = allocate_sm_server_port(proxy.ports, server.free_port, prior_port=port)
            guarded.declare_restart(restarted_port, occupied_ports=sm_proxy_port_inventory(proxy.ports))
            return restarted_port
        return SimpleNamespace(gate=hooks.gate, witnesses=witnesses, scheduler=scheduler,
            p3_enabled=True, rest=rest, model=modules.RP.PINNED_MODEL, prepare_routes=stage.prepare,
            observe_routes=observe_routes,
            scheduler_module=modules.SC, guarded=guarded, port=port, prepare_boot2=prepare_boot2,
            read_firewall=lambda unit, binary, parent_check=None: read_firewall_real(contract, launch, modules, scheduler,
                run=run, unit=unit, binary=binary, resources=resources, parent_check=parent_check),
            policy_authority=lambda fw: False, declare_listener=declare,
            ready_ceiling_s=120.0, key_wait_s=10.0, request=sm_request,
            listeners=lambda boot: native_listeners(native, boot), tree=server.tree,
            version=version, auth_target=auth_target, check_summary=c6_check_summary,
            catcher_lines=lambda: len(modules.AC.load_proxy(Path(proxy.run_dir)).catcher))
    return SimpleNamespace(test_only=False, owner_home=Path(owner_home), setup=setup)


def main(argv: Sequence[str] | None = None, *, entry: Callable | None = None) -> int:
    """CLI admits only explicit GO; refusal is rc2 and an incomplete protocol is rc1."""
    import argparse
    parser = argparse.ArgumentParser(description='Guarded Supermemory A8 P0-P2 probe')
    parser.add_argument('--run', required=True)
    parser.add_argument('--go', action='store_true')
    parser.add_argument('--bundle-run', default=BUNDLE_RUN)
    parser.add_argument('--d1-run', default='d1')
    parser.add_argument('--proxy-python', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.go or args.bundle_run != BUNDLE_RUN:
        return 2
    import sys
    if entry is None and (os.environ.get('NEVERTWICE_SANDBOX') == '1'
            or getattr(sys.modules.get('sandbox_guard'), '_MODE', None) == 'sandbox'):
        return 2
    try:
        if entry is None:
            import importlib.util
            def load(name: str, filename: str) -> Any:
                path = Path(__file__).resolve().parent / filename
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                return module
            launch = load('v3_launch_for_sm_entry', 'launch.py')
            bundle = load('v3_sm_bundle', 'sm_bundle.py')
            server = load('v3_sm_server', 'sm_server.py')
            probe = load('v3_probe_a8_for_sm_entry', 'probe_a8.py')
            contract = launch.Contract.default()
            deps = real_sm_deps(contract, launch, probe, bundle, server, environ=os.environ,
                                proxy_python=args.proxy_python, owner_home=Path('C:/Users/Platon'))
            result = run_sm_p0p2(contract, launch, bundle, server, run=args.run, d1_run=args.d1_run, deps=deps)
        else:
            result = entry(args)
        outcome = result.get('outcome') if isinstance(result, dict) else None
        print('Supermemory probe outcome:', outcome if type(outcome) is str and (outcome in {'pass', 'fail'}
              or outcome.startswith(('refused:', 'blocked:'))) else 'invalid')
        return 0 if outcome == 'pass' else 2 if type(outcome) is str and outcome.startswith('refused:') else 1
    except PreflightRefused as exc:
        print('Supermemory static refusal:', str(exc))
        return 2
    except Exception as exc:
        print('Supermemory probe failure:', type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
