# AGENTS.md
Strict operating guide for autonomous coding agents in this repository.

## 1) Mission and output contract
- Purpose: local-first long-form faceless explainer video generation via `local-video-mvp` (Python CLI, `src/` layout).
- Keep this output contract stable unless the user explicitly requests a change:
  - `projects/<project-id>/output/final.mp4`
  - `projects/<project-id>/output/final.srt`
  - `projects/<project-id>/script.json`
  - `projects/<project-id>/timeline.json`
  - `projects/<project-id>/rights_manifest.json`
  - `projects/<project-id>/run_report.json`
  - `projects/<project-id>/run.log`

## 2) Instruction precedence
Apply instructions in this order:
1. Direct user instruction in current task
2. This `AGENTS.md`
3. `README.md`
4. `PROJECT_ONBOARDING.md`
If rules conflict, follow the highest-priority source and state the conflict in final notes.

## 3) Rules files status
- `.cursorrules`: absent
- `.cursor/rules/`: absent
- `.github/copilot-instructions.md`: absent
- If these appear later, treat them as mandatory and update this guide.

## 4) Current implementation state (history-aware)
Implemented on `main`:
- baseline local MVP pipeline and CLI
- subtitle chunking + subtitle burn-in
- duration tolerance with auto expand/compress script passes
- pacing refinements + pause-boundary improvements
- voice inventory and voice A/B preview command
- visual effects presets (`clean`, `subtle-motion`, `dynamic`)
- intro/outro bookends with style presets and safer wrapped titles
- onboarding docs for collaborators
Next milestone:
- human-in-the-loop flow: `draft -> review -> preview -> finalize`

## 5) Repository map (source of truth)
- CLI boundary and input validation: `src/local_video_mvp/cli.py`
- Config/data contracts: `src/local_video_mvp/models.py`
- Runtime orchestration and stage logic: `src/local_video_mvp/pipeline.py`
- Setup/run guidance: `README.md`
- Project status/workflow: `PROJECT_ONBOARDING.md`
- If code and docs disagree, trust code first and then update docs.

## 6) Autonomous execution policy
- Default to action; do not ask permission for safe, reversible work.
- Ask exactly one targeted question only when blocked by material ambiguity, irreversible risk, or missing secret/credential.
- Finish non-blocked work first; keep changes minimal and focused.

## 7) Non-negotiable invariants
### MUST
- Preserve strict commercial-safe defaults unless the user explicitly changes policy.
- Preserve artifact paths, report schema keys, and explicit fallback warnings (script, captions, assets, burn-in).
- Keep subprocess calls time-bounded and return-code checked.
- Keep text/JSON writes UTF-8 and deterministic in structure.
- Run at least one meaningful validation step for non-trivial changes.

### MUST NOT
- Commit `.env`, secrets, credentials, or large generated binaries.
- Silently relax strict-safe behavior.
- Rename/remove core output files without explicit request.
- Remove run-report keys without explicit migration note.
- Use destructive git operations unless explicitly requested.
- Introduce unrelated refactors in feature/fix tasks.

## 8) Setup, build, and run commands
Setup:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
python -m pip install -e '.[voice,captions]'
```
Build/package:
```bash
python -m pip install build
python -m build
```
Main CLI commands:
```bash
local-video-mvp --help
local-video-mvp run --help
local-video-mvp inspect --project-dir ./projects/<project-id>
local-video-mvp voices --melo-language EN
local-video-mvp voice-ab --project-dir ./projects/<project-id> --speakers EN-US EN-Default EN-AU
```
System binaries expected in PATH:
- required: `ffmpeg`, `ffprobe`
- optional by mode: `ollama`, `say`

## 9) Lint, format, type-check, and tests
No repository-pinned configs currently exist for `ruff`, `mypy`, `black`, or `pytest`.
Recommended checks:
```bash
python -m ruff check src
python -m ruff format --check src
python -m mypy src
python -m black --check src
```
Test status:
- no tracked test suite currently exists.
When tests are added, use these pytest patterns (single-test support included):
```bash
python -m pytest -q
python -m pytest -q tests/test_example.py
python -m pytest -q tests/test_example.py::test_specific_case
python -m pytest -q tests/test_example.py::TestSomething::test_specific_case
python -m pytest -q -k "duration"
```

## 10) Code style and implementation rules
### Imports and formatting
- Keep `from __future__ import annotations` at top where used.
- Group imports in order: stdlib, third-party, local.
- Use 4-space indentation, double quotes, clear wrapping, and f-strings.

### Types and naming
- Type annotate public and non-trivial internal functions.
- Prefer built-in generics (`list[str]`, `dict[str, Any]`) and `X | None`.
- Naming: `snake_case` functions/vars, `PascalCase` classes, `UPPER_SNAKE_CASE` constants.
- Keep CLI flags kebab-case and aligned with existing names.

### Errors, paths, subprocess
- Validate user inputs at CLI boundary.
- Raise actionable `RuntimeError`/`ValueError` with clear context.
- Use `pathlib.Path` and normalize with `expanduser().resolve()`.
- Capture and surface subprocess stderr on failure.

### Determinism and fallbacks
- Do not add hidden nondeterminism.
- Keep fallback behavior explicit and user-visible via warnings/report fields.
- If changing selection logic, prefer stable hashes over process-randomized behavior.

## 11) Validation requirements
For behavior-affecting changes, run at least one relevant validation:
- targeted command check for CLI behavior,
- smoke run through affected pipeline stages,
- artifact/report integrity check.
Minimum practical validation for pipeline edits:
1. run a short generation,
2. run `local-video-mvp inspect --project-dir <run-dir>`,
3. confirm report keys and expected outputs exist.
If tooling is missing, report the exact command and reason.

## 12) Failure triage flow
Debug run failures in this order:
1. `run_report.json` (status, failing stage, warnings)
2. `run.log` (first error and stage context)
3. ffmpeg/ffprobe stderr details
Then:
1. classify failing domain (deps/input/script/tts/captions/assets/render/report),
2. reproduce minimally,
3. patch the smallest root cause,
4. re-run targeted validation.

## 13) Definition of done
A task is done only when all are true:
- requested behavior is implemented with minimal scope,
- invariants and output contract remain intact,
- validation was run (or missing tooling is explicitly documented),
- docs/help are updated if flags/behavior changed,
- final response includes changed files, rationale, validation, and residual risks.

## 14) Contribution guidance
- Keep commits focused and logically grouped.
- Commit regularly when a task or meaningful subtask is complete.
- Prefer small, reversible commits even for experimental work; use clear messages so bad changes can be reverted quickly.
- Prefer reproducible local runs over speculative changes.
- Keep docs synchronized with CLI/runtime behavior.
- Favor maintainability and explicitness over clever one-liners.
