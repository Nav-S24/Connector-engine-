import { AlertTriangle, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchRunDeadLetters, fetchRunDetail } from '../api';
import type { DeadLetterRecord, RunDetail } from '../types';
import { formatDuration, formatNumber, formatTimestamp } from '../utils/format';
import { EmptyState, ErrorState, TableSkeleton } from './States';
import { StatusBadge } from './StatusBadge';

interface RunDrawerProps {
  runId: string | null;
  onClose: () => void;
}

export function RunDrawer({ runId, onClose }: RunDrawerProps) {
  const [activeTab, setActiveTab] = useState<'summary' | 'dead-letters'>('summary');
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [deadLetters, setDeadLetters] = useState<DeadLetterRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRun = async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const [detailData, dlData] = await Promise.all([
        fetchRunDetail(id),
        fetchRunDeadLetters(id),
      ]);
      setDetail(detailData);
      setDeadLetters(dlData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to fetch run details');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!runId) return;
    setActiveTab('summary');
    void loadRun(runId);
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [runId, onClose]);

  if (!runId) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="fixed inset-0 bg-black/65 backdrop-blur-[1px]"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-drawer-title"
        className="relative z-10 w-full max-w-2xl h-full bg-[#0d121c] border-l border-slate-800 flex flex-col shadow-2xl"
      >
        <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-slate-800">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <h2
                id="run-drawer-title"
                className="text-base font-semibold text-slate-100 font-mono truncate"
              >
                {runId}
              </h2>
              {detail ? <StatusBadge status={detail.status} /> : null}
            </div>
            <p className="mt-1 text-xs text-slate-400">
              {detail
                ? `${detail.connector} · Started ${formatTimestamp(detail.started_at)}`
                : 'Loading run metadata...'}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close run details drawer"
            className="p-2 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800/80 transition-colors shrink-0"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div className="flex items-center gap-2 px-5 pt-3 border-b border-slate-800">
          <button
            type="button"
            onClick={() => setActiveTab('summary')}
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors whitespace-nowrap shrink-0 ${
              activeTab === 'summary'
                ? 'border-sky-400 text-sky-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            Summary & Streams
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('dead-letters')}
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors whitespace-nowrap shrink-0 tabular-nums ${
              activeTab === 'dead-letters'
                ? 'border-sky-400 text-sky-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            Dead Letters ({deadLetters.length})
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {loading ? (
            <TableSkeleton rows={4} cols={4} />
          ) : error ? (
            <ErrorState message={error} onRetry={() => void loadRun(runId)} />
          ) : detail ? (
            activeTab === 'summary' ? (
              <>
                {detail.error_message ? (
                  <div className="flex items-start gap-3 p-3.5 rounded-md bg-rose-950/35 border border-rose-900/60 text-xs text-rose-200">
                    <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" aria-hidden="true" />
                    <div>
                      <p className="font-medium text-rose-100">Failure Reason</p>
                      <p className="mt-1 font-mono text-[11px] leading-relaxed text-rose-200/90">
                        {detail.error_message}
                      </p>
                    </div>
                  </div>
                ) : null}

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className="p-3 rounded bg-[#111827] border border-slate-800/80">
                    <div className="text-[11px] text-slate-400">Rows Synced</div>
                    <div className="mt-1 text-base font-semibold text-slate-100 font-mono tabular-nums">
                      {formatNumber(detail.rows_synced)}
                    </div>
                  </div>
                  <div className="p-3 rounded bg-[#111827] border border-slate-800/80">
                    <div className="text-[11px] text-slate-400">Dead Lettered</div>
                    <div className="mt-1 text-base font-semibold text-slate-100 font-mono tabular-nums">
                      {formatNumber(detail.rows_dead_lettered)}
                    </div>
                  </div>
                  <div className="p-3 rounded bg-[#111827] border border-slate-800/80">
                    <div className="text-[11px] text-slate-400">Retries</div>
                    <div className="mt-1 text-base font-semibold text-slate-100 font-mono tabular-nums">
                      {formatNumber(detail.retries)}
                    </div>
                  </div>
                  <div className="p-3 rounded bg-[#111827] border border-slate-800/80">
                    <div className="text-[11px] text-slate-400">Duration</div>
                    <div className="mt-1 text-base font-semibold text-slate-100 font-mono tabular-nums">
                      {formatDuration(detail.duration_ms)}
                    </div>
                  </div>
                </div>

                <div>
                  <h3 className="text-xs font-semibold text-slate-300 mb-2.5">
                    Per-Stream Breakdown
                  </h3>
                  {detail.streams.length === 0 ? (
                    <EmptyState
                      title="No per-stream metrics recorded"
                      description="Stream metrics are captured once pages begin committing to DuckDB."
                    />
                  ) : (
                    <div className="rounded-md border border-slate-800/90 overflow-x-auto bg-[#101622]">
                      <table className="w-full text-left border-collapse text-xs">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-400 bg-[#0b1019]">
                            <th className="py-2.5 px-3 font-medium">Stream</th>
                            <th className="py-2.5 px-3 font-medium text-right">Synced</th>
                            <th className="py-2.5 px-3 font-medium text-right">Dead Letters</th>
                            <th className="py-2.5 px-3 font-medium text-right">Retries</th>
                            <th className="py-2.5 px-3 font-medium text-right">Duration</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/70">
                          {detail.streams.map((stream) => (
                            <tr key={stream.stream_name} className="hover:bg-slate-800/30">
                              <td className="py-2.5 px-3">
                                <div className="font-medium text-slate-200 font-mono">
                                  {stream.stream_name}
                                </div>
                                {stream.table_name ? (
                                  <div className="text-[11px] text-slate-500 font-mono">
                                    {stream.table_name}
                                  </div>
                                ) : null}
                              </td>
                              <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-200">
                                {formatNumber(stream.rows_synced)}
                              </td>
                              <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">
                                {formatNumber(stream.rows_dead_lettered)}
                              </td>
                              <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">
                                {formatNumber(stream.retries)}
                              </td>
                              <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-400">
                                {formatDuration(stream.duration_ms)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </>
            ) : deadLetters.length === 0 ? (
              <EmptyState
                title="No dead-letter records"
                description="All records extracted in this run passed schema and primary-key validation."
              />
            ) : (
              <div className="space-y-3">
                {deadLetters.map((dl, idx) => (
                  <div
                    key={dl.id ?? idx}
                    className="p-3.5 rounded-md bg-[#101622] border border-slate-800/90 space-y-2.5"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="text-xs text-rose-300 font-medium leading-relaxed">
                        {dl.error_reason}
                      </div>
                      {dl.stream_name ? (
                        <span className="text-[11px] font-mono text-slate-400 shrink-0">
                          {dl.stream_name}
                        </span>
                      ) : null}
                    </div>
                    <pre className="p-2.5 rounded bg-[#090d14] border border-slate-800/70 text-[11px] font-mono text-slate-300 overflow-x-auto leading-relaxed">
                      {JSON.stringify(dl.record, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            )
          ) : null}
        </div>
      </aside>
    </div>
  );
}
