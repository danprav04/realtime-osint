---
name: test-runner
description: >-
  Reliable execution, monitoring, and resolution of test suites in this project.
  Use whenever running pytest, checking background test completion, or resolving hung test runs
  to prevent getting stuck or going idle after launching test commands.
---

# Test Runner & Execution Discipline

## Purpose
Prevents the agent from getting stuck or waiting passively in an idle state when running test suites. Ensures test commands either complete synchronously or are immediately inspected and reported upon completion.

## Core Guidelines & Workflow

### 1. Synchronous Execution Preference
Always configure `WaitMsBeforeAsync` to `10000` (10 seconds) for test commands so unit and integration tests complete synchronously within the tool call:
```powershell
$env:PYTHONPATH="."; pytest backend/tests/test_m2_plugins.py -v
```

### 2. Immediate Background Task Inspection (Never Go Idle)
If a test command is offloaded to a background task by `run_command`:
1. Note the log file location returned by the tool (e.g., `.../.system_generated/tasks/task-<id>.log`).
2. **NEVER** just end the turn with an open-ended "I have launched the test and will wait" message and abandon execution.
3. Immediately inspect the task log using `view_file` on the log path, or check task status using `manage_task` (Action: `status`).
4. As soon as the log contains test summary markers (e.g., `passed in`, `failed`, or `ERRORS`), extract the result, inform the user with the exact counts, and continue with the next implementation step immediately.

### 3. Repository Test Conventions
- **Environment**: Always prepend `$env:PYTHONPATH="."` on Windows PowerShell.
- **Targeted Subsets**: Target specific test files or filters (`-k "telegram"`, `test_m3_pipeline.py`) during development to ensure fast (<3s) turnaround.
- **Failure Analysis**: If errors occur, inspect the exact traceback from the log file and immediately fix the offending code.
