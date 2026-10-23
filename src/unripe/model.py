"""The few things unripe passes around: a locked package, and what it found out about it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# the registries unripe can ask for a publish time
ECOSYSTEMS = ("npm", "pypi", "cargo", "go", "composer", "rubygems", "nuget", "maven", "jsr", "actions", "pub", "cocoapods", "swift", "terraform", "conan", "cran")
REGISTRY = {
    "npm": "the npm registry",
    "pypi": "PyPI",
    "cargo": "crates.io",
    "go": "the Go module proxy",
    "composer": "Packagist",
    "rubygems": "rubygems.org",
    "nuget": "nuget.org",
    "maven": "the Maven repositories (add more with maven-repositories)",
    "jsr": "jsr.io",
    "actions": "GitHub",
    "pub": "pub.dev",
    "cocoapods": "the CocoaPods trunk",
    "swift": "GitHub",
    "terraform": "the Terraform registry",
    "conan": "ConanCenter",
    "cran": "CRAN",
}
STATES = ("unripe", "unknown", "allowed", "ok", "excluded")


@dataclass(frozen=True, order=True)
class Package:
    """One locked version of one package, and the lockfile it came from."""

    ecosystem: str
    name: str
    version: str
    lockfile: str = field(default="", compare=False)
    # some lockfiles carry the publish time themselves (composer.lock does)
    published: datetime | None = field(default=None, compare=False, hash=False)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.ecosystem, self.name, self.version

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class Finding:
    """
    What unripe concluded about one package.

    `state` is one of:
    - "ok": published long enough ago
    - "unripe": published more recently than the policy allows
    - "unknown": the registry could not say. never quietly treated as ok
    - "allowed": too new or unknown, but let through by an allow entry or as a security fix
    - "excluded": skipped by an exclude rule
    """

    package: Package
    state: str
    published: datetime | None = None
    age_seconds: float | None = None
    reason: str = ""
    # every lockfile that locks this version, with the line it is on when known
    locations: tuple[tuple[str, int | None], ...] = ()
    # the minimum age that applied to this package
    minimum: timedelta | None = None
    # for a version that is too new: the newest version in the same line that is old enough, and when it was published
    instead: tuple[str, datetime] | None = None
    # "commit" when the time is a commit's, which its author sets, rather than a registry's
    basis: str = ""

    @property
    def ripe(self) -> datetime | None:
        """When the version becomes old enough."""
        return self.published + self.minimum if self.published and self.minimum is not None else None

    @property
    def age_days(self) -> float | None:
        return None if self.age_seconds is None else self.age_seconds / 86400
