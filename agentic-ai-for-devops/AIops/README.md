# AIops -- background AIOps for Kubernetes on kind

`aiops` is a CLI that runs **in the background**. It watches a local
[kind](https://kind.sigs.k8s.io/) Kubernetes cluster and does the following for every problem
it finds:

1. **Auto-detects the anomaly.** Checks cover CrashLoopBackOff, OOMKilled, ImagePullBackOff,
   unschedulable pods, config errors, stuck containers, probe failures, high restart counts
   and NotReady nodes.
2. **Generates an RCA** (root-cause analysis) with a local **Qwen** model through
   [Ollama](https://ollama.com).
3. **Creates a solution.** This is one validated, whitelisted automated fix, or exact manual
   steps when no automated fix is safe.
4. **Writes a ready-to-use SOP** in Markdown, one per incident. `sops/README.md` is the index.

It follows the same Ollama, LangChain and MCP pattern as
[`docker-agent`](../../docker-agent), turned into a background service.

## Architecture

```
            aiops start  ──►  background daemon (python -m aiops run), every N seconds:
                                        │
  kubectl get pods/events/nodes (JSON)  ▼
                            ┌───────────────────────┐
                            │ detector.py           │  deterministic, no LLM
                            │ -> Anomaly records    │  (a healthy cluster costs 0 LLM calls)
                            └──────────┬────────────┘
                     new incident?     │ dedup by namespace/workload/category (state.py)
                                       ▼
                            ┌───────────────────────┐
                            │ evidence.py           │  describe, events, logs (--previous),
                            │                       │  resource specs, node allocatable
                            └──────────┬────────────┘
                                       ▼
                            ┌───────────────────────┐
                            │ rca.py -> Ollama Qwen │  ONE structured JSON call (think=off)
                            │  + rule-based fallback│  if the LLM is down or returns junk
                            └──────────┬────────────┘
                                       ▼
                            ┌───────────────────────┐
                            │ remediation.py        │  pins targets to the real workload,
                            │  sanitize_fix()       │  validates quantities, clamps replicas
                            └──────────┬────────────┘
                                       ▼
                 sops/<category>_<ns>_<workload>_<id>.md  +  sops/README.md index
                                       │
            status = pending_fix ──► you: `aiops approve <id>` ──► kubectl (whitelisted)
                                       │
            anomaly gone for 2 scans ──► status = resolved, SOP updated
```

### Safety model

- **Detection is plain code.** The LLM never decides what counts as broken.
- **The daemon never changes the cluster.** A proposed fix is queued as `pending_fix`.
  Only `aiops approve <id>`, run by a person, applies it.
- **Only four fix actions exist:** `restart_deployment`, `delete_pod`,
  `scale_deployment` (0-10 replicas) and `patch_resource_limits`. There is no "run any
  kubectl" tool anywhere.
- **Fix targets are pinned.** Before a fix is stored, the namespace, deployment, pod and
  container are overwritten with the ones that were actually detected. A hallucinated
  target can't touch a different workload.
- **Some model mistakes are corrected.** For example, an OOM fix that wouldn't actually
  raise the memory limit is replaced.
- **It only runs against kind clusters.** It refuses any kubectl context that doesn't start
  with `kind-`.
- **`aiops chat` is read-only.** Its MCP server has no tool that changes anything.

### Why RCA is a single LLM call

On a CPU-only laptop, a small model driving a tool-calling loop takes 5-15 minutes per
incident. AIops collects the evidence itself and asks Qwen once, with a JSON schema
(`format=`) and thinking turned off. That takes about 1-3 minutes on CPU and always
includes the logs.

## Setup

Prerequisites: Docker, `kind`, `kubectl`, Ollama, Python 3.10+.

```bash
# 1. Local cluster
kind create cluster --name aiops --config kind/cluster-config.yaml

# 2. Ollama + a Qwen model (qwen3:4b fits in ~4 GB RAM; use qwen3:8b/14b if you have more)
ollama serve &
ollama pull qwen3:4b

# 3. Install the CLI
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 4. Check everything
aiops doctor
```

## Usage

```bash
aiops start                 # start the background daemon (scans every 60s)
aiops status                # daemon state, incident counts, fixes awaiting approval
aiops logs -f               # follow what the daemon is doing
aiops incidents             # list incidents
aiops show inc-1a2b3c       # print an incident's SOP
aiops approve inc-1a2b3c    # review + apply its proposed fix (asks y/N)
aiops reject inc-1a2b3c     # decline it; follow the SOP's manual steps
aiops stop                  # stop the daemon

aiops scan                  # one cycle in the foreground (no daemon)
aiops run                   # the daemon loop in the foreground (Ctrl+C to stop)
aiops chat                  # ask questions about the cluster (MCP + LangChain agent)
```

Common flags (all commands): `--model`, `--namespace/-n`, `--interval`, `--no-llm`
(rule-based RCA only), `--restart-threshold`, `--include-system-namespaces`, `--home`,
`--sops-dir`.

| Env var | Default | Meaning |
|---|---|---|
| `AIOPS_MODEL` | `qwen3:4b` | Ollama model used for RCA and chat |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |
| `AIOPS_INTERVAL` | `60` | Seconds between daemon scans |
| `AIOPS_LLM_TIMEOUT` | `600` | Per-request LLM timeout (falls back to rules) |
| `AIOPS_NUM_CTX` | `8192` | Model context window |
| `AIOPS_HOME` | `./.aiops` | State, log and PID files |
| `AIOPS_SOPS_DIR` | `./sops` | Where SOPs are written |

## Demo

```bash
kubectl apply -f kind/test-manifests/     # 4 deliberately broken workloads
aiops start
aiops logs -f                             # watch detection + RCA (a few minutes on CPU)
aiops status                              # -> e.g. "aiops approve inc-xxxxxx  # OOMKilled ..."
aiops approve <oom-incident-id>           # raises the memory limit
aiops approve <pending-incident-id>       # lowers the oversized requests
# ~2 scans later both incidents flip to "resolved" and their SOPs are updated
cat sops/README.md
```

| Manifest | Detected as | Expected solution |
|---|---|---|
| `bad-image.yaml` | ImagePullBackOff | manual: fix the image tag (`no_action`) |
| `crashloop.yaml` | CrashLoopBackOff | manual: fix the app error in the logs (`no_action`) |
| `oom.yaml` | OOMKilled | `patch_resource_limits` (raise memory limit) |
| `pending-unschedulable.yaml` | PendingUnschedulable | `patch_resource_limits` (lower requests) |

Clean up: `aiops stop && kind delete cluster --name aiops`.

## What each SOP contains

Summary, symptoms, root cause and evidence, **diagnose** commands, **resolution** (the
automated fix, its `aiops approve` command, the equivalent `kubectl` command and the manual
steps), **verify**, **rollback**, **prevention**, and a timeline of the incident. The SOP is
re-rendered whenever the incident's status changes.

## Project layout

```
aiops/
  cli.py          argparse entry point (`aiops ...`)
  daemon.py       start/stop/status + watch loop
  engine.py       one detect -> RCA -> SOP cycle, dedup, auto-resolve
  detector.py     deterministic anomaly detection
  evidence.py     collects describe/events/logs/resources for the RCA
  rca.py          Ollama Qwen structured RCA + rule-based fallback + guardrails
  remediation.py  fix validation (sanitize_fix) and whitelisted execution
  sop.py          SOP + index Markdown rendering
  state.py        locked JSON incident store
  k8s.py          kubectl wrapper (the only place kubectl is called)
  mcp_server.py   read-only FastMCP tools for `aiops chat`
  chat.py         LangChain + ChatOllama troubleshooting agent
kind/             cluster config + broken test workloads
sops/             generated SOPs
```

## Limitations

- It's built for local kind clusters. Watching is polling (`kubectl` every interval), not
  the Kubernetes watch API.
- It covers pod- and node-level failures. It doesn't analyze metrics or logs
  (no Prometheus or Loki).
- A small CPU-only model can still misdiagnose. Every SOP records whether its RCA came from
  the LLM or the rules, plus a confidence level. Review a fix before approving it.
