"""
Reporter CLI package.

This package provides a command-line interface (CLI) for assembling a rich
contextual prompt that can be sent to the OpenAI Responses API and Agents
API for code generation.  The resulting prompt includes:

* The user‑supplied instructions describing what to build and where.
* A configurable subset of the project’s file tree and file contents.
* Optional summaries or system descriptions provided by the user.
* The fixed PatchPilot diff instructions required by the code generator
  (omitted when running with the `--no-diff` flag).

The package is designed to be modular and resilient.  Each component
handles a single responsibility, which improves testability and
maintainability.

See `cli.py` for the entry point.
"""

__all__ = [
    "cli",
]