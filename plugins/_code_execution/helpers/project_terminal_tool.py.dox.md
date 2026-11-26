# Team terminal tool

- The team resolver exposes this tool through `terminal` and the native `code_execution_tool` name. Both use the existing code execution permission ID.
- `terminal` takes `command`. `code_execution_tool` takes `runtime` and `code`. Both support optional project-relative `files` and a visible `skill` whose scripts are staged.
- The tool returns output and exit status with `break_loop=False`, and logs a generic terminal heading. Command execution belongs to `helpers/team_terminal.py`.
