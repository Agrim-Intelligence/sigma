#!/usr/bin/env python3
"""Read-only resource admission snapshot; see docs/parity-resources.md for the v1 contract."""
import sys

if __name__ == '__main__':
    # The documented gesture must not depend on -B or the operator's environment.
    sys.dont_write_bytecode = True

import argparse
import ctypes
from datetime import datetime, timezone
import json
import math
import platform

if __package__:
    from . import resource_sources as sources
else:
    import resource_sources as sources


SCHEMA = 'parity-resource-preflight/v1'
LIMITATIONS = ['snapshot-not-reservation', 'decoded-text-only', 'cpu-count-not-quota',
              'caller-supplied-record-size', 'scan-throughput-unmeasured']


def sampled_at():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def object_overhead():
    return sys.getsizeof(b'') + sys.getsizeof(chr(sys.maxunicode)) - ctypes.sizeof(ctypes.c_uint32)


def reserve_ratio(fraction):
    if (type(fraction) not in (int, float) or not math.isfinite(fraction)
            or fraction <= 0 or fraction >= 1):
        raise sources.Refused('invalid-input', 'arguments')
    return float(fraction).as_integer_ratio()


def calculate(available, fraction, record_bytes, cpu_count, disk_available):
    numerator, denominator = reserve_ratio(fraction)
    sources.number(record_bytes, 'arguments', positive=True, code='invalid-input')
    sources.number(available, 'available-memory')
    sources.number(cpu_count, 'cpu-count', positive=True)
    sources.number(disk_available, 'output-volume')
    if available == 0:
        raise sources.Refused('memory-exhausted', 'available-memory')
    usable = available * (denominator-numerator) // denominator
    decode, output = usable//2, usable-usable//2
    expansion = 1 + ctypes.sizeof(ctypes.c_uint32)
    overhead = sources.number(object_overhead(), 'python-object-overhead', positive=True)
    record_memory = expansion*record_bytes + overhead
    workers = min(cpu_count, decode//record_memory)
    if workers == 0:
        raise sources.Refused('budget-exhausted', 'decode-budget')
    blob = (decode//workers-overhead)//expansion
    records = output//record_bytes
    total = records*record_bytes
    # W>0 implies D>=R, O>=D and B>=record_bytes: all partitions/output counts are positive.
    # Floors construct W*(E*B+H)<=D and T<=O; D+O=U<=A without a redundant post-hoc guard.
    if disk_available < total:
        raise sources.Refused('disk-exhausted', 'output-volume')
    values = dict(available_bytes=available, reserve_bytes=available-usable, usable_bytes=usable,
                  decode_bytes=decode, output_buffer_bytes=output, record_memory_bytes=record_memory,
                  object_overhead_bytes=overhead, blob_bytes_per_worker=blob,
                  output_records=records, output_bytes=total)
    return {**{key: str(value) for key, value in values.items()},
            'worker_slots': workers, 'decode_expansion': expansion}


def memory_receipts(rows):
    if not isinstance(rows, list):
        raise sources.Refused('invalid-source', 'memory-sources')
    encoded, finite = [], []
    expected = {'name', 'location', 'mode', 'limit_bytes', 'used_bytes', 'available_bytes', 'facts'}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != expected
                or not isinstance(row['name'], str) or not row['name']
                or not isinstance(row['location'], str) or not row['location']
                or not isinstance(row['facts'], dict)):
            raise sources.Refused('invalid-source', 'memory-source')
        name, limit, used, available = row['name'], row['limit_bytes'], row['used_bytes'], row['available_bytes']
        if row['mode'] == 'finite':
            sources.number(limit, name)
            sources.number(used, name)
            if limit-used <= 0:
                raise sources.Refused('memory-exhausted', name)
            sources.number(available, name, positive=True)
            if limit-used != available:
                raise sources.Refused('invalid-source', name)
            finite.append(available)
        elif row['mode'] == 'unlimited':
            if limit is not None or available is not None:
                raise sources.Refused('invalid-source', name)
            if used is None:
                if name not in ('RLIMIT_AS', 'cgroup-v2-root'):
                    raise sources.Refused('invalid-source', name)
            else:
                sources.number(used, name)
        else:
            raise sources.Refused('invalid-source', name)
        facts = {}
        for key, value in row['facts'].items():
            if not isinstance(key, str) or not key:
                raise sources.Refused('invalid-source', name)
            facts[key] = str(sources.number(value, name))
        encoded.append(dict(name=name, location=row['location'], mode=row['mode'],
                            limit_bytes=None if limit is None else str(limit),
                            used_bytes=None if used is None else str(used),
                            available_bytes=None if available is None else str(available), facts=facts))
    if not finite:
        raise sources.Refused('invalid-source', 'memory-sources')
    return encoded, min(finite)


def cgroup_receipt(value, rows=None):
    keys = {'mode', 'mount', 'mount_root', 'process_path', 'ancestors'}
    if not isinstance(value, dict) or set(value) != keys:
        raise sources.Refused('invalid-source', 'cgroup-provenance')
    if value['mode'] == 'v2' and sys.platform == 'linux':
        if (value['mount_root'] != '/' or not isinstance(value['ancestors'], list)
                or not value['ancestors']):
            raise sources.Refused('invalid-source', 'cgroup-provenance')
        for path in [value['mount'], value['process_path'], *value['ancestors']]:
            if not isinstance(path, str):
                raise sources.Refused('invalid-source', 'cgroup-provenance')
            sources.kernel_path(path)
        if value['ancestors'][-1] != value['mount']:
            raise sources.Refused('invalid-source', 'cgroup-provenance')
        if rows is not None:
            locations = [row['location'] for row in rows if row['name'] in ('cgroup-v2', 'cgroup-v2-root')]
            if locations != value['ancestors']:
                raise sources.Refused('invalid-source', 'cgroup-provenance')
    elif value['mode'] == ('none' if sys.platform == 'linux' else 'not-applicable'):
        if any(value[key] is not None for key in ('mount', 'mount_root', 'process_path')) or value['ancestors'] != []:
            raise sources.Refused('invalid-source', 'cgroup-provenance')
    else:
        raise sources.Refused('invalid-source', 'cgroup-provenance')
    return value


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise sources.Refused('invalid-input', 'arguments')


def main(argv=None):
    provenance = None
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('--reserve-fraction', type=float, required=True)
        parser.add_argument('--record-bytes', type=int, required=True)
        parser.add_argument('--output-dir', required=True)
        args = parser.parse_args(argv)
        numerator, denominator = reserve_ratio(args.reserve_fraction)
        sources.number(args.record_bytes, 'arguments', positive=True, code='invalid-input')
        snapshot = sources.collect(args.output_dir)
        # Do not echo malformed, unverified provenance inside the refusal for that very defect.
        provenance = cgroup_receipt(snapshot['cgroup'])
        rows, available = memory_receipts(snapshot['sources'])
        provenance = cgroup_receipt(provenance, rows)
        disk = snapshot['disk']
        if (not isinstance(disk, dict) or set(disk) != {'api', 'directory', 'available_bytes'}
                or disk['api'] not in ('fstatvfs', 'GetDiskFreeSpaceExW')
                or not isinstance(disk['directory'], str) or not disk['directory']):
            raise sources.Refused('invalid-source', 'output-volume')
        limits = calculate(available, args.reserve_fraction, args.record_bytes,
                           snapshot['cpu_count'], disk['available_bytes'])
        receipt = dict(schema=SCHEMA, status='ok', platform=sys.platform,
                       python=platform.python_version(), sampled_at=sampled_at(),
                       inputs=dict(reserve_numerator=str(numerator), reserve_denominator=str(denominator),
                                   record_bytes=str(args.record_bytes)), cpu_count=snapshot['cpu_count'],
                       cgroup=provenance, sources=rows,
                       disk={**disk, 'available_bytes': str(disk['available_bytes'])},
                       limits=limits, limitations=list(LIMITATIONS))
        print(json.dumps(receipt, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')))
        return 0
    except sources.Refused as refusal:
        receipt = dict(schema=SCHEMA, status='refused', platform=sys.platform,
                       code=refusal.code, source=refusal.source,
                       cgroup=refusal.cgroup if refusal.cgroup is not None else provenance)
        print(json.dumps(receipt, ensure_ascii=True, allow_nan=False, sort_keys=True,
                         separators=(',', ':')), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
