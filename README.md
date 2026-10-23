# unripe

[![PyPI](https://img.shields.io/pypi/v/unripe)](https://pypi.org/project/unripe/)
[![CI](https://github.com/TexWoW/unripe/actions/workflows/ci.yml/badge.svg)](https://github.com/TexWoW/unripe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<!-- mcp-name: io.github.texWoW/unripe -->

unripe checks your lockfiles and fails if any dependency version was
published too recently. The default is 3 days. It works across npm, pnpm,
yarn, bun, Deno, PyPI, Cargo, Go, Composer, RubyGems, NuGet, Gradle, Dart and
Flutter, CocoaPods, Swift, Terraform and Conan, and it can check the actions
your GitHub workflows use too. A repo with a Python backend, a JavaScript
frontend and a Flutter app gets one check and one policy instead of a
different setting in every tool.

It runs in CI, as a pre-commit hook, from Python, and as an
[MCP server](docs/agents.md) so AI coding agents check a version's age
before they add it.

Here it is on a small test project with Deno, Python and PHP parts, the
evening of October 21, 2026. `uv add` had just locked a certifi released two
days earlier:

```
$ unripe check
unripe: checked 867 versions in 4 lockfiles, minimum age 3d

unripe: 2 versions published less than 3d ago
  composer laravel/framework 12.70.1  13h old, published 2026-10-21 12:55 UTC, old enough at 2026-10-24 12:55 UTC
           php/composer.lock:1
           12.70.0 is old enough (published 2026-10-13)
  pypi     certifi 2026.10.19         47h old, published 2026-10-20 02:24 UTC, old enough at 2026-10-23 02:24 UTC
           api/pylock.toml:8, api/uv.lock:6
           2026.10.18 is old enough (published 2026-10-18)

fail: 2 unripe, 0 unknown, 865 ok, 0 excluded
```

Nothing is wrong with either of those. That's normal. Most new versions are
fine. The point is that when one isn't, you find out after everyone else
does, not before. And when one is too new, unripe tells you which older
version would pass.

## Why bother

When a popular package gets hijacked, the bad version is usually spotted
and pulled within hours. In August 2026 a worm went through npm and
published malicious versions of
[444 packages](https://cybernews.com/security/npm-supply-chain-attack-shai-hulud-worm/)
in about four hours, stealing CI and cloud credentials from whoever
installed them. In March, two backdoored LiteLLM releases sat on PyPI for
somewhere between
[40 minutes and three hours](https://docs.litellm.ai/blog/security-update-march-2026).
Waiting a few days before taking a new version would have skipped both.

You can check that claim. Here is a lockfile with the `debug` and `chalk`
versions from the September 2025 npm compromise, checked as of two hours
after they went out:

```
$ unripe check --as-of 2025-09-08T15:00Z
unripe: checked 3 versions in 1 lockfile, minimum age 3d

unripe: 2 versions published less than 3d ago
  npm      chalk 5.6.1  1h old, published 2025-09-08 13:13 UTC, old enough at 2025-09-11 13:13 UTC
           package-lock.json:3
           5.6.0 is old enough (published 2025-08-17)
  npm      debug 4.4.2  1h old, published 2025-09-08 13:12 UTC, old enough at 2025-09-11 13:12 UTC
           package-lock.json:2
           4.4.1 is old enough (published 2025-05-13)

fail: 2 unripe, 0 unknown, 1 ok, 0 excluded
```

Run it today and both come back as unpublished, which is how npm takes
malicious versions down, so a lockfile that still has one fails either way.

Most package managers have added a setting for this in the last year, and
if yours has one you should turn it on. unripe is for what those settings
don't do. It checks what is already in the lockfile, including whatever a bot,
an agent or an old tool put there. It reports on just the versions a pull
request adds. It applies one policy to every ecosystem in the repo. And it
covers Go, Composer, NuGet, Gradle, Dart, CocoaPods, Swift, Terraform, Conan
and GitHub Actions, which don't have a setting yet.

[docs/case-studies.md](docs/case-studies.md) replays more incidents the same
way, and [docs/threat-model.md](docs/threat-model.md) says plainly what a
cooldown does not protect against.

## Install

You need Python 3.11 or newer. unripe has no dependencies, on purpose: a
tool that checks your supply chain shouldn't add to it.

```
uvx unripe check
```

or `pipx install unripe`, or `pip install unripe`. To start a config and a
GitHub workflow in one go:

```
unripe init --github
```

`unripe doctor` checks the setup: which config and lockfiles it finds,
whether each lockfile reads, and whether each registry they use answers.

## Examples

Check everything under the current directory:

```
unripe
```

Only the versions a branch adds, the way a pull request check does:

```
unripe check --since origin/main
```

Pick a version before adding it. Without a version, `explain` lists recent
releases and marks the newest one that is old enough:

```
$ unripe explain pypi certifi
pypi certifi: 77 versions, minimum age 3d
  2026.10.19  2026-10-20 02:24 UTC    2d 8h ago  too new, old enough at 2026-10-23 02:24 UTC
  2026.10.18  2026-10-18 16:21 UTC   3d 19h ago  old enough  <- newest old enough
  2026.7.22   2026-07-22 03:35 UTC      13w ago  old enough
  ...
```

A stricter rule for one ecosystem and a looser one for type packages, in
`.unripe.toml`:

```toml
min-age = "3d"

[min-age-for]
npm = "7d"
"npm:@types/*" = "1d"
```

Your own packages are always new; skip them:

```
unripe check --exclude "npm:@myorg/*"
```

A version you need now, let through until a date, with the reason in the
report:

```toml
[[allow]]
package = "npm:axios@1.13.2"
reason = "fixes the redirect bug we hit in production"
until = 2026-11-15
```

Results for a script:

```
unripe check --format json | jq -r '.findings[] | select(.state == "unripe") | "\(.name) \(.version)"'
```

Quiet unless something is wrong, for a git hook or a cron job:

```
unripe check --quiet
```

## Lockfiles it reads

| File | Publish times come from |
|---|---|
| `package-lock.json`, `npm-shrinkwrap.json` | npm registry |
| `pnpm-lock.yaml` | npm registry |
| `yarn.lock` (classic and berry) | npm registry |
| `bun.lock` | npm registry |
| `deno.lock` | npm registry and jsr.io |
| `uv.lock` | the lockfile's own upload times |
| `pylock.toml`, `pylock.*.toml` | the lockfile's own upload times, else PyPI |
| `poetry.lock`, `Pipfile.lock` | PyPI |
| `requirements*.txt`, `constraints*.txt` | PyPI (exact `==` pins only) |
| `Cargo.lock` | crates.io sparse index |
| `go.mod` | Go module proxy and checksum database |
| `composer.lock` | the lockfile's own `time` field, else Packagist |
| `Gemfile.lock` | rubygems.org |
| `packages.lock.json` | nuget.org |
| `gradle.lockfile`, `gradle/dependency-locks/*.lockfile` | Maven Central, Google's Maven repository, the Gradle plugin portal |
| `pubspec.lock` (Dart and Flutter) | pub.dev |
| `Podfile.lock` | the CocoaPods trunk |
| `Package.resolved` (Swift) | GitHub releases, for packages on GitHub |
| `.terraform.lock.hcl` | the Terraform registry |
| `conan.lock` (Conan 1 and 2) | ConanCenter, by recipe revision |
| `renv.lock` | CRAN |
| `.github/workflows/*.yml`, `action.yml` (with `--workflows`) | GitHub releases |

Dependencies from git, a local path, a workspace or another registry are
skipped, since there's nothing public to ask about them. The old binary
`bun.lockb` can't be read; bun 1.2 and later write a text `bun.lock`.

NuGet's `packages.lock.json` doesn't record which feed a package came from,
so unripe looks everything up on nuget.org. Packages from a private feed come
back unknown; exclude them. For Gradle, artifacts that live in another Maven
repository (Confluent's, say) can be found by adding it to
`maven-repositories` in the config.

For Go it reads `go.mod`, not `go.sum`. Since Go 1.17, `go.mod` lists every
module in the build, while `go.sum` also remembers versions that aren't used.

Swift packages are dated like GitHub Actions, below, so only packages hosted
on GitHub are checked. Terraform providers from other registries, including
OpenTofu's, are skipped. A Conan version is dated by the recipe revision the
lockfile pins, since a new revision of an old version is new code; recipes
with a user and channel aren't on ConanCenter and are skipped.

renv.lock packages are read from CRAN, or from a Posit Package Manager mirror
serving CRAN under `/cran/`, under whatever name the repository was given.
Packages from Bioconductor, GitHub, a local path or another repository are
skipped, since CRAN is the only public registry unripe can ask about them.

### GitHub Actions

With `--workflows` (or `workflows = true` in the config), unripe also reads
the `uses:` lines in your workflows and composite actions. Each one is dated
by the GitHub release whose tag points at the same commit, so
`actions/checkout@v7` and `actions/checkout@3d3c42e...` are both judged by
when that commit was released. A commit with no release falls back to its
commit time. An action used at a branch, like `@main`, has no release to go
by, so it comes back unknown. Set `GITHUB_TOKEN` to get past GitHub's limit
of 60 requests an hour; in the Action it is set for you.

A commit time is whatever the commit's author says it is, so a version that
passes on one is marked: `by its commit time` in the text report and
`"dated_by": "commit"` in JSON.

## In GitHub Actions

```yaml
name: unripe
on: pull_request

permissions:
  contents: read
  pull-requests: write  # only needed for comment: true

jobs:
  unripe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: TexWoW/unripe@v0.6.1
        with:
          min-age: 3d
          comment: true
```

On a pull request it only looks at versions the PR adds, compared with the
base commit. It fetches that commit if the checkout is shallow, so you don't
need `fetch-depth: 0`. On a push it checks everything.

Each version that is too new is marked on its line in the lockfile, in the
pull request's files view. The report goes to the job summary, and with
`comment: true` also to a comment on the pull request, so reviewers see it
without opening the logs. That's one comment, posted when something is too
new or unknown and edited in place on later runs, so it doesn't pile up. If
the job can't comment, for example on a pull request from a fork, it says so
in the log and the check result stays the same.

Pin the action to a full commit sha rather than a tag if you can. The action
runs unripe from its own checkout, so the code that runs is the commit you
pinned, and nothing gets downloaded from PyPI.

Inputs:

| Input | Default | |
|---|---|---|
| `min-age` | `3d` | `90m`, `72h`, `3d`, `1w` |
| `since` | PR base | a git ref, or `none` to check everything |
| `paths` | whole repo | files or directories, space or newline separated |
| `exclude` | | patterns, one per line |
| `unknown` | `fail` | set to `warn` to let unknown ages pass |
| `fail` | `true` | set to `false` to report without failing, while you try it out |
| `workflows` | `false` | also check the actions your workflows use |
| `annotations` | `true` | mark problems on the lockfile lines |
| `summary` | `true` | write the job summary |
| `sarif` | | also write SARIF to this path |
| `comment` | `false` | comment on the pull request when something is too new or unknown |
| `github-token` | `github.token` | the token used for that comment and for dating actions |
| `config` | | a config file other than the defaults |

Outputs: `result` (`pass`, `fail` or `error`), `unripe` and `unknown` (how
many versions), and `report` and `json` (paths to the markdown and JSON
reports).

If you want the results as code scanning alerts, write SARIF and upload it
(this needs `security-events: write`):

```yaml
      - uses: TexWoW/unripe@v0.6.1
        with:
          sarif: unripe.sarif
      - uses: github/codeql-action/upload-sarif@v4
        if: always()
        with:
          sarif_file: unripe.sarif
```

## As a pre-commit hook

```yaml
repos:
  - repo: https://github.com/TexWoW/unripe
    rev: v0.6.1
    hooks:
      - id: unripe
```

It runs when a lockfile changes. Use the `unripe-changed` hook instead if
you only want it to look at what the commit adds.

## In GitLab CI

```yaml
include:
  - remote: https://raw.githubusercontent.com/TexWoW/unripe/v0.6.1/ci/gitlab.yml
```

That adds an `unripe` job to the `test` stage. On merge requests it checks
only the versions the merge request adds; on the default branch it checks
everything. Findings show in the merge request's code quality widget and
test report, and the markdown report is kept as a job artifact. Set
`UNRIPE_MIN_AGE` or `UNRIPE_ARGS` (for example `--exclude npm:@myorg/*`) in
your own `variables:` to change it.

## In other CI systems

[docs/ci.md](docs/ci.md) has pipelines for Azure Pipelines, Jenkins,
CircleCI and Bitbucket. `--format junit` works with all of them, and
`--report FORMAT:FILE` writes any format to a file next to the normal
output. [docs/bots.md](docs/bots.md) covers Renovate and Dependabot: set
their cooldowns too, so they stop opening pull requests unripe would fail.

## With AI coding agents

```bash
claude mcp add unripe -- uvx unripe@0.6.1 mcp
```

`unripe mcp` is a Model Context Protocol server with three read-only tools:
`check_package` (how old a version is, or the newest version that is old
enough), `check_lockfiles` and `cooldown_settings`. It works with Claude
Code, Codex, Copilot in VS Code, Cursor, Claude Desktop, Windsurf and Gemini
CLI; [docs/agents.md](docs/agents.md) has the setup for each, and a line to
put in `AGENTS.md` so the agent uses it before adding a dependency.

## Configuration

Put settings in `.unripe.toml`, or under `[tool.unripe]` in
`pyproject.toml`. unripe looks in the current directory and then each one
above it, up to the top of the git repository, or reads the file named in
`UNRIPE_CONFIG`. Flags on the command line win. Excludes from both are
combined.

```toml
min-age = "3d"
unknown = "fail"
paths = ["web", "api"]
maven-repositories = ["https://packages.confluent.io/maven"]
exclude = [
  "npm:@myorg/*",
  "left-pad@1.3.0",
]

# check workflow actions too
workflows = true

# let a version through early when a security advisory names it as the fix
allow-security-fixes = true

# a different minimum age for an ecosystem or some packages
[min-age-for]
npm = "7d"
"npm:@types/*" = "1d"

# one version let through early, with a reason and an end date
[[allow]]
package = "npm:axios@1.13.2"
reason = "fixes the redirect bug we hit in production"
until = 2026-11-15
```

An exclude is a package name or a glob. Prefix it with an ecosystem
(`npm:`, `pypi:`, `cargo:`, `go:`, `composer:`, `rubygems:`, `nuget:`,
`maven:`, `jsr:`, `actions:`, `pub:`, `cocoapods:`, `swift:`, `terraform:`,
`conan:`, `cran:`) to limit it to one, and add `@version` to limit
it to certain versions, like `@types/node@20.*`. Gradle packages are named
`group:artifact`, like `maven:com.mycorp:*`. PyPI names are normalised the
way PyPI does it, so `Typing_Extensions` and `typing-extensions` are the same
package, and NuGet names ignore case.

Excludes are for packages you never want checked, like your own. For a
version you need now, use `[[allow]]` instead. It only lets that version
through while it is too new, it shows up in the report as allowed, and once
the `until` date has passed it stops applying. unripe also says when an allow
entry is no longer needed because the version has aged past the minimum.

`min-age-for` takes an ecosystem name or a package pattern. A package
pattern wins over an ecosystem, and the first pattern that matches is used.

`allow-security-fixes` asks [osv.dev](https://osv.dev) about each version
that is too new. If an advisory names that exact version as the fix, the
version is allowed through and the report says which advisory. A newer
version that happens to come after the fix doesn't count, and neither do
malware reports. It's off by default, since it means one more request for
each version that is too new.

`unripe config` shows the settings in use and where they came from. It also
reads the cooldown settings your package managers and update bots already
have (`.npmrc`, `pnpm-workspace.yaml`, `.yarnrc.yml`, `bunfig.toml`, uv,
Poetry, Deno, Cargo, Bundler, Dependabot and Renovate), says where they
disagree with unripe, and prints the lines you could add for the ones that
are missing.

## Command line

```
unripe check [paths...]
  --min-age AGE             default 3d
  --since REF               only versions added since this git ref
  --exclude PATTERN         can be repeated
  --unknown fail|warn       default fail
  --allow-security-fixes    let through versions an advisory names as the fix
  --workflows               also check GitHub Actions in workflows
  --format FORMAT           text, json, sarif, markdown, github, codequality or junit
  --output FILE             write the report here instead of stdout
  --summary FILE            also append a markdown report, e.g. $GITHUB_STEP_SUMMARY
  --sarif FILE              also write a SARIF report
  --json FILE               also write a JSON report
  --report FORMAT:FILE      also write any other format, can be repeated
  --annotate                also print GitHub annotations
  --as-of TIME              check as if it were then, like 2025-09-08T15:00Z
  --exit-zero               report, but don't fail
  --offline                 use only cached publish times
  --no-suggest              don't look for an older version that passes
  --config FILE
  --no-cache
  --verbose                 list the versions that passed too, and the timing
  --quiet                   print nothing when the check passes

unripe explain ECOSYSTEM NAME [VERSION]
unripe config
unripe doctor [paths...]
unripe init [--github] [--dry-run]
unripe mcp
```

`unripe` with no command runs `unripe check`. It exits 0 when everything is
old enough, 1 when something is too new or has an unknown age, and 2 when a
lockfile can't be read, no lockfile was found, or an option is wrong. The
JSON report's fields are described in [docs/json.md](docs/json.md).

`explain` answers a single question:

```
$ unripe explain npm left-pad 1.3.0
npm left-pad 1.3.0: published 2018-04-09 01:10 UTC, 8y ago
old enough: the minimum age is 3d
```

Leave the version out to see the recent releases and which one is the
newest you could use today:

```
$ unripe explain composer laravel/framework
composer laravel/framework: 1303 versions, minimum age 3d, 11 prereleases not shown
  12.70.1  2026-10-21 12:55 UTC      13h ago  too new, old enough at 2026-10-24 12:55 UTC
  13.38.0  2026-10-20 10:45 UTC      39h ago  too new, old enough at 2026-10-23 10:45 UTC
  13.37.0  2026-10-16 04:59 UTC   5d 21h ago  old enough  <- newest old enough
```

## Details worth knowing

If a registry can't tell unripe when a version was published, because it's
down or rate limiting or has never heard of the version, that version counts
as unknown, and unknown fails. A check that quietly passes when the registry
is unreachable is one an attacker can turn off. Use `--unknown warn` if you'd
rather not. A version that npm has unpublished counts as unknown too, since
that's how npm takes malicious versions down.

A publish time more than an hour in the future is not a real one, so that
version counts as unknown too.

When a version is too new, unripe looks for the newest version in the same
release line (same major version, or same minor below 1.0) that is old
enough, skipping prereleases and anything yanked or unpublished, and
preferring versions that aren't deprecated. It does this for npm, PyPI,
Cargo, Composer, RubyGems, NuGet, JSR, pub.dev, CocoaPods and Terraform.

Publish times never change, so answers are cached on disk forever, in
`~/.cache/unripe` (`%LOCALAPPDATA%\unripe\cache` on Windows), or wherever
`UNRIPE_CACHE` points. A second run on the same lockfiles makes no requests,
apart from finding older versions for anything that is too new, and
`--offline` makes none at all. Each version is looked up once even if
several lockfiles have it, lookups run 16 at a time, and each keeps its
connection to a registry open between requests. For npm,
Cargo, Composer, RubyGems, JSR, pub.dev, CocoaPods and Terraform one request
covers every version of a package. GitHub Action tags like `v7` move, so those are looked up every run.

NuGet and Maven needed care too. nuget.org reports an unlisted version as
published in 1900, so unripe reads its real time from the NuGet catalog
instead; otherwise a version unlisted the day it came out would look 126
years old. Maven repositories never change a published file, so a POM's
`Last-Modified` header is when the version was published, and it's there
the moment the file is, before the search index catches up.

Mirrors work through `UNRIPE_NPM_REGISTRY`, `UNRIPE_PYPI_URL` and
`UNRIPE_CONAN_REMOTE` (any server with Conan's v2 API), and
`UNRIPE_MAVEN_REPOS` (comma separated) adds Maven repositories the same way
`maven-repositories` does. For Go, unripe uses the first proxy in `GOPROXY`.
Behind a corporate proxy, `HTTPS_PROXY` and `NO_PROXY` work as usual, and a
company CA bundle can be given with `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` or
`UNRIPE_CA_BUNDLE`. A certificate error says so, rather than being retried.

Go needed extra work. The module proxy only reports a version's commit
time, and a commit time is whatever the author says it is, so a tag pushed
today on an old commit (or a commit dated last year on purpose) would look
old. So unripe also asks Go's checksum database, sum.golang.org, which gives
every module version a number in the order it first saw them and can't be
rewritten. Placing that number against the timeline of the Go module index
says roughly when the version first appeared. If the commit claims to be
older than that, unripe goes by first appearance. For recent versions the
estimate is within minutes; for old ones, where it can't narrow things down,
it uses the youngest age it can't rule out. `GOSUMDB`, `GONOSUMDB` and
`GOPRIVATE` are honoured, and private modules fall back to the commit time.
[docs/go.md](docs/go.md) explains how it works.

`uv.lock`, `pylock.toml` and `composer.lock` carry publish times themselves
and unripe trusts them. Someone who can edit your lockfile can change those
times, but they could also point it at any package they like, so that isn't
something unripe tries to defend against.

## Next to the built-in settings

As of October 2026:

| | Setting |
|---|---|
| npm 11.10+ | `min-release-age` in `.npmrc`, in days |
| pnpm 10.16+ | `minimumReleaseAge` in `pnpm-workspace.yaml`, in minutes (on by default since pnpm 11, at one day) |
| yarn 4.10+ | `npmMinimalAgeGate` in `.yarnrc.yml` |
| bun 1.3+ | `minimumReleaseAge` in `bunfig.toml`, in seconds |
| Deno | `minimumDependencyAge` in `deno.json` (on by default since Deno 2.9, at 24 hours) |
| uv | `exclude-newer`, a date or a duration like `7 days` |
| pip 26.1+ | `--uploaded-prior-to P3D` |
| Poetry 2.4+ | `solver.min-release-age`, in days |
| Bundler 4.0.13+ | `bundle config set cooldown 3`, in days |
| Hex 2.5.0+ | `mix hex.config cooldown 3d` |
| Cargo | `registry.global-min-publish-age`, unstable now, expected stable in Rust 1.100 |
| Composer | proposed |
| Go, NuGet, Maven, Gradle, GitHub Actions | nothing yet |
| Dart, CocoaPods, Swift, Terraform, Conan | nothing we know of |

These stop a new version at install time, which unripe can't do, so use
them where you can; `unripe config` prints the lines for your repo. unripe
is the check in CI afterwards: one policy for the whole repo, run against
what actually got locked. One more difference: some of the built-in
settings let a version through when they can't find its publish date.
unripe fails instead.

[cooldowns.dev](https://cooldowns.dev) keeps a longer, up to date list of
these settings, with examples.

## Using it from Python

```python
import unripe

for finding in unripe.scan(["."], min_age="3d", exclude=["npm:@myorg/*"]):
    if finding.state != "ok":
        print(finding.package, finding.state, finding.reason)
```

Each finding's state is `ok`, `unripe`, `unknown`, `allowed` or `excluded`.
`scan()` raises `unripe.check.Problem` if a lockfile can't be read or the
`since` ref doesn't exist.

## Questions

**Our own packages are always new.** Exclude them, e.g. `npm:@myorg/*`.

**We need a version that came out yesterday.** Add an `[[allow]]` entry for
that exact version with a reason and an `until` date. If it's a security
fix, `allow-security-fixes = true` handles it without an entry.

**We use a private registry.** unripe can't see into it, so those packages
come back unknown. Exclude them, or use `--unknown warn`. For private Go
modules, point `GOPROXY` at your own proxy.

**Does this replace `npm audit` or similar?** No. Audit tools know about
reported vulnerabilities. unripe doesn't know anything about any package. It
just won't trust a version until it has been around for a while. They catch
different things, so run both.

**Can we try it without breaking builds?** Use `fail: false` in the Action,
or `--exit-zero` on the command line. Everything is still reported.

**Is it slow?** Not very. Discourse's `Gemfile.lock`, Cargo's `Cargo.lock`
and Vite's `pnpm-lock.yaml`, 2,048 versions between them, took 7 seconds
with nothing cached and a third of a second after that.

## More

- [docs/agents.md](docs/agents.md): AI coding agents and MCP
- [docs/ci.md](docs/ci.md): Azure Pipelines, Jenkins, CircleCI, Bitbucket
- [docs/bots.md](docs/bots.md): Renovate and Dependabot
- [docs/case-studies.md](docs/case-studies.md): past attacks, replayed with `--as-of`
- [docs/threat-model.md](docs/threat-model.md): what it protects against, and what it doesn't
- [docs/json.md](docs/json.md): the JSON report
- [docs/go.md](docs/go.md): how Go versions are dated
- [ROADMAP.md](ROADMAP.md): what's next, and what isn't planned

## Contributing

Bug reports and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md)
explains how to run the tests and how to add a new ecosystem, which is the
most useful thing anyone could add right now.

## License

MIT
