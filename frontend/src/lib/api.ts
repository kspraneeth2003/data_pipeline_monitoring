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

export type Ticket = {
  id: string;
  key: string;
  title: string;
  description: string;
  priority: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  assignee: string | null;
  status: "TODO" | "IN_PROGRESS" | "DONE";
  created_at: string;
  updated_at: string;
};

export type TicketWithContext = Ticket & {
  check_run_id: string;
  check_id: string;
  check_name: string;
  database_slug: string;
  database_name: string;
  project_slug: string;
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
  ticket: Ticket | null;
};

export type ProjectHealth = {
  total_checks: number;
  passing: number;
  failing: number;
  erroring: number;
  never_run: number;
  disabled: number;
  open_tickets: number;
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
  listProjectTickets: (key: string) => request<TicketWithContext[]>(`/api/projects/${key}/tickets`),

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

  listTickets: () => request<TicketWithContext[]>("/api/tickets"),
  updateTicket: (id: string, payload: Record<string, unknown>) =>
    request<Ticket>(`/api/tickets/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
};
