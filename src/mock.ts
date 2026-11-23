import type {
  AuditLogEntry,
  BenchmarksData,
  ConnectorSummary,
  DeadLetterRecord,
  DriftEvent,
  RepairProposal,
  RunDetail,
  RunSummary,
} from './types';

const now = Date.now();
const minsAgo = (m: number) => new Date(now - m * 60_000).toISOString();
const hoursAgo = (h: number) => new Date(now - h * 3_600_000).toISOString();

const PAYSTUB_DIFF = `--- connectors/paystub.yaml
+++ connectors/paystub.yaml
@@ -8,11 +8,11 @@
   - name: charges
     path: /v1/charges
     method: GET
-    records_path: data
+    records_path: payload.charges
     primary_key: id
     pagination:
       type: cursor
-      cursor_param: starting_after
+      cursor_param: after
       cursor_field: id
     schema:
-      amount: integer
+      amount_cents: integer`;

const GITLIKE_ESCALATED_DIFF = `--- connectors/gitlike.yaml
+++ connectors/gitlike.yaml
@@ -11,8 +11,8 @@
   - name: repositories
     path: /orgs/octo/repos
     method: GET
     records_path: data.repositories
-    primary_key: id
+    primary_key: repo_slug
     pagination:
       type: offset
       offset_param: offset`;

const SHEETSY_APPLIED_DIFF = `--- connectors/sheetsy.yaml
+++ connectors/sheetsy.yaml
@@ -9,7 +9,7 @@
   - name: spreadsheets
     path: /v2/sheets
     method: GET
     records_path: rows
     primary_key: sheet_id
     schema:
-      title: string
+      sheet_title: string`;

const PAYSTUB_REJECTED_DIFF = `--- connectors/paystub.yaml
+++ connectors/paystub.yaml
@@ -14,5 +14,5 @@
     pagination:
       type: cursor
       cursor_param: starting_after
-      limit_param: limit
+      limit_param: max_items`;

const INITIAL_CONNECTORS: ConnectorSummary[] = [
  {
    name: 'paystub',
    streams: ['charges', 'customers', 'refunds'],
    last_run_status: 'success',
    last_run_at: minsAgo(14),
    last_rows_synced: 14280,
  },
  {
    name: 'gitlike',
    streams: ['repositories', 'issues', 'pull_requests'],
    last_run_status: 'failed',
    last_run_at: minsAgo(42),
    last_rows_synced: 3190,
  },
  {
    name: 'sheetsy',
    streams: ['spreadsheets', 'worksheets'],
    last_run_status: 'success',
    last_run_at: hoursAgo(2),
    last_rows_synced: 6840,
  },
  {
    name: 'hubcrm',
    streams: ['contacts', 'deals'],
    last_run_status: 'success',
    last_run_at: hoursAgo(5),
    last_rows_synced: 9415,
  },
];

const INITIAL_RUN_DETAILS: Record<string, RunDetail> = {
  'run-9f41c8': {
    run_id: 'run-9f41c8',
    connector: 'paystub',
    status: 'success',
    rows_synced: 14280,
    rows_dead_lettered: 2,
    retries: 1,
    drift_events: 1,
    duration_ms: 1842,
    started_at: minsAgo(15),
    finished_at: minsAgo(14),
    streams: [
      {
        stream_name: 'charges',
        table_name: 'paystub__charges',
        rows_synced: 9420,
        rows_dead_lettered: 2,
        pages_fetched: 19,
        retries: 1,
        drift_events_count: 1,
        duration_ms: 1120,
        bookmark: 'ch_009420',
      },
      {
        stream_name: 'customers',
        table_name: 'paystub__customers',
        rows_synced: 3610,
        rows_dead_lettered: 0,
        pages_fetched: 8,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 482,
        bookmark: 'cus_003610',
      },
      {
        stream_name: 'refunds',
        table_name: 'paystub__refunds',
        rows_synced: 1250,
        rows_dead_lettered: 0,
        pages_fetched: 3,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 240,
        bookmark: 're_001250',
      },
    ],
  },
  'run-7c22b1': {
    run_id: 'run-7c22b1',
    connector: 'gitlike',
    status: 'failed',
    rows_synced: 3190,
    rows_dead_lettered: 14,
    retries: 4,
    drift_events: 2,
    duration_ms: 4310,
    started_at: minsAgo(43),
    finished_at: minsAgo(42),
    error_message:
      'Primary key field "id" missing from stream "repositories" response records after upstream schema mutation.',
    streams: [
      {
        stream_name: 'repositories',
        table_name: 'gitlike__repositories',
        rows_synced: 0,
        rows_dead_lettered: 14,
        pages_fetched: 1,
        retries: 3,
        drift_events_count: 2,
        duration_ms: 2150,
        bookmark: null,
      },
      {
        stream_name: 'issues',
        table_name: 'gitlike__issues',
        rows_synced: 2140,
        rows_dead_lettered: 0,
        pages_fetched: 5,
        retries: 1,
        drift_events_count: 0,
        duration_ms: 1420,
        bookmark: 'iss_02140',
      },
      {
        stream_name: 'pull_requests',
        table_name: 'gitlike__pull_requests',
        rows_synced: 1050,
        rows_dead_lettered: 0,
        pages_fetched: 3,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 740,
        bookmark: 'pr_01050',
      },
    ],
  },
  'run-6a19d4': {
    run_id: 'run-6a19d4',
    connector: 'sheetsy',
    status: 'success',
    rows_synced: 6840,
    rows_dead_lettered: 0,
    retries: 0,
    drift_events: 1,
    duration_ms: 1290,
    started_at: hoursAgo(2),
    finished_at: hoursAgo(2),
    streams: [
      {
        stream_name: 'spreadsheets',
        table_name: 'sheetsy__spreadsheets',
        rows_synced: 2420,
        rows_dead_lettered: 0,
        pages_fetched: 5,
        retries: 0,
        drift_events_count: 1,
        duration_ms: 710,
        bookmark: 'sh_02420',
      },
      {
        stream_name: 'worksheets',
        table_name: 'sheetsy__worksheets',
        rows_synced: 4420,
        rows_dead_lettered: 0,
        pages_fetched: 9,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 580,
        bookmark: 'ws_04420',
      },
    ],
  },
  'run-5e80f0': {
    run_id: 'run-5e80f0',
    connector: 'hubcrm',
    status: 'success',
    rows_synced: 9415,
    rows_dead_lettered: 1,
    retries: 2,
    drift_events: 0,
    duration_ms: 2105,
    started_at: hoursAgo(5),
    finished_at: hoursAgo(5),
    streams: [
      {
        stream_name: 'contacts',
        table_name: 'hubcrm__contacts',
        rows_synced: 6100,
        rows_dead_lettered: 1,
        pages_fetched: 13,
        retries: 2,
        drift_events_count: 0,
        duration_ms: 1380,
        bookmark: 'ct_06100',
      },
      {
        stream_name: 'deals',
        table_name: 'hubcrm__deals',
        rows_synced: 3315,
        rows_dead_lettered: 0,
        pages_fetched: 7,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 725,
        bookmark: 'dl_03315',
      },
    ],
  },
  'run-4b02a7': {
    run_id: 'run-4b02a7',
    connector: 'paystub',
    status: 'success',
    rows_synced: 13910,
    rows_dead_lettered: 0,
    retries: 0,
    drift_events: 0,
    duration_ms: 1695,
    started_at: hoursAgo(8),
    finished_at: hoursAgo(8),
    streams: [
      {
        stream_name: 'charges',
        table_name: 'paystub__charges',
        rows_synced: 9120,
        rows_dead_lettered: 0,
        pages_fetched: 19,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 1030,
        bookmark: 'ch_009120',
      },
      {
        stream_name: 'customers',
        table_name: 'paystub__customers',
        rows_synced: 3560,
        rows_dead_lettered: 0,
        pages_fetched: 8,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 450,
        bookmark: 'cus_003560',
      },
      {
        stream_name: 'refunds',
        table_name: 'paystub__refunds',
        rows_synced: 1230,
        rows_dead_lettered: 0,
        pages_fetched: 3,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 215,
        bookmark: 're_001230',
      },
    ],
  },
  'run-3c91e3': {
    run_id: 'run-3c91e3',
    connector: 'sheetsy',
    status: 'failed',
    rows_synced: 1120,
    rows_dead_lettered: 6,
    retries: 5,
    drift_events: 1,
    duration_ms: 5120,
    started_at: hoursAgo(12),
    finished_at: hoursAgo(12),
    error_message: 'HTTP 429 rate-limit exhausted after 5 retries on stream "worksheets".',
    streams: [
      {
        stream_name: 'spreadsheets',
        table_name: 'sheetsy__spreadsheets',
        rows_synced: 1120,
        rows_dead_lettered: 0,
        pages_fetched: 3,
        retries: 1,
        drift_events_count: 1,
        duration_ms: 1180,
        bookmark: 'sh_01120',
      },
      {
        stream_name: 'worksheets',
        table_name: 'sheetsy__worksheets',
        rows_synced: 0,
        rows_dead_lettered: 6,
        pages_fetched: 1,
        retries: 4,
        drift_events_count: 0,
        duration_ms: 3940,
        bookmark: null,
      },
    ],
  },
  'run-2d77b9': {
    run_id: 'run-2d77b9',
    connector: 'gitlike',
    status: 'success',
    rows_synced: 7850,
    rows_dead_lettered: 0,
    retries: 1,
    drift_events: 0,
    duration_ms: 1940,
    started_at: hoursAgo(18),
    finished_at: hoursAgo(18),
    streams: [
      {
        stream_name: 'repositories',
        table_name: 'gitlike__repositories',
        rows_synced: 4650,
        rows_dead_lettered: 0,
        pages_fetched: 10,
        retries: 1,
        drift_events_count: 0,
        duration_ms: 990,
        bookmark: '4650',
      },
      {
        stream_name: 'issues',
        table_name: 'gitlike__issues',
        rows_synced: 2150,
        rows_dead_lettered: 0,
        pages_fetched: 5,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 610,
        bookmark: '2150',
      },
      {
        stream_name: 'pull_requests',
        table_name: 'gitlike__pull_requests',
        rows_synced: 1050,
        rows_dead_lettered: 0,
        pages_fetched: 3,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 340,
        bookmark: '1050',
      },
    ],
  },
  'run-1a04f2': {
    run_id: 'run-1a04f2',
    connector: 'hubcrm',
    status: 'success',
    rows_synced: 8920,
    rows_dead_lettered: 0,
    retries: 0,
    drift_events: 0,
    duration_ms: 1785,
    started_at: hoursAgo(22),
    finished_at: hoursAgo(22),
    streams: [
      {
        stream_name: 'contacts',
        table_name: 'hubcrm__contacts',
        rows_synced: 5720,
        rows_dead_lettered: 0,
        pages_fetched: 12,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 1120,
        bookmark: 'ct_05720',
      },
      {
        stream_name: 'deals',
        table_name: 'hubcrm__deals',
        rows_synced: 3200,
        rows_dead_lettered: 0,
        pages_fetched: 7,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 665,
        bookmark: 'dl_03200',
      },
    ],
  },
};

const INITIAL_DEAD_LETTERS: Record<string, DeadLetterRecord[]> = {
  'run-9f41c8': [
    {
      id: 'dl-101',
      stream_name: 'charges',
      error_reason: 'Field "amount_cents" expected integer, received malformed string "N/A"',
      recorded_at: minsAgo(14),
      record: {
        id: 'ch_008914',
        amount_cents: 'N/A',
        currency: 'usd',
        status: 'pending',
        created_at: minsAgo(16),
      },
    },
    {
      id: 'dl-102',
      stream_name: 'charges',
      error_reason: 'Null constraint violation on non-nullable timestamp "created_at"',
      recorded_at: minsAgo(14),
      record: {
        id: 'ch_009102',
        amount_cents: 4200,
        currency: 'eur',
        status: 'succeeded',
        created_at: null,
      },
    },
  ],
  'run-7c22b1': [
    {
      id: 'dl-201',
      stream_name: 'repositories',
      error_reason: 'Missing primary key "id" on extracted record',
      recorded_at: minsAgo(42),
      record: {
        repo_slug: 'acme/vector-ingest',
        stargazers_count: 412,
        language: 'Rust',
        private: false,
      },
    },
    {
      id: 'dl-202',
      stream_name: 'repositories',
      error_reason: 'Missing primary key "id" on extracted record',
      recorded_at: minsAgo(42),
      record: {
        repo_slug: 'acme/warehouse-sync',
        stargazers_count: 198,
        language: 'Python',
        private: true,
      },
    },
    {
      id: 'dl-203',
      stream_name: 'repositories',
      error_reason: 'Missing primary key "id" on extracted record',
      recorded_at: minsAgo(42),
      record: {
        repo_slug: 'acme/schema-guard',
        stargazers_count: 87,
        language: 'TypeScript',
        private: false,
      },
    },
  ],
  'run-5e80f0': [
    {
      id: 'dl-301',
      stream_name: 'contacts',
      error_reason: 'Invalid JSON payload type in nested attribute "properties"',
      recorded_at: hoursAgo(5),
      record: {
        id: 'ct_05988',
        email: 'ops-archive@example.org',
        properties: 'CORRUPT_STRING',
      },
    },
  ],
  'run-3c91e3': [
    {
      id: 'dl-401',
      stream_name: 'worksheets',
      error_reason: 'Row exceeded max nested depth (8) during normalization',
      recorded_at: hoursAgo(12),
      record: {
        sheet_id: 'ws_891',
        title: 'Q4 Forecast Matrix',
        cells: { nested: { overflow: true } },
      },
    },
  ],
};

const INITIAL_DRIFT_EVENTS: DriftEvent[] = [
  {
    connector: 'paystub',
    stream: 'charges',
    type: 'added',
    field: 'fee_cents',
    detected_at: minsAgo(14),
    details: { inferred_type: 'integer' },
    run_id: 'run-9f41c8',
  },
  {
    connector: 'paystub',
    stream: 'charges',
    type: 'retyped',
    field: 'amount',
    detected_at: hoursAgo(3),
    details: { expected: 'integer', observed: 'string' },
    run_id: 'run-4b02a7',
  },
  {
    connector: 'gitlike',
    stream: 'repositories',
    type: 'removed',
    field: 'id',
    detected_at: minsAgo(42),
    details: { replacement_candidate: 'repo_slug' },
    run_id: 'run-7c22b1',
  },
  {
    connector: 'gitlike',
    stream: 'repositories',
    type: 'added',
    field: 'stargazers_count',
    detected_at: minsAgo(42),
    details: { inferred_type: 'integer' },
    run_id: 'run-7c22b1',
  },
  {
    connector: 'sheetsy',
    stream: 'spreadsheets',
    type: 'retyped',
    field: 'row_count',
    detected_at: hoursAgo(2),
    details: { expected: 'integer', observed: 'string' },
    run_id: 'run-6a19d4',
  },
  {
    connector: 'sheetsy',
    stream: 'spreadsheets',
    type: 'removed',
    field: 'title',
    detected_at: hoursAgo(12),
    details: { renamed_to: 'sheet_title' },
    run_id: 'run-3c91e3',
  },
];

const INITIAL_REPAIRS: RepairProposal[] = [
  {
    id: 'rep-paystub-01',
    connector: 'paystub',
    trigger: 'Stream "charges" records_path moved from "data" to "payload.charges" and cursor param renamed to "after"',
    diff: PAYSTUB_DIFF,
    validation_results: [
      {
        check: 'YAML Syntax & Pydantic v2 Schema',
        passed: true,
        reason: 'Config conforms to ConnectorConfig schema v2 with SQL-safe stream identifiers.',
      },
      {
        check: 'Live HTTP Endpoint Dry-Run Probe',
        passed: true,
        reason: 'GET /v1/charges returned HTTP 200 and extracted 40 records via payload.charges.',
      },
      {
        check: 'Cursor Pagination Continuity',
        passed: true,
        reason: 'Subsequent page request using ?after=ch_0040 returned non-overlapping records.',
      },
      {
        check: 'In-Memory DuckDB Upsert & PK Check',
        passed: true,
        reason: 'Upserted 40/40 sample rows into paystub__charges with zero null primary keys.',
      },
    ],
    status: 'proposed',
    attempts: 2,
    created_at: minsAgo(11),
  },
  {
    id: 'rep-gitlike-02',
    connector: 'gitlike',
    trigger: 'Primary key "id" removed from /orgs/octo/repos payload; candidate replacement "repo_slug" proposed',
    diff: GITLIKE_ESCALATED_DIFF,
    validation_results: [
      {
        check: 'YAML Syntax & Pydantic v2 Schema',
        passed: true,
        reason: 'Config parses cleanly and passes static model validation.',
      },
      {
        check: 'Primary Key Non-Null & Uniqueness Probe',
        passed: false,
        reason: '2 of 35 live records returned duplicate or empty "repo_slug" values; primary key switch unsafe without warehouse migration.',
      },
      {
        check: 'In-Memory DuckDB Upsert Check',
        passed: false,
        reason: 'Skipped because upstream uniqueness invariant failed.',
      },
    ],
    status: 'escalated',
    attempts: 3,
    created_at: minsAgo(39),
  },
  {
    id: 'rep-sheetsy-03',
    connector: 'sheetsy',
    trigger: 'Upstream field "title" renamed to "sheet_title" on stream "spreadsheets"',
    diff: SHEETSY_APPLIED_DIFF,
    validation_results: [
      {
        check: 'YAML Syntax & Pydantic v2 Schema',
        passed: true,
        reason: 'Valid ConnectorConfig structure.',
      },
      {
        check: 'Live HTTP Endpoint Dry-Run Probe',
        passed: true,
        reason: 'Extracted 30 sample rows from GET /v2/sheets with "sheet_title" populated.',
      },
      {
        check: 'In-Memory DuckDB Upsert & PK Check',
        passed: true,
        reason: 'Upserted 30/30 rows idempotently.',
      },
    ],
    status: 'applied',
    attempts: 1,
    created_at: hoursAgo(11),
  },
  {
    id: 'rep-paystub-04',
    connector: 'paystub',
    trigger: 'Optional pagination limit parameter rename check during fault injection test',
    diff: PAYSTUB_REJECTED_DIFF,
    validation_results: [
      {
        check: 'YAML Syntax & Pydantic v2 Schema',
        passed: true,
        reason: 'Valid YAML syntax.',
      },
      {
        check: 'Live HTTP Endpoint Dry-Run Probe',
        passed: true,
        reason: 'Endpoint ignored max_items and fell back to default page size.',
      },
    ],
    status: 'rejected',
    attempts: 1,
    created_at: hoursAgo(19),
  },
];

const INITIAL_AUDIT: AuditLogEntry[] = [
  {
    id: 'aud-008',
    timestamp: minsAgo(11),
    connector: 'paystub',
    trigger: 'records_path shifted data -> payload.charges',
    model: 'gemini-4-hydrogen',
    decision: 'proposed',
    attempts: 2,
    diff: PAYSTUB_DIFF,
  },
  {
    id: 'aud-007',
    timestamp: minsAgo(39),
    connector: 'gitlike',
    trigger: 'Primary key id removed in repositories stream',
    model: 'gemini-4-hydrogen',
    decision: 'escalated',
    attempts: 3,
    diff: GITLIKE_ESCALATED_DIFF,
  },
  {
    id: 'aud-006',
    timestamp: hoursAgo(11),
    connector: 'sheetsy',
    trigger: 'Field rename title -> sheet_title',
    model: 'gemini-4-hydrogen',
    decision: 'applied',
    attempts: 1,
    diff: SHEETSY_APPLIED_DIFF,
  },
  {
    id: 'aud-005',
    timestamp: hoursAgo(19),
    connector: 'paystub',
    trigger: 'Pagination limit parameter mismatch probe',
    model: 'gemini-4-hydrogen',
    decision: 'rejected',
    attempts: 1,
    diff: PAYSTUB_REJECTED_DIFF,
  },
  {
    id: 'aud-004',
    timestamp: hoursAgo(26),
    connector: 'hubcrm',
    trigger: 'Spec-to-config generation from HubCRM OpenAPI v3',
    model: 'gemini-4-hydrogen',
    decision: 'applied',
    attempts: 1,
    diff: `--- /dev/null\n+++ connectors/hubcrm.yaml\n@@ -0,0 +1,14 @@\n+name: hubcrm\n+base_url: http://localhost:8000/hubcrm\n+auth:\n+  type: bearer\n+  env_var: HUBCRM_TOKEN\n+streams:\n+  - name: contacts\n+    path: /crm/v3/objects/contacts\n+    records_path: results\n+    primary_key: id`,
  },
  {
    id: 'aud-003',
    timestamp: hoursAgo(38),
    connector: 'gitlike',
    trigger: 'Spec-to-config generation from GitLike OpenAPI spec',
    model: 'gemini-4-hydrogen',
    decision: 'applied',
    attempts: 2,
    diff: `--- /dev/null\n+++ connectors/gitlike.yaml\n@@ -0,0 +1,12 @@\n+name: gitlike\n+base_url: http://localhost:8000/gitlike\n+auth:\n+  type: bearer\n+  env_var: GITLIKE_TOKEN\n+streams:\n+  - name: repositories\n+    path: /orgs/octo/repos\n+    records_path: result.items\n+    primary_key: id`,
  },
];

const BENCHMARKS: BenchmarksData = {
  throughput: [
    { concurrency: 1, batch_size: 100, records_per_sec: 3210.4 },
    { concurrency: 2, batch_size: 100, records_per_sec: 5490.2 },
    { concurrency: 4, batch_size: 100, records_per_sec: 7940.8 },
    { concurrency: 8, batch_size: 100, records_per_sec: 9120.1 },
    { concurrency: 16, batch_size: 100, records_per_sec: 9663.5 },
    { concurrency: 32, batch_size: 100, records_per_sec: 9215.0 },

    { concurrency: 1, batch_size: 500, records_per_sec: 6407.17 },
    { concurrency: 2, batch_size: 500, records_per_sec: 9840.5 },
    { concurrency: 4, batch_size: 500, records_per_sec: 14920.3 },
    { concurrency: 8, batch_size: 500, records_per_sec: 17480.9 },
    { concurrency: 16, batch_size: 500, records_per_sec: 18072.94 },
    { concurrency: 32, batch_size: 500, records_per_sec: 16910.4 },

    { concurrency: 1, batch_size: 1000, records_per_sec: 7135.27 },
    { concurrency: 2, batch_size: 1000, records_per_sec: 11472.04 },
    { concurrency: 4, batch_size: 1000, records_per_sec: 22327.73 },
    { concurrency: 8, batch_size: 1000, records_per_sec: 23082.19 },
    { concurrency: 16, batch_size: 1000, records_per_sec: 22806.66 },
    { concurrency: 32, batch_size: 1000, records_per_sec: 20430.54 },

    { concurrency: 1, batch_size: 5000, records_per_sec: 7820.1 },
    { concurrency: 2, batch_size: 5000, records_per_sec: 13190.6 },
    { concurrency: 4, batch_size: 5000, records_per_sec: 23940.2 },
    { concurrency: 8, batch_size: 5000, records_per_sec: 25410.8 },
    { concurrency: 16, batch_size: 5000, records_per_sec: 26091.28 },
    { concurrency: 32, batch_size: 5000, records_per_sec: 24310.7 },
  ],
  baseline_rps: 6407.17,
  optimized_rps: 26091.28,
  ai_eval: {
    gen_pass_at_1: 0.6,
    gen_pass_at_3: 1.0,
    repair_success_rate: 1.0,
    gate_catch_rate: 1.0,
    mean_latency_ms: 171.12,
  },
};

// Stateful in-memory store so interactive triggers, polling, and approvals persist across navigation in mock mode.
class MockStore {
  connectors: ConnectorSummary[] = structuredClone(INITIAL_CONNECTORS);
  runDetails: Record<string, RunDetail> = structuredClone(INITIAL_RUN_DETAILS);
  deadLetters: Record<string, DeadLetterRecord[]> = structuredClone(INITIAL_DEAD_LETTERS);
  driftEvents: DriftEvent[] = structuredClone(INITIAL_DRIFT_EVENTS);
  repairs: RepairProposal[] = structuredClone(INITIAL_REPAIRS);
  audit: AuditLogEntry[] = structuredClone(INITIAL_AUDIT);
  benchmarks: BenchmarksData = structuredClone(BENCHMARKS);

  getConnectors(): ConnectorSummary[] {
    return structuredClone(this.connectors);
  }

  triggerRun(name: string): { run_id: string } {
    const connector = this.connectors.find((c) => c.name === name);
    if (!connector) {
      throw new Error(`Connector "${name}" not found`);
    }
    const runId = `run-${Math.random().toString(16).slice(2, 8)}`;
    const startedAt = new Date().toISOString();
    connector.last_run_status = 'running';
    connector.last_run_at = startedAt;

    const activeRun: RunDetail = {
      run_id: runId,
      connector: name,
      status: 'running',
      rows_synced: 0,
      rows_dead_lettered: 0,
      retries: 0,
      drift_events: 0,
      duration_ms: 0,
      started_at: startedAt,
      finished_at: null,
      streams: connector.streams.map((streamName) => ({
        stream_name: streamName,
        table_name: `${name}__${streamName}`,
        rows_synced: 0,
        rows_dead_lettered: 0,
        retries: 0,
        drift_events_count: 0,
        duration_ms: 0,
      })),
    };
    this.runDetails[runId] = activeRun;

    setTimeout(() => {
      const target = this.runDetails[runId];
      if (!target) return;
      const baseRows = Math.max(1200, (connector.last_rows_synced ?? 4800) + Math.floor(Math.random() * 450));
      const perStream = Math.floor(baseRows / Math.max(1, connector.streams.length));
      const finishedAt = new Date().toISOString();
      target.status = 'success';
      target.rows_synced = baseRows;
      target.duration_ms = 1480 + Math.floor(Math.random() * 620);
      target.finished_at = finishedAt;
      target.streams = connector.streams.map((streamName, idx) => ({
        stream_name: streamName,
        table_name: `${name}__${streamName}`,
        rows_synced:
          idx === connector.streams.length - 1
            ? baseRows - perStream * (connector.streams.length - 1)
            : perStream,
        rows_dead_lettered: 0,
        pages_fetched: Math.max(2, Math.ceil(perStream / 500)),
        retries: 0,
        drift_events_count: 0,
        duration_ms: Math.floor(target.duration_ms / connector.streams.length),
        bookmark: `${streamName.slice(0, 2)}_${baseRows}`,
      }));

      connector.last_run_status = 'success';
      connector.last_run_at = finishedAt;
      connector.last_rows_synced = baseRows;
    }, 2000);

    return { run_id: runId };
  }

  getRuns(connector?: string, limit = 50): RunSummary[] {
    return Object.values(this.runDetails)
      .filter((r) => !connector || r.connector === connector)
      .sort((a, b) => b.started_at.localeCompare(a.started_at))
      .slice(0, limit)
      .map((r) => ({
        run_id: r.run_id,
        connector: r.connector,
        status: r.status,
        rows_synced: r.rows_synced,
        rows_dead_lettered: r.rows_dead_lettered,
        retries: r.retries,
        drift_events: r.drift_events,
        duration_ms: r.duration_ms,
        started_at: r.started_at,
      }));
  }

  getRunDetail(runId: string): RunDetail {
    const found = this.runDetails[runId];
    if (!found) {
      throw new Error(`Run "${runId}" not found`);
    }
    return structuredClone(found);
  }

  getRunDeadLetters(runId: string): DeadLetterRecord[] {
    return structuredClone(this.deadLetters[runId] ?? []);
  }

  getDriftEvents(): DriftEvent[] {
    return structuredClone(this.driftEvents);
  }

  getRepairs(): RepairProposal[] {
    return structuredClone(this.repairs);
  }

  approveRepair(id: string): RepairProposal {
    const proposal = this.repairs.find((p) => p.id === id);
    if (!proposal) {
      throw new Error(`Repair "${id}" not found`);
    }
    if (proposal.status !== 'proposed') {
      throw new Error(`Repair "${id}" cannot be approved from status "${proposal.status}"`);
    }
    proposal.status = 'applied';
    this.audit.unshift({
      id: `aud-${Math.random().toString(16).slice(2, 6)}`,
      timestamp: new Date().toISOString(),
      connector: proposal.connector,
      trigger: `Manual approval via Control Room (${id})`,
      model: 'gemini-4-hydrogen',
      decision: 'applied',
      attempts: proposal.attempts,
      diff: proposal.diff,
    });
    return structuredClone(proposal);
  }

  rejectRepair(id: string): RepairProposal {
    const proposal = this.repairs.find((p) => p.id === id);
    if (!proposal) {
      throw new Error(`Repair "${id}" not found`);
    }
    if (proposal.status !== 'proposed') {
      throw new Error(`Repair "${id}" cannot be rejected from status "${proposal.status}"`);
    }
    proposal.status = 'rejected';
    this.audit.unshift({
      id: `aud-${Math.random().toString(16).slice(2, 6)}`,
      timestamp: new Date().toISOString(),
      connector: proposal.connector,
      trigger: `Rejected by operator via Control Room (${id})`,
      model: 'gemini-4-hydrogen',
      decision: 'rejected',
      attempts: proposal.attempts,
      diff: proposal.diff,
    });
    return structuredClone(proposal);
  }

  getAudit(limit = 100): AuditLogEntry[] {
    return structuredClone(this.audit.slice(0, limit));
  }

  getBenchmarks(): BenchmarksData {
    return structuredClone(this.benchmarks);
  }
}

export const mockStore = new MockStore();
