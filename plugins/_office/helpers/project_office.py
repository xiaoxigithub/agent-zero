"""Read and write native Writer artifacts in the active team's project."""

import asyncio
from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from helpers import projects, team_projects, team_runs
from helpers.file_browser import FileBrowser
from helpers.file_transfers import FileLimitExceeded
from helpers.team_access import TEAM_OFFICE_ACTIONS, TEAM_OFFICE_TOOL_ID
from helpers.tool import Response
from helpers.tool_actions import BUNDLED_ACTIONS
from plugins._office.helpers import artifact_editor, document_store


FORMATS = ("odt", "docx")
_PACKAGE_LIMIT = 16 * 1024**2
_UNPACKED_LIMIT = 32 * 1024**2
_TEXT_LIMIT = 4 * 1024**2
_EDIT_ARGS = {
    "operation", "content", "find", "replace", "count",
    "text", "value", "body", "old", "old_text", "new", "new_text", "replacement",
    "add_lines", "append_lines", "edit", "edits", "update", "patch",
}


def _target(action, args):
    path = args["path"] or args["file_id"]
    if args["path"] and args["file_id"] and args["path"] != args["file_id"]:
        raise ValueError("Use the same project file for path and file_id.")
    if not isinstance(path, str):
        raise ValueError("Choose a project-relative Office file.")
    extension = PurePosixPath(path).suffix.lower().lstrip(".")
    if action == "create":
        if str(args["kind"]).lower() not in {"document", "writer", ""}:
            raise ValueError("Choose a Writer document (.odt or .docx).")
        extension = str(args["format"] or extension or "odt").strip().lower().lstrip(".")
        if not path:
            stem = document_store.safe_document_stem(str(args["title"])[:60], extension)
            path = f"{stem}.{extension}"
        if PurePosixPath(path).suffix.lower() != f".{extension}":
            raise ValueError("The project filename must match the document format.")
    if extension not in FORMATS:
        raise ValueError("Choose a project-relative .odt or .docx file.")
    return path, team_projects.file_parts(path), extension


def _validate_package(data, extension, limit):
    if len(data) > limit:
        raise FileLimitExceeded(limit)
    body = "content.xml" if extension == "odt" else "word/document.xml"
    with ZipFile(BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 1024 or sum(member.file_size for member in members) > _UNPACKED_LIMIT:
            raise ValueError("The Office package exceeds the unpacked size or member limit.")
        if len({member.filename for member in members}) != len(members):
            raise ValueError("The Office package contains duplicate names.")
        if any(member.flag_bits & 1 for member in members):
            raise ValueError("Choose an Office document without password protection.")
        if archive.getinfo(body).file_size > _TEXT_LIMIT:
            raise ValueError("The Office document text exceeds the size limit.")
        if extension == "odt":
            mime = document_store.ODF_MIMETYPES["odt"]
            if archive.getinfo("mimetype").file_size != len(mime) or archive.read("mimetype") != mime.encode():
                raise ValueError("Choose a valid ODT document.")
        else:
            archive.getinfo("[Content_Types].xml")


def _prepare(action, extension, data, args, limit):
    if action == "create":
        content = args["content"]
        if not isinstance(content, str) or len(content.encode()) > _TEXT_LIMIT:
            raise ValueError("Supply document text within the Office text limit.")
        data = document_store.template_bytes("document", extension, str(args["title"])[:200], content)
    _validate_package(data, extension, limit)
    result = {"ok": True, "action": action}
    if action == "read":
        max_chars = max(1, min(12_000, int(args["max_chars"] or 12_000)))
        result["content"] = artifact_editor.read_writer_artifact(BytesIO(data), extension, max_chars)
    elif action == "edit":
        edit_args = {key: args[key] for key in _EDIT_ARGS if key in args}
        if len(json.dumps(edit_args, ensure_ascii=False).encode()) > _TEXT_LIMIT:
            raise ValueError("The Office edit exceeds the text limit.")
        data, result = artifact_editor.render_artifact_edit(data, extension, **edit_args)
        if result["changed"]:
            _validate_package(data, extension, limit)
        result["preview"] = artifact_editor.read_writer_artifact(BytesIO(data), extension, 4000)
    return data, result


async def execute(agent, action, args):
    action = BUNDLED_ACTIONS[TEAM_OFFICE_TOOL_ID].normalize(action)
    try:
        run, scope = team_runs.executing_scope(agent.context)
        project = projects.get_context_project_name(agent.context)
        if action not in TEAM_OFFICE_ACTIONS:
            raise ValueError("Choose create, open, read, edit, inspect or status for project Office files.")
        if action == "status":
            result = {"ok": True, "action": action, "formats": FORMATS, "actions": TEAM_OFFICE_ACTIONS}
        else:
            result = await _operate(agent, run, scope, project, action, args)
        return Response(message=json.dumps(result, ensure_ascii=False), break_loop=False)
    except (BadZipFile, KeyError, ParseError, NotImplementedError):
        return Response(message=f"office_artifact {action} failed: Choose a valid, readable Office package.", break_loop=False)
    except (TypeError, ValueError) as error:
        return Response(message=f"office_artifact {action} failed: {error}", break_loop=False)
    except OSError as error:
        message = str(error) if isinstance(error, PermissionError) and error.errno is None else team_projects.safe_file_error(error)[0]
        return Response(message=f"office_artifact {action} failed: {message}", break_loop=False)


async def _operate(agent, run, scope, project, action, args):
    path, parts, extension = _target(action, args)
    limit = min(FileBrowser.max_file_bytes(), _PACKAGE_LIMIT)
    parent, filename = parts[:-1], parts[-1]
    data, revision = b"", None
    with team_projects.activity(f"tool.office_artifact.{action}", run.account_id, run.project_id, context_id=run.context_id):
        if action != "create":
            with team_projects.project_lock(project, scope), team_projects.open_directory(project, "workspace", parent) as directory:
                with team_projects.open_file(directory, filename) as source:
                    data = source.read(limit + 1)
            if len(data) > limit:
                raise FileLimitExceeded(limit)
            revision = sha256(data).hexdigest()
            if action == "edit":
                if not args.get("revision"):
                    raise ValueError("Read the document and pass its revision before editing.")
                team_projects.require_revision(args["revision"], revision)

        data, result = await asyncio.to_thread(_prepare, action, extension, data, args, limit)
        _, scope = team_runs.executing_scope(agent.context)
        with team_projects.project_lock(project, scope):
            if action in {"create", "edit"}:
                with team_projects.open_directory(project, "workspace", parent) as directory:
                    if action == "create" or result["changed"]:
                        receipt = team_projects._save_stream(directory, filename, BytesIO(data), revision, limit)
                        revision = receipt["revision"]
                    else:
                        team_projects.require_revision(revision, team_projects._existing_revision(directory, filename))
    result["document"] = {
        "file_id": path, "path": path, "name": filename, "extension": extension,
        "size": len(data), "revision": revision,
    }
    return result
