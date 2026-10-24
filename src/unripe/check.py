"""
The check itself: find lockfiles, read them, ask when each version was
published, and compare that with the policy.
"""

from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from unripe import lockfiles
from unripe.model import REGISTRY, STATES, Finding, Package
from unripe.policy import Policy
from unripe.registries import Missing, Registries, Said, Unreachable

WORKERS = 16
# a suggestion costs a request on some registries; past this many there is a bigger problem than which version to use
SUGGEST = 200
PRERELEASE = re.compile(r"(alpha|beta|rc|pre|dev|canary|next|nightly|snapshot|preview|insiders|experimental|\d[abc]\d)", re.IGNORECASE)


class Problem(Exception):
    """Something that stops the check from running at all: a bad git ref, no git."""


@dataclass
class Result:
    findings: list[Finding]
    policy: Policy
    lockfiles: list[str] = field(default_factory=list)
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    since: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # things worth saying that are not findings, like an allow entry that expired
    notes: list[str] = field(default_factory=list)

    def count(self, state: str) -> int:
        return sum(1 for finding in self.findings if finding.state == state)

    def failing(self, finding: Finding) -> bool:
        return finding.state == "unripe" or (finding.state == "unknown" and self.policy.unknown == "fail")

    @property
    def exitcode(self) -> int:
        """0 clean, 1 unripe (or unknown under the strict default), 2 a lockfile that could not be read."""
        if self.unreadable or not self.lockfiles:
            return 2
        return 1 if any(self.failing(finding) for finding in self.findings) else 0


def display(path: Path) -> str:
    """A path as reports should show it: relative to here when it can be, always with forward slashes."""
    try:
        shown = os.path.relpath(path)
        if shown.startswith(".."):
            shown = str(path)
    except ValueError:  # another drive on windows
        shown = str(path)
    return shown.replace("\\", "/")


def line(rows: list[str], package: Package) -> int | None:
    """
    The line a package is locked on, for annotations, given the lockfile's
    lower cased lines. Most formats name the package and its version on one
    line (`name@1.2.3:`, `name (1.2.3)`); the rest put the version on a line
    just after the name, or for Pipfile.lock, further down.
    """
    names = {package.name.lower()}
    if package.ecosystem == "pypi":
        names |= {package.name.replace("-", "_"), package.name.replace("-", ".")}
    named = re.compile("|".join(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])" for name in names))
    # composer.lock keeps the `v` that unripe drops from versions
    version = re.compile(rf"(?<![\w.-])v?{re.escape(package.version.lower())}(?![\w.-])")
    hits = [number for number, row in enumerate(rows) if named.search(row)]
    # on one line: where the name comes first, so `/react@18.2.0:` beats `/react-dom@18.2.0(react@18.2.0):`
    same = [(named.search(rows[number]).start(), number) for number in hits if version.search(rows[number])]
    if same:
        return min(same)[1] + 1
    for window in (4, 40):
        for number in hits:
            if any(version.search(row) for row in rows[number:number + window]):
                return number + 1
    return hits[0] + 1 if hits else None


class Git:
    """Reads lockfiles as they were at an earlier commit."""

    def __init__(self, ref: str):
        self.ref = ref
        self.checked: set[str] = set()

    def run(self, directory: Path, *args: str) -> subprocess.CompletedProcess:
        try:
            # git never reads stdin here, and on Windows an inherited stdin that isn't a
            # real handle (unripe started by an agent or a service) fails with WinError 6
            return subprocess.run(["git", "-C", str(directory), *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        except FileNotFoundError as error:
            raise Problem("--since needs git, and git was not found") from error
        except subprocess.TimeoutExpired as error:
            raise Problem(f"git took too long in {directory}") from error

    def show(self, path: Path) -> str | None:
        """The file's text at the ref, or None when it did not exist there."""
        directory = path.parent.resolve()
        if str(directory) not in self.checked:
            done = self.run(directory, "rev-parse", "--verify", "--quiet", f"{self.ref}^{{commit}}")
            if done.returncode != 0:
                raise Problem(
                    f"--since {self.ref}: not a commit in {display(directory)}. "
                    "in CI, fetch more history (actions/checkout with fetch-depth: 0)"
                )
            self.checked.add(str(directory))
        done = self.run(directory, "show", f"{self.ref}:./{path.name}")
        if done.returncode != 0:
            return None
        return done.stdout.decode("utf-8-sig", errors="replace")


# how far ahead of now a publish time may be before it is treated as wrong
FUTURE = timedelta(hours=1)


def judge(
    package: Package,
    policy: Policy,
    registries: Registries,
    now: datetime,
    where: tuple[tuple[str, int | None], ...] = (),
) -> Finding:
    """What the policy says about one locked version."""
    rule = policy.excluded(package.ecosystem, package.name, package.version)
    if rule:
        return Finding(package, "excluded", reason=f"matches exclude {rule}", locations=where)
    minimum = policy.age(package.ecosystem, package.name, package.version)
    registry = REGISTRY.get(package.ecosystem, package.ecosystem)
    try:
        published = registries.published(package)
    except Said as error:
        finding = Finding(package, "unknown", reason=str(error), locations=where, minimum=minimum)
    except Missing:
        finding = Finding(package, "unknown", reason=f"not found on {registry}", locations=where, minimum=minimum)
    except Unreachable as error:
        finding = Finding(package, "unknown", reason=f"{registry} did not answer ({error})", locations=where, minimum=minimum)
    except Exception as error:  # a registry answering nonsense is an unknown, never an ok
        finding = Finding(package, "unknown", reason=f"{registry} gave an answer unripe could not use ({error})", locations=where, minimum=minimum)
    else:
        age = (now - published).total_seconds()
        if age < -FUTURE.total_seconds() and not registries.replaying:
            # a clock does not run that far ahead: the date is wrong, so the age is unknown
            reason = f"{registry} dates it {published.strftime('%Y-%m-%d %H:%M UTC')}, which is in the future"
            finding = Finding(package, "unknown", reason=reason, locations=where, minimum=minimum)
        else:
            state = "ok" if age >= minimum.total_seconds() else "unripe"
            finding = Finding(package, state, published, age, locations=where, minimum=minimum, basis=registries.basis(package))
    if finding.state == "ok":
        return finding

    entry, _ = policy.allowed(package.ecosystem, package.name, package.version, now)
    if entry:
        return replace(finding, state="allowed", reason=entry.describe())
    if finding.state == "unripe" and policy.security_fixes:
        try:
            advisories = registries.fixes(package)
        except (Missing, Unreachable):
            advisories = []
        if advisories:
            return replace(finding, state="allowed", reason=f"fixes {', '.join(advisories[:3])}")
    return finding


def numbers(version: str) -> tuple[int, ...]:
    """The leading numbers of a version, for ordering: `v1.12.0-rc.1` is (1, 12, 0)."""
    core = re.split(r"[-+]", version.removeprefix("v"), maxsplit=1)[0]
    return tuple(int(part) for part in re.findall(r"\d+", core)[:4])


def prerelease(ecosystem: str, version: str) -> bool:
    if ecosystem == "rubygems":
        # Gemfile.lock adds a platform after a dash: `1.16.0-x86_64-linux` is a release
        return bool(re.search(r"[a-z]", version.split("-", 1)[0], re.IGNORECASE))
    if ecosystem in ("npm", "cargo", "nuget", "go", "composer") and "-" in version.split("+", 1)[0]:
        return True
    return bool(PRERELEASE.search(version))


def instead(finding: Finding, registries: Registries, now: datetime) -> tuple[str, datetime] | None:
    """
    For a version that is too new: the newest version in the same release line
    (same major, or same minor under 1.0) that is old enough and was not
    yanked or unpublished, preferring ones that are not deprecated. Only from
    registries that list every version.
    """
    package, minimum = finding.package, finding.minimum or timedelta(0)
    try:
        times = registries.versions(package.ecosystem, package.name)
    except (Missing, Unreachable, KeyError, ValueError, TypeError, AttributeError):
        return None
    pulled = registries.pulled.get((package.ecosystem, package.name), set())
    deprecated = registries.deprecated.get((package.ecosystem, package.name), set())
    locked = numbers(package.version)
    if not locked:
        return None
    line = locked[:2] if locked[0] == 0 else locked[:1]
    platform = package.version.partition("-")[2] if package.ecosystem == "rubygems" else ""
    loose = prerelease(package.ecosystem, package.version)
    best = None
    for version, published in times.items():
        if version == package.version or version in pulled or published > now - minimum:
            continue
        if platform != (version.partition("-")[2] if package.ecosystem == "rubygems" else ""):
            continue
        if (not loose and prerelease(package.ecosystem, version)) or numbers(version)[: len(line)] != line:
            continue
        key = (version not in deprecated, numbers(version), published)
        if best is None or key > best[0]:
            best = (key, version, published)
    return (best[1], best[2]) if best else None


@dataclass
class Releases:
    """A package's recent releases, for `unripe explain` without a version and for agents choosing a version."""

    total: int
    minimum: timedelta
    shown: list[tuple[str, datetime]]
    pulled: set[str]
    deprecated: set[str]
    prereleases: int
    # the newest release that is old enough and was not pulled, preferring one that is not deprecated
    newest: tuple[str, datetime] | None


def releases(registries: Registries, ecosystem: str, name: str, minimum: timedelta, now: datetime, count: int = 10) -> Releases:
    """Raises Missing when the package is unknown or the registry cannot list versions, Unreachable when it does not answer."""
    times = registries.versions(ecosystem, name)
    pulled = registries.pulled.get((ecosystem, name), set())
    deprecated = registries.deprecated.get((ecosystem, name), set())
    ordered = sorted(times.items(), key=lambda item: item[1], reverse=True)
    stable = [(version, published) for version, published in ordered if not prerelease(ecosystem, version)]
    ready = [(version, published) for version, published in stable if published <= now - minimum and version not in pulled]
    newest = max(ready, key=lambda item: (item[0] not in deprecated, numbers(item[0]), item[1])) if ready else None
    # prereleases and nightlies drown out releases on some packages; show them only when there is nothing else
    shown = (stable or ordered)[:count]
    return Releases(len(times), minimum, shown, pulled, deprecated, len(ordered) - len(stable) if stable else 0, newest)


def notes(findings: list[Finding], policy: Policy, now: datetime, since: str | None) -> list[str]:
    """What to say about allow entries: expired ones that still matter, and ones nothing needs any more."""
    said = []
    for entry in policy.allow:
        matched = [f for f in findings if entry.rule.matches(f.package.ecosystem, f.package.name, f.package.version)]
        if entry.expired(now):
            if any(f.state in ("unripe", "unknown") for f in matched):
                said.append(f"the allow entry for {entry.rule} ended on {entry.until.isoformat()}, so it no longer applies")
        elif matched and all(f.state in ("ok", "excluded") for f in matched):
            said.append(f"the allow entry for {entry.rule} is no longer needed: everything it covers is old enough now")
        elif not matched and not since:
            said.append(f"the allow entry for {entry.rule} matches nothing in these lockfiles")
    return said


def check(
    paths: list[str],
    policy: Policy,
    registries: Registries,
    since: str | None = None,
    now: datetime | None = None,
    progress: Callable[[int, int], None] | None = None,
    suggest: bool = True,
) -> Result:
    now = now or datetime.now(timezone.utc)
    result = Result(findings=[], policy=policy, since=since, checked_at=now)
    git = Git(since) if since else None

    # every distinct version, with each lockfile it is locked in
    locked: dict[tuple[str, str, str], Package] = {}
    places: dict[tuple[str, str, str], list[str]] = {}
    texts: dict[str, str] = {}
    for path in lockfiles.find(paths, policy.workflows):
        shown = display(path)
        result.lockfiles.append(shown)
        try:
            text = path.read_bytes().decode("utf-8-sig", errors="replace")
            packages = lockfiles.read(text, shown)
            texts[shown] = text
        except OSError as error:
            result.unreadable.append((shown, f"{shown}: could not be opened ({error.strerror or error})"))
            continue
        except lockfiles.Unreadable as error:
            result.unreadable.append((shown, str(error)))
            continue
        if git:
            before = git.show(path)
            old: set[tuple[str, str, str]] = set()
            if before is not None:
                try:
                    old = {package.key for package in lockfiles.read(before, shown)}
                except lockfiles.Unreadable:
                    old = set()  # unreadable then: treat every version as new
            packages = [package for package in packages if package.key not in old]
        for package in packages:
            locked.setdefault(package.key, package)
            if shown not in places.setdefault(package.key, []):
                places[package.key].append(shown)

    findings: list[Finding] = []
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [
                pool.submit(judge, package, policy, registries, now, tuple((shown, None) for shown in places[package.key]))
                for package in locked.values()
            ]
            for done, future in enumerate(as_completed(futures), 1):
                findings.append(future.result())
                if progress:
                    progress(done, len(futures))
            if suggest:
                young = [index for index, finding in enumerate(findings) if finding.state == "unripe"][:SUGGEST]
                answers = pool.map(lambda index: instead(findings[index], registries, now), young)
                for index, answer in zip(young, answers):
                    if answer:
                        findings[index] = replace(findings[index], instead=answer)
    finally:
        registries.cache.save()

    # line numbers only matter for what gets reported, and finding them is slow on big lockfiles
    rows: dict[str, list[str]] = {}
    for index, finding in enumerate(findings):
        if finding.state in ("unripe", "unknown", "allowed"):
            spots = []
            for shown, _ in finding.locations:
                if shown not in rows:
                    rows[shown] = texts[shown].lower().splitlines()
                spots.append((shown, line(rows[shown], finding.package)))
            findings[index] = replace(finding, locations=tuple(spots))

    order = {state: index for index, state in enumerate(STATES)}
    findings.sort(key=lambda f: (order.get(f.state, 9), f.package.ecosystem, f.package.name.lower(), f.package.version))
    result.findings = findings
    result.notes = notes(findings, policy, now, since)
    return result
