/* Meridian command-center API boundary: authenticated product sessions, tenant-scoped data, and durable queue status only. */
export type Capability = "repository.metadata.read" | "repository.read" | "browser.read" | "filesystem.read";

export type Project = {
  project_id: string;
  display_name: string;
  role: "owner" | "operator" | "viewer";
  created_at?: string;
  updated_at?: string;
};

export type ProductUser = {
  user_id: string;
  tenant_id: string;
  email: string;
  role: "owner" | "operator" | "viewer";
};

export type ProductSession = {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  user: ProductUser;
  projects: Project[];
};

export type MissionQueue = {
  status: "QUEUED" | "LEASED" | "COMPLETED" | "FAILED" | string | null;
  attempts?: number | null;
  max_attempts?: number | null;
  available_at?: string | null;
  lease_expires_at?: string | null;
  last_error?: string | null;
};

export type MissionSummary = {
  mission_id: string;
  project_id: string;
  status: string;
  reality: string;
  verification_status: string;
  action_state: string;
  external_invocations: number;
  queue?: MissionQueue;
  result?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type ProductHealth = {
  status: string;
  service: string;
  database: { status: string; missions: number; projects: number; users?: number; path?: string; queue?: Record<string, number> };
  providers: Record<string, { status: string; availability?: boolean; limitations?: string[]; authentication?: string }>;
  real_reads_enabled: boolean;
  authorization_boundary: string;
  authentication?: { mode: string; bootstrap_owner_configured: boolean; session_hours: number };
  queue?: { worker_command: string; lease_seconds: number; max_attempts: number };
  github?: { transport: string; authentication: string };
  runtime_state?: { state: string; reason: string; configured_database_url?: string; database_engine?: string; database_portability?: string };
  initial_owner_setup_available?: boolean;
  owner_registration_available?: boolean;
};

export type MemoryItem = { memory_id: string; mission_id?: string; source: string; confidence: string; freshness_at: string; reality_state: string; status: string; user_note?: string | null; retired_at?: string | null; content: Record<string, unknown> };
export type Outcome = { outcome_id: string; mission_id: string; state: string; reality_state: string; verification_state: string; updated_at: string; summary: Record<string, unknown> };
export type AuditEvent = { audit_id: number; action: string; outcome: string; mission_id?: string; created_at: string; detail: Record<string, unknown> };
export type ProviderState = { identity?: string; status: string; availability?: boolean; limitations?: string[]; authentication?: string; authorization?: string; risk?: string; side_effects?: boolean; execution_state?: string; last_execution?: string | null; last_successful_execution?: string | null; last_failure_state?: string | null; last_verification_state?: string };
export type MissionEvent = { event_id: number; event_type: string; payload: Record<string, unknown>; created_at: string };
export type MissionEvidence = { evidence_id: number; capability?: string; provider?: string; observation_id?: string; verification_state?: string; reality?: string; created_at: string };
export type DatabaseInspection = { database: "sqlite"; tenant_id: string; row_counts: Record<string, number>; integrity_check: string; foreign_keys: boolean; journal_mode: string };
export type ProjectContext = { project_id: string; current_objective: string | null; latest_mission: MissionSummary | null; active_missions: MissionSummary[]; blockers: Array<{ mission_id: string; status: string; error?: string | null }>; discovered: Array<{ memory_id: string; source: string; reality_state: string; verification_state: string; status: string; user_note?: string | null }>; outcomes: Outcome[]; next_action: string; continuity: { memory_count: number; mission_count: number; active_count: number; blocker_count: number } };

const configuredBase = import.meta.env.VITE_NEXUS_API_BASE_URL?.replace(/\/$/, "");
const runtimeBaseStorageKey = "nexus.product.api-base.v1";
export const apiBase = configuredBase || "";
const sessionStorageKey = "nexus.product.session.v1";

export function getApiBase(): string {
  if (typeof window !== "undefined") {
    const override = window.localStorage.getItem(runtimeBaseStorageKey)?.trim().replace(/\/$/, "");
    if (override) return override;
  }
  return configuredBase || "";
}

export function configureApiBase(value: string): string {
  const normalized = value.trim().replace(/\/$/, "");
  if (!/^https?:\/\//.test(normalized)) throw new Error("Enter a complete runtime URL beginning with http:// or https://");
  window.localStorage.setItem(runtimeBaseStorageKey, normalized);
  return normalized;
}

export function readProductSession(): ProductSession | null {
  try {
    const raw = window.sessionStorage.getItem(sessionStorageKey);
    return raw ? (JSON.parse(raw) as ProductSession) : null;
  } catch {
    return null;
  }
}

export function clearProductSession(): void {
  window.sessionStorage.removeItem(sessionStorageKey);
}

export function persistProductSession(session: ProductSession): ProductSession {
  window.sessionStorage.setItem(sessionStorageKey, JSON.stringify(session));
  return session;
}

async function request<T>(path: string, init?: RequestInit, authenticated = false): Promise<T> {
  const base = getApiBase();
  if (!base) throw new Error("NEXUS runtime is not connected. Add the URL of your independently running API before signing in.");
  const session = authenticated ? readProductSession() : null;
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string> || {}) };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  const response = await fetch(`${base}${path}`, { ...init, headers });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && authenticated) clearProductSession();
  if (!response.ok) throw new Error(payload.detail || `API request failed (${response.status})`);
  return payload as T;
}

export const nexusApi = {
  health: () => request<ProductHealth>("/health"),
  authenticatedHealth: () => request<ProductHealth>("/api/v1/health", undefined, true),
  setupOwner: async (email: string, password: string) => persistProductSession(await request<ProductSession>("/api/v1/setup/owner", { method: "POST", body: JSON.stringify({ email, password }) })),
  registerOwner: async (email: string, password: string) => persistProductSession(await request<ProductSession>("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ email, password }) })),
  login: async (email: string, password: string) => persistProductSession(await request<ProductSession>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) })),
  logout: async () => {
    try {
      await request<void>("/api/v1/auth/logout", { method: "POST" }, true);
    } finally {
      clearProductSession();
    }
  },
  me: () => request<{ user: ProductUser; projects: Project[] }>("/api/v1/me", undefined, true),
  listProjects: () => request<{ projects: Project[] }>("/api/v1/projects", undefined, true),
  createProject: (projectId: string, displayName: string) => request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ project_id: projectId, display_name: displayName }) }, true),
  listMissions: (projectId: string) => request<{ project_id: string; missions: MissionSummary[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/missions`, undefined, true),
  listMemory: (projectId: string) => request<{ project_id: string; memory: MemoryItem[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/memory`, undefined, true),
  updateMemory: (projectId: string, memoryId: string, action: "retire" | "restore" | "annotate", note?: string) => request<{ memory: MemoryItem }>(`/api/v1/projects/${encodeURIComponent(projectId)}/memory/${encodeURIComponent(memoryId)}`, { method: "POST", body: JSON.stringify({ action, note }) }, true),
  projectContext: (projectId: string) => request<ProjectContext>(`/api/v1/projects/${encodeURIComponent(projectId)}/context`, undefined, true),
  listOutcomes: (projectId: string) => request<{ project_id: string; outcomes: Outcome[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/outcomes`, undefined, true),
  listAuditEvents: (projectId?: string) => request<{ audit_events: AuditEvent[] }>(`/api/v1/audit-events${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`, undefined, true),
  listCapabilities: () => request<{ capabilities: Array<{ capability: string; provider: string; risk: string; side_effects: boolean }> }>("/api/v1/capabilities", undefined, true),
  listProviders: () => request<{ providers: Record<string, ProviderState> }>("/api/v1/providers", undefined, true),
  databaseInspection: () => request<DatabaseInspection>("/api/v1/operator/database", undefined, true),
  diagnostics: () => request<ProductHealth>("/api/v1/diagnostics", undefined, true),
  missionEvents: (missionId: string) => request<{ mission_id: string; events: MissionEvent[] }>(`/api/v1/missions/${encodeURIComponent(missionId)}/events`, undefined, true),
  missionEvidence: (missionId: string) => request<{ mission_id: string; evidence: MissionEvidence[] }>(`/api/v1/missions/${encodeURIComponent(missionId)}/evidence`, undefined, true),
  listCheckpoints: (missionId: string) => request<{ mission_id: string; checkpoints: Array<{ checkpoint_id: string; state: string; created_at: string }> }>(`/api/v1/missions/${encodeURIComponent(missionId)}/checkpoints`, undefined, true),
  controlMission: (missionId: string, control: "pause" | "resume" | "cancel") => request<MissionSummary>(`/api/v1/missions/${encodeURIComponent(missionId)}/control/${control}`, { method: "POST" }, true),
  continueMission: (missionId: string) => request<{ mission: MissionSummary; recovery: Record<string, unknown> }>(`/api/v1/missions/${encodeURIComponent(missionId)}/continue`, { method: "POST" }, true),
  submitMission: (payload: { intent: string; project_id: string; scope: string; mode: "REAL_READ" | "SIMULATION"; capabilities: Capability[] }) =>
    request<MissionSummary>("/api/v1/missions", { method: "POST", body: JSON.stringify(payload) }, true),
};
