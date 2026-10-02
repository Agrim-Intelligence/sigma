"""Pack the files of a report directory into one archive."""
import os
import subprocess


def pack_report(directory, archive):
    """Create the gzip tar ``archive`` from everything in ``directory``."""
    subprocess.run(["tar", "-czf", archive, "-C", directory, "."], check=True)
    return archive


def packable(directory):
    """The names in ``directory`` that ``pack_report`` would include, in a fixed order."""
    return sorted(os.listdir(directory))


def archive_name(directory):
    """The default archive name for ``directory``."""
    return os.path.basename(os.path.normpath(directory)) + ".tar.gz"
