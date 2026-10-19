# Releases and packages

The package name is `ai-trading-playground`; its version is defined in
`pyproject.toml`. Python 3.13 on macOS/Linux is required.

## Build locally

```sh
uv sync --locked --group dev
uv run pytest
uv build
```

`dist/` contains a wheel (`.whl`) and source distribution (`.tar.gz`). The wheel
includes all runtime packages, both console commands and dashboard static assets,
including the performance calendar. `MANIFEST.in` also includes documentation,
offline tests, diagnostic scripts, submodule setup guidance, and the repository
coding conventions in the source archive. The `libs/skills` submodule contents
are optional development resources and are not runtime dependencies.
Install it into a dedicated Python 3.13 virtual environment:

```sh
uv venv /tmp/autotrader-install --python 3.13
uv pip install --python /tmp/autotrader-install/bin/python dist/*.whl
/tmp/autotrader-install/bin/ai-autotrader --help
```

Supply environment configuration before starting `ai-autotrader` or
`ai-autotrader-dashboard`. Original checkout and Docker entry points still work.

## Publish a release

Commit and push the release setup before creating the first tag. For each release,
update `project.version`, run `uv lock`, and commit both files with the changes.
Then tag that commit with the matching stable version:

```sh
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin main
git push origin v0.1.0
```

Use a new version and tag for subsequent releases. The release workflow rejects
version mismatches and prerelease tags. It runs the offline suite, builds the
Python distributions, checks an installed wheel outside the checkout and builds
and smoke-checks the Docker image before publishing:

- A GitHub Release with generated notes, wheel, source archive and `SHA256SUMS`.
- A Linux amd64 container at `ghcr.io/wecet/ai-based-autotrader:v0.1.0`
  (substitute the release tag).

The same image supports the trader and dashboard; the dashboard command is
`python -m dashboard`. Preserve the environment, volumes and security settings
in [docker-compose.yml](../docker-compose.yml) when using a published image.
Tags are explicit; the workflow does not maintain a moving `latest` tag.

GitHub Actions must be enabled and repository policy must allow the workflow's
`contents: write` and `packages: write` permissions. Publishing uses the built-in
`GITHUB_TOKEN`; no PyPI account or external registry secret is needed. Package
visibility/access is managed in GitHub package settings. See GitHub's
[container publishing documentation](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).

Inspect the Actions run after pushing a tag. Failed checks prevent publishing;
a later publication failure can leave a container or draft release behind.
Re-running the failed workflow can complete the same release. Python packages
are release downloads; this workflow does not publish to PyPI.
