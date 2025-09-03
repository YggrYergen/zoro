"""
Configuration management for the reporter CLI.

This module centralizes loading of configuration values from environment
variables and JSON configuration files.  It defines sane defaults and
provides an interface for the rest of the application to query these
settings.

The configuration file `reporter_config.json` allows users to control
which files are included in the prompt.  For example, it can specify
patterns to include or exclude when traversing the project tree, and
limits on the number of lines or total characters per file.  If the
configuration file is absent, reasonable defaults are used.
 
Defaults are conservative and exclude common generated artifacts
(.venv, __pycache__, build, dist, egg-info, caches) and reporter outputs
(current_step.md, diff.patch).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List


@dataclass
class IncludeExcludeConfig:
    """Configuration for selecting files to include in the prompt.

    Attributes
    ----------
    include_patterns: List[str]
        Glob patterns describing which files should be considered for
        inclusion.  Patterns are evaluated relative to the project root.

    exclude_patterns: List[str]
        Glob patterns describing files or directories to exclude from
        consideration.  Exclusions override inclusions.

    max_file_lines: int
        Maximum number of lines to include from any single file.  If a
        file is longer, only the first `max_file_lines` lines are
        included and the remainder is omitted.  Set to 0 to include
        complete files regardless of length.

    max_total_characters: int
        Maximum total number of characters across all included file
        contents.  If this limit is reached while accumulating file
        contents, subsequent files are skipped.  Set to 0 to disable
        this limit.
    """

    # Include both root-level files and deeper files.  '**/*' does not match
    # files in the root directory when using fnmatch.fnmatch, so add '*' as
    # an additional pattern.  See context_builder._should_include for details.
    include_patterns: List[str] = field(default_factory=lambda: ["*", "**/*"])
    exclude_patterns: List[str] = field(default_factory=lambda: [
        "**/node_modules/**",
        "**/.git/**",
        "**/__pycache__/**",
        "**/.venv/**",
        "**/dist/**",
        "**/build/**",
        "**/*.egg-info/**",
        "**/.pytest_cache/**",
        "**/.mypy_cache/**",
        "**/.ruff_cache/**",
        "**/coverage/**",
        "Thumbs.db",
        "desktop.ini",
        "**/.DS_Store",
        # Generated files from reporter should not be included in context
        "current_step.md",
        "diff.patch",
    ])
    max_file_lines: int = 400
    max_total_characters: int = 0


@dataclass
class ReporterConfig:
    """Top‑level configuration for the reporter CLI.

    Attributes
    ----------
    openai_api_key: str
        The API key used to authenticate with OpenAI.  This is loaded
        from the environment by default.  If not set, API calls will
        fail unless a key is provided at runtime.

    include_exclude: IncludeExcludeConfig
        Configuration controlling which files are included in the prompt.

    reporter_config_path: Path
        Path to the configuration file from which this object was
        loaded.  Retained for logging and debugging purposes.
    """

    openai_api_key: str | None
    include_exclude: IncludeExcludeConfig
    reporter_config_path: Path | None

    @staticmethod
    def load(base_dir: Path) -> "ReporterConfig":
        """Load configuration values from `reporter_config.json` and the
        environment.

        Parameters
        ----------
        base_dir: Path
            The directory where the CLI command is being executed.  This
            directory is scanned for a `reporter_config.json` file.

        Returns
        -------
        ReporterConfig
            A populated configuration object.
        """
        config_path = base_dir / "reporter_config.json"
        include_exclude: IncludeExcludeConfig
        if config_path.exists():
            try:
                with config_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                include_exclude = IncludeExcludeConfig(**data.get("include_exclude", {}))
            except Exception as exc:  # pragma: no cover - user controlled file
                # If the file cannot be parsed, fall back to defaults and log a warning.
                print(f"[reporter] Warning: Failed to parse {config_path}: {exc}")
                include_exclude = IncludeExcludeConfig()
        else:
            include_exclude = IncludeExcludeConfig()

        api_key = os.environ.get("OPENAI_API_KEY")

        return ReporterConfig(
            openai_api_key=api_key,
            include_exclude=include_exclude,
            reporter_config_path=config_path if config_path.exists() else None,
        )