from __future__ import annotations

import argparse
import json

import uvicorn

from .api import create_app
from .config import ProductSettings
from .schemas import MissionSubmission
from .service import StandaloneMissionService


def main() -> None:
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


if __name__ == "__main__":
    main()
