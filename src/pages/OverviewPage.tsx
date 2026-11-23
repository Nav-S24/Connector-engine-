import { Play } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchConnectors,
  fetchDriftEvents,
  fetchRepairs,
  fetchRunDetail,
  fetchRuns,
  triggerConnectorRun,
} from '../api';
import type { ConnectorSummary, DriftEvent, RepairProposal, RunSummary } from '../types';
import { formatNumber, formatPercent, formatRelativeTime, formatTimestamp } from '../utils/format';
import { CardGridSkeleton, EmptyState, ErrorState, StatTilesSkeleton } from '../components/States';
import { StatusBadge } from '../components/StatusBadge';

interface OverviewPageProps {
  refreshSignal: number;
  onRefreshStateChange?: (loading: boolean) => void;
}

export function OverviewPage({ refreshSignal, onRefreshStateChange }: OverviewPageProps) {
  const [connectors, setConnectors] = useState<ConnectorSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [driftEvents, setDriftEvents] = useState<DriftEvent[]>([]);
  const [repairs, setRepairs] = useState<RepairProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [runningMap, setRunningMap] = useState<Record<string, string>>({});

  const timersRef = useRef<Record<string, number>>({});

  const loadAll = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      onRefreshStateChange?.(true);
      setError(null);
      try {
        const [connData, runData, driftData, repairData] = await Promise.all([
          fetchConnectors(),
          fetchRuns(undefined, 200),
          fetchDriftEvents(),
          fetchRepairs(),
        ]);
        setConnectors(connData);
        setRuns(runData);
        setDriftEvents(driftData);
        setRepairs(repairData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unable to load overview telemetry');
      } finally {
        setLoading(false);
        onRefreshStateChange?.(false);
      }
    },
    [onRefreshStateChange],
  );

  useEffect(() => {
    void loadAll();
  }, [loadAll, refreshSignal]);

  useEffect(() => {
    return () => {
      Object.values(timersRef.current).forEach((timerId) => window.clearInterval(timerId));
      timersRef.current = {};
    };
  }, []);

  const handleRunNow = async (connectorName: string) => {
    setTriggerError(null);
    try {
      const { run_id } = await triggerConnectorRun(connectorName);
      setRunningMap((prev) => ({ ...prev, [connectorName]: run_id }));
      setConnectors((prev) =>
        prev.map((c) => (c.name === connectorName ? { ...c, last_run_status: 'running' } : c)),
      );

      if (timersRef.current[connectorName]) {
        window.clearInterval(timersRef.current[connectorName]);
      }

      const poll = async () => {
        try {
          const detail = await fetchRunDetail(run_id);
          if (detail.status !== 'running') {
            window.clearInterval(timersRef.current[connectorName]);
            delete timersRef.current[connectorName];
            setRunningMap((prev) => {
              const next = { ...prev };
              delete next[connectorName];
              return next;
            });
            await loadAll(true);
          }
        } catch {
          window.clearInterval(timersRef.current[connectorName]);
          delete timersRef.current[connectorName];
          setRunningMap((prev) => {
            const next = { ...prev };
            delete next[connectorName];
            return next;
          });
        }
      };

      timersRef.current[connectorName] = window.setInterval(() => {
        void poll();
      }, 900);
    } catch (err) {
      setTriggerError(
        err instanceof Error ? err.message : `Failed to trigger sync for ${connectorName}`,
      );
    }
  };

  const stats = useMemo(() => {
    const totalRuns = runs.length;
    const finished = runs.filter((r) => r.status === 'success' || r.status === 'failed');
    const succeeded = finished.filter((r) => r.status === 'success').length;
    const successRate = finished.length > 0 ? succeeded / finished.length : 0;

    const cutoff24h = Date.now() - 24 * 3_600_000;
    const rows24h = runs.reduce((acc, r) => {
      const ts = new Date(r.started_at).getTime();
      if (!Number.isNaN(ts) && ts >= cutoff24h) {
        return acc + r.rows_synced;
      }
      return acc;
    }, 0);

    const openRepairConnectors = new Set(
      repairs.filter((p) => p.status === 'proposed' || p.status === 'escalated').map((p) => p.connector),
    );
    const openDriftCount =
      openRepairConnectors.size > 0
        ? driftEvents.filter((d) => openRepairConnectors.has(d.connector)).length
        : driftEvents.length;

    return { totalRuns, successRate, rows24h, openDriftCount };
  }, [runs, driftEvents, repairs]);

  return (
    <div className="space-y-6">
      {error ? <ErrorState message={error} onRetry={() => void loadAll()} /> : null}
      {triggerError ? (
        <ErrorState
          title="Unable to start connector sync"
          message={triggerError}
          onRetry={() => setTriggerError(null)}
        />
      ) : null}

      {loading ? (
        <StatTilesSkeleton />
      ) : (
        <section aria-label="Platform summary metrics" className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <div className="p-4 rounded-md bg-[#101622] border border-slate-800/90">
            <div className="text-xs font-medium text-slate-400">Total Runs</div>
            <div className="mt-1.5 text-2xl font-semibold text-slate-100 font-mono tabular-nums">
              {formatNumber(stats.totalRuns)}
            </div>
            <div className="mt-1 text-xs text-slate-500">Recorded across all connectors</div>
          </div>

          <div className="p-4 rounded-md bg-[#101622] border border-slate-800/90">
            <div className="text-xs font-medium text-slate-400">Success Rate</div>
            <div className="mt-1.5 text-2xl font-semibold text-slate-100 font-mono tabular-nums">
              {formatPercent(stats.successRate)}
            </div>
            <div className="mt-1 text-xs text-slate-500">Completed sync runs</div>
          </div>

          <div className="p-4 rounded-md bg-[#101622] border border-slate-800/90">
            <div className="text-xs font-medium text-slate-400">Rows Synced (24h)</div>
            <div className="mt-1.5 text-2xl font-semibold text-slate-100 font-mono tabular-nums">
              {formatNumber(stats.rows24h)}
            </div>
            <div className="mt-1 text-xs text-slate-500">Upserted into DuckDB warehouse</div>
          </div>

          <div className="p-4 rounded-md bg-[#101622] border border-slate-800/90">
            <div className="text-xs font-medium text-slate-400">Open Drift Events</div>
            <div className="mt-1.5 text-2xl font-semibold text-slate-100 font-mono tabular-nums">
              {formatNumber(stats.openDriftCount)}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {formatNumber(driftEvents.length)} total schema changes detected
            </div>
          </div>
        </section>
      )}

      <section aria-label="Configured connectors" className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-slate-200">Connectors</h2>
          <span className="text-xs text-slate-400 tabular-nums">
            {connectors.length} configured
          </span>
        </div>

        {loading ? (
          <CardGridSkeleton count={4} />
        ) : connectors.length === 0 ? (
          <EmptyState
            title="No connectors found"
            description="Add a declarative YAML file under connectors/ and restart the control API to manage syncs here."
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {connectors.map((connector) => {
              const isRunning =
                connector.last_run_status === 'running' || Boolean(runningMap[connector.name]);
              return (
                <article
                  key={connector.name}
                  className="p-5 rounded-md bg-[#101622] border border-slate-800/90 flex flex-col justify-between gap-4"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h3 className="text-base font-semibold text-slate-100 font-mono truncate">
                        {connector.name}
                      </h3>
                      <p className="mt-1 text-xs text-slate-400 font-mono truncate">
                        {connector.streams.join(' · ')}
                      </p>
                    </div>
                    <StatusBadge status={isRunning ? 'running' : connector.last_run_status} />
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-4 pt-3 border-t border-slate-800/80">
                    <div className="flex items-center gap-5 text-xs">
                      <div>
                        <span className="text-slate-500 block">Last Run</span>
                        <span
                          className="text-slate-200 font-mono tabular-nums"
                          title={formatTimestamp(connector.last_run_at)}
                        >
                          {formatRelativeTime(connector.last_run_at)}
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Rows Synced</span>
                        <span className="text-slate-200 font-mono tabular-nums">
                          {formatNumber(connector.last_rows_synced)}
                        </span>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() => void handleRunNow(connector.name)}
                      disabled={isRunning}
                      aria-label={`Run ${connector.name} connector now`}
                      className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded bg-sky-500 hover:bg-sky-400 text-slate-950 transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shrink-0"
                    >
                      <Play className="w-3.5 h-3.5 fill-current" aria-hidden="true" />
                      {isRunning ? 'Syncing...' : 'Run now'}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
