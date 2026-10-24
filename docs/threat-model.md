# What unripe protects against, and what it doesn't

## The attack it is for

Someone gets publish rights to a package people already use: a stolen token,
a phished maintainer, a compromised CI job, a worm that spreads through
maintainers' credentials. They publish a new version with something bad in
it. Everyone whose lockfile picks up that version in the next few hours runs
it.

These versions are usually found and taken down quickly, often within hours,
because so many people install them at once. A project that refuses
versions younger than a few days mostly never sees them. unripe enforces
that in CI, from the lockfile, for every ecosystem in the repository.

## What it does not do

- **It does not judge code.** A version that passes is old, not safe. An old
  version can have a known vulnerability (run an audit tool for that) or a
  backdoor nobody has found yet.
- **It does not stop slow attacks.** A malicious version that stays
  unnoticed for longer than your minimum age passes. Longer minimum ages
  catch more and cost more; there is no age that catches everything.
- **It does not stop the install.** unripe runs after the lockfile is
  written, usually in CI. A developer who runs `npm install` on a
  compromised version has already run its install scripts. The package
  managers' own cooldown settings act at install time; use them too.
  `unripe config` shows which ones you have.
- **It does not check typosquats or new packages.** A brand new package is
  young, so it fails like any new version, but unripe has no opinion on
  whether `reqeusts` is the package you meant.
- **It does not protect against whoever controls your lockfile.** `uv.lock`,
  `pylock.toml` and `composer.lock` carry publish times, and unripe trusts
  them. Someone who can edit the lockfile can change those times, but they
  could also point it at any package they like.

## Where the dates come from, and how far to trust them

Most registries record when a version was uploaded, and the uploader can't
change that: npm, PyPI, crates.io, RubyGems, NuGet, Packagist, JSR, pub.dev,
the CocoaPods trunk, the Terraform registry and ConanCenter (the time a
recipe revision reached the server). Maven repositories don't record it, but
they never change a file once published, so the `Last-Modified` header of a
version's POM is its publish time.

CRAN does too, read through crandb.r-pkg.org, which keeps every version CRAN
published, archived ones included.

Some dates come from git instead, and a commit time is whatever its author
says:

- **Go.** The module proxy reports commit times. unripe also asks Go's
  checksum database when it first saw the version, and goes by that when the
  commit claims to be older. Modules outside the checksum database (private
  ones, or with `GONOSUMDB` set) only have the commit time.
  [go.md](go.md) has the details.
- **GitHub Actions and Swift packages.** Dated by the GitHub release for the
  tag. A tag or commit with no release falls back to the commit time.

When a version passes on a commit time, the report says so: `by its commit
time` in the text output, `"dated_by": "commit"` in JSON.

A publish time more than an hour in the future is not a real one. unripe
reports that version as unknown rather than working out an age from it.

## Failing closed

When unripe can't date a version, because the registry is down, rate
limiting, answering nonsense, or has never heard of it, the version is
unknown, and unknown fails by default. A check that passes when the registry
can't be reached is a check an attacker can switch off by blocking it.
`--unknown warn` changes that if you decide the trade is worth it.

A version the registry has taken down (unpublished on npm, deleted on PyPI,
a recipe revision ConanCenter doesn't have) is unknown too. That is how
registries remove malicious versions, so a lockfile still holding one should
fail.

A lockfile unripe can't parse fails the run with exit code 2. It is never
read as empty.

## Exceptions

`[[allow]]` lets one exact version through early, with a reason and an end
date, and every report lists it. `allow-security-fixes` lets a version
through when an osv.dev advisory names that exact version as the fix;
malware reports are never counted as fixes. Both are visible in the output,
so an exception can't hide in a config file.

## Privacy

unripe sends package names and versions to the registries that host them,
and with `allow-security-fixes`, to osv.dev. Nothing else leaves the
machine: no telemetry, no account, no unripe server. Credentials for a
registry in `[registries]` are read from an environment variable, sent only
over https to that registry's host and path, and dropped on any redirect to
another host. The same goes for `GITHUB_TOKEN`, which only goes to GitHub's
API. Answers are cached on
disk so a second run asks nothing.
