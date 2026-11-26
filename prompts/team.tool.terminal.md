## terminal

Run a shell command in an isolated project workspace. The command returns its output and exit code when it exits. The workspace is temporary. Pass project-relative input paths in `files` and a skill name in `skill` to stage its scripts under `$A0_SKILL_DIR`. To use a project-owned configuration, stage it with `files` and give the script its path. Keep credentials out of command output. Use `curl` or Python's `urllib.request` for public HTTP or HTTPS.

Input schema for tool_args:
{"type":"object","properties":{"command":{"type":"string"},"files":{"type":"array","items":{"type":"string"}},"skill":{"type":"string"}},"required":["command"]}
