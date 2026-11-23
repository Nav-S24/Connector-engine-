import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Search } from 'lucide-react';
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { fetchAuditLog } from '../api';
import { DiffViewer } from '../components/DiffViewer';
import { EmptyState, ErrorState, TableSkeleton } from '../components/States';
import { StatusBadge } from '../components/StatusBadge';
import type { AuditLogEntry } from '../types';
import { formatTimestamp } from '../utils/format';

interface AuditLogPageProps {
  refreshSignal: number;
  onRefreshStateChange?: (loading: boolean) => void;
}

type SortKey = 'timestamp' | 'connector' | 'decision' | 'attempts';

export function AuditLogPage({ refreshSignal, onRefreshStateChange }: AuditLogPageProps) {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('timestamp');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadAudit = useCallback(async () => {
    setLoading(true);
    onRefreshStateChange?.(true);
    setError(null);
    try {
      const rows = await fetchAuditLog(200);
      setEntries(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load AI audit log');
    } finally {
      setLoading(false);
      onRefreshStateChange?.(false);
    }
  }, [onRefreshStateChange]);

  useEffect(() => {
    void loadAudit();
  }, [loadAudit, refreshSignal]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'timestamp' || key === 'attempts' ? 'desc' : 'asc');
    }
  };

  const toggleExpanded = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filteredAndSorted = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? entries.filter(
          (e) =>
            e.connector.toLowerCase().includes(q) ||
            e.trigger.toLowerCase().includes(q) ||
            e.model.toLowerCase().includes(q) ||
            e.decision.toLowerCase().includes(q) ||
            e.diff.toLowerCase().includes(q),
        )
      : entries;

    return [...filtered].sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'attempts') {
        cmp = a.attempts - b.attempts;
      } else {
        cmp = String(a[sortKey]).localeCompare(String(b[sortKey]));
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [entries, query, sortKey, sortDir]);

  const renderSortIcon = (key: SortKey) => {
    if (sortKey !== key) return null;
    return sortDir === 'asc' ? (
      <ArrowUp className="w-3 h-3 inline ml-1 text-sky-400" aria-hidden="true" />
    ) : (
      <ArrowDown className="w-3 h-3 inline ml-1 text-sky-400" aria-hidden="true" />
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search
            className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
            aria-hidden="true"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search connector, trigger, model, decision, or diff..."
            aria-label="Search audit log entries"
            className="w-full h-9 pl-9 pr-3 rounded bg-[#101622] border border-slate-800 text-xs text-slate-100 placeholder:text-slate-500"
          />
        </div>

        <span className="text-xs text-slate-400 tabular-nums">
          {filteredAndSorted.length} {filteredAndSorted.length === 1 ? 'entry' : 'entries'}
        </span>
      </div>

      {error ? <ErrorState message={error} onRetry={() => void loadAudit()} /> : null}

      {loading ? (
        <TableSkeleton rows={6} cols={6} />
      ) : filteredAndSorted.length === 0 ? (
        <EmptyState
          title="No matching audit entries"
          description={
            query
              ? 'No audit records matched your search query.'
              : 'AI generation and drift-repair audit events will appear here once recorded.'
          }
          action={
            query ? (
              <button
                type="button"
                onClick={() => setQuery('')}
                className="px-4 py-2 text-xs font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-100 border border-slate-700 transition-colors"
              >
                Clear search
              </button>
            ) : undefined
          }
        />
      ) : (
        <div className="rounded-md bg-[#101622] border border-slate-800/90 overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-[#0c111b] text-slate-400">
                <th className="w-10 py-2.5 px-3" aria-label="Expand diff" />
                <th
                  className="py-2.5 px-3 font-medium"
                  aria-sort={sortKey === 'timestamp' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort('timestamp')}
                    className="hover:text-slate-100 transition-colors whitespace-nowrap"
                  >
                    Timestamp
                    {renderSortIcon('timestamp')}
                  </button>
                </th>
                <th
                  className="py-2.5 px-3 font-medium"
                  aria-sort={sortKey === 'connector' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort('connector')}
                    className="hover:text-slate-100 transition-colors whitespace-nowrap"
                  >
                    Connector
                    {renderSortIcon('connector')}
                  </button>
                </th>
                <th className="py-2.5 px-3 font-medium">Trigger</th>
                <th className="py-2.5 px-3 font-medium">Model</th>
                <th
                  className="py-2.5 px-3 font-medium"
                  aria-sort={sortKey === 'decision' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort('decision')}
                    className="hover:text-slate-100 transition-colors whitespace-nowrap"
                  >
                    Decision
                    {renderSortIcon('decision')}
                  </button>
                </th>
                <th
                  className="py-2.5 px-3 font-medium text-right"
                  aria-sort={sortKey === 'attempts' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort('attempts')}
                    className="hover:text-slate-100 transition-colors whitespace-nowrap"
                  >
                    Attempts
                    {renderSortIcon('attempts')}
                  </button>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {filteredAndSorted.map((entry) => {
                const isExpanded = expandedIds.has(entry.id);
                return (
                  <Fragment key={entry.id}>
                    <tr
                      onClick={() => toggleExpanded(entry.id)}
                      className="h-11 hover:bg-slate-800/40 cursor-pointer transition-colors"
                    >
                      <td className="py-2.5 px-3">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleExpanded(entry.id);
                          }}
                          aria-expanded={isExpanded}
                          aria-label={`${isExpanded ? 'Collapse' : 'Expand'} diff for ${entry.connector}`}
                          className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800"
                        >
                          {isExpanded ? (
                            <ChevronDown className="w-3.5 h-3.5" aria-hidden="true" />
                          ) : (
                            <ChevronRight className="w-3.5 h-3.5" aria-hidden="true" />
                          )}
                        </button>
                      </td>
                      <td className="py-2.5 px-3 font-mono tabular-nums text-slate-400 whitespace-nowrap">
                        {formatTimestamp(entry.timestamp)}
                      </td>
                      <td className="py-2.5 px-3 font-mono text-slate-200 whitespace-nowrap">
                        {entry.connector}
                      </td>
                      <td className="py-2.5 px-3 text-slate-300 max-w-md truncate" title={entry.trigger}>
                        {entry.trigger}
                      </td>
                      <td className="py-2.5 px-3 font-mono text-slate-400 whitespace-nowrap">
                        {entry.model}
                      </td>
                      <td className="py-2.5 px-3 whitespace-nowrap">
                        <StatusBadge status={entry.decision} />
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">
                        {entry.attempts}
                      </td>
                    </tr>
                    {isExpanded ? (
                      <tr className="bg-[#0b1019]">
                        <td colSpan={7} className="p-4 border-t border-slate-800/70">
                          <DiffViewer diff={entry.diff} compact />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
