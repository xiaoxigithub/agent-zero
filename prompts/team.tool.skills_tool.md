## skills_tool

Use `list` or `search` to find skills visible in this project. For `load` and `read_file`, pass the exact name shown in the list as `skill_name`. `read_file` also requires a relative Markdown or text `file_path`. To run the skill's scripts, pass its name to the project `terminal`.

Input schema for tool_args:
{"type":"object","properties":{"action":{"type":"string","enum":["list","search","load","read_file"]},"query":{"type":"string"},"skill_name":{"type":"string"},"file_path":{"type":"string"}},"required":["action"]}

Example:
```json
{"tool_name":"skills_tool","tool_args":{"action":"load","skill_name":"writer-documents"}}
```
