"""Runtime settings. Every value can be overridden by an environment variable or a CLI flag.

Paths are resolved to absolute paths up front so the background daemon keeps writing to
the same place regardless of its working directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    model: str = field(default_factory=lambda: os.environ.get("AIOPS_MODEL", "qwen3:4b"))
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    llm_timeout_seconds: int = field(
        default_factory=lambda: int(os.environ.get("AIOPS_LLM_TIMEOUT", "600"))
    )
    num_ctx: int = field(default_factory=lambda: int(os.environ.get("AIOPS_NUM_CTX", "8192")))
    home: Path = field(
        default_factory=lambda: Path(os.environ.get("AIOPS_HOME", PROJECT_DIR / ".aiops"))
    )
    sops_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("AIOPS_SOPS_DIR", PROJECT_DIR / "sops"))
    )
    interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("AIOPS_INTERVAL", "60"))
    )
    namespace: str | None = None
    restart_threshold: int = 3
    stuck_threshold_seconds: int = 300
    include_system_namespaces: bool = False
    use_llm: bool = True

    def __post_init__(self) -> None:
        self.home = Path(self.home).expanduser().resolve()
        self.sops_dir = Path(self.sops_dir).expanduser().resolve()

    @property
    def state_file(self) -> Path:
        return self.home / "state.json"

    @property
    def pid_file(self) -> Path:
        return self.home / "aiops.pid"

    @property
    def log_file(self) -> Path:
        return self.home / "aiops.log"
