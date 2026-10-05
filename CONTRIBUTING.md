# Contributing

Use Python 3.12 or later and the pinned dependencies in `requirements-lock.txt`. Follow the source installation steps in the README, including the editable application install and frontend build.

Keep changes within the relevant feature package. Preserve existing document bytes, source IDs, revisions, job states, and HTTP contracts. Provider tests should use fake transports; do not require paid credentials for the default suite.

Run the relevant tests, then the Python suite and lint checks. Changes to frontend or the independent API also require their tests and typechecks. Packaging/resource changes require `python scripts/verify_package.py` and the synthetic browser checks described in the frontend README.

For pull requests, describe the trigger, changed behavior, and actual verification. Distinguish checks that passed from checks not executed. Runtime `data/`, secrets, model weights, generated frontend bundles, and bulk QA output must not be committed. Add small synthetic regression fixtures under `tests/fixtures/` when needed.

The application is MIT licensed. Keep third-party notices and provenance when reusing code; model or dependency metadata does not by itself establish distribution clearance.
