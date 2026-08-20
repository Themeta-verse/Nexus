from __future__ import annotations

import argparse
import json
import os
import sys

import uvicorn

from .api import create_app
from .config import ProductSettings
from .schemas import MissionSubmission
from .service import StandaloneMissionService


def main() -> None:
    known_commands = {"serve", "worker", "bootstrap", "run", "ask", "health", "migrate", "backup", "restore", "recover", "-h", "--help"}
    if len(sys.argv) > 1 and sys.argv[1] not in known_commands:
        sys.argv.insert(1, "ask")
    parser = argparse.ArgumentParser(prog="nexus-independent", description="Independent NEXUS product runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the authenticated standalone NEXUS API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    worker = sub.add_parser("worker", help="process durable queued missions")
    worker.add_argument("--once", action="store_true", help="claim and process at most one queued mission")
    worker.add_argument("--worker-id", default=None)
    bootstrap = sub.add_parser("bootstrap", help="create or recover the product owner and primary tenant project")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--password", required=True)
    bootstrap.add_argument("--tenant", default="NEXUS")
    bootstrap.add_argument("--project-id", default="local")
    run = sub.add_parser("run", help="enqueue and execute one read-only mission through the local worker")
    run.add_argument("intent")
    run.add_argument("--email", required=True)
    run.add_argument("--password", required=True)
    run.add_argument("--project-id", default="local")
    run.add_argument("--scope", default="Themeta-verse/Nexus")
    run.add_argument("--mode", choices=["REAL_READ", "SIMULATION"], default="SIMULATION")
    run.add_argument("--capability", action="append", dest="capabilities")
    run.add_argument("--browser-url")
    run.add_argument("--filesystem-path")
    run.add_argument("--repository-scope")
    health = sub.add_parser("health", help="show standalone runtime health")
    migrate = sub.add_parser("migrate", help="apply standalone SQLite product migrations")
    backup = sub.add_parser("backup", help="create a consistent standalone SQLite backup")
    backup.add_argument("destination")
    restore = sub.add_parser("restore", help="restore a stopped standalone runtime from a SQLite backup")
    restore.add_argument("source")
    restore.add_argument("--confirm-restore", action="store_true", help="required because restore replaces the live product database")
    recover = sub.add_parser("recover", help="recover a persisted mission")
    recover.add_argument("mission_id")
    recover.add_argument("--email", required=True)
    recover.add_argument("--password", required=True)
    ask = sub.add_parser("ask", help="queue a governed objective or read durable project continuity from ordinary language")
    ask.add_argument("intent")
    ask.add_argument("--email", default=os.getenv("NEXUS_CLI_EMAIL"))
    ask.add_argument("--password", default=os.getenv("NEXUS_CLI_PASSWORD"))
    ask.add_argument("--project-id", default=os.getenv("NEXUS_CLI_PROJECT", "local"))
    ask.add_argument("--scope", default=os.getenv("NEXUS_GITHUB_REPOSITORY", "Themeta-verse/Nexus"))
    ask.add_argument("--mode", choices=["REAL_READ", "SIMULATION"], default="REAL_READ")
    args = parser.parse_args()
    settings = ProductSettings.from_env()
    service = StandaloneMissionService(settings)
    if args.command == "serve":
        uvicorn.run(create_app(service), host=args.host or settings.api_host, port=args.port or settings.api_port)
    elif args.command == "worker":
        print(json.dumps({"processed": service.run_worker(args.worker_id, once=args.once), "worker_id": args.worker_id}, indent=2))
    elif args.command == "bootstrap":
        print(json.dumps(service.bootstrap_owner(args.email, args.password, args.tenant, args.project_id), indent=2, default=str))
    elif args.command == "run":
        session = service.login(args.email, args.password)
        if not session:
            raise SystemExit("authentication failed")
        payload = MissionSubmission(intent=args.intent, project_id=args.project_id, scope=args.scope, mode=args.mode, capabilities=args.capabilities, browser_url=args.browser_url, filesystem_path=args.filesystem_path, repository_scope=args.repository_scope)
        print(json.dumps(service.submit_and_execute(session["user"], payload), indent=2, default=str))
    elif args.command == "health":
        print(json.dumps(service.health(), indent=2, default=str))
    elif args.command == "migrate":
        service.database.migrate()
        print(json.dumps({"status": "MIGRATED", "database": str(settings.database_path)}, indent=2))
    elif args.command == "backup":
        print(json.dumps({"status": "BACKED_UP", "database": str(service.database.backup_to(args.destination))}, indent=2))
    elif args.command == "restore":
        if not args.confirm_restore:
            raise SystemExit("restore requires --confirm-restore after stopping API and worker processes")
        print(json.dumps({"status": "RESTORED", "database": str(service.database.restore_from(args.source))}, indent=2))
    elif args.command == "recover":
        session = service.login(args.email, args.password)
        if not session:
            raise SystemExit("authentication failed")
        result = service.recover(session["user"], args.mission_id)
        print(json.dumps(result or {"status": "NOT_FOUND"}, indent=2, default=str))
    elif args.command == "ask":
        if not args.email or not args.password:
            raise SystemExit("ask requires product credentials via --email/--password or NEXUS_CLI_EMAIL/NEXUS_CLI_PASSWORD")
        session = service.login(args.email, args.password)
        if not session:
            raise SystemExit("authentication failed")
        principal = session["user"]
        normalized = args.intent.strip().lower()
        context_questions = {"where were we?", "where were we", "what changed?", "what changed", "what should happen next?", "what should happen next", "what next?", "what next"}
        if normalized in context_questions:
            print(json.dumps({"kind": "project_context", "context": service.project_context(principal, args.project_id)}, indent=2, default=str))
            return
        context = service.project_context(principal, args.project_id)
        if normalized in {"continue", "continue."}:
            latest = context.get("latest_mission")
            if latest is None:
                print(json.dumps({"kind": "continuation", "status": "NO_DURABLE_MISSION", "next_action": context["next_action"]}, indent=2))
                return
            print(json.dumps({"kind": "continuation", "result": service.continue_mission(principal, latest["mission_id"])}, indent=2, default=str))
            return
        capabilities = ["repository.metadata.read"]
        if any(term in normalized for term in ("analyze", "project", "repository", "code", "blocking", "unfinished")):
            capabilities = ["repository.read", "filesystem.read"]
        elif any(term in normalized for term in ("browser", "web page", "website")):
            capabilities = ["browser.read"]
        payload = MissionSubmission(intent=args.intent, project_id=args.project_id, scope=args.scope, mode=args.mode, capabilities=capabilities)
        queued = service.enqueue_mission(principal, payload)
        print(json.dumps({"kind": "queued_objective", "mission": queued, "required_worker": "nexus worker", "capabilities": capabilities}, indent=2, default=str))


if __name__ == "__main__":
    main()
