Check README.md for more context on the project

- Always use uv during development

## Verification Commands

```bash
# Run all tests
uv run pytest tests/ -v

# Run linter
uv run ruff check src/ tests/
```