const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    throw new Error(message || res.statusText);
  }
  return body as T;
}

export type ProbeResult = {
  account: string | null;
  username: string | null;
  role: string | null;
  warehouse: string | null;
  databases: string[];
};

export type Connector = {
  id: string;
  name: string;
  project_id: string;
  type: string;
  config: Record<string, unknown>;
  comment: string | null;
  created_at: string;
  updated_at: string;
  checks_count: number;
};

export type RcaResult = {
  id: string;
  summary: string;
  root_cause: string | null;
  confidence: number | null;
  evidence: Record<string, unknown> | null;
  next_steps: string | null;
  suggested_owner: string | null;
  created_at: string;
};

export type CheckRun = {
  id: string;
  status: "RUNNING" | "PASSED" | "FAILED" | "ERROR";
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  metrics: Record<string, unknown> | null;
  message: string | null;
  rca: RcaResult | null;
};

export type ProjectHealth = {
  total_checks: number;
  passing: number;
  failing: number;
  erroring: number;
  never_run: number;
  disabled: number;
  open_incidents: number;
  last_run_at: string | null;
  status: "PASSED" | "FAILED" | "ERROR" | "NONE";
};

export type Database = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  project_id: string;
  connector_id: string;
  connector: { id: string; name: string };
  created_at: string;
  updated_at: string;
  health: ProjectHealth;
};

/** One statement a check issues, and what it establishes.
 *
 * Built by the backend from the check's config on every read, so it is the SQL
 * the engine would actually run rather than a description of it. */
export type CheckStatement = {
  label: string;
  sql: string;
  connection: "primary" | "secondary";
};

export type ProposedCheck = {
  key: string;
  name: string;
  description: string;
  rationale: string;
  type: string;
  schedule: string;
  database: string;
  config: Record<string, unknown>;
  source: "heuristic" | "llm";
  concerns: string[];
  statements: CheckStatement[];
  statements_error: string | null;
};

export type ProposedDatabase = {
  name: string;
  description: string;
  repo_paths: Record<string, string>;
  tables: string[];
};

export type TableCoverage = {
  table: string;
  parity: boolean;
  freshness: boolean;
  schema_drift: boolean;
  gaps: string[];
};

export type Coverage = {
  tables_total: number;
  tables_expecting_parity: number;
  tables_with_parity: number;
  uncovered: TableCoverage[];
  summary: string;
};

export type RepoAnalysis = {
  repo_url: string;
  repo_ref: string | null;
  repo_commit: string | null;
  repo_commit_subject: string | null;
  project_name: string;
  project_description: string;
  databases: ProposedDatabase[];
  checks: ProposedCheck[];
  coverage: Coverage;
  sql_files: string[];
  table_count: number;
  llm_error: string | null;
  warnings: string[];
};

export type IncidentEvent = {
  id: string;
  kind: "OPENED" | "COMMENT" | "ESCALATED" | "REOPENED" | "CLEARED" | "SUPPRESSED";
  body: string;
  author: string;
  check_run_id: string | null;
  created_at: string;
};

export type Incident = {
  id: string;
  check_id: string;
  check_name: string;
  check_type: string;
  database_name: string | null;
  state: "OPEN" | "WARNING" | "CLEARED" | "SUPPRESSED";
  severity: string;
  title: string;
  summary: string | null;
  opened_at: string;
  last_seen_at: string;
  cleared_at: string | null;
  failure_count: number;
  escalation_count: number;
  correlation_id: string | null;
  triage_source: string;
  // The Jira issue, as of the last sweep's refresh. Null means none exists:
  // Jira is not configured, or filing it failed.
  ticket_key: string | null;
  ticket_url: string | null;
  ticket_status: string | null;
  ticket_assignee: string | null;
  ticket_synced_at: string | null;
  event_count: number;
};

export type IncidentDetail = Incident & { events: IncidentEvent[] };

export type CheckRevision = {
  id: string;
  check_id: string | null;
  check_name: string | null;
  database_name: string;
  kind: "CREATE" | "UPDATE" | "RETIRE";
  status: "PENDING" | "APPLIED" | "REJECTED" | "AUTO_APPLIED";
  current_config: Record<string, unknown> | null;
  proposed_name: string | null;
  proposed_type: string | null;
  proposed_schedule: string | null;
  proposed_config: Record<string, unknown> | null;
  reason: string;
  confidence: number | null;
  triggered_by_commit: string | null;
  created_at: string;
  reviewed_at: string | null;
};

export type MonitorStatus = {
  agent_enabled: boolean;
  agent_model: string | null;
  ticket_backend: string | null;
  monitor_interval_seconds: number;
  maintenance_interval_seconds: number;
  incident_counts: Record<string, number>;
  pending_revisions: number;
};

export type SweepResult = {
  synced: number;
  reopened: number;
  escalated: number;
  agent_actions: number;
  agent_error: string | null;
};

export type MaintenanceResult = {
  project: string;
  changed: boolean;
  revisions: number;
  auto_applied: number;
  undocumented_ddl: number;
  errors: string[];
  agent_error: string | null;
};

export type GitHubRepository = {
  full_name: string;
  clone_url: string;
  private: boolean;
  default_branch: string;
  description: string | null;
  pushed_at: string | null;
  installation_id: number;
  account: string;
};

export type GitHubStatus = {
  configured: boolean;
  install_url: string | null;
  installations: { id: number; account: string; repository_selection: string }[];
  error: string | null;
};

export type IngestJob = {
  id: string;
  status: "RUNNING" | "DONE" | "ERROR";
  stage: string;
  repo_url: string;
  error: string | null;
  analysis: RepoAnalysis | null;
};

export type Project = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  health: ProjectHealth;
  databases: Database[];
};

export type Check = {
  id: string;
  name: string;
  description: string | null;
  rationale: string | null;
  statements: CheckStatement[];
  statements_error: string | null;
  type: string;
  schedule: string;
  enabled: boolean;
  database_id: string;
  database: {
    id: string;
    slug: string;
    name: string;
    project: { id: string; slug: string; name: string };
  };
  connector_id: string;
  secondary_connector_id: string | null;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  connector: { id: string; name: string };
  secondary_connector: { id: string; name: string } | null;
  runs: CheckRun[];
};

export const api = {
  listProjects: () => request<Project[]>("/api/projects"),
  getProject: (key: string) => request<Project>(`/api/projects/${key}`),
  createProject: (payload: Record<string, unknown>) =>
    request<Project>("/api/projects", { method: "POST", body: JSON.stringify(payload) }),
  updateProject: (key: string, payload: Record<string, unknown>) =>
    request<Project>(`/api/projects/${key}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteProject: (key: string) =>
    request<void>(`/api/projects/${key}`, { method: "DELETE" }),

  listDatabases: (projectKey: string) => request<Database[]>(`/api/projects/${projectKey}/databases`),
  getDatabase: (projectKey: string, dbKey: string) =>
    request<Database>(`/api/projects/${projectKey}/databases/${dbKey}`),
  createDatabase: (projectKey: string, payload: Record<string, unknown>) =>
    request<Database>(`/api/projects/${projectKey}/databases`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateDatabase: (projectKey: string, dbKey: string, payload: Record<string, unknown>) =>
    request<Database>(`/api/projects/${projectKey}/databases/${dbKey}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteDatabase: (projectKey: string, dbKey: string) =>
    request<void>(`/api/projects/${projectKey}/databases/${dbKey}`, { method: "DELETE" }),
  listDatabaseChecks: (projectKey: string, dbKey: string) =>
    request<Check[]>(`/api/projects/${projectKey}/databases/${dbKey}/checks`),

  discoverDatabases: (connectorId: string) =>
    request<string[]>(`/api/connectors/${connectorId}/databases`),

  listChecks: () => request<Check[]>("/api/checks"),
  getCheck: (id: string) => request<Check>(`/api/checks/${id}`),
  createCheck: (payload: Record<string, unknown>) =>
    request<Check>("/api/checks", { method: "POST", body: JSON.stringify(payload) }),
  updateCheck: (id: string, payload: Record<string, unknown>) =>
    request<Check>(`/api/checks/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteCheck: (id: string) => request<{ ok: true }>(`/api/checks/${id}`, { method: "DELETE" }),
  runCheck: (id: string) => request<{ run_id: string }>(`/api/checks/${id}/run`, { method: "POST" }),

  getGitHubStatus: () => request<GitHubStatus>("/api/github/status"),
  listGitHubRepositories: () => request<GitHubRepository[]>("/api/github/repositories"),

  startIngestion: (payload: {
    repo_url: string;
    token?: string;
    ref?: string;
    installation_id?: number;
  }) =>
    request<IngestJob>("/api/ingest", { method: "POST", body: JSON.stringify(payload) }),
  getIngestion: (jobId: string) => request<IngestJob>(`/api/ingest/${jobId}`),
  discardIngestion: (jobId: string) => request<void>(`/api/ingest/${jobId}`, { method: "DELETE" }),
  createProjectFromIngestion: (jobId: string, payload: Record<string, unknown>) =>
    request<Project>(`/api/ingest/${jobId}/project`, { method: "POST", body: JSON.stringify(payload) }),

  probeConnection: (payload: Record<string, unknown>) =>
    request<ProbeResult>("/api/connectors/probe", { method: "POST", body: JSON.stringify(payload) }),
  setupProject: (payload: Record<string, unknown>) =>
    request<Project>("/api/projects/setup", { method: "POST", body: JSON.stringify(payload) }),
  listProjectConnectors: (key: string) => request<Connector[]>(`/api/projects/${key}/connectors`),

  listConnectors: (projectId?: string) =>
    request<Connector[]>(`/api/connectors${projectId ? `?project_id=${projectId}` : ""}`),
  createConnector: (payload: Record<string, unknown>) =>
    request<Connector>("/api/connectors", { method: "POST", body: JSON.stringify(payload) }),
  deleteConnector: (id: string) => request<{ ok: true }>(`/api/connectors/${id}`, { method: "DELETE" }),

  getMonitorStatus: () => request<MonitorStatus>("/api/monitor/status"),
  runSweep: () => request<SweepResult>("/api/monitor/sweep", { method: "POST" }),
  listIncidents: (slug: string, state?: string) =>
    request<Incident[]>(`/api/projects/${slug}/incidents${state ? `?state=${state}` : ""}`),
  getIncident: (id: string) => request<IncidentDetail>(`/api/incidents/${id}`),

  listRevisions: (slug: string, status?: string) =>
    request<CheckRevision[]>(`/api/projects/${slug}/revisions${status ? `?status=${status}` : ""}`),
  runMaintenance: (slug: string) =>
    request<MaintenanceResult>(`/api/projects/${slug}/maintenance`, { method: "POST" }),
  applyRevision: (id: string) =>
    request<CheckRevision>(`/api/revisions/${id}/apply`, { method: "POST" }),
  rejectRevision: (id: string) =>
    request<CheckRevision>(`/api/revisions/${id}/reject`, { method: "POST" }),
};
