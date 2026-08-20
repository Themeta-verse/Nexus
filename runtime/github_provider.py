"""Bounded read-only repository provider using the direct GitHub REST adapter."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
import hashlib
import json

try:
    from canonical_pilot import DirectGitHubAPIAdapter, analyze_repository, verify_recommendation
    from canonical_core import core_id, RepositoryObservation
    from persistent_fabric import CapabilityProvider, CapabilityRequest, CapabilityResponse, ExecutionReceipt
except ImportError:
    from .canonical_pilot import DirectGitHubAPIAdapter, analyze_repository, verify_recommendation
    from .canonical_core import core_id, RepositoryObservation
    from .persistent_fabric import CapabilityProvider, CapabilityRequest, CapabilityResponse, ExecutionReceipt


READ_OPERATIONS = {"repository.read", "repository.metadata.read", "repository.branch.read", "repository.commits.read", "repository.tree.read", "repository.readme.read", "repository.issues.read", "repository.pull_requests.read", "repository.health.read"}
WRITE_OPERATIONS = {"repository.write", "repository.delete", "repository.deploy", "repository.merge", "repository.pull_request.create", "repository.settings.modify"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class GitHubReadProvider(CapabilityProvider):
    name = "github-read"
    provider_health = "AVAILABLE"

    def __init__(self, adapter: DirectGitHubAPIAdapter | None = None):
        self.adapter = adapter or DirectGitHubAPIAdapter()
        self.receipts: list[dict[str, Any]] = []

    def health(self) -> dict[str, Any]:
        return {
            "status": self.provider_health,
            "availability": True,
            "operations": sorted(READ_OPERATIONS),
            "write_operations": [],
            "transport": "direct-github-rest",
            "authentication": "PRODUCT_MANAGED_TOKEN" if bool(self.adapter.token) else "PUBLIC_READ_ONLY",
            "limitations": ["only bounded read endpoints are exposed", "authentication token is process-local and never returned"],
        }

    def discover(self, request: CapabilityRequest | None = None) -> dict[str, Any]:
        return {"provider": self.name, "health": self.provider_health, "operations": sorted(READ_OPERATIONS), "write_operations": [], "provenance": "direct GitHub REST adapter", "authentication": "PRODUCT_MANAGED_TOKEN" if bool(self.adapter.token) else "PUBLIC_READ_ONLY"}

    def validate(self, request: CapabilityRequest) -> dict[str, Any]:
        errors = []
        if request.capability not in {"github-read", "repository-read"}:
            errors.append("unsupported capability")
        if request.operation not in READ_OPERATIONS:
            errors.append("operation is not read-only or not supported")
        if request.scope.count("/") != 1:
            errors.append("scope must be owner/repository")
        if request.execution_mode not in {"REAL_READ", "SIMULATION", "DRY_RUN"}:
            errors.append("invalid execution mode")
        if request.authorization not in {"CONFIRMED_READ_ONLY", "READ_ONLY_AUTHORIZED"} and request.execution_mode == "REAL_READ":
            errors.append("read authorization evidence required")
        if request.governance not in {"READ_ONLY", "PREPARE_ONLY", "CONFIRM_READ_ONLY"}:
            errors.append("governance does not permit this operation")
        return {"valid": not errors, "errors": errors, "provider": self.name, "operation": request.operation}

    def prepare(self, request: CapabilityRequest) -> dict[str, Any]:
        check = self.validate(request)
        return {"status": "PREPARED" if check["valid"] else "BLOCKED", "validation": check, "side_effects": False}

    def _receipt(self, request: CapabilityRequest, response: CapabilityResponse, start: str, observation: RepositoryObservation | None = None) -> dict[str, Any]:
        end = now()
        raw = observation.raw if observation is not None else {}
        receipt = ExecutionReceipt(core_id("execution"), request.request_id, self.name, request.operation, start, end, response.status, False, response.outputs, response.observations, response.verification, request.authorization, ["github-read-provider", "direct-github-rest"])
        data = asdict(receipt)
        data.update({"capability": request.capability, "scope": request.scope, "inputs_hash": content_hash(request.inputs), "output_reference": content_hash(raw) if raw else None, "reality": response.reality, "failure_state": None if response.status in {"SUCCESS", "EXECUTED"} else response.reason})
        self.receipts.append(data)
        return data

    def execute(self, request: CapabilityRequest) -> dict[str, Any]:
        check = self.validate(request)
        start = now()
        if not check["valid"]:
            response = CapabilityResponse(request.request_id, "BLOCKED", "UNKNOWN", {}, [], "UNKNOWN", self.name, "; ".join(check["errors"]))
            return {"response": asdict(response), "receipt": self._receipt(request, response, start)}
        if request.execution_mode in {"SIMULATION", "DRY_RUN"}:
            response = CapabilityResponse(request.request_id, "EXECUTED", "SIMULATED", {"repository": request.scope}, [{"source": "github-read-simulation", "reality": "SIMULATED"}], "UNVERIFIED", self.name, "simulation mode; no GitHub access")
            return {"response": asdict(response), "receipt": self._receipt(request, response, start)}
        observation = self.adapter.observe(request.scope)
        response = CapabilityResponse(request.request_id, "EXECUTED" if observation.status in {"SUCCESS", "PARTIAL"} else "FAILED", "OBSERVED" if observation.status in {"SUCCESS", "PARTIAL"} else "UNKNOWN", {"observation": asdict(observation)}, [{"source": "direct-github-rest", "reality": "OBSERVED", "scope": request.scope, "observed_at": now()}], "UNVERIFIED", self.name, "real read-only GitHub observation")
        return {"response": asdict(response), "receipt": self._receipt(request, response, start, observation), "observation": asdict(observation)}

    def observe(self, request: CapabilityRequest) -> dict[str, Any]:
        return self.execute(request)

    def verify(self, request: CapabilityRequest, bundle: dict[str, Any]) -> dict[str, Any]:
        observation = bundle.get("observation")
        if not observation:
            return {"status": "UNKNOWN", "verification_state": "UNVERIFIED", "independent": True, "reason": "no observation"}
        result = analyze_repository(RepositoryObservation(**observation))
        verification = verify_recommendation(result, RepositoryObservation(**observation))
        return {"analysis": result, "verification": asdict(verification), "evidence_chain": result.get("evidence_chain", []), "reality": "INFERRED"}

    def invoke_metadata(self, request: CapabilityRequest) -> dict[str, Any]:
        check = self.validate(request)
        start = now()
        if not check["valid"]:
            response = CapabilityResponse(request.request_id, "BLOCKED", "UNKNOWN", {}, [], "UNKNOWN", self.name, "; ".join(check["errors"]))
            return {"response": asdict(response), "receipt": self._receipt(request, response, start)}
        if request.execution_mode in {"SIMULATION", "DRY_RUN"}:
            response = CapabilityResponse(request.request_id, "EXECUTED", "SIMULATED", {"repository": request.scope}, [{"source": "github-read-metadata-simulation", "reality": "SIMULATED"}], "UNVERIFIED", self.name, "simulation mode; no GitHub access")
            return {"response": asdict(response), "receipt": self._receipt(request, response, start)}
        observation = self.adapter.observe_metadata(request.scope)
        response = CapabilityResponse(request.request_id, "EXECUTED" if observation.status == "SUCCESS" else "FAILED", "OBSERVED" if observation.status == "SUCCESS" else "UNKNOWN", {"observation": asdict(observation)}, [{"source": "direct-github-rest-metadata", "reality": "OBSERVED", "scope": request.scope, "observed_at": now()}], "UNVERIFIED", self.name, "real metadata-only read")
        bundle = {"response": asdict(response), "receipt": self._receipt(request, response, start, observation), "observation": asdict(observation)}
        if observation.status == "SUCCESS":
            bundle["verification"] = self.verify_metadata(request, bundle)
            bundle["freshness"] = {"observed_at": now(), "source": "GitHub REST API metadata endpoint", "scope": request.scope, "content_hash": content_hash(bundle["observation"].get("raw", {})), "state": "CURRENT"}
        return bundle

    def verify_metadata(self, request: CapabilityRequest, bundle: dict[str, Any]) -> dict[str, Any]:
        observation = bundle.get("observation") or {}
        metadata = (observation.get("raw") or {}).get("metadata")
        verified = bool(metadata) and observation.get("status") == "SUCCESS" and observation.get("reality") == "OBSERVED"
        return {"status": "VERIFIED" if verified else "UNKNOWN", "verification_state": "VERIFIED" if verified else "UNVERIFIED", "independent": True, "authority": "RepositoryObservation metadata integrity", "method": "metadata schema and scope comparison", "depth": "METADATA_ONLY", "what_it_proves": ["repository identity and metadata endpoint response"] if verified else [], "what_it_does_not_prove": ["branch health", "recent commits", "tree/test depth", "README or issue state", "deep repository health"]}

    def invoke_health(self, request: CapabilityRequest) -> dict[str, Any]:
        bundle = self.execute(request)
        if "observation" not in bundle:
            return bundle
        bundle["verification"] = self.verify(request, bundle)
        bundle["freshness"] = {"observed_at": bundle["observation"].get("raw", {}).get("metadata", {}).get("updated_at") or now(), "source": "GitHub REST API", "scope": request.scope, "content_hash": content_hash(bundle["observation"].get("raw", {})), "state": "CURRENT"}
        bundle["capability_status"] = {operation: "REAL_READ_VERIFIED" for operation in READ_OPERATIONS}
        bundle["capability_status"].update({"repository.write": "UNAVAILABLE", "repository.delete": "UNAVAILABLE", "repository.deploy": "UNAVAILABLE"})
        return bundle


def detect_change(previous: dict | None, current: dict) -> dict[str, Any]:
    if not previous:
        return {"status": "NEW", "changed_fields": list(current.keys()), "evidence": "current observation only"}
    fields = ("repository", "status", "scope", "raw")
    changed = [field for field in fields if previous.get(field) != current.get(field)]
    return {"status": "CHANGED" if changed else "UNCHANGED", "changed_fields": changed, "evidence": "typed observation comparison; no difference invented"}
