# Contributing

Issues and pull requests are welcome: bug reports from other Samsung TV models are especially useful, since the
integration is only tested on one Tizen TV.

## Workflow

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Create one branch per change and open a pull request against `main`.
3. Keep PRs focused. CI (HACS validation, hassfest, ruff, pytest, the card tests and CodeQL) must pass before merge.

## Ground rules

The binding project rules are in [docs/architecture.md](docs/architecture.md) and
[docs/cybersecurity.md](docs/cybersecurity.md). In short:

- every behaviour change ships with a test that fails without it; security changes also test the rejection path;
- user-visible changes update `README.md`, and UI text goes in `translations/en.json`;
- no tokens, real LAN addresses or personal media in the repo or in issues; tests use `192.0.2.x`;
- new inputs get an anchored, bounded schema; secrets never reach logs or the frontend.

## Local checks

```bash
uv venv .venv --python 3.14 && uv pip install -p .venv -r requirements_test.txt
.venv/bin/ruff check custom_components tests media-pc
.venv/bin/pytest -q --cov
node --test 'tests/js/*.test.mjs'   # the card and the TV page
```

Test requirements are pinned exactly in `requirements_test.txt`. `pytest-homeassistant-custom-component` fixes the
Home Assistant version, so when it is bumped, set `home-assistant-frontend`, `home-assistant-intents`, `hassil`,
`gazetteer-matcher` and `samsungtvws` to what that Home Assistant release pins, and keep `pyytlounge` equal to
`manifest.json`. Dependabot only proposes `pytest-homeassistant-custom-component` and `ruff`.

## Releases

Bump `version` in `custom_components/homehub_media/manifest.json`, merge to `main`, then push a matching tag
(`v0.3.6` for version `0.3.6`). The release workflow builds `homehub_media.zip` and publishes the GitHub release.

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).

## Sign-off

No CLA and no DCO sign-off required. By contributing you agree your work is released under the project's
[MIT license](LICENSE).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
