import {
  AlertTriangle,
  ArrowLeftRight,
  CheckCircle2,
  Clock,
  Loader2,
  MinusCircle,
  PlusCircle,
  XCircle,
} from 'lucide-react';
import type { DriftType, RepairStatus, RunStatus } from '../types';

type AnyStatus = RunStatus | RepairStatus | DriftType | string | null;

interface StatusMeta {
  label: string;
  colorClass: string;
  Icon: typeof CheckCircle2;
  spin?: boolean;
}

const STATUS_MAP: Record<string, StatusMeta> = {
  success: {
    label: 'Success',
    colorClass: 'text-emerald-400',
    Icon: CheckCircle2,
  },
  applied: {
    label: 'Applied',
    colorClass: 'text-emerald-400',
    Icon: CheckCircle2,
  },
  failed: {
    label: 'Failed',
    colorClass: 'text-rose-400',
    Icon: XCircle,
  },
  rejected: {
    label: 'Rejected',
    colorClass: 'text-rose-400',
    Icon: XCircle,
  },
  running: {
    label: 'Running',
    colorClass: 'text-sky-400',
    Icon: Loader2,
    spin: true,
  },
  proposed: {
    label: 'Proposed',
    colorClass: 'text-sky-400',
    Icon: Clock,
  },
  escalated: {
    label: 'Escalated',
    colorClass: 'text-amber-400',
    Icon: AlertTriangle,
  },
  added: {
    label: 'Added',
    colorClass: 'text-emerald-400',
    Icon: PlusCircle,
  },
  removed: {
    label: 'Removed',
    colorClass: 'text-rose-400',
    Icon: MinusCircle,
  },
  retyped: {
    label: 'Retyped',
    colorClass: 'text-amber-400',
    Icon: ArrowLeftRight,
  },
};

export function StatusBadge({ status }: { status: AnyStatus }) {
  if (!status) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 whitespace-nowrap shrink-0">
        <Clock className="w-3.5 h-3.5 text-slate-500" aria-hidden="true" />
        Never run
      </span>
    );
  }

  const key = String(status).toLowerCase();
  const meta = STATUS_MAP[key] ?? {
    label: String(status),
    colorClass: 'text-slate-300',
    Icon: Clock,
  };
  const Icon = meta.Icon;

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap shrink-0 ${meta.colorClass}`}
    >
      <Icon
        className={`w-3.5 h-3.5 shrink-0 ${meta.spin ? 'animate-spin motion-reduce:animate-none' : ''}`}
        aria-hidden="true"
      />
      <span>{meta.label}</span>
    </span>
  );
}
