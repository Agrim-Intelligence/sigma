# Scale probe (D5)

Measured by `tools/readiness/scale_probe.py` at commit `4d16cfe49c34e42ac3e1857f1ed814987029191c`; raw
numbers in `scale-probe-4d16cfe49c34.json`. macOS, 12 CPUs, one machine, warm cache, median of 3 wall
times of a fresh process, run natively on macOS with no other heavy job running. Synthetic scratch `.sdlc`.

| Path | 1x | 10x | 100x |
| --- | --- | --- | --- |
| `sigma-log slots`, goals with 14 action-log events each | 85: 0.037 s | 850: 0.128 s | 8,500: 1.071 s |
| `liveness_prune.py sweep --dry-run --limit 0`, aged `claims/*.lock` | 1,000: 0.397 s | 10,000: 0.575 s | 100,000: 2.476 s |

The slots reader is the serialised chokepoint of the action log: about 1.1 s per call at 8,500 goals,
roughly linear past 850 (the earlier page measured 0.68 s at the same count; this run is the larger,
and the difference is machine and synthetic-event shape, not explained further). The claims sweep includes a
fixed process start of about 0.3 s; 100,000 files cost about 2.1 s above that.

Not measured: Linux timings of these two probes, a real filesystem's directory-lookup cost beyond the
files here, any path in the table not listed above.
