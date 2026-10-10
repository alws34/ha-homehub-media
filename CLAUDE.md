# CLAUDE.md

HomeHub Media is a Home Assistant custom integration distributed through HACS
(`custom_components/homehub_media`). It covers Samsung Tizen TV control, ambience (lights plus video or
soundtrack, with YouTube through the Lounge API), Plex and Jellyfin, and a voice assistant. It ships a vanilla-JS
Lovelace card (`www/homehub-media-card.js`) and a TV page (`www/ambience.html`). There are also optional Ubuntu
daemons in `media-pc/`.

## Read first

- [docs/architecture.md](docs/architecture.md): layers, dependency rules, lifecycle, concurrency, conventions.
  Binding.
- [docs/cybersecurity.md](docs/cybersecurity.md): threat model, validation, secrets, auth, egress, frontend, limits,
  accepted risks, review checklist. Binding.
- `README.md` (user docs) and `docs/superpowers/` (original spec and plan with the frozen contracts K1–K8).

Every change must follow both binding docs. If a change needs to break a rule, update the doc in the same commit and
say why. For security rules, add an entry to "Accepted risks".

## Commands

```bash
.venv/bin/python -m pytest -q          # integration tests (pytest-homeassistant-custom-component)
.venv/bin/ruff check .                 # lint (config in pyproject.toml)
node --test 'tests/js/*.test.mjs'      # card and TV page tests (the glob is needed on Node 24)
```

CI (`.github/workflows/validate.yml`) runs HACS validation, hassfest, ruff, pytest and the node tests on every
push.

## Working rules

- **Style ("ponytail")**: the shortest correct code; reuse existing helpers; no speculative abstraction. A deliberate
  limit gets a `# ponytail:` comment that names the limit and the upgrade path. Never simplify away validation, error
  handling or security.
- **Tests**: every behaviour change ships with a test that fails without it. Security changes also test the
  rejection path.
- **Docs**: user-visible changes update `README.md` in the same commit. Structural changes update
  `docs/architecture.md`.
- **Translations**: config and options text, entities, services and selectors go in `translations/en.json`, with no
  URLs containing domains (hassfest).
- **Versioning**: bump `manifest.json` `version` for every release users should install. HACS installs from `main`.
- **Git**:
  - run `git fetch` before starting work; more than one session has worked on this repo;
  - never force-push;
  - commit on `feat/hacs-integration`, then fast-forward `main` after CI passes.
- **Secrets and privacy**: no tokens, real LAN addresses, personal media or `HANDOFF.md` in the repo. Tests use
  `192.0.2.x`.
- **Live testing**:
  - ask the user before restarting their Home Assistant or doing anything visible on their TV;
  - the HA token comes from the user and is never written to a file in the repo.

## Verified TV behaviour (Samsung Tizen 6.5)

- The WebSocket `ed.apps.launch` call is never acknowledged. Launch apps through REST
  (`POST :8001/api/v2/applications/<id>`).
- Remote URL launches open the browser hidden, so the TV-browser ambience page does not work on TV-only setups.
- YouTube DIAL (`:8080/ws/apps/YouTube`) needs `Origin: package:com.google.android.youtube`. YouTube draws its own
  keyboard, so Samsung IME text input (`SendInputString`) cannot type there.
- While the browser is open, the TV may send `ms.remote.touchEnable/Disable` before `ms.channel.connect`.
