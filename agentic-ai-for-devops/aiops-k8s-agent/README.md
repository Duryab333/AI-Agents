# AIOps K8s Agent

A CLI that scans a local `kind` Kubernetes cluster, detects anomalies, uses a local LLM
agent to diagnose root cause and propose a fix, asks you to confirm before applying
anything, and writes a ready-to-use Markdown SOP per incident.

Built as the Kubernetes analog of `../docker-agent` (same FastMCP + LangChain + Ollama
pattern), extended with deterministic detection, structured RCA output, and a
propose-then-confirm safety gate.

## How it works

```
kubectl (pods + events, JSON)
        |
        v
  detector.py  --------->  Anomaly records (deterministic, no LLM call)
        |
        v
  agent.py: RCA agent (Ollama, READ-ONLY k8s tools only)
        |
        v
  RCAResult { root_cause, evidence, proposed_fix, confidence, summary }
        |
        v
  cli.py: prints diagnosis, asks "Apply this fix? [y/N]"
        |                              |
       yes                             no / no_action
        |                              |
        v                              v
  agent.apply_fix() -- calls the       (skipped / info-only)
  ONE whitelisted remediation tool
  the LLM named, via k8s_client.py
        |
        v
  sop_writer.py -- writes sops/<incident>.md + updates sops/README.md index
```

**Safety model:** anomaly *detection* is plain Python parsing `kubectl` JSON output --
never left to the LLM. The RCA agent is only ever given read-only tools
(`list_pods`, `describe_pod`, `get_pod_logs`, `get_pod_events`, `get_node_status`,
`list_deployments`), so it is structurally unable to change the cluster. It can only
*propose* one of four whitelisted remediation actions (`restart_deployment`,
`delete_pod`, `scale_deployment`, `patch_resource_limits`) or `no_action`. Applying a
fix is a separate step in `cli.py`, gated on you typing `y` -- there is no
`--auto-approve` flag, and no generic "run any kubectl command" tool anywhere in the
system. `k8s_client.assert_kind_context()` also refuses to run at all unless the current
`kubectl` context starts with `kind-`, so it can't accidentally point at a real cluster.

## Setup

1. **Create a local cluster:**
   ```bash
   kind create cluster --name aiops-demo --config kind/cluster-config.yaml
   ```
2. **Install and start Ollama, then pull a tool-calling-capable model:**
   ```bash
   ollama serve &
   ollama pull gemma4:26b   # or set --model / AIOPS_MODEL to another tool-calling model
   ```
   (matches `../docker-agent`'s model choice; any tool-calling-capable Ollama model works)
3. **Install Python dependencies:**
   ```bash
   python3 -m pip install -r requirements.txt
   ```
   `requirements.txt` pins `mcp<2.0.0` deliberately -- `langchain_mcp_adapters` 0.3.x isn't
   yet compatible with the mcp SDK's 2.x API, and `mcp` already bundles the FastMCP server
   framework (`mcp.server.fastmcp`) used by `mcp_server.py`, so no separate `fastmcp`
   package is needed.

## Usage

```bash
python3 cli.py scan
```

Flags:
- `--namespace/-n NS` -- scan one namespace instead of the whole cluster
- `--model MODEL` -- Ollama model tag (default `gemma4:26b`, or `$AIOPS_MODEL`)
- `--restart-threshold N` -- restart count that counts as a "high restart" anomaly (default 5)
- `--stuck-threshold-seconds N` -- how long `ContainerCreating` must persist to count as stuck (default 300)
- `--sops-dir DIR` -- where to write SOPs (default `./sops`)
- `--include-system-namespaces` -- also scan `kube-system` etc. (excluded by default)
- `--dry-run` -- detect + diagnose + write SOPs, but never prompt to apply a fix

## Demo walkthrough

```bash
kind create cluster --name aiops-demo --config kind/cluster-config.yaml
kubectl apply -f kind/test-manifests/
# wait ~1-2 minutes for restart/backoff counters to accumulate
python3 cli.py scan
```

This deploys four deliberately broken workloads (bad image, crash-looping command,
an OOM-triggering `stress` container, and an over-large resource request) so you can see
every anomaly category, including cases where the agent correctly proposes `no_action`
(bad image, app-level crash) versus a real fix (OOM, unschedulable).

Clean up with `kind delete cluster --name aiops-demo`.

## v1 scope / not included

- One-shot `scan` only -- no continuous watch/daemon mode.
- Ollama only -- no OpenAI backend.
- No fully-autonomous remediation -- every fix requires an interactive `y`.

These are natural v2 extensions (`aiops watch`, a `--backend openai` flag, etc.) but are
out of scope here by design, per the AIOps idea list in `../demo/AIops/README.md`.
