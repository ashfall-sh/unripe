"""The unripe command."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from unripe import __version__, native, registries as network
from unripe.check import Problem, check, display, judge, releases
from unripe.lockfiles.python import normal
from unripe.model import ECOSYSTEMS, Package
from unripe.policy import Policy, PolicyError, build, config, duration, spoken
from unripe.registries import Cache, Missing, Registries, Unreachable, cachedir
from unripe.report import FORMATS, ago, annotations, explanation, plural, render, stamp

COMMANDS = ("check", "explain", "config", "init", "doctor", "mcp")
# one old, well known version per registry, for `unripe doctor` to look up
CANARIES = {
    "npm": ("left-pad", "1.3.0"),
    "pypi": ("idna", "3.6"),
    "cargo": ("serde", "1.0.197"),
    "go": ("github.com/BurntSushi/toml", "v1.3.2"),
    "composer": ("monolog/monolog", "3.5.0"),
    "rubygems": ("rake", "13.0.6"),
    "nuget": ("Newtonsoft.Json", "13.0.3"),
    "maven": ("org.slf4j:slf4j-api", "2.0.9"),
    "jsr": ("@std/path", "1.0.0"),
    "actions": ("actions/checkout", "v4.1.0"),
    "pub": ("http", "1.1.0"),
    "cocoapods": ("Alamofire", "5.8.1"),
    "swift": ("apple/swift-argument-parser", "1.3.0"),
    "terraform": ("hashicorp/aws", "5.31.0"),
    "conan": ("zlib", "1.3.1"),
    "cran": ("jsonlite", "1.8.4"),
}


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="unripe",
        description="Fail when a locked dependency version was published more recently than you trust.",
    )
    top.add_argument("--version", action="version", version=f"unripe {__version__}")
    commands = top.add_subparsers(dest="command", metavar="command")

    run = commands.add_parser(
        "check",
        help="check every lockfile under the given paths (the default command)",
        description="Check every lockfile under the given files and directories. Exit 0 when every version "
        "is old enough, 1 when one is not (or its age is unknown), 2 when a lockfile could not be read.",
    )
    run.add_argument("paths", nargs="*", help="lockfiles or directories to search (default: the config's paths, or .)")
    run.add_argument("--min-age", metavar="AGE", help="how old a version must be, like 90m, 72h, 3d or 1w (default 3d)")
    run.add_argument("--since", metavar="REF", help="only check versions added since this git ref, like origin/main")
    run.add_argument(
        "--exclude",
        metavar="PATTERN",
        action="append",
        default=[],
        help="skip packages matching a name or glob, optionally with an ecosystem (npm:@types/*) or a version (name@1.2.3). repeatable",
    )
    run.add_argument("--unknown", choices=("fail", "warn"), help="what an unknown publish time does (default fail)")
    run.add_argument(
        "--allow-security-fixes",
        action="store_true",
        default=None,
        help="let a too new version through when a security advisory names it as the fix (asks osv.dev)",
    )
    run.add_argument(
        "--workflows",
        action="store_true",
        default=None,
        help="also check the actions GitHub workflows use, dated by their releases (set GITHUB_TOKEN for more requests)",
    )
    run.add_argument("--format", choices=FORMATS, default="text", help="report format (default text)")
    run.add_argument("--output", metavar="FILE", help="write the report to a file instead of stdout")
    run.add_argument("--summary", metavar="FILE", help="also append a markdown report to this file, like $GITHUB_STEP_SUMMARY")
    run.add_argument("--sarif", metavar="FILE", help="also write a SARIF report to this file, for code scanning")
    run.add_argument("--json", metavar="FILE", help="also write a JSON report to this file")
    run.add_argument(
        "--report",
        metavar="FORMAT:FILE",
        action="append",
        default=[],
        help="also write a report in another format, like codequality:gl-code-quality-report.json or junit:unripe.xml. repeatable",
    )
    run.add_argument("--annotate", action="store_true", help="also print GitHub Actions annotations, so problems show on the lockfile line")
    run.add_argument("--as-of", metavar="TIME", help="check as if it were this moment, like 2026-09-08 or 2026-09-08T14:00Z")
    run.add_argument("--exit-zero", action="store_true", help="report, but exit 0 even when something is too new or unknown")
    run.add_argument("--config", metavar="FILE", help="read settings from this file instead of .unripe.toml or pyproject.toml")
    run.add_argument("--no-cache", action="store_true", help="ask the registries again instead of using cached publish times")
    run.add_argument("--offline", action="store_true", help="use only cached publish times; anything not cached is unknown")
    run.add_argument("--no-suggest", action="store_true", help="don't look for an older version that is old enough")
    run.add_argument("--verbose", "-v", action="store_true", help="list the versions that passed too, and how long the check took")
    run.add_argument("--quiet", "-q", action="store_true", help="print nothing when the check passes")

    one = commands.add_parser(
        "explain",
        help="say when a version was published and whether it passes, or list a package's recent versions",
        description="Look up one version and say when it was published and whether it is old enough. "
        "Without a version, list the package's recent versions and say which is the newest old enough.",
    )
    one.add_argument("ecosystem", choices=ECOSYSTEMS, help="which registry to ask")
    one.add_argument("name", help="the package, crate, module or gem name")
    one.add_argument("version", nargs="?", help="the exact version (leave out to list recent versions)")
    one.add_argument("--min-age", metavar="AGE", default=None, help="how old a version must be (default 3d)")
    one.add_argument("--no-cache", action="store_true", help="ask the registry again instead of using the cache")

    settings = commands.add_parser(
        "config",
        help="show the settings unripe would use, and the package managers' own cooldown settings",
        description="Show the policy unripe would apply here and where it came from, then the cooldown settings "
        "package managers and update bots have in this directory, and which ones could be added.",
    )
    settings.add_argument("--config", metavar="FILE", help="read settings from this file instead of .unripe.toml or pyproject.toml")

    doctor = commands.add_parser(
        "doctor",
        help="check that unripe can read the lockfiles here and reach their registries",
        description="Say which config and lockfiles unripe finds here, whether each lockfile reads, whether the "
        "cache can be written, and whether each registry the lockfiles use answers. Exit 1 when something is wrong.",
    )
    doctor.add_argument("paths", nargs="*", help="lockfiles or directories to search (default .)")
    doctor.add_argument("--config", metavar="FILE", help="read settings from this file instead of .unripe.toml or pyproject.toml")

    commands.add_parser(
        "mcp",
        help="run an MCP server on stdio, so AI coding agents can check package ages",
        description="Run a Model Context Protocol server on stdin and stdout. Agents such as Claude Code, Codex, "
        "Cursor and Copilot can then check a version's age before adding it, and check lockfiles after changing them.",
    )

    start = commands.add_parser(
        "init",
        help="write a starter .unripe.toml, and a GitHub workflow with --github",
        description="Write a starter .unripe.toml here. With --github, also write .github/workflows/unripe.yml.",
    )
    start.add_argument("--min-age", metavar="AGE", default="3d", help="the minimum age to start with (default 3d)")
    start.add_argument("--github", action="store_true", help="also write a GitHub Actions workflow")
    start.add_argument("--force", action="store_true", help="overwrite files that already exist")
    start.add_argument("--dry-run", action="store_true", help="print what would be written, and write nothing")
    return top


def registries(nocache: bool, mavenrepos: list[str] | None = None, now: datetime | None = None, offline: bool = False) -> Registries:
    return Registries(Cache(None if nocache else cachedir()), mavenrepos=mavenrepos or (), now=now, offline=offline)


def progress(done: int, total: int) -> None:
    sys.stderr.write(f"\rchecking {total} versions: {done}/{total}")
    if done == total:
        sys.stderr.write("\r" + " " * 40 + "\r")
    sys.stderr.flush()


def write(path: str, content: str, append: bool = False) -> None:
    with open(path, "a" if append else "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def colored() -> bool:
    """Colour on a terminal, unless NO_COLOR is set; FORCE_COLOR turns it on anywhere."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def moment(text: str) -> datetime:
    """--as-of: a date (midnight UTC) or a date and time. A time without a zone is UTC."""
    try:
        when = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise PolicyError(f"--as-of {text!r} is not a date or time; use something like 2026-09-08 or 2026-09-08T14:00Z") from error
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def here(source: Path | None) -> bool:
    """Whether a config file is in the directory unripe runs in."""
    return source is not None and source.resolve().parent == Path.cwd().resolve()


def extra(text: str) -> tuple[str, str]:
    form, colon, path = text.partition(":")
    if not colon or form not in FORMATS or not path:
        raise PolicyError(f"--report {text!r}: use FORMAT:FILE, where FORMAT is one of {', '.join(FORMATS)}")
    return form, path


def runcheck(args: argparse.Namespace) -> int:
    extras = [extra(text) for text in args.report]
    settings, source = config(".", args.config)
    policy = build(settings, args.min_age, args.exclude, args.unknown, source, args.allow_security_fixes, args.workflows)
    # a config found further up the repository sets the policy, but its paths are relative to where it is
    paths = args.paths or (policy.paths if args.config or os.environ.get("UNRIPE_CONFIG") or here(source) else []) or ["."]
    missing = [path for path in paths if not Path(path).exists()]
    if missing:
        raise PolicyError(f"no such file or directory: {', '.join(missing)}")
    now = moment(args.as_of) if args.as_of else datetime.now(timezone.utc)
    show = progress if sys.stderr.isatty() else None
    started = time.monotonic()
    found = registries(args.no_cache, policy.maven_repositories, now, args.offline)
    result = check(paths, policy, found, since=args.since, now=now, progress=show, suggest=not args.no_suggest)

    report = render(result, args.format, args.verbose, color=args.format == "text" and not args.output and colored())
    if args.output:
        write(args.output, report)
    elif not (args.quiet and result.exitcode == 0):
        sys.stdout.write(report)
    if args.annotate and args.format != "github":
        sys.stdout.write("".join(row + "\n" for row in annotations(result, soft=args.exit_zero)))
    if args.summary:
        write(args.summary, render(result, "markdown"), append=True)
    if args.sarif:
        write(args.sarif, render(result, "sarif"))
    if args.json:
        write(args.json, render(result, "json"))
    for form, path in extras:
        write(path, render(result, form))
    if args.verbose:
        sys.stderr.write(f"unripe: took {time.monotonic() - started:.1f}s and {network.REQUESTS[0]} requests\n")
    if args.exit_zero and result.exitcode == 1:
        return 0
    return result.exitcode


def listing(package: Package, policy: Policy, found: Registries, now: datetime) -> int:
    """`unripe explain ECOSYSTEM NAME`: the recent versions, and the newest that is old enough."""
    minimum = policy.age(package.ecosystem, package.name, "")
    try:
        listed = releases(found, package.ecosystem, package.name, minimum, now)
    except Missing as error:
        if "cannot list" in str(error):
            raise PolicyError(f"unripe cannot list every version on {package.ecosystem}; give a version") from error
        sys.stdout.write(f"{package.ecosystem} {package.name}: not found\n")
        return 1
    except Unreachable as error:
        sys.stdout.write(f"{package.ecosystem} {package.name}: the registry did not answer ({error})\n")
        return 1
    shown, newest = listed.shown, listed.newest
    head = f"{package.ecosystem} {package.name}: {plural(listed.total, 'version')}, minimum age {spoken(minimum)}"
    out = [head + (f", {plural(listed.prereleases, 'prerelease')} not shown" if listed.prereleases else "")]
    width = max(len(version) for version, _ in shown) if shown else 0
    for version, published in shown:
        if version in listed.pulled:
            status = "yanked or unpublished"
        elif published > now - minimum:
            status = f"too new, old enough at {stamp(published + minimum)}"
        else:
            status = "old enough"
        if version in listed.deprecated and version not in listed.pulled:
            status += ", deprecated"
        tag = "  <- newest old enough" if newest and version == newest[0] else ""
        out.append(f"  {version:<{width}}  {stamp(published)}  {ago((now - published).total_seconds()):>7} ago  {status}{tag}")
    if newest and newest[0] not in [version for version, _ in shown]:
        out.append(f"  newest old enough: {newest[0]}, published {stamp(newest[1])}")
    elif not newest:
        out.append("  no release is old enough yet")
    found.cache.save()
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def runexplain(args: argparse.Namespace) -> int:
    settings, source = config(".")
    policy = build(settings, args.min_age, (), None, source)
    name = normal(args.name) if args.ecosystem == "pypi" else args.name
    now = datetime.now(timezone.utc)
    found = registries(args.no_cache, policy.maven_repositories)
    if args.version is None:
        return listing(Package(args.ecosystem, name, ""), policy, found, now)
    version = args.version.lstrip("v") if args.ecosystem == "composer" else args.version
    package = Package(args.ecosystem, name, version)
    finding = judge(package, policy, found, now)
    found.cache.save()
    sys.stdout.write(explanation(finding, finding.minimum or policy.min_age))
    if finding.state == "excluded":
        sys.stdout.write(f"excluded by the config: {finding.reason}\n")
    return {"ok": 0, "excluded": 0, "allowed": 0, "unripe": 1}.get(finding.state, 1)


def runconfig(args: argparse.Namespace) -> int:
    settings, source = config(".", args.config)
    policy = build(settings, None, (), None, source)
    out = [f"unripe settings from {display(source)}" if source else "unripe settings: none found, so the defaults"]
    out.append(f"  min-age              {spoken(policy.min_age)}")
    for key, age in policy.min_age_for.items():
        out.append(f"  min-age-for {key:<8} {spoken(age)}")
    out.append(f"  unknown              {policy.unknown}")
    out.append(f"  allow-security-fixes {'true' if policy.security_fixes else 'false'}")
    out.append(f"  workflows            {'true' if policy.workflows else 'false'}")
    if policy.paths:
        out.append(f"  paths                {', '.join(policy.paths)}")
    for pattern in policy.exclude:
        out.append(f"  exclude              {pattern}")
    now = datetime.now(timezone.utc)
    for entry in policy.allow:
        state = " (ended)" if entry.expired(now) else ""
        out.append(f"  allow                {entry.rule}  {entry.describe()}{state}")
    for url in policy.maven_repositories:
        out.append(f"  maven-repositories   {url}")
    out.append(f"  cache                {cachedir()}")

    directory = source.resolve().parent if source and not args.config else Path.cwd()
    found = native.find(directory)
    out.append("")
    if found:
        out.append("cooldown settings of the package managers and bots here:")
        for setting in found:
            if setting.age is None:
                verdict = "not a duration unripe can compare"
            elif setting.age < policy.min_age:
                verdict = f"shorter than unripe's {spoken(policy.min_age)}"
            elif setting.age > policy.min_age:
                verdict = f"longer than unripe's {spoken(policy.min_age)}"
            else:
                verdict = "same as unripe"
            said = spoken(setting.age) if setting.age else setting.raw
            out.append(f"  {setting.tool:<10} {setting.file}: {setting.name} = {setting.raw} ({said}), {verdict}")
    else:
        out.append("no package manager or bot cooldown settings found here")
    missing = native.hints(directory, found, policy.min_age)
    if missing:
        out.append("")
        out.append("these would stop new versions at install time too:")
        for hint in missing:
            out.append(f"  {hint.tool}, in {hint.file}:")
            out.extend(f"      {row}" for row in hint.line.splitlines())
    sys.stdout.write("\n".join(out) + "\n")
    return 0


STARTER = """\
# unripe: fail when a locked dependency version is younger than this.
# https://github.com/TexWoW/unripe#configuration
min-age = "{age}"

# what a version whose publish time can't be found does: fail or warn
unknown = "fail"

# let a version through early when a security advisory names it as the fix
allow-security-fixes = false

# packages to skip entirely, like your own: "npm:@myorg/*"
exclude = []

# other minimum ages for an ecosystem or some packages
# [min-age-for]
# npm = "7d"
# "npm:@types/*" = "1d"

# one version let through early, with a reason and an end date
# [[allow]]
# package = "npm:axios@1.13.2"
# reason = "security fix we need now"
# until = "2026-11-30"
"""

WORKFLOW = """\
name: unripe
on:
  pull_request:
  push:
    branches: [{branch}]

permissions:
  contents: read
  pull-requests: write  # for comment: true

jobs:
  unripe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - uses: TexWoW/unripe@v{version}
        with:
          comment: true
"""


def runinit(args: argparse.Namespace) -> int:
    age = spoken(duration(args.min_age))
    targets = [(Path(".unripe.toml"), STARTER.format(age=age))]
    if args.github:
        branch = "main"
        head = Path(".git/HEAD")
        if head.is_file():
            ref = head.read_text("utf-8", errors="replace").strip()
            if ref.startswith("ref: refs/heads/"):
                branch = ref.removeprefix("ref: refs/heads/")
        targets.append((Path(".github/workflows/unripe.yml"), WORKFLOW.format(branch=branch, version=__version__)))
    existing = [str(path) for path, _ in targets if path.exists()]
    if args.dry_run:
        for path, content in targets:
            replacing = " (replacing the one there)" if path.exists() else ""
            sys.stdout.write(f"would write {path.as_posix()}{replacing}:\n\n{content}\n")
        return 0
    if existing and not args.force:
        raise PolicyError(f"{', '.join(existing)} already exists; use --force to overwrite")
    for path, content in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        write(str(path), content)
        sys.stdout.write(f"wrote {path.as_posix()}\n")
    sys.stdout.write("run `unripe config` to see it, and which package manager settings could go with it\n")
    return 0


def rundoctor(args: argparse.Namespace) -> int:
    """`unripe doctor`: what unripe finds here, and whether each part of a check would work."""
    problems = 0
    out = [f"unripe {__version__} on Python {sys.version.split()[0]}, {sys.platform}"]
    try:
        settings, source = config(".", args.config)
        policy = build(settings, None, (), None, source)
        out.append(f"config     {display(source) if source else 'none, so the defaults'} (min-age {spoken(policy.min_age)})")
    except PolicyError as error:
        problems += 1
        policy = Policy()
        out.append(f"config     broken: {error}")

    folder = cachedir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".doctor.{os.getpid()}"
        probe.write_text("ok", "utf-8")
        probe.unlink()
        out.append(f"cache      {folder}")
    except OSError as error:
        out.append(f"cache      {folder} cannot be written ({error}); every run asks the registries again")
    proxy = next((name for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy") if os.environ.get(name)), None)
    out.append(f"proxy      {proxy + ' is set' if proxy else 'none set'}")
    bundle = next((name for name in ("UNRIPE_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE") if os.environ.get(name)), None)
    out.append(f"ca bundle  {bundle + ' is set' if bundle else 'none set, so the system certificates'}")

    paths = args.paths or ["."]
    from unripe import lockfiles

    found = lockfiles.find(paths, workflows=policy.workflows)
    ecosystems: dict[str, int] = {}
    unreadable = []
    for path in found:
        try:
            for package in lockfiles.read(path.read_text("utf-8", errors="replace"), path):
                ecosystems[package.ecosystem] = ecosystems.get(package.ecosystem, 0) + 1
        except (lockfiles.Unreadable, OSError) as error:
            unreadable.append(str(error))
    out.append(f"lockfiles  {plural(len(found), 'lockfile')} found" + (": " + ", ".join(display(p) for p in found[:8]) if found else ""))
    if len(found) > 8:
        out.append(f"           and {len(found) - 8} more")
    for message in unreadable:
        problems += 1
        out.append(f"           could not read {message}")
    if not found:
        problems += 1
        out.append("           none here, so a check would fail; run unripe doctor where your lockfiles are")
    if ecosystems.keys() & {"actions", "swift"} and not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        out.append("github     no GITHUB_TOKEN, so 60 requests an hour for actions and swift packages")

    lookups = registries(True, policy.maven_repositories)
    for ecosystem in ECOSYSTEMS:
        if ecosystem not in ecosystems:
            continue
        name, version = CANARIES[ecosystem]
        started = time.monotonic()
        try:
            published = lookups.published(Package(ecosystem, name, version))
            said = f"answers ({name} {version} published {stamp(published)}, {(time.monotonic() - started) * 1000:.0f}ms)"
        except (Missing, Unreachable) as error:
            problems += 1
            said = f"did not answer: {error}"
        except Exception as error:  # a registry answering nonsense is a problem to show, not a crash
            problems += 1
            said = f"answered with something unripe could not use ({error})"
        out.append(f"  {ecosystem:<9} {plural(ecosystems[ecosystem], 'version')} locked, registry {said}")

    out.append("")
    out.append("doctor: everything works" if not problems else f"doctor: {plural(problems, 'problem')}")
    sys.stdout.write("\n".join(out) + "\n")
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `unripe`, `unripe .` and `unripe --min-age 7d` all mean `unripe check ...`
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "check")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parser().parse_args(argv)
    try:
        if args.command == "explain":
            return runexplain(args)
        if args.command == "config":
            return runconfig(args)
        if args.command == "init":
            return runinit(args)
        if args.command == "doctor":
            return rundoctor(args)
        if args.command == "mcp":
            from unripe import mcp

            return mcp.serve()
        return runcheck(args)
    except (PolicyError, Problem) as error:
        sys.stderr.write(f"unripe: {error}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("unripe: stopped\n")
        return 130
    except OSError as error:
        sys.stderr.write(f"unripe: {error}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
