# Project coding conventions

These rules apply to all project code, including tests and fixtures:

- Do not use `getattr`, `setattr`, their dunder variants, or helpers such as
  `monkeypatch.setattr`. Use explicit attribute access, direct assignments,
  dependency injection, or named test doubles. Do not disguise dynamic attribute
  access through string-based patch helpers or object namespace dictionaries.
- Restore test replacements with explicit assignments in `finally` blocks or
  teardown fixtures so tests remain isolated.
- Do not import or use `argparse`. Keep configuration in environment variables
  and handle small command-line interfaces with explicit argument checks.
- Preserve offline tests, Docker health checks, and existing entry-point behavior
  when applying these conventions.
