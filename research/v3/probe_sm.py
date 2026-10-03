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
            'embed': embed, 'switches': switches, 'data_dir': arm / 'data', 'owner_env_present': owner_present}

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
    except PreflightRefused as exc:
        live_refusal = str(exc)
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
        # P3-P5 fields remain absent until their connected implementation lands.
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

    def __init__(self, modules: Any, kind: str, *, resources: list, tag: str = '', digest: str = '') -> None:
        self.requests, self.traps, self.unknown = [], [], []
        self.script = [{'content': ''}]
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


def read_firewall_real(contract: Any, launch: Any, modules: Any, scheduler: Any, *,
                       run: str, unit: str, binary: Path, resources: list) -> dict:
    """Fresh required-Job readonly child and complete check; never modify policy."""
    witnesses = scheduler.witnesses
    cid = 'a8-sm-' + run + '-' + unit
    boot = None
    open_check = True
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
        witnesses.begin_check(cid)
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
    return {'facts': facts, 'complete': not problems and isinstance(facts, dict), 'problems': problems,
            'rc': rc, 'output': output_meta, 'check': check,
            'script_sha256': hashlib.sha256(launch.FW_READ_SCRIPT.encode('utf-8')).hexdigest()}


def real_sm_deps(contract: Any, launch: Any, probe: Any, bundle: Any, server: Any, *,
                 environ: Mapping[str, str], proxy_python: Path, owner_home: Path) -> Any:
    """Prepare production collaborators without starting threads or consuming a run."""
    from types import SimpleNamespace
    modules = probe.probe_modules()  # Before any fake/proxy/gate thread.
    env = dict(environ)

    def setup(contract: Any, launch: Any, static: dict, run: str, resources: list) -> Any:
        status = modules.SL.StatusLog(contract.runs_root / '_a8' / run / 'STATUS')
        status_scope = OwnedStatus(status, run=run, model=modules.RP.PINNED_MODEL)
        resources.append(('status', status_scope.close,
                          lambda row: isinstance(row, dict) and row.get('complete') is True))
        status_scope.start()
        # Only loopback synthetic upstreams are reachable in A8; no provider key is read.
        fd = OwnedUpstream(modules.U, 'deepseek', resources=resources)
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
        port = _port(server.free_port())
        ports = {proxy.ports['scheduler'], *(proxy.ports.get('arms', {}).get(ARM) or {}).values()}
        if port in ports:
            raise PreflightRefused('server-proxy-port-collision')
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
            raise PreflightRefused('auth-contract-unpinned')  # C6b observed/pinned OpenAPI supplies this.
        def version(boot: OwnedBoot) -> dict:
            return sm_field({'observed': False}, ok=False)  # Never substitute package metadata for runtime output.
        return SimpleNamespace(gate=hooks.gate, witnesses=witnesses, scheduler=scheduler,
            scheduler_module=modules.SC, guarded=guarded, port=port,
            read_firewall=lambda unit, binary: read_firewall_real(contract, launch, modules, scheduler,
                run=run, unit=unit, binary=binary, resources=resources),
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
