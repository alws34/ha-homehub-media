## What and why

<!-- What does this change, and why? Link the issue if there is one. -->

## Checklist

- [ ] Tests added or updated (a behaviour change has a test that fails without it; security changes also test the rejection path)
- [ ] `ruff check custom_components tests media-pc`, `pytest -q` and `node --test 'tests/js/*.test.mjs'` pass
- [ ] `README.md` updated for user-visible changes; UI text is in `translations/en.json`
- [ ] Follows `docs/cybersecurity.md`: bounded input schemas, no secrets in logs or the frontend, timeouts and size caps on outbound calls
- [ ] No tokens, real LAN addresses or personal media in the diff
- [ ] `manifest.json` version bumped if this should be released
