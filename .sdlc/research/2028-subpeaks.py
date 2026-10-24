import json, pathlib, statistics, sys, time
root = pathlib.Path.home() / ".claude" / "projects"
starts, peaks, n_files = [], [], 0
cutoff = time.time() - 30 * 86400
for f in root.glob("*/*/subagents/agent-*.jsonl"):
    try:
        if f.stat().st_mtime < cutoff: continue
    except OSError: continue
    first = peak = None
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"usage"' not in line: continue
                try: d = json.loads(line)
                except ValueError: continue
                m = d.get("message") if isinstance(d, dict) else None
                u = m.get("usage") if isinstance(m, dict) else None
                if not isinstance(u, dict): continue
                ctx = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                if ctx <= 0: continue
                if first is None: first = ctx
                peak = ctx if peak is None else max(peak, ctx)
    except OSError: continue
    if peak is None: continue
    n_files += 1; starts.append(first); peaks.append(peak)
def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(p * len(xs)))]
print("subagent transcripts (last 30 days):", n_files)
for name, xs in (("first-turn context", starts), ("peak context", peaks)):
    print(name, "median", int(statistics.median(xs)), "p90", q(xs, .9), "p99", q(xs, .99), "max", max(xs))
print("share of peaks >= 100k:", round(sum(p >= 100_000 for p in peaks) / len(peaks), 3),
      ">= 200k:", round(sum(p >= 200_000 for p in peaks) / len(peaks), 3))
