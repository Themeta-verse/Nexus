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
  manus_runtime_required: boolean;
};

export type MemoryItem = { memory_id: string; mission_id?: string; source: string; confidence: string; freshness_at: string; reality_state: string; status: string; content: Record<string, unknown> };
export type Outcome = { outcome_id: string; mission_id: string; state: string; reality_state: string; verification_state: string; updated_at: string; summary: Record<string, unknown> };
export type AuditEvent = { audit_id: number; action: string; outcome: string; mission_id?: string; created_at: string; detail: Record<string, unknown> };
export type ProviderState = { status: string; availability?: boolean; limitations?: string[]; authentication?: string };

const configuredBase = import.meta.env.VITE_NEXUS_API_BASE_URL?.replace(/\/$/, "");
export const apiBase = configuredBase || "http://127.0.0.1:8787";
const sessionStorageKey = "nexus.product.session.v1";

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

function persistProductSession(session: ProductSession): ProductSession {
  window.sessionStorage.setItem(sessionStorageKey, JSON.stringify(session));
  return session;
}

async function request<T>(path: string, init?: RequestInit, authenticated = false): Promise<T> {
  const session = authenticated ? readProductSession() : null;
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string> || {}) };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  const response = await fetch(`${apiBase}${path}`, { ...init, headers });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && authenticated) clearProductSession();
  if (!response.ok) throw new Error(payload.detail || `API request failed (${response.status})`);
  return payload as T;
}

export const nexusApi = {
  health: () => request<ProductHealth>("/health"),
  authenticatedHealth: () => request<ProductHealth>("/api/v1/health", undefined, true),
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
  listMissions: (projectId: string) => request<{ project_id: string; missions: MissionSummary[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/missions`, undefined, true),
  listMemory: (projectId: string) => request<{ project_id: string; memory: MemoryItem[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/memory`, undefined, true),
  listOutcomes: (projectId: string) => request<{ project_id: string; outcomes: Outcome[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/outcomes`, undefined, true),
  listAuditEvents: (projectId?: string) => request<{ audit_events: AuditEvent[] }>(`/api/v1/audit-events${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`, undefined, true),
  listCapabilities: () => request<{ capabilities: Array<{ capability: string; provider: string; risk: string; side_effects: boolean }> }>("/api/v1/capabilities", undefined, true),
  listProviders: () => request<{ providers: Record<string, ProviderState> }>("/api/v1/providers", undefined, true),
  listCheckpoints: (missionId: string) => request<{ mission_id: string; checkpoints: Array<{ checkpoint_id: string; state: string; created_at: string }> }>(`/api/v1/missions/${encodeURIComponent(missionId)}/checkpoints`, undefined, true),
  controlMission: (missionId: string, control: "pause" | "resume" | "cancel") => request<MissionSummary>(`/api/v1/missions/${encodeURIComponent(missionId)}/control/${control}`, { method: "POST" }, true),
  submitMission: (payload: { intent: string; project_id: string; scope: string; mode: "REAL_READ" | "SIMULATION"; capabilities: Capability[] }) =>
    request<MissionSummary>("/api/v1/missions", { method: "POST", body: JSON.stringify(payload) }, true),
};
