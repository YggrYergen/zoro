# Reporter CLI

The **reporter** CLI is a tool designed to streamline the process of
creating high–quality prompts for OpenAI’s Responses API and Agents API
in software development workflows.  It assembles user instructions,
context from your project, and fixed diff instructions into a single
Markdown file (`current_step.md`) that can be submitted to the API.  It
can also optionally call the Responses API on your behalf to obtain a
unified diff patch.

## Features

* **Contextual prompt generation** – Collects a curated set of source
  files from your project based on configurable include/exclude
  patterns and size limits.
* **Modular architecture** – Separates concerns into configuration,
  context building, OpenAI interactions, and CLI presentation.  This
  makes the tool maintainable and easy to extend.
* **Unified diff instructions first** – The prompt begins with the
  PatchPilot “System instructions” block, ensuring the model produces
  diffs rather than full files.
* **Optional API invocation** – When `--call-api` is supplied and
  `OPENAI_API_KEY` is set, the tool will submit the assembled prompt
  and write the resulting diff to `diff.patch`.

## Installation

1. Ensure you have Python 3.9+ installed.
2. Install dependencies:

   ```sh
   pip install openai tiktoken
   ```
3. Clone or copy this `reporter` directory into your project.  To use it
   globally, you can create a virtual environment and install it with
   `pip install -e .` at the root containing `reporter`.
4. Set your OpenAI API key in the environment:

   ```sh
   $Env:OPENAI_API_KEY = "sk-proj-..."  # PowerShell example
   ```

## Usage

Change into the project directory you wish to work on and run:

```sh
python -m reporter.cli . --message task.md --system-description SRS.md
```

This command generates `current_step.md` in the current directory.  The
file contains your user instructions, a file tree of your project,
selected file contents, optional system descriptions and summaries,
and the fixed PatchPilot diff instructions.
selected file contents, optional system description,
and the fixed PatchPilot diff instructions at the top.
flag (your API key must be set):

```sh
python -m reporter.cli . --message task.md --call-api
```
python -m reporter.cli . --message task.md --call-api --model gpt-4o
This will write the model’s response into `diff.patch`.  Review and
apply the patch manually using your preferred tools.

## Configuration

### Additional CLI options

* `--model <name>` – Select the model used for the Responses API (default: `gpt-4o`).
* `--reasoning-effort {low,medium,high}` – Optional. If the selected model supports
  reasoning, the effort is passed to the API; otherwise it is ignored with an info log.

The behaviour of the reporter can be customized by creating a
`report_config.json` in your project root.  The file supports the
following keys under `include_exclude`:
`reporter_config.json` in your project root.  The file supports the
* `include_patterns` – A list of glob patterns describing files to
  include.  Defaults to all files.
* `exclude_patterns` – A list of glob patterns describing files or
  directories to exclude.  Exclusions override inclusions.
* `max_file_lines` – Maximum number of lines to include from any
  single file (0 means unlimited).  Defaults to 400.
* `max_total_characters` – Maximum cumulative characters across all
  included files (0 means unlimited).  Defaults to 100000.

See `reporter/report_config.json` for an example.

If a legacy `report_config.json` is present and `reporter_config.json` is absent,
it will be used with a deprecation warning.

## Output format

The generated `current_step.md` uses this exact section order:

1. `# System instructions` – PatchPilot block.
2. `# User instructions`
3. `# System general description` (optional)
4. `# Current codebase structure` – directories (with `/`) and files, properly indented.
5. `# Current codebase files` – Each file is wrapped with markers:

```
--- inicio archivo path/to/file.ext; LOC 0 ---
```<language?>
<file content ... may be truncated>
```
--- fin archivo path/to/file.ext; LOC <real_total_lines + 1> ---
```

* The tool currently supports the OpenAI Responses API.  Agents API
  integration is planned for a future release.
* API calls may incur cost.  The tool prints an estimated cost prior
  to submission.  Be mindful of your usage limits and budgets.
* Large files may be truncated to stay within model context limits.
  Consider providing summaries via the `--summaries` flag for omitted
  sections.

## License

This tool is provided under the MIT license.  See `LICENSE` if
present.