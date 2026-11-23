import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchConnectors, fetchRuns } from '../api';
import { RunDrawer } from '../components/RunDrawer';
import { EmptyState, ErrorState, TableSkeleton } from '../components/States';
import { StatusBadge } from '../components/StatusBadge';
import type { RunStatus, RunSummary } from '../types';
import { formatDuration, formatNumber, formatRelativeTime, formatTimestamp } from '../utils/format';

interface RunsPageProps {
  refreshSignal: number;
  onRefreshStateChange?: (loading: boolean) => void;
}

const STATUS_OPTIONS: Array<{ value: 'all' | RunStatus; label: string }> = [
  { value: 'all', label: 'All statuses' },
  { value: 'success', label: 'Success' },
  { value: 'failed', label: 'Failed' },
  { value: 'running', label: 'Running' },
];

export function RunsPage({ refreshSignal, onRefreshStateChange }: RunsPageProps) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [connectorOptions, setConnectorOptions] = useState<string[]>([]);
  const [connectorFilter, setConnectorFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<'all' | RunStatus>('all');
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    onRefreshStateChange?.(true);
    setError(null);
    try {
      const [runRows, connectors] = await Promise.all([
        fetchRuns(connectorFilter || undefined, 100),
        fetchConnectors(),
      ]);
      setRuns(runRows);
      const names = Array.from(
        new Set([
          ...connectors.map((c) => c.name),
          ...runRows.map((r) => r.connector),
        ]),
      ).sort();
      setConnectorOptions(names);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load connector runs');
    } finally {
      setLoading(false);
      onRefreshStateChange?.(false);
    }
  }, [connectorFilter, onRefreshStateChange]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns, refreshSignal]);

  const filteredRuns = useMemo(() => {
    return runs.filter((r) => {
      if (statusFilter !== 'all' && r.status !== statusFilter) return false;
      return true;
    });
  }, [runs, statusFilter]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <label htmlFor="runs-connector-filter" className="text-xs font-medium text-slate-400">
              Connector
            </label>
            <select
              id="runs-connector-filter"
              value={connectorFilter}
              onChange={(e) => setConnectorFilter(e.target.value)}
              className="h-9 px-3 rounded bg-[#101622] border border-slate-800 text-xs text-slate-100 font-mono"
            >
              <option value="">All connectors</option>
              {connectorOptions.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>

          <div
            role="group"
            aria-label="Filter runs by status"
            className="flex items-center gap-1 p-1 rounded bg-[#101622] border border-slate-800"
          >
            {STATUS_OPTIONS.map((opt) => {
              const active = statusFilter === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setStatusFilter(opt.value)}
                  aria-pressed={active}
                  className={`px-3 py-1 text-xs font-medium rounded transition-colors whitespace-nowrap shrink-0 ${
                    active
                      ? 'bg-sky-500/20 text-sky-300 border border-sky-500/30'
                      : 'text-slate-400 hover:text-slate-200 border border-transparent'
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        <span className="text-xs text-slate-400 tabular-nums">
          {filteredRuns.length} {filteredRuns.length === 1 ? 'run' : 'runs'}
        </span>
      </div>

      {error ? <ErrorState message={error} onRetry={() => void loadRuns()} /> : null}

      {loading ? (
        <TableSkeleton rows={7} cols={8} />
      ) : filteredRuns.length === 0 ? (
        <EmptyState
          title="No matching sync runs"
          description="No connector executions match the selected connector or status filter."
          action={
            connectorFilter || statusFilter !== 'all' ? (
              <button
                type="button"
                onClick={() => {
                  setConnectorFilter('');
                  setStatusFilter('all');
                }}
                className="px-4 py-2 text-xs font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-100 border border-slate-700 transition-colors"
              >
                Clear filters
              </button>
            ) : undefined
          }
        />
      ) : (
        <div className="rounded-md bg-[#101622] border border-slate-800/90 overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-[#0c111b] text-slate-400">
                <th className="py-2.5 px-4 font-medium">Run ID</th>
                <th className="py-2.5 px-4 font-medium">Connector</th>
                <th className="py-2.5 px-4 font-medium">Status</th>
                <th className="py-2.5 px-4 font-medium text-right">Rows Synced</th>
                <th className="py-2.5 px-4 font-medium text-right">Dead Letters</th>
                <th className="py-2.5 px-4 font-medium text-right">Retries</th>
                <th className="py-2.5 px-4 font-medium text-right">Drift</th>
                <th className="py-2.5 px-4 font-medium text-right">Duration</th>
                <th className="py-2.5 px-4 font-medium text-right">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {filteredRuns.map((run) => (
                <tr
                  key={run.run_id}
                  tabIndex={0}
                  role="button"
                  aria-label={`Inspect run ${run.run_id} for ${run.connector}`}
                  onClick={() => setSelectedRunId(run.run_id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setSelectedRunId(run.run_id);
                    }
                  }}
                  className="h-11 hover:bg-slate-800/40 cursor-pointer transition-colors"
                >
                  <td className="py-2.5 px-4 font-mono text-sky-300 whitespace-nowrap">
                    {run.run_id}
                  </td>
                  <td className="py-2.5 px-4 font-mono text-slate-200 whitespace-nowrap">
                    {run.connector}
                  </td>
                  <td className="py-2.5 px-4 whitespace-nowrap">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="py-2.5 px-4 text-right font-mono tabular-nums text-slate-200">
                    {formatNumber(run.rows_synced)}
                  </td>
                  <td
                    className={`py-2.5 px-4 text-right font-mono tabular-nums ${
                      run.rows_dead_lettered > 0 ? 'text-rose-300' : 'text-slate-400'
                    }`}
                  >
                    {formatNumber(run.rows_dead_lettered)}
                  </td>
                  <td className="py-2.5 px-4 text-right font-mono tabular-nums text-slate-300">
                    {formatNumber(run.retries)}
                  </td>
                  <td
                    className={`py-2.5 px-4 text-right font-mono tabular-nums ${
                      run.drift_events > 0 ? 'text-amber-300' : 'text-slate-400'
                    }`}
                  >
                    {formatNumber(run.drift_events)}
                  </td>
                  <td className="py-2.5 px-4 text-right font-mono tabular-nums text-slate-300 whitespace-nowrap">
                    {formatDuration(run.duration_ms)}
                  </td>
                  <td
                    className="py-2.5 px-4 text-right font-mono tabular-nums text-slate-400 whitespace-nowrap"
                    title={formatTimestamp(run.started_at)}
                  >
                    {formatRelativeTime(run.started_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <RunDrawer runId={selectedRunId} onClose={() => setSelectedRunId(null)} />
    </div>
  );
}
