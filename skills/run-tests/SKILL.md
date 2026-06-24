---
name: run-tests
description: Run pytest test suite and report results. Use when the user asks to run tests, check test results, or verify code changes pass tests.
license: MIT
---
# Run Tests

Run the project's pytest test suite and report pass/fail results.

## Workflow
1. Run `python3 -m pytest tests/ -v`
2. Report: total tests, passed, failed, execution time
3. If any tests fail, show which ones and their error messages

## Input
- Optional: test file path or test name to run a subset

## Output
- Test count (total/passed/failed)
- Execution time
- Failure details if any
