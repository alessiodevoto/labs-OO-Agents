import json
import os
import shlex
from pathlib import Path
from typing import Any

import yaml

from harbor.agents.installed.base import BaseInstalledAgent, ExecInput
from harbor.models.agent.context import AgentContext


class HermesAgent(BaseInstalledAgent):
    """Harbor adapter for NousResearch/hermes-agent via its CLI."""

    @staticmethod
    def name() -> str:
        return "hermes"

    @property
    def _install_agent_template_path(self) -> Path:
        return Path(__file__).parent / "install-hermes.sh.j2"

    def _gateway_model(self) -> str:
        model = self.model_name or "openai/azure/openai/gpt-5.5"
        if model.startswith("openai/"):
            return model[len("openai/") :]
        return model

    def _build_config_yaml(self) -> str:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("NVIDIA_INTERNAL_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "https://inference-api.nvidia.com/v1"
        config: dict[str, Any] = {
            "model": {
                "provider": "custom",
                "default": self._gateway_model(),
                "base_url": base_url,
                "api_key": api_key,
                "api_mode": "chat_completions",
            },
            "toolsets": ["hermes-cli"],
            "agent": {"max_turns": 90},
            "memory": {"memory_enabled": False, "user_profile_enabled": False},
            "compression": {"enabled": True, "threshold": 0.85},
            "terminal": {"backend": "local", "timeout": 180},
            "delegation": {"max_iterations": 50},
            "checkpoints": {"enabled": False},
        }
        return yaml.dump(config, default_flow_style=False)

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        escaped_instruction = shlex.quote(instruction)
        model = self._gateway_model()
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("NVIDIA_INTERNAL_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "https://inference-api.nvidia.com/v1"
        config_yaml = self._build_config_yaml()
        env = {
            "HERMES_HOME": "/tmp/hermes",
            "OPENAI_API_KEY": api_key,
            "NVIDIA_INTERNAL_API_KEY": os.environ.get("NVIDIA_INTERNAL_API_KEY", api_key),
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_BASE": base_url,
        }
        return [
            ExecInput(
                command=(
                    "mkdir -p /tmp/hermes /tmp/hermes/sessions /tmp/hermes/skills /tmp/hermes/memories /logs/agent && "
                    "cat > /tmp/hermes/config.yaml <<'EOF'\n"
                    f"{config_yaml}"
                    "EOF\n"
                    "cat > /tmp/hermes/.env <<'EOF'\n"
                    f"OPENAI_API_KEY={api_key}\n"
                    f"OPENAI_BASE_URL={base_url}\n"
                    f"OPENAI_API_BASE={base_url}\n"
                    f"NVIDIA_INTERNAL_API_KEY={os.environ.get('NVIDIA_INTERNAL_API_KEY', api_key)}\n"
                    "EOF\n"
                    'export PATH="$HOME/.local/bin:$PATH"; '
                    "echo HERMES_VERSION_BEGIN; hermes version || hermes --version || true; echo HERMES_VERSION_END"
                ),
                env=env,
                timeout_sec=30,
            ),
            ExecInput(
                command=(
                    "bash -lc "
                    + shlex.quote(
                        'export PATH="$HOME/.local/bin:$PATH"; '
                        f"hermes --yolo chat -q {escaped_instruction} -Q --model {shlex.quote(model)} --provider custom "
                        "2>&1 | stdbuf -oL tee /logs/agent/hermes.txt; "
                        "rc=${PIPESTATUS[0]}; "
                        "hermes sessions export /logs/agent/hermes-session.jsonl --source cli 2>/dev/null || true; "
                        "test -s /logs/agent/hermes-session.jsonl && cp /logs/agent/hermes-session.jsonl /logs/agent/hermes-session-copy.jsonl || true; "
                        "exit $rc"
                    )
                ),
                env=env,
            ),
        ]

    def populate_context_post_run(self, context: AgentContext) -> None:
        session_file = self.logs_dir / "hermes-session.jsonl"
        if not session_file.exists():
            session_file = self.logs_dir / "hermes-session-copy.jsonl"
        if not session_file.exists():
            return

        total_input_tokens = 0
        total_output_tokens = 0
        for line in session_file.read_text(errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            messages = event.get("messages") if isinstance(event, dict) else None
            records = messages if isinstance(messages, list) else [event]
            for record in records:
                if not isinstance(record, dict):
                    continue
                usage = record.get("usage") or record.get("token_usage") or {}
                if not isinstance(usage, dict):
                    continue
                total_input_tokens += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or usage.get("input", 0) or 0
                total_output_tokens += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or usage.get("output", 0) or 0

        context.n_input_tokens = total_input_tokens or None
        context.n_output_tokens = total_output_tokens or None
