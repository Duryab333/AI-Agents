"""AIOps CLI: detect, diagnose, and (optionally, with confirmation) fix anomalies
in a local `kind` Kubernetes cluster.

Usage:
    python cli.py scan [--namespace NS] [--model MODEL] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import agent as agent_mod
import detector
import k8s_client
import sop_writer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aiops",
        description="Detect, diagnose, and fix anomalies in a local kind Kubernetes cluster.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser(
        "scan", help="Scan the cluster, generate RCA + SOPs, and optionally apply fixes."
    )
    scan.add_argument("--namespace", "-n", default=None, help="Limit to one namespace (default: all)")
    scan.add_argument("--restart-threshold", type=int, default=5)
    scan.add_argument("--stuck-threshold-seconds", type=int, default=300)
    scan.add_argument("--model", default=os.environ.get("AIOPS_MODEL", "gemma4:26b"))
    scan.add_argument("--sops-dir", default="sops")
    scan.add_argument("--include-system-namespaces", action="store_true")
    scan.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect and diagnose only; never prompt to apply a fix.",
    )
    return parser.parse_args()


async def run_scan(args: argparse.Namespace) -> None:
    k8s_client.assert_kind_context()

    try:
        anomalies = detector.detect_anomalies(
            namespace=args.namespace,
            restart_threshold=args.restart_threshold,
            stuck_threshold_seconds=args.stuck_threshold_seconds,
            exclude_system_namespaces=not args.include_system_namespaces,
        )
    except RuntimeError as exc:
        print(f"Error scanning cluster: {exc}", file=sys.stderr)
        sys.exit(1)

    if not anomalies:
        print("No anomalies detected.")
        return

    print(f"Detected {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'}:")
    for a in anomalies:
        print(f"  - [{a.severity.upper()}] {a.category} in {a.namespace}/{a.pod_name}")
    print()

    read_tools, remediation_tools = await agent_mod.build_mcp_tools()

    sops_dir = Path(args.sops_dir)
    incidents_for_index = []
    counts = {"applied": 0, "skipped": 0, "failed": 0, "info_only": 0}

    for anomaly in anomalies:
        print("=" * 70)
        print(f"{anomaly.category} - {anomaly.namespace}/{anomaly.pod_name}")
        print(f"Signals: {anomaly.signals}")
        print("Running RCA agent (calls the local Ollama model)...")

        rca = await agent_mod.run_rca(anomaly, read_tools, args.model)

        print(f"\nRoot cause: {rca.root_cause}")
        if rca.evidence:
            print("Evidence:")
            for e in rca.evidence:
                print(f"  - {e}")
        print(f"Confidence: {rca.confidence}")
        print(f"Summary: {rca.summary}")

        apply_result = None
        if rca.proposed_fix.tool_name == "no_action":
            print("\nProposed fix: none -- manual investigation required.")
            print(f"Rationale: {rca.proposed_fix.rationale}")
            outcome = "info_only"
        else:
            print(f"\nProposed fix: {rca.proposed_fix.tool_name}({rca.proposed_fix.args})")
            print(f"Rationale: {rca.proposed_fix.rationale}")
            if args.dry_run:
                print("(--dry-run: not prompting to apply)")
                outcome = "skipped"
            else:
                answer = input("Apply this fix? [y/N]: ").strip().lower()
                if answer == "y":
                    apply_result = await agent_mod.apply_fix(remediation_tools, rca.proposed_fix)
                    if apply_result.get("success"):
                        print("Fix applied.")
                        outcome = "applied"
                    else:
                        print(f"Fix failed: {apply_result.get('message', apply_result)}")
                        outcome = "failed"
                else:
                    outcome = "skipped"

        counts[outcome] = counts.get(outcome, 0) + 1

        sop_path = sop_writer.write_sop(anomaly, rca, outcome, apply_result, sops_dir)
        incidents_for_index.append(
            {
                "date": anomaly.detected_at.strftime("%Y-%m-%d %H:%M:%S"),
                "namespace": anomaly.namespace,
                "workload": anomaly.workload_name or anomaly.pod_name,
                "category": anomaly.category,
                "outcome": outcome,
                "filename": sop_path.name,
            }
        )
        print(f"SOP written to {sop_path}")
        print()

    sop_writer.write_index(sops_dir, incidents_for_index)

    print("=" * 70)
    print("Run summary:", counts)


def main() -> None:
    args = parse_args()
    if args.command == "scan":
        asyncio.run(run_scan(args))


if __name__ == "__main__":
    main()
