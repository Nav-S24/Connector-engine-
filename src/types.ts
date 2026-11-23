export type RunStatus = 'success' | 'failed' | 'running';

export interface ConnectorSummary {
  name: string;
  streams: string[];
  last_run_status: RunStatus | null;
  last_run_at: string | null;
  last_rows_synced: number | null;
}

export interface TriggerRunResponse {
  run_id: string;
}

export interface RunSummary {
  run_id: string;
  connector: string;
  status: RunStatus;
  rows_synced: number;
  rows_dead_lettered: number;
  retries: number;
  drift_events: number;
  duration_ms: number;
  started_at: string;
}

export interface StreamBreakdown {
  stream_name: string;
  table_name: string;
  rows_synced: number;
  rows_dead_lettered: number;
  pages_fetched?: number;
  retries: number;
  drift_events_count: number;
  duration_ms: number;
  bookmark?: string | null;
}

export interface RunDetail extends RunSummary {
  engine_run_id?: string | null;
  finished_at?: string | null;
  error_message?: string | null;
  streams: StreamBreakdown[];
}

export interface DeadLetterRecord {
  id?: string;
  stream_name?: string;
  record: unknown;
  error_reason: string;
  recorded_at?: string;
}

export type DriftType = 'added' | 'removed' | 'retyped';

export interface DriftEvent {
  connector: string;
  stream: string;
  type: DriftType;
  field: string;
  detected_at: string;
  details?: Record<string, unknown>;
  run_id?: string;
}

export interface ValidationCheck {
  check: string;
  passed: boolean;
  reason: string;
}

export type RepairStatus = 'proposed' | 'applied' | 'escalated' | 'rejected';

export interface RepairProposal {
  id: string;
  connector: string;
  trigger: string;
  diff: string;
  validation_results: ValidationCheck[];
  status: RepairStatus;
  attempts: number;
  created_at: string;
}

export type AuditDecision = 'applied' | 'proposed' | 'escalated' | 'rejected' | string;

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  connector: string;
  trigger: string;
  model: string;
  decision: AuditDecision;
  attempts: number;
  diff: string;
}

export interface ThroughputPoint {
  concurrency: number;
  batch_size: number;
  records_per_sec: number;
}

export interface AiEvalMetrics {
  gen_pass_at_1: number;
  gen_pass_at_3: number;
  repair_success_rate: number;
  gate_catch_rate: number;
  mean_latency_ms: number;
}

export interface BenchmarksData {
  throughput: ThroughputPoint[];
  baseline_rps: number;
  optimized_rps: number;
  ai_eval: AiEvalMetrics;
}
