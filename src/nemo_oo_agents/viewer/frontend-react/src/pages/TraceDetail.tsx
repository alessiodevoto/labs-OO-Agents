import { useSearchParams, useNavigate } from "react-router";
import { TraceView } from "@/components/trace/TraceView";
import { CopyButton } from "@/components/shared/CopyButton";

export function TraceDetail() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const sessionId = searchParams.get("session_id") || "";

  const debugPrompt = sessionId
    ? [
        `# one-time setup (if necessary): uv run trace-explorer --install-skill`,
        `uv run trace-explorer --viewer ${window.location.origin} --session-id '${sessionId}'`,
      ].join("\n")
    : "";

  return (
    <div className="max-w-[100rem] mx-auto px-4 py-6">
      <div className="flex items-center gap-3 mb-4 min-w-0">
        <button
          onClick={() => navigate(-1)}
          className="shrink-0 text-gray-400 hover:text-gray-200 transition-colors text-sm whitespace-nowrap"
        >
          &#9666; Back
        </button>
        <h1
          className="text-lg font-mono text-gray-200 truncate min-w-0"
          title={sessionId}
        >
          {sessionId}
        </h1>
        {debugPrompt && (
          <CopyButton
            text={debugPrompt}
            label="DEBUG"
            title="Copy a prompt to debug this trace with Claude Code, Cursor or other coding agents"
            className="shrink-0 !px-1.5 !py-0.5 !text-[9px] leading-none font-medium uppercase tracking-wide !rounded border border-gray-700 !bg-gray-900 !text-gray-400 hover:!text-gray-200 hover:!bg-gray-800"
          />
        )}
      </div>

      <TraceView sessionId={sessionId} onBack={() => navigate(-1)} />
    </div>
  );
}
