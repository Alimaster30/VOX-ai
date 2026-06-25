# VOX CI

The GitHub Actions workflow lives at:

```text
.github/workflows/ci.yml
```

It runs on pushes, pull requests, and manual dispatch.

The workflow uses:

```text
requirements-ci.txt
```

This is intentionally lighter than `requirements.txt`.
The full production file installs GPU/model/audio packages, while CI only needs the packages required by the automated tests.

CI checks:

- installs lightweight test dependencies
- compiles key entrypoints
- runs the full pytest suite
- runs the PostgreSQL integration test against a live PostgreSQL service
- compiles operational commands including `smoke_test.py`

The workflow disables model autoloading with:

```text
VOX_AUTOLOAD_MODELS=0
```

So CI validates the API, persistence, deployment artifacts, backups, monitoring config, and safety checks without downloading local AI models.
