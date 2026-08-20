/**
 * Meridian Operations Desk — evidence-first, technical editorial control room.
 * The secure extension keeps the original philosophy: identity before access,
 * tenant scope before data, and observed queue state before execution claims.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  CircleAlert,
  Clock3,
  Database,
  ExternalLink,
  FileSearch,
  Globe2,
  KeyRound,
  LoaderCircle,
  LogOut,
  Radar,
  ShieldCheck,
  TerminalSquare,
  UserRound,
} from "lucide-react";
import { apiBase, AuditEvent, Capability, clearProductSession, MemoryItem, MissionSummary, nexusApi, Outcome, ProductHealth, ProductSession, ProviderState, readProductSession } from "@/lib/nexusApi";

const capabilities: Array<{ id: Capability; label: string; detail: string; icon: typeof Globe2 }> = [
  { id: "repository.metadata.read", label: "Metadata", detail: "one bounded repository identity read", icon: Radar },
  { id: "repository.read", label: "Repository health", detail: "full read-only health evidence", icon: FileSearch },
  { id: "browser.read", label: "Browser context", detail: "read-only Chromium CDP evidence", icon: Globe2 },
  { id: "filesystem.read", label: "Local evidence", detail: "bounded source filesystem read", icon: Database },
];

const defaultIntent = "Quick check repository identity";
const terminalStatuses = new Set(["COMPLETED", "PARTIAL", "FAILED", "BLOCKED"]);

function StatusMark({ state }: { state: string }) {
  const normalized = state.toLowerCase();
  const tone = normalized.includes("verified") || normalized.includes("healthy") || normalized.includes("completed")
    ? "is-good"
    : normalized.includes("failed") || normalized.includes("blocked") || normalized.includes("unavailable")
      ? "is-blocked"
      : "is-attention";
  return <span className={`status-mark ${tone}`} aria-hidden="true" />;
}

function EvidenceStamp({ state, label }: { state: string; label: string }) {
  const locked = state.toLowerCase().includes("locked") || state.toLowerCase().includes("auth");
  return <span className={`evidence-stamp ${locked ? "is-locked" : ""}`}><span className="aperture-mini" aria-hidden="true"><i /><b /></span><StatusMark state={state} />{label}</span>;
}

function CapabilityRow({ item, checked, onToggle, available, disabled }: { item: typeof capabilities[number]; checked: boolean; onToggle: () => void; available?: boolean; disabled?: boolean }) {
  const Icon = item.icon;
  return (
    <label className={`capability-row ${checked ? "is-selected" : ""} ${disabled ? "is-disabled" : ""}`}>
      <input type="checkbox" checked={checked} onChange={onToggle} disabled={disabled} />
      <span className="capability-icon"><Icon size={15} strokeWidth={1.8} /></span>
      <span className="capability-copy"><strong>{item.label}</strong><small>{item.detail}</small></span>
      <span className="capability-state"><StatusMark state={available ? "AVAILABLE" : "UNKNOWN"} />{available ? "ready" : "unproven"}</span>
    </label>
  );
}

function MissionLine({ mission }: { mission: MissionSummary }) {
  const queueState = mission.queue?.status || mission.status;
  return (
    <article className="mission-line">
      <div className="mission-line-top"><span className="mono-label">{mission.mission_id.slice(0, 18)}</span><span className="state-chip"><StatusMark state={mission.status} />{mission.status}</span></div>
      <p>{queueState} · {mission.reality} · {mission.verification_status}</p>
      {!terminalStatuses.has(mission.status) && <div className="execution-meter"><span /></div>}
    </article>
  );
}

function LoginPanel({ onAuthenticated }: { onAuthenticated: (session: ProductSession) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const signIn = async () => {
    setSubmitting(true); setError(null);
    try { onAuthenticated(await nexusApi.login(email, password)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Authentication could not be completed."); }
    finally { setSubmitting(false); }
  };
  return (
    <section className="login-panel" aria-labelledby="login-title">
      <div className="login-copy"><span className="mono-label">PRODUCT SECURITY / REQUIRED</span><h2 id="login-title">Authenticate before the ledger becomes visible.</h2><p>The command center reads tenant-scoped projects and mission receipts only after product-owned session authentication.</p></div>
      <div className="login-form">
        <label>Email<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" placeholder="owner@example.com" /></label>
        <label>Password<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" placeholder="Your product password" onKeyDown={(event) => event.key === "Enter" && void signIn()} /></label>
        {error && <p className="login-error"><CircleAlert size={15} />{error}</p>}
        <button className="run-button" disabled={submitting || !email || !password} onClick={() => void signIn()}>{submitting ? <LoaderCircle className="spin" size={17} /> : <KeyRound size={17} />}{submitting ? "Verifying identity" : "Open secure workspace"}</button>
      </div>
    </section>
  );
}

export default function Home() {
  const [health, setHealth] = useState<ProductHealth | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [session, setSession] = useState<ProductSession | null>(() => readProductSession());
  const [activeProject, setActiveProject] = useState("");
  const [intent, setIntent] = useState(defaultIntent);
  const [mode, setMode] = useState<"REAL_READ" | "SIMULATION">("SIMULATION");
  const [selected, setSelected] = useState<Capability[]>(["repository.metadata.read"]);
  const [submitting, setSubmitting] = useState(false);
  const [mission, setMission] = useState<MissionSummary | null>(null);
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [capabilityStates, setCapabilityStates] = useState<Array<{ capability: string; provider: string; risk: string; side_effects: boolean }>>([]);
  const [providerStates, setProviderStates] = useState<Record<string, ProviderState>>({});
  const [checkpointCount, setCheckpointCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const providerAvailability = useMemo(() => health?.providers || {}, [health]);
  const selectedCount = selected.length;
  const latestMission = mission ?? missions[0] ?? null;
  const watchingExecution = Boolean(latestMission && !terminalStatuses.has(latestMission.status));

  const refresh = useCallback(async () => {
    try {
      const nextHealth = await (session ? nexusApi.authenticatedHealth() : nexusApi.health());
      setHealth(nextHealth); setHealthError(null);
      if (session && activeProject) {
        const [nextMissions, nextMemory, nextOutcomes, nextAudit, nextCapabilities, nextProviders] = await Promise.all([
          nexusApi.listMissions(activeProject), nexusApi.listMemory(activeProject), nexusApi.listOutcomes(activeProject),
          nexusApi.listAuditEvents(activeProject), nexusApi.listCapabilities(), nexusApi.listProviders(),
        ]);
        setMissions(nextMissions.missions);
        setMemory(nextMemory.memory); setOutcomes(nextOutcomes.outcomes); setAuditEvents(nextAudit.audit_events);
        setCapabilityStates(nextCapabilities.capabilities); setProviderStates(nextProviders.providers);
        setMission((current) => current ? nextMissions.missions.find((item) => item.mission_id === current.mission_id) ?? current : null);
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Independent API unavailable";
      setHealthError(message);
      if (message.includes("401")) { clearProductSession(); setSession(null); setMissions([]); setMission(null); }
    }
  }, [activeProject, session]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (session && !activeProject && session.projects[0]) setActiveProject(session.projects[0].project_id);
  }, [activeProject, session]);
  useEffect(() => {
    if (!session || !activeProject) return;
    const interval = window.setInterval(() => { void refresh(); }, watchingExecution ? 2000 : 8000);
    return () => window.clearInterval(interval);
  }, [activeProject, refresh, session, watchingExecution]);

  const toggleCapability = (capability: Capability) => setSelected((current) => current.includes(capability) ? current.filter((item) => item !== capability) : [...current, capability]);
  const onAuthenticated = (nextSession: ProductSession) => { setSession(nextSession); setActiveProject(nextSession.projects[0]?.project_id || ""); setError(null); };
  const logout = async () => { await nexusApi.logout(); setSession(null); setMissions([]); setMission(null); setActiveProject(""); };

  const queueMission = async () => {
    if (!session) { setError("Authenticate before creating a governed mission."); return; }
    if (!activeProject) { setError("Select a tenant project before creating a mission."); return; }
    if (!intent.trim() || selected.length === 0) { setError("Name an outcome and select at least one evidence capability."); return; }
    setSubmitting(true); setError(null);
    try {
      const created = await nexusApi.submitMission({ intent: intent.trim(), project_id: activeProject, scope: "Themeta-verse/Nexus", mode, capabilities: selected });
      setMission(created); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Mission could not be queued."); }
    finally { setSubmitting(false); }
  };
  const controlLatestMission = async (control: "pause" | "resume" | "cancel") => {
    if (!latestMission) return;
    setError(null);
    try { setMission(await nexusApi.controlMission(latestMission.mission_id, control)); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Mission control could not be applied."); }
  };
  useEffect(() => {
    if (!session || !latestMission || terminalStatuses.has(latestMission.status)) { setCheckpointCount(0); return; }
    void nexusApi.listCheckpoints(latestMission.mission_id).then((payload) => setCheckpointCount(payload.checkpoints.length)).catch(() => setCheckpointCount(0));
  }, [latestMission?.mission_id, latestMission?.status, session]);

  return (
    <main className="nexus-shell">
      <aside className="identity-rail" aria-label="NEXUS product identity and runtime state">
        <div className="rail-top"><img className="nexus-logo" src="/assets/nexus-aperture-logo.png" alt="NEXUS aperture symbol" /><div className="wordmark">NEXUS<span>IND</span></div></div>
        <div className="rail-coordinate">PRODUCT / 02</div>
        <nav className="rail-nav" aria-label="Command center sections"><a className="rail-link is-active" href="#mission"><TerminalSquare size={16} />Mission desk</a><a className="rail-link" href="#evidence"><ShieldCheck size={16} />Evidence</a><a className="rail-link" href="#history"><Clock3 size={16} />Continuity</a></nav>
        <div className="rail-status"><span className="mono-label">RUNTIME</span><strong>{health?.status || "OFFLINE"}</strong><small>{session ? "Authenticated tenant scope" : "Identity required for tenant data"}</small></div>
        <div className="rail-footer">MERIDIAN / {new Date().getFullYear()}</div>
      </aside>

      <section className="main-canvas">
        <header className="topline">
          <div><span className="mono-label">INDEPENDENT COMMAND CENTER</span><p>Evidence before action. Identity before access.</p></div>
          <div className="topline-actions">
            {session ? <span className="identity-chip"><UserRound size={14} />{session.user.email}</span> : <span className="identity-chip is-locked"><KeyRound size={14} />LOCKED</span>}
            {session && <button className="quiet-control" onClick={() => void logout()}><LogOut size={15} />Sign out</button>}
            <button className="quiet-control" onClick={() => void refresh()}><Activity size={15} />Refresh state</button>
            <a className="api-link" href={`${apiBase}/docs`} target="_blank" rel="noreferrer">API <ExternalLink size={14} /></a>
          </div>
        </header>

        <section className="hero-panel" id="mission">
          <img src="/assets/nexus-meridian-hero.jpg" alt="Abstract technical survey background" /><div className="hero-veil" />
          <div className="hero-content"><div className="hero-locator"><EvidenceStamp state="READ ONLY" label="canonical path" /><span>CONTROL ARCHIVE / 02.1</span></div><h1>Queue evidence.<br />Verify the result.</h1><p>The product runtime secures tenant access, persists the queue, and reports worker execution from durable state.</p><div className="hero-facts"><span><StatusMark state={health?.status || "UNKNOWN"} />{health?.status || "API unavailable"}</span><span>{health?.database?.queue?.queued ?? 0} queued / {health?.database?.queue?.leased ?? 0} executing</span><span>{health?.github?.transport || "provider pending"}</span></div><div className="hero-provenance"><span>OBSERVATION SOURCE / SQLITE + CHECKPOINTS</span><span>EXTERNAL ACTION / NONE</span></div></div>
        </section>

        {!session ? <LoginPanel onAuthenticated={onAuthenticated} /> : <section className="mission-composer" aria-labelledby="mission-title">
          <div className="section-heading"><div><div className="heading-line"><EvidenceStamp state="OBSERVED" label="tenant scope enforced" /><span className="mono-label">01 / MISSION COMPILER</span></div><h2 id="mission-title">State an outcome, then queue verified evidence.</h2></div><span className="selection-count">{selectedCount.toString().padStart(2, "0")} capabilities selected</span></div>
          <div className="tenant-toolbar"><span className="mono-label">TENANT SCOPE</span><select value={activeProject} onChange={(event) => setActiveProject(event.target.value)} aria-label="Active tenant project">{session.projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.display_name} · {project.role}</option>)}</select><span className="live-indicator"><StatusMark state={watchingExecution ? "EXECUTING" : "HEALTHY"} />{watchingExecution ? "live worker watch / 2s" : "durable status sync / 8s"}</span></div>
          <div className="composer-grid"><div className="intent-column"><label htmlFor="mission-intent">Mission objective</label><textarea id="mission-intent" value={intent} onChange={(event) => setIntent(event.target.value)} spellCheck={false} /><div className="mode-switch" role="group" aria-label="Mission mode"><button className={mode === "SIMULATION" ? "is-active" : ""} onClick={() => setMode("SIMULATION")}>Simulation</button><button className={mode === "REAL_READ" ? "is-active" : ""} onClick={() => setMode("REAL_READ")}>Real read</button></div><p className="helper-text">Submission creates a durable queue record. A separate read-only worker claims, executes, verifies, and persists the mission.</p></div>
            <div className="capabilities-column" id="evidence"><span className="column-kicker">EVIDENCE SET</span>{capabilities.map((item) => <CapabilityRow key={item.id} item={item} checked={selected.includes(item.id)} onToggle={() => toggleCapability(item.id)} available={item.id.startsWith("repository") ? Boolean(health) : Boolean(providerAvailability[item.id.replace(".read", "-read")])} />)}</div></div>
          {error && <div className="error-line"><CircleAlert size={16} />{error}</div>}
          <div className="composer-footer"><div><span className="mono-label">AUTHORIZATION</span><strong>READ-ONLY / TENANT-SCOPED / NO SIDE EFFECTS</strong></div><button className="run-button" disabled={submitting} onClick={() => void queueMission()}>{submitting ? <LoaderCircle className="spin" size={17} /> : <ArrowUpRight size={17} />}{submitting ? "Queueing mission" : "Queue governed mission"}</button></div>
        </section>}

        <section className="result-strip" aria-live="polite"><div className="result-object"><img src="/assets/nexus-verification-object.png" alt="NEXUS verification aperture seal" /><span className="seal-coordinate">V / 01</span></div><div><div className="heading-line result-heading"><EvidenceStamp state={latestMission?.status || (session ? "PREPARED" : "LOCKED")} label="latest mission" /><span className="mono-label">LIVE STATE</span></div><h2>{latestMission ? latestMission.status : session ? "No mission in this project." : "Workspace is locked."}</h2><p>{latestMission ? `${latestMission.queue?.status || latestMission.status} · ${latestMission.reality} · ${latestMission.verification_status} · ${latestMission.external_invocations} external calls` : session ? "Queue a simulation to validate the secured API, worker, and continuity loop." : "Sign in to reveal only your tenant-scoped durable history."}</p></div><div className="result-boundary"><EvidenceStamp state={latestMission?.action_state || "PENDING"} label="action state" /><strong>{latestMission?.action_state || "PENDING"}</strong><small>{watchingExecution ? "Worker execution is being observed." : "Prepared is not authorized."}</small>{session && latestMission && latestMission.status !== "EXECUTING" && !terminalStatuses.has(latestMission.status) && <span className="mission-controls"><button onClick={() => void controlLatestMission(latestMission.status === "PAUSED" ? "resume" : "pause")}>{latestMission.status === "PAUSED" ? "resume" : "pause"}</button><button onClick={() => void controlLatestMission("cancel")}>cancel</button></span>}</div></section>

        {session && <section className="reality-grid" aria-label="Persisted project intelligence">
          <article><div className="heading-line"><EvidenceStamp state="OBSERVED" label="memory" /><span className="mono-label">PROJECT-SCOPED</span></div><strong>{memory.length}</strong><p>{memory[0] ? `${memory[0].source} · ${memory[0].confidence} · ${memory[0].reality_state}` : "No persisted observation memory yet."}</p></article>
          <article><div className="heading-line"><EvidenceStamp state="VERIFIED" label="outcomes" /><span className="mono-label">MISSION PROJECTIONS</span></div><strong>{outcomes.length}</strong><p>{outcomes[0] ? `${outcomes[0].state} · ${outcomes[0].verification_state}` : "No persisted outcome yet."}</p></article>
          <article><div className="heading-line"><EvidenceStamp state="OBSERVED" label="audit" /><span className="mono-label">TENANT-SCOPED</span></div><strong>{auditEvents.length}</strong><p>{auditEvents[0] ? `${auditEvents[0].action} · ${auditEvents[0].outcome}` : "No product audit event yet."}</p></article>
          <article><div className="heading-line"><EvidenceStamp state="READ ONLY" label="fabric" /><span className="mono-label">CAPABILITIES / PROVIDERS</span></div><strong>{capabilityStates.length} / {Object.keys(providerStates).length}</strong><p>{capabilityStates[0] ? `${capabilityStates[0].capability} · ${capabilityStates[0].risk}` : "Provider fabric not available."} {checkpointCount ? `· ${checkpointCount} checkpoint(s)` : ""}</p></article>
        </section>}
      </section>

      <aside className="inspection-pane" id="history" aria-label="Evidence and continuity inspection"><div className="inspection-header"><div className="heading-line"><EvidenceStamp state={session ? "OBSERVED" : "AUTH REQUIRED"} label="ledger access" /><span className="mono-label">03 / INSPECTION</span></div><h2>Reality ledger</h2></div><div className="runtime-fact"><span>Database</span><strong>{health?.database?.status || "UNAVAILABLE"}</strong><small>{session ? `${missions.length} visible project records` : "Tenant records hidden until sign-in"}</small></div><div className="runtime-fact"><span>Boundary</span><strong>{session ? "TENANT + READ ONLY" : "AUTH REQUIRED"}</strong><small>Writes remain absent, not hidden.</small></div><div className="inspection-visual"><img src="/assets/nexus-evidence-pattern.jpg" alt="Inspection crop from the NEXUS evidence ledger" /><span className="artifact-tag">INSPECTION CROP / PROVENANCE LEDGER</span><span className="artifact-coordinate">SOURCE REF / 03-14</span></div><div className="history-head"><span className="mono-label">PERSISTED MISSIONS</span><span>{session ? missions.length : "—"}</span></div><div className="mission-history">{session && missions.length ? missions.slice(0, 5).map((item) => <MissionLine key={item.mission_id} mission={item} />) : <p className="empty-ledger">{session ? (healthError ? "API not connected. Start the independent runtime to read continuity." : "No durable mission records for this project yet.") : "Authenticate to view only the mission history allowed by your project membership."}</p>}</div><div className="api-note"><Radar size={15} /><span>API target<br /><code>{apiBase}</code></span></div></aside>
    </main>
  );
}
