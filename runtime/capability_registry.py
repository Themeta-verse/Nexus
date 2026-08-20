#!/usr/bin/env python3
"""Evidence-backed capability registry for NEXUS Ω.

Discovery is read-only. This module reads local configuration and skill metadata;
it does not enable connectors, invoke connector tools, grant permissions, or
perform external writes. External content remains untrusted data.
"""
from __future__ import annotations
import json, os, re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
try:
    from canonical_core import governance_for, core_id, utc_now, Capability
except ImportError:
    from .canonical_core import governance_for, core_id, utc_now, Capability

HEALTH={"AVAILABLE","DEGRADED","UNAVAILABLE","UNAUTHORIZED","UNKNOWN"}
CATEGORIES={"COMMUNICATION","KNOWLEDGE","ENGINEERING","RESEARCH","CREATION","AUTOMATION","BUSINESS","LOCAL","SKILL","API","MCP"}
OPERATIONS={"DISCOVER","READ","WRITE","EXECUTE","VERIFY","SEARCH","DRAFT","CREATE","MODIFY","PUBLISH","DELETE"}

@dataclass
class CapabilityRecord:
    id:str
    name:str
    category:str
    provider:str
    operations:list[str]
    authorization:str
    risk:str
    input_schema:str
    output_schema:str
    verification_method:str
    dependencies:list[str]
    failure_modes:list[str]
    availability:str
    scope:str
    source:str
    observed_at:str=field(default_factory=utc_now)
    confidence:str="bounded"
    limitations:list[str]=field(default_factory=list)
    state:dict[str,bool]=field(default_factory=dict)
    def validate(self):
        if self.category not in CATEGORIES: raise ValueError(f"invalid category: {self.category}")
        if set(self.operations)-OPERATIONS: raise ValueError("unsupported capability operation")
        if self.availability not in HEALTH: raise ValueError(f"invalid capability health: {self.availability}")
        if not self.name or not self.provider or not self.source or not self.scope: raise ValueError("missing capability provenance")
        return True

@dataclass
class CapabilityEdge:
    source_id:str
    target_id:str
    relation:str
    provenance:str

class CapabilityRegistry:
    def __init__(self, source="local-environment"):
        self.source=source; self.records={}; self.edges=[]; self.discovery_events=[]
    def register(self, record:CapabilityRecord):
        record.validate(); self.records[record.id]=record; return record
    def connect(self, source_id,target_id,relation,provenance):
        if source_id not in self.records or target_id not in self.records: raise KeyError("edge endpoint not registered")
        self.edges.append(CapabilityEdge(source_id,target_id,relation,provenance))
    def discover_config(self, config_path: str | None = None):
        if not config_path:
            self.discovery_events.append({"status":"NOT_CONFIGURED","source":"NEXUS_CONNECTOR_CONFIG_PATH","observed_at":utc_now(),"reason":"external connector catalog is not configured for this product runtime"})
            return
        p=Path(config_path); observed=utc_now()
        if not p.exists():
            self.discovery_events.append({"status":"UNKNOWN","source":str(p),"observed_at":observed,"reason":"configuration unavailable"}); return
        try: data=json.loads(p.read_text())
        except Exception as exc:
            self.discovery_events.append({"status":"FAILED","source":str(p),"observed_at":observed,"reason":f"malformed configuration: {exc}"}); return
        entries=data.get("connectors",[]) if isinstance(data.get("connectors"),list) else []
        for idx,item in enumerate(entries):
            if not isinstance(item,dict): continue
            name=item.get("name") or item.get("uid") or f"connector-{idx}"
            enabled=item.get("enabled") is True
            uid=item.get("uid") or f"config-{idx}"
            health="AVAILABLE" if enabled else "UNAVAILABLE"
            auth="enabled connector; operations not inferred" if enabled else "not enabled"
            ops=["DISCOVER"] if enabled else []
            if enabled and name=="GitHub": ops=["DISCOVER","READ","VERIFY"]
            rec=CapabilityRecord(id=f"connector:{uid}",name=name,category="ENGINEERING" if name=="GitHub" else "API",provider="external connector configuration",operations=ops,authorization=auth,risk="medium" if enabled else "unknown",input_schema="connector-defined or unknown",output_schema="connector-defined or unknown",verification_method="provider response; not invoked by discovery",dependencies=[],failure_modes=["unavailable","unauthorized","timeout","malformed response"],availability=health,scope="explicit product connector configuration",source=str(p),observed_at=observed,confidence="high" if enabled else "observed",limitations=["discovery does not prove callable operations","no connector invocation performed"])
            self.register(rec)
        self.discovery_events.append({"status":"SUCCESS","source":str(p),"observed_at":observed,"entries_observed":len(entries),"enabled":sum(1 for x in entries if isinstance(x,dict) and x.get('enabled') is True),"invocations":0})
    def register_local(self,name,category,operations,scope="local-runtime"):
        return self.register(CapabilityRecord(id=f"local:{name}",name=name,category=category,provider="NEXUS local runtime",operations=operations,authorization="local process only",risk="low",input_schema="python object",output_schema="python object",verification_method="local tests",dependencies=[],failure_modes=["exception","invalid input"],availability="AVAILABLE",scope=scope,source="local code inventory",confidence="bounded",limitations=["not an external connector"]))
    def health(self):
        return {"available":sum(r.availability=="AVAILABLE" for r in self.records.values()),"degraded":sum(r.availability=="DEGRADED" for r in self.records.values()),"unavailable":sum(r.availability=="UNAVAILABLE" for r in self.records.values()),"unauthorized":sum(r.availability=="UNAUTHORIZED" for r in self.records.values()),"unknown":sum(r.availability=="UNKNOWN" for r in self.records.values())}
    def select(self,intent,required_ops=None,allow_writes=False):
        required_ops=set(required_ops or ["READ"]); candidates=[]
        for r in self.records.values():
            if r.availability!="AVAILABLE" or not required_ops.issubset(set(r.operations)): continue
            if not allow_writes and set(r.operations)&{"WRITE","PUBLISH","DELETE","MODIFY","CREATE"}: continue
            candidates.append(r)
        return {"intent":intent,"required_operations":sorted(required_ops),"selected":[asdict(x) for x in candidates],"selection_status":"SUCCESS" if candidates else "UNKNOWN","writes_allowed":False,"governance":governance_for(intent),"source":"capability-registry","decision_basis":[{"record_id":x.id,"state":x.state,"limitations":x.limitations} for x in candidates],"authorization_not_inferred_from_catalog":True}

    def register_verified_runtime(self, artifact_path: str | None = None):
        evidence_root=Path(os.getenv("NEXUS_EVIDENCE_ROOT", Path.cwd() / ".nexus_evidence"))
        artifact_path=artifact_path or str(evidence_root / "nexus-action-ready-runtime.json")
        providers=[
          ("repository.read","repository.read","ENGINEERING","github-read",["READ","VERIFY"],"CONFIRMED_READ_ONLY","owner/repository","github provider receipt + independent repository observation",["read-only","full health depth uses seven GitHub API reads"],artifact_path),
          ("repository.metadata.read","repository.metadata.read","ENGINEERING","github-read",["READ","VERIFY"],"CONFIRMED_READ_ONLY","owner/repository","metadata schema, scope, and observed response verifier",["read-only","metadata-only; does not prove deep repository health"],str(evidence_root / "nexus-metadata-fast-path-real-fixed.json")),
          ("browser.read","browser.read","RESEARCH","browser-read",["READ","VERIFY"],"CONFIRMED_BROWSER_READ","explicit HTTPS URL","browser receipt + content integrity verifier",["read-only","explicit invocation only"],artifact_path),
          ("filesystem.read","filesystem.read","LOCAL","filesystem-read",["READ","VERIFY"],"CONFIRMED_LOCAL_READ","configured bounded filesystem root","filesystem receipt + SHA-256 verifier",["read-only","bounded path only"],artifact_path),
        ]
        for rid,name,category,provider,ops,auth,scope,verification,limitations,record_artifact in providers:
            record_persisted=Path(record_artifact).exists()
            self.register(CapabilityRecord(id=f"runtime:{rid}",name=name,category=category,provider=provider,operations=ops,authorization=auth,risk="LOW_READ_ONLY",input_schema="provider-specific bounded read",output_schema="source-preserving observation",verification_method=verification,dependencies=[],failure_modes=["timeout","unavailable","scope mismatch","stale evidence"],availability="AVAILABLE",scope=scope,source="canonical MissionComposer provider contract; evidence="+str(record_artifact),confidence="verified-history" if record_persisted else "bounded-contract",limitations=limitations+["no write or deployment authority","historical evidence does not imply current freshness"],state={"DISCOVERED":True,"AVAILABLE":True,"AUTHORIZED":True,"CALLABLE":True,"EXECUTED":record_persisted,"OBSERVED":record_persisted,"VERIFIED":record_persisted,"PERSISTED":record_persisted}))
        return self
    def graph(self):
        nodes=[]
        for record in self.records.values():
            node=asdict(record); operations=set(record.operations); writable=bool(operations & {"WRITE","EXECUTE","MODIFY","CREATE","PUBLISH","DELETE"})
            if writable:
                level=5 if operations & {"PUBLISH","DELETE","WRITE","EXECUTE"} else 4
                approval_required=True; execution_allowed=False; boundary='EXPLICIT_APPROVAL_REQUIRED'
            elif operations & {"READ","SEARCH"} and record.state.get("AUTHORIZED") and record.state.get("CALLABLE"):
                level=1; approval_required=False; execution_allowed=True; boundary='SCOPED_READ_ONLY'
            else:
                level=0; approval_required=True; execution_allowed=False; boundary='NOT_AVAILABLE_OR_UNAUTHORIZED'
            node.update({'action_level':level,'approval_required':approval_required,'execution_allowed':execution_allowed,'governance_boundary':boundary,'governance':governance_for(record.name)})
            nodes.append(node)
        return {"nodes":nodes,"edges":[asdict(x) for x in self.edges],"discovery_events":self.discovery_events,"health":self.health(),"source":self.source,"fail_closed":True}

def discover_actual_registry(config_path: str | None = None):
    r=CapabilityRegistry()
    r.discover_config(config_path or os.getenv("NEXUS_CONNECTOR_CONFIG_PATH"))
    r.register_verified_runtime()
    return r

if __name__=="__main__":
    print(json.dumps(discover_actual_registry().graph(),indent=2))
