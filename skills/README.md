# skills/

One folder per released plugin, `skills/<plugin>/`, copied by release automation from the source
commit recorded in `release.json`. A folder here without a `release.json` entry fails CI.

Each folder is a Claude Code plugin:

- a single-skill plugin: `.claude-plugin/plugin.json` with `"skills": ["."]` and `SKILL.md` beside it;
- a bundle: `.claude-plugin/plugin.json` with `"skills": ["./skills"]` (or no `skills` field) and
  `skills/<name>/SKILL.md` for each skill.

The plugin's `version` equals its `release.json` version, and so does a single skill's
`metadata.version`. A sentence "This copy is version X.Y.Z" in any `SKILL.md` equals that skill's
`metadata.version`.
