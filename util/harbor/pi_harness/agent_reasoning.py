
import json

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from util.harbor.pi_harness.agent import PiAgent


class PiAgentReasoningHigh(PiAgent):
    """PiAgent that emits reasoning_effort=high via `pi --thinking high`."""

    @staticmethod
    def name() -> str:
        return "pi-reasoning-high"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        config_json = self._models_json(reasoning=True, max_tokens=16000)
        env = self._env()
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p ~/.pi/agent && cat > ~/.pi/agent/models.json <<'EOF'\n{config_json}\nEOF",
            env=env,
            timeout_sec=10,
        )
        await self.exec_as_agent(
            environment,
            command=self._run_command(instruction, thinking_high=True),
            env=env,
        )
