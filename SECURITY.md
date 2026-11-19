# Security Policy

## Supported versions

Only the **latest release** receives security fixes; HACS installs its `homehub_media.zip`. Update through HACS,
then restart Home Assistant.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately through GitHub's private vulnerability reporting: open the repository's **Security** tab and click
**Report a vulnerability** (<https://github.com/alws34/ha-homehub-media/security/advisories/new>).

Include what you can: the affected version or commit, your Home Assistant version, a description of the issue and
its impact, and steps or a proof of concept to reproduce it. Leave out real tokens, API keys and LAN addresses.

## What to expect

- **Acknowledgement within 7 days.**
- An initial assessment (accepted or declined, with reasons) within 14 days.
- For accepted reports, a fix in a new release as soon as practical, normally within 90 days. Coordinated
  disclosure: the advisory is published when the fix is released, or at 90 days, whichever comes first, unless we
  agree otherwise.
- Credit in the advisory if you want it.

This is a one-maintainer project, so timelines are best effort, but reports are taken seriously.

## Scope

In scope:

- The integration in `custom_components/homehub_media/`: services, WebSocket commands, HTTP views (the artwork
  proxy and the TV page state), the config and options flows, the LLM tools API and the conversation agent
- The Lovelace card and the TV page in `custom_components/homehub_media/www/`
- The media-PC daemons and systemd units in `media-pc/`
- The blueprints in `blueprints/` and the release workflow

Out of scope:

- Home Assistant core, HACS, the Samsung TV firmware, Plex, Jellyfin, YouTube and the LLM providers: report those to
  their own projects
- Risks the project already accepts, listed with reasons in
  [docs/cybersecurity.md, section 9](docs/cybersecurity.md#9-accepted-risks) (for example the TV's self-signed
  WebSocket certificate and plain HTTP to LAN devices without TLS)
- A media PC or keyboard bridge run without the firewall step in [media-pc/README.md](media-pc/README.md)

The threat model, input-validation rules and secret handling are in [docs/cybersecurity.md](docs/cybersecurity.md).

## Verifying releases

Each release zip has a `.sha256` file and a GitHub build-provenance attestation:

```bash
sha256sum -c homehub_media.zip.sha256
gh attestation verify homehub_media.zip --repo alws34/ha-homehub-media
```
