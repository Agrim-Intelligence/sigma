"""Public-argv own-clause controls with main, collect and validators intact.

All OSes below are simulations: raw proc reads, native calls and OS measurements
are seams. Every rejection is observed as exit 2, empty stdout and exact JSON
keys on stderr; no preconstructed Refused exception enters the fixture.
"""
import ctypes
import importlib
from contextlib import contextmanager
from pathlib import Path
import json
try:
    import resource
except ImportError:  # POSIX measurements remain simulated on a Windows test host.
    resource = None
import stat
import sys
from types import SimpleNamespace

import pytest

from test_parity_resources import darwin_fixture, windows_fixture, WINDOWS_GUID_ROOT


def build_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    resources = importlib.import_module('tools.parity.resources')
    sources = resources.sources
    rlimit = resource or SimpleNamespace(RLIM_INFINITY=-1, RLIMIT_AS=9, getrlimit=None)
    if resource is None:
        monkeypatch.setitem(sys.modules, 'resource', rlimit)
    monkeypatch.setattr(sources.os, 'O_DIRECTORY', getattr(sources.os, 'O_DIRECTORY', 0), raising=False)
    identity = dict(st_mode=stat.S_IFDIR | 0o700, st_dev=1, st_ino=2)
    state = SimpleNamespace(resources=resources, sources=sources, platform='linux',
        files={'/proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 800 kB\n',
               '/proc/self/cgroup': '0::/parent/child\n',
               '/proc/self/mountinfo': '29 23 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n',
               '/proc/cgroups': '#subsys_name hierarchy num_cgroups enabled\nmemory 0 1 1\n',
               '/sys/fs/cgroup/cgroup.controllers': 'cpu memory',
               '/sys/fs/cgroup/parent/child/memory.max': 'max',
               '/sys/fs/cgroup/parent/child/memory.current': '100',
               '/sys/fs/cgroup/parent/memory.max': '100000',
               '/sys/fs/cgroup/parent/memory.current': '20000',
               '/proc/self/statm': '10 2 3 4 5 6 7'},
        cpu=2, limit=rlimit.RLIM_INFINITY, page=4096, overhead=112,
        disk=SimpleNamespace(f_bavail=1000000, f_frsize=1),
        before=dict(identity), opened=dict(identity), after=dict(identity), stat_calls=0,
        closed=[], close_error=None)

    def read(path, **kwargs):
        value = state.files.get(str(path).replace('\\', '/'), FileNotFoundError('opaque'))
        if isinstance(value, Exception):
            raise value
        return value

    def measured(value):
        if isinstance(value, Exception):
            raise value
        return value

    def path_stat(path, **kwargs):
        state.stat_calls += 1
        return SimpleNamespace(**(state.before if state.stat_calls == 1 else state.after))

    def close(fd):
        state.closed.append(fd)
        if state.close_error:
            raise state.close_error

    monkeypatch.setattr(sources.Path, 'read_text', read)
    monkeypatch.setattr(sources.Path, 'resolve', lambda path, **kwargs: path)
    monkeypatch.setattr(sources.Path, 'stat', path_stat)
    monkeypatch.setattr(sources.os, 'open', lambda *args: 11)
    monkeypatch.setattr(sources.os, 'fstat', lambda fd: SimpleNamespace(**state.opened))
    monkeypatch.setattr(sources.os, 'fstatvfs', lambda fd: measured(state.disk), raising=False)
    monkeypatch.setattr(sources.os, 'close', close)
    monkeypatch.setattr(sources.os, 'cpu_count', lambda: state.cpu)
    monkeypatch.setattr(sources.os, 'sysconf', lambda key: measured(state.page), raising=False)
    monkeypatch.setattr(rlimit, 'getrlimit', lambda key: (measured(state.limit), rlimit.RLIM_INFINITY))
    monkeypatch.setattr(resources, 'object_overhead', lambda: state.overhead)

    def invoke(code=0, source=None, error=None, *, fraction='0.25', record='256'):
        monkeypatch.setattr(sources.sys, 'platform', state.platform)
        state.stat_calls = 0
        try:
            actual = resources.main(['--reserve-fraction', fraction, '--record-bytes', record,
                                     '--output-dir', '.'])
        except Exception as failure:
            pytest.fail(f'public CLI escaped typed JSON refusal: {type(failure).__name__}: {failure}')
        out = capsys.readouterr()
        assert actual == code, (actual, out)
        if code == 2:
            assert out.out == '' and out.err.endswith('\n')
            payload = json.loads(out.err)
            assert set(payload) == {'schema', 'status', 'platform', 'code', 'source', 'cgroup'}
            assert payload['schema'] == 'parity-resource-preflight/v1'
            assert payload['status'] == 'refused' and payload['platform'] == state.platform
            assert payload['code'] == error and payload['source'] == source
        else:
            assert out.err == '' and out.out.endswith('\n')
            payload = json.loads(out.out)
            assert payload['status'] == 'ok'
        return payload

    state.invoke = invoke
    return state


@pytest.fixture
def cli_case(monkeypatch, tmp_path, capsys):
    @contextmanager
    def case():
        with monkeypatch.context() as patch:
            yield build_cli(patch, tmp_path, capsys), patch
    return case


def native(cli, monkeypatch, platform, **kwargs):
    """Borrow raw API doubles, then restore real binding construction for the CLI."""
    cli.platform = platform
    sources = cli.sources
    binding = sources.darwin_bindings if platform == 'darwin' else sources.windows_bindings
    if platform == 'darwin':
        events = darwin_fixture(monkeypatch, sources, **kwargs)
        lib, page, task = sources.darwin_bindings()
        monkeypatch.setattr(sources, 'darwin_bindings', binding)
        monkeypatch.setattr(sources.ctypes, 'CDLL', lambda *args, **kw: lib)
        monkeypatch.setattr(sources, 'darwin_exports', lambda library: (page, task))
    else:
        events = windows_fixture(monkeypatch, sources, **kwargs)
        lib = sources.windows_bindings()
        monkeypatch.setattr(sources, 'windows_bindings', binding)
        monkeypatch.setattr(sources.ctypes, 'WinDLL', lambda *args, **kw: lib, raising=False)
    return lib, events


def test_darwin_memory_refusals_reach_cli(cli_case):
    for kwargs, error, source in [
        ({'status': 1}, 'native-api-failed', 'host_statistics64'),
        ({'host_port': 0}, 'native-api-failed', 'mach_host_self'),
        ({'count': 1}, 'invalid-source', 'HOST_VM_INFO64'),
        ({'count': 100}, 'invalid-source', 'HOST_VM_INFO64'),
        ({'speculative': 11}, 'invalid-source', 'HOST_VM_INFO64'),
        ({'total': 100}, 'invalid-source', 'HOST_VM_INFO64'),
        ({'free': 0, 'inactive': 0, 'speculative': 0}, 'memory-exhausted', 'HOST_VM_INFO64'),
        ({'sysctl_status': 1}, 'native-api-failed', 'sysctlbyname'),
        ({'sysctl_size': 7}, 'invalid-source', 'hw.memsize'),
        ({'deallocate': 1}, 'native-api-failed', 'mach_port_deallocate'),
    ]:
        with cli_case() as (cli, monkeypatch):
            _, events = native(cli, monkeypatch, 'darwin', **kwargs)
            payload = cli.invoke(2, source, error)
            assert payload['cgroup']['mode'] == 'not-applicable'
            assert events == ([] if kwargs.get('host_port') == 0 else [(11, 7)])
            assert cli.closed == [], 'invalid memory must stop collection before probing the disk'


def test_darwin_virtual_refusals_reach_cli(cli_case):
    for case, error in [('status', 'native-api-failed'),
        ('count', 'invalid-source'), ('zero', 'invalid-source'), ('overflow', 'invalid-source'),
        ('exhausted', 'memory-exhausted')]:
        with cli_case() as (cli, monkeypatch):
            kwargs = {'task_count': 1} if case == 'count' else {'virtual': {'zero': 0,
                'overflow': sys.maxsize+1, 'exhausted': 1000000}.get(case, 123456)}
            lib, _ = native(cli, monkeypatch, 'darwin', **kwargs)
            if case == 'status':
                lib.task_info.callback = lambda *args: 1
            cli.limit = 1000000
            cli.invoke(2, 'RLIMIT_AS' if case == 'exhausted' else ('task_info' if case == 'status' else 'TASK_VM_INFO'), error)


def test_windows_refusals_reach_cli(cli_case):
    for kwargs, error, source in [
        ({'memory_status': 0}, 'native-api-failed', 'GlobalMemoryStatusEx'),
        ({'available': 120001}, 'invalid-source', 'GlobalMemoryStatusEx'),
        ({'available': 0}, 'memory-exhausted', 'GlobalMemoryStatusEx'),
        ({'handle': None}, 'native-api-failed', 'CreateFileW'),
        ({'handle': 0}, 'native-api-failed', 'CreateFileW'),
        ({'handle': -1}, 'native-api-failed', 'CreateFileW'),
        ({'handle': ctypes.c_void_p(-1).value}, 'native-api-failed', 'CreateFileW'),
        ({'information_status': 0}, 'native-api-failed', 'GetFileInformationByHandle'),
        ({'directory_attribute': 0}, 'invalid-input', 'output-directory'),
        ({'required_size': 0}, 'native-api-failed', 'GetFinalPathNameByHandleW'),
        ({'required_size': 32769}, 'invalid-source', 'GetFinalPathNameByHandleW'),
        ({'path_status': 0}, 'native-api-failed', 'GetFinalPathNameByHandleW'),
        ({'path_status': 99999}, 'invalid-source', 'GetFinalPathNameByHandleW'),
        ({'final_path': 'C:\\mutable\\alias'}, 'invalid-source', 'GetFinalPathNameByHandleW'),
        ({'disk_status': 0}, 'native-api-failed', 'GetDiskFreeSpaceExW'),
        ({'close_status': 0}, 'native-api-failed', 'CloseHandle'),
    ]:
        with cli_case() as (cli, monkeypatch):
            _, events = native(cli, monkeypatch, 'win32', **kwargs)
            assert cli.invoke(2, source, error)['cgroup']['mode'] == 'not-applicable'
            if source == 'GlobalMemoryStatusEx':
                assert events == [], 'invalid memory must stop collection before opening the output sink'
            if events and kwargs.get('handle', 99) not in (None, 0, -1, ctypes.c_void_p(-1).value):
                assert events[-1] == ('close', 99)


def test_binding_and_platform_failure_reach_cli(cli_case):
    for platform in ['darwin', 'win32', 'unknown-os']:
        with cli_case() as (cli, monkeypatch):
            cli.platform = platform
            def unavailable(*args, **kwargs):
                raise OSError('opaque native loader failure')
            monkeypatch.setattr(cli.sources.ctypes, 'CDLL', unavailable)
            monkeypatch.setattr(cli.sources.ctypes, 'WinDLL', unavailable, raising=False)
            cli.invoke(2, {'darwin': 'libSystem', 'win32': 'kernel32', 'unknown-os': 'platform'}[platform],
                       'unsupported-platform' if platform == 'unknown-os' else 'native-binding-unavailable')


def test_posix_identity_refusals_reach_cli(cli_case):
    for field, value, when in [('st_dev', 3, 'opened'), ('st_ino', 3, 'opened'),
        ('st_mode', stat.S_IFREG | 0o600, 'opened'), ('st_dev', 3, 'after'), ('st_ino', 3, 'after')]:
        with cli_case() as (cli, monkeypatch):
            getattr(cli, when)[field] = value
            if when == 'opened':
                cli.after.update(cli.opened)
            cli.invoke(2, 'output-directory', 'invalid-source')
            assert cli.closed == [11]


def test_posix_disk_number_refusals_reach_cli(cli_case):
    for field, value in [('f_bavail', -1), ('f_bavail', sys.maxsize+1),
        ('f_bavail', True), ('f_frsize', 0), ('f_frsize', sys.maxsize)]:
        with cli_case() as (cli, monkeypatch):
            setattr(cli.disk, field, value)
            cli.invoke(2, 'fstatvfs', 'invalid-source')
            assert cli.closed == [11]


def test_posix_close_failure_reaches_cli(cli_case):
    with cli_case() as (cli, monkeypatch):
        cli.close_error = OSError('opaque close failure')
        cli.invoke(2, 'close-directory', 'source-unavailable')


def test_measured_cpu_refusals_reach_cli(cli_case):
    for value in [None, True, 0, -1, sys.maxsize+1, 1.5]:
        with cli_case() as (cli, monkeypatch):
            cli.cpu = value
            cli.invoke(2, 'cpu-count', 'invalid-source')


def test_measured_overhead_refusals_reach_cli(cli_case):
    for value in [0, -1, True, sys.maxsize+1]:
        with cli_case() as (cli, monkeypatch):
            cli.overhead = value
            cli.invoke(2, 'python-object-overhead', 'invalid-source')


def test_fraction_refusals_reach_cli(cli_case):
    for fraction in ['0', '1', '-1', 'nan', 'inf', '-inf']:
        with cli_case() as (cli, monkeypatch):
            cli.invoke(2, 'arguments', 'invalid-input', fraction=fraction)


def test_record_refusals_reach_cli(cli_case):
    for record in ['0', '-1', str(sys.maxsize+1)]:
        with cli_case() as (cli, monkeypatch):
            cli.invoke(2, 'arguments', 'invalid-input', record=record)


def test_budget_and_disk_boundaries_reach_cli(cli_case):
    with cli_case() as (cli, monkeypatch):
        cli.invoke(2, 'decode-budget', 'budget-exhausted', record='100000')
        payload = cli.invoke()
        total = int(payload['limits']['output_bytes'])
        cli.disk.f_bavail = total
        assert cli.invoke()['limits']['output_bytes'] == str(total)
        cli.disk.f_bavail = total-1
        cli.invoke(2, 'output-volume', 'disk-exhausted')


def test_linux_arithmetic_and_complete_provenance_reach_cli(cli_case):
    with cli_case() as (cli, monkeypatch):
        cli.limit = 100000
        payload = cli.invoke()
        assert payload['cgroup']['ancestors'] == ['/sys/fs/cgroup/parent/child', '/sys/fs/cgroup/parent', '/sys/fs/cgroup']
        assert [(r['name'], r['available_bytes']) for r in payload['sources']] == [
            ('MemAvailable', '819200'), ('cgroup-v2', None), ('cgroup-v2', '80000'),
            ('cgroup-v2-root', None), ('RLIMIT_AS', '59040')]
        assert payload['sources'][-1]['used_bytes'] == '40960'
        assert payload['limits'] == dict(available_bytes='59040', reserve_bytes='14760', usable_bytes='44280',
            decode_bytes='22140', output_buffer_bytes='22140', record_memory_bytes='1392',
            object_overhead_bytes='112', blob_bytes_per_worker='2191', output_records='86',
            output_bytes='22016', worker_slots=2, decode_expansion=5)
        cli.cpu = 100
        assert cli.invoke()['limits']['worker_slots'] == 15


def test_native_success_routes_reach_cli(cli_case):
    with cli_case() as (cli, monkeypatch):
        native(cli, monkeypatch, 'darwin')
        cli.limit = 150000
        payload = cli.invoke()
        assert [(r['name'], r['available_bytes']) for r in payload['sources']] == [
            ('HOST_VM_INFO64', '122880'), ('RLIMIT_AS', '26544')]
        assert payload['sources'][0]['facts'] == dict(free_pages='10', inactive_pages='20', speculative_pages='7', page_bytes='4096')
        _, events = native(cli, monkeypatch, 'win32', total=3000, available=3000)
        payload = cli.invoke(record='16')
        assert payload['disk']['available_bytes'] == '1234'
        assert events[-2:] == [('volume', WINDOWS_GUID_ROOT), ('close', 99)]
        assert [r['name'] for r in payload['sources']] == ['GlobalMemoryStatusEx']


def test_linux_bad_measurements_reach_cli(cli_case):
    for where, text, error, source in [
        ('/proc/meminfo', 'MemTotal: 1 kB\nMemAvailable: 2 kB', 'invalid-source', 'MemAvailable'),
        ('/proc/meminfo', 'MemTotal: 1 kB', 'invalid-source', '/proc/meminfo'),
        ('/proc/meminfo', 'MemTotal: 2 kB\nMemAvailable: 1 MB', 'invalid-source', '/proc/meminfo'),
        ('/proc/meminfo', 'MemTotal: 2 kB\nMemAvailable: 0 kB', 'memory-exhausted', 'MemAvailable'),
        ('/proc/meminfo', 'MemTotal: 2 kB\nMemTotal: 2 kB\nMemAvailable: 1 kB', 'invalid-source', '/proc/meminfo'),
        ('/sys/fs/cgroup/parent/memory.max', 'bad', 'invalid-source', 'memory.max'),
        ('/sys/fs/cgroup/parent/memory.current', '100001', 'memory-exhausted', 'cgroup-v2'),
        ('/sys/fs/cgroup/parent/child/memory.current', '-1', 'invalid-source', 'memory.current'),
        ('/proc/self/cgroup', '0::/a\n0::/b\n', 'cgroup-mapping-unsupported', '/proc/self/cgroup'),
        ('/proc/self/cgroup', 'bad', 'cgroup-mapping-unsupported', '/proc/self/cgroup'),
        ('/proc/self/cgroup', '0:cpu', 'cgroup-mapping-unsupported', '/proc/self/cgroup'),
        ('/proc/self/cgroup', 'x:memory:/', 'cgroup-mapping-unsupported', '/proc/self/cgroup'),
        ('/proc/self/mountinfo', 'no separator', 'cgroup-mapping-unsupported', '/proc/self/mountinfo'),
        ('/proc/self/mountinfo', '29 23 0:26 / /sys/fs/cgroup - cgroup2 cgroup rw', 'cgroup-mapping-unsupported', '/proc/self/mountinfo'),
        ('/proc/self/mountinfo', '29 23 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup', 'cgroup-mapping-unsupported', '/proc/self/mountinfo'),
        ('/proc/self/mountinfo', '29 23 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n30 23 0:26 / /other rw - cgroup2 cgroup rw', 'cgroup-mapping-unsupported', '/proc/self/mountinfo'),
        ('/sys/fs/cgroup/cgroup.controllers', 'cpu memory memory', 'invalid-source', 'cgroup.controllers'),
        ('/sys/fs/cgroup/cgroup.controllers', 'cpu memory bad!', 'invalid-source', 'cgroup.controllers'),
        ('/proc/self/mountinfo', '29 23 0:26 /container /sys/fs/cgroup rw - cgroup2 cgroup rw', 'cgroup-mapping-unsupported', '/proc/self/mountinfo'),
        ('/proc/self/mountinfo', r'29 23 0:26 / /a\unknown rw - cgroup2 cgroup rw', 'cgroup-mapping-unsupported', '/proc/self/mountinfo'),
    ]:
        with cli_case() as (cli, monkeypatch):
            cli.files[where] = text
            cli.invoke(2, source, error)


def test_linux_path_validation_reaches_cli(cli_case):
    for path in ['relative', '/../escape', '/a/../b', '/a//b', '/a/./b',
        '/a\x00b', '/a\tb', '/a\x7fb', '/a\x85b']:
        with cli_case() as (cli, monkeypatch):
            cli.files['/proc/self/cgroup'] = f'0::{path}\n'
            cli.invoke(2, '/proc/self/cgroup', 'cgroup-mapping-unsupported')


def test_linux_statm_refusals_reach_cli(cli_case):
    for text, page, source in [('garbage', 4096, '/proc/self/statm'),
        ('-1 2 3 4 5 6 7', 4096, '/proc/self/statm'), ('1001', 4096, '/proc/self/statm'),
        ('1001 2 3 4 5 6 7 8', 4096, '/proc/self/statm'), ('0 2 3 4 5 6 7', 4096, '/proc/self/statm'),
        ('1001 2 3 4 5 6 7', 0, 'SC_PAGE_SIZE'), (f'{sys.maxsize} 2 3 4 5 6 7', 4096, '/proc/self/statm'),
        ('01 2 3 4 5 6 7', 4096, '/proc/self/statm'), ('+1 2 3 4 5 6 7', 4096, '/proc/self/statm')]:
        with cli_case() as (cli, monkeypatch):
            cli.limit, cli.page = 10000000, page
            cli.files['/proc/self/statm'] = text
            cli.invoke(2, source, 'invalid-source')


def test_real_read_error_translation_reaches_cli(cli_case):
    for where in ['/proc/meminfo', '/proc/self/statm',
        '/sys/fs/cgroup/cgroup.controllers', '/sys/fs/cgroup/parent/child/memory.current',
        '/sys/fs/cgroup/memory.max']:
        for failure, error in [(FileNotFoundError('opaque'), 'source-unavailable'),
        (PermissionError('opaque'), 'source-unavailable'),
        (UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'opaque'), 'invalid-source')]:
            with cli_case() as (cli, monkeypatch):
                cli.limit = 1000000
                cli.files[where] = failure
                if where == '/sys/fs/cgroup/memory.max' and isinstance(failure, FileNotFoundError):
                    assert cli.invoke()['sources'][-2]['available_bytes'] is None
                else:
                    source = where if where.startswith('/proc/') else where.rsplit('/', 1)[-1]
                    cli.invoke(2, source, error)


def test_linux_root_and_address_modes_reach_cli(cli_case):
    for case in ['max-only', 'current-only', 'root-cap', 'root-cap-without-controller', 'none', 'v1', 'exhausted']:
        with cli_case() as (cli, monkeypatch):
            root = '/sys/fs/cgroup/'
            if case in ('max-only', 'root-cap', 'root-cap-without-controller'):
                cli.files[root+'memory.max'] = '50000'
            if case in ('current-only', 'root-cap', 'root-cap-without-controller'):
                cli.files[root+'memory.current'] = '20000'
            if case in ('none', 'root-cap-without-controller'):
                cli.files[root+'cgroup.controllers'] = 'cpu io'
            if case in ('max-only', 'current-only'):
                cli.invoke(2, 'memory.max/current', 'source-unavailable')
            elif case == 'v1':
                cli.files['/proc/self/cgroup'] = '5:cpu,memory:/legacy\n0::/\n'
                payload = cli.invoke(2, '/proc/self/cgroup', 'cgroup-v1-memory-availability-unsupported')
                assert payload['cgroup'] == dict(mode='v1-unsupported', mount=None, mount_root=None, process_path='/legacy', ancestors=[])
            elif case == 'exhausted':
                cli.limit = 40960
                cli.invoke(2, 'RLIMIT_AS', 'memory-exhausted')
            else:
                payload = cli.invoke()
                if case == 'none':
                    assert payload['cgroup'] == dict(mode='none', mount=None, mount_root=None, process_path=None, ancestors=[])
                else:
                    assert payload['limits']['available_bytes'] == '30000'
                    assert payload['sources'][-2]['available_bytes'] == '30000'


def test_os_measurement_errors_reach_cli(cli_case):
    for measurement, source in [('page', 'SC_PAGE_SIZE'), ('limit', 'RLIMIT_AS'), ('disk', 'fstatvfs')]:
        with cli_case() as (cli, monkeypatch):
            cli.limit = 1000000
            setattr(cli, measurement, OSError('opaque'))
            cli.invoke(2, source, 'source-unavailable')


def test_native_abi_is_constructed_on_public_cli_route(cli_case):
    for platform in ['darwin', 'win32']:
        with cli_case() as (cli, monkeypatch):
            c = ctypes
            s = cli.sources
            signatures = {
                'mach_host_self': ([], c.c_uint32),
                'host_statistics64': ([c.c_uint32, c.c_int, c.c_void_p, c.POINTER(c.c_uint32)], c.c_int),
                'sysctlbyname': ([c.c_char_p, c.c_void_p, c.POINTER(c.c_size_t), c.c_void_p, c.c_size_t], c.c_int),
                'mach_port_deallocate': ([c.c_uint32, c.c_uint32], c.c_int),
                'task_info': ([c.c_uint32, c.c_int, c.c_void_p, c.POINTER(c.c_uint32)], c.c_int),
                'GlobalMemoryStatusEx': ([c.POINTER(s.MemoryStatusEx)], c.c_int),
                'GetDiskFreeSpaceExW': ([c.c_wchar_p, c.POINTER(c.c_uint64), c.POINTER(c.c_uint64), c.POINTER(c.c_uint64)], c.c_int),
                'CreateFileW': ([c.c_wchar_p, c.c_uint32, c.c_uint32, c.c_void_p, c.c_uint32, c.c_uint32, c.c_void_p], c.c_void_p),
                'GetFileInformationByHandle': ([c.c_void_p, c.POINTER(s.FileInformation)], c.c_int),
                'GetFinalPathNameByHandleW': ([c.c_void_p, c.c_wchar_p, c.c_uint32, c.c_uint32], c.c_uint32),
                'CloseHandle': ([c.c_void_p], c.c_int),
            }
            lib, _ = native(cli, monkeypatch, platform, **({'total': 3000, 'available': 3000} if platform == 'win32' else {}))
            cli.limit = 150000
            for name, function in vars(lib).items():
                original = function.callback
                def checked(*args, function=function, original=original, name=name):
                    assert function.argtypes == signatures[name][0], name
                    assert function.restype is signatures[name][1], name
                    return original(*args)
                function.callback = checked
            cli.invoke(record='16')


def test_valid_noncontrol_unicode_path_reaches_cli(cli_case):
    with cli_case() as (cli, monkeypatch):
        cli.files['/proc/self/cgroup'] = '0::/parent/雪\u00a0\n'
        cli.files['/sys/fs/cgroup/parent/雪\u00a0/memory.max'] = 'max'
        cli.files['/sys/fs/cgroup/parent/雪\u00a0/memory.current'] = '100'
        assert cli.invoke()['cgroup']['process_path'] == '/parent/雪\u00a0'


def test_output_file_refuses_before_open(cli_case):
    with cli_case() as (cli, monkeypatch):
        cli.before['st_mode'] = stat.S_IFREG | 0o600
        cli.invoke(2, 'output-directory', 'invalid-input')
        assert cli.closed == []


def test_raw_measurement_exception_clauses_reach_cli(cli_case):
    for case in ('decimal-conversion', 'rlimit-binding', 'resolve-os', 'resolve-value',
                     'open-binding', 'open-os'):
        with cli_case() as (cli, monkeypatch):
            if case == 'decimal-conversion':
                cli.files['/sys/fs/cgroup/parent/memory.max'] = '9'*5000
                source, error = 'memory.max', 'invalid-source'
            elif case == 'rlimit-binding':
                cli.limit = AttributeError('opaque binding')
                source, error = 'RLIMIT_AS', 'native-binding-unavailable'
            else:
                failure = {'resolve-os': OSError, 'resolve-value': ValueError,
                           'open-binding': AttributeError, 'open-os': OSError}[case]
                def fail(*args, **kwargs):
                    raise failure('opaque measurement')
                if case.startswith('resolve'):
                    monkeypatch.setattr(cli.sources.Path, 'resolve', fail)
                    source = 'output-directory'
                    error = 'invalid-input' if case == 'resolve-value' else 'source-unavailable'
                else:
                    monkeypatch.setattr(cli.sources.os, 'open', fail)
                    source = 'fstatvfs' if case == 'open-binding' else 'output-directory'
                    error = 'native-binding-unavailable' if case == 'open-binding' else 'source-unavailable'
            cli.invoke(2, source, error)
