"""Background daemon: start/stop/status plus the watch loop itself.

`aiops start` re-launches this package as `python -m aiops run ...` in a new session
(detached from the terminal), with stdout/stderr going to the log file and its PID in a
pidfile. The loop never applies fixes: it can't ask for confirmation, so proposed fixes
are queued as `pending_fix` for `aiops approve`.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

from aiops import engine, k8s
from aiops.config import PROJECT_DIR, Settings
from aiops.state import StateStore

log = logging.getLogger("aiops.daemon")


def read_pid(settings: Settings) -> int | None:
    try:
        pid = int(settings.pid_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        settings.pid_file.unlink(missing_ok=True)  # stale pidfile
        return None
    except PermissionError:
        pass
    return pid


def start(settings: Settings, run_args: list[str]) -> int:
    pid = read_pid(settings)
    if pid:
        raise RuntimeError(f"AIops daemon already running (PID {pid}). Use `aiops stop` first.")
    settings.home.mkdir(parents=True, exist_ok=True)
    log_fh = open(settings.log_file, "a")
    env = {**os.environ, "PYTHONUNBUFFERED": "1",
           "PYTHONPATH": os.pathsep.join(filter(None, [str(PROJECT_DIR), os.environ.get("PYTHONPATH")]))}
    proc = subprocess.Popen(
        [sys.executable, "-m", "aiops", "run", *run_args],
        stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
        start_new_session=True, env=env, cwd=str(PROJECT_DIR),
    )
    settings.pid_file.write_text(str(proc.pid))
    time.sleep(1.5)
    if proc.poll() is not None:
        settings.pid_file.unlink(missing_ok=True)
        raise RuntimeError(f"Daemon exited immediately (code {proc.returncode}); see {settings.log_file}")
    return proc.pid


def stop(settings: Settings, timeout: float = 15) -> int | None:
    pid = read_pid(settings)
    if not pid:
        return None
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.3)
    else:
        os.kill(pid, signal.SIGKILL)  # stuck in a long LLM call
    settings.pid_file.unlink(missing_ok=True)
    return pid


def run_forever(settings: Settings) -> None:
    """The watch loop (runs in the foreground; `start` runs it detached)."""
    stopping = False

    def _handle(signum, _frame):
        nonlocal stopping
        log.info("Received signal %s, shutting down after current step", signum)
        stopping = True
        if signum == signal.SIGINT:
            raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    if not settings.pid_file.exists():
        settings.pid_file.parent.mkdir(parents=True, exist_ok=True)
        settings.pid_file.write_text(str(os.getpid()))

    store = StateStore(settings.state_file)
    log.info("AIops daemon started: PID %s, model=%s, interval=%ss, namespace=%s, sops=%s",
             os.getpid(), settings.model if settings.use_llm else "rules-only",
             settings.interval_seconds, settings.namespace or "all", settings.sops_dir)
    try:
        while not stopping:
            started = time.time()
            try:
                ctx = k8s.assert_kind_context()
                report = engine.run_cycle(settings, store, progress=log.info)
                log.info("Cycle done on %s: %d anomalies, %d new, %d ongoing, %d resolved",
                         ctx, report.detected, len(report.new), report.ongoing, len(report.resolved))
            except k8s.NotKindClusterError as exc:
                log.warning("Waiting for a kind cluster: %s", exc)
            except Exception:
                log.exception("Scan cycle failed; retrying next interval")
            while not stopping and time.time() - started < settings.interval_seconds:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if settings.pid_file.read_text().strip() == str(os.getpid()):
                settings.pid_file.unlink()
        except FileNotFoundError:
            pass
        log.info("AIops daemon stopped")
