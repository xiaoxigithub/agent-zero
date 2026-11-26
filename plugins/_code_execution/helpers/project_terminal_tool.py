from helpers import team_terminal
from helpers.team_access import TEAM_EXECUTION_RUNTIMES
from helpers.tool import Response, Tool


class TeamTerminal(Tool):
    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool", heading="icon://terminal Project Terminal", content="",
        )

    async def before_execution(self, **kwargs):
        self.log = self.get_log_object()

    async def execute(self, **kwargs):
        mode = str(self.args.get("runtime") or "terminal").strip().lower()
        if mode not in TEAM_EXECUTION_RUNTIMES:
            return Response(message="Choose terminal, python or nodejs. Each project command returns its own output.", break_loop=False)
        command = self.args.get("command") or self.args.get("code")
        try:
            output = await team_terminal.execute(
                self.agent, mode, command, self.args.get("files", []), self.args.get("skill", ""),
            )
        except (ValueError, OSError, RuntimeError) as exc:
            return Response(message=f"Project Terminal: {exc}", break_loop=False)
        await self.agent.handle_intervention(output)
        return Response(message=output, break_loop=False)
