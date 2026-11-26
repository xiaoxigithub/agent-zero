# Project terminal process launcher

- The launcher drops supplementary groups and privileges and sets file size, process, and core-dump limits before executing the chosen command.
- Landlock gives the process access to its temporary home and the runtime files it needs. Seccomp restricts socket creation to Unix sockets.
- Verify the launcher with the framework's Docker runtime and an isolated project configuration.
