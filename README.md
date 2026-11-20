# swarm-hub-agents

Released skills and plugins from Swarm Hub.

Each release is an immutable tag named `<plugin>@<version>`. `release.json` lists the current
stable release of every plugin. This repository is published by release automation; changes arrive
as reviewed release pull requests.

## Layout

| Path | What it is |
| --- | --- |
| `release.json` | The release manifest: per plugin its `version`, `tag`, source `commit`, `path`, `default`, `roles` and `min_portal`. Everything that installs or checks a plugin reads its version here. |
| `release.schema.json` | The JSON Schema for `release.json`. |
| `examples/release.example.json` | A manifest with one made-up plugin, to show the shape. |
| `skills/<plugin>/` | One folder per released plugin, a Claude Code plugin (see `skills/README.md`). |
| `.claude-plugin/marketplace.json` | The marketplace index for runtimes that sync a branch: it lists exactly the released plugins. |
| `CHANGELOG.md` | One entry per release. |
| `scripts/`, `tests/` | The checks CI runs, and their tests. |

`main` always equals the latest stable release. To pin a plugin, install it at its tag.

## What CI refuses

- **validate:** a `release.json` that breaks the schema; a tag that is not `<plugin>@<version>`; a
  version that differs between `release.json`, the plugin's `plugin.json`, a single skill's
  `metadata.version` and its "This copy is version X.Y.Z" sentence; a plugin folder with no release
  or a release with no folder; a marketplace that lists anything else.
- **neutrality:** a workgroup value in any file, commit message or PR title and body. Generic
  patterns live in `scripts/neutrality-denylist.txt`; the literal values come from the
  `NEUTRALITY_DENYLIST` Actions secret (one per line), and the check fails when that secret is
  missing. A released skill reads workgroup values from bindings at run time, never from its text.
- **Secret Scan (Gitleaks):** any secret in the tree or in the commits a change adds.

Run the same checks locally:

```sh
python3 scripts/validate_release.py
python3 scripts/check_neutrality.py   # set NEUTRALITY_DENYLIST to check the literals too
python3 -m unittest discover -s tests
gitleaks dir .
```
