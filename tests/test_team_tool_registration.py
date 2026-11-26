import importlib
import time
from types import SimpleNamespace

import pytest

from helpers import responses_tools, team_access, team_surfaces, tool_policy
from helpers.team_access import TEAM_EXECUTION_TOOL_ID, TEAM_OFFICE_ACTIONS, TEAM_OFFICE_TOOL_ID


_dispatch = importlib.import_module(
    "extensions.python._functions.agent.Agent.get_tool.start._10_team_tool"
)


def _agent():
    return SimpleNamespace(
        context=SimpleNamespace(get_data={team_access.CHAT_PROJECT_ID: "project-id"}.get),
        read_prompt=lambda name: name,
    )


def test_team_prompt_and_dispatch_agree(monkeypatch):
    monkeypatch.setattr(tool_policy, "get_policy", lambda _: {"mode": "inherit"})
    monkeypatch.setattr(tool_policy.plugins, "get_enabled_plugins", lambda _: {
        "_browser", "_code_execution", "_goal", "_memory", "_office", "_search_engine", "_text_editor", "_wait",
    })
    monkeypatch.setattr(responses_tools, "_vision_tool_prompt", lambda _: "Vision is configured")
    monkeypatch.setattr(responses_tools.subagents, "get_paths", lambda *args: [])
    agent = _agent()
    local = dict(responses_tools.team_tool_prompts(agent))
    assert set(local) == {
        "browser", "code_execution_tool", "goal", "memory", "office_artifact", "response",
        "search_engine", "skills_tool", "terminal", "text_editor", "vision_load", "wait",
    }
    schemas, names = responses_tools.build_responses_function_tools(agent)
    assert set(names.values()) == set(local)
    parameters = {names[tool["name"]]: tool["parameters"] for tool in schemas}
    assert parameters["terminal"]["required"] == ["command"]
    assert parameters["skills_tool"]["properties"]["skill_name"] == {"type": "string"}
    assert parameters["office_artifact"]["properties"]["action"]["enum"] == list(TEAM_OFFICE_ACTIONS)
    for name in local:
        hook = _dispatch.TeamToolResolution(agent=agent)
        data = {"args": (agent, name, None, {}, "", None), "kwargs": {}}
        hook.execute(data)
        assert data["result"] is not None and data["result"].name == name
        if name in {"terminal", "code_execution_tool"}:
            assert type(data["result"]).__name__ == "TeamTerminal"


@pytest.mark.parametrize("name", ["terminal", "code_execution_tool"])
def test_project_terminal_uses_native_policy_for_both_names(monkeypatch, name):
    monkeypatch.setattr(tool_policy.plugins, "get_enabled_plugins", lambda _: {"_code_execution"})
    policy = tool_policy.normalize_policy({
        "mode": "custom", "default": "block",
        "allowed": [f"{TEAM_EXECUTION_TOOL_ID}:terminal"],
    })
    agent = _agent()
    assert tool_policy.resolve_tool(agent, name, tool_args={"code": "pwd"}, _policy=policy).allowed
    assert not tool_policy.resolve_tool(agent, name, tool_args={"runtime": "python"}, _policy=policy).allowed
    assert not tool_policy.resolve_tool(agent, name, tool_args={"runtime": "reset"}, _policy=policy).allowed
    assert not tool_policy.resolve_tool(agent, "code_execution_remote", _policy=policy).allowed


def test_team_browser_tracks_plugin_activation(monkeypatch):
    monkeypatch.setattr(tool_policy.plugins, "get_enabled_plugins", lambda _: set())
    assert not tool_policy.resolve_tool(_agent(), "browser").allowed


def test_team_office_uses_plugin_activation_and_project_actions(monkeypatch):
    agent = _agent()
    monkeypatch.setattr(tool_policy.plugins, "get_enabled_plugins", lambda _: set())
    assert not tool_policy.resolve_tool(agent, "office_artifact").allowed
    monkeypatch.setattr(tool_policy.plugins, "get_enabled_plugins", lambda _: {"_office"})
    policy = tool_policy.normalize_policy({
        "mode": "custom", "default": "block",
        "allowed": [f"{TEAM_OFFICE_TOOL_ID}:read", f"{TEAM_OFFICE_TOOL_ID}:export"],
    })
    assert tool_policy.resolve_tool(agent, "office_artifact", action="extract", _policy=policy).allowed
    assert not tool_policy.resolve_tool(agent, "office_artifact", action="edit", _policy=policy).allowed
    assert not tool_policy.resolve_tool(agent, "office_artifact", action="export", _policy=policy).allowed
    policy = tool_policy.normalize_policy({
        "mode": "custom", "default": "block", "allowed": [f"{TEAM_OFFICE_TOOL_ID}:export"],
    })
    assert not tool_policy.resolve_tool(agent, "office_artifact", _policy=policy).allowed


def test_reaper_keeps_bound_admin_browser_and_reaps_revoked_member(monkeypatch):
    admin, revoked = (
        SimpleNamespace(actor_id=actor, project="test-project", project_id="stable-project",
                        touched=time.monotonic(), process=SimpleNamespace(poll=lambda: None))
        for actor in (team_access.ADMIN_ACTOR_ID, "revoked-account")
    )
    viewers = {"admin": admin, "revoked": revoked}
    monkeypatch.setattr(team_surfaces, "_viewers", viewers)
    monkeypatch.setattr(team_access, "bound_project_id", lambda name: "stable-project")
    monkeypatch.setattr(team_access, "assigned_projects", lambda actor: {})
    stopped = []
    monkeypatch.setattr(team_surfaces, "_stop", stopped.append)

    team_surfaces._reap()

    assert viewers == {"admin": admin}
    assert stopped == [revoked]
