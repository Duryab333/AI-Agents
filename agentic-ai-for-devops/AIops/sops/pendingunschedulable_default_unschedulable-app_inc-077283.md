# SOP: PendingUnschedulable in `default/unschedulable-app`

| Field | Value |
|---|---|
| Incident ID | `inc-077283` |
| Status | 🟡 Fix proposed -- awaiting approval |
| Severity | high |
| Category | PendingUnschedulable |
| Namespace / Workload | `default` / Deployment `unschedulable-app` |
| Pod / Container | `unschedulable-app-d49957787-dg94w` / `(pod-level)` |
| First seen / Last seen | 2026-09-22T22:28:19Z / 2026-09-22T22:28:19Z |
| Detections | 1 |
| RCA source / confidence | heuristic / medium |

## 1. Summary

No node can satisfy the pod's resource requests: 0/2 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling. (Rule-based analysis: LLM failed: ReadTimeout: timed out.)

## 2. Symptoms

- Detection signals: `{'message': '0/2 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.'}`
- Kubernetes events:
- FailedScheduling: 0/2 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.

## 3. Root Cause Analysis

No node can satisfy the pod's resource requests: 0/2 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.

### Evidence

- message: 0/2 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.
- FailedScheduling: 0/2 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.

## 4. Diagnose (run these first)

```bash
kubectl describe pod unschedulable-app-d49957787-dg94w -n default
kubectl get events -n default --field-selector involvedObject.name=unschedulable-app-d49957787-dg94w --sort-by=.lastTimestamp
kubectl logs unschedulable-app-d49957787-dg94w -n default --previous --tail=50
```

## 5. Resolution

**Automated fix (whitelisted, validated):** `patch_resource_limits`

- Arguments: `{'namespace': 'default', 'deployment_name': 'unschedulable-app', 'container_name': 'app', 'cpu_request': '100m', 'memory_request': '128Mi'}`
- Why: Lower resource requests so the pod fits on the kind nodes.

Apply it with AIops (recommended -- records the result in this SOP):

```bash
aiops approve inc-077283
```

Or apply the equivalent manually:

```bash
kubectl set resources deployment/unschedulable-app -n default -c app --requests=cpu=100m,memory=128Mi
```

### Manual remediation steps

- kubectl describe pod unschedulable-app-d49957787-dg94w -n default

## 6. Verify

```bash
kubectl rollout status deployment/unschedulable-app -n default --timeout=120s
kubectl get pods -n default | grep unschedulable-app
# Expect STATUS=Running, READY=1/1 and RESTARTS not increasing
aiops scan --no-llm   # expect this incident to no longer be reported
```

## 7. Rollback

```bash
kubectl rollout undo deployment/unschedulable-app -n default
```

## 8. Prevention

- Keep requests within node allocatable; use LimitRange defaults.

## 9. Timeline

| Time (UTC) | Event |
|---|---|
| 2026-09-22T21:15:52Z | Detected: PendingUnschedulable ({'message': '0/2 nodes are available: 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.'}) |
| 2026-09-22T21:15:52Z | RCA (llm, high): proposed `patch_resource_limits` |
| 2026-09-22T21:16:23Z | Proposed fix re-checked by request-size guardrail |
| 2026-09-22T21:32:02Z | Fix applied by operator: `kubectl patch deployment unschedulable-app -n default --type strategic -p {"spec": {"template": {"spec": {"containers": [{"name": "app", "resources": {"requests": {"memory": "3934Mi"}}}]}}}}` |
| 2026-09-22T21:33:56Z | Resolved: anomaly no longer detected |
| 2026-09-22T22:13:38Z | Recurred: PendingUnschedulable ({'message': '0/2 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.'}) |
| 2026-09-22T22:13:38Z | RCA (heuristic, medium): proposed `patch_resource_limits` |
| 2026-09-22T22:28:19Z | Recurred: PendingUnschedulable ({'message': '0/2 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 1 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.'}) |
| 2026-09-22T22:28:19Z | RCA (heuristic, medium): proposed `patch_resource_limits` |

---
*Generated by AIops (`heuristic` analysis). Review before acting in any non-local environment.*
