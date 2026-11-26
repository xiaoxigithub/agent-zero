"""Connect the native browser tool to its current run owner's project viewer."""

import asyncio
from contextvars import ContextVar
from urllib.parse import urlsplit

from helpers import ephemeral_images, projects, team_runs, team_surfaces
from plugins._browser.helpers.runtime import _BrowserRuntimeCore
from plugins._browser.helpers.url import normalize_url


_runtime: ContextVar[tuple[str, "ProjectBrowser"] | None] = ContextVar("team_browser_runtime", default=None)


def _browser_url(url):
    if not url or url == "about:blank":
        return "about:blank"
    target = normalize_url(url)
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Choose a public HTTP or HTTPS page.")
    return target


class ProjectBrowser(_BrowserRuntimeCore):
    def __init__(self, context):
        super().__init__(context.id)
        self.agent_context = context
        self.project = projects.get_context_project_name(context)
        self.endpoint = ""

    def _persist_browser_tabs(self, *, strict=False):
        # The project viewer owns its profile and tabs.
        pass

    def _load_restore_state(self):
        self._restore_state_loaded = True

    def _initial_url(self, url=""):
        return _browser_url(url)

    async def navigate(self, browser_id, url, **kwargs):
        return await super().navigate(browser_id, _browser_url(url), **kwargs)

    async def _start(self):
        from patchright.async_api import Error, async_playwright

        _, scope = team_runs.executing_scope(self.agent_context)
        endpoint = await asyncio.to_thread(team_surfaces.browser_endpoint, scope, self.project)
        self._closing = False
        self.playwright = await async_playwright().start()
        try:
            async with asyncio.timeout(12):
                while True:
                    try:
                        browser = await self.playwright.chromium.connect_over_cdp(endpoint, timeout=2000)
                        break
                    except Error:
                        await asyncio.sleep(0.2)
            self.context = browser.contexts[0]
            self.endpoint = endpoint
            self.context.set_default_timeout(15000)
            self.context.set_default_navigation_timeout(15000)
            self.context.on("close", self._on_context_closed)
            self.context.on("page", self._on_new_page_sync)
            for page in self.context.pages:
                await self._register_page(page, self.context_id)
        except TimeoutError as error:
            await self.disconnect()
            raise RuntimeError("Project Browser did not become ready.") from error
        except BaseException:
            await self.disconnect()
            raise

    async def _goto(self, page, url, *, wait_until="domcontentloaded"):
        await page.goto(url, wait_until=wait_until, timeout=15000)

    async def call(self, method, *args, **kwargs):
        _, scope = team_runs.executing_scope(self.agent_context)
        if self.context:
            endpoint = await asyncio.to_thread(team_surfaces.browser_endpoint, scope, self.project)
            if endpoint != self.endpoint:
                await self.disconnect()
        result = await getattr(self, method)(*args, **kwargs)
        team_runs.executing_scope(self.agent_context)
        return result

    async def disconnect(self):
        # Stopping the Playwright connection leaves the member's native viewer open.
        self._closing = True
        if self.playwright:
            await self._stop_playwright("Project Browser connection cleanup failed")
        self.context = None
        self.endpoint = ""
        self.pages.clear()

    async def screenshot_file(self, browser_id=None, *, quality=80, full_page=False, path=""):
        if path:
            raise ValueError("Project Browser screenshots are available in this chat through vision_load.")
        await self.ensure_started()
        browser_id = self._resolve_browser_id(browser_id)
        page = self._page(browser_id)
        screenshot = await page.screenshot(type="jpeg", quality=max(20, min(95, int(quality))), full_page=bool(full_page))
        ref = ephemeral_images.put_image_bytes(
            context_id=self.context_id, mime="image/jpeg", payload=screenshot, name=f"browser-{browser_id}.jpg",
        )
        return {
            "browser_id": browser_id,
            "vision_load": {"tool_name": "vision_load", "tool_args": {"paths": [ref]}},
        }

    async def upload_file(self, browser_id, ref, path="", paths=None):
        if paths is not None and not isinstance(paths, list):
            raise ValueError("Provide project-relative file paths.")
        project_paths = [*(paths or []), *([path] if path else [])]
        if not project_paths or len(project_paths) > 12:
            raise ValueError("Choose up to 12 files from this project.")
        await self.ensure_started()
        _, scope = team_runs.executing_scope(self.agent_context)
        staged = await asyncio.to_thread(team_surfaces.browser_upload_paths, scope, self.project, project_paths)
        return await super().upload_file(browser_id, ref, paths=staged)


async def get_runtime(context):
    run, _ = team_runs.executing_scope(context)
    active = _runtime.get()
    if active and active[0] == run.id:
        return active[1]
    browser = ProjectBrowser(context)
    _runtime.set((run.id, browser))
    team_runs.on_tool_run_end(browser.disconnect)
    return browser
