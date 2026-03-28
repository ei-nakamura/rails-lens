# Contributing to rails-lens

Thank you for your interest in contributing to rails-lens!

## Development Setup

**Requirements:** Python 3.11+, Git

```bash
git clone https://github.com/ei-nakamura/rails-lens.git
cd rails-lens
pip install -e ".[dev]"
```

This installs all development dependencies: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, and `mypy`.

## Running Tests

```bash
# Run all tests
pytest tests/

# Run with coverage report
pytest tests/ --cov=src/rails_lens --cov-report=term-missing

# Run a specific test file
pytest tests/unit/test_config.py -v
```

Coverage must remain at **80% or above**. PRs that drop coverage below this threshold will not be merged.

**SKIP = FAIL**: Tests marked with `pytest.mark.skip` or `skipif` count as incomplete. Do not submit PRs with skipped tests unless accompanied by a clear explanation and a linked issue.

## Coding Standards

### Linting and Type Checking

All code must pass `ruff` and `mypy` before submitting:

```bash
ruff check src/ tests/
mypy src/rails_lens/
```

Fix all issues before opening a PR. Do not suppress ruff or mypy errors with `# noqa` or `# type: ignore` unless absolutely necessary and accompanied by a comment explaining why.

### Style

- Line length: 100 characters (configured in `pyproject.toml`)
- Follow the existing code style in each module
- Keep functions focused and small (single responsibility)
- Add docstrings only for public API functions

## Submitting a Pull Request

1. Fork the repository and create a branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. Make your changes and add tests for new functionality.

3. Ensure all checks pass:
   ```bash
   ruff check src/ tests/
   mypy src/rails_lens/
   pytest tests/ --cov=src/rails_lens
   ```

4. Commit your changes (see commit message format below).

5. Push to your fork and open a pull request against `main`.

6. Fill in the PR description explaining what changed and why.

## Commit Message Format

Use conventional commit prefixes:

| Prefix | Use for |
|--------|---------|
| `feat:` | New features |
| `fix:` | Bug fixes |
| `docs:` | Documentation changes |
| `test:` | Adding or updating tests |
| `refactor:` | Code refactoring (no behavior change) |
| `chore:` | Build process, dependency updates, tooling |

**Examples:**
```
feat: add rails_lens_get_routes tool
fix: handle empty schema.rb gracefully
docs: update README with Cursor configuration example
test: add coverage for error path in runner.py
```

Keep the subject line under 72 characters. Add a body if the change needs explanation.

## Reporting Issues

Open an issue on GitHub with:
- A clear description of the bug or feature request
- Steps to reproduce (for bugs)
- Expected vs actual behavior
- Rails and Python version information
