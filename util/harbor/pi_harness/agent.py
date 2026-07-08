import json
import os
import shlex

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class PiAgent(BaseInstalledAgent):
    """Harbor adapter for @mariozechner/pi-coding-agent v0.72.1."""

    @staticmethod
    def name() -> str:
        return "pi"

    def __init__(
        self,
        provider: str = "nvidia",
        api_base: str = "https://inference-api.nvidia.com/v1",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._provider = provider
        self._api_base = api_base

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=(
                "apt-get update && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates && "
                # uv-on-PATH fix: SWE-bench verifier test.sh runs `uv run parser.py`;
                # symlink the harbor uv into a world-readable global PATH dir so the
                # non-root agent user (exec_as_agent) resolves it at verify time.
                "ln -sf /opt/harbor/bin/uv /usr/local/bin/uv 2>/dev/null || true"
            ),
            timeout_sec=1200,
        )
        version = self.version() or "0.72.1"
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash; "
                'export NVM_DIR="$HOME/.nvm"; '
                '. "$NVM_DIR/nvm.sh"; '
                "nvm install 22; "
                "npm -v; "
                f"npm install -g @mariozechner/pi-coding-agent@{shlex.quote(version)}; "
                "pi --version"
            ),
            timeout_sec=1200,
        )

    def _models_json(self, *, reasoning: bool = False, max_tokens: int = 8192) -> str:
        model = self.model_name or "nvidia/nvidia/nemotron-3-ultra-preview"
        config = {
            "providers": {
                self._provider: {
                    "baseUrl": self._api_base,
                    "api": "openai-completions",
                    "apiKey": "OPENAI_API_KEY",
                    "compat": {
                        "supportsDeveloperRole": False,
                        "supportsReasoningEffort": reasoning,
                    },
                    "models": [
                        {
                            "id": model,
                            "name": model,
                            "reasoning": reasoning,
                            "input": ["text"],
                            "contextWindow": 262144,
                            "maxTokens": max_tokens,
                            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        }
                    ],
                }
            }
        }
        return json.dumps(config)

    def _env(self) -> dict[str, str]:
        return {
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY")
            or os.environ.get("NVIDIA_INTERNAL_API_KEY", ""),
            "NVIDIA_INTERNAL_API_KEY": os.environ.get("NVIDIA_INTERNAL_API_KEY", ""),
            "OPENAI_BASE_URL": self._api_base,
            "OPENAI_API_BASE": self._api_base,
            "PI_OFFLINE": "1",
            "PI_TELEMETRY": "0",
        }

    def _run_command(self, instruction: str, *, thinking_high: bool = False) -> str:
        model = self.model_name or "nvidia/nvidia/nemotron-3-ultra-preview"
        model_arg = model if model.startswith(f"{self._provider}/") else f"{self._provider}/{model}"
        escaped_instruction = shlex.quote(instruction)
        thinking = "--thinking high " if thinking_high else ""
        return (
            'export NVM_DIR="$HOME/.nvm"; '
            '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
            "nvm use default >/dev/null 2>&1 || true; "
            'export PATH="$HOME/.local/bin:$PATH"; '
            "pi --version; "
            f"pi --provider {shlex.quote(self._provider)} --model {shlex.quote(model_arg)} "
            f"{thinking}--mode json --no-context-files --no-skills --no-prompt-templates --no-themes "
            f"--tools read,bash,edit,write,grep,find,ls --no-session -p {escaped_instruction} "
            "2>&1 | tee /logs/agent/pi.txt"
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        config_json = self._models_json(reasoning=False, max_tokens=8192)
        env = self._env()
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p ~/.pi/agent && cat > ~/.pi/agent/models.json <<'EOF'\n{config_json}\nEOF",
            env=env,
            timeout_sec=10,
        )
        await self.exec_as_agent(
            environment,
            command=self._run_command(instruction, thinking_high=False),
            env=env,
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        output_file = self.logs_dir / "pi.txt"
        if not output_file.exists():
            return

        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_read_tokens = 0
        total_cache_write_tokens = 0
        total_cost = 0.0

        for line in output_file.read_text(errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "message_end":
                continue
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            usage = message.get("usage") or {}
            total_input_tokens += usage.get("input", 0)
            total_output_tokens += usage.get("output", 0)
            total_cache_read_tokens += usage.get("cacheRead", 0)
            total_cache_write_tokens += usage.get("cacheWrite", 0)
            cost = usage.get("cost") or {}
            total_cost += cost.get("total", 0.0)

        context.n_input_tokens = total_input_tokens + total_cache_read_tokens
        context.n_output_tokens = total_output_tokens
        context.n_cache_tokens = total_cache_read_tokens + total_cache_write_tokens
        context.cost_usd = total_cost if total_cost > 0 else None
