import asyncio
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from helpers import projects, skills, team_access, team_projects, team_runs
from helpers.team_store import ADMIN_ACTOR_ID, TeamStore
from plugins._office.helpers import document_store, project_office
from plugins._office.tools.office_artifact import OfficeArtifact
from tools.skills_tool import SkillsTool


@pytest.fixture
def member_turn(tmp_path, monkeypatch):
    store = TeamStore(tmp_path / "access.sqlite3")
    account = store.create_account("writer", "qa-test-pass-1234", actor_id=ADMIN_ACTOR_ID)
    monkeypatch.setattr(projects, "get_projects_parent_folder", lambda: tmp_path)
    monkeypatch.setattr(projects, "get_context_project_name", lambda context: "p1")
    monkeypatch.setattr(project_office.FileBrowser, "max_file_bytes", lambda: 8 * 1024**2)
    for module in (team_access, team_projects, team_runs):
        monkeypatch.setattr(module, "get_store", lambda: store)
    for name in ("p1", "p2"):
        metadata = tmp_path / name / projects.PROJECT_META_DIR
        metadata.mkdir(parents=True)
        (metadata / projects.PROJECT_HEADER_FILE).write_text("{}", encoding="utf-8")
        team_access.register_project(name)
    project_id = store.project_id("p1")
    store.set_membership(account.id, project_id, True, actor_id=ADMIN_ACTOR_ID)
    run, _ = store.reserve_run(context_id="member-chat", project_id=project_id, account_id=account.id,
                               runtime_id="qa", request_id="request", request_digest="message")
    assert store.start_run(run)
    data = {team_access.CHAT_PROJECT_ID: run.project_id}
    context = SimpleNamespace(id=run.context_id, get_data=data.get, set_data=data.__setitem__)
    token = team_runs._executing_run.set(run)
    try:
        yield SimpleNamespace(context=context), store, account
    finally:
        team_runs._executing_run.reset(token)


@pytest.mark.parametrize(("extension", "xml"), [("odt", "content.xml"), ("docx", "word/document.xml")])
def test_member_can_create_read_and_edit_writer_file(member_turn, tmp_path, extension, xml):
    agent, _, _ = member_turn
    tool = OfficeArtifact(agent, "office_artifact", None, {}, "", None)
    path = f"Review.{extension}"
    (tmp_path / "p2" / path).write_bytes(document_store.template_bytes("document", extension, "Private", "Other project"))

    create = asyncio.run(tool.execute(action="create", path=path, content="Draft line."))
    assert create.message.startswith("{"), create.message
    before = json.loads(create.message)["document"]
    assert before["file_id"] == path and before["extension"] == extension
    assert (tmp_path / "p1" / path).is_file()
    read = asyncio.run(tool.execute(action="read", file_id=before["file_id"]))
    assert "Draft line." in json.loads(read.message)["content"]["text"]
    edit = asyncio.run(tool.execute(action="patch", file_id=path, revision=before["revision"],
                                    edit={"operation": "replace_text", "find": "Draft", "replace": "Reviewed"}))
    after = json.loads(edit.message)
    assert after["changed"] and after["document"]["revision"] != before["revision"]
    assert "Reviewed line." in after["preview"]["text"]
    with ZipFile(tmp_path / "p1" / path) as package:
        assert b"Reviewed line." in package.read(xml)
    with ZipFile(tmp_path / "p2" / path) as package:
        assert b"Other project" in package.read(xml)

    stale = asyncio.run(tool.execute(action="edit", path=path, revision=before["revision"], content="Stale"))
    duplicate = asyncio.run(tool.execute(action="create", path=path, content="Duplicate"))
    assert "version has changed" in stale.message and "version has changed" in duplicate.message
    assert "Reviewed line." in json.loads(asyncio.run(tool.execute(action="read", path=path)).message)["content"]["text"]


def test_member_access_is_rechecked_before_publishing_office_file(member_turn, tmp_path, monkeypatch):
    agent, store, account = member_turn
    prepare = project_office._prepare

    def revoke_during_render(*args):
        result = prepare(*args)
        store.set_membership(account.id, store.project_id("p1"), False, actor_id=ADMIN_ACTOR_ID)
        return result

    monkeypatch.setattr(project_office, "_prepare", revoke_during_render)
    tool = OfficeArtifact(agent, "office_artifact", None, {}, "", None)
    result = asyncio.run(tool.execute(action="create", path="Revoked.odt", content="Draft"))
    assert "no longer has project access" in result.message
    assert not (tmp_path / "p1" / "Revoked.odt").exists()


def test_edit_checks_revision_again_after_rendering(member_turn, tmp_path, monkeypatch):
    agent, _, _ = member_turn
    tool = OfficeArtifact(agent, "office_artifact", None, {}, "", None)
    path = "Shared.odt"
    created = asyncio.run(tool.execute(action="create", path=path, content="Draft"))
    revision = json.loads(created.message)["document"]["revision"]
    prepare = project_office._prepare
    concurrent = document_store.template_bytes("document", "odt", "Newer", "Another edit")

    def save_during_render(*args):
        result = prepare(*args)
        _, scope = team_runs.executing_scope(agent.context)
        with team_projects.project_lock("p1", scope), team_projects.open_directory("p1", "workspace", ()) as directory:
            team_projects._save_stream(directory, path, BytesIO(concurrent), revision, 8 * 1024**2)
        return result

    monkeypatch.setattr(project_office, "_prepare", save_during_render)
    response = asyncio.run(tool.execute(action="edit", path=path, revision=revision, content="Overlapping edit"))
    assert "version has changed" in response.message
    assert (tmp_path / "p1" / path).read_bytes() == concurrent


def test_invalid_office_package_fails_without_editing_source(member_turn, tmp_path):
    agent, _, _ = member_turn
    document = tmp_path / "p1" / "Invalid.docx"
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr("word/document.xml", "<word/>")
    source = package.getvalue()
    document.write_bytes(source)
    tool = OfficeArtifact(agent, "office_artifact", None, {}, "", None)
    response = asyncio.run(tool.execute(action="edit", path=document.name, revision=sha256(source).hexdigest(),
                                        operation="append_text", content="Rejected"))
    assert "valid, readable Office package" in response.message
    assert document.read_bytes() == source


@pytest.mark.parametrize(("name_arg", "name"), [
    ("name", "office-artifacts"),
    ("name", "writer-documents"),
    ("skill_name", "@[skill/writer-documents]"),
])
def test_member_loads_listed_skills_by_name(member_turn, monkeypatch, name_arg, name):
    agent, _, _ = member_turn
    catalog = [
        {"name": listed, "description": "An Office skill", "path": f"/a0/skills/{listed}", "hidden": False, "origin": "Built-in"}
        for listed in ("office-artifacts", "writer-documents")
    ]
    monkeypatch.setattr(skills, "list_skill_catalog", lambda *, agent, project_scope: catalog)
    monkeypatch.setattr(skills, "load_skill_for_agent", lambda path, agent: f"Instructions for {path}")
    monkeypatch.setattr(skills, "add_loaded_skill_name", lambda *args, **kwargs: None)
    item = name.removeprefix("@[skill/").removesuffix("]")
    tool = SkillsTool(agent, "skills_tool", None, {"action": "load", name_arg: name}, "", None)
    assert item in tool.get_heading()
    result = asyncio.run(tool.execute())
    assert result.additional, result.message
    assert result.additional["skill_instructions"]["name"] == item


def test_member_skill_load_reports_missing_name_separately(member_turn, monkeypatch):
    agent, _, _ = member_turn
    monkeypatch.setattr(skills, "list_skill_catalog", lambda **kwargs: [])
    tool = SkillsTool(agent, "skills_tool", None, {"action": "load"}, "", None)
    assert "Supply skill_name" in asyncio.run(tool.execute()).message
    assert "Select a skill available" in asyncio.run(tool.execute(skill_name="unavailable")).message
