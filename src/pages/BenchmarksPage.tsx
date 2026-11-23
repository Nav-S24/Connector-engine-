import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { fetchBenchmarks } from '../api';
import { ChartSkeleton, EmptyState, ErrorState, StatTilesSkeleton } from '../components/States';
import type { BenchmarksData } from '../types';
import { formatNumber, formatPercent } from '../utils/format';

interface BenchmarksPageProps {
  refreshSignal: number;
  onRefreshStateChange?: (loading: boolean) => void;
}

const LINE_COLORS = ['#64748b', '#38bdf8', '#0ea5e9', '#34d399', '#f59e0b'];

export function BenchmarksPage({ refreshSignal, onRefreshStateChange }: BenchmarksPageProps) {
  const [data, setData] = useState<BenchmarksData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadBenchmarks = useCallback(async () => {
    setLoading(true);
    onRefreshStateChange?.(true);
    setError(null);
    try {
      const res = await fetchBenchmarks();
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load benchmark telemetry');
    } finally {
      setLoading(false);
      onRefreshStateChange?.(false);
    }
  }, [onRefreshStateChange]);

  useEffect(() => {
    void loadBenchmarks();
  }, [loadBenchmarks, refreshSignal]);

  const { chartData, batchSizes } = useMemo(() => {
    if (!data || data.throughput.length === 0) {
      return { chartData: [], batchSizes: [] as number[] };
    }

    const sizes = Array.from(new Set(data.throughput.map((p) => p.batch_size))).sort(
      (a, b) => a - b,
    );
    const byConcurrency = new Map<number, Record<string, number>>();

    for (const point of data.throughput) {
      const existing = byConcurrency.get(point.concurrency) ?? {
        concurrency: point.concurrency,
      };
      existing[`batch_${point.batch_size}`] = point.records_per_sec;
      byConcurrency.set(point.concurrency, existing);
    }

    const sortedRows = Array.from(byConcurrency.values()).sort(
      (a, b) => a.concurrency - b.concurrency,
    );

    return { chartData: sortedRows, batchSizes: sizes };
  }, [data]);

  const speedup = useMemo(() => {
    if (!data || !data.baseline_rps || !data.optimized_rps) return null;
    return (data.optimized_rps / data.baseline_rps).toFixed(2);
  }, [data]);

  const aiCards = useMemo(() => {
    if (!data) return [];
    const evals = data.ai_eval;
    return [
      {
        label: 'Generation Pass@1',
        value: formatPercent(evals.gen_pass_at_1),
        explanation:
          'Share of API specs turned into a fully validated connector YAML on the very first attempt without validation-gate feedback.',
      },
      {
        label: 'Generation Pass@3',
        value: formatPercent(evals.gen_pass_at_3),
        explanation:
          'Share of valid connector specs that pass syntax, live HTTP probe, and DuckDB upsert checks within 3 feedback retries.',
      },
      {
        label: 'Repair Success Rate',
        value: formatPercent(evals.repair_success_rate),
        explanation:
          'Percentage of breaking schema and endpoint mutations automatically repaired and verified end to end against live payloads.',
      },
      {
        label: 'Validation Gate Catch Rate',
        value: formatPercent(evals.gate_catch_rate),
        explanation:
          'Adversarial and broken configs blocked before ever touching warehouse tables or overwriting YAML files on disk.',
      },
      {
        label: 'Mean Scenario Latency',
        value: `${evals.mean_latency_ms.toFixed(1)} ms`,
        explanation:
          'Average wall-clock time to generate or repair a connector config and complete full validation-gate verification.',
      },
    ];
  }, [data]);

  if (loading) {
    return (
      <div className="space-y-6">
        <ChartSkeleton />
        <StatTilesSkeleton count={5} />
      </div>
    );
  }

  if (error) {
    return <ErrorState message={error} onRetry={() => void loadBenchmarks()} />;
  }

  if (!data || (chartData.length === 0 && !data.baseline_rps)) {
    return (
      <EmptyState
        title="No benchmark results available"
        description="Run bench_throughput and eval_ai_layer in connector-platform to populate throughput curves and AI evaluation metrics."
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Baseline vs Optimized callout */}
      <section
        aria-label="Throughput speedup summary"
        className="p-5 rounded-md bg-[#101622] border border-slate-800/90 flex flex-wrap items-center justify-between gap-6"
      >
        <div>
          <div className="text-xs font-medium text-slate-400">
            Baseline vs Optimized Throughput
          </div>
          <div className="mt-1 text-2xl sm:text-3xl font-semibold text-slate-100 font-mono tabular-nums">
            Baseline vs optimized:{' '}
            <span className="text-sky-400">{speedup ? `${speedup}x speedup` : '—'}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-6 text-xs">
          <div>
            <span className="text-slate-500 block">Baseline (Sequential)</span>
            <span className="mt-0.5 block text-base font-semibold font-mono tabular-nums text-slate-200">
              {formatNumber(Math.round(data.baseline_rps))} rec/s
            </span>
          </div>
          <div className="h-8 w-px bg-slate-800 hidden sm:block" aria-hidden="true" />
          <div>
            <span className="text-slate-500 block">Optimized (Concurrent + Vectorized)</span>
            <span className="mt-0.5 block text-base font-semibold font-mono tabular-nums text-sky-400">
              {formatNumber(Math.round(data.optimized_rps))} rec/s
            </span>
          </div>
        </div>
      </section>

      {/* Recharts Throughput Line Chart */}
      <section
        aria-label="Records per second versus concurrency chart"
        className="p-5 rounded-md bg-[#101622] border border-slate-800/90 space-y-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">
              Sync Throughput by Concurrency & Batch Size
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Measured across 100,000 records at 50 ms simulated API latency (records/sec vs concurrency)
            </p>
          </div>
        </div>

        <div className="h-80 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 10, right: 20, left: 10, bottom: 10 }}>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis
                dataKey="concurrency"
                stroke="#94a3b8"
                tick={{ fill: '#94a3b8', fontSize: 12 }}
                label={{
                  value: 'Concurrency (workers)',
                  position: 'insideBottom',
                  offset: -6,
                  fill: '#94a3b8',
                  fontSize: 11,
                }}
              />
              <YAxis
                stroke="#94a3b8"
                tick={{ fill: '#94a3b8', fontSize: 12 }}
                tickFormatter={(v: number) => `${(v / 1000).toFixed(0)}k`}
                label={{
                  value: 'Records / sec',
                  angle: -90,
                  position: 'insideLeft',
                  fill: '#94a3b8',
                  fontSize: 11,
                }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#0b1019',
                  borderColor: '#1e293b',
                  borderRadius: '6px',
                  fontSize: '12px',
                  color: '#f1f5f9',
                }}
                formatter={(value) => [
                  `${formatNumber(Math.round(Number(value ?? 0)))} rec/s`,
                ]}
                labelFormatter={(label) => `Concurrency: ${label}`}
              />
              <Legend
                wrapperStyle={{ fontSize: '12px', paddingTop: '12px' }}
              />
              {batchSizes.map((size, idx) => (
                <Line
                  key={size}
                  type="monotone"
                  dataKey={`batch_${size}`}
                  name={`Batch ${size}`}
                  stroke={LINE_COLORS[idx % LINE_COLORS.length]}
                  strokeWidth={2}
                  dot={{ r: 3.5 }}
                  activeDot={{ r: 5 }}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      {/* AI Evaluation Metric Cards */}
      <section aria-label="AI evaluation metrics" className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-slate-200">
            AI Generation & Self-Healing Evaluation
          </h2>
          <span className="text-xs text-slate-400">24-scenario benchmark suite</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {aiCards.map((card) => (
            <article
              key={card.label}
              className="p-4 rounded-md bg-[#101622] border border-slate-800/90 space-y-2"
            >
              <div className="text-xs font-medium text-slate-400">{card.label}</div>
              <div className="text-2xl font-semibold text-slate-100 font-mono tabular-nums">
                {card.value}
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">{card.explanation}</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
