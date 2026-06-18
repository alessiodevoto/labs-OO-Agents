import json
import os
import shlex

from harbor.agents.installed.base import ExecInput
from util.harbor.pi_harness.agent import PiAgent


class PiAgentReasoningHigh(PiAgent):
    """PiAgent that actually emits reasoning_effort=high via `pi --thinking high`.

    The base PiAgent hardcodes compat.supportsReasoningEffort=False and model
    reasoning=False, so it never sends a thinking level. This subclass flips both
    on and appends `--thinking high` to the pi CLI invocation.
    """

    @staticmethod
    def name() -> str:
        return "pi-reasoning-high"

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        model = self.model_name or "nvidia/nvidia/nemotron-3-ultra-preview"
        model_arg = model if model.startswith(f"{self._provider}/") else f"{self._provider}/{model}"
        escaped_instruction = shlex.quote(instruction)
        config = {
            "providers": {
                self._provider: {
                    "baseUrl": self._api_base,
                    "api": "openai-completions",
                    "apiKey": "OPENAI_API_KEY",
                    "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": True},
                    "models": [
                        {
                            "id": model,
                            "name": model,
                            "reasoning": True,
                            "input": ["text"],
                            "contextWindow": 262144,
                            "maxTokens": 8192,
                            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        }
                    ],
                }
            }
        }
        config_json = json.dumps(config)
        env = {
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY")
            or os.environ.get("NVIDIA_INTERNAL_API_KEY", ""),
            "NVIDIA_INTERNAL_API_KEY": os.environ.get("NVIDIA_INTERNAL_API_KEY", ""),
            "OPENAI_BASE_URL": self._api_base,
            "OPENAI_API_BASE": self._api_base,
            "PI_OFFLINE": "1",
            "PI_TELEMETRY": "0",
        }
        return [
            ExecInput(
                command=f"mkdir -p ~/.pi/agent && cat > ~/.pi/agent/models.json <<'EOF'\n{config_json}\nEOF",
                env=env,
                timeout_sec=10,
            ),
            ExecInput(
                command=(
                    'export PATH="$HOME/.local/bin:$PATH"; '
                    "pi --version; "
                    f"pi --provider {shlex.quote(self._provider)} --model {shlex.quote(model_arg)} --thinking high --no-context-files --no-skills --no-prompt-templates --no-themes --tools read,bash,edit,write,grep,find,ls --no-session -p {escaped_instruction} "
                    "2>&1 | tee /logs/agent/pi.txt"
                ),
                env=env,
            ),
        ]
