"""Throughput benchmark comparing sequential baseline vs concurrent batched engine."""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import uvicorn

from connector.config import ConnectorConfig
from connector.destination import DuckDBDestination
from connector.engine import ConnectorEngine
from connector.retry import RetryConfig
from mock_server.app import create_app
from mock_server.faults import FaultConfig


@dataclass
class BenchmarkRow:
    """Single benchmark measurement row."""

    scenario: str
    concurrency: int
    batch_size: int
    use_pooling: bool
    latency_ms: float
    rate_429: float
    rate_500: float
    records_target: int
    records_loaded: int
    retries: int
    duration_seconds: float
    records_per_second: float


def _find_free_port() -> int:
    """Bind an ephemeral TCP port and return its port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _BackgroundMockServer:
    """Context manager running uvicorn mock server in a daemon thread."""

    def __init__(
        self,
        record_count: int,
        latency_ms: float,
        rate_429: float = 0.0,
        rate_500: float = 0.0,
        retry_after: str = "0.02",
        seed: int = 42,
    ) -> None:
        self.port = _find_free_port()
        self.app = create_app(
            paystub_count=100,
            gitlike_count=record_count,
            sheetsy_count=50,
            seed=seed,
            gitlike_token="bench-token",
            fault_config=FaultConfig(
                latency_ms=latency_ms,
                rate_429=rate_429,
                rate_500=rate_500,
                retry_after=retry_after,
            ),
        )
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def base_url(self) -> str:
        """Return base URL for the running mock server."""
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _BackgroundMockServer:
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("Mock uvicorn server failed to start")
        return self

    def __exit__(self, *args: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5.0)


def _build_bench_config(
    base_url: str,
    page_size: int = 500,
    max_concurrency: int = 1,
) -> ConnectorConfig:
    """Build a ConnectorConfig targeting the mock gitlike offset endpoint."""
    return ConnectorConfig.model_validate(
        {
            "name": "bench",
            "base_url": f"{base_url}/gitlike",
            "max_concurrency": max_concurrency,
            "auth": {"type": "bearer", "env_var": "BENCH_GITLIKE_TOKEN"},
            "streams": [
                {
                    "name": "repos",
                    "path": "/v1/repos",
                    "method": "GET",
                    "records_path": "result.items",
                    "primary_key": "id",
                    "pagination": {
                        "type": "offset",
                        "offset_param": "offset",
                        "limit_param": "limit",
                        "page_size": page_size,
                        "total_path": "meta.total",
                    },
                    "schema": {
                        "id": "integer",
                        "name": "string",
                        "stars": "integer",
                        "private": "boolean",
                        "updated_at": "string",
                    },
                }
            ],
        }
    )


async def _run_single(
    scenario: str,
    base_url: str,
    record_count: int,
    page_size: int,
    concurrency: int,
    batch_size: int,
    use_pooling: bool,
    latency_ms: float,
    rate_429: float = 0.0,
    rate_500: float = 0.0,
) -> BenchmarkRow:
    """Execute one connector run against the mock server and measure throughput."""
    cfg = _build_bench_config(
        base_url=base_url,
        page_size=page_size,
        max_concurrency=concurrency,
    )
    quiet_logger = logging.getLogger("connector.bench.quiet")
    quiet_logger.setLevel(logging.CRITICAL)

    retry_cfg = RetryConfig(
        max_retries=8,
        base_delay=0.01,
        max_delay=0.5,
        jitter=True,
    )

    with DuckDBDestination(":memory:") as dest:
        engine = ConnectorEngine(
            destination=dest,
            retry_config=retry_cfg,
            max_concurrency=concurrency,
            batch_size=batch_size,
            use_pooling=use_pooling,
            logger=quiet_logger,
        )
        t0 = time.perf_counter()
        result = await engine.run(cfg)
        elapsed = max(1e-6, time.perf_counter() - t0)
        loaded = dest.count("bench_repos")

    if loaded != record_count:
        raise AssertionError(
            f"Scenario '{scenario}' data mismatch: expected {record_count}, got {loaded}"
        )

    return BenchmarkRow(
        scenario=scenario,
        concurrency=concurrency,
        batch_size=batch_size,
        use_pooling=use_pooling,
        latency_ms=latency_ms,
        rate_429=rate_429,
        rate_500=rate_500,
        records_target=record_count,
        records_loaded=loaded,
        retries=result.total_retries,
        duration_seconds=round(elapsed, 4),
        records_per_second=round(loaded / elapsed, 2),
    )


def _write_csv(rows: list[BenchmarkRow], csv_path: Path) -> None:
    """Write benchmark results table to CSV."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(rows[0]).keys())
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _plot_charts(rows: list[BenchmarkRow], output_dir: Path) -> tuple[Path, Path]:
    """Generate throughput vs concurrency and throughput vs batch size PNG charts."""
    conc_rows = [r for r in rows if r.scenario.startswith("concurrency_")]
    batch_rows = [r for r in rows if r.scenario.startswith("batch_")]

    conc_path = output_dir / "throughput_vs_concurrency.png"
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(
        [r.concurrency for r in conc_rows],
        [r.records_per_second for r in conc_rows],
        marker="o",
        linewidth=2,
        color="#1f77b4",
    )
    ax.set_title("Connector Throughput vs Concurrency")
    ax.set_xlabel("Concurrency Level")
    ax.set_ylabel("Throughput (records/sec)")
    ax.set_xticks([r.concurrency for r in conc_rows])
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.tight_layout()
    fig.savefig(conc_path, dpi=150)
    plt.close(fig)

    batch_path = output_dir / "throughput_vs_batch_size.png"
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(
        [str(r.batch_size) for r in batch_rows],
        [r.records_per_second for r in batch_rows],
        marker="s",
        linewidth=2,
        color="#2ca02c",
    )
    ax.set_title("Connector Throughput vs Upsert Batch Size")
    ax.set_xlabel("Upsert Batch Size (records)")
    ax.set_ylabel("Throughput (records/sec)")
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.tight_layout()
    fig.savefig(batch_path, dpi=150)
    plt.close(fig)

    return conc_path, batch_path


async def run_benchmark(
    record_count: int = 100_000,
    page_size: int = 500,
    latency_ms: float = 50.0,
    concurrency_levels: tuple[int, ...] = (1, 2, 4, 8, 16, 32),
    batch_sizes: tuple[int, ...] = (100, 500, 1000, 5000),
    output_dir: Path = Path("benchmarks"),
) -> dict[str, Any]:
    """Run baseline, sweeps, and fault-injection benchmarks and save artifacts."""
    os.environ.setdefault("BENCH_GITLIKE_TOKEN", "bench-token")
    rows: list[BenchmarkRow] = []

    with _BackgroundMockServer(
        record_count=record_count,
        latency_ms=latency_ms,
        rate_429=0.0,
        rate_500=0.0,
    ) as server:
        # 1. Sequential baseline (concurrency=1, no connection pooling, small batch)
        baseline = await _run_single(
            scenario="baseline_sequential",
            base_url=server.base_url,
            record_count=record_count,
            page_size=page_size,
            concurrency=1,
            batch_size=page_size,
            use_pooling=False,
            latency_ms=latency_ms,
        )
        rows.append(baseline)

        # 2. Sweep concurrency levels
        for conc in concurrency_levels:
            rows.append(
                await _run_single(
                    scenario=f"concurrency_{conc}",
                    base_url=server.base_url,
                    record_count=record_count,
                    page_size=page_size,
                    concurrency=conc,
                    batch_size=1000,
                    use_pooling=True,
                    latency_ms=latency_ms,
                )
            )

        # 3. Sweep batch sizes at concurrency=16
        sweep_conc = 16 if 16 in concurrency_levels else concurrency_levels[-1]
        for bsize in batch_sizes:
            rows.append(
                await _run_single(
                    scenario=f"batch_{bsize}",
                    base_url=server.base_url,
                    record_count=record_count,
                    page_size=page_size,
                    concurrency=sweep_conc,
                    batch_size=bsize,
                    use_pooling=True,
                    latency_ms=latency_ms,
                )
            )

        # 4. Fault injection run: 5% 429s + 2% 500s
        server.app.state.faults.config.rate_429 = 0.05
        server.app.state.faults.config.rate_500 = 0.02
        server.app.state.faults.reset(seed=42)
        fault_row = await _run_single(
            scenario="faults_5pct429_2pct500",
            base_url=server.base_url,
            record_count=record_count,
            page_size=page_size,
            concurrency=sweep_conc,
            batch_size=1000,
            use_pooling=True,
            latency_ms=latency_ms,
            rate_429=0.05,
            rate_500=0.02,
        )
        rows.append(fault_row)

    csv_path = output_dir / "throughput_results.csv"
    _write_csv(rows, csv_path)
    conc_chart, batch_chart = _plot_charts(rows, output_dir)

    best_clean = max(
        (r for r in rows if r.rate_429 == 0.0 and r.rate_500 == 0.0),
        key=lambda r: r.records_per_second,
    )
    speedup = best_clean.records_per_second / max(1e-6, baseline.records_per_second)

    return {
        "rows": rows,
        "baseline_rps": baseline.records_per_second,
        "optimized_rps": best_clean.records_per_second,
        "speedup": round(speedup, 2),
        "fault_loaded": fault_row.records_loaded,
        "fault_retries": fault_row.retries,
        "csv_path": str(csv_path),
        "concurrency_chart": str(conc_chart),
        "batch_chart": str(batch_chart),
    }


def main() -> None:
    """CLI entrypoint for running throughput benchmarks."""
    parser = argparse.ArgumentParser(description="Run connector throughput benchmark.")
    parser.add_argument("--records", type=int, default=100_000)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--latency-ms", type=float, default=50.0)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks"))
    args = parser.parse_args()

    summary = asyncio.run(
        run_benchmark(
            record_count=args.records,
            page_size=args.page_size,
            latency_ms=args.latency_ms,
            output_dir=args.output_dir,
        )
    )

    print(
        f"\n{'Scenario':<26} {'Conc':>4} {'Batch':>6} {'Pool':>5} "
        f"{'Rows':>8} {'Retries':>7} {'Sec':>8} {'Rec/s':>10}"
    )
    print("-" * 82)
    for row in summary["rows"]:
        print(
            f"{row.scenario:<26} {row.concurrency:>4} {row.batch_size:>6} "
            f"{str(row.use_pooling):>5} {row.records_loaded:>8} "
            f"{row.retries:>7} {row.duration_seconds:>8.3f} "
            f"{row.records_per_second:>10.2f}"
        )

    print(
        f"\nFault run check: loaded={summary['fault_loaded']} "
        f"(zero data loss, zero duplicates), retries={summary['fault_retries']}"
    )
    print(
        f"Baseline: {summary['baseline_rps']:.2f} rec/s, "
        f"Optimized: {summary['optimized_rps']:.2f} rec/s, "
        f"Speedup: {summary['speedup']:.2f}x"
    )


if __name__ == "__main__":
    main()
