"""Drop privileges and sandbox a single team terminal command."""

import os
from pathlib import Path
import resource
import sys

from team_surface_sandbox import _EXECUTE, _FS_ACCESS, _LIST, _READ, _WRITE, apply_sandbox


def main():
    raw_uid, mode, command = sys.argv[1:]
    uid = int(raw_uid)
    home = Path(os.environ["HOME"])
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    paths = [
        (path, _EXECUTE | _READ | _LIST)
        for path in (
            "/usr/bin", "/usr/lib", "/usr/lib64", "/usr/local/bin", "/usr/local/lib",
            "/usr/share", "/usr/local/share", "/opt/venv", "/etc/alternatives",
            "/etc/ssl", "/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/group",
        )
    ]
    paths.extend((path, _READ | _WRITE) for path in ("/dev/null", "/dev/zero", "/dev/urandom", "/dev/random"))
    paths.extend(((home, _FS_ACCESS), (home.parent / "bridge", _READ | _LIST | _EXECUTE)))
    apply_sandbox(paths, unix_only=True)
    executable = {"terminal": "/bin/bash", "python": "python3", "nodejs": "node"}[mode]
    os.execvp(executable, [executable, command])


if __name__ == "__main__":
    main()
