"""
Entry point for the reporter command-line interface (exposed as `zoro`).

The reporter CLI assembles the information necessary to construct a rich
prompt for the OpenAI Responses API.  Given a project directory and a
set of optional metadata files, it collects the relevant source code,
context, and user instructions into a single Markdown file called
`current_step.md`.  Optionally it can also submit the prompt to
OpenAI's Responses API to produce a unified diff and write the result
to output files in the same folder as `current_step.md`.

Usage examples::

    # Generate current_step.md in the current directory
    zoro . --message task.md --system-description SRS.md
    # (alternativa equivalente durante desarrollo)
    # python -m reporter.cli . --message task.md --system-description SRS.md

    # Generate prompt and immediately call the API (requires OPENAI_API_KEY)
    zoro . --message task.md --call-api

    # Generate prompt WITHOUT PatchPilot instructions and call the API
    # expecting free-form text (not a unified diff)
    zoro . --message task.md --system-description SRS.md --call-api --no-diff

This module should not be imported by other modules; it is intended to
be executed directly via the installed console script `zoro`
or, during development, via:
    python -m reporter.cli
from the project root.

The generated current_step.md follows this section order:
1) System instructions (unless --no-diff), 2) User instructions, 3) System general description, 4) Current codebase structure, 5) Current codebase files.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .config import ReporterConfig
from .context_builder import ContextBuilder
from .openai_client import OpenAIClient


# The fixed block of instructions that must always be present in the
# generated prompt.  This string is inserted verbatim and directs the
# downstream code generator to emit unified diffs only.  Do not modify
# these instructions unless you are updating to a new specification.
PATCH_INSTRUCTIONS = (
    "[--- PatchPilot AI Code Generation Instructions ---\n\n"
    "**CONTEXT:** I am using a VS Code extension called PatchPilot. Your primary task when I request code changes is to provide ONLY the modifications in the standard **unified diff format**. Do NOT output the complete modified file(s).\n\n"
    "**CORE RULES FOR ALL CODE RESPONSES:**\n\n"
    "1.  **OUTPUT FORMAT:** MUST be **unified diff**.\n"
    "    *   Example:\n"
    "        ```diff\n"
    "        diff --git a/path/to/file.ext b/path/to/file.ext\n"
    "        --- a/path/to/file.ext\n"
    "        +++ b/path/to/file.ext\n"
    "        @@ -old_start,old_lines +new_start,new_lines @@\n"
    "         context line (unchanged)\n"
    "        -line to remove\n"
    "        +line to add\n"
    "         another context line\n"
    "        ```\n\n"
    "2.  **FILE PATHS:** MUST use correct **relative paths** from the project root in all header lines (`diff --git`, `---`, `+++`).\n"
    "    *   Example: `src/components/Button.tsx`, NOT `Button.tsx` or `/abs/path/to/Button.tsx`.\n\n"
    "3.  **CONTEXT LINES:** MUST include **at least 3 lines** of unchanged context before and after each changed block within a hunk (`@@ ... @@`). Lines starting with a space are context lines.\n\n"
    "4.  **MULTI-FILE CHANGES:** MUST be combined into a **single diff output block**. Each file's changes must be separated by the standard `diff --git ...` header sequence for that file.\n\n"
    "5.  **NEW FILES:** MUST use `/dev/null` as the source file in headers.\n"
    "    *   Example:\n"
    "        ```diff\n"
    "        diff --git a/dev/null b/path/to/new_file.ext\n"
    "        --- /dev/null\n"
    "        +++ b/path/to/new_file.ext\n"
    "        @@ -0,0 +1,5 @@\n"
    "        +new line 1\n"
    "        +new line 2\n"
    "        +new line 3\n"
    "        +new line 4\n"
    "        +new line 5\n"
    "        ```\n\n"
    "6.  **IGNORE MINOR FORMATTING:** You do **not** need to worry about:\n"
    "    *   Line Endings: PatchPilot normalizes LF/CRLF automatically.\n"
    "    *   Leading Spaces: PatchPilot adds missing leading spaces on context lines automatically.\n"
    "    *   Focus on generating the *correct code change logic* within the diff structure.\n\n"
    "**PERSISTENCE:** Please adhere to these rules **consistently** for all subsequent code modification requests in this session without needing further reminders. Respond ONLY with the diff block unless I specifically ask for something else.\n"
    "]"
)


def read_optional_file(path: Optional[Path]) -> str:
    """Return the contents of `path` if it exists, else an empty string."""
    if path is None:
        return ""
    try:
        with path.open("r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as exc:
        return f"[Error reading {path}: {exc}]"


def build_prompt(
    user_instructions: str,
    system_description: str,
    file_tree: str,
    file_entries: List[ContextBuilder.FileEntry],
    summaries: str,  # kept for CLI compatibility; not included in output per new spec
    include_patch_instructions: bool = True,
) -> str:
    """Assemble the final prompt string from its constituent parts."""
    parts: List[str] = []
    # 1) System instructions (PatchPilot) first, unless explicitly disabled
    if include_patch_instructions:
        parts.append(
            "# System instructions\n"
            + PATCH_INSTRUCTIONS.strip()
            + "\n\n# End of system instructions\n ---\n ---\n"
        )
    # 2) User instructions
    parts.append("\n\n\n\n ---\n ---\n# User instructions\n" + user_instructions.strip() + "\n\n# End of user instructions\n ---\n ---\n")
    # 3) System general description (optional)
    if system_description:
        parts.append("\n\n\n\n ---\n ---\n# System general description\n" + system_description.strip() + "\n\n# End of system general description\n ---\n ---\n")
    # 4) Current codebase structure
    parts.append("\n\n\n\n\n\n\n\n ---\n ---\n# Current codebase structure\n" + file_tree + "\n\n# End of current codebase structure\n ---\n")
    # 5) Current codebase files
    if file_entries:
        fc_lines: List[str] = ["\n\n ---\n# Current codebase files"]
        for fe in file_entries:
            fc_lines.append(f"--- # inicio archivo {fe.rel_path}; LOC 0 ---")
            fc_lines.append(fe.content)
            fc_lines.append(f"--- # fin archivo {fe.rel_path}; LOC {fe.loc_total + 1} --- \n\n\n\n")
            fc_lines.append("")  # spacer between files
        fc_lines.append("# End of current codebase files\n ---\n ---\n")
        parts.append("\n".join(fc_lines).rstrip())
    # Per new spec, no "summaries" or legacy section names here.
    return "\n\n".join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    """Primary CLI entry point.

    Parses arguments, builds the prompt, optionally calls the API, and
    writes outputs to disk.  Returns an exit code.
    """
    parser = argparse.ArgumentParser(description="Generate contextual prompts for code generation.")
    parser.add_argument(
        "root",
        type=Path,
        help="Root directory of the project for which to build the prompt.",
    )
    parser.add_argument(
        "--message",
        type=Path,
        default=None,
        help="Path to the file containing the user's instructions.  If omitted, the CLI attempts to infer a file named 'task.md' or 'message.md' in the root directory.",
    )
    parser.add_argument(
        "--system-description",
        type=Path,
        default=None,
        help="Optional path to a file containing a high-level system description (e.g., SRS/SAD).",
    )
    parser.add_argument(
        "--summaries",
        type=Path,
        default=None,
        help="Optional path to a file containing pre-computed summaries of large files omitted from the prompt.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("current_step.md"),
        help="Name of the Markdown file to write the prompt to.",
    )
    parser.add_argument(
        "--call-api",
        action="store_true",
        help="If set, call the OpenAI Responses API after generating the prompt and write the diff to diff.patch.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=120000,
        help="Maximum number of tokens to generate from the Responses API.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o",
        help="Model name to use for the Responses API (default: gpt-4o).",
    )
    parser.add_argument(
        "--total-token-budget",
        type=int,
        default=120_000,
        help="Total token budget per run (input + reasoning + output). Default: 120000.",
    )
    parser.add_argument(
        "--input-token-limit",
        type=int,
        default=77_777,
        help="Maximum allowed input tokens per request. Default: 77777.",
    )
    parser.add_argument(
        "--reasoning-effort",
        type=str,
        choices=["low", "medium", "high"],
        default=None,
        help="Optional reasoning effort for models that support reasoning (ignored otherwise).",
    )
    parser.add_argument(
        "--previous-response-id",
        type=str,
        default=None,
        help="Optional previous response ID for context continuation in the Responses API.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=os.environ.get("REPORTER_LOGLEVEL", "INFO").upper(),
        help="Logging verbosity (default from env REPORTER_LOGLEVEL or INFO).",
    )
    parser.add_argument(
        "--no-diff",
        action="store_true",
        help="Compose the prompt without PatchPilot diff instructions. When combined with --call-api, request a free-form text response instead of a unified diff.",
    )
    args = parser.parse_args(argv)

    # Set up logging honoring --log-level and enable httpx/openai logs at same level
    level = getattr(logging, (args.log_level or "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(asctime)s %(name)s:%(lineno)d - %(message)s",
    )
    logging.getLogger().setLevel(level)
    logging.getLogger("httpx").setLevel(level)
    logging.getLogger("openai").setLevel(level)
    # Also surface httpcore (wire) when DEBUG to help diagnose networking
    if level <= logging.DEBUG:
        try:
            logging.getLogger("httpcore").setLevel(level)
        except Exception:
            logging.getLogger("httpcore").setLevel(level)
            pass
    logger = logging.getLogger("reporter.cli")

    root = args.root.resolve()
    if not root.exists() or not root.is_dir():
        logger.error("The specified root directory %s does not exist or is not a directory.", root)
        return 1

    # Load configuration
    config = ReporterConfig.load(root)
    logger.info("Loaded configuration from %s", config.reporter_config_path or "defaults")

    # Determine message file if not provided
    message_path = args.message
    if message_path is None:
        candidates = [root / "task.md", root / "message.md", root / "task.txt", root / "message.txt"]
        for cand in candidates:
            if cand.exists():
                message_path = cand
                break
        if message_path is None:
            logger.error("No user instruction file found. Specify --message.")
            return 1

    system_path = args.system_description
    summaries_path = args.summaries

    user_instructions = read_optional_file(message_path)
    system_desc = read_optional_file(system_path) if system_path else ""
    summaries_text = read_optional_file(summaries_path) if summaries_path else ""

    # Execution context diagnostics
    logger.info(
        "Execution context: cwd=%s | root=%s | message=%s | system_description=%s | output=%s | call_api=%s",
        Path.cwd(),
        root,
        message_path.resolve() if message_path else None,
        system_path.resolve() if system_path else None,
        args.output.resolve(),
        args.call_api,
    )

    # Build context
    # Interactive collection based on current working directory and persisted decisions
    builder = ContextBuilder(Path.cwd(), config.include_exclude)
    file_tree, file_entries = builder.collect_interactive()

    prompt = build_prompt(
        user_instructions=user_instructions,
        system_description=system_desc,
        file_tree=file_tree,
        file_entries=file_entries,
        summaries=summaries_text,
        include_patch_instructions=not args.no_diff,
    )

    # Write prompt to output file
    try:
        with args.output.open("w", encoding="utf-8") as f:
            f.write(prompt)
        logger.info("Prompt written to %s", args.output)
    except Exception as exc:
        logger.error("Failed to write prompt file: %s", exc)
        return 1

    # Optionally call the API
    if args.call_api:
        if args.no_diff:
            logger.info("Mode: --no-diff enabled → text response (no unified diff).")
        else:
            logger.info("Mode: diff (default) → expecting unified diff output.")
        logger.info("API policy: forcing model to GPT-5 with reasoning_effort=high and verbosity=high.")
        logger.debug("Requested CLI model was '%s' (will be overridden).", args.model)
        api_key = config.openai_api_key
        if not api_key:
            logger.error("OPENAI_API_KEY is not set in the environment. Cannot call API.")
            return 1
        #
        # Enforce GPT-5 + high reasoning effort + high verbosity when calling the API.
        #
        forced_model = "gpt-5"
        if args.model != forced_model:
            logger.info("Overriding requested model '%s' → '%s' due to --call-api policy.", args.model, forced_model)
        client = OpenAIClient(api_key=api_key, model=forced_model)
        # Compose messages for responses API: we send the entire prompt as user input,
        # and rely on our diff instructions embedded within the prompt.  We provide
        # minimal additional instructions to keep the model focused on generating a diff.
        messages = [{"role": "user", "content": prompt}]
        if args.no_diff:
            instructions = (
                "You are an expert assistant. Read the user's instructions and the provided project context "
                "(system description and current codebase). Provide a clear, concisely written but thorough answer in text on .md formatting (titles, subtitles, etc.)."
                "Do not output or mention unified diffs. If the user's instructions are in Spanish, reply in Spanish; "
                "otherwise reply in the same language as the instructions."
            )
        else:
            instructions = (
            "You are an expert code generation agent. Read the user's instructions and context."
            " Produce a unified diff that applies the requested changes to the provided files."
            " Do not explain the diff; just output the diff itself."
            )
        # ---- Token accounting & guardrails ----
        # Exact model-aware tokenization via tiktoken (encoding_for_model('gpt-5') if available).
        try:
            input_tokens = client.estimate_tokens(instructions + "\n" + prompt)
        except Exception as exc:
            logger.warning("Failed to estimate tokens precisely (%s). Falling back to heuristic.", exc)
            input_tokens = max(1, (len(instructions) + len(prompt)) // 4)
        logger.info(
            "Token limits: input=%d / limit=%d | total_budget=%d",
            input_tokens, args.input_token_limit, args.total_token_budget
        )

        if input_tokens > args.input_token_limit:
            logger.error(
                "Input token count %d exceeds limit %d. Reduce context or adjust include/exclude settings.",
                input_tokens,
                args.input_token_limit,
            )
            return 2

        remaining_budget = args.total_token_budget - input_tokens
        if remaining_budget <= 0:
            logger.error(
                "No token budget left for reasoning/output (input=%d, total budget=%d).",
                input_tokens,
                args.total_token_budget,
            )
            return 2

        # Per GPT-5 docs, max_output_tokens bounds *both* reasoning and visible output tokens.
        # So choose the lesser of the user's cap and the remaining budget to keep total ≤ budget.
        bounded_max_output = max(1, min(args.max_output_tokens, remaining_budget))
        if bounded_max_output < args.max_output_tokens:
            logger.info(
                "Capping max_output_tokens from %d → %d to honor total token budget (%d).",
                args.max_output_tokens,
                bounded_max_output,
                args.total_token_budget,
            )
        logger.info(
            "Final request: model=%s | reasoning_effort=high | verbosity=high | temperature=0 | max_output_tokens=%d | prev_id=%s",
            forced_model, bounded_max_output, bool(args.previous_response_id)
        )

        try:
            response = client.call_responses_api(
                messages=messages,
                instructions=instructions,
                tools=[],
                previous_response_id=args.previous_response_id,
                max_output_tokens=bounded_max_output,
                temperature=0,
                reasoning_effort="high",
                verbosity="high",
            )
        except Exception as exc:
            logger.error("API call failed: %s", exc)
            return 1
        # Extract the output_text property from the response object
        try:
            output_text = response.output_text
        except AttributeError:
            # Fallback for different response structure
            output_text = getattr(response, "choices", [{}])[0].get("message", {}).get("content", "")
        logger.info(
            "API response summary: id=%s status=%s output_len=%s",
            getattr(response, "id", None),
            getattr(response, "status", None),
            len(output_text or "")
        )
        # If server marked completed but text is empty, log a diagnostic hint
        if (getattr(response, "status", None) or "").lower() == "completed" and not (output_text or "").strip():
            logger.warning(
                "Response status is 'completed' but no output_text was extracted. "
                "This can happen with certain SDK shapes; extractor normalization has been applied. "
                "Consider enabling --log-level DEBUG to inspect raw response details."
            )

        # Determine output folder (same as current_step.md)
        output_dir = args.output.parent if args.output else Path(".")

        # Helper: pick next free alphabetical diff name: a.diff, b.diff, ..., z.diff, aa.diff, ab.diff, ...
        def _next_alpha_diff_path(base_dir: Path) -> Path:
            def _name_for(n: int) -> str:
                # 0->a, 1->b, ..., 25->z, 26->aa, 27->ab, ...
                s = []
                n0 = n
                while True:
                    s.append(chr(ord('a') + (n % 26)))
                    n = n // 26 - 1
                    if n < 0: break
                return "".join(reversed(s)) + ".diff"
            i = 0
            while True:
                candidate = base_dir / _name_for(i)
                if not candidate.exists():
                    return candidate
                i += 1
        if args.no_diff:
            # Plain text response output
            resp_md = output_dir / "response.md"
            try:
                with resp_md.open("w", encoding="utf-8") as f:
                    f.write(output_text or "")
                logger.info("Text response written to %s", resp_md.resolve())
            except Exception as exc:
                logger.error("Failed to write text response file: %s", exc)
                return 1
        else:
            # Write diff outputs:
            # 1) Always (over)write current_diff.md
            diff_md = output_dir / "current_diff.md"
            # 2) Also write to the first free alphabetical file: a.diff, b.diff, c.diff, ...
            diff_alpha = _next_alpha_diff_path(output_dir)
            try:
                with diff_md.open("w", encoding="utf-8") as f:
                    f.write(output_text or "")
                with diff_alpha.open("w", encoding="utf-8") as f:
                    f.write(output_text or "")
                logger.info("Diff written to %s and %s", diff_md.resolve(), diff_alpha.resolve())
            except Exception as exc:
                logger.error("Failed to write diff files: %s", exc)
                return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(main(sys.argv[1:]))