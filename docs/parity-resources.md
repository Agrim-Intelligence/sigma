# Inventory resource preflight

From the repository root, using the selected Python interpreter:

```sh
python tools/parity/resources.py --reserve-fraction 0.25 --record-bytes 256 --output-dir .
```

The reserve fraction is an explicit operator policy, not a fleet default. Supply your
observed encoded record size rather than treating the example's 256 bytes as a measurement.
The output directory must already exist. The command only reads machine facts: it creates
no directory, output file or bytecode cache, and starts no executable or network request.
It does not start an inventory scan or run a model.

Exit 0 prints one newline-terminated JSON receipt on stdout. Exit 2 prints one typed JSON
refusal on stderr, with no success stdout. Missing, malformed and unknown arguments use
that same refusal shape; `--help` is ordinary requested help. A refusal is not a successful
availability measurement. Do not silently fall back to copied limits or ignore it.

## What is measured

| Platform | Available memory | Additional constraint | Output volume |
| --- | --- | --- | --- |
| Linux | `/proc/meminfo` MemAvailable, checked against MemTotal | Every visible cgroup-v2 ancestor's memory.max minus memory.current; finite RLIMIT_AS minus current virtual bytes | Existing directory descriptor, fstatvfs f_bavail × f_frsize |
| macOS | In-process HOST_VM_INFO64 free + inactive pages × vm_kernel_page_size, checked against hw.memsize | Finite RLIMIT_AS minus TASK_VM_INFO revision-0 virtual_size | Existing directory descriptor, fstatvfs f_bavail × f_frsize |
| Windows | In-process GlobalMemoryStatusEx ullAvailPhys, checked against ullTotalPhys | No POSIX address-space source | GetDiskFreeSpaceExW per-caller available bytes on the held directory handle's GUID volume |

Linux virtual bytes use `/proc/self/statm`'s **first** field (total virtual pages) times the
measured SC_PAGE_SIZE, not resident memory. Active cgroup-v1 memory accounting refuses.
Ambiguous or non-root cgroup2 mount mappings refuse; paths containing traversal or control
characters refuse before use. All non-root accounting must be readable and valid, even if
another source would supply a smaller minimum. An explicit `max` is unlimited, not zero.
The genuine hierarchy root has no memory.max/current interface: its usage is unmeasured,
not an invented zero. A namespace can display an outer non-root group at its visible root:
when both accounting files exist there, their limit and usage remain applicable. A partial
pair or unreadable evidence refuses rather than becoming unlimited. Only an absent controller
with no mapped-root accounting is a distinct `none` mode.
Without a unified membership, `/proc/cgroups` must have its kernel header and valid,
unique controller rows with unsigned hierarchy/count fields and a binary enabled field.
Empty or malformed evidence refuses; a validated absent or disabled memory controller permits `none`.

Mach speculative pages are already included in free pages and are not added again. Native
return codes and returned structure sizes are checked; the acquired Mach host port is
released on success and failure. ctypes argument/return widths are explicitly declared.
Native binding/API failures refuse, with no shell-command fallback.

Output paths are resolved strictly and must be directories, not files or symlinks to files.
POSIX measures an O_DIRECTORY descriptor and checks its type/device/inode against the original
and post-measurement pathname identity. Windows opens an existing directory for metadata only,
checks its handle attributes, and obtains its canonical volume-GUID path. It measures that GUID
volume while holding the directory handle, then closes the handle on every path. The Windows
receipt names this canonical GUID directory, not a mutable drive/junction alias. Missing GUID
support or failed/truncated native results refuse; there is no weaker pathname fallback. Native
handle-path buffers are bounded by the Windows API's extended-path protocol limit, not a laptop
memory policy. A filesystem probe is still not a reservation for a later write.

These semantics follow the [Linux cgroup-v2 interface](https://docs.kernel.org/admin-guide/cgroup-v2.html),
the macOS SDK's mach/vm_statistics.h and mach/task_info.h declarations, and Microsoft's
[GlobalMemoryStatusEx](https://learn.microsoft.com/windows/win32/api/sysinfoapi/nf-sysinfoapi-globalmemorystatusex)
and [GetDiskFreeSpaceExW](https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getdiskfreespaceexw)
contracts. Local fixtures are not evidence that another operating system ran successfully.

## Integer allocation

Validate every finite source before selecting its minimum remaining capacity `A`. Inputs,
observed counts and finite source values must be real integers, not booleans, within the
executing interpreter's sys.maxsize. Fraction `r` must be finite and strictly between 0 and 1.
Use its exact binary-float ratio `p/q`, not rounded decimal arithmetic:

```text
U = A * (q-p) // q                 usable memory; reserve = A-U
D = U // 2; O = U-D                decode and output partitions
E = 1 + sizeof(uint32)             encoded byte plus worst-case decoded code point
H = getsizeof(b'') + getsizeof(chr(sys.maxunicode)) - sizeof(uint32)
R = E * record_bytes + H           one encoded record plus decoded text and object overhead
W = min(cpu_count, D // R)         simultaneous worker ceiling
B = (D // W - H) // E              encoded blob ceiling per worker
N = O // record_bytes              buffered output record ceiling
T = N * record_bytes               output byte ceiling
```

Refuse a nonpositive partition, zero workers (before division), blob or output ceiling.
Require available output-volume bytes ≥ `T`; exact equality passes. These invariants hold:

```text
W * (E*B + H) <= D
T <= O
D + O <= U <= A
```

All arithmetic uses Python integers. The two text-object overheads are measured from this
interpreter. This bounds raw bytes and decoded text only: it does **not** include an AST,
parser object graph, arbitrary library overhead, or other allocations by future callers.

For a hand-checked synthetic fixture only, A=100000, r=0.5, record_bytes=16, cpu_count=2,
H=112 and E=5 yield U=50000, D=O=25000, R=192, W=2, B=2477, N=1562, T=24992. Those
fixture values are not measurements of a developer machine or recommended operating limits.

## Receipt contract: parity-resource-preflight/v1

Byte/page counts and fraction numerator/denominator are canonical unsigned decimal **strings**.
CPU count, worker count and decode expansion are positive JSON integers. No floats or nonfinite
values are emitted. Unknown/unlimited values are JSON null, never fabricated numeric facts.

Success has exactly these keys:

- `schema`, `status`: `parity-resource-preflight/v1`, `ok`.
- `platform`, `python`, `sampled_at`: sys.platform, interpreter version, UTC ISO-8601 ending Z.
- `inputs`: reserve_numerator, reserve_denominator, record_bytes.
- `cpu_count`.
- `cgroup`: mode, mount, mount_root, process_path, ancestors. Mode is v2, none or
  not-applicable. v2 preserves the ordered process-to-mounted-root ancestor paths. The other
  modes have null paths and an empty ancestor list.
- `sources`: ordered rows with name, location, mode, limit_bytes, used_bytes, available_bytes,
  facts. Each finite row satisfies limit − used = available > 0. Unlimited rows have null
  limit/available; usage is measured when possible, otherwise null for the unaccounted root
  or unlimited RLIMIT_AS. Facts map unit-bearing names to decimal strings. Every selected
  cgroup ancestor has a row, including unlimited ancestors; root uses name cgroup-v2-root.
- `disk`: api, directory (resolved existing directory), available_bytes.
- `limits`: available_bytes, reserve_bytes, usable_bytes, decode_bytes, output_buffer_bytes,
  record_memory_bytes, object_overhead_bytes, blob_bytes_per_worker, output_records,
  output_bytes, worker_slots, decode_expansion.
- `limitations`: exactly snapshot-not-reservation, decoded-text-only, cpu-count-not-quota,
  caller-supplied-record-size, scan-throughput-unmeasured, in that order.

Refusal has exactly `schema`, `status` (`refused`), `platform`, `code`, `source` and `cgroup`.
`source` is a stable identifier, not arbitrary exception text. Before discovery, cgroup is null;
after discovery it preserves known provenance. A v1 refusal has mode v1-unsupported with its
safely parsed process_path and no invented mount/ancestors. Refusal codes are:

```text
invalid-input                       unsupported-platform
source-unavailable                  invalid-source
native-binding-unavailable          native-api-failed
cgroup-v1-memory-availability-unsupported
cgroup-mapping-unsupported          memory-exhausted
budget-exhausted                    disk-exhausted
```

An incompatible shape change requires an explicit contract/version decision. Paths are JSON
escaped, not printed as raw line-oriented records.

## Limits, scale and recovery

This is a snapshot, **not a memory/disk reservation**. Capacity may shrink immediately after a
read. Callers must enforce the returned ceilings, account for their other allocations, and rerun
preflight before allocating or retrying after an exhausted source. Read/binding/API failures
recover by correcting the unavailable source/platform and rerunning this read-only command;
it leaves no partial scan or persistent state to clear. No automatic retry hides bad facts.

Cgroup provenance covers visible ancestors only. A namespace can hide external ancestors:
this receipt does not attest host-wide headroom or hidden policy. CPU count is a scheduling
bound, not a job/container CPU-quota entitlement. Record size is caller-supplied, not attested.

Linux performs O(depth) memory-file probes and emits O(depth) rows, but path processing and
receipt **bytes** scale with the sum of ancestor path lengths. Growing paths can therefore
produce quadratic byte/work growth in nesting depth; a row count is not a timing. Mountinfo
parsing also scales with its text bytes, and output-directory resolution adds path-component
work. The macOS/Windows memory probes use a constant number of native calls, not a claimed
constant execution time. OS/filesystem latency is unmeasured. With fixed measured facts, 10×/100×
encoded record sizes reduce output record counts roughly 10×/100× (integer rounding applies),
increase per-record decode cost, and can reduce workers to zero, which refuses. Memory does
not grow with a repository's item count here: this command does not enumerate the repository.
Inventory scan throughput is **unmeasured** by this preflight.

## Evidence gates

```sh
python -m pytest tests/test_parity_resources.py tests/test_no_direct_gh.py -q -p no:cacheprovider
python -m pytest tests/test_no_direct_gh.py
```

Tests include source fixtures, native ABI construction, exact receipt/refusal checks, arithmetic
boundaries, no-side-effects, and individual broken/restored guard controls. Generic native pytest
permits a typed refusal on unsupported/constrained environments; it never turns that into an
availability claim.

The focused Parity resource native proof workflow separately runs the literal documented command
on Linux, macOS and Windows with Python 3.12 and requires a successful receipt for each. It logs
raw JSON and validates native source/provenance and budget consistency. Delivery requires inspected
receipts from all three jobs at the current PR head. Missing/failed jobs remain an explicit hold;
fixtures or historical runs cannot replace them. The larger B4 CI integration is a separate slice.
