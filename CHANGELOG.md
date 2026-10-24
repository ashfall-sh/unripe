# Changelog

## 0.7.0 (2026-10-23)

- `[registries]` in `.unripe.toml` points npm, PyPI, Conan or the Go proxy at
  your own registry or mirror. A registry can take a token (`token-env`) or
  a user and password (`basic-env`) from an environment variable; the
  secret never goes in the file, is only sent over https to that registry,
  and never follows a redirect to another host. `unripe config` and
  `unripe doctor` show whether each variable is set, never its value.
- Behind a proxy, a redirect to another host no longer carries the
  `Authorization` header with it. That also covers `GITHUB_TOKEN`.
- `--since` no longer fails on Windows with `WinError 6` when unripe is
  started without a console, by an agent or a service for example: git gets
  an empty stdin instead of an inherited one.
- A test that failed when `NO_COLOR` was already set now clears it first.
  Thanks to @tyza66 for reporting both.

## 0.6.1 (2026-10-22)

- The README names the MCP server the way the official MCP registry expects
  (`io.github.texWoW/unripe`), so unripe can be listed there. No code changes.

## 0.6.0 (2026-10-22)

New things to check:

- Dart and Flutter: `pubspec.lock`, dated by pub.dev. Retracted versions are
  never suggested.
- CocoaPods: `Podfile.lock`, dated by the CocoaPods trunk.
- Swift: `Package.resolved` (all three formats), for packages on GitHub,
  dated by the GitHub release for the tag.
- Terraform: `.terraform.lock.hcl`, dated by the Terraform registry.
- Conan: `conan.lock` from Conan 1 and 2, dated by when ConanCenter received
  the locked recipe revision. `UNRIPE_CONAN_REMOTE` points it at another
  server with Conan's v2 API.

For AI coding agents:

- `unripe mcp` runs a Model Context Protocol server on stdio with three
  read-only tools: `check_package`, `check_lockfiles` and
  `cooldown_settings`. It supports the 2026-07-28 protocol and the older
  handshake. [docs/agents.md](docs/agents.md) has the setup for Claude Code,
  Codex, VS Code, Cursor, Claude Desktop, Windsurf and Gemini CLI.

Trust:

- A version dated by a git commit time, which its author sets, is marked as
  such: `by its commit time` in text, `"dated_by": "commit"` in JSON. That
  covers actions and Swift tags with no release, and Go modules outside the
  checksum database.
- A publish time more than an hour in the future is reported as unknown
  instead of being used.
- Deprecated versions (npm deprecations, abandoned Composer packages) are
  no longer treated like yanked ones. They can still be suggested, after
  every version that isn't deprecated, and `explain` marks them.
- A registry answer over 256 MB is refused rather than read into memory.
- A TLS certificate error is not retried, and says to set
  `UNRIPE_CA_BUNDLE` or `SSL_CERT_FILE`.

Commands:

- `unripe doctor` shows the config, the lockfiles and whether each reads,
  the cache, proxy and CA settings, and whether each registry the lockfiles
  use answers.
- `unripe check --quiet` prints nothing when the check passes.
- `unripe init --dry-run` prints what it would write.
- The JSON report has `"schema": 1`, described in
  [docs/json.md](docs/json.md).

Docs: pipelines for Azure Pipelines, Jenkins, CircleCI and Bitbucket
([docs/ci.md](docs/ci.md)), Renovate and Dependabot
([docs/bots.md](docs/bots.md)), a threat model, replays of past attacks
([docs/case-studies.md](docs/case-studies.md)), and a
[roadmap](ROADMAP.md).

## 0.5.1 (2026-10-22)

- Much faster with nothing cached. Each lookup thread keeps its connection
  to a registry open instead of starting a new one for every request:
  2,048 versions across three big lockfiles took 6 to 7 seconds, against
  11 to 33 seconds before, depending on the network.
  Through a proxy, requests go the way they did before.
- An npm version that was unpublished is now reported even when another
  lockfile in the same run had already fetched that package.

## 0.5.0 (2026-10-22)

New things to check:

- Deno: reads `deno.lock` (versions 3 to 5), with npm packages dated by the
  npm registry and JSR packages by jsr.io.
- `pylock.toml`, the standard Python lockfile from PEP 751, using its own
  upload times when it has them.
- GitHub Actions, with `--workflows` or `workflows = true`. Each `uses:` ref
  is dated by the GitHub release that points at the same commit.
- A version npm has unpublished now counts as unknown, since that is how npm
  takes malicious versions down.

Policy:

- `[min-age-for]` sets a different minimum age for an ecosystem
  (`npm = "7d"`) or a package pattern (`"npm:@types/*" = "1d"`).
- `[[allow]]` lets one version through while it is too new, with a reason
  and an `until` date. Allowed versions are reported as `allowed`. unripe says
  when an entry has ended, is no longer needed, or matches nothing.
- `allow-security-fixes = true` lets a too new version through when an
  advisory on osv.dev names that exact version as the fix.
- The config is found in parent directories up to the top of the git
  repository, or wherever `UNRIPE_CONFIG` points.

Reports:

- A version that is too new comes with the newest version in the same line
  that is old enough, for npm, PyPI, Cargo, Composer, RubyGems, NuGet and JSR.
- New formats: `github` (annotations on the lockfile line), `codequality`
  (GitLab) and `junit`. `--report FORMAT:FILE` writes any of them next to the
  normal output, and `--json FILE` writes JSON.
- Colour on a terminal, off with `NO_COLOR`.
- The markdown report lists at most 50 versions, so a pull request comment
  stays readable.

Commands and options:

- `unripe explain ECOSYSTEM NAME` without a version lists the recent
  releases and the newest one that is old enough.
- `unripe config` shows the settings in use and the cooldown settings your
  package managers and bots have, and suggests the ones that are missing.
- `unripe init` writes a starter `.unripe.toml`, and with `--github` a
  workflow.
- `--as-of TIME` checks as if it were then.
- `--exit-zero` reports without failing. `--offline` uses only the cache.
  `--no-suggest` skips the suggestions. `--verbose` also prints how long the
  check took and how many requests it made.
- Company CA bundles from `REQUESTS_CA_BUNDLE` or `UNRIPE_CA_BUNDLE`.

CI:

- The Action marks problems on the lockfile lines (`annotations`), can report
  without failing (`fail: false`), can check workflows (`workflows: true`),
  and has outputs: `result`, `unripe`, `unknown`, `report` and `json`.
- The GitLab template feeds the merge request's code quality widget and test
  report.
- A second pre-commit hook, `unripe-changed`, checks only what a commit adds.
- Tested on Python 3.14.

## 0.4.0 (2026-10-21)

- The GitHub Action can comment on pull requests (`comment: true`). It posts
  one comment when something is too new or unknown and keeps it up to date on
  later runs. If it can't comment, it warns and the result is unchanged.
- A GitLab CI template, `ci/gitlab.yml`, that checks only what a merge request
  adds.

## 0.3.0 (2026-10-21)

- NuGet: reads `packages.lock.json` and asks nuget.org. Unlisted versions,
  which nuget.org dates to 1900, get their real time from the NuGet catalog.
- Gradle: reads `gradle.lockfile` and the older
  `gradle/dependency-locks/*.lockfile`, and asks Maven Central, Google's Maven
  repository and the Gradle plugin portal. Other repositories can be added
  with `maven-repositories` in the config or `UNRIPE_MAVEN_REPOS`.
- Registry answers are fetched gzipped, which makes big npm documents much
  quicker to download.

## 0.2.0 (2026-10-21)

- Go ages can no longer be faked with an old commit. The module proxy only
  knows commit times, which the author controls, so unripe now also asks
  Go's checksum database when a version first appeared and goes by that when
  the commit claims to be older. Honours `GOSUMDB`, `GONOSUMDB` and
  `GOPRIVATE`.
- The README's table of built-in cooldown settings is up to date with pip,
  Bundler, Hex, Cargo and Composer.
- Contributing guide, security policy and issue templates.

## 0.1.0 (2026-10-21)

First release. Reads package-lock.json, npm-shrinkwrap.json, pnpm-lock.yaml,
yarn.lock, bun.lock, uv.lock, poetry.lock, Pipfile.lock, pinned requirements
files, Cargo.lock, go.mod, composer.lock and Gemfile.lock. Reports as text,
JSON, SARIF or markdown. Ships as a command, a GitHub Action and a pre-commit
hook.
