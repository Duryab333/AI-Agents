# AIOps Project Ideas

A collection of beginner-to-intermediate AIOps project ideas — using AI/LLM agents to automate, monitor, and troubleshoot infrastructure and DevOps workflows.

## 1. Log Anomaly Detector
Ingest application/system logs, use an LLM (or a lightweight ML model) to flag unusual patterns — error spikes, new exception types, latency outliers — and post a summary to Slack/Teams instead of raw noise.
- **Skills:** log parsing, embeddings/clustering or simple statistical baselines, alerting integration.

## 2. Incident Triage Assistant
An agent that watches incoming alerts (PagerDuty/Opsgenie webhook or a shared queue), correlates them with recent deploys and known runbooks, and drafts a first-pass root-cause summary + suggested next steps for the on-call engineer.
- **Skills:** tool-calling agent, retrieval over runbooks/past incidents, webhook handling.

## 3. Self-Healing Docker/K8s Agent
Extend the existing `docker-agent` MCP server: an agent that watches container health/restarts, and when it detects a crash loop, automatically pulls the last N log lines, diagnoses the likely cause, and either restarts the container or opens a ticket with its findings.
- **Skills:** MCP tools, Docker/K8s CLI, guardrails + human-approval before destructive actions.

## 4. ChatOps Deployment Assistant
A Slack/Teams bot backed by an LLM agent that can answer "what's deployed in prod right now?", trigger a rollback, or check CI pipeline status — all via natural language, with tool calls to your CI/CD and orchestration APIs.
- **Skills:** chat integration, tool-calling, scoped permissions (read-only vs. action-taking).

## 5. Cost & Resource Optimization Advisor
An agent that periodically reviews cloud resource usage (CPU/memory/idle instances) and generates a plain-English weekly report of underused resources and estimated savings, with optional auto-tagging of candidates for review.
- **Skills:** cloud provider APIs (AWS/GCP/Azure), scheduled agent runs, reporting.

## 6. Auto-Generated Postmortem Writer
After an incident is resolved, an agent gathers the alert timeline, relevant logs, and chat thread, then drafts a structured postmortem (timeline, root cause, impact, action items) for a human to review and finalize.
- **Skills:** multi-source retrieval, summarization, structured document generation.

## 7. Config Drift Detector
An agent that periodically diffs live infrastructure config (Terraform state, K8s manifests, etc.) against the source-of-truth repo, and explains in plain language what changed and whether it looks risky.
- **Skills:** IaC parsing, diffing, risk classification.

## 8. CI/CD Failure Explainer
Hook into your CI pipeline; when a build/test fails, the agent reads the failure logs and posts a concise "here's likely why it failed and how to fix it" comment on the PR.
- **Skills:** CI API integration, log summarization, PR commenting.

---

### Suggested build order (easiest → hardest)
1. Log Anomaly Detector
2. CI/CD Failure Explainer
3. ChatOps Deployment Assistant
4. Self-Healing Docker/K8s Agent
5. Incident Triage Assistant
6. Config Drift Detector
7. Auto-Generated Postmortem Writer
8. Cost & Resource Optimization Advisor
