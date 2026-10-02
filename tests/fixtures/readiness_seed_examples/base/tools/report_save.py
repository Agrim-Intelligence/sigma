"""Save a report so that a reader never sees a half-written file."""
import json
import os


def save_report(path, report):
    """Write ``report`` as JSON to ``path``; the old file stays whole until the new one is complete."""
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def report_size(report):
    """The number of entries a report holds."""
    return len(report)
