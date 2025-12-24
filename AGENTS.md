# GrandmaTVBot: Agent Guidelines

## Project Overview
GrandmaTVBot is a Python-based tool to automate LG WebOS TV actions using macros. It uses `aiowebostv` for TV control and `wakeonlan` for waking the device. Configuration is stored in `config.yml`.

## Technology Stack
- **Language**: Python 3.12+
- **Package Manager**: `uv`
- **Linting/Formatting**: `ruff`
- **Testing**: `pytest`

## Coding Standards
- **Philosophy**: Write Pythonic, maintainable, testable, and readable code.
- **Simplicity**: Prefer simple solutions over clever ones. Optimize for readability first, then line count.
- **Syntax**: Use modern Python 3.12+ features:
    - Native type hints (e.g., `list[str]`, `dict[str, int]`)
    - Union operator `|` (e.g., `str | None`)
    - F-strings for string interpolation (avoid `%` or `.format()`)
- **Logging**: Use the `logging` module instead of `print()`.
- **Documentation**: All public modules, classes, and functions must have thorough **Google Style** docstrings.

## Code Structure
- `main.py`: Entry point and macro definitions.
- `config.yml`: User configuration (ignored by git).
- `config.yml.example`: Example configuration.

## Workflow
1. **Understand the Goal**: Read the user request and related code.
2. **Edit Code**: Make necessary changes.
3. **Verify**: Run the code to ensure it works as expected.

## Quality Assurance
**CRITICAL**: After any changes, you **MUST** run the following commands to ensure code quality and correctness:

1. **Lint and Fix**:
   ```sh
   uv run ruff check --fix .
   ```

2. **Format**:
   ```sh
   uv run ruff format .
   ```

3. **Test**:
   ```sh
   uv run pytest
   ```
   *Ensure all tests pass before submitting changes.*