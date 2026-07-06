# Contributing to HomeStream

Thank you for your interest in contributing to HomeStream! This document outlines the process for contributing code, documentation, and ideas.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). Please read it before participating.

## How to Contribute

### Reporting Bugs

1. Check the [issue tracker](https://github.com/Ninefoldatwill/homestream/issues) to see if it's already reported
2. Include: HomeStream version, OS, Python version, steps to reproduce, expected vs actual behavior
3. Use the "Bug Report" issue template if available

### Suggesting Features

1. Check existing issues and discussions
2. Open a new issue with the "Feature Request" label
3. Describe the use case, proposed solution, and any alternatives considered

### Pull Requests

#### Development Workflow

```bash
# 1. Fork and clone
git clone https://github.com/YOUR_USERNAME/homestream.git
cd homestream

# 2. Create a branch
git checkout -b feat/your-feature-name

# 3. Set up environment
cp .env.example .env
pip install -r requirements.txt

# 4. Make your changes
# ... write code ...

# 5. Run tests
pytest -v

# 6. Run pre-commit checks
pre-commit run --all-files

# 7. Commit and push
git commit -m "feat: describe your change"
git push origin feat/your-feature-name

# 8. Open a Pull Request
```

#### Commit Message Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `test:` Adding or updating tests
- `refactor:` Code restructuring without behavior change
- `chore:` Maintenance tasks

#### Pull Request Checklist

Before submitting, ensure:

- [ ] Tests pass: `pytest -v`
- [ ] Coverage doesn't drop: `pytest --cov=. --cov-fail-under=70`
- [ ] No security issues: `bandit -r .`
- [ ] Code formatted: `ruff format .`
- [ ] No lint errors: `ruff check .`
- [ ] Documentation updated if needed

### Review Process

1. All PRs require at least one review from a maintainer
2. CI checks must pass (tests, lint, security)
3. Maintainers may request changes — this is normal and collaborative
4. Once approved, a maintainer will merge your PR

## Development Setup

### Prerequisites

- Python 3.9+
- pip
- (Optional) pre-commit

### Installing Dependencies

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # development extras
```

### Running Tests

```bash
# All tests
pytest -v

# Specific test file
pytest test_meeting_room.py -v

# With coverage
pytest --cov=. --cov-report=html
```

### Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for formatting and linting:

```bash
ruff check .        # lint
ruff format .       # format
ruff check . --fix  # auto-fix
```

## Project Structure

```
homestream/
├── bridge_v7_server.py    # FastAPI main server
├── event_stream.py         # EventStream engine
├── config.py               # Configuration (from .env)
├── openbridge/             # CLI and core package
│   ├── cli.py              # Typer CLI
│   └── __init__.py
├── providers/              # Model provider integrations
├── test_*.py               # Test files
├── .env.example            # Environment template
├── pyproject.toml          # Project configuration
└── requirements.txt        # Dependencies
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

**Questions?** Open a [Discussion](https://github.com/Ninefoldatwill/homestream/discussions) or email contribute@jiuchong.studio.
