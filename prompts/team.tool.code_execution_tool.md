## code_execution_tool

Run one command or script in the same isolated project workspace as `terminal`. Use `runtime` with `terminal`, `python` or `nodejs`, and pass the program in `code`. Stage project-relative input paths in `files` or a skill's scripts with `skill`. Use `text_editor` to publish project changes with revision checks.

Input schema for tool_args:
{"type":"object","properties":{"runtime":{"type":"string","enum":["terminal","python","nodejs"]},"code":{"type":"string"},"files":{"type":"array","items":{"type":"string"}},"skill":{"type":"string"}},"required":["runtime","code"]}
