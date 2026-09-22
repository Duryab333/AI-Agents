# Incident: ImagePullBackOff in default/bad-image-app

- **Detected:** 2026-09-22 20:04:14 UTC
- **Namespace / Pod / Container:** default / bad-image-app-74fbdf7c75-x2kmw / app
- **Workload:** Deployment bad-image-app
- **Category:** ImagePullBackOff
- **Severity:** high
- **Outcome:** info_only

## Symptoms

- Detection signals: `{'reason': 'ImagePullBackOff', 'message': 'Back-off pulling image "nginx:this-tag-does-not-exist-xyz123": ErrImagePull: rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:this-tag-does-not-exist-xyz123": failed to resolve reference "docker.io/library/nginx:this-tag-does-not-exist-xyz123": docker.io/library/nginx:this-tag-does-not-exist-xyz123: not found'}`
- Related events at detection time:
  - FailedScheduling: 0/2 nodes are available: 2 node(s) had untolerated taint(s). no new claims to deallocate, preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling.
  - Scheduled: Successfully assigned default/bad-image-app-74fbdf7c75-x2kmw to aiops-demo-worker
  - Pulling: Pulling image "nginx:this-tag-does-not-exist-xyz123"
  - Failed: Failed to pull image "nginx:this-tag-does-not-exist-xyz123": rpc error: code = NotFound desc = failed to pull and unpack image "docker.io/library/nginx:this-tag-does-not-exist-xyz123": failed to resolve reference "docker.io/library/nginx:this-tag-does-not-exist-xyz123": docker.io/library/nginx:this-tag-does-not-exist-xyz123: not found
  - Failed: Error: ErrImagePull
  - BackOff: Back-off pulling image "nginx:this-tag-does-not-exist-xyz123"
  - Failed: Error: ImagePullBackOff

## Root Cause Analysis

Deployment specifies invalid image tag 'nginx:this-tag-does-not-exist-xyz123'

### Evidence

- Deployment bad-image-app uses invalid image 'nginx:this-tag-does-not-exist-xyz123' (list_deployments output)
- Image pull failure: 'docker.io/library/nginx:this-tag-does-not-exist-xyz123: not found' (events)
- Pod fails to schedule due to image pull error (events)

## Proposed Fix

- **Tool:** `no_action`
- **Arguments:** `{}`
- **Rationale:** The deployment's image tag 'nginx:this-tag-does-not-exist-xyz123' is invalid and requires manual correction in the deployment spec. No tool exists to update the image tag safely; human intervention is needed to fix the deployment configuration.

## Fix Result

- **Status:** No automated fix available -- manual action required.


## Prevention / Monitoring Recommendations

- Validate image tags against Docker Hub before deploying
- Use image registry tags with proper naming conventions (e.g., nginx:latest instead of custom tags)
