# AIops

A CLI that runs **in the background** next to a local Kubernetes ([kind](https://kind.sigs.k8s.io/))
cluster. It does four things:

1. **Detects** broken pods and nodes automatically.
2. **Explains** why each one broke (root-cause analysis) using a local **Qwen** model in
   [Ollama](https://ollama.com).
3. **Proposes a fix.** Nothing is changed until you approve it.
4. **Writes an SOP**, a ready-to-use Markdown runbook, for every incident in `sops/`.

---

## Quick start (already set up?)

```bash
cd ~/AI-Agents/agentic-ai-for-devops/AIops
source .venv/bin/activate

aiops doctor      # check everything is ready
aiops start       # start the background daemon
aiops status      # see incidents and fixes waiting for you
```

> ⚠️ Don't run `python aiops/cli.py`. It's part of a package, not a script, so it won't
> work. Always use the `aiops` command after activating `.venv`, or `.venv/bin/aiops ...`.

---

## First-time setup

### Step 0: What you need

| Tool | Check it's installed | Install |
|---|---|---|
| Docker | `docker ps` | Docker Desktop (with WSL integration on Windows) |
| kind | `kind version` | https://kind.sigs.k8s.io/docs/user/quick-start/#installation |
| kubectl | `kubectl version --client` | https://kubernetes.io/docs/tasks/tools/ |
| Ollama | `ollama --version` | `curl -fsSL https://ollama.com/install.sh \| sh` |
| Python 3.10+ | `python3 --version` | your OS package manager |

**RAM:** use a model that fits in memory. With about 8 GB of RAM, use `qwen3:4b` (the
default). Larger models like `gemma4:26b` (18 GB) won't load and will just hang.

### Step 1: Start Ollama and download the Qwen model

```bash
ollama serve > /dev/null 2>&1 &     # skip if Ollama already runs as a service
ollama pull qwen3:4b                # ~2.5 GB, one time only
```

✅ Check: `ollama list` shows `qwen3:4b`.

### Step 2: Create the Kubernetes cluster

```bash
cd ~/AI-Agents/agentic-ai-for-devops/AIops
kind create cluster --name aiops --config kind/cluster-config.yaml
```

✅ Check: `kubectl config current-context` prints `kind-aiops`.

### Step 3: Install AIops

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

✅ Check: `aiops --version` prints `aiops 0.1.0`.

### Step 4: Verify everything

```bash
aiops doctor
```

Every line should say `[OK]`:

```
AIops doctor
  [OK] kubectl installed
  [OK] kind installed
  [OK] kubectl context is a kind cluster -- kind-aiops
  [OK] cluster reachable -- ...
  [OK] Ollama reachable at http://localhost:11434
  [OK] model 'qwen3:4b' pulled
  [INFO] daemon: not running
```

Setup is done. 🎉

---

## Try the demo (about 20 minutes, mostly waiting)

**1. Break some things on purpose.** This deploys 4 deliberately broken apps:

```bash
kubectl apply -f kind/test-manifests/
```

**2. Start AIops in the background:**

```bash
aiops start
```

**3. Watch it work.** Press `Ctrl+C` to stop watching; the daemon keeps running:

```bash
aiops logs -f
```

On a CPU-only machine each diagnosis takes about 3-5 minutes. You'll see lines like:

```
[3/4] RCA for OOMKilled in default/oom-app (model: qwen3:4b) ...
    -> inc-f51d68: The pod is being OOMKilled because the memory limit (50Mi) is too low...
    -> proposed: patch_resource_limits  | SOP: .../sops/oomkilled_default_oom-app_inc-f51d68.md
```

**4. See what it found:**

```bash
aiops status
```

```
Incidents: open=2, pending_fix=2

Fixes awaiting approval:
  aiops approve inc-f51d68   # OOMKilled in default/oom-app
  aiops approve inc-077283   # PendingUnschedulable in default/unschedulable-app
```

**5. Read the SOP, then approve a fix:**

```bash
aiops show inc-f51d68       # full runbook: root cause, fix, verify, rollback
aiops approve inc-f51d68    # shows the exact change, asks "Apply this fix? [y/N]"
```

**6. Watch it resolve.** About 2 minutes later, `aiops incidents` shows the incident as
`resolved`, and its SOP gets a timeline entry.

| Demo app | What AIops finds | What it proposes |
|---|---|---|
| `bad-image-app` | image tag doesn't exist | manual fix: correct the image tag |
| `crashloop-app` | the app's command exits with an error | manual fix: correct the command |
| `oom-app` | memory limit too low | **auto fix:** raise the memory limit |
| `unschedulable-app` | asks for more memory than a node has | **auto fix:** lower the request |

---

## Talk to it in plain English 💬

Just type `aiops` to open the conversational agent:

```
$ aiops
AIops agent ready (model qwen3:4b). Talk to it in plain English...

You: is anything broken in my cluster?
   ⚙  scan_cluster()
AIops: 1 problem found: oom-app is OOMKilled (memory limit 50Mi too low).
       Proposed fix: raise the memory limit (incident inc-f51d68).

You: fix it
   ⚙  apply_fix(incident_id='inc-f51d68')
Incident inc-f51d68: OOMKilled in default/oom-app
Fix:        patch_resource_limits {... 'memory_limit': '256Mi'}
   >>> Apply this fix? [y/N]: y
AIops: Done. The memory limit of oom-app was raised; it should be healthy in a minute.
```

Things you can say:

| You say | The agent does |
|---|---|
| "is anything broken?" / "scan the cluster" | detects anomalies, runs RCA and writes SOPs (fast rules) |
| "do a deep AI analysis of inc-xxxx" | re-analyzes that incident with Qwen (takes minutes) |
| "what's wrong?" / "list incidents" | lists open incidents |
| "why is oom-app failing?" | explains the root cause, evidence and fix |
| "fix it" / "apply the fix for oom-app" | applies the fix, **after you type `y`** |
| "don't fix that" | rejects the proposed fix |
| "show logs of crashloop-app" / "describe pod X" | reads the cluster (read-only) |
| "start background monitoring" / "stop monitoring" | starts or stops the daemon |

🔒 The agent can't change your cluster without you. Every fix stops at a
`Apply this fix? [y/N]` prompt in your terminal. That prompt is code, not the AI.

⏱️ On a CPU-only machine each reply takes about 30s-2min, while Qwen thinks.

---

## Everyday commands

| Command | What it does |
|---|---|
| `aiops start` | Start the background daemon (scans every 60s) |
| `aiops stop` | Stop the daemon |
| `aiops status` | Is it running? Which fixes are waiting? |
| `aiops logs -f` | Follow the daemon's log |
| `aiops incidents` | List all incidents |
| `aiops show <id>` | Print an incident's SOP |
| `aiops approve <id>` | Apply the proposed fix (asks y/N) |
| `aiops reject <id>` | Decline the fix (follow the SOP's manual steps) |
| `aiops scan` | Run one scan in the foreground instead of the daemon |
| `aiops scan --no-llm` | Fast scan using built-in rules only (seconds, no Ollama) |
| `aiops` (or `aiops chat`) | Talk to the agent in plain English |
| `aiops doctor` | Check prerequisites |
| `aiops --help` | All commands and options |

Useful options: `--model qwen3:8b`, `--namespace myapp`, `--interval 120`, `--no-llm`.

---

## Where things are

| Path | Contents |
|---|---|
| `sops/README.md` | **Index of all incidents**, with links to each SOP |
| `sops/*.md` | One SOP per incident |
| `.aiops/aiops.log` | Daemon log |
| `.aiops/state.json` | Incident database |

Each SOP contains: summary · symptoms · root cause and evidence · diagnose commands ·
fix (the `aiops approve` command and the equivalent `kubectl` command) · verify ·
rollback · prevention · timeline.

---

## Stop and clean up

```bash
aiops stop                           # stop the daemon
kind delete cluster --name aiops     # delete the cluster
```

Start again later with Step 2 of the setup, then `aiops start`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python aiops/cli.py` does nothing or errors | Use `aiops ...` after `source .venv/bin/activate` (see Quick start). |
| `aiops: command not found` | Activate the venv: `source .venv/bin/activate`, or use `.venv/bin/aiops`. |
| `Refusing to run ... not a kind cluster` | `kubectl config use-context kind-aiops`. If there's no cluster, do Step 2. AIops only works on kind clusters, on purpose. |
| `doctor`: Ollama not reachable | Run `ollama serve &`. |
| `doctor`: model not pulled | Run `ollama pull qwen3:4b`. |
| Ollama error `llama-server binary not found` | The Ollama install is broken. Reinstall with `curl -fsSL https://ollama.com/install.sh \| sh`. |
| RCA hangs or never finishes | The model is too big for your RAM. Use `qwen3:4b` (`--model qwen3:4b`) or run `aiops start --no-llm`. |
| RCA is slow (minutes) | Normal on CPU. After 10 minutes AIops falls back to rule-based analysis automatically. |
| `daemon already running` | `aiops stop`, then `aiops start`. |
| No incidents found | Wait 1-2 minutes after deploying. Pods need time to fail. Check with `kubectl get pods`. |

---

## How it works

```
aiops start ─► background daemon, every 60s:

  kubectl (pods, events, nodes)
      │
      ▼
  detector.py      finds anomalies with plain code (no AI = fast, free, reliable)
      │  new problem? (each problem is analyzed once, not every scan)
      ▼
  evidence.py      collects describe, events, logs, resource settings
      ▼
  rca.py           ONE request to Qwen for root cause and fix (falls back to rules on failure)
      ▼
  remediation.py   safety checks: fix can only target the broken workload, sane values
      ▼
  sop.py           writes sops/<incident>.md and sops/README.md
      ▼
  you: aiops approve <id>  ─►  kubectl applies the fix  ─►  daemon marks it resolved
```

**Safety:**
- The daemon **never** changes your cluster on its own. Every fix needs `aiops approve`.
- There are only 4 possible fix actions: restart a deployment, delete a pod, scale a
  deployment (0-10 replicas), or change CPU/memory settings.
- Fixes are locked to the workload that is actually broken, so the AI can't target
  something else.
- AIops refuses to run against anything that isn't a local `kind-*` cluster.
- The chat agent uses the same `approve` code path, so the fix is validated and you
  must type `y` in the terminal. Its kubectl tools (logs, describe, events) are read-only.

**Code layout** (`aiops/`): `cli.py` (commands), `daemon.py` (background loop),
`engine.py` (one scan cycle), `detector.py`, `evidence.py`, `rca.py`, `remediation.py`,
`sop.py`, `state.py`, `k8s.py` (the only place kubectl is called), `mcp_server.py` +
`chat.py` (chat agent, same pattern as `../../docker-agent`).

**Settings** (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `AIOPS_MODEL` | `qwen3:4b` | Ollama model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama address |
| `AIOPS_INTERVAL` | `60` | Seconds between scans |
| `AIOPS_LLM_TIMEOUT` | `600` | Max seconds per AI request before falling back to rules |
| `AIOPS_HOME` | `./.aiops` | Log, state and PID files |
| `AIOPS_SOPS_DIR` | `./sops` | Where SOPs go |

**Limitations:** it's built for local kind clusters only. It checks pods and nodes, not
metrics or app logs. A small model can still get things wrong, so read the SOP before
approving a fix.
