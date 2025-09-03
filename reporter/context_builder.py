"""
Context builder for the reporter CLI.

This module encapsulates logic for collecting and assembling the contextual
information necessary to generate a useful prompt for code generation.
It traverses the project directory, selects files based on include and
exclude patterns, reads their contents subject to configured limits, and
builds a human‑readable representation of both the directory structure and
the file contents.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Set
from datetime import datetime

from .config import IncludeExcludeConfig

CONFIG_FILE = "reporter_config.json"


def es_binario(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as file:
            chunk = file.read(1024)
            return b"\0" in chunk
    except Exception:
        return False

class ContextBuilder:
    """Constructs context for the prompt based on a project directory and configuration."""

    def __init__(self, base_dir: Path, config: IncludeExcludeConfig) -> None:
        self.base_dir = base_dir
        self.config = config

    @dataclass
    class FileEntry:
        """Container for included file data."""
        rel_path: str
        content: str
        loc_total: int
        language: str
    @staticmethod
    def _prompt_yes_no(message: str) -> bool:
        while True:
            resp = input(message).strip().lower()
            if resp in ("y", "n"):
                return resp == "y"

    def _is_excluded(self, rel_path: str) -> bool:
        """Return True if the given path (file or directory) matches any exclude pattern."""
        for pattern in self.config.exclude_patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
        return False

    def _should_include(self, rel_path: str) -> bool:
        """Return True if the file should be included according to patterns."""
        # Exclude patterns override include patterns
        for pattern in self.config.exclude_patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return False
        # If include_patterns is empty treat as include everything
        if not self.config.include_patterns:
            return True
        for pattern in self.config.include_patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
        return False

    def list_files(self) -> List[str]:
        """Generate a sorted list of relative file paths that are considered for inclusion."""
        files: List[str] = []
        for root, dirs, filenames in os.walk(self.base_dir, topdown=True):
            # Compute relative directory in posix style
            rel_dir = os.path.relpath(root, self.base_dir).replace(os.sep, "/")
            # Ensure we descend into directories unless they are excluded.
            # Apply ONLY excludes to dirs, not includes.
            kept_dirs = []
            for d in dirs:
                if rel_dir == ".":
                    dir_rel = f"{d}/"
                else:
                    dir_rel = f"{rel_dir}/{d}/"
                if not self._is_excluded(dir_rel):
                    kept_dirs.append(d)
            dirs[:] = kept_dirs
            for filename in filenames:
                rel_path = os.path.relpath(os.path.join(root, filename), self.base_dir)
                # Normalize Windows style separators to '/' for glob matching
                rel_norm = rel_path.replace(os.sep, "/")
                if self._should_include(rel_norm):
                    files.append(rel_norm)
        files.sort()
        return files

    def build_file_tree_section(self, files: List[str]) -> str:
        """Return a string representation of the project's file tree.

        The representation includes directories (with trailing '/')
        and files, with indentation reflecting depth. Only directories
        that are ancestors of the included files are shown.
        """
        # Derive directories and direct parent relationships
        dirs: Set[str] = set()
        dir_files: Dict[str, List[str]] = {}
        dir_children: Dict[str, Set[str]] = {}

        def parent_dir(path: str) -> str:
            return path.rsplit("/", 1)[0] if "/" in path else ""

        def base_name(path: str) -> str:
            return path.rsplit("/", 1)[1] if "/" in path else path

        # Collect files per directory and all ancestor directories
        for f in files:
            d = parent_dir(f)
            dir_files.setdefault(d, []).append(base_name(f))
            # add ancestors of d
            if d:
                parts = d.split("/")
                for i in range(1, len(parts) + 1):
                    dirs.add("/".join(parts[:i]))
        # Build directory children mapping
        for d in list(dirs):
            p = parent_dir(d)
            name = base_name(d)
            dir_children.setdefault(p, set()).add(name)

        # Ensure root-level files are represented
        dir_files.setdefault("", [])
        dir_children.setdefault("", set())

        # Sort helper
        def render_dir(current: str, depth: int, lines: List[str]) -> None:
            # Render child directories first
            children_dirs = sorted(dir_children.get(current, set()))
            for name in children_dirs:
                full = f"{current}/{name}" if current else name
                indent = "  " * depth
                lines.append(f"{indent}- {name}/")
                render_dir(full, depth + 1, lines)
            # Render files in this directory
            for fname in sorted(dir_files.get(current, [])):
                indent = "  " * depth
                lines.append(f"{indent}- {fname}")

        lines: List[str] = []
        # At root: render all top-level directories and files
        render_dir("", 0, lines)
        return "\n".join(lines)

    def _detect_language(self, rel_path: str) -> str:
        """Infer a language identifier for markdown fences based on extension."""
        ext = Path(rel_path).suffix.lower().lstrip(".")
        mapping = {
            "py": "py",
            "ts": "ts",
            "tsx": "tsx",
            "js": "js",
            "jsx": "jsx",
            "json": "json",
            "md": "md",
            "yml": "yaml",
            "yaml": "yaml",
            "toml": "toml",
            "sh": "sh",
            "bash": "bash",
            "ps1": "powershell",
            "bat": "bat",
            "ini": "ini",
            "cfg": "ini",
            "xml": "xml",
            "html": "html",
            "css": "css",
            "scss": "scss",
            "less": "less",
            "java": "java",
            "kt": "kotlin",
            "go": "go",
            "rs": "rust",
            "c": "c",
            "h": "c",
            "cpp": "cpp",
            "cc": "cpp",
            "cxx": "cpp",
            "hpp": "cpp",
            "m": "objectivec",
            "mm": "objectivecpp",
            "swift": "swift",
            "php": "php",
            "rb": "ruby",
            "pl": "perl",
            "sql": "sql",
            "proto": "proto",
        }
        return mapping.get(ext, "")

    def count_file_lines(self, rel_path: str) -> int:
        """Count the real total number of lines in a file, ignoring any truncation settings."""
        abs_path = self.base_dir / rel_path
        try:
            with abs_path.open("r", encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def read_files(self, files: List[str]) -> List["ContextBuilder.FileEntry"]:
        """Read the contents of the provided files subject to configured limits.

        Returns a list of FileEntry objects `(rel_path, content, loc_total, language)`.
        When `max_file_lines` is set and non‑zero, only the first `max_file_lines` lines
        of each file are included in `content`. If `max_total_characters` is set and non‑zero,
        reading stops when the cumulative length of all included contents exceeds this threshold.
        """
        included: List[ContextBuilder.FileEntry] = []
        total_chars = 0
        for rel_path in files:
            abs_path = self.base_dir / rel_path
            loc_total = self.count_file_lines(rel_path)
            language = self._detect_language(rel_path)
            try:
                with abs_path.open("r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except Exception as exc:
                # If file cannot be read, skip and annotate.
                included.append(ContextBuilder.FileEntry(rel_path, f"[Error reading file: {exc}]", loc_total, language))
                continue
            # Respect max_file_lines
            if self.config.max_file_lines > 0:
                selected_lines = lines[: self.config.max_file_lines]
                truncated = len(lines) > self.config.max_file_lines
            else:
                selected_lines = lines
                truncated = False
            content = "".join(selected_lines)
            # Check cumulative character limit
            if self.config.max_total_characters > 0 and total_chars + len(content) > self.config.max_total_characters:
                # Stop further inclusion and note truncation
                break
            total_chars += len(content)
            if truncated:
                content += "\n[...truncated...]\n"
            included.append(ContextBuilder.FileEntry(rel_path, content, loc_total, language))
        return included

    def collect_interactive(self) -> Tuple[str, List["ContextBuilder.FileEntry"]]:
        """Interactively traverse the current working directory to decide which
        directories and files to include. Decisions are persisted in reporter_config.json.

        Returns:
            Tuple[str, List[FileEntry]]: (structure_markdown, file_entries)
        """
        scan_root = Path.cwd()
        config_path = scan_root / CONFIG_FILE

        # Load existing configuration data (if any)
        existing_data: Dict[str, object] = {}
        if config_path.exists():
            try:
                with config_path.open("r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except Exception:
                existing_data = {}

        # Ask whether to reuse saved decisions
        decisions: Dict[str, bool] = {}
        if existing_data:
            reuse = self._prompt_yes_no("Se encontró reporter_config.json. ¿Deseas reutilizar la configuración previa (Y/N)? ")
            if reuse:
                for k, v in existing_data.items():
                    if isinstance(v, bool) and (k.startswith("dir::") or k.startswith("file::")):
                        decisions[k] = v
        # Build structure and content by traversing from CWD
        structure_lines: List[str] = []
        file_entries: List[ContextBuilder.FileEntry] = []

        def preguntar_inclusion(tipo: str, nombre: str) -> bool:
            return self._prompt_yes_no(f"¿Deseas incluir {tipo} '{nombre}' (Y/N)? ")

        def norm_rel(p: Path) -> str:
            # relpath relative to CWD, normalized with forward slashes for consistency
            return os.path.relpath(str(p)).replace(os.sep, "/")

        def count_lines_abs(p: Path) -> int:
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    return sum(1 for _ in f)
            except Exception:
                return 0

        def traverse(current_dir: Path, depth: int = 0) -> None:
            try:
                items = sorted(os.listdir(current_dir))
            except Exception:
                return

            # First process directories
            for name in [n for n in items if (current_dir / n).is_dir()]:
                abs_dir = current_dir / name
                rel_dir = norm_rel(abs_dir)
                key = f"dir::{rel_dir}"
                include = decisions.get(key)
                if include is None:
                    include = preguntar_inclusion("la carpeta", rel_dir)
                    decisions[key] = bool(include)
                if include:
                    structure_lines.append(f"{'  ' * depth}- {name}/")
                    traverse(abs_dir, depth + 1)

            # Then process files
            for name in [n for n in items if (current_dir / n).is_file()]:
                abs_file = current_dir / name
                rel_file = norm_rel(abs_file)
                key = f"file::{rel_file}"
                include = decisions.get(key)
                if include is None:
                    include = preguntar_inclusion("el archivo", rel_file)
                    decisions[key] = bool(include)
                if include:
                    structure_lines.append(f"{'  ' * depth}- {name}")
                    # Prepare file entry (no truncation, detect binaries)
                    language = self._detect_language(rel_file)
                    if es_binario(str(abs_file)):
                        content = "(Archivo binario, no se muestra el contenido)"
                        loc_total = count_lines_abs(abs_file)
                    else:
                        try:
                            with abs_file.open("r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                        except Exception as exc:
                            content = f"[Error reading file: {exc}]"
                        loc_total = count_lines_abs(abs_file)
                    file_entries.append(ContextBuilder.FileEntry(rel_file, content, loc_total, language))

        traverse(scan_root, 0)

        # Persist updated decisions back to reporter_config.json
        # Preserve any non-decision keys (e.g., include_exclude) from existing_data
        new_data: Dict[str, object] = {}
        if isinstance(existing_data, dict):
            # Keep existing non-decision keys
            for k, v in existing_data.items():
                if not (isinstance(v, bool) and (k.startswith("dir::") or k.startswith("file::"))):
                    # Preserve known config blocks like include_exclude
                    if k != CONFIG_FILE:  # avoid accidental recursion
                        new_data[k] = v
        # Add current session decisions
        new_data.update(decisions)
        try:
            with config_path.open("w", encoding="utf-8") as f:
                json.dump(new_data, f, indent=2, ensure_ascii=False)
        except Exception:
            # Silently ignore write errors; interactive choices will be asked again next time
            pass

        return ("\n".join(structure_lines).rstrip(), file_entries)