# SOP: ImagePullBackOff in `default/bad-image-app`

| Field | Value |
|---|---|
| Incident ID | `inc-521d0b` |
| Status | 🟠 Open -- manual action required |
| Severity | high |
| Category | ImagePullBackOff |
| Namespace / Workload | `default` / Deployment `bad-image-app` |
| Pod / Container | `bad-image-app-74fbdf7c75-66xcv` / `app` |
| First seen / Last seen | 2026-09-22T22:01:39Z / 2026-09-22T22:01:39Z |
| Detections | 1 |
| RCA source / confidence | llm / high |

## 1. Summary

The pod fails because the specified image tag does not exist. This is a configuration error, not a resource issue. No automated fix is possible; manual correction of the image tag in the deployment is required.

## 2. Symptoms

- Detection signals: `{'reason': 'ImagePullBackOff', 'image': 'nginx:this-tag-does-not-exist-xyz123', 'message': 'Back-off pulling image "nginx:this-tag-does-not-exist-xyz123": ErrImagePull: rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:this-tag-does-not-exist-xyz123": failed to resolve reference "docker.io/library/nginx:this-tag-does-not-exist-xyz123": docker.io/library/nginx:this-tag-does-not-exist-xyz123: not found'}`
- Kubernetes events:
- Scheduled: Successfully assigned default/bad-image-app-74fbdf7c75-66xcv to aiops-worker
- BackOff: Back-off pulling image "nginx:this-tag-does-not-exist-xyz123"
- Failed: Error: ImagePullBackOff
- Pulling: Pulling image "nginx:this-tag-does-not-exist-xyz123"
- Failed: Failed to pull image "nginx:this-tag-does-not-exist-xyz123": rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:this-tag-does-not-exist-xyz123": failed to resolve reference "docker.io/library/nginx:this-tag-does-not-exist-xyz123": docker.io/library/nginx:this-tag-does-not-exist-xyz123: not found
- Failed: Error: ErrImagePull

## 3. Root Cause Analysis

The pod is failing to pull an image that does not exist (nginx:this-tag-does-not-exist-xyz123). The error message explicitly states that the image reference is not found.

### Evidence

- reason: ImagePullBackOff
- image: nginx:this-tag-does-not-exist-xyz123
- message: Back-off pulling image "nginx:this-tag-does-not-exist-xyz123": ErrImagePull: rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:this-tag-does-not-exist-xyz123": failed to resolve reference "docker.io/library/nginx:this-tag-does-not-exist-xyz123": docker.io/library/nginx:this-tag-does-not-exist-xyz123: not found
- Failed: Error: ImagePullBackOff
- Pulling: Pulling image "nginx:this-tag-does-not-exist-xyz123"
- Failed: Failed to pull image "nginx:this-tag-does-not-exist-xyz123": rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:this-tag-does-not-exist-xyz123": failed to resolve reference "docker.io/library/nginx:this-tag-does-not-exist-xyz123": docker.io/library/nginx:this-tag-does-not-exist-xyz123: not found
- Failed: Error: ErrImagePull

## 4. Diagnose (run these first)

```bash
kubectl describe pod bad-image-app-74fbdf7c75-66xcv -n default
kubectl get events -n default --field-selector involvedObject.name=bad-image-app-74fbdf7c75-66xcv --sort-by=.lastTimestamp
kubectl logs bad-image-app-74fbdf7c75-66xcv -n default --previous --tail=50
```

## 5. Resolution

**No automated fix is safe for this failure** -- a human change is required.

- Why: The error indicates a non-existent image tag (nginx:this-tag-does-not-exist-xyz123). This is a configuration error in the image specification, not a resource issue. Automated tools cannot fix image tag errors. The correct fix requires manually correcting the image tag in the deployment spec.

### Manual remediation steps

- 1. Check the current deployment configuration: `kubectl get deployment bad-image-app -o yaml`
- 2. Locate the image field in the deployment spec and replace `nginx:this-tag-does-not-exist-xyz123` with a valid image tag (e.g., `nginx:latest` or `nginx:1.25.3`).
- 3. Apply the corrected deployment: `kubectl apply -f deployment.yaml`

## 6. Verify

```bash
kubectl rollout status deployment/bad-image-app -n default --timeout=120s
kubectl get pods -n default | grep bad-image-app
# Expect STATUS=Running, READY=1/1 and RESTARTS not increasing
aiops scan --no-llm   # expect this incident to no longer be reported
```

## 7. Rollback

```bash
# Not applicable: no automated change was made.
```

## 8. Prevention

- Always verify image tags exist before deploying. Use CI/CD pipelines to validate image tags against a registry before deployment.

## 9. Timeline

| Time (UTC) | Event |
|---|---|
| 2026-09-22T21:02:44Z | Detected: ImagePullBackOff ({'reason': 'ErrImagePull', 'image': 'nginx:this-tag-does-not-exist-xyz123', 'message': 'rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:this-tag-does-not-exist-xyz123": failed to resolve reference "docker.io/library/nginx:this-tag-does-n |
| 2026-09-22T21:02:44Z | RCA (llm, high): proposed `no_action` |
| 2026-09-22T21:55:09Z | Resolved: anomaly no longer detected |
| 2026-09-22T22:01:39Z | Recurred: ImagePullBackOff ({'reason': 'ImagePullBackOff', 'image': 'nginx:this-tag-does-not-exist-xyz123', 'message': 'Back-off pulling image "nginx:this-tag-does-not-exist-xyz123": ErrImagePull: rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:this-tag-does-not-ex |
| 2026-09-22T22:01:39Z | RCA (llm, high): proposed `no_action` |

---
*Generated by AIops (`llm` analysis). Review before acting in any non-local environment.*
