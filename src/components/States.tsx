import { AlertCircle, Inbox, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';

export function StatTilesSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4" aria-busy="true" aria-label="Loading metrics">
      {Array.from({ length: count }).map((_, idx) => (
        <div
          key={idx}
          className="p-4 rounded-md bg-[#101622] border border-slate-800/80 animate-pulse"
        >
          <div className="h-3 w-24 bg-slate-800 rounded mb-3" />
          <div className="h-7 w-20 bg-slate-800 rounded mb-2" />
          <div className="h-3 w-32 bg-slate-800/70 rounded" />
        </div>
      ))}
    </div>
  );
}

export function CardGridSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4" aria-busy="true" aria-label="Loading cards">
      {Array.from({ length: count }).map((_, idx) => (
        <div
          key={idx}
          className="p-5 rounded-md bg-[#101622] border border-slate-800/80 animate-pulse space-y-4"
        >
          <div className="flex items-center justify-between">
            <div className="h-5 w-32 bg-slate-800 rounded" />
            <div className="h-4 w-20 bg-slate-800 rounded" />
          </div>
          <div className="h-3 w-48 bg-slate-800/80 rounded" />
          <div className="flex items-center justify-between pt-2 border-t border-slate-800/60">
            <div className="h-4 w-28 bg-slate-800 rounded" />
            <div className="h-9 w-24 bg-slate-800 rounded" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <div
      className="rounded-md bg-[#101622] border border-slate-800/80 overflow-hidden"
      aria-busy="true"
      aria-label="Loading table rows"
    >
      <div className="h-10 bg-[#0d131f] border-b border-slate-800/80" />
      <div className="divide-y divide-slate-800/70">
        {Array.from({ length: rows }).map((_, rIdx) => (
          <div key={rIdx} className="flex items-center gap-4 px-4 h-11 animate-pulse">
            {Array.from({ length: cols }).map((__, cIdx) => (
              <div
                key={cIdx}
                className="h-3.5 bg-slate-800/90 rounded flex-1"
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ChartSkeleton() {
  return (
    <div
      className="p-5 rounded-md bg-[#101622] border border-slate-800/80 animate-pulse"
      aria-busy="true"
      aria-label="Loading chart"
    >
      <div className="h-4 w-48 bg-slate-800 rounded mb-4" />
      <div className="h-72 w-full bg-slate-900/80 rounded border border-slate-800/60" />
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center px-6 py-12 rounded-md bg-[#101622] border border-slate-800/80">
      <div className="w-10 h-10 rounded-md bg-slate-900 border border-slate-800 flex items-center justify-center mb-3 text-slate-400">
        <Inbox className="w-5 h-5" aria-hidden="true" />
      </div>
      <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
      <p className="mt-1 text-xs text-slate-400 max-w-md leading-relaxed">{description}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  title = 'Failed to load data',
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 rounded-md bg-rose-950/30 border border-rose-900/60 text-rose-200"
    >
      <div className="flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" aria-hidden="true" />
        <div>
          <h3 className="text-sm font-semibold text-rose-100">{title}</h3>
          <p className="text-xs text-rose-300/90 mt-0.5">{message}</p>
        </div>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium rounded bg-rose-900/50 hover:bg-rose-900/80 text-rose-100 border border-rose-800 transition-colors whitespace-nowrap shrink-0 self-start sm:self-center"
        >
          <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
          Retry
        </button>
      ) : null}
    </div>
  );
}
