import { mockStore } from './mock';
import type {
  AuditLogEntry,
  BenchmarksData,
  ConnectorSummary,
  DeadLetterRecord,
  DriftEvent,
  RepairProposal,
  RunDetail,
  RunStatus,
  RunSummary,
  StreamBreakdown,
  TriggerRunResponse,
  ValidationCheck,
} from './types';

const API_BASE = (import.meta.env.VITE_API_URL || 'http://localhost:8100').replace(/\/+$/, '');
const FORCE_MOCK = String(import.meta.env.VITE_USE_MOCK || '').toLowerCase() === 'true';

let mockModeActive = FORCE_MOCK;
const listeners = new Set<(active: boolean) => void>();

export function isMockMode(): boolean {
  return mockModeActive;
}

export function subscribeMockMode(listener: (active: boolean) => void): () => void {
  listeners.add(listener);
  listener(mockModeActive);
  return () => {
    listeners.delete(listener);
  };
}

function activateMockMode(): void {
  if (!mockModeActive) {
    mockModeActive = true;
    for (const listener of listeners) {
      listener(true);
    }
  }
}

class ApiHttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (typeof body?.detail === 'string') {
          detail = body.detail;
        } else if (body?.detail?.message) {
          const errs = Array.isArray(body.detail.errors) ? body.detail.errors.join('; ') : '';
          detail = errs ? `${body.detail.message}: ${errs}` : String(body.detail.message);
        }
      } catch {
        // Ignore body parse failure on error responses
      }
      throw new ApiHttpError(response.status, detail);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}

async function withMockFallback<T>(
  liveCall: () => Promise<T>,
  mockCall: () => T | Promise<T>,
): Promise<T> {
  if (FORCE_MOCK || mockModeActive) {
    activateMockMode();
    return await mockCall();
  }
  try {
    return await liveCall();
  } catch (err) {
    // Only auto-fallback when API is unreachable (network / abort / CORS block), not 4xx validation errors.
    if (err instanceof ApiHttpError) {
      throw err;
    }
    activateMockMode();
    return await mockCall();
  }
}

function normalizeStatus(raw: unknown): RunStatus {
  if (raw === 'failed' || raw === 'running' || raw === 'success') {
    return raw;
  }
  return 'failed';
}

function normalizeRunSummary(raw: Record<string, unknown>): RunSummary {
  const driftVal = raw.drift_events;
  const driftCount =
    typeof driftVal === 'number'
      ? driftVal
      : Array.isArray(driftVal)
        ? driftVal.length
        : Number(raw.drift_events_count ?? 0);

  return {
    run_id: String(raw.run_id ?? ''),
    connector: String(raw.connector ?? raw.connector_name ?? ''),
    status: normalizeStatus(raw.status),
    rows_synced: Number(raw.rows_synced ?? 0),
    rows_dead_lettered: Number(raw.rows_dead_lettered ?? 0),
    retries: Number(raw.retries ?? 0),
    drift_events: driftCount,
    duration_ms: Number(raw.duration_ms ?? 0),
    started_at: String(raw.started_at ?? ''),
  };
}

function normalizeStreams(rawStreams: unknown): StreamBreakdown[] {
  if (Array.isArray(rawStreams)) {
    return rawStreams.map((item) => {
      const s = item as Record<string, unknown>;
      return {
        stream_name: String(s.stream_name ?? s.name ?? ''),
        table_name: String(s.table_name ?? ''),
        rows_synced: Number(s.rows_synced ?? 0),
        rows_dead_lettered: Number(s.rows_dead_lettered ?? 0),
        pages_fetched: s.pages_fetched != null ? Number(s.pages_fetched) : undefined,
        retries: Number(s.retries ?? 0),
        drift_events_count: Array.isArray(s.drift_events)
          ? s.drift_events.length
          : Number(s.drift_events_count ?? 0),
        duration_ms: Number(s.duration_ms ?? 0),
        bookmark: s.bookmark != null ? String(s.bookmark) : null,
      };
    });
  }
  if (rawStreams && typeof rawStreams === 'object') {
    return Object.entries(rawStreams as Record<string, Record<string, unknown>>).map(
      ([key, s]) => ({
        stream_name: String(s.stream_name ?? key),
        table_name: String(s.table_name ?? ''),
        rows_synced: Number(s.rows_synced ?? 0),
        rows_dead_lettered: Number(s.rows_dead_lettered ?? 0),
        pages_fetched: s.pages_fetched != null ? Number(s.pages_fetched) : undefined,
        retries: Number(s.retries ?? 0),
        drift_events_count: Array.isArray(s.drift_events)
          ? s.drift_events.length
          : Number(s.drift_events_count ?? 0),
        duration_ms: Number(s.duration_ms ?? 0),
        bookmark: s.bookmark != null ? String(s.bookmark) : null,
      }),
    );
  }
  return [];
}

function normalizeValidationResults(raw: unknown): ValidationCheck[] {
  if (Array.isArray(raw)) {
    return raw.map((item) => {
      const c = item as Record<string, unknown>;
      return {
        check: String(c.check ?? 'Validation check'),
        passed: Boolean(c.passed),
        reason: String(c.reason ?? ''),
      };
    });
  }
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    const valid = Boolean(obj.valid);
    const errors = Array.isArray(obj.errors) ? obj.errors.map(String) : [];
    const warnings = Array.isArray(obj.warnings) ? obj.warnings.map(String) : [];
    const counts =
      obj.sample_counts && typeof obj.sample_counts === 'object'
        ? (obj.sample_counts as Record<string, number>)
        : {};

    const checks: ValidationCheck[] = [
      {
        check: 'Validation Gate Overall',
        passed: valid,
        reason: valid
          ? 'Passed YAML schema, HTTP endpoint probe, and DuckDB upsert validation.'
          : errors[0] || 'Failed validation gate checks.',
      },
    ];

    const countEntries = Object.entries(counts);
    if (countEntries.length > 0) {
      checks.push({
        check: 'Live Stream Sample Extraction',
        passed: countEntries.every(([, n]) => Number(n) > 0),
        reason: countEntries.map(([stream, n]) => `${stream}: ${n} rows`).join(', '),
      });
    }

    for (const errMsg of errors) {
      checks.push({
        check: 'Gate Constraint Failure',
        passed: false,
        reason: errMsg,
      });
    }
    for (const warnMsg of warnings) {
      checks.push({
        check: 'Schema Advisory',
        passed: true,
        reason: warnMsg,
      });
    }
    return checks;
  }
  return [];
}

function normalizeRepair(raw: Record<string, unknown>): RepairProposal {
  const statusRaw = String(raw.status ?? 'proposed');
  const status =
    statusRaw === 'applied' || statusRaw === 'escalated' || statusRaw === 'rejected'
      ? statusRaw
      : 'proposed';
  return {
    id: String(raw.id ?? ''),
    connector: String(raw.connector ?? ''),
    trigger: String(raw.trigger ?? ''),
    diff: String(raw.diff ?? ''),
    validation_results: normalizeValidationResults(raw.validation_results),
    status,
    attempts: Number(raw.attempts ?? 1),
    created_at: String(raw.created_at ?? ''),
  };
}

function parsePercentOrNumber(val: unknown): number {
  if (typeof val === 'number') {
    return val > 1 && val <= 100 ? val / 100 : val;
  }
  const str = String(val ?? '0');
  const match = str.match(/[\d.]+/);
  if (!match) return 0;
  const num = parseFloat(match[0]);
  return str.includes('%') || num > 1 ? num / 100 : num;
}

function parseLatencyMs(val: unknown): number {
  if (typeof val === 'number') return val;
  const match = String(val ?? '0').match(/[\d.]+/);
  return match ? parseFloat(match[0]) : 0;
}

export async function fetchConnectors(): Promise<ConnectorSummary[]> {
  return withMockFallback(
    () => requestJson<ConnectorSummary[]>('/api/connectors'),
    () => mockStore.getConnectors(),
  );
}

export async function triggerConnectorRun(name: string): Promise<TriggerRunResponse> {
  return withMockFallback(
    () =>
      requestJson<TriggerRunResponse>(`/api/connectors/${encodeURIComponent(name)}/run`, {
        method: 'POST',
      }),
    () => mockStore.triggerRun(name),
  );
}

export async function fetchRuns(connector?: string, limit = 50): Promise<RunSummary[]> {
  return withMockFallback(
    async () => {
      const params = new URLSearchParams();
      if (connector) params.set('connector', connector);
      params.set('limit', String(limit));
      const rows = await requestJson<Record<string, unknown>[]>(`/api/runs?${params.toString()}`);
      return rows.map(normalizeRunSummary);
    },
    () => mockStore.getRuns(connector, limit),
  );
}

export async function fetchRunDetail(runId: string): Promise<RunDetail> {
  return withMockFallback(
    async () => {
      const raw = await requestJson<Record<string, unknown>>(
        `/api/runs/${encodeURIComponent(runId)}`,
      );
      const summary = normalizeRunSummary(raw);
      return {
        ...summary,
        engine_run_id: raw.engine_run_id != null ? String(raw.engine_run_id) : null,
        finished_at: raw.finished_at != null ? String(raw.finished_at) : null,
        error_message: raw.error_message != null ? String(raw.error_message) : null,
        streams: normalizeStreams(raw.streams),
      };
    },
    () => mockStore.getRunDetail(runId),
  );
}

export async function fetchRunDeadLetters(runId: string): Promise<DeadLetterRecord[]> {
  return withMockFallback(
    async () => {
      const rows = await requestJson<Record<string, unknown>[]>(
        `/api/runs/${encodeURIComponent(runId)}/dead-letters`,
      );
      return rows.map((row) => ({
        id: row.id != null ? String(row.id) : undefined,
        stream_name: row.stream_name != null ? String(row.stream_name) : undefined,
        record: row.record ?? row.raw_record ?? {},
        error_reason: String(row.error_reason ?? 'Unknown error'),
        recorded_at: row.recorded_at != null ? String(row.recorded_at) : undefined,
      }));
    },
    () => mockStore.getRunDeadLetters(runId),
  );
}

export async function fetchDriftEvents(): Promise<DriftEvent[]> {
  return withMockFallback(
    () => requestJson<DriftEvent[]>('/api/drift'),
    () => mockStore.getDriftEvents(),
  );
}

export async function fetchRepairs(): Promise<RepairProposal[]> {
  return withMockFallback(
    async () => {
      const rows = await requestJson<Record<string, unknown>[]>('/api/repairs');
      return rows.map(normalizeRepair);
    },
    () => mockStore.getRepairs(),
  );
}

export async function approveRepair(id: string): Promise<RepairProposal> {
  return withMockFallback(
    async () => {
      const raw = await requestJson<Record<string, unknown>>(
        `/api/repairs/${encodeURIComponent(id)}/approve`,
        { method: 'POST' },
      );
      return normalizeRepair(raw);
    },
    () => mockStore.approveRepair(id),
  );
}

export async function rejectRepair(id: string): Promise<RepairProposal> {
  return withMockFallback(
    async () => {
      const raw = await requestJson<Record<string, unknown>>(
        `/api/repairs/${encodeURIComponent(id)}/reject`,
        { method: 'POST' },
      );
      return normalizeRepair(raw);
    },
    () => mockStore.rejectRepair(id),
  );
}

export async function fetchAuditLog(limit = 100): Promise<AuditLogEntry[]> {
  return withMockFallback(
    async () => {
      const rows = await requestJson<Record<string, unknown>[]>(`/api/audit?limit=${limit}`);
      return rows.map((raw, index) => {
        let decision = String(raw.decision ?? '');
        if (!decision) {
          if (raw.applied) decision = 'applied';
          else if (raw.accepted) decision = 'proposed';
          else if (String(raw.reason ?? '').toLowerCase().includes('reject')) decision = 'rejected';
          else decision = 'escalated';
        }
        const meta =
          raw.metadata && typeof raw.metadata === 'object'
            ? (raw.metadata as Record<string, unknown>)
            : {};
        return {
          id: String(raw.entry_id ?? raw.id ?? `aud-${index}`),
          timestamp: String(raw.timestamp ?? ''),
          connector: String(raw.connector ?? raw.connector_name ?? ''),
          trigger: String(raw.trigger ?? raw.reason ?? ''),
          model: String(raw.model ?? meta.model ?? 'gemini-4-hydrogen'),
          decision,
          attempts: Number(raw.attempts ?? 1),
          diff: String(raw.diff ?? raw.diff_text ?? ''),
        };
      });
    },
    () => mockStore.getAudit(limit),
  );
}

export async function fetchBenchmarks(): Promise<BenchmarksData> {
  return withMockFallback(
    async () => {
      const raw = await requestJson<Record<string, unknown>>('/api/benchmarks');
      // Support both flat shape and connector-platform nested { throughput: { rows }, ai_eval: { metrics } } shape
      const throughputRaw = raw.throughput;
      let throughputPoints: BenchmarksData['throughput'] = [];
      let baselineRps = Number(raw.baseline_rps ?? 0);
      let optimizedRps = Number(raw.optimized_rps ?? 0);

      if (Array.isArray(throughputRaw)) {
        throughputPoints = throughputRaw.map((p) => {
          const item = p as Record<string, unknown>;
          return {
            concurrency: Number(item.concurrency ?? 1),
            batch_size: Number(item.batch_size ?? 500),
            records_per_sec: Number(item.records_per_sec ?? item.records_per_second ?? 0),
          };
        });
      } else if (throughputRaw && typeof throughputRaw === 'object') {
        const tObj = throughputRaw as Record<string, unknown>;
        baselineRps = Number(tObj.baseline_rps ?? baselineRps);
        optimizedRps = Number(tObj.optimized_rps ?? optimizedRps);
        const rows = Array.isArray(tObj.rows) ? tObj.rows : [];
        throughputPoints = rows
          .filter((r) => {
            const row = r as Record<string, unknown>;
            return Number(row.rate_429 ?? 0) === 0 && Number(row.rate_500 ?? 0) === 0;
          })
          .map((r) => {
            const row = r as Record<string, unknown>;
            return {
              concurrency: Number(row.concurrency ?? 1),
              batch_size: Number(row.batch_size ?? 500),
              records_per_sec: Number(row.records_per_sec ?? row.records_per_second ?? 0),
            };
          });
      }

      const aiRaw = (raw.ai_eval ?? {}) as Record<string, unknown>;
      const metricsMap =
        aiRaw.metrics && typeof aiRaw.metrics === 'object'
          ? (aiRaw.metrics as Record<string, unknown>)
          : aiRaw;

      return {
        throughput: throughputPoints,
        baseline_rps: baselineRps,
        optimized_rps: optimizedRps,
        ai_eval: {
          gen_pass_at_1: parsePercentOrNumber(
            metricsMap.gen_pass_at_1 ?? metricsMap.generation_first_attempt_pass_rate,
          ),
          gen_pass_at_3: parsePercentOrNumber(
            metricsMap.gen_pass_at_3 ?? metricsMap.generation_pass_3_rate,
          ),
          repair_success_rate: parsePercentOrNumber(
            metricsMap.repair_success_rate ??
              metricsMap.repair_success_rate_within_3_retries_full_sync,
          ),
          gate_catch_rate: parsePercentOrNumber(
            metricsMap.gate_catch_rate ??
              metricsMap.validation_gate_catch_rate_adversarial_broken_configs,
          ),
          mean_latency_ms: parseLatencyMs(
            metricsMap.mean_latency_ms ?? metricsMap.mean_latency_per_scenario,
          ),
        },
      };
    },
    () => mockStore.getBenchmarks(),
  );
}
