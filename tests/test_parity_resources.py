"""Resource preflight contract; native gestures are distinct from patched source fixtures."""
import json
import importlib
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'tools' / 'parity' / 'resources.py'
GESTURE = ['--reserve-fraction', '0.25', '--record-bytes', '256', '--output-dir', '.']


def test_documented_gesture_yields_native_receipt_without_files(tmp_path):
    """Run the real documented argv on this OS; no fixture/native-source override is used."""
    assert CLI.is_file(), 'the resource preflight capability is not implemented'
    # A fresh, byte-identical source copy makes a missing cache guard observable even if the
    # developer's checkout already has a cache. The native APIs and argv remain unchanged.
    directory = tmp_path / 'tools' / 'parity'
    directory.mkdir(parents=True)
    for name in ('resources.py', 'resource_sources.py'):
        shutil.copyfile(CLI.with_name(name), directory / name)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() if p.is_file() else None
              for p in tmp_path.rglob('*')}
    environment = dict(os.environ)
    environment.pop('PYTHONDONTWRITEBYTECODE', None)
    result = subprocess.run([sys.executable, 'tools/parity/resources.py', *GESTURE], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30, env=environment)
    assert result.returncode in (0, 2), result.stderr
    receipt = json.loads(result.stdout if result.returncode == 0 else result.stderr)
    assert receipt['schema'] == 'parity-resource-preflight/v1'
    assert receipt['platform'] == sys.platform
    if result.returncode == 0:
        assert result.stderr == ''
        assert receipt['status'] == 'ok'
        assert receipt['sources']
        assert int(receipt['limits']['output_bytes']) > 0
        names = {row['name'] for row in receipt['sources']}
        assert {'linux': 'MemAvailable', 'darwin': 'HOST_VM_INFO64',
                'win32': 'GlobalMemoryStatusEx'}[sys.platform] in names
        if sys.platform in ('linux', 'darwin'):
            assert 'RLIMIT_AS' in names
        assert receipt['disk']['api'] == ('GetDiskFreeSpaceExW' if sys.platform == 'win32' else 'fstatvfs')
    else:
        assert result.stdout == ''
        assert set(receipt) == {'schema', 'status', 'platform', 'code', 'source', 'cgroup'}
        assert receipt['status'] == 'refused'
        assert receipt['code'] in {'unsupported-platform', 'source-unavailable', 'invalid-source',
            'native-binding-unavailable', 'native-api-failed', 'cgroup-mapping-unsupported',
            'cgroup-v1-memory-availability-unsupported', 'memory-exhausted', 'budget-exhausted',
            'disk-exhausted'}
        assert isinstance(receipt['source'], str) and receipt['source']
    assert {str(p.relative_to(tmp_path)): p.read_bytes() if p.is_file() else None
            for p in tmp_path.rglob('*')} == before


@pytest.fixture
def modules(monkeypatch):
    def load():
        assert CLI.is_file(), 'the resource preflight capability is not implemented'
        monkeypatch.syspath_prepend(str(ROOT))
        monkeypatch.setattr(sys, 'dont_write_bytecode', True)
        resources = importlib.import_module('tools.parity.resources')
        return resources, resources.sources
    return LazyFixture(load)


class LazyFixture:
    """Load in the test body so an absent capability is assertion-red, not a setup error."""
    def __init__(self, load):
        self.load = load

    def __iter__(self):
        return iter(self.load())


def finite(name='physical', location='native', limit=120000, used=20000, facts=None):
    return dict(name=name, location=location, mode='finite', limit_bytes=limit,
                used_bytes=used, available_bytes=limit-used, facts=facts or {})


NOT_APPLICABLE = dict(mode='not-applicable', mount=None, mount_root=None,
                      process_path=None, ancestors=[])


@pytest.fixture
def fake_cli(modules, monkeypatch, tmp_path, capsys):
    return LazyFixture(lambda: make_fake_cli(modules, monkeypatch, tmp_path, capsys))


def make_fake_cli(modules, monkeypatch, tmp_path, capsys):
    resources, sources = modules
    monkeypatch.setattr(sources.sys, 'platform', 'darwin')
    snapshot = dict(sources=[finite()], cgroup=dict(NOT_APPLICABLE), cpu_count=2,
                    disk=dict(api='fstatvfs', directory=str(tmp_path), available_bytes=100000))
    monkeypatch.setattr(sources, 'collect', lambda directory: snapshot)
    monkeypatch.setattr(resources, 'object_overhead', lambda: 112)
    monkeypatch.setattr(resources, 'sampled_at', lambda: '2026-10-15T00:00:00Z')
    monkeypatch.setattr(resources.platform, 'python_version', lambda: '3.12.0')
    # These are forbidden side effects of the real CLI, not expectations on doubles.
    def forbidden(*args, **kwargs):
        raise AssertionError('preflight attempted process/network access')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    def invoke(argv=None):
        code = resources.main(argv or ['--reserve-fraction', '0.5', '--record-bytes',
                                      '16', '--output-dir', str(tmp_path)])
        output = capsys.readouterr()
        assert not (output.out and output.err)
        payload = json.loads(output.out or output.err)
        assert (output.out.endswith('\n') if code == 0 else output.err.endswith('\n'))
        assert list(tmp_path.iterdir()) == []
        return code, payload
    return invoke, snapshot, resources, sources


def test_exact_receipt_and_hand_checked_rounding(fake_cli, tmp_path):
    invoke, snapshot, _, _ = fake_cli
    code, receipt = invoke()
    assert code == 0
    assert receipt == {
        'schema': 'parity-resource-preflight/v1', 'status': 'ok', 'platform': sys.platform,
        'python': '3.12.0', 'sampled_at': '2026-10-15T00:00:00Z',
        'inputs': {'reserve_numerator': '1', 'reserve_denominator': '2', 'record_bytes': '16'},
        'cpu_count': 2, 'cgroup': NOT_APPLICABLE,
        'sources': [dict(name='physical', location='native', mode='finite',
                         limit_bytes='120000', used_bytes='20000', available_bytes='100000', facts={})],
        'disk': {'api': 'fstatvfs', 'directory': str(tmp_path), 'available_bytes': '100000'},
        'limits': {'available_bytes': '100000', 'reserve_bytes': '50000',
                   'usable_bytes': '50000', 'decode_bytes': '25000',
                   'output_buffer_bytes': '25000', 'record_memory_bytes': '192',
                   'object_overhead_bytes': '112', 'blob_bytes_per_worker': '2477',
                   'output_records': '1562', 'output_bytes': '24992',
                   'worker_slots': 2, 'decode_expansion': 5},
        'limitations': ['snapshot-not-reservation', 'decoded-text-only', 'cpu-count-not-quota',
                        'caller-supplied-record-size', 'scan-throughput-unmeasured'],
    }
    snapshot['sources'].append(finite('RLIMIT_AS', 'getrlimit', 1000000, 200000))
    assert invoke()[1]['limits']['available_bytes'] == '100000'


def test_fraction_clause_refuses_through_public_argv(fake_cli):
    for fraction in ['0', '1', '-0.1', 'nan', 'inf', '-inf', '1.01']:
        invoke, _, _, _ = fake_cli
        code, receipt = invoke(['--reserve-fraction', fraction, '--record-bytes', '16', '--output-dir', '.'])
        assert code == 2
        assert receipt == dict(schema='parity-resource-preflight/v1', status='refused',
                              platform=sys.platform, code='invalid-input', source='arguments', cgroup=None)


def test_cli_serializes_each_native_refusal_without_partial_success(fake_cli, monkeypatch):
    invoke, _, _, sources = fake_cli
    for code in ['unsupported-platform', 'source-unavailable', 'invalid-source',
                 'native-binding-unavailable', 'native-api-failed',
                 'cgroup-v1-memory-availability-unsupported', 'cgroup-mapping-unsupported',
                 'memory-exhausted', 'budget-exhausted', 'disk-exhausted']:
        provenance = dict(mode='v1-unsupported', mount=None, mount_root=None,
                          process_path='/legacy', ancestors=[]) if code.startswith('cgroup-v1') else None
        def refuse(directory):
            raise sources.Refused(code, 'native-source', provenance)
        monkeypatch.setattr(sources, 'collect', refuse)
        assert invoke() == (2, dict(schema='parity-resource-preflight/v1', status='refused',
                                   platform=sys.platform, code=code, source='native-source', cgroup=provenance))


def test_invalid_argument_shape_is_typed(fake_cli):
    for argv in [[], ['--unknown'], ['--reserve-fraction', '0.5'],
        ['--reserve-fraction', '0.5', '--record-bytes', '0', '--output-dir', '.'],
        ['--reserve-fraction', '0.5', '--record-bytes', '-1', '--output-dir', '.'],
        ['--reserve-fraction', '0.5', '--record-bytes', str(sys.maxsize+1), '--output-dir', '.'],
        ['--reserve-fraction', '0.5', '--record-bytes', '1.2', '--output-dir', '.']]:
        _, _, resources, _ = fake_cli
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            assert resources.main(argv) == 2
        assert out.getvalue() == ''
        assert json.loads(err.getvalue())['code'] == 'invalid-input'


def test_invalid_observed_cpu_is_not_a_worker_entitlement(fake_cli):
    for key, value in [('cpu_count', 0), ('cpu_count', True),
                                         ('cpu_count', sys.maxsize+1)]:
        invoke, snapshot, _, _ = fake_cli
        snapshot[key] = value
        assert invoke()[1]['code'] == 'invalid-source'


def test_library_record_validation(fake_cli):
    for value in [0, -1, True, 1.5, sys.maxsize+1]:
        _, _, resources, sources = fake_cli
        with pytest.raises(sources.Refused) as refusal:
            resources.calculate(100000, 0.5, value, 2, 100000)
        assert refusal.value.code == 'invalid-input'


def test_library_fraction_capacity_and_disk_types(fake_cli):
    _, _, resources, sources = fake_cli
    for fraction in ('0.5', None, object()):
        with pytest.raises(sources.Refused) as refusal:
            resources.calculate(100000, fraction, 16, 2, 100000)
        assert refusal.value.code == 'invalid-input'
    for available, disk, code in [(0, 100000, 'memory-exhausted'),
            (-1, 100000, 'invalid-source'), (True, 100000, 'invalid-source'),
            (sys.maxsize+1, 100000, 'invalid-source'), (100000, True, 'invalid-source'),
            (100000, 1.5, 'invalid-source'), (100000, -1, 'invalid-source')]:
        with pytest.raises(sources.Refused) as refusal:
            resources.calculate(available, 0.5, 16, 2, disk)
        assert refusal.value.code == code


def test_invalid_policy_never_starts_measurements(fake_cli, monkeypatch):
    invoke, _, _, sources = fake_cli
    def forbidden(directory):
        raise AssertionError('invalid policy reached native measurement')
    monkeypatch.setattr(sources, 'collect', forbidden)
    for option, value in [('--record-bytes', '0'), ('--record-bytes', str(sys.maxsize+1)),
                          ('--reserve-fraction', '0'), ('--reserve-fraction', 'nan')]:
        argv = ['--reserve-fraction', '0.5', '--record-bytes', '16', '--output-dir', '.']
        argv[argv.index(option)+1] = value
        code, receipt = invoke(argv)
        assert code == 2 and receipt['cgroup'] is None


def test_malformed_source_rows_facts_and_unlimited_state_refuse(fake_cli):
    from collections import UserDict
    invoke, snapshot, _, _ = fake_cli
    unlimited = dict(name='RLIMIT_AS', location='getrlimit(RLIMIT_AS)', mode='unlimited',
                     limit_bytes=None, used_bytes=None, available_bytes=None, facts={})
    malformed = [None, (), [], (finite(),), [UserDict(finite())],
        [dict(finite(), extra='field')], [dict(finite(), name=123)], [dict(finite(), name='')],
        [dict(finite(), location=123)], [dict(finite(), location='')],
        [dict(finite(), facts=[])], [dict(finite(), facts={123: 1})], [dict(finite(), facts={'': 1})],
        [dict(finite(), facts={'free_pages': True})], [unlimited],
        [finite(), dict(unlimited, limit_bytes=1)], [finite(), dict(unlimited, available_bytes=1)],
        [finite(), dict(unlimited, name='cgroup-v2')]]
    for rows in malformed:
        snapshot['sources'] = rows
        code, receipt = invoke()
        assert code == 2 and receipt['code'] == 'invalid-source', rows


def test_cgroup_shape_and_ancestor_validation_are_typed(fake_cli, monkeypatch):
    from collections import UserDict, UserList
    import copy
    invoke, snapshot, _, sources = fake_cli
    # Unknown malformed provenance must not itself leak an invalid shape into a refusal.
    for value in [UserDict(NOT_APPLICABLE), {}, 'bad', dict(NOT_APPLICABLE, mount='/unexpected'),
                  dict(NOT_APPLICABLE, ancestors=['/unexpected'])]:
        snapshot['cgroup'] = value
        code, receipt = invoke()
        assert code == 2 and receipt['code'] == 'invalid-source'
        assert receipt['cgroup'] is None
    monkeypatch.setattr(sources.sys, 'platform', 'linux')
    proc_fixture(monkeypatch, sources)
    rows, original = sources.linux_memory()
    snapshot['sources'] = rows
    for change in [dict(mount_root='/hidden'), dict(ancestors=tuple(original['ancestors'])),
                   dict(ancestors=UserList(original['ancestors'])),
                   dict(ancestors=[]), dict(process_path=123), dict(mount=123),
                   dict(ancestors=[original['ancestors'][1], original['ancestors'][0], *original['ancestors'][2:]]),
                   dict(ancestors=['/wrong', *original['ancestors'][1:]]), dict(mount='/other')]:
        snapshot['cgroup'] = dict(copy.deepcopy(original), **change)
        code, receipt = invoke()
        assert code == 2 and receipt['code'] == 'invalid-source', change


def test_bad_disk_snapshot_is_a_typed_refusal(fake_cli):
    invoke, snapshot, _, _ = fake_cli
    original = dict(snapshot['disk'])
    for disk in [None, [], dict(original, extra=1), dict(original, api='unknown'),
                 dict(original, directory=123), dict(original, directory='')]:
        snapshot['disk'] = disk
        code, receipt = invoke()
        assert code == 2 and receipt['code'] == 'invalid-source', disk


def test_every_finite_source_is_validated_before_minimum(fake_cli):
    for change in [dict(limit_bytes=True), dict(used_bytes=-1),
        dict(available_bytes=100001), dict(limit_bytes=sys.maxsize+1),
        dict(available_bytes=1.25), dict(mode='unknown')]:
        invoke, snapshot, _, _ = fake_cli
        row = finite(limit=1000000, used=200000)
        row.update(change)
        snapshot['sources'].append(row)
        code, receipt = invoke()
        assert code == 2
        assert receipt['code'] == 'invalid-source'


def test_exhaustion_refuses_without_partial_receipt(fake_cli):
    for available, record, disk, code in [
        (0, 16, 100000, 'memory-exhausted'), (100, 16, 100000, 'budget-exhausted'),
        (100000, 10000, 100000, 'budget-exhausted'),
        (100000, 16, 24991, 'disk-exhausted')]:
        invoke, snapshot, _, _ = fake_cli
        snapshot['sources'] = [finite(limit=available+10, used=10)]
        snapshot['disk']['available_bytes'] = disk
        rc, receipt = invoke(['--reserve-fraction', '0.5', '--record-bytes', str(record), '--output-dir', '.'])
        assert rc == 2
        assert receipt['code'] == code
        assert set(receipt) == {'schema', 'status', 'platform', 'code', 'source', 'cgroup'}


def test_disk_equality_and_odd_partition_rounding(fake_cli):
    invoke, snapshot, _, _ = fake_cli
    snapshot['disk']['available_bytes'] = 24992
    assert invoke()[0] == 0
    snapshot['sources'] = [finite(limit=100003, used=0)]
    limits = invoke()[1]['limits']
    assert (limits['usable_bytes'], limits['reserve_bytes'], limits['decode_bytes'],
            limits['output_buffer_bytes']) == ('50001', '50002', '25000', '25001')


def proc_fixture(monkeypatch, sources, *, path='/parent/child', root='/', controllers='cpu memory',
                 groups=None, rows=None):
    files = {
        '/proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 800 kB\n',
        '/proc/self/cgroup': groups if groups is not None else f'0::{path}\n',
        '/proc/self/mountinfo': f'29 23 0:26 {root} /sys/fs/cgroup rw - cgroup2 cgroup rw\n',
        '/proc/cgroups': '#subsys_name hierarchy num_cgroups enabled\nmemory 0 1 1\n',
        '/sys/fs/cgroup/cgroup.controllers': controllers,
        '/sys/fs/cgroup/parent/child/memory.max': 'max\n',
        '/sys/fs/cgroup/parent/child/memory.current': '100\n',
        '/sys/fs/cgroup/parent/memory.max': '100000\n',
        '/sys/fs/cgroup/parent/memory.current': '20000\n',
    }
    files.update(rows or {})
    def read(path, source, *, missing_ok=False):
        if str(path) not in files:
            if missing_ok:
                return None
            raise sources.Refused('source-unavailable', source)
        return files[str(path)]
    monkeypatch.setattr(sources, 'read_text', read)
    return files


def test_linux_every_ancestor_and_unlimited_provenance(modules, monkeypatch):
    _, sources = modules
    proc_fixture(monkeypatch, sources)
    rows, provenance = sources.linux_memory()
    assert provenance == dict(mode='v2', mount='/sys/fs/cgroup', mount_root='/',
                             process_path='/parent/child', ancestors=[
                                 '/sys/fs/cgroup/parent/child', '/sys/fs/cgroup/parent', '/sys/fs/cgroup'])
    assert [(r['mode'], r['available_bytes']) for r in rows] == [
        ('finite', 819200), ('unlimited', None), ('finite', 80000), ('unlimited', None)]
    assert rows[-1] == dict(name='cgroup-v2-root', location='/sys/fs/cgroup', mode='unlimited',
                           limit_bytes=None, used_bytes=None, available_bytes=None, facts={})
    assert [r['location'] for r in rows[1:]] == provenance['ancestors']


def test_linux_minimum_and_path_identity_use_public_argv(fake_cli, monkeypatch):
    invoke, snapshot, _, sources = fake_cli
    monkeypatch.setattr(sources.sys, 'platform', 'linux')
    files = proc_fixture(monkeypatch, sources)
    snapshot['sources'], snapshot['cgroup'] = sources.linux_memory()
    first = invoke()[1]
    assert first['limits']['available_bytes'] == '80000'
    files['/proc/self/cgroup'] = '0::/parent/other\n'
    files['/sys/fs/cgroup/parent/other/memory.max'] = 'max'
    files['/sys/fs/cgroup/parent/other/memory.current'] = '100'
    snapshot['sources'], snapshot['cgroup'] = sources.linux_memory()
    second = invoke()[1]
    assert first['limits'] == second['limits']
    assert first['cgroup'] != second['cgroup']


def test_cgroup_kernel_path_rejects_traversal_and_controls(modules, monkeypatch):
    for path in ['relative', '/../escape', '/a/../b', '/a//b', '/a/./b', '/a\x00b', '/a\tb', '/a\x7fb', '/a\x85b']:
        _, sources = modules
        proc_fixture(monkeypatch, sources, path=path)
        with pytest.raises(sources.Refused) as refusal:
            sources.linux_memory()
        assert refusal.value.code == 'cgroup-mapping-unsupported'
    assert sources.kernel_path('/parent/雪') == '/parent/雪'


def test_source_text_protocols_and_no_controller_mode(modules, monkeypatch):
    _, sources = modules
    for value in ('01', '+1', '-1', '1.0', 'bad'):
        with pytest.raises(sources.Refused, match='invalid-source'):
            sources.unsigned(value, 'fixture')
    assert sources.mount_path(r'/a\134b') == '/a\\b'
    with pytest.raises(sources.Refused, match='cgroup-mapping-unsupported'):
        sources.mount_path(r'/a\unknown')
    proc_fixture(monkeypatch, sources, groups='1:cpu:/\n', rows={
        '/proc/cgroups': '#subsys_name hierarchy num_cgroups enabled\nmemory 0 1 0\n'})
    rows, provenance = sources.linux_memory()
    assert len(rows) == 1 and provenance['mode'] == 'none'
    for function in (sources.collect, sources.disk_capacity, lambda directory: sources.virtual_bytes()):
        monkeypatch.setattr(sources.sys, 'platform', 'unknown')
        with pytest.raises(sources.Refused, match='unsupported-platform'):
            function('.')


def test_read_text_distinguishes_missing_optional_from_failed_evidence(modules, monkeypatch):
    _, sources = modules
    for failure, code in [(FileNotFoundError('opaque-read-error'), 'source-unavailable'),
                          (PermissionError('opaque-read-error'), 'source-unavailable'),
                          (UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'bad'), 'invalid-source')]:
        def fail(*args, **kwargs):
            raise failure
        monkeypatch.setattr(sources.Path, 'read_text', fail)
        with pytest.raises(sources.Refused) as refusal:
            sources.read_text('/fixture', 'native-read')
        assert refusal.value.code == code
        assert 'opaque-read-error' not in str(refusal.value)
        if type(failure) is FileNotFoundError:
            assert sources.read_text('/fixture', 'native-read', missing_ok=True) is None
        else:
            with pytest.raises(sources.Refused) as refusal:
                sources.read_text('/fixture', 'native-read', missing_ok=True)
            assert refusal.value.code == code


def test_snapshot_routes_native_sources_and_address_limits_by_os(modules, monkeypatch, tmp_path):
    _, sources = modules
    monkeypatch.setattr(sources, 'address_space_limit', lambda: None)
    proc_fixture(monkeypatch, sources)
    monkeypatch.setattr(sources.sys, 'platform', 'linux')
    linux = sources.collect(str(tmp_path))
    assert [row['name'] for row in linux['sources']] == [
        'MemAvailable', 'cgroup-v2', 'cgroup-v2', 'cgroup-v2-root', 'RLIMIT_AS']
    darwin_fixture(monkeypatch, sources)
    monkeypatch.setattr(sources.sys, 'platform', 'darwin')
    darwin = sources.collect(str(tmp_path))
    assert [row['name'] for row in darwin['sources']] == ['HOST_VM_INFO64', 'RLIMIT_AS']
    windows_fixture(monkeypatch, sources)
    monkeypatch.setattr(sources.sys, 'platform', 'win32')
    windows = sources.collect(str(tmp_path))
    assert [row['name'] for row in windows['sources']] == ['GlobalMemoryStatusEx']
    assert windows['cgroup'] == NOT_APPLICABLE


def test_linux_bad_sources_refuse(modules, monkeypatch):
    for where, text, code in [
        ('/proc/meminfo', 'MemTotal: 1 kB\nMemAvailable: 2 kB', 'invalid-source'),
        ('/proc/meminfo', 'MemTotal: 1 kB', 'invalid-source'),
        ('/proc/meminfo', 'MemTotal: 2 kB\nMemAvailable: 1 MB', 'invalid-source'),
        ('/proc/meminfo', 'MemTotal: 2 kB\nMemAvailable: 0 kB', 'memory-exhausted'),
        ('/proc/meminfo', 'MemTotal: 2 kB\nMemTotal: 2 kB\nMemAvailable: 1 kB', 'invalid-source'),
        ('/sys/fs/cgroup/parent/memory.max', 'bad', 'invalid-source'),
        ('/sys/fs/cgroup/parent/memory.current', '100001', 'memory-exhausted'),
        ('/sys/fs/cgroup/parent/child/memory.current', '-1', 'invalid-source'),
        ('/proc/self/cgroup', '0::/a\n0::/b\n', 'cgroup-mapping-unsupported'),
        ('/proc/self/cgroup', 'bad', 'cgroup-mapping-unsupported'),
        ('/proc/self/cgroup', '0:cpu', 'cgroup-mapping-unsupported'),
        ('/proc/self/cgroup', 'x:memory:/', 'cgroup-mapping-unsupported'),
        ('/proc/self/cgroup', 'x::/', 'cgroup-mapping-unsupported'),
        ('/proc/self/mountinfo', 'no separator', 'cgroup-mapping-unsupported'),
        ('/proc/self/mountinfo', '29 23 0:26 / /sys/fs/cgroup - cgroup2 cgroup rw', 'cgroup-mapping-unsupported'),
        ('/proc/self/mountinfo', '29 23 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup', 'cgroup-mapping-unsupported'),
        ('/proc/self/mountinfo', '29 23 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n30 23 0:26 / /other rw - cgroup2 cgroup rw', 'cgroup-mapping-unsupported'),
        ('/sys/fs/cgroup/cgroup.controllers', 'cpu memory memory', 'invalid-source'),
        ('/sys/fs/cgroup/cgroup.controllers', 'cpu memory bad!', 'invalid-source'),
        ('/proc/self/mountinfo', '29 23 0:26 /container /sys/fs/cgroup rw - cgroup2 cgroup rw',
         'cgroup-mapping-unsupported')]:
        _, sources = modules
        proc_fixture(monkeypatch, sources, rows={where: text})
        with pytest.raises(sources.Refused) as refusal:
            sources.linux_memory()
        assert refusal.value.code == code


def test_linux_missing_nonroot_or_controller_evidence_refuses(modules, monkeypatch):
    _, sources = modules
    files = proc_fixture(monkeypatch, sources)
    del files['/sys/fs/cgroup/parent/child/memory.current']
    with pytest.raises(sources.Refused, match='source-unavailable'):
        sources.linux_memory()
    files = proc_fixture(monkeypatch, sources)
    del files['/sys/fs/cgroup/cgroup.controllers']
    with pytest.raises(sources.Refused, match='source-unavailable'):
        sources.linux_memory()


def test_visible_namespace_root_limit_cannot_be_silently_ignored(modules, monkeypatch):
    _, sources = modules
    proc_fixture(monkeypatch, sources, rows={
        '/sys/fs/cgroup/memory.max': '50000', '/sys/fs/cgroup/memory.current': '20000'})
    rows, provenance = sources.linux_memory()
    assert rows[-1]['location'] == '/sys/fs/cgroup'
    assert rows[-1]['mode'] == 'finite'
    assert rows[-1]['available_bytes'] == 30000
    assert provenance['ancestors'][-1] == '/sys/fs/cgroup'
    proc_fixture(monkeypatch, sources, controllers='cpu', rows={
        '/sys/fs/cgroup/memory.max': '50000', '/sys/fs/cgroup/memory.current': '20000'})
    assert sources.linux_memory()[0][-1]['available_bytes'] == 30000
    proc_fixture(monkeypatch, sources, rows={'/sys/fs/cgroup/memory.max': '50000'})
    with pytest.raises(sources.Refused, match='source-unavailable'):
        sources.linux_memory()


def test_no_controller_and_v1_preserve_distinct_modes(modules, monkeypatch):
    _, sources = modules
    proc_fixture(monkeypatch, sources, controllers='cpu io')
    rows, provenance = sources.linux_memory()
    assert len(rows) == 1
    assert provenance == dict(mode='none', mount=None, mount_root=None, process_path=None, ancestors=[])
    proc_fixture(monkeypatch, sources, groups='5:cpu,memory:/legacy\n0::/\n')
    with pytest.raises(sources.Refused) as refusal:
        sources.linux_memory()
    assert refusal.value.code == 'cgroup-v1-memory-availability-unsupported'
    assert refusal.value.cgroup == dict(mode='v1-unsupported', mount=None, mount_root=None,
                                       process_path='/legacy', ancestors=[])


def test_finite_address_limit_subtracts_virtual_not_resident_space(modules, monkeypatch):
    _, sources = modules
    monkeypatch.setattr(sources, 'address_space_limit', lambda: 100000)
    monkeypatch.setattr(sources, 'virtual_bytes', lambda: 30000)
    row = sources.posix_address_space()
    assert (row['limit_bytes'], row['used_bytes'], row['available_bytes']) == (100000, 30000, 70000)
    monkeypatch.setattr(sources, 'virtual_bytes', lambda: 100001)
    with pytest.raises(sources.Refused, match='memory-exhausted'):
        sources.posix_address_space()
    monkeypatch.setattr(sources, 'address_space_limit', lambda: None)
    assert sources.posix_address_space()['used_bytes'] is None


def test_linux_statm_uses_total_pages_and_measured_page_size(modules, monkeypatch):
    _, sources = modules
    monkeypatch.setattr(sources.sys, 'platform', 'linux')
    proc_fixture(monkeypatch, sources, rows={'/proc/self/statm': '1001 17 3 4 5 6 7\n'})
    monkeypatch.setattr(sources.os, 'sysconf', lambda key: 4096, raising=False)
    monkeypatch.setattr(sources, 'address_space_limit', lambda: 10000000)
    assert sources.virtual_bytes() == 4100096
    row = sources.posix_address_space()
    assert (row['location'], row['used_bytes'], row['available_bytes']) == (
        '/proc/self/statm', 4100096, 5899904)


def test_linux_statm_malformed_zero_or_overflow_refuses(modules, monkeypatch):
    for text, page in [('garbage', 4096), ('-1 2 3 4 5 6 7', 4096),
        ('1001', 4096), ('1001 2 3 4 5 6 7 8', 4096),
        ('0 2 3 4 5 6 7', 4096), ('1001 17 3 4 5 6 7', 0),
        (f'{sys.maxsize} 2 3 4 5 6 7', 4096)]:
        _, sources = modules
        monkeypatch.setattr(sources.sys, 'platform', 'linux')
        proc_fixture(monkeypatch, sources, rows={'/proc/self/statm': text})
        monkeypatch.setattr(sources.os, 'sysconf', lambda key: page, raising=False)
        with pytest.raises(sources.Refused, match='invalid-source'):
            sources.virtual_bytes()


def test_linux_statm_missing_refuses(modules, monkeypatch):
    _, sources = modules
    monkeypatch.setattr(sources.sys, 'platform', 'linux')
    proc_fixture(monkeypatch, sources)
    with pytest.raises(sources.Refused, match='source-unavailable'):
        sources.virtual_bytes()


def test_posix_disk_reads_available_not_free_and_does_not_create(modules, monkeypatch, tmp_path):
    _, sources = modules
    from types import SimpleNamespace
    monkeypatch.setattr(sources.sys, 'platform', 'darwin')
    monkeypatch.setattr(sources.os, 'fstatvfs', lambda fd: SimpleNamespace(f_bavail=7, f_bfree=99, f_frsize=4096), raising=False)
    monkeypatch.setattr(sources.os, 'open', lambda path, flags: 11)
    identity = tmp_path.stat()
    monkeypatch.setattr(sources.os, 'fstat', lambda fd: identity)
    closed = []
    monkeypatch.setattr(sources.os, 'close', closed.append)
    disk = sources.disk_capacity(str(tmp_path))
    assert disk == dict(api='fstatvfs', directory=str(tmp_path.resolve()), available_bytes=28672)
    assert closed == [11]
    with pytest.raises(sources.Refused):
        sources.disk_capacity(str(tmp_path/'does-not-exist'))
    assert list(tmp_path.iterdir()) == []


def test_posix_output_swap_refuses_and_closes_descriptor(modules, monkeypatch, tmp_path):
    _, sources = modules
    monkeypatch.setattr(sources.sys, 'platform', 'darwin')
    directory = tmp_path/'chosen'
    directory.mkdir()
    real_open, real_close = os.open, os.close
    opened, closed = [], []
    def swap(path, flags):
        fd = real_open(path, flags)
        opened.append(fd)
        directory.rename(tmp_path/'original')
        directory.mkdir()
        return fd
    def close(fd):
        closed.append(fd)
        real_close(fd)
    monkeypatch.setattr(sources.os, 'open', swap)
    monkeypatch.setattr(sources.os, 'close', close)
    with pytest.raises(sources.Refused, match='invalid-source'):
        sources.disk_capacity(str(directory))
    assert opened and closed == opened


def test_posix_descriptor_type_identity_and_close_errors(modules, monkeypatch, tmp_path):
    _, sources = modules
    from types import SimpleNamespace
    import stat
    monkeypatch.setattr(sources.sys, 'platform', 'darwin')
    original = dict(st_mode=stat.S_IFDIR|0o700, st_dev=1, st_ino=2)
    for field, value, when in [('st_dev', 3, 'opened'), ('st_ino', 3, 'opened'),
                               ('st_mode', stat.S_IFREG|0o600, 'opened'),
                               ('st_dev', 3, 'after'), ('st_ino', 3, 'after')]:
        opened = dict(original)
        after = dict(original)
        (opened if when == 'opened' else after)[field] = value
        # Match the post-open target when testing the original-identity comparison alone.
        if when == 'opened':
            after.update(opened)
        class Resolved:
            def stat(self):
                return SimpleNamespace(**after)
            def __str__(self):
                return '/chosen'
        monkeypatch.setattr(sources, 'output_directory', lambda path: (Resolved(), SimpleNamespace(**original)))
        monkeypatch.setattr(sources.os, 'open', lambda *args: 11)
        monkeypatch.setattr(sources.os, 'fstat', lambda fd: SimpleNamespace(**opened))
        monkeypatch.setattr(sources.os, 'fstatvfs', lambda fd: SimpleNamespace(f_bavail=7, f_frsize=4096), raising=False)
        closed = []
        monkeypatch.setattr(sources.os, 'close', closed.append)
        with pytest.raises(sources.Refused, match='invalid-source'):
            sources.disk_capacity('.')
        assert closed == [11]
    monkeypatch.setattr(sources.os, 'fstat', lambda fd: SimpleNamespace(**original))
    after.update(original)
    def close_failure(fd):
        raise OSError('close failed')
    monkeypatch.setattr(sources.os, 'close', close_failure)
    with pytest.raises(sources.Refused, match='source-unavailable'):
        sources.disk_capacity('.')


def test_output_file_and_symlink_to_file_refuse_on_each_native_route(modules, monkeypatch, tmp_path, capsys):
    resources, sources = modules
    regular = tmp_path/'ordinary-file'
    regular.write_text('preserve this')
    link = tmp_path/'link-to-file'
    link.symlink_to(regular)
    def forbidden(*args, **kwargs):
        raise AssertionError('a nondirectory reached the volume API')
    for platform_name in ('darwin', 'win32'):
        monkeypatch.setattr(sources.sys, 'platform', platform_name)
        monkeypatch.setattr(sources, 'windows_bindings', forbidden)
        monkeypatch.setattr(sources.os, 'fstatvfs', forbidden, raising=False)
        # Real directory resolution/type validation, with no memory-source or volume API shortcut.
        monkeypatch.setattr(sources, 'collect', lambda directory: dict(
            sources=[finite()], cgroup=NOT_APPLICABLE, cpu_count=2,
            disk=sources.disk_capacity(directory)))
        for path in (regular, link):
            assert resources.main(['--reserve-fraction', '0.25', '--record-bytes', '256',
                                   '--output-dir', str(path)]) == 2
            output = capsys.readouterr()
            assert output.out == ''
            assert json.loads(output.err)['code'] == 'invalid-input'
            assert json.loads(output.err)['source'] == 'output-directory'
        assert regular.read_text() == 'preserve this'
        assert set(p.name for p in tmp_path.iterdir()) == {'ordinary-file', 'link-to-file'}


class NativeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def darwin_fixture(monkeypatch, sources, *, status=0, total=1000000, free=10,
                   inactive=20, speculative=7, count=38, deallocate=0, task_count=36, virtual=123456,
                   host_port=7, sysctl_status=0, sysctl_size=8):
    import ctypes
    from types import SimpleNamespace
    calls = []
    def statistics(host, flavor, ptr, size):
        assert (host, flavor) == (7, 4)
        stats = ctypes.cast(ptr, ctypes.POINTER(sources.VMStatistics64)).contents
        stats.free_count, stats.inactive_count, stats.speculative_count = free, inactive, speculative
        ctypes.cast(size, ctypes.POINTER(ctypes.c_uint32)).contents.value = count
        return status
    def memsize(name, ptr, size, new, newlen):
        assert name == b'hw.memsize' and new is None and newlen == 0
        ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint64)).contents.value = total
        ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = sysctl_size
        return sysctl_status
    def release(task, host):
        calls.append((task, host))
        return deallocate
    def task_info(task, flavor, ptr, size):
        assert (task, flavor) == (11, 22)
        ctypes.cast(ptr, ctypes.POINTER(sources.TaskVMInfo)).contents.virtual_size = virtual
        ctypes.cast(size, ctypes.POINTER(ctypes.c_uint32)).contents.value = task_count
        return status
    lib = SimpleNamespace(mach_host_self=NativeFunction(lambda: host_port),
                          host_statistics64=NativeFunction(statistics),
                          sysctlbyname=NativeFunction(memsize),
                          mach_port_deallocate=NativeFunction(release),
                          task_info=NativeFunction(task_info))
    monkeypatch.setattr(sources, 'darwin_bindings', lambda: (lib, 4096, 11))
    return calls


def test_darwin_speculative_is_already_free_and_port_is_released(modules, monkeypatch):
    _, sources = modules
    calls = darwin_fixture(monkeypatch, sources)
    row = sources.darwin_memory()
    assert row == finite('HOST_VM_INFO64', 'host_statistics64', 1000000, 877120,
                         {'free_pages': 10, 'inactive_pages': 20, 'speculative_pages': 7, 'page_bytes': 4096})
    assert calls == [(11, 7)]
    assert sources.darwin_virtual_bytes() == 123456
    import ctypes
    assert ctypes.sizeof(sources.TaskVMInfo) == 144


def test_darwin_native_return_codes_and_counts_fail_closed(modules, monkeypatch):
    for kwargs, code in [({'status': 1}, 'native-api-failed'),
        ({'count': 1}, 'invalid-source'), ({'total': 100}, 'invalid-source'),
        ({'count': 100}, 'invalid-source'), ({'sysctl_status': 1}, 'native-api-failed'),
        ({'sysctl_size': 7}, 'invalid-source'),
        ({'free': 0, 'inactive': 0, 'speculative': 0}, 'memory-exhausted'),
        ({'speculative': 11}, 'invalid-source'), ({'deallocate': 1}, 'native-api-failed')]:
        _, sources = modules
        calls = darwin_fixture(monkeypatch, sources, **kwargs)
        with pytest.raises(sources.Refused) as refusal:
            sources.darwin_memory()
        assert refusal.value.code == code
        assert calls == [(11, 7)]
    calls = darwin_fixture(monkeypatch, sources, host_port=0)
    with pytest.raises(sources.Refused, match='native-api-failed'):
        sources.darwin_memory()
    assert calls == []


def test_darwin_virtual_source_status_count_and_value_checked(modules, monkeypatch):
    _, sources = modules
    for kwargs, code in [({'status': 1}, 'native-api-failed'),
                         ({'task_count': 1}, 'invalid-source'), ({'virtual': 0}, 'invalid-source')]:
        darwin_fixture(monkeypatch, sources, **kwargs)
        with pytest.raises(sources.Refused) as refusal:
            sources.darwin_virtual_bytes()
        assert refusal.value.code == code


WINDOWS_GUID_ROOT = '\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}\\'
WINDOWS_GUID_DIRECTORY = WINDOWS_GUID_ROOT + 'chosen'


def windows_fixture(monkeypatch, sources, *, memory_status=1, disk_status=1,
                    total=120000, available=100000, handle=99, information_status=1,
                    directory_attribute=16, path_status=None, required_size=None,
                    final_path=WINDOWS_GUID_DIRECTORY, close_status=1, after_open=None):
    import ctypes
    from types import SimpleNamespace
    def memory(ptr):
        stats = ctypes.cast(ptr, ctypes.POINTER(sources.MemoryStatusEx)).contents
        assert stats.dwLength == 64
        stats.ullTotalPhys, stats.ullAvailPhys = total, available
        return memory_status
    events = []
    queries = 0
    def disk(directory, avail, total, free):
        events.append(('volume', directory))
        # A mutable alias would identify another volume after the deliberate swap.
        ctypes.cast(avail, ctypes.POINTER(ctypes.c_uint64)).contents.value = (
            1234 if directory == WINDOWS_GUID_ROOT else 999999)
        return disk_status
    def open_directory(path, access, sharing, security, creation, flags, template):
        assert access == 0 and sharing == 3 and creation == 3 and flags == 0x02000000
        assert security is None and template is None
        events.append(('open', path))
        if after_open:
            after_open()
        return handle
    def information(opened, ptr):
        assert opened == handle
        ctypes.cast(ptr, ctypes.POINTER(sources.FileInformation)).contents.dwFileAttributes = directory_attribute
        return information_status
    def final_name(opened, buffer, size, flags):
        nonlocal queries
        assert opened == handle and flags == 1
        if not size:
            queries += 1
            # A failed size query can recover later; the first failure must still be refused.
            return len(final_path)+1 if required_size is None or queries > 1 else required_size
        if path_status is not None:
            buffer.value = final_path
            return path_status
        buffer.value = final_path
        return len(final_path)
    def close(opened):
        events.append(('close', opened))
        return close_status
    lib = SimpleNamespace(GlobalMemoryStatusEx=NativeFunction(memory),
                          GetDiskFreeSpaceExW=NativeFunction(disk),
                          CreateFileW=NativeFunction(open_directory),
                          GetFileInformationByHandle=NativeFunction(information),
                          GetFinalPathNameByHandleW=NativeFunction(final_name),
                          CloseHandle=NativeFunction(close))
    monkeypatch.setattr(sources, 'windows_bindings', lambda: lib)
    return events


def test_windows_native_memory_and_caller_volume_capacity(modules, monkeypatch, tmp_path):
    _, sources = modules
    windows_fixture(monkeypatch, sources)
    assert sources.windows_memory() == finite('GlobalMemoryStatusEx', 'GlobalMemoryStatusEx')
    assert sources.windows_disk(str(tmp_path)) == dict(api='GetDiskFreeSpaceExW',
        directory=WINDOWS_GUID_DIRECTORY, available_bytes=1234)


def test_windows_memory_status_and_totals_checked(modules, monkeypatch):
    for kwargs, code in [({'memory_status': 0}, 'native-api-failed'),
        ({'available': 120001}, 'invalid-source'), ({'available': 0}, 'memory-exhausted')]:
        _, sources = modules
        windows_fixture(monkeypatch, sources, **kwargs)
        with pytest.raises(sources.Refused) as refusal:
            sources.windows_memory()
        assert refusal.value.code == code


def test_windows_disk_status_checked(modules, monkeypatch, tmp_path):
    _, sources = modules
    windows_fixture(monkeypatch, sources, disk_status=0)
    with pytest.raises(sources.Refused, match='native-api-failed'):
        sources.windows_disk(str(tmp_path))


def test_windows_held_handle_binds_volume_despite_alias_swap(modules, monkeypatch, tmp_path):
    _, sources = modules
    directory = tmp_path/'alias'
    directory.mkdir()
    def swap():
        directory.rename(tmp_path/'original')
        directory.mkdir()
    events = windows_fixture(monkeypatch, sources, after_open=swap)
    disk = sources.windows_disk(str(directory))
    assert disk['directory'] == WINDOWS_GUID_DIRECTORY
    assert disk['available_bytes'] == 1234
    assert events[-2:] == [('volume', WINDOWS_GUID_ROOT), ('close', 99)]


def test_windows_directory_handle_and_final_path_fail_closed(modules, monkeypatch, tmp_path):
    _, sources = modules
    for kwargs, code in [({'handle': None}, 'native-api-failed'),
        ({'handle': -1}, 'native-api-failed'), ({'information_status': 0}, 'native-api-failed'),
        ({'directory_attribute': 0}, 'invalid-input'), ({'required_size': 0}, 'native-api-failed'),
        ({'required_size': 32769}, 'invalid-source'), ({'path_status': 0}, 'native-api-failed'),
        ({'path_status': 99999}, 'invalid-source'), ({'final_path': 'C:\\mutable\\alias'}, 'invalid-source'),
        ({'close_status': 0}, 'native-api-failed')]:
        events = windows_fixture(monkeypatch, sources, **kwargs)
        with pytest.raises(sources.Refused) as refusal:
            sources.windows_disk(str(tmp_path))
        assert refusal.value.code == code, kwargs
        if kwargs.get('handle', 99) not in (None, -1):
            assert events[-1] == ('close', 99)


def test_native_bindings_and_platform_cannot_fall_back_to_subprocess(modules, monkeypatch):
    _, sources = modules
    def unavailable(*args, **kwargs):
        raise OSError('unavailable')
    monkeypatch.setattr(sources.ctypes, 'CDLL', unavailable)
    with pytest.raises(sources.Refused, match='native-binding-unavailable'):
        sources.darwin_bindings()
    monkeypatch.setattr(sources.ctypes, 'WinDLL', unavailable, raising=False)
    with pytest.raises(sources.Refused, match='native-binding-unavailable'):
        sources.windows_bindings()
    monkeypatch.setattr(sources.sys, 'platform', 'unknown-os')
    with pytest.raises(sources.Refused, match='unsupported-platform'):
        sources.collect('.')


def test_binding_construction_declares_exact_abi(modules, monkeypatch):
    for name in ['mach_host_self', 'host_statistics64', 'sysctlbyname',
        'mach_port_deallocate', 'task_info', 'GlobalMemoryStatusEx', 'GetDiskFreeSpaceExW',
        'CreateFileW', 'GetFileInformationByHandle', 'GetFinalPathNameByHandleW', 'CloseHandle']:
        _, sources = modules
        import ctypes as c
        from types import SimpleNamespace
        signatures = {
            'mach_host_self': ([], c.c_uint32),
            'host_statistics64': ([c.c_uint32, c.c_int, c.c_void_p, c.POINTER(c.c_uint32)], c.c_int),
            'sysctlbyname': ([c.c_char_p, c.c_void_p, c.POINTER(c.c_size_t), c.c_void_p, c.c_size_t], c.c_int),
            'mach_port_deallocate': ([c.c_uint32, c.c_uint32], c.c_int),
            'task_info': ([c.c_uint32, c.c_int, c.c_void_p, c.POINTER(c.c_uint32)], c.c_int),
            'GlobalMemoryStatusEx': ([c.POINTER(sources.MemoryStatusEx)], c.c_int),
            'GetDiskFreeSpaceExW': ([c.c_wchar_p, c.POINTER(c.c_uint64), c.POINTER(c.c_uint64),
                                   c.POINTER(c.c_uint64)], c.c_int),
            'CreateFileW': ([c.c_wchar_p, c.c_uint32, c.c_uint32, c.c_void_p,
                            c.c_uint32, c.c_uint32, c.c_void_p], c.c_void_p),
            'GetFileInformationByHandle': ([c.c_void_p, c.POINTER(sources.FileInformation)], c.c_int),
            'GetFinalPathNameByHandleW': ([c.c_void_p, c.c_wchar_p, c.c_uint32, c.c_uint32], c.c_uint32),
            'CloseHandle': ([c.c_void_p], c.c_int),
        }
        lib = SimpleNamespace(**{key: NativeFunction(lambda *args: 0) for key in signatures})
        if name in ('GlobalMemoryStatusEx', 'GetDiskFreeSpaceExW', 'CreateFileW',
                    'GetFileInformationByHandle', 'GetFinalPathNameByHandleW', 'CloseHandle'):
            monkeypatch.setattr(sources.ctypes, 'WinDLL', lambda *args, **kwargs: lib, raising=False)
            assert sources.windows_bindings() is lib
        else:
            monkeypatch.setattr(sources.ctypes, 'CDLL', lambda *args, **kwargs: lib)
            monkeypatch.setattr(sources, 'darwin_exports', lambda library: (4096, 11))
            assert sources.darwin_bindings() == (lib, 4096, 11)
        assert getattr(getattr(lib, name), 'argtypes', None) == signatures[name][0]
        assert getattr(getattr(lib, name), 'restype', None) is signatures[name][1]


def test_native_ci_validator_rejects_refusal_wrong_os_sources_and_budgets(tmp_path):
    """Exercise the actual workflow's receipt gate, not a test-owned validator."""
    workflow = ROOT / '.github' / 'workflows' / 'parity-resources.yml'
    assert workflow.is_file(), 'the required native proof workflow is not implemented'
    import copy
    import textwrap
    text = workflow.read_text()
    program = textwrap.dedent(text.split("<<'PY'\n", 1)[1].split('\n          PY', 1)[0])
    base = {'schema': 'parity-resource-preflight/v1', 'status': 'ok', 'platform': 'linux',
        'cpu_count': 2, 'cgroup': dict(NOT_APPLICABLE, mode='none'),
        'sources': [dict(name='MemAvailable', location='/proc/meminfo', mode='finite',
                        limit_bytes='120000', used_bytes='20000', available_bytes='100000', facts={}),
                    dict(name='RLIMIT_AS', location='getrlimit(RLIMIT_AS)', mode='unlimited',
                         limit_bytes=None, used_bytes=None, available_bytes=None, facts={})],
        'disk': {'api': 'fstatvfs', 'available_bytes': '100000'},
        'limits': {'available_bytes': '100000', 'reserve_bytes': '50000', 'usable_bytes': '50000',
                   'decode_bytes': '25000', 'output_buffer_bytes': '25000',
                   'object_overhead_bytes': '112', 'blob_bytes_per_worker': '2477',
                   'output_bytes': '24992', 'worker_slots': 2, 'decode_expansion': 5}}
    platforms = [('linux', 'MemAvailable'), ('darwin', 'HOST_VM_INFO64'), ('win32', 'GlobalMemoryStatusEx')]
    for platform_name, api in platforms:
        intact = copy.deepcopy(base)
        intact['platform'] = platform_name
        intact['sources'][0]['name'] = api
        intact['cgroup'] = dict(NOT_APPLICABLE, mode='none' if platform_name == 'linux' else 'not-applicable')
        if platform_name == 'win32':
            intact['sources'] = intact['sources'][:1]
            intact['disk']['api'] = 'GetDiskFreeSpaceExW'
        extra_limit = dict(name='additional-native-limit', location='fixture', mode='finite',
                           limit_bytes='100000', used_bytes='10000', available_bytes='90000', facts={})
        changes = [None, ('schema', 'old-schema'), ('status', 'refused'), ('platform', 'other'),
            ('sources', []), ('sources', [{**intact['sources'][0], 'name': 'unknown'}, *intact['sources'][1:]]),
            ('sources', [{**intact['sources'][0], 'limit_bytes': '120001'}, *intact['sources'][1:]]),
            ('sources', [*intact['sources'], extra_limit]), ('disk.api', 'unknown'),
            ('cgroup.mode', 'not-applicable' if platform_name == 'linux' else 'none'),
            ('limits.output_bytes', '25001'), ('limits.output_bytes', '0'),
            ('limits.worker_slots', 0), ('limits.decode_expansion', 5.0), ('limits.decode_expansion', 0),
            ('cpu_count', 1), ('limits.reserve_bytes', '50001'),
            ('limits.blob_bytes_per_worker', '100000'), ('disk.available_bytes', '24991'),
            ('limits.available_bytes', '0100000'), ('limits.available_bytes', 100000),
            ('limits.decode_bytes', '24999'), ('limits.output_buffer_bytes', '25001')]
        if platform_name != 'win32':
            changes.extend([('sources', intact['sources'][:1]),
                ('sources', [intact['sources'][0], dict(intact['sources'][1], mode='unknown')]),
                ('sources', [intact['sources'][0], dict(intact['sources'][1], limit_bytes='1')]),
                ('sources', [intact['sources'][0], dict(intact['sources'][1], available_bytes='1')])])
        for change in changes:
            receipt = copy.deepcopy(intact)
            if change:
                key, value = change
                parts = key.split('.')
                target = receipt
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
            path = tmp_path/'receipt.json'
            path.write_text(json.dumps(receipt))
            result = subprocess.run([sys.executable, '-c', program, str(path), platform_name],
                                    capture_output=True, text=True, timeout=60)
            assert (result.returncode == 0) == (change is None), (platform_name, change, result.stderr)
    intact = copy.deepcopy(base)
    intact['cgroup'] = dict(mode='v2', mount='/sys/fs/cgroup', mount_root='/', process_path='/parent/child',
                           ancestors=['/sys/fs/cgroup/parent/child', '/sys/fs/cgroup/parent', '/sys/fs/cgroup'])
    intact['sources'].extend([
        dict(name='cgroup-v2', location='/sys/fs/cgroup/parent/child', mode='finite',
             limit_bytes='150000', used_bytes='10000', available_bytes='140000', facts={}),
        dict(name='cgroup-v2', location='/sys/fs/cgroup/parent', mode='finite',
             limit_bytes='140000', used_bytes='20000', available_bytes='120000', facts={}),
        dict(name='cgroup-v2-root', location='/sys/fs/cgroup', mode='unlimited',
             limit_bytes=None, used_bytes=None, available_bytes=None, facts={})])
    for change in (None, ('mount_root', '/hidden'), ('mount', '/other'),
                   ('ancestors', ['/wrong', '/sys/fs/cgroup/parent', '/sys/fs/cgroup']),
                   ('ancestors', ['/sys/fs/cgroup/parent', '/sys/fs/cgroup/parent/child', '/sys/fs/cgroup']),
                   ('ancestors', [])):
        receipt = copy.deepcopy(intact)
        if change:
            receipt['cgroup'][change[0]] = change[1]
        path = tmp_path/'receipt.json'
        path.write_text(json.dumps(receipt))
        result = subprocess.run([sys.executable, '-c', program, str(path), 'linux'],
                                capture_output=True, text=True, timeout=60)
        assert (result.returncode == 0) == (change is None), (change, result.stderr)
