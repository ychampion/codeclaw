# Release Process

This project publishes to PyPI from GitHub Actions and can mirror artifacts to GitHub Packages.

## Publishing Modes

The publish workflow supports two authentication paths:

- Trusted publishing (OIDC) via PyPI trusted publisher.
- API token fallback via repository secret `PYPI_API_TOKEN`.

If `PYPI_API_TOKEN` is set, it is used first. Otherwise the workflow uses trusted publishing.

## GitHub Packages Mirror

The publish workflow includes a GitHub Packages (Python) upload step using the repository `GITHUB_TOKEN`.

- repository URL: `https://pypi.pkg.github.com/<OWNER>/`
- install index URL: `https://pypi.pkg.github.com/<OWNER>/simple/`

## One-Time Setup (Recommended: Trusted Publishing)

1. Create the project on PyPI (if it does not exist yet).
2. In PyPI project settings, configure a trusted publisher:
   - Owner: `ychampion`
   - Repository: `codeclaw`
   - Workflow: `.github/workflows/publish.yml`
   - Environment: `pypi`
3. Ensure this GitHub repo has an environment named `pypi`.

## Token Fallback Setup

If you are not using trusted publishing:

1. Create a PyPI API token scoped to the `codeclaw` project.
2. Add GitHub Actions secret `PYPI_API_TOKEN` in this repository.

## Release Steps

1. Validate locally:
   - `python -m pytest -q`
   - `python -m build`
   - `python -m twine check dist/*`
   - `python -m codeclaw --help`
2. Create and push tag:
   - `git tag -a vX.Y.Z -m "CodeClaw vX.Y.Z"`
   - `git push origin vX.Y.Z`
3. GitHub Actions `publish.yml` runs on tag push and uploads artifacts to PyPI.

## Troubleshooting

If publish fails with `invalid-publisher`, trusted publisher claims do not match PyPI settings.

Expected claims for this repo:

- repository: `ychampion/codeclaw`
- workflow: `.github/workflows/publish.yml`
- environment: `pypi`

For debugging, note that `workflow_ref` differs by trigger:

- tag publish: `ychampion/codeclaw/.github/workflows/publish.yml@refs/tags/vX.Y.Z...`
- manual dispatch from `main`: `ychampion/codeclaw/.github/workflows/publish.yml@refs/heads/main`
