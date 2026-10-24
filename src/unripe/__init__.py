"""
unripe: fail the build when a locked dependency version is younger than you trust.

Library use:

    import unripe
    for finding in unripe.scan(["."], min_age="3d"):
        if finding.state != "ok":
            print(finding.package, finding.state, finding.reason)
"""

from __future__ import annotations

__version__ = "0.7.0"

__all__ = ["__version__", "scan"]


def scan(
    paths: list[str] | None = None,
    min_age: str = "3d",
    exclude: list[str] | tuple[str, ...] = (),
    since: str | None = None,
    cache: bool = True,
) -> list:
    """
    Check every lockfile under `paths` and return one Finding per locked version.

    `since` is a git ref: only versions added since that ref are checked.
    Raises unripe.check.Problem when a lockfile cannot be read or the ref is bad.
    """
    # imported here so `from unripe import __version__` stays cheap and cycle free
    from unripe.check import Problem, check
    from unripe.policy import Policy, duration
    from unripe.registries import Cache, Registries, cachedir

    policy = Policy(min_age=duration(min_age), exclude=list(exclude))
    registries = Registries(Cache(cachedir() if cache else None), mavenrepos=policy.maven_repositories)
    result = check(paths or ["."], policy, registries, since=since)
    if result.unreadable:
        raise Problem("; ".join(message for _, message in result.unreadable))
    return result.findings
