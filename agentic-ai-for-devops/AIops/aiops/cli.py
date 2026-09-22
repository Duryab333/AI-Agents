"""aiops -- background AIOps for a local kind Kubernetes cluster.

    aiops doctor                  check kubectl / kind / Ollama / model
    aiops start | stop | status   manage the background daemon
    aiops logs [-f]               daemon log
    aiops scan                    one detection -> RCA -> SOP cycle in the foreground
    aiops run                     the daemon loop in the foreground
    aiops incidents               list incidents
    aiops show ID                 print an incident's SOP
    aiops approve ID | reject ID  act on a proposed fix
    aiops  (or: aiops chat)       talk to the AIops agent in plain English
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import time

from aiops import __version__, actions, daemon, engine, k8s, sop
from aiops.config import Settings
from aiops.state import StateStore, find

# ---- argument parsing ----


def _common(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("settings")
    g.add_argument("--model", help="Ollama model (default: $AIOPS_MODEL or qwen3:4b)")
    g.add_argument("--namespace", "-n", help="watch only this namespace (default: all)")
    g.add_argument("--interval", type=int, help="seconds between scans (default: 60)")
    g.add_argument("--home", help="state/log/pid dir (default: ./.aiops)")
    g.add_argument("--sops-dir", help="where SOP docs are written (default: ./sops)")
    g.add_argument("--no-llm", action="store_true", help="rule-based RCA only (no Ollama)")
    g.add_argument("--restart-threshold", type=int, default=3)
    g.add_argument("--include-system-namespaces", action="store_true")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aiops", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"aiops {__version__}")
    sub = parser.add_subparsers(dest="command")

    for name, helptext in [
        ("doctor", "check prerequisites"),
        ("start", "start the background daemon"),
        ("stop", "stop the background daemon"),
        ("status", "daemon + incident status"),
        ("run", "run the watch loop in the foreground"),
        ("scan", "run one scan cycle in the foreground"),
        ("incidents", "list incidents"),
        ("chat", "talk to the AIops agent in plain English (default)"),
    ]:
        _common(sub.add_parser(name, help=helptext))

    p = sub.add_parser("logs", help="show the daemon log")
    _common(p)
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("--lines", type=int, default=40)

    for name, helptext in [("show", "print an incident SOP"),
                           ("approve", "apply an incident's proposed fix"),
                           ("reject", "reject an incident's proposed fix")]:
        p = sub.add_parser(name, help=helptext)
        _common(p)
        p.add_argument("incident_id")
        if name == "approve":
            p.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)
    if args.command is None:  # bare `aiops` opens the conversational agent
        args = parser.parse_args(["chat", *(argv or sys.argv[1:])])
    return args


def build_settings(args: argparse.Namespace) -> Settings:
    s = Settings()
    overrides = {"model": args.model, "namespace": args.namespace,
                 "interval_seconds": args.interval, "home": args.home, "sops_dir": args.sops_dir}
    for k, v in overrides.items():
        if v is not None:
            setattr(s, k, v)
    s.use_llm = not args.no_llm
    s.restart_threshold = args.restart_threshold
    s.include_system_namespaces = args.include_system_namespaces
    s.__post_init__()
    return s


def run_args_for_daemon(s: Settings, args: argparse.Namespace) -> list[str]:
    out = ["--model", s.model, "--interval", str(s.interval_seconds), "--home", str(s.home),
           "--sops-dir", str(s.sops_dir), "--restart-threshold", str(s.restart_threshold)]
    if s.namespace:
        out += ["--namespace", s.namespace]
    if not s.use_llm:
        out.append("--no-llm")
    if s.include_system_namespaces:
        out.append("--include-system-namespaces")
    return out


# ---- commands ----


def cmd_doctor(s: Settings) -> int:
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  [{'OK' if passed else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")

    print("AIops doctor")
    for tool in ("kubectl", "kind"):
        check(f"{tool} installed", shutil.which(tool) is not None)
    ctx = k8s.current_context()
    check("kubectl context is a kind cluster", ctx.startswith("kind-"), ctx or "no context set")
    if ctx.startswith("kind-"):
        nodes = k8s.run_kubectl(["get", "nodes", "--no-headers"])
        check("cluster reachable", not nodes.startswith("Error"), nodes.splitlines()[0] if nodes else "")
    if s.use_llm:
        try:
            import ollama
            models = [m.model for m in ollama.Client(host=s.ollama_host, timeout=5).list().models]
            check(f"Ollama reachable at {s.ollama_host}", True)
            check(f"model '{s.model}' pulled", s.model in models,
                  "" if s.model in models else f"run: ollama pull {s.model}")
        except Exception as exc:
            check(f"Ollama reachable at {s.ollama_host}", False, f"{exc} (run: ollama serve)")
    pid = daemon.read_pid(s)
    print(f"  [INFO] daemon: {'running, PID ' + str(pid) if pid else 'not running'}")
    return 0 if ok else 1


def cmd_status(s: Settings) -> int:
    pid = daemon.read_pid(s)
    print(f"Daemon: {'RUNNING (PID ' + str(pid) + ')' if pid else 'stopped'}")
    print(f"Model: {s.model} | State: {s.state_file} | SOPs: {s.sops_dir}")
    incidents = StateStore(s.state_file).load()
    counts: dict[str, int] = {}
    for i in incidents.values():
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    print("Incidents:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none")
    pending = [i for i in incidents.values() if i["status"] == "pending_fix"]
    if pending:
        print("\nFixes awaiting approval:")
        for i in pending:
            print(f"  aiops approve {i['id']}   # {i['category']} in {i['namespace']}/{i['workload']}")
    return 0


def cmd_incidents(s: Settings) -> int:
    incidents = sorted(StateStore(s.state_file).load().values(),
                       key=lambda i: i["first_seen"], reverse=True)
    if not incidents:
        print("No incidents recorded.")
        return 0
    print(f"{'ID':<11}{'STATUS':<13}{'SEV':<9}{'CATEGORY':<27}{'WORKLOAD':<34}FIX")
    for i in incidents:
        print(f"{i['id']:<11}{i['status']:<13}{i['severity']:<9}{i['category']:<27}"
              f"{(i['namespace'] + '/' + i['workload'])[:33]:<34}{i['rca']['proposed_fix']['tool_name']}")
    return 0


def cmd_show(s: Settings, incident_id: str) -> int:
    inc = find(StateStore(s.state_file).load(), incident_id)
    if not inc:
        print(f"No incident {incident_id}", file=sys.stderr)
        return 1
    print(sop.render(inc))
    print(f"(file: {s.sops_dir / sop.sop_filename(inc)})")
    return 0


def cmd_approve(s: Settings, incident_id: str, assume_yes: bool) -> int:
    def confirm() -> bool:
        return assume_yes or input("Apply this fix? [y/N]: ").strip().lower() == "y"

    result = actions.approve(s, incident_id, confirm)
    if not result["applied"]:
        print(result["message"])
        return 0 if "declined" in result["message"] else 1
    print(f"Fix applied: {result['message']}")
    print(f"SOP updated: {result['sop']}")
    print("The daemon will mark the incident resolved once the anomaly clears.")
    return 0


def cmd_reject(s: Settings, incident_id: str) -> int:
    print(actions.reject(s, incident_id))
    return 0


def cmd_scan(s: Settings) -> int:
    k8s.assert_kind_context()
    t0 = time.time()
    report = engine.run_cycle(s, StateStore(s.state_file))
    print(f"\nScan done in {time.time() - t0:.0f}s: {report.detected} anomalies "
          f"({len(report.new)} new, {report.ongoing} ongoing, {len(report.resolved)} resolved).")
    print(f"SOP index: {s.sops_dir / 'README.md'}")
    return 0


def cmd_logs(s: Settings, follow: bool, lines: int) -> int:
    if not s.log_file.exists():
        print("No log yet -- start the daemon with `aiops start`.")
        return 0
    cmd = ["tail", "-n", str(lines)] + (["-f"] if follow else []) + [str(s.log_file)]
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


def main(argv=None) -> None:
    args = parse_args(argv)
    s = build_settings(args)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        c = args.command
        if c == "doctor":
            code = cmd_doctor(s)
        elif c == "start":
            pid = daemon.start(s, run_args_for_daemon(s, args))
            print(f"AIops daemon started (PID {pid}), scanning every {s.interval_seconds}s "
                  f"with {s.model if s.use_llm else 'rules-only RCA'}.")
            print(f"  logs: aiops logs -f   |  status: aiops status  |  SOPs: {s.sops_dir}")
            code = 0
        elif c == "stop":
            pid = daemon.stop(s)
            print(f"Stopped AIops daemon (PID {pid})." if pid else "Daemon is not running.")
            code = 0
        elif c == "status":
            code = cmd_status(s)
        elif c == "run":
            daemon.run_forever(s)
            code = 0
        elif c == "scan":
            code = cmd_scan(s)
        elif c == "incidents":
            code = cmd_incidents(s)
        elif c == "show":
            code = cmd_show(s, args.incident_id)
        elif c == "approve":
            code = cmd_approve(s, args.incident_id, args.yes)
        elif c == "reject":
            code = cmd_reject(s, args.incident_id)
        elif c == "logs":
            code = cmd_logs(s, args.follow, args.lines)
        elif c == "chat":
            from aiops.chat import chat
            asyncio.run(chat(s))
            code = 0
        else:
            code = 2
    except (k8s.NotKindClusterError, actions.ActionError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)
