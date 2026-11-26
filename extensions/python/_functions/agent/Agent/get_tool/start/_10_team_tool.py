from helpers import extract_tools, files
from helpers.extension import Extension
from helpers.team_access import TEAM_EXECUTION_TOOL_ID, TEAM_TOOL_IDS, is_team_chat
from helpers.tool import Tool
from helpers.tool_actions import bundled_path


class TeamToolResolution(Extension):
    def execute(self, data, **kwargs) -> None:
        if not self.agent or not is_team_chat(self.agent.context):
            return
        args = data["args"]
        tool_kwargs = data["kwargs"]
        name = tool_kwargs.get("name", args[1] if len(args) > 1 else "")
        tool_id = TEAM_TOOL_IDS.get(name)
        if not tool_id:
            data["result"] = None
            return
        if name == "response":
            path = "plugins/_goal/tools/response.py"
        elif tool_id == TEAM_EXECUTION_TOOL_ID:
            path = "plugins/_code_execution/helpers/project_terminal_tool.py"
        else:
            path = bundled_path(tool_id)
        classes = extract_tools.load_classes_from_file(files.get_abs_path(path), Tool)
        data["result"] = classes[0](*args, **tool_kwargs) if classes else None
