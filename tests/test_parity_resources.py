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
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['schema'] == 'parity-resource-preflight/v1'
    assert receipt['status'] == 'ok'
    assert receipt['platform'] == sys.platform
    assert receipt['sources']
    assert int(receipt['limits']['output_bytes']) > 0
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


@pytest.mark.parametrize('fraction', ['0', '1', '-0.1', 'nan', 'inf', '-inf', '1.01'])
def test_fraction_clause_refuses_through_public_argv(fake_cli, fraction):
    invoke, _, _, _ = fake_cli
    code, receipt = invoke(['--reserve-fraction', fraction, '--record-bytes', '16', '--output-dir', '.'])
    assert code == 2
    assert receipt == dict(schema='parity-resource-preflight/v1', status='refused',
                          platform=sys.platform, code='invalid-input', source='arguments', cgroup=None)


@pytest.mark.parametrize('argv', [[], ['--unknown'], ['--reserve-fraction', '0.5'],
    ['--reserve-fraction', '0.5', '--record-bytes', '0', '--output-dir', '.'],
    ['--reserve-fraction', '0.5', '--record-bytes', '-1', '--output-dir', '.'],
    ['--reserve-fraction', '0.5', '--record-bytes', str(sys.maxsize+1), '--output-dir', '.'],
    ['--reserve-fraction', '0.5', '--record-bytes', '1.2', '--output-dir', '.']])
def test_invalid_argument_shape_is_typed(fake_cli, argv):
    _, _, resources, _ = fake_cli
    from io import StringIO
    from contextlib import redirect_stdout, redirect_stderr
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        assert resources.main(argv) == 2
    assert out.getvalue() == ''
    assert json.loads(err.getvalue())['code'] == 'invalid-input'


@pytest.mark.parametrize('key,value', [('cpu_count', 0), ('cpu_count', True),
                                     ('cpu_count', sys.maxsize+1)])
def test_invalid_observed_cpu_is_not_a_worker_entitlement(fake_cli, key, value):
    invoke, snapshot, _, _ = fake_cli
    snapshot[key] = value
    assert invoke()[1]['code'] == 'invalid-source'


@pytest.mark.parametrize('value', [0, -1, True, 1.5, sys.maxsize+1])
def test_library_record_validation(fake_cli, value):
    _, _, resources, sources = fake_cli
    with pytest.raises(sources.Refused) as refusal:
        resources.calculate(100000, 0.5, value, 2, 100000)
    assert refusal.value.code == 'invalid-input'


@pytest.mark.parametrize('change', [dict(limit_bytes=True), dict(used_bytes=-1),
    dict(available_bytes=100001), dict(limit_bytes=sys.maxsize+1),
    dict(available_bytes=1.25), dict(mode='unknown')])
def test_every_finite_source_is_validated_before_minimum(fake_cli, change):
    invoke, snapshot, _, _ = fake_cli
    row = finite(limit=1000000, used=200000)
    row.update(change)
    snapshot['sources'].append(row)
    code, receipt = invoke()
    assert code == 2
    assert receipt['code'] == 'invalid-source'


@pytest.mark.parametrize('available,record,disk,code', [
    (0, 16, 100000, 'memory-exhausted'), (100, 16, 100000, 'budget-exhausted'),
    (100000, 10000, 100000, 'budget-exhausted'),
    (100000, 16, 24991, 'disk-exhausted')])
def test_exhaustion_refuses_without_partial_receipt(fake_cli, available, record, disk, code):
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
    def read(path, source):
        if str(path) not in files:
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


@pytest.mark.parametrize('path', ['/../escape', '/a/../b', '/a//b', '/a/./b', '/a\x00b', '/a\tb'])
def test_cgroup_kernel_path_rejects_traversal_and_controls(modules, monkeypatch, path):
    _, sources = modules
    proc_fixture(monkeypatch, sources, path=path)
    with pytest.raises(sources.Refused):
        sources.linux_memory()


@pytest.mark.parametrize('where,text,code', [
    ('/proc/meminfo', 'MemTotal: 1 kB\nMemAvailable: 2 kB', 'invalid-source'),
    ('/proc/meminfo', 'MemTotal: 1 kB', 'invalid-source'),
    ('/proc/meminfo', 'MemTotal: 2 kB\nMemAvailable: 1 MB', 'invalid-source'),
    ('/proc/meminfo', 'MemTotal: 2 kB\nMemAvailable: 0 kB', 'memory-exhausted'),
    ('/sys/fs/cgroup/parent/memory.max', 'bad', 'invalid-source'),
    ('/sys/fs/cgroup/parent/memory.current', '100001', 'memory-exhausted'),
    ('/sys/fs/cgroup/parent/child/memory.current', '-1', 'invalid-source'),
    ('/proc/self/cgroup', '0::/a\n0::/b\n', 'cgroup-mapping-unsupported'),
    ('/proc/self/mountinfo', '29 23 0:26 /container /sys/fs/cgroup rw - cgroup2 cgroup rw',
     'cgroup-mapping-unsupported')])
def test_linux_bad_sources_refuse(modules, monkeypatch, where, text, code):
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
    monkeypatch.setattr(sources.os, 'sysconf', lambda key: 4096)
    monkeypatch.setattr(sources, 'address_space_limit', lambda: 10000000)
    assert sources.virtual_bytes() == 4100096
    row = sources.posix_address_space()
    assert (row['location'], row['used_bytes'], row['available_bytes']) == (
        '/proc/self/statm', 4100096, 5899904)


@pytest.mark.parametrize('text,page', [('garbage', 4096), ('-1 2 3 4 5 6 7', 4096),
    ('0 2 3 4 5 6 7', 4096), ('1001 17 3 4 5 6 7', 0),
    (f'{sys.maxsize} 2 3 4 5 6 7', 4096)])
def test_linux_statm_malformed_zero_or_overflow_refuses(modules, monkeypatch, text, page):
    _, sources = modules
    monkeypatch.setattr(sources.sys, 'platform', 'linux')
    proc_fixture(monkeypatch, sources, rows={'/proc/self/statm': text})
    monkeypatch.setattr(sources.os, 'sysconf', lambda key: page)
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
    monkeypatch.setattr(sources.os, 'fstatvfs', lambda fd: SimpleNamespace(f_bavail=7, f_bfree=99, f_frsize=4096))
    disk = sources.disk_capacity(str(tmp_path))
    assert disk == dict(api='fstatvfs', directory=str(tmp_path.resolve()), available_bytes=28672)
    with pytest.raises(sources.Refused):
        sources.disk_capacity(str(tmp_path/'does-not-exist'))
    assert list(tmp_path.iterdir()) == []


class NativeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def darwin_fixture(monkeypatch, sources, *, status=0, total=1000000, free=10,
                   inactive=20, speculative=7, count=38, deallocate=0):
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
        ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = 8
        return 0
    def release(task, host):
        calls.append((task, host))
        return deallocate
    def task_info(task, flavor, ptr, size):
        assert (task, flavor) == (11, 22)
        ctypes.cast(ptr, ctypes.POINTER(sources.TaskVMInfo)).contents.virtual_size = 123456
        return status
    lib = SimpleNamespace(mach_host_self=NativeFunction(lambda: 7),
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


@pytest.mark.parametrize('kwargs,code', [({'status': 1}, 'native-api-failed'),
    ({'count': 1}, 'invalid-source'), ({'total': 100}, 'invalid-source'),
    ({'free': 0, 'inactive': 0}, 'memory-exhausted'), ({'deallocate': 1}, 'native-api-failed')])
def test_darwin_native_return_codes_and_counts_fail_closed(modules, monkeypatch, kwargs, code):
    _, sources = modules
    calls = darwin_fixture(monkeypatch, sources, **kwargs)
    with pytest.raises(sources.Refused) as refusal:
        sources.darwin_memory()
    assert refusal.value.code == code
    assert calls == [(11, 7)]


def windows_fixture(monkeypatch, sources, *, memory_status=1, disk_status=1,
                    total=120000, available=100000):
    import ctypes
    from types import SimpleNamespace
    def memory(ptr):
        stats = ctypes.cast(ptr, ctypes.POINTER(sources.MemoryStatusEx)).contents
        assert stats.dwLength == 64
        stats.ullTotalPhys, stats.ullAvailPhys = total, available
        return memory_status
    def disk(directory, avail, total, free):
        assert isinstance(directory, str)
        ctypes.cast(avail, ctypes.POINTER(ctypes.c_uint64)).contents.value = 1234
        return disk_status
    lib = SimpleNamespace(GlobalMemoryStatusEx=NativeFunction(memory),
                          GetDiskFreeSpaceExW=NativeFunction(disk))
    monkeypatch.setattr(sources, 'windows_bindings', lambda: lib)


def test_windows_native_memory_and_caller_volume_capacity(modules, monkeypatch, tmp_path):
    _, sources = modules
    windows_fixture(monkeypatch, sources)
    assert sources.windows_memory() == finite('GlobalMemoryStatusEx', 'GlobalMemoryStatusEx')
    assert sources.windows_disk(str(tmp_path)) == dict(api='GetDiskFreeSpaceExW',
        directory=str(tmp_path.resolve()), available_bytes=1234)


@pytest.mark.parametrize('kwargs,code', [({'memory_status': 0}, 'native-api-failed'),
    ({'available': 120001}, 'invalid-source'), ({'available': 0}, 'memory-exhausted')])
def test_windows_memory_status_and_totals_checked(modules, monkeypatch, kwargs, code):
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
