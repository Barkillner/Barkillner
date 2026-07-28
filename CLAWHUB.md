# `clawhub install`

A small, dependency-free command for installing [ClawHub](https://clawhub.ai)
skills into this repository. ClawHub is the public skill registry for OpenClaw
("npm for AI agents"), and this command talks to the same registry the official
`npx clawhub` CLI uses.

The implementation lives in [`bin/clawhub.js`](bin/clawhub.js) and uses only
Node.js built-ins (Node 18+).

## Usage

```bash
# Directly with Node — no install step needed
node bin/clawhub.js install <skill-slug>

# Or expose it as `clawhub` on your PATH
npm link          # once
clawhub install <skill-slug>

# npx also works after linking / when run from the repo root
npx clawhub install <skill-slug>
```

`<skill-slug>` is either a bare slug (`github`) or a fully-qualified
`@owner/slug` (`@steipete/github`). When a bare slug matches several skills the
command lists the candidates and asks you to re-run with a qualified slug —
exactly like the hosted CLI.

## Examples

```bash
# Install the latest version of a specific owner's skill
clawhub install @steipete/github

# Pin a specific version
clawhub install @steipete/github --version 1.0.0

# Reinstall over an existing copy
clawhub install @steipete/github --force

# Install into a custom directory
clawhub install @dbalve/fast-io --dir .codex/skills
```

## What it does

1. Resolves the skill against `https://clawhub.ai/api/v1/skills/<slug>` and
   checks moderation status (refuses malware; requires `--force` for skills
   flagged for review).
2. Resolves the version to install and downloads the archive.
3. Extracts it atomically into the install directory.
4. Verifies the skill's `SKILL.md` frontmatter.
5. Records the install in `.clawhub/lock.json` and a per-skill
   `.clawhub/origin.json`, matching the official on-disk layout.

## Options

| Flag | Description |
|---|---|
| `-v, --version <ver>` | Install a specific version instead of the latest. |
| `-f, --force` | Reinstall over an existing copy / install a flagged skill. |
| `-d, --dir <path>` | Install directory. Defaults to `.codex/skills` in this repo (falls back to `./skills` elsewhere). |
| `--workdir <path>` | Directory holding `.clawhub/lock.json` (default: current directory). |
| `--registry <url>` | Registry base URL (default: `https://clawhub.ai`). |
| `-y, --yes` | Assume yes for prompts (non-interactive). |
| `-h, --help` | Show help. |

Environment overrides: `CLAWHUB_REGISTRY`, `CLAWHUB_INSTALL_DIR`,
`CLAWHUB_WORKDIR`.

## Limitations

This is a focused client for the `install` verb. Skills whose canonical source
is a GitHub repository (rather than a published archive) are not fetched by this
minimal client — use the official `npx clawhub install …` for those. Search,
publish, update, and auth are likewise out of scope here.
