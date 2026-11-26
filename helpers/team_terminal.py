"""Run approved project shell calls with a private filesystem and public HTTP proxy."""

import asyncio
from contextlib import contextmanager, suppress
import os
from pathlib import Path
import pwd
import signal
import tempfile
from threading import Lock

from helpers import files, projects, skills, team_projects, team_runs
from helpers.team_surface_proxy import ProjectUnixWebProxy
from helpers.team_surfaces import _stop_detached_processes


_uid_lock = Lock()
_active_uids: set[int] = set()
_COMMAND_TIMEOUT = 45
_OUTPUT_LIMIT = 32_768
_INPUT_LIMIT = 16 * 1024**2
_PROXY_HOST = "agent-zero-project-proxy"
_PYTHON_BRIDGE = """\
import os
import socket

_original = socket.create_connection

class _ProxySocket(socket.socket):
    def setsockopt(self, level, option, value, *args):
        if level == socket.IPPROTO_TCP and option == socket.TCP_NODELAY:
            return None
        return super().setsockopt(level, option, value, *args)

def _project_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    if address != ("agent-zero-project-proxy", 80):
        return _original(address, timeout, source_address, **kwargs)
    connection = _ProxySocket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            connection.settimeout(timeout)
        connection.connect(os.environ["A0_PROJECT_PROXY_SOCKET"])
        return connection
    except BaseException:
        connection.close()
        raise

socket.create_connection = _project_connection
"""


@contextmanager
def _terminal_uid():
    if os.geteuid() != 0 or not Path("/.dockerenv").is_file():
        raise RuntimeError("Project Terminal requires the Linux team Docker runtime.")
    with _uid_lock:
        for uid in range(63200, 63300):
            if uid in _active_uids:
                continue
            try:
                pwd.getpwuid(uid)
            except KeyError:
                _active_uids.add(uid)
                break
        else:
            raise RuntimeError("The local Project Terminal capacity is in use.")
    try:
        _stop_detached_processes(uid)
        yield uid
    finally:
        _stop_detached_processes(uid)
        with _uid_lock:
            _active_uids.discard(uid)


def _copy(source, destination, uid, remaining):
    size = os.fstat(source.fileno()).st_size
    if size > remaining:
        raise ValueError("The selected Terminal inputs exceed the size limit.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = source.read(remaining + 1)
    if len(raw) > remaining:
        raise ValueError("The selected Terminal inputs exceed the size limit.")
    with destination.open("xb") as target:
        target.write(raw)
    os.chown(destination, uid, uid)
    return remaining - len(raw)


def _stage_files(project, scope, project_files, workspace, uid):
    if not isinstance(project_files, list) or len(project_files) > 32:
        raise ValueError("Supply up to 32 project-relative input file paths.")
    remaining = _INPUT_LIMIT
    with team_projects.project_lock(project, scope):
        for path in project_files:
            parts = team_projects.file_parts(path)
            with team_projects.open_directory(project, "workspace", parts[:-1]) as directory:
                with team_projects.open_file(directory, parts[-1]) as source:
                    remaining = _copy(source, workspace.joinpath(*parts), uid, remaining)


def _skill_files(directory, parent=()):
    with os.scandir(directory) as entries:
        for entry in entries:
            name = entry.name
            if name.startswith((".", "__")):
                continue
            parts = (*parent, name)
            if entry.is_dir(follow_symlinks=False):
                with team_projects.open_path_directory(directory, (name,)) as child:
                    yield from _skill_files(child, parts)
            elif entry.is_file(follow_symlinks=False) and Path(name).suffix in {".md", ".py", ".sh", ".js", ".mjs"}:
                if name.lower().startswith(("config", "secret", "credential")):
                    continue
                with team_projects.open_file(directory, name) as source:
                    yield parts, source


def _stage_skill(agent, name, destination, uid):
    entry = skills.project_skill(name, agent)
    if not entry:
        raise ValueError("Choose a skill available in this project.")
    root = Path(files.fix_dev_path(entry["path"]))
    remaining = _INPUT_LIMIT
    # Installed skills contribute scripts and documentation. Credentials
    # are provided explicitly as authorized project files.
    with team_projects.open_path_directory(root) as directory:
        for count, (parts, source) in enumerate(_skill_files(directory), start=1):
            if count > 128:
                raise ValueError("The selected Terminal skill has too many script files.")
            remaining = _copy(source, destination.joinpath(*parts), uid, remaining)


def _create_bridge(bridge):
    bridge.mkdir()
    (bridge / "sitecustomize.py").write_text(_PYTHON_BRIDGE, encoding="utf-8")
    bash = bridge / "bash"
    bash.write_text('#!/bin/sh\nexec /bin/bash --noprofile --norc "$@"\n', encoding="utf-8")
    bash.chmod(0o755)
    curl = bridge / "curl"
    curl.write_text(
        '#!/bin/sh\nexec /usr/bin/curl --proxy "socks5h://localhost$A0_PROJECT_PROXY_SOCKET" '
        '--noproxy "" "$@"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)


async def _capture(process):
    output = bytearray()
    truncated = False
    while chunk := await process.stdout.read(4096):
        remaining = _OUTPUT_LIMIT - len(output)
        output.extend(chunk[:remaining])
        truncated |= len(chunk) > remaining
    await process.wait()
    suffix = "\n[Output truncated]" if truncated else ""
    return output.decode("utf-8", errors="replace") + suffix


async def _run_process(agent, mode, script, workspace, home, bridge, socket_path, uid, skill_dir):
    proxy_url = f"http://{_PROXY_HOST}:80"
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/python3", "-Es", files.get_abs_path("helpers", "team_terminal_sandbox.py"),
        str(uid), mode, str(script),
        cwd=workspace,
        env={
            "HOME": str(home),
            "TMPDIR": str(home),
            "PATH": f"{bridge}:/opt/venv/bin:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONPATH": str(bridge),
            "PYTHONDONTWRITEBYTECODE": "1",
            "A0_PROJECT_PROXY_SOCKET": str(socket_path),
            "A0_SKILL_DIR": str(skill_dir) if skill_dir else "",
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
        },
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    output = asyncio.create_task(_capture(process))
    try:
        async with asyncio.timeout(_COMMAND_TIMEOUT):
            while not output.done():
                await asyncio.wait({output}, timeout=0.5)
                team_runs.executing_scope(agent.context)
            return f"Exit code: {process.returncode}\n{output.result()}"
    except TimeoutError:
        return f"Project Terminal timed out after {_COMMAND_TIMEOUT} seconds."
    finally:
        if not output.done():
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        _stop_detached_processes(uid)
        if not output.done():
            await asyncio.gather(output, return_exceptions=True)


async def execute(agent, mode, command, project_files, skill_name):
    _, scope = team_runs.executing_scope(agent.context)
    project = projects.get_context_project_name(agent.context)
    if not isinstance(command, str) or not command.strip() or len(command) > 65_536:
        raise ValueError("Supply a nonempty command within the Terminal input limit.")

    root = Path(files.get_abs_path("tmp", "team-terminal"))
    root.mkdir(mode=0o711, parents=True, exist_ok=True)
    with _terminal_uid() as uid, tempfile.TemporaryDirectory(prefix="command-", dir=root) as directory:
        directory = Path(directory)
        directory.chmod(0o711)
        home = directory / "home"
        workspace = home / "workspace"
        home.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        for path in (home, workspace):
            os.chown(path, uid, uid)
        _stage_files(project, scope, project_files, workspace, uid)
        script = home / "command"
        script.write_text(command, encoding="utf-8")
        script.chmod(0o400)
        os.chown(script, uid, uid)
        skill_dir = home / "skill" if skill_name else None
        if skill_dir:
            _stage_skill(agent, skill_name, skill_dir, uid)
        bridge = directory / "bridge"
        _create_bridge(bridge)
        socket_path = bridge / "http.sock"
        proxy = ProjectUnixWebProxy(str(socket_path))
        try:
            os.chown(socket_path, uid, uid)
            socket_path.chmod(0o600)
            return await _run_process(agent, mode, script, workspace, home, bridge, socket_path, uid, skill_dir)
        finally:
            try:
                await asyncio.to_thread(proxy.shutdown)
            finally:
                proxy.server_close()
