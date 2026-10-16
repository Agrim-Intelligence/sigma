"""Checked native snapshots for the inventory preflight; no subprocess or network client."""
import ctypes
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


class Refused(Exception):
    def __init__(self, code, source, cgroup=None):
        super().__init__(f'{code}: {source}')
        self.code, self.source, self.cgroup = code, source, cgroup


def number(value, source, *, positive=False, code='invalid-source'):
    if type(value) is not int or value < 0 or value > sys.maxsize or (positive and value == 0):
        raise Refused(code, source)
    return value


def unsigned(text, source):
    if not re.fullmatch(r'0|[1-9][0-9]*', text):
        raise Refused('invalid-source', source)
    try:
        return number(int(text), source)
    except ValueError:
        raise Refused('invalid-source', source) from None


def source_row(name, location, limit, used, facts=None):
    if used is not None:
        number(used, name)
    if limit is None:
        available, mode = None, 'unlimited'
    else:
        number(limit, name)
        number(used, name)
        available, mode = limit-used, 'finite'
        if available <= 0:
            raise Refused('memory-exhausted', name)
    return dict(name=name, location=location, mode=mode, limit_bytes=limit,
                used_bytes=used, available_bytes=available, facts=facts or {})


def physical_row(name, location, total, available, facts=None):
    number(total, name)
    number(available, name)
    # An overlarge availability produces negative usage, rejected by the shared integer guard.
    return source_row(name, location, total, total-available, facts)


def read_text(path, source, *, missing_ok=False):
    try:
        return Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        if missing_ok:
            return None
        raise Refused('source-unavailable', source) from None
    except UnicodeError:
        raise Refused('invalid-source', source) from None
    except OSError:
        raise Refused('source-unavailable', source) from None


def cgroup_record(mode, mount=None, path=None, ancestors=None):
    return dict(mode=mode, mount=mount, mount_root='/' if mount else None,
                process_path=path, ancestors=ancestors or [])


def kernel_path(value):
    if (not value.startswith('/') or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
            or (value != '/' and any(p in ('', '.', '..') for p in value.split('/')[1:]))):
        raise Refused('cgroup-mapping-unsupported', '/proc/self/cgroup')
    return value


def mount_path(value):
    decoded = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    if '\\' in decoded:
        # A literal backslash is safe; an unknown kernel escape is not a reliable mapping.
        if re.search(r'\\(?![0-7]{3})', value):
            raise Refused('cgroup-mapping-unsupported', '/proc/self/mountinfo')
    return kernel_path(decoded)


def linux_physical():
    values = {}
    for line in read_text('/proc/meminfo', '/proc/meminfo').splitlines():
        if line.startswith(('MemTotal:', 'MemAvailable:')):
            match = re.fullmatch(r'(MemTotal|MemAvailable):\s+(0|[1-9][0-9]*)\s+kB', line)
            if not match or match[1] in values:
                raise Refused('invalid-source', '/proc/meminfo')
            values[match[1]] = number(unsigned(match[2], '/proc/meminfo')*1024, '/proc/meminfo')
    if set(values) != {'MemTotal', 'MemAvailable'}:
        raise Refused('invalid-source', '/proc/meminfo')
    return physical_row('MemAvailable', '/proc/meminfo', values['MemTotal'], values['MemAvailable'])


def linux_cgroup_location():
    paths = []
    for line in read_text('/proc/self/cgroup', '/proc/self/cgroup').splitlines():
        parts = line.split(':', 2)
        if len(parts) != 3 or not parts[0].isdigit():
            raise Refused('cgroup-mapping-unsupported', '/proc/self/cgroup')
        path = kernel_path(parts[2])
        if 'memory' in parts[1].split(','):
            raise Refused('cgroup-v1-memory-availability-unsupported', '/proc/self/cgroup',
                          cgroup_record('v1-unsupported', path=path))
        if parts[0] == '0' and parts[1] == '':
            paths.append(path)
    if not paths:
        # Absence is evidence only after validating the complete kernel table.
        rows = [line.split() for line in read_text('/proc/cgroups', '/proc/cgroups').splitlines()]
        if rows[:1] != [['#subsys_name', 'hierarchy', 'num_cgroups', 'enabled']]:
            raise Refused('invalid-source', '/proc/cgroups')
        controllers = {}
        for row in rows[1:]:
            if len(row) != 4:
                raise Refused('invalid-source', '/proc/cgroups')
            name, hierarchy, count, enabled = row
            if not re.fullmatch(r'[a-z][a-z0-9_]*', name) or name in controllers:
                raise Refused('invalid-source', '/proc/cgroups')
            unsigned(hierarchy, '/proc/cgroups')
            unsigned(count, '/proc/cgroups')
            if enabled not in ('0', '1'):
                raise Refused('invalid-source', '/proc/cgroups')
            controllers[name] = enabled
        if controllers.get('memory', '0') == '0':
            return None, None
        raise Refused('cgroup-mapping-unsupported', '/proc/self/cgroup')
    if len(paths) != 1:
        raise Refused('cgroup-mapping-unsupported', '/proc/self/cgroup')
    mounts = []
    for line in read_text('/proc/self/mountinfo', '/proc/self/mountinfo').splitlines():
        parts = line.split(' - ', 1)
        if len(parts) != 2:
            raise Refused('cgroup-mapping-unsupported', '/proc/self/mountinfo')
        right, left = parts[1].split(), parts[0].split()
        if right and right[0] == 'cgroup2':
            if len(left) < 6 or len(right) < 3:
                raise Refused('cgroup-mapping-unsupported', '/proc/self/mountinfo')
            mounts.append((mount_path(left[3]), mount_path(left[4])))
    if len(mounts) != 1 or mounts[0][0] != '/':
        raise Refused('cgroup-mapping-unsupported', '/proc/self/mountinfo')
    return mounts[0][1], paths[0]


def linux_memory():
    rows, provenance = [linux_physical()], None
    try:
        mount, process_path = linux_cgroup_location()
        if mount is None:
            return rows, cgroup_record('none')
        root = PurePosixPath(mount)
        controllers = read_text(root/'cgroup.controllers', 'cgroup.controllers').split()
        if (len(set(controllers)) != len(controllers)
                or any(not re.fullmatch(r'[a-z][a-z0-9_]*', c) for c in controllers)):
            raise Refused('invalid-source', 'cgroup.controllers')
        root_max = read_text(root/'memory.max', 'memory.max', missing_ok=True)
        root_current = read_text(root/'memory.current', 'memory.current', missing_ok=True)
        if (root_max is None) != (root_current is None):
            raise Refused('source-unavailable', 'memory.max/current')
        if 'memory' not in controllers and root_max is None:
            return rows, cgroup_record('none')
        current = root / process_path.lstrip('/')
        ancestors = []
        while True:
            ancestors.append(str(current))
            if current == root:
                break
            current = current.parent
        provenance = cgroup_record('v2', mount, process_path, ancestors)
        for path in ancestors:
            at_root = path == mount
            maximum = root_max if at_root else read_text(PurePosixPath(path)/'memory.max', 'memory.max')
            used = root_current if at_root else read_text(PurePosixPath(path)/'memory.current', 'memory.current')
            name = 'cgroup-v2-root' if at_root else 'cgroup-v2'
            if maximum is None:
                rows.append(source_row(name, path, None, None))
            else:
                limit = None if maximum.strip() == 'max' else unsigned(maximum.strip(), 'memory.max')
                rows.append(source_row(name, path, limit, unsigned(used.strip(), 'memory.current')))
        return rows, provenance
    except Refused as refusal:
        if refusal.cgroup is None:
            refusal.cgroup = provenance
        raise


def address_space_limit():
    try:
        import resource
        soft, _ = resource.getrlimit(resource.RLIMIT_AS)
    except (ImportError, AttributeError):
        raise Refused('native-binding-unavailable', 'RLIMIT_AS') from None
    except (OSError, ValueError):
        raise Refused('source-unavailable', 'RLIMIT_AS') from None
    return None if soft == resource.RLIM_INFINITY else number(soft, 'RLIMIT_AS')


def virtual_bytes():
    if sys.platform == 'darwin':
        return darwin_virtual_bytes()
    if sys.platform != 'linux':
        raise Refused('unsupported-platform', 'virtual-address-space')
    fields = read_text('/proc/self/statm', '/proc/self/statm').split()
    if len(fields) != 7:
        raise Refused('invalid-source', '/proc/self/statm')
    pages = [unsigned(field, '/proc/self/statm') for field in fields]
    try:
        page_bytes = number(os.sysconf('SC_PAGE_SIZE'), 'SC_PAGE_SIZE', positive=True)
    except (OSError, ValueError, AttributeError):
        raise Refused('source-unavailable', 'SC_PAGE_SIZE') from None
    return number(pages[0]*page_bytes, '/proc/self/statm', positive=True)


def posix_address_space():
    limit = address_space_limit()
    if limit is None:
        return source_row('RLIMIT_AS', 'getrlimit(RLIMIT_AS)', None, None)
    location = '/proc/self/statm' if sys.platform == 'linux' else 'task_info(TASK_VM_INFO)'
    return source_row('RLIMIT_AS', location, limit, virtual_bytes())


class VMStatistics64(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ('free_count', 'active_count', 'inactive_count', 'wire_count')]
    _fields_ += [(name, ctypes.c_uint64) for name in ('zero_fill_count', 'reactivations', 'pageins', 'pageouts',
                 'faults', 'cow_faults', 'lookups', 'hits', 'purges')]
    _fields_ += [(name, ctypes.c_uint32) for name in ('purgeable_count', 'speculative_count')]
    _fields_ += [(name, ctypes.c_uint64) for name in ('decompressions', 'compressions', 'swapins', 'swapouts')]
    _fields_ += [(name, ctypes.c_uint32) for name in ('compressor_page_count', 'throttled_count', 'external_page_count', 'internal_page_count')]
    _fields_ += [('total_uncompressed_pages_in_compressor', ctypes.c_uint64)]


class TaskVMInfo(ctypes.Structure):
    _pack_ = 4  # SDK revision-0 prefix; later revisions are not needed to read virtual_size.
    _fields_ = [('virtual_size', ctypes.c_uint64), ('region_count', ctypes.c_int32),
                ('page_size', ctypes.c_int32), ('remaining_revision_zero', ctypes.c_uint64*16)]


class MemoryStatusEx(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_uint32), ('dwMemoryLoad', ctypes.c_uint32)]
    _fields_ += [(name, ctypes.c_uint64) for name in ('ullTotalPhys', 'ullAvailPhys', 'ullTotalPageFile',
                  'ullAvailPageFile', 'ullTotalVirtual', 'ullAvailVirtual', 'ullAvailExtendedVirtual')]


class FileInformation(ctypes.Structure):
    # BY_HANDLE_FILE_INFORMATION is 13 DWORDs; only the first attribute field is consumed.
    _fields_ = [('dwFileAttributes', ctypes.c_uint32), ('remaining_fields', ctypes.c_uint32*12)]


def bind(library, signatures):
    for name, (arguments, result) in signatures.items():
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    return library


def darwin_exports(library):
    page = ctypes.c_ulong.in_dll(library, 'vm_kernel_page_size').value
    task = ctypes.c_uint32.in_dll(library, 'mach_task_self_').value
    return number(page, 'vm_kernel_page_size', positive=True), number(task, 'mach_task_self_', positive=True)


def darwin_bindings():
    c = ctypes
    try:
        lib = bind(c.CDLL('libSystem.B.dylib', use_errno=True), {
            'mach_host_self': ([], c.c_uint32),
            'host_statistics64': ([c.c_uint32, c.c_int, c.c_void_p, c.POINTER(c.c_uint32)], c.c_int),
            'sysctlbyname': ([c.c_char_p, c.c_void_p, c.POINTER(c.c_size_t), c.c_void_p, c.c_size_t], c.c_int),
            'mach_port_deallocate': ([c.c_uint32, c.c_uint32], c.c_int),
            'task_info': ([c.c_uint32, c.c_int, c.c_void_p, c.POINTER(c.c_uint32)], c.c_int),
        })
        page, task = darwin_exports(lib)
        return lib, page, task
    except (OSError, AttributeError, ValueError):
        raise Refused('native-binding-unavailable', 'libSystem') from None


def darwin_memory():
    lib, page, task = darwin_bindings()
    host = lib.mach_host_self()
    if not host:
        raise Refused('native-api-failed', 'mach_host_self')
    try:
        stats = VMStatistics64()
        capacity = ctypes.sizeof(stats)//ctypes.sizeof(ctypes.c_uint32)
        count = ctypes.c_uint32(capacity)
        if lib.host_statistics64(host, 4, ctypes.byref(stats), ctypes.byref(count)) != 0:
            raise Refused('native-api-failed', 'host_statistics64')
        if not 24 <= count.value <= capacity or stats.speculative_count > stats.free_count:
            raise Refused('invalid-source', 'HOST_VM_INFO64')
        total, size = ctypes.c_uint64(), ctypes.c_size_t(ctypes.sizeof(ctypes.c_uint64))
        if lib.sysctlbyname(b'hw.memsize', ctypes.byref(total), ctypes.byref(size), None, 0) != 0:
            raise Refused('native-api-failed', 'sysctlbyname')
        if size.value != ctypes.sizeof(total):
            raise Refused('invalid-source', 'hw.memsize')
        facts = dict(free_pages=stats.free_count, inactive_pages=stats.inactive_count,
                     speculative_pages=stats.speculative_count, page_bytes=page)
        return physical_row('HOST_VM_INFO64', 'host_statistics64', total.value,
                            (stats.free_count+stats.inactive_count)*page, facts)
    finally:
        if lib.mach_port_deallocate(task, host) != 0:
            raise Refused('native-api-failed', 'mach_port_deallocate')


def darwin_virtual_bytes():
    lib, _, task = darwin_bindings()
    info = TaskVMInfo()
    count = ctypes.c_uint32(ctypes.sizeof(info)//ctypes.sizeof(ctypes.c_uint32))
    capacity = count.value
    if lib.task_info(task, 22, ctypes.byref(info), ctypes.byref(count)) != 0:
        raise Refused('native-api-failed', 'task_info')
    if count.value != capacity:
        raise Refused('invalid-source', 'TASK_VM_INFO')
    return number(info.virtual_size, 'TASK_VM_INFO', positive=True)


def windows_bindings():
    c = ctypes
    try:
        return bind(c.WinDLL('kernel32', use_last_error=True), {
            'GlobalMemoryStatusEx': ([c.POINTER(MemoryStatusEx)], c.c_int),
            'GetDiskFreeSpaceExW': ([c.c_wchar_p, c.POINTER(c.c_uint64), c.POINTER(c.c_uint64), c.POINTER(c.c_uint64)], c.c_int),
            'CreateFileW': ([c.c_wchar_p, c.c_uint32, c.c_uint32, c.c_void_p, c.c_uint32, c.c_uint32, c.c_void_p], c.c_void_p),
            'GetFileInformationByHandle': ([c.c_void_p, c.POINTER(FileInformation)], c.c_int),
            'GetFinalPathNameByHandleW': ([c.c_void_p, c.c_wchar_p, c.c_uint32, c.c_uint32], c.c_uint32),
            'CloseHandle': ([c.c_void_p], c.c_int),
        })
    except (OSError, AttributeError, ValueError):
        raise Refused('native-binding-unavailable', 'kernel32') from None


def windows_memory():
    lib = windows_bindings()
    stats = MemoryStatusEx()
    stats.dwLength = ctypes.sizeof(stats)
    if not lib.GlobalMemoryStatusEx(ctypes.byref(stats)):
        raise Refused('native-api-failed', 'GlobalMemoryStatusEx')
    return physical_row('GlobalMemoryStatusEx', 'GlobalMemoryStatusEx', stats.ullTotalPhys, stats.ullAvailPhys)


def output_directory(directory):
    try:
        resolved = Path(directory).resolve(strict=True)
        identity = resolved.stat()
        if not stat.S_ISDIR(identity.st_mode):
            raise Refused('invalid-input', 'output-directory')
        return resolved, identity
    except (OSError, RuntimeError):
        raise Refused('source-unavailable', 'output-directory') from None
    except (TypeError, ValueError):
        raise Refused('invalid-input', 'output-directory') from None


def windows_disk(directory):
    resolved, _ = output_directory(directory)
    lib = windows_bindings()
    handle = lib.CreateFileW(str(resolved), 0, 3, None, 3, 0x02000000, None)
    if handle in (None, 0, -1, ctypes.c_void_p(-1).value):
        raise Refused('native-api-failed', 'CreateFileW')
    try:
        information = FileInformation()
        if not lib.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise Refused('native-api-failed', 'GetFileInformationByHandle')
        if not information.dwFileAttributes & 16:
            raise Refused('invalid-input', 'output-directory')
        required = lib.GetFinalPathNameByHandleW(handle, None, 0, 1)
        if not required:
            raise Refused('native-api-failed', 'GetFinalPathNameByHandleW')
        # Windows extended-path protocol maximum, not a machine-resource policy.
        if required > 32768:
            raise Refused('invalid-source', 'GetFinalPathNameByHandleW')
        buffer = ctypes.create_unicode_buffer(required)
        written = lib.GetFinalPathNameByHandleW(handle, buffer, required, 1)
        if not written:
            raise Refused('native-api-failed', 'GetFinalPathNameByHandleW')
        if written >= required:
            raise Refused('invalid-source', 'GetFinalPathNameByHandleW')
        final = buffer.value
        volume = re.match(r'^(\\\\\?\\Volume\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}\\)', final)
        if not volume:
            raise Refused('invalid-source', 'GetFinalPathNameByHandleW')
        available, total, free = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
        if not lib.GetDiskFreeSpaceExW(volume[1], ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)):
            raise Refused('native-api-failed', 'GetDiskFreeSpaceExW')
        return dict(api='GetDiskFreeSpaceExW', directory=final,
                    available_bytes=number(available.value, 'GetDiskFreeSpaceExW'))
    finally:
        if not lib.CloseHandle(handle):
            raise Refused('native-api-failed', 'CloseHandle')


def disk_capacity(directory):
    if sys.platform == 'win32':
        return windows_disk(directory)
    if sys.platform not in ('linux', 'darwin'):
        raise Refused('unsupported-platform', 'output-volume')
    resolved, before = output_directory(directory)
    try:
        descriptor = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY)
    except AttributeError:
        raise Refused('native-binding-unavailable', 'fstatvfs') from None
    except OSError:
        raise Refused('source-unavailable', 'output-directory') from None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise Refused('invalid-source', 'output-directory')
        capacity = os.fstatvfs(descriptor)
        available = number(number(capacity.f_bavail, 'fstatvfs') *
                           number(capacity.f_frsize, 'fstatvfs', positive=True), 'fstatvfs')
        after = resolved.stat()
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise Refused('invalid-source', 'output-directory')
        return dict(api='fstatvfs', directory=str(resolved), available_bytes=available)
    except (OSError, AttributeError):
        raise Refused('source-unavailable', 'fstatvfs') from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            raise Refused('source-unavailable', 'close-directory') from None


def collect(directory):
    provenance = None
    try:
        if sys.platform == 'linux':
            rows, provenance = linux_memory()
        elif sys.platform == 'darwin':
            provenance = cgroup_record('not-applicable')
            rows = [darwin_memory()]
        elif sys.platform == 'win32':
            provenance = cgroup_record('not-applicable')
            rows = [windows_memory()]
        else:
            raise Refused('unsupported-platform', 'platform')
        if sys.platform in ('linux', 'darwin'):
            rows.append(posix_address_space())
        return dict(sources=rows, cgroup=provenance, cpu_count=os.cpu_count(),
                    disk=disk_capacity(directory))
    except Refused as refusal:
        if refusal.cgroup is None:
            refusal.cgroup = provenance
        raise
