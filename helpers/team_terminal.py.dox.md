# Project terminal execution

- `execute()` verifies the active team run and runs one command in a private temporary workspace with a reserved Linux UID.
- Project inputs are explicit paths, opened relative to the current project's safe workspace descriptor. Visible skill script and documentation files may be staged. Supply project configuration explicitly as an authorized input file.
- The shell closes after each command. Output and duration are bounded, project authorization is checked while work runs, and cleanup kills the owned process group and detached processes.
- The network bridge gives `curl` and standard Python `urllib.request` access to the public-address proxy through a private Unix socket. The child receives a restricted environment and a fixed runtime path.
- Nested `bash` commands found on the project `PATH`, including `bash -lc`, skip system login files and keep that environment and proxy path.
- Verify exit codes, visible project inputs, a mock authenticated read, rejection of project-external files and raw TCP, and process cleanup on stop.
