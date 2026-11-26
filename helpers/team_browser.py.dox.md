# Project browser automation

- `ProjectBrowser` binds the native browser actions to the project Browser viewer for the executing account and project. Active run authorization is checked before the call and before returning its result.
- The Browser uses a loopback DevTools endpoint and the viewer's existing public-address proxy. Browser tabs and credentials stay within the viewer.
- Uploads stage visible project files through the project file helpers. Screenshots yield chat-scoped image references to the scoped vision tool. Runtime cleanup disconnects Playwright from the viewer after the agent turn.
- Verify real Chromium navigation and DOM extraction, the same viewer's visible tabs, screenshot scope, and access after project revocation.
