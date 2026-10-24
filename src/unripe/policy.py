"""The policy: how old a version must be, what to skip, and what an unknown means."""

from __future__ import annotations

import fnmatch
import os
import re
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from unripe.model import ECOSYSTEMS

UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([mhdw])\s*$", re.IGNORECASE)
DEFAULT_AGE = timedelta(days=3)
CONFIGS = (".unripe.toml", "pyproject.toml")
KEYS = {"min-age", "min-age-for", "exclude", "allow", "allow-security-fixes", "unknown", "paths", "maven-repositories", "workflows", "registries"}
# the registries a [registries] table can point somewhere else: a mirror, or a company registry
MIRRORED = ("npm", "pypi", "conan", "go")


class PolicyError(ValueError):
    """A policy value or config file unripe cannot use."""


def duration(text: str | int | float) -> timedelta:
    """`90m`, `72h`, `3d` or `1w`. A bare number is a number of days."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        days = float(text)
    else:
        raw = str(text).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", raw):
            days = float(raw)
        else:
            match = DURATION.match(raw)
            if not match:
                raise PolicyError(f"{text!r} is not a duration; use something like 90m, 72h, 3d or 1w")
            return timedelta(seconds=float(match.group(1)) * UNITS[match.group(2).lower()])
    return timedelta(days=days)


def spoken(age: timedelta) -> str:
    """A duration the way a person would say it: `3d`, `36h`, `90m`."""
    seconds = int(age.total_seconds())
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


@dataclass(frozen=True)
class Rule:
    """One exclude pattern: a name or glob, optionally scoped to one ecosystem (`npm:@types/*`)."""

    pattern: str
    ecosystem: str | None = None

    @classmethod
    def parse(cls, text: str) -> "Rule":
        text = text.strip()
        prefix, colon, rest = text.partition(":")
        if colon and prefix.lower() in ECOSYSTEMS and rest:
            return cls(rest.strip(), prefix.lower())
        return cls(text)

    def matches(self, ecosystem: str, name: str, version: str) -> bool:
        if self.ecosystem and self.ecosystem != ecosystem:
            return False
        pattern = self.pattern
        if "@" in pattern[1:]:
            # `name@version` pins the rule to one version
            at = pattern.rfind("@")
            if not fnmatch.fnmatchcase(version, pattern[at + 1:]):
                return False
            pattern = pattern[:at]
        candidate = name
        if ecosystem == "pypi":
            candidate, pattern = name.lower(), re.sub(r"[-_.]+", "-", pattern).lower()
        elif ecosystem == "nuget":
            # nuget ids ignore case
            candidate, pattern = name.lower(), pattern.lower()
        return fnmatch.fnmatchcase(candidate, pattern)


    def __str__(self) -> str:
        return f"{self.ecosystem}:{self.pattern}" if self.ecosystem else self.pattern


@dataclass(frozen=True)
class Allow:
    """
    A version let through even though it is too new or its age is unknown,
    say for an urgent security fix, with a reason and usually an end date.
    """

    rule: Rule
    reason: str = ""
    until: date | None = None

    def expired(self, now: datetime) -> bool:
        return self.until is not None and now.astimezone(timezone.utc).date() > self.until

    def describe(self) -> str:
        said = f"allowed until {self.until.isoformat()}" if self.until else "allowed"
        return f"{said}: {self.reason}" if self.reason else said


@dataclass(frozen=True)
class Mirror:
    """
    Where to ask one registry instead of its public address, and the
    environment variables that hold its credentials. The credentials
    themselves never go in the config file.
    """

    url: str
    # a token, sent as a bearer token
    token_env: str = ""
    # "user:password", sent as basic auth
    basic_env: str = ""


@dataclass
class Policy:
    min_age: timedelta = DEFAULT_AGE
    exclude: list[str] = field(default_factory=list)
    unknown: str = "fail"  # or "warn"
    paths: list[str] = field(default_factory=list)
    # extra Maven repositories to look in after the usual ones
    maven_repositories: list[str] = field(default_factory=list)
    # other minimum ages: an ecosystem name (`npm`) or a package pattern (`npm:@types/*`) to an age
    min_age_for: dict[str, timedelta] = field(default_factory=dict)
    allow: list[Allow] = field(default_factory=list)
    # let a too new version through when an advisory names it as the fix
    security_fixes: bool = False
    # also check the actions GitHub workflows use
    workflows: bool = False
    # other addresses for npm, pypi, conan and go
    registries: dict[str, Mirror] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.unknown not in ("fail", "warn"):
            raise PolicyError(f"unknown must be fail or warn, not {self.unknown!r}")
        if self.min_age < timedelta(0) or any(age < timedelta(0) for age in self.min_age_for.values()):
            raise PolicyError("min-age cannot be negative")
        self.rules = [Rule.parse(text) for text in self.exclude if text.strip()]
        # a whole ecosystem's age, and ages for package patterns, which win over it
        self.ecosystem_ages = {key.lower(): age for key, age in self.min_age_for.items() if key.lower() in ECOSYSTEMS}
        self.package_ages = [(Rule.parse(key), age) for key, age in self.min_age_for.items() if key.lower() not in ECOSYSTEMS]

    def excluded(self, ecosystem: str, name: str, version: str) -> str | None:
        """The exclude pattern that covers this package, if any."""
        for rule in self.rules:
            if rule.matches(ecosystem, name, version):
                return str(rule)
        return None

    def age(self, ecosystem: str, name: str, version: str) -> timedelta:
        """How old this package must be: the first package pattern that matches, else its ecosystem's age, else min-age."""
        for rule, age in self.package_ages:
            if rule.matches(ecosystem, name, version):
                return age
        return self.ecosystem_ages.get(ecosystem, self.min_age)

    def varies(self) -> bool:
        """Whether some packages have a minimum age other than min-age."""
        return any(age != self.min_age for age in self.min_age_for.values())

    def allowed(self, ecosystem: str, name: str, version: str, now: datetime) -> tuple[Allow | None, list[Allow]]:
        """The allow entry that lets this package through, if any, and the ones that would have but expired."""
        stale = []
        for entry in self.allow:
            if entry.rule.matches(ecosystem, name, version):
                if not entry.expired(now):
                    return entry, stale
                stale.append(entry)
        return None, stale


def places(directory: Path | str) -> list[Path]:
    """
    Where to look for a config: the directory, then each parent up to the top
    of the git repository it is in. Outside a repository, only the directory.
    """
    start = Path(directory).resolve()
    chain = [start, *start.parents]
    for index, folder in enumerate(chain):
        if (folder / ".git").exists():
            return chain[: index + 1]
    return [start]


def config(directory: Path | str = ".", path: Path | str | None = None) -> tuple[dict, Path | None]:
    """
    The settings from `.unripe.toml` (top level keys) or `[tool.unripe]` in
    pyproject.toml, looked for in `directory` and up to the repository's top,
    or in UNRIPE_CONFIG. An explicit `path` must exist.
    """
    path = path or os.environ.get("UNRIPE_CONFIG") or None
    candidates = [Path(path)] if path else [folder / name for folder in places(directory) for name in CONFIGS]
    for candidate in candidates:
        if not candidate.is_file():
            if path:
                raise PolicyError(f"{candidate}: no such config file")
            continue
        try:
            data = tomllib.loads(candidate.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise PolicyError(f"{candidate}: could not be read ({error})") from error
        if candidate.name == "pyproject.toml":
            data = data.get("tool", {}).get("unripe")
            if data is None:
                if path:
                    raise PolicyError(f"{candidate}: has no [tool.unripe] table")
                continue
        if not isinstance(data, dict):
            raise PolicyError(f"{candidate}: the unripe settings should be a table")
        unexpected = set(data) - KEYS
        if unexpected:
            raise PolicyError(f"{candidate}: unripe does not know {', '.join(sorted(unexpected))} (it knows {', '.join(sorted(KEYS))})")
        return data, candidate
    return {}, None


def build(
    settings: dict,
    min_age=None,
    exclude=(),
    unknown=None,
    source: Path | None = None,
    security_fixes: bool | None = None,
    workflows: bool | None = None,
) -> Policy:
    """A Policy from config settings, with command line values on top. Excludes from both apply."""
    where = f"{source}: " if source else ""

    def strings(key: str) -> list[str]:
        value = settings.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise PolicyError(f"{where}{key} should be a list of strings")
        return value

    def flag(key: str) -> bool:
        value = settings.get(key, False)
        if not isinstance(value, bool):
            raise PolicyError(f"{where}{key} should be true or false")
        return value

    try:
        age = duration(min_age if min_age is not None else settings.get("min-age", "3d"))
    except PolicyError as error:
        raise PolicyError(f"{where if min_age is None else ''}min-age: {error}") from error

    ages = settings.get("min-age-for", {})
    if not isinstance(ages, dict):
        raise PolicyError(f'{where}min-age-for should be a table, like npm = "7d"')
    others = {}
    for key, value in ages.items():
        try:
            others[key] = duration(value)
        except PolicyError as error:
            raise PolicyError(f"{where}min-age-for {key}: {error}") from error

    return Policy(
        min_age=age,
        exclude=strings("exclude") + list(exclude),
        unknown=unknown or str(settings.get("unknown", "fail")),
        paths=strings("paths"),
        maven_repositories=strings("maven-repositories"),
        min_age_for=others,
        allow=allows(settings.get("allow", []), where),
        security_fixes=flag("allow-security-fixes") if security_fixes is None else security_fixes,
        workflows=flag("workflows") if workflows is None else workflows,
        registries=mirrors(settings.get("registries", {}), where),
    )


def mirrors(table, where: str = "") -> dict[str, Mirror]:
    """
    `[registries]`: a registry name to a URL, or to a table with `url` and the
    name of the environment variable holding a token (`token-env`) or a
    user and password (`basic-env`).
    """
    example = 'like npm = "https://npm.example.com" or pypi = { url = "https://pypi.example.com", token-env = "PYPI_TOKEN" }'
    if not isinstance(table, dict):
        raise PolicyError(f"{where}registries should be a table, {example}")
    found = {}
    for name, value in table.items():
        if name not in MIRRORED:
            raise PolicyError(f"{where}registries: unripe can point {', '.join(MIRRORED)} elsewhere, not {name} (Maven has maven-repositories)")
        if isinstance(value, str):
            value = {"url": value}
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            raise PolicyError(f"{where}registries {name} needs a url, {example}")
        unexpected = set(value) - {"url", "token-env", "basic-env"}
        if unexpected:
            raise PolicyError(f"{where}registries {name} has {', '.join(sorted(unexpected))}; it takes url, token-env and basic-env")
        url = value["url"].strip().rstrip("/")
        scheme = urllib.parse.urlsplit(url).scheme
        if scheme not in ("http", "https") or not urllib.parse.urlsplit(url).netloc:
            raise PolicyError(f"{where}registries {name}: {url!r} is not an http or https URL")
        token, basic = value.get("token-env", ""), value.get("basic-env", "")
        if not isinstance(token, str) or not isinstance(basic, str):
            raise PolicyError(f"{where}registries {name}: token-env and basic-env name an environment variable")
        if token and basic:
            raise PolicyError(f"{where}registries {name}: use token-env or basic-env, not both")
        if (token or basic) and scheme != "https":
            raise PolicyError(f"{where}registries {name}: credentials are only sent over https, and {url} is http")
        found[name] = Mirror(url, token.strip(), basic.strip())
    return found


def allows(entries, where: str = "") -> list[Allow]:
    """`[[allow]]` tables: a package pattern, and optionally a reason and the last day it applies."""
    if not isinstance(entries, list):
        raise PolicyError(f'{where}allow should be a list of tables, like [[allow]] package = "npm:axios@1.13.2"')
    found = []
    for entry in entries:
        if isinstance(entry, str):
            entry = {"package": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("package"), str) or not entry["package"].strip():
            raise PolicyError(f'{where}each allow entry needs a package, like package = "npm:axios@1.13.2"')
        unexpected = set(entry) - {"package", "reason", "until"}
        if unexpected:
            raise PolicyError(f"{where}an allow entry has {', '.join(sorted(unexpected))}; it takes package, reason and until")
        until = entry.get("until")
        if isinstance(until, datetime):
            until = until.date()
        elif isinstance(until, str):
            try:
                until = date.fromisoformat(until.strip())
            except ValueError as error:
                raise PolicyError(f"{where}allow {entry['package']}: until should be a date like 2026-11-30") from error
        elif until is not None and not isinstance(until, date):
            raise PolicyError(f"{where}allow {entry['package']}: until should be a date like 2026-11-30")
        found.append(Allow(Rule.parse(entry["package"]), str(entry.get("reason") or "").strip(), until))
    return found
