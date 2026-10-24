# Roadmap

Where unripe is going, and what it won't do. The list started from a
70 item review of the project; every item is here with where it stands.

The rules every item has to keep:

- No runtime dependencies.
- An unknown age fails by default. Nothing quietly passes.
- A lockfile that can't be read fails the run. It is never read as empty.
- A registry's own record of a publish beats a timestamp someone can set.
- Old does not mean safe, and unripe never says it does.
- No telemetry, no accounts, no unripe server.
- No claim about speed or accuracy that hasn't been measured.

## Next

- **Hex (Elixir).** A pull request is in review.
- **R (CRAN).** A pull request is approved and waiting to be merged.
- **A benchmark script.** Reproducible cold and warm cache timings on public
  lockfiles, so speed claims can be checked by anyone.
- **The v1.0 contract.** Exit codes, the JSON schema
  ([docs/json.md](docs/json.md)), config keys and the Python API, written down
  as what won't change without a major version.
- **Research issues** for vcpkg, Nix flakes, Bazel's central registry and R
  (CRAN, renv). Each needs a publish time that can't be set by the author;
  the issue for each says where that would come from, if anywhere.

## Later

- Named policy presets and a shared base config for larger organisations.
- A configurable number of parallel lookups (16 now).
- Cache versioning, so a format change never misreads an old cache.
- A summary per directory for large monorepos.
- Policy drift as JSON (`unripe config` prints it as text today; the MCP
  server's `cooldown_settings` returns it as data).
- A searchable documentation site.
- A container image, if people ask for one.

## Not planned

- **Vulnerability data in findings.** Audit tools do this well; unripe
  answers a different question and should stay small.
- **Typosquatting checks.** A different problem with a high false positive
  rate. A brand new package already fails the age check.
- **A plugin system.** Adding an ecosystem is a parser and a lookup in this
  repository ([CONTRIBUTING.md](CONTRIBUTING.md)); a plugin interface would
  add a supply chain to a supply chain tool.
- **Usage measurement of any kind.** Not even opt in.
- **Standalone executables.** unripe is pure Python with no dependencies;
  `uvx unripe` already runs it anywhere Python does.
- **An interactive setup wizard.** `unripe init --dry-run` shows what would
  be written, and `unripe config` prints the lines to add for each package
  manager, which also works in CI.
- **HTML reports.** Markdown, SARIF, JUnit and code quality reports already
  show up where people look.

## All 70 items

Done means released, with tests. The version says when.

### Onboarding and everyday use

| # | Item | Status |
|---|---|---|
| 1 | Interactive setup wizard | Not planned (see above) |
| 2 | Monorepo and ecosystem detection | Done: every lockfile under the given paths, vendored folders skipped |
| 3 | `unripe doctor` | Done, 0.6.0 |
| 4 | Clear failures with next steps | Done: when it will be old enough, and the older version to use |
| 5 | Beginner explanations | Done, 0.6.0: [docs/threat-model.md](docs/threat-model.md), README examples |
| 6 | Dry run for config generation | Done, 0.6.0: `unripe init --dry-run` |
| 7 | Better terminal presentation | Done: progress line, colour on terminals, `NO_COLOR` |
| 8 | Versioned output schemas | Done, 0.6.0: `"schema": 1`, [docs/json.md](docs/json.md) |
| 9 | Quiet, normal and verbose output | Done, 0.6.0: `--quiet` added to `--verbose` |
| 10 | Better network and config errors | Done, 0.6.0: TLS and rate limit hints, `doctor` |

### Security and trust

| # | Item | Status |
|---|---|---|
| 11 | Threat model | Done, 0.6.0 |
| 12 | Publish time provenance | Partly: Go checksum database; commit-time dates labelled since 0.6.0 |
| 13 | Future-dated metadata | Done, 0.6.0: more than an hour ahead is unknown |
| 14 | Yanked, unpublished, missing, deprecated | Done, 0.6.0: deprecated is ranked last, not treated as pulled |
| 15 | Security-fix exceptions | Done: exact fixed version only, malware reports excluded |
| 16 | Vulnerability context | Not planned |
| 17 | Typosquatting | Not planned |
| 18 | Mirrors and private registries | Done, 0.7.0: `[registries]` with credentials from the environment |
| 19 | Parser fuzz testing | Done, 0.6.0: every fixture truncated and scrambled |
| 20 | Attack regression fixtures | Done, 0.6.0: [docs/case-studies.md](docs/case-studies.md) |

### Performance

| # | Item | Status |
|---|---|---|
| 21 | Reproducible benchmarks | Next |
| 22 | Configurable parallelism | Later |
| 23 | Cache integrity and versioning | Later |
| 24 | Changed-dependency scanning | Done: `--since` |
| 25 | Very large monorepos | Measured at 2,048 versions; Later for more |
| 26 | Retries and backoff | Done: bounded, honours `Retry-After` |
| 27 | Progress indicators | Done |
| 28 | No repeated requests | Done: one lookup per version, one request per package where the registry allows |
| 29 | Response size limits | Done, 0.6.0: 256 MB cap |
| 30 | Performance diagnostics | Done: `--verbose` prints time and request count |

### Ecosystems

| # | Item | Status |
|---|---|---|
| 31 | Dart and Flutter | Done, 0.6.0 |
| 32 | Elixir (Hex) | In review |
| 33 | Swift Package Manager | Done, 0.6.0 |
| 34 | CocoaPods | Done, 0.6.0 |
| 35 | Conan | Done, 0.6.0 |
| 36 | vcpkg | Research issue |
| 37 | Nix flakes | Research issue |
| 38 | Bazel | Research issue |
| 39 | Private registry setup | Done, 0.7.0, with 18 |
| 40 | Extension interface | Not planned as plugins; documented in CONTRIBUTING |
| | Terraform providers | Done, 0.6.0 (not on the original list) |

### Integrations

| # | Item | Status |
|---|---|---|
| 41 | GitHub Marketplace | Done: branding, inputs and outputs documented |
| 42 | Renovate | Done, 0.6.0: [docs/bots.md](docs/bots.md), and `unripe config` reads its settings |
| 43 | Dependabot | Done, 0.6.0: same |
| 44 | GitLab | Done: template with code quality and JUnit reports |
| 45 | Azure Pipelines | Done, 0.6.0: [docs/ci.md](docs/ci.md) |
| 46 | Jenkins | Done, 0.6.0: same |
| 47 | CircleCI | Done, 0.6.0: same (Bitbucket too) |
| 48 | Least-privilege workflows | Done: `unripe init --github` writes read-only permissions |
| 49 | Editor integration | Done, 0.6.0, as an MCP server for AI agents and editors: [docs/agents.md](docs/agents.md) |
| 50 | Container image | Later |

### Policy and reporting

| # | Item | Status |
|---|---|---|
| 51 | Policy presets | Later |
| 52 | Policy inheritance | Partly: config found up to the repository root, `UNRIPE_CONFIG`. Later |
| 53 | Exception metadata | Done: `[[allow]]` with reason and end date, listed in every report |
| 54 | Remediation steps | Done: the older version that passes |
| 55 | Policy simulation | Done: `--as-of` |
| 56 | Monorepo summaries | Later |
| 57 | Migrating from package manager settings | Done: `unripe config` |
| 58 | Policy drift as data | Later |
| 59 | Decision audit export | Done: the JSON report has every finding and why |
| 60 | HTML reports | Not planned |

### Distribution and community

| # | Item | Status |
|---|---|---|
| 61 | Standalone executables | Not planned |
| 62 | Documentation site | Later |
| 63 | Interactive examples | Done, 0.6.0: case studies anyone can rerun |
| 64 | Published benchmarks | Next, with 21 |
| 65 | Incident demonstrations | Done, 0.6.0 |
| 66 | Contributor docs and starter issues | Done: CONTRIBUTING, issue templates |
| 67 | Release provenance | Done: PyPI trusted publishing from a tagged release |
| 68 | Adoption measurement | Not planned |
| 69 | Issue triage | Done: bug and ecosystem templates |
| 70 | v1.0 contract | Next |
