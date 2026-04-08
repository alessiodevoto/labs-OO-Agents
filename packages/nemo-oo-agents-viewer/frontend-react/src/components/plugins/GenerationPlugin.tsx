import type { PluginProps } from './registry';
import { CodeBox } from '@/components/shared/CodeBox';

function formatDuration(ns: number): { ms: string; sec: string } {
  const ms = (ns / 1e6).toFixed(0);
  const sec = (ns / 1e9).toFixed(2);
  return { ms, sec };
}

export function GenerationPlugin({ event, viewState, rawJsonOpen, viewControls }: PluginProps) {
  const attrs = event.attributes || {};
  const strategy = (attrs['generation.strategy'] as string) || 'unknown';
  const durationNs = (attrs.duration_ns as number) || 0;
  const agentMethod = attrs['agent.method'] as string | undefined;
  const agentName = (attrs['agent.name'] as string) || 'Agent';
  const timestamp = event.timestamp ? new Date(event.timestamp).toLocaleTimeString() : '';

  const statusCode = (attrs.status_code as string) || '';
  const errorType = (attrs['error.type'] as string) || '';
  const errorMessage = (attrs['error.message'] as string) || '';
  const isError = statusCode === 'ERROR' || !!errorType || !!errorMessage;
  const isMaxIter =
    errorMessage.includes('max_iterations') || errorMessage.includes('max iterations');

  const { ms, sec } = formatDuration(durationNs);

  const headerLine = (
    <div className="flex items-center gap-3 text-xs">
      <span className="px-1.5 py-0.5 rounded bg-purple-900 text-purple-200 text-xs font-semibold">
        {strategy}
      </span>
      {durationNs > 0 && (
        <span className="text-gray-400">
          {ms}ms ({sec}s)
        </span>
      )}
      {agentMethod && (
        <span className="text-gray-400">
          {agentName}.{agentMethod}
        </span>
      )}
      {isMaxIter && <span className="text-orange-400 font-semibold">MAX ITERATIONS</span>}
      {isError && !isMaxIter && <span className="text-red-400 font-semibold">ERROR</span>}
      <span className="ml-auto text-gray-600">{timestamp}</span>
      {viewControls}
    </div>
  );

  if (viewState === 'collapsed' || viewState === 'concise') {
    return (
      <div
        className={
          isError ? `border-l-[3px] pl-2 ${isMaxIter ? 'border-orange-500' : 'border-red-500'}` : ''
        }
      >
        {headerLine}
      </div>
    );
  }

  // Expanded
  const meta: Record<string, unknown> = {};
  if (attrs['generation.id']) meta['Generation ID'] = attrs['generation.id'];
  if (agentMethod) meta['Method'] = agentMethod;
  if (agentName) meta['Agent'] = agentName;

  return (
    <div>
      {headerLine}

      {isError && (
        <div
          className={`mt-3 p-3 rounded-lg border ${
            isMaxIter
              ? 'bg-gradient-to-br from-yellow-950 to-yellow-900 border-yellow-600'
              : 'bg-gradient-to-br from-red-950 to-red-900 border-red-600'
          }`}
        >
          <div className="flex items-center gap-2 mb-2">
            <span className="text-lg font-bold">{isMaxIter ? '!' : 'X'}</span>
            <span
              className={`font-semibold text-sm ${isMaxIter ? 'text-yellow-200' : 'text-red-200'}`}
            >
              {isMaxIter ? 'Max Iterations Reached' : errorType || 'Generation Error'}
            </span>
          </div>
          {errorMessage && (
            <pre
              className={`text-xs font-mono whitespace-pre-wrap break-words ${isMaxIter ? 'text-yellow-100' : 'text-red-100'}`}
            >
              {errorMessage}
            </pre>
          )}
        </div>
      )}

      {Object.keys(meta).length > 0 && (
        <div className="mt-3 p-3 bg-gray-900 rounded border-l-4 border-purple-700">
          <div className="text-xs text-gray-500 mb-1">Metadata</div>
          <CodeBox code={JSON.stringify(meta, null, 2)} language="json" />
        </div>
      )}

      <details className="mt-2" open={rawJsonOpen}>
        <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-300">
          Raw JSON
        </summary>
        <CodeBox code={JSON.stringify(event, null, 2)} language="json" />
      </details>
    </div>
  );
}
