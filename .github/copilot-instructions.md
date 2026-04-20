# Copilot Instructions

These are the project-wide coding rules and preferences for this workspace.

## What are we doing

This is django-ultracache, a poweful caching framework for Django

## Key Documentation

- **`docs/architecture.md`** — the primary project reference. Read this for architecture, modelling decisions, migration status, and what remains to be ported. Consult it before planning new features.
- **`docs/plans/`** — historical migration plans from earlier phases (admin, code, data migration). These document past decisions but should **not** drive new feature planning. Do not treat them as active requirements.

## Rules of Engagement

- **Address the user as "Daddy"** — in every response, to signal when context window has degraded.
- **Don't run tests after every edit** — only run `tox` when you believe a task is complete, or when you need deterministic confirmation that a refactor hasn't broken anything. Don't run linters or tests after individual file changes mid-task.
- When implementing a plan, start each phase in a new agent to prevent context drift.
- When checking on shell progress, and it looks like a long running task, decrease the check frequency.

## General Principles

- **Prefer explicit over implicit** — avoid magic; name things clearly.
- **Always write tests for new code** — no feature is done without tests. Use pytest via tox.
- **Create plan docs before implementing** — write a markdown plan in `docs/` before starting any non-trivial work.
- **Don't over-split files** — keep code in a single module (e.g. `views.py`, `models.py`) until it genuinely becomes unwieldy. A 400-line `views.py` does not need splitting into `views/cars.py` and `views/manufacturers.py`. Only create new files when there is a clear organisational benefit.

## Research
- If you do not already have a library's code loaded in your context, then consult the library's online documentation first.
- If the online documentation is insufficient, then only read the library's source code.

## Python

- **Follow PEP 8** — standard formatting and naming conventions.
- **Use type hints** — annotate function parameters and return types, but **not** for local variables.
- **No code in f-strings** — declare a local variable first, then reference it in the f-string.

  ```python
  # Bad
  print(f"Result: {some_dict[key].calculate()}")

  # Good
  result = some_dict[key].calculate()
  print(f"Result: {result}")
  ```

- **Virtual environment** — use the `ve` directory.
- **Imports at the top** — put imports at the top of a module as far as possible. **No relative imports.**
- **No class-only encapsulation** — don't create classes with only `staticmethod`s. Use functions instead; the module itself is the encapsulation.
- **Terse comments** — good Python is self-documenting. If a comment has more than one sentence, end with a full stop. All comments must start with a capital letter. Never put comments on the same line as code. No excessive ASCII delimiters.
- **Fail fast** — assume all invariants are guaranteed. Do not use `try/except` or `.get()` for mandatory fields. Code should be strict and fail-fast.
- **No unnecessary abbreviations** — if a variable name is eight characters or fewer, spell it out fully. For example, use `response` not `resp`, `request` not `req`, `result` not `res`, `message` not `msg`.

## Django

- **Follow Django best practices** — fat models/thin views, use Django's ORM properly, leverage built-in features before reaching for third-party packages.
- **Keep views focused** — each view should do one thing.
- **Use Django's URL naming** — always use `reverse()` or `{% url %}` instead of hardcoded paths.

## Testing

- **Test runner:** pytest via tox (`tox` to run all tests). Attempt to use more than one core when running tests, else it takes forever. Hint: append `-- -n number_of_cores`.
- **Test structure:** tests live in the top-level `tests/` directory.
- **Use fixtures** — shared fixtures go in `conftest.py`.
- **Parameterize** — use `@pytest.mark.parametrize` for repetitive test cases.
