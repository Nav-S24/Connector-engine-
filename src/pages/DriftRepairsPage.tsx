import { AlertTriangle, Check, CheckCircle2, X, XCircle } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { approveRepair, fetchDriftEvents, fetchRepairs, rejectRepair } from '../api';
import { DiffViewer } from '../components/DiffViewer';
import { CardGridSkeleton, EmptyState, ErrorState } from '../components/States';
import { StatusBadge } from '../components/StatusBadge';
import type { DriftEvent, RepairProposal } from '../types';
import { formatRelativeTime, formatTimestamp } from '../utils/format';

interface DriftRepairsPageProps {
  refreshSignal: number;
  onRefreshStateChange?: (loading: boolean) => void;
}

function formatDriftDetail(event: DriftEvent): string {
  const details = event.details ?? {};
  if (event.type === 'retyped' && details.expected && details.observed) {
    return `${String(details.expected)} → ${String(details.observed)}`;
  }
  if (event.type === 'added' && details.inferred_type) {
    return `type: ${String(details.inferred_type)}`;
  }
  if (event.type === 'removed' && (details.renamed_to || details.replacement_candidate)) {
    return `candidate: ${String(details.renamed_to ?? details.replacement_candidate)}`;
  }
  return event.run_id ? `run ${event.run_id}` : 'schema check';
}

export function DriftRepairsPage({
  refreshSignal,
  onRefreshStateChange,
}: DriftRepairsPageProps) {
  const [driftEvents, setDriftEvents] = useState<DriftEvent[]>([]);
  const [repairs, setRepairs] = useState<RepairProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyRepairId, setBusyRepairId] = useState<string | null>(null);
  const [repairErrors, setRepairErrors] = useState<Record<string, string>>({});

  const loadData = useCallback(async () => {
    setLoading(true);
    onRefreshStateChange?.(true);
    setError(null);
    try {
      const [driftData, repairData] = await Promise.all([
        fetchDriftEvents(),
        fetchRepairs(),
      ]);
      setDriftEvents(driftData);
      setRepairs(repairData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load drift and repair data');
    } finally {
      setLoading(false);
      onRefreshStateChange?.(false);
    }
  }, [onRefreshStateChange]);

  useEffect(() => {
    void loadData();
  }, [loadData, refreshSignal]);

  const groupedDrift = useMemo(() => {
    const groups = new Map<string, DriftEvent[]>();
    for (const evt of driftEvents) {
      const list = groups.get(evt.connector) ?? [];
      list.push(evt);
      groups.set(evt.connector, list);
    }
    for (const [key, list] of groups.entries()) {
      groups.set(
        key,
        [...list].sort((a, b) => b.detected_at.localeCompare(a.detected_at)),
      );
    }
    return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [driftEvents]);

  const handleAction = async (id: string, action: 'approve' | 'reject') => {
    setBusyRepairId(id);
    setRepairErrors((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    try {
      const updated = action === 'approve' ? await approveRepair(id) : await rejectRepair(id);
      setRepairs((prev) => prev.map((r) => (r.id === id ? updated : r)));
    } catch (err) {
      setRepairErrors((prev) => ({
        ...prev,
        [id]: err instanceof Error ? err.message : `Failed to ${action} repair proposal`,
      }));
      // Re-fetch repairs in case validation gate escalated the proposal status
      try {
        const latest = await fetchRepairs();
        setRepairs(latest);
      } catch {
        // Keep existing state if reload fails
      }
    } finally {
      setBusyRepairId(null);
    }
  };

  return (
    <div className="space-y-8">
      {error ? <ErrorState message={error} onRetry={() => void loadData()} /> : null}

      {/* Section (a): Drift Events Timeline */}
      <section aria-label="Schema drift events timeline" className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-slate-200">
            Drift Events Timeline
          </h2>
          <span className="text-xs text-slate-400 tabular-nums">
            {driftEvents.length} events across {groupedDrift.length} connectors
          </span>
        </div>

        {loading ? (
          <CardGridSkeleton count={2} />
        ) : groupedDrift.length === 0 ? (
          <EmptyState
            title="No schema drift events detected"
            description="All tracked SaaS streams match their configured YAML schemas."
          />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {groupedDrift.map(([connectorName, events]) => (
              <div
                key={connectorName}
                className="rounded-md bg-[#101622] border border-slate-800/90 overflow-hidden"
              >
                <div className="px-4 py-3 border-b border-slate-800/90 bg-[#0c111b] flex items-center justify-between">
                  <h3 className="text-xs font-semibold font-mono text-slate-100">
                    {connectorName}
                  </h3>
                  <span className="text-xs text-slate-400 tabular-nums">
                    {events.length} {events.length === 1 ? 'change' : 'changes'}
                  </span>
                </div>
                <ol className="divide-y divide-slate-800/70">
                  {events.map((evt, idx) => (
                    <li
                      key={`${evt.connector}-${evt.stream}-${evt.field}-${idx}`}
                      className="px-4 py-3 flex items-start justify-between gap-3"
                    >
                      <div className="min-w-0 space-y-1">
                        <div className="flex items-center gap-2 text-xs">
                          <StatusBadge status={evt.type} />
                          <span className="text-slate-600" aria-hidden="true">
                            ·
                          </span>
                          <span className="font-mono text-slate-200 truncate">
                            {evt.stream}.{evt.field}
                          </span>
                        </div>
                        <p className="text-[11px] font-mono text-slate-400">
                          {formatDriftDetail(evt)}
                        </p>
                      </div>
                      <time
                        dateTime={evt.detected_at}
                        title={formatTimestamp(evt.detected_at)}
                        className="text-[11px] font-mono tabular-nums text-slate-500 whitespace-nowrap shrink-0"
                      >
                        {formatRelativeTime(evt.detected_at)}
                      </time>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Section (b): Repair Proposals */}
      <section aria-label="AI repair proposals" className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-slate-200">
            Repair Proposals
          </h2>
          <span className="text-xs text-slate-400 tabular-nums">
            {repairs.filter((r) => r.status === 'proposed').length} awaiting approval
          </span>
        </div>

        {loading ? (
          <CardGridSkeleton count={2} />
        ) : repairs.length === 0 ? (
          <EmptyState
            title="No repair proposals"
            description="When upstream schema drift breaks a connector contract, self-healing proposals appear here after running through the validation gate."
          />
        ) : (
          <div className="space-y-4">
            {repairs.map((proposal) => {
              const isProposed = proposal.status === 'proposed';
              const isEscalated = proposal.status === 'escalated';
              const isBusy = busyRepairId === proposal.id;
              const inlineError = repairErrors[proposal.id];

              return (
                <article
                  key={proposal.id}
                  className="p-5 rounded-md bg-[#101622] border border-slate-800/90 space-y-4"
                >
                  {isEscalated ? (
                    <div
                      role="status"
                      className="flex items-center gap-2.5 px-3.5 py-2.5 rounded bg-amber-500/15 border border-amber-500/35 text-xs font-medium text-amber-200"
                    >
                      <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" aria-hidden="true" />
                      <span>
                        Needs human review — automated validation gate rejected candidate patches after{' '}
                        <span className="font-mono tabular-nums">{proposal.attempts}</span> attempts.
                      </span>
                    </div>
                  ) : null}

                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2.5 text-xs">
                        <h3 className="text-sm font-semibold font-mono text-slate-100">
                          {proposal.connector}
                        </h3>
                        <span className="text-slate-600" aria-hidden="true">
                          ·
                        </span>
                        <StatusBadge status={proposal.status} />
                        <span className="text-slate-600" aria-hidden="true">
                          ·
                        </span>
                        <span className="font-mono tabular-nums text-slate-400">
                          Attempts: {proposal.attempts}
                        </span>
                        <span className="text-slate-600" aria-hidden="true">
                          ·
                        </span>
                        <span
                          className="font-mono tabular-nums text-slate-500"
                          title={formatTimestamp(proposal.created_at)}
                        >
                          {formatRelativeTime(proposal.created_at)}
                        </span>
                      </div>
                      <p className="text-xs text-slate-300 leading-relaxed">
                        {proposal.trigger}
                      </p>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        type="button"
                        onClick={() => void handleAction(proposal.id, 'approve')}
                        disabled={!isProposed || isBusy}
                        className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded bg-sky-500 hover:bg-sky-400 text-slate-950 transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap shrink-0"
                      >
                        <Check className="w-3.5 h-3.5" aria-hidden="true" />
                        {isBusy ? 'Validating...' : 'Approve'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleAction(proposal.id, 'reject')}
                        disabled={!isProposed || isBusy}
                        className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap shrink-0"
                      >
                        <X className="w-3.5 h-3.5" aria-hidden="true" />
                        Reject
                      </button>
                    </div>
                  </div>

                  {inlineError ? (
                    <div
                      role="alert"
                      className="px-3.5 py-2.5 rounded bg-rose-950/40 border border-rose-900/70 text-xs text-rose-200"
                    >
                      {inlineError}
                    </div>
                  ) : null}

                  <DiffViewer diff={proposal.diff} compact />

                  <div className="pt-2 border-t border-slate-800/80 space-y-2">
                    <h4 className="text-xs font-semibold text-slate-300">
                      Validation Gate Checklist
                    </h4>
                    {proposal.validation_results.length === 0 ? (
                      <p className="text-xs text-slate-500">No validation checks recorded.</p>
                    ) : (
                      <ul className="divide-y divide-slate-800/60 bg-[#0b1019] rounded border border-slate-800/80">
                        {proposal.validation_results.map((check, idx) => (
                          <li
                            key={idx}
                            className="px-3.5 py-2.5 flex items-start gap-2.5 text-xs"
                          >
                            {check.passed ? (
                              <CheckCircle2
                                className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5"
                                aria-hidden="true"
                              />
                            ) : (
                              <XCircle
                                className="w-4 h-4 text-rose-400 shrink-0 mt-0.5"
                                aria-hidden="true"
                              />
                            )}
                            <div className="min-w-0">
                              <span
                                className={`font-medium ${
                                  check.passed ? 'text-slate-200' : 'text-rose-200'
                                }`}
                              >
                                {check.check}
                              </span>
                              {check.reason ? (
                                <p className="text-slate-400 mt-0.5 leading-relaxed">
                                  {check.reason}
                                </p>
                              ) : null}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
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
