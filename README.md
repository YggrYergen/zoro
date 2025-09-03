# zoro

zoro is a CLI that assembles a rich, reproducible prompt to drive AI-assisted code changes. It packages your task/instructions together with a curated snapshot of your repository (structure + selected file contents), and optionally calls OpenAI’s Responses API to produce a patch in standard Git diff format.

It is designed to be:
- Modular and resilient: small, focused components.
- Explicit: all inputs and outputs are files in your workspace.
- Guardrailed: token budgeting and logging prevent costly mistakes.

This README documents every feature in the current codebase, all CLI flags, the configuration format, how the interactive file picker works, how API calls are made, and what will be built next.

--------------------------------------------------------------------------------

## Contents

- What zoro does
- How it works (modules and responsibilities)
- Installation
- Quick start
- CLI reference (all flags)
- Outputs and file formats
- Interactive file selection (persisted decisions)
- Configuration file (reporter_config.json)
- Token budgeting and API behavior
- Logging and diagnostics
- Programmatic notes (module-by-module)
- Known behaviors and quirks
- Troubleshooting
- Roadmap (planned next features)
- License

--------------------------------------------------------------------------------

## What zoro does

Given a project directory and a task file, zoro produces a single Markdown prompt file (current_step.md) with this structure:

1) System instructions (PatchPilot guidance for patch-only output; omitted with --no-diff)
2) User instructions (your task)
3) System general description (optional SRS/SAD)
4) Current codebase structure (tree view of included items)
5) Current codebase files (each included file’s contents with clear delimiters)

Optionally, zoro can immediately call the OpenAI Responses API and save the result:
- Default mode: patch output written to current_dif.md and o.diff
- --no-diff mode: regular text output written to response.md

The tool persists your interactive include/exclude decisions (per folder and file) in reporter_config.json so future runs can reuse them.

--------------------------------------------------------------------------------

## How it works (modules and responsibilities)

- reporter/cli.py
  - Entry point exposed as the zoro command.
  - Parses CLI flags, loads configuration, drives context collection, builds the prompt, and optionally calls the OpenAI API.
  - Enforces a strict prompt section order.
  - Handles token budgeting and output file writing.

- reporter/context_builder.py
  - Collects the project’s file structure and contents.
  - Two code paths exist:
    - Interactive picker (currently used by the CLI): asks Y/N per folder/file, persists decisions.
    - Non-interactive helpers (list_files/read_files): honor include/exclude patterns and size limits.
  - Detects binary files and avoids dumping binary data into the prompt.

- reporter/config.py
  - Loads environment variables (OPENAI_API_KEY) and reporter_config.json.
  - Defines IncludeExcludeConfig (patterns and limits) and ReporterConfig (top-level config).

- reporter/openai_client.py
  - A thin wrapper over the OpenAI Responses API with:
    - Token estimation (tiktoken if available; heuristic otherwise).
    - Cost estimation (based on GPT-4o published rates).
    - Response normalization across possible SDK response shapes.
    - Retry logic for parameters that some models reject.

--------------------------------------------------------------------------------

## Installation

Prerequisites:
- Python 3.9+
- An OpenAI API key if you plan to call the API (set OPENAI_API_KEY in your environment)

Install from the project root:
- pip install -e .  (development)
- or build a wheel with Hatch and install the wheel.

This installs the zoro CLI entrypoint.

--------------------------------------------------------------------------------

## Quick start

- Put your task in task.md (or message.md, task.txt, message.txt).
- From your project root, run:

  - Generate prompt only:
    - zoro . --message task.md --system-description SRS.md

  - Generate prompt and call the API to produce a patch:
    - zoro . --message task.md --call-api

  - Generate prompt and call the API for a plain text response (no patch):
    - zoro . --message task.md --system-description SRS.md --call-api --no-diff

On first run (or when decisions are not reused), you will be prompted interactively to include/exclude directories and files. Press y or n for each prompt. Your choices are stored in reporter_config.json.

--------------------------------------------------------------------------------

## CLI reference (all flags)

Positional:
- root (Path) – Required. Root directory of the project to build the prompt for. Note: the interactive picker scans the current working directory; see “Known behaviors” below.

Options:
- --message PATH
  - File containing your user instructions.
  - If omitted, zoro looks for task.md, message.md, task.txt, or message.txt under root.
- --system-description PATH
  - Optional file with SRS/SAD or high-level system description.
- --summaries PATH
  - Optional file with precomputed summaries. Currently accepted for backward compatibility but not injected in the output by the new prompt spec.
- --output PATH (default: current_step.md)
  - Where to write the assembled prompt.
- --call-api
  - After generating the prompt, call the OpenAI Responses API and save the output. See “Outputs”.
- --no-diff
  - Compose the prompt without PatchPilot system instructions. When combined with --call-api, request a normal text response instead of a patch. Output goes to response.md.
- --model NAME (default: gpt-4o)
  - Model to use when calling the API. Note: when --call-api is used, the CLI currently overrides this to gpt-5 by policy. See “Token budgeting and API behavior”.
- --max-output-tokens INT (default: 120000)
  - Upper bound for the model’s output tokens (also bounds internal reasoning tokens for some models).
- --total-token-budget INT (default: 120000)
  - Total budget: input + reasoning + output. The CLI caps max-output-tokens to keep within this budget.
- --input-token-limit INT (default: 77777)
  - Hard cap for input tokens. If the prompt exceeds this, the run exits with code 2.
- --reasoning-effort {low,medium,high}
  - Optional. Used by models that support it. When --call-api is set, the CLI enforces high regardless of this flag.
- --previous-response-id STR
  - Optional OpenAI Responses API response ID for continuation.
- --log-level {DEBUG,INFO,WARNING,ERROR}
  - Logging verbosity. Defaults to env REPORTER_LOGLEVEL or INFO.

Exit codes:
- 0 success
- 1 generic error (missing files, I/O, network/API, etc.)
- 2 token budgeting guardrails triggered (input tokens exceed limit or no budget remains)

--------------------------------------------------------------------------------

## Outputs and file formats

Files created in the working directory where you run zoro:

- current_step.md
  - The assembled prompt you can inspect, edit, and version.
- If --call-api and default (diff-like) mode:
  - current_dif.md and a.diff, both containing the patch output. A directory's current_diff.md gets overwritten each run; a.diff jumps to the next abailavle letter alphabetically.
- If --call-api with --no-diff:
  - response.md containing the plain text reply from the model.

Note: filenames current_dif.md and o.diff are intentionally both written for convenience.

--------------------------------------------------------------------------------

## Interactive file selection (persisted decisions)

- zoro asks per directory and file whether to include it.
- Prompts are in Spanish and accept y/n (lowercase) for yes/no. Press y to include, n to skip.
- Decisions are persisted to reporter_config.json as boolean keys:
  - dir::path → whether a folder should be traversed/included
  - file::path → whether that file’s contents should be included
- On subsequent runs, zoro offers to reuse previous decisions.
- Files recognized as binary are not dumped into the prompt; a placeholder note is inserted instead.

Tree rendering:
- The “Current codebase structure” section lists included directories (with trailing /) and files, indented by depth.

File content blocks:
- Each included file is wrapped with clear delimiters:
  - --- # inicio archivo path/to/file.ext; LOC 0 ---
  - [file contents]
  - --- # fin archivo path/to/file.ext; LOC N ---
- LOC shows the real total number of lines in the file as counted on disk. In interactive mode, contents are not truncated by size limits.

--------------------------------------------------------------------------------

## Configuration file (reporter_config.json)

This file is both a home for:
- Persisted interactive decisions, and
- Non-interactive include/exclude patterns and size limits.

Shape:
- Interactive decisions:
  - "dir::some/path": true/false
  - "file::some/path/file.py": true/false
- Non-interactive config block:
  - include_exclude: {
      "include_patterns": ["*", "**/*"],
      "exclude_patterns": [
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
        "current_step.md",
        "diff.patch"
      ],
      "max_file_lines": 400,
      "max_total_characters": 0
    }

Notes:
- The interactive builder (currently used by the CLI) stores decisions at the top level alongside include_exclude. zoro preserves non-decision keys on write-back.
- The non-interactive helpers (list_files, read_files) in ContextBuilder honor include_exclude; they are ready for future non-interactive modes.

--------------------------------------------------------------------------------

## Token budgeting and API behavior

When you run with --call-api:
- The CLI estimates input tokens (using tiktoken when available; otherwise a heuristic).
- If input tokens exceed --input-token-limit, execution stops (exit 2).
- It computes the remaining budget from --total-token-budget and caps --max-output-tokens accordingly.
- For cost transparency, it estimates request cost using GPT‑4o rates: $0.005 per 1K input tokens and $0.015 per 1K output tokens.

Model selection and enforced policy:
- Although you can pass --model, the current CLI enforces gpt-5 when --call-api is used, with reasoning_effort=high and verbosity=high. Temperature is set to 0 when supported; if not supported, zoro retries without it automatically.
- The wrapper normalizes response shapes and extracts text content robustly. It also polls the Responses API if the first response is not terminal.

Environment:
- OPENAI_API_KEY must be set to call the API.

Outputs from API calls:
- Default mode: patch output → current_dif.md and o.diff
- With --no-diff: plain text → response.md

--------------------------------------------------------------------------------

## Logging and diagnostics

- Configure with --log-level or env REPORTER_LOGLEVEL.
- DEBUG level enables extra SDK and HTTP traces (httpx, openai, and httpcore).
- The OpenAI client prints the responses.create signature once per call (debug aid).
- If the API reports “completed” but no text is extracted, zoro logs a warning and suggests enabling DEBUG to inspect raw response data.

--------------------------------------------------------------------------------

## Programmatic notes (module-by-module)

- reporter/cli.py
  - build_prompt(...) assembles the Markdown. The “summaries” argument is retained for CLI compatibility but not injected into the output.
  - The “System instructions” block sets up PatchPilot to expect patch-only outputs (unless --no-diff).
  - Token budgeting logic: rejects oversized inputs, caps output tokens, writes outputs atomically.

- reporter/context_builder.py
  - collect_interactive() runs from the current working directory, not the root positional argument. It persists include/exclude decisions and builds both structure and file entries.
  - list_files()/read_files(): non-interactive path that honors include_exclude patterns and global limits (max_file_lines and max_total_characters).

- reporter/config.py
  - ReporterConfig.load(root) reads reporter_config.json located under the provided root path, merges with env OPENAI_API_KEY, and returns a ReporterConfig.

- reporter/openai_client.py
  - estimate_tokens(): uses tiktoken when possible, fallback is #chars / 4 heuristic.
  - call_responses_api(): builds a Responses API request, strips/adjusts parameters that the selected model doesn’t accept, polls if needed, and ensures response.output_text is present.
  - extract_output_text(): robustly normalizes different SDK response shapes.

--------------------------------------------------------------------------------

## Known behaviors and quirks

- Interactive scanner uses the current working directory:
  - The positional root argument is used to locate task files and load configuration, but the interactive traversal uses Path.cwd(). The recommended usage is to cd into your repo root and run zoro from there.
- Prompts for inclusion are localized to Spanish and expect lowercase y/n input.
- In interactive mode, file content is not truncated by the include_exclude size limits (max_file_lines and max_total_characters). Those limits are enforced only by the non-interactive helpers.
- Output filenames after API calls are current_dif.md and o.diff (two files with identical contents).
- The CLI enforces gpt-5 when --call-api is used, regardless of --model. If your environment doesn’t support this model, calls will fail; see Troubleshooting for how to proceed during development.

--------------------------------------------------------------------------------

## Troubleshooting

- The OpenAI package is not installed
  - Error: ImportError about openai not installed.
  - Fix: pip install openai tiktoken

- API call fails with model not found or unsupported parameters
  - The CLI enforces gpt-5 with reasoning_effort=high and verbosity=high. If your account or SDK version doesn’t recognize these, update the SDK, change the enforced model locally, or wait for the roadmap item that restores full model control.

- “Input token count exceeds limit”
  - Reduce included files via the interactive picker, or temporarily move large files out of scope. You can also split your request and run multiple smaller sessions.

- Empty output but status=completed
  - Enable --log-level DEBUG to inspect the raw response shape. The client attempts normalization, but some models may require different fields.

- Interactive prompts repeat too often
  - zoro asks at the start if you want to reuse saved decisions. Answer y to avoid re-answering every item.

--------------------------------------------------------------------------------

## Roadmap (planned next features)

The following capabilities are planned and will be integrated with zoro’s existing flow. Public interfaces and flags may evolve slightly during implementation.

1) Web control surface for file selection (backed by reporter_config.json)
- What: A local web UI that displays your repo tree and lets you include/exclude files and folders with checkboxes. Your choices persist to reporter_config.json, same schema the CLI uses today.
- Why: Faster, less error-prone than terminal prompts; supports bulk actions and previews before writing.
- Proposed usage:
  - zoro web
  - zoro web --host 127.0.0.1 --port 8787 --root .
- Features:
  - Live search and filters (e.g., show only large files, only unknown extensions).
  - Import/export of selection presets.
  - Read-only preview of file contents to aid selection.
  - One-click “select all matching include_patterns” or “apply exclude_patterns”.
  - Safe write-back to reporter_config.json with diff/preview.

2) Summarization command for repositories, directories, and files
- What: Generate AI summaries for each directory and file to optimize context usage (choose what to include at a glance).
- Outputs:
  - summaries/FILE_SUMMARIES.jsonl
  - summaries/DIR_SUMMARIES.json
  - summaries/SUMMARY.md (human-friendly aggregate)
- Proposed usage:
  - zoro summarize .
  - zoro summarize . --model gpt-4o --max-chunk-tokens 8000 --workers 4
  - zoro summarize path/to/dir --include "**/*.py" --exclude "**/tests/**"
- Features:
  - Chunking for large files, merging chunk summaries into a final per-file summary.
  - Optional top-k “most critical files” report based on dependency signals, size, and content heuristics.
  - Integration with the web UI: display summaries inline and toggle inclusion.
  - Optionally inject summaries into the prompt as a separate section or replace some raw file contents when token budgets are tight (user-controlled).

3) Full model/parameter control in every route and command
- What: Every CLI route (prompt build, summarize, web, agent) will accept and honor model parameters rather than enforcing a single smartest model. This lets users test performance and speed while monitoring costs.
- Parameters:
  - --model, --temperature, --top-p, --max-output-tokens, --reasoning-effort, --verbosity, --seed
- Policy:
  - The current enforced gpt-5 policy will become configurable with a --no-enforce-policy flag or equivalent configuration in reporter_config.json.

4) Agent interface with sequential and parallel tool use
- What: A zoro “agent” that can orchestrate multiple runs of prompt building, summarization, and patch generation both sequentially and in parallel (fan-out/fan-in).
- Proposed usage:
  - zoro agent run --plan plan.yaml
  - zoro agent run --parallel 4 --goal "Refactor and add tests"
- Features:
  - Task graphs (DAG) with dependencies.
  - Parallel summarization across files/directories, then parallelized patch proposals, then merge/resolve step.
  - Pluggable strategies: “conservative”, “aggressive”, “tests-first”.
  - Optional confirmation gates after each stage via the web control surface.

5) Non-interactive mode that honors include_exclude patterns
- What: A no-prompt mode that uses IncludeExcludeConfig to compute the file set.
- Proposed usage:
  - zoro . --non-interactive
  - zoro . --non-interactive --respect-limits
- Behavior:
  - Uses list_files/read_files with optional enforcement of max_file_lines and max_total_characters.
  - When limits truncate content, zoro annotates the prompt with “[...truncated...]”.

6) Support for streamed responses (seeing the chain of thought reduces stress... we've benn told)

7) Support for background mode and response retrieval so no response gets lost.

--------------------------------------------------------------------------------

## Examples

- Build prompt only, with SRS:
  - zoro . --message task.md --system-description SRS.md

- Build prompt and call API for a patch:
  - zoro . --message task.md --call-api

- Build prompt and call API for a plain text response:
  - zoro . --message task.md --system-description SRS.md --call-api --no-diff

- Increase verbosity for diagnostics:
  - zoro . --message task.md --call-api --log-level DEBUG

- Restrict token budgets:
  - zoro . --message task.md --call-api --input-token-limit 50000 --total-token-budget 80000 --max-output-tokens 20000

--------------------------------------------------------------------------------

## License

MIT License. See the license field in pyproject.toml.

--------------------------------------------------------------------------------

## Appendix: Prompt layout specification

The generated current_step.md follows this order:

1) System instructions
- Included by default (PatchPilot guidance for patch-only responses).
- Omitted if you pass --no-diff.

2) User instructions
- Contents of the file passed to --message (or auto-detected task.md/message.md/task.txt/message.txt).

3) System general description
- Optional; contents of --system-description.

4) Current codebase structure
- A concise tree of included directories and files.

5) Current codebase files
- One section per included file, wrapped by “inicio archivo …/fin archivo …” markers with LOC metadata.

This structure is intentionally stable so downstream tools can rely on it.