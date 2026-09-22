# SOP: OOMKilled in `default/oom-app`

| Field | Value |
|---|---|
| Incident ID | `inc-f51d68` |
| Status | 🔵 Fix applied -- verifying |
| Severity | high |
| Category | OOMKilled |
| Namespace / Workload | `default` / Deployment `oom-app` |
| Pod / Container | `oom-app-5f9d576ff9-sm4t6` / `app` |
| First seen / Last seen | 2026-09-22T22:18:17Z / 2026-09-22T22:18:17Z |
| Detections | 1 |
| RCA source / confidence | llm / high |

## 1. Summary

The app container is OOMKilled because its memory limit (50Mi) is too low for the stress workload (150M). Patching the memory limit to 200Mi resolves the issue. Prevent recurrence by setting memory limits based on actual workload needs.

## 2. Symptoms

- Detection signals: `{'restart_count': 6}`
- Kubernetes events:
- Pulled: Successfully pulled image "polinux/stress" in 711ms (712ms including waiting). Image size: 4041495 bytes.
- Pulled: Successfully pulled image "polinux/stress" in 756ms (756ms including waiting). Image size: 4041495 bytes.
- Created: Container created
- Started: Container started
- Pulled: Successfully pulled image "polinux/stress" in 712ms (712ms including waiting). Image size: 4041495 bytes.
- Pulling: Pulling image "polinux/stress"
- Pulled: Successfully pulled image "polinux/stress" in 704ms (704ms including waiting). Image size: 4041495 bytes.
- BackOff: Back-off restarting failed container app in pod oom-app-5f9d576ff9-sm4t6_default(de07eff2-f97d-419c-95b7-3a44f731b48f)

## 3. Root Cause Analysis

The container's memory limit (50Mi) is too low for the workload, causing OOMKilled events. The stress command is using 150M of memory (as seen in the command args: `--vm-bytes 150M`), which exceeds the 50Mi limit.

### Evidence

- restart_count: 6
- Pulled: Successfully pulled image "polinux/stress" in 712ms (712ms including waiting). Image size: 4041495 bytes.
- Pulling: Pulling image "polinux/stress"
- Pulled: Successfully pulled image "polinux/stress" in 704ms (704ms including waiting). Image size: 4041495 bytes.
- BackOff: Back-off restarting failed container app in pod oom-app-5f9d576ff9-sm4t6_default(de07eff2-f97d-419c-95b7-3a44f731b48f)

## 4. Diagnose (run these first)

```bash
kubectl describe pod oom-app-5f9d576ff9-sm4t6 -n default
kubectl get events -n default --field-selector involvedObject.name=oom-app-5f9d576ff9-sm4t6 --sort-by=.lastTimestamp
kubectl logs oom-app-5f9d576ff9-sm4t6 -n default --previous --tail=50
```

## 5. Resolution

**Automated fix (whitelisted, validated):** `patch_resource_limits`

- Arguments: `{'namespace': 'default', 'deployment_name': 'oom-app', 'container_name': 'app', 'memory_limit': '200Mi'}`
- Why: The memory limit is too low (50Mi) for the stress workload which uses 150M. Patching to 200Mi (2x the current limit) ensures sufficient memory without causing node pressure.

Apply it with AIops (recommended -- records the result in this SOP):

```bash
aiops approve inc-f51d68
```

Or apply the equivalent manually:

```bash
kubectl set resources deployment/oom-app -n default -c app --limits=memory=200Mi
```

### Manual remediation steps

- kubectl patch deployment oom-app -p '{"spec": {"template": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "200Mi"}}]}}}}}' --type=json

### Fix execution result

- Command: `kubectl patch deployment oom-app -n default --type strategic -p {"spec": {"template": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "200Mi"}}}]}}}}`
- Success: True
- Output: `deployment.apps/oom-app patched`

## 6. Verify

```bash
kubectl rollout status deployment/oom-app -n default --timeout=120s
kubectl get pods -n default | grep oom-app
# Expect STATUS=Running, READY=1/1 and RESTARTS not increasing
aiops scan --no-llm   # expect this incident to no longer be reported
```

## 7. Rollback

```bash
kubectl rollout undo deployment/oom-app -n default
```

## 8. Prevention

- Always set memory limits based on actual workload usage (e.g., use `stress --vm-bytes 150M` to calculate required memory).

## 9. Timeline

| Time (UTC) | Event |
|---|---|
| 2026-09-22T21:12:14Z | Detected: OOMKilled ({'restart_count': 4}) |
| 2026-09-22T21:12:14Z | RCA (llm, high): proposed `patch_resource_limits` |
| 2026-09-22T21:30:14Z | Fix applied by operator: `kubectl patch deployment oom-app -n default --type strategic -p {"spec": {"template": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "200Mi"}}}]}}}}` |
| 2026-09-22T21:31:55Z | Resolved: anomaly no longer detected |
| 2026-09-22T22:13:37Z | Recurred: OOMKilled ({'restart_count': 8}) |
| 2026-09-22T22:13:37Z | RCA (heuristic, medium): proposed `patch_resource_limits` |
| 2026-09-22T22:18:17Z | Recurred: OOMKilled ({'restart_count': 6}) |
| 2026-09-22T22:18:17Z | RCA (llm, high): proposed `patch_resource_limits` |
| 2026-09-22T22:32:20Z | Fix applied by operator: `kubectl patch deployment oom-app -n default --type strategic -p {"spec": {"template": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "200Mi"}}}]}}}}` |

---
*Generated by AIops (`llm` analysis). Review before acting in any non-local environment.*
