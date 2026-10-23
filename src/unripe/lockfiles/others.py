"""Cargo.lock, go.mod, composer.lock, Gemfile.lock, conan.lock and renv.lock."""

from __future__ import annotations

import json
import re
import tomllib
import urllib.parse
from datetime import datetime, timezone

from unripe.model import Package

CRATES = ("registry+https://github.com/rust-lang/crates.io-index", "sparse+https://index.crates.io/")
# name/version, then @user/channel, #recipe-revision and %timestamp, each optional
CONANREF = re.compile(r"^([a-z0-9_][a-z0-9_+.-]*)/([^@#%\s]+)(?:@([^#%\s]+))?(?:#([0-9a-f]+))?(?:%[0-9.]+)?$")
# a JSON string, or R's NA written bare: outside a string there is no legal
# identifier, so a bare NA can only be R's
R_NA = re.compile(r'"(?:[^"\\]|\\.)*"|\bNA\b')


# CRAN itself, and the hosts that serve CRAN's packages at CRAN's versions.
# Posit Package Manager mirrors CRAN under /cran/ and Bioconductor under
# /bioconductor/, so the path decides which of the two it is.
CRAN_HOSTS = ("cran.r-project.org", "cloud.r-project.org", "cran.rstudio.com")
PPM_HOSTS = ("packagemanager.posit.co", "packagemanager.rstudio.com", "p3m.dev")


def cranurl(url) -> bool:
    """Whether a URL points at CRAN, or at a host that serves CRAN unchanged."""
    if not isinstance(url, str) or not url.strip():
        return False
    split = urllib.parse.urlsplit(url.strip())
    if split.hostname in CRAN_HOSTS:
        return True
    # a Posit Package Manager URL is CRAN under /cran/, Bioconductor under /bioconductor/
    return split.hostname in PPM_HOSTS and split.path.startswith("/cran/")


def cranrepos(section) -> dict[str, str]:
    """
    renv's R.Repositories as a name to URL map. renv writes a list of
    {"Name": ..., "URL": ...}; older ones wrote the name and URL as an object.
    """
    if isinstance(section, dict):
        return {str(name): url for name, url in section.items() if isinstance(url, str)}
    found = {}
    for entry in section if isinstance(section, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("Name"), str):
            found[entry["Name"]] = str(entry.get("URL") or "")
    return found


def cransource(entry: dict, repos: dict[str, str]) -> bool:
    """
    Whether an entry was installed from CRAN, however its repository was
    named: renv writes "CRAN", pak writes it as the Source, and a mirror is
    called whatever it was called (RSPM, P3M, PPM) with the URL it points at
    in R.Repositories or in the entry's own RemoteRepos.
    """
    if "CRAN" in (entry.get("Source"), entry.get("Repository")):
        return True
    if cranurl(repos.get(str(entry.get("Repository") or ""))):
        return True
    remotes = entry.get("RemoteRepos")
    if isinstance(remotes, str):
        remotes = [remotes]
    return isinstance(remotes, list) and any(cranurl(remote) for remote in remotes)


def cargo(text: str, lockfile: str) -> list[Package]:
    data = tomllib.loads(text)
    found = []
    for entry in data.get("package") or []:
        # workspace members have no source; git and path crates are not on crates.io
        if entry.get("source") in CRATES and entry.get("name") and entry.get("version"):
            found.append(Package("cargo", entry["name"], str(entry["version"]), lockfile))
    return found


GOLINE = re.compile(r"^\s*([^\s()]+)\s+(v[^\s]+)")


def gomod(text: str, lockfile: str) -> list[Package]:
    """
    go.mod rather than go.sum: since go 1.17 go.mod lists every module in the
    build, while go.sum also keeps old versions that are never built.
    """
    required: dict[str, str] = {}
    # module -> its replacement module and version, or None for a local directory
    replaced: dict[str, tuple[str, str] | None] = {}
    block = None  # "require" or "replace" while inside a `require (` ... `)` block
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if block:
            if line == ")":
                block = None
            else:
                directive(block, line, required, replaced)
            continue
        verb, _, body = line.partition(" ")
        if verb not in ("require", "replace"):
            continue
        if body.strip() == "(":
            block = verb
        else:
            directive(verb, body, required, replaced)
    found = []
    for module, version in required.items():
        if module in replaced:
            target = replaced[module]
            if target is None:
                continue
            module, version = target
        found.append(Package("go", module, version, lockfile))
    return found


def directive(verb: str, body: str, required: dict, replaced: dict) -> None:
    """One `require` or `replace` line, inside a block or not."""
    if verb == "require":
        match = GOLINE.match(body)
        if match:
            required[match.group(1)] = match.group(2)
        return
    left, arrow, right = body.partition("=>")
    if not arrow or not left.split():
        return
    module = left.split()[0]
    target = right.split()
    if len(target) < 2 or target[0].startswith((".", "/")):
        # replaced by a local directory: nothing to look up
        replaced[module] = None
    else:
        replaced[module] = (target[0], target[1])


def composer(text: str, lockfile: str) -> list[Package]:
    """composer.lock records each package's release time itself, so no registry is needed."""
    data = json.loads(text)
    found = []
    for group in ("packages", "packages-dev"):
        for entry in data.get(group) or []:
            name, version = entry.get("name"), str(entry.get("version") or "")
            if not name or not version or version.startswith("dev-"):
                continue
            published = None
            if entry.get("time"):
                try:
                    published = datetime.fromisoformat(str(entry["time"]).replace("Z", "+00:00"))
                except ValueError:
                    published = None
                if published and not published.tzinfo:
                    # old composer versions wrote times without a zone; packagist uses UTC
                    published = published.replace(tzinfo=timezone.utc)
            found.append(Package("composer", name, version.lstrip("v"), lockfile, published))
    return found


def gemfile(text: str, lockfile: str) -> list[Package]:
    """Gems under a `GEM` section whose remote is rubygems.org. Platform suffixes are kept for the lookup."""
    found = []
    section = remote = None
    specs = False
    for line in text.splitlines():
        if line and not line.startswith(" "):
            section, remote, specs = line.strip(), None, False
            continue
        if section != "GEM":
            continue
        stripped = line.strip()
        if line.startswith("  remote:"):
            remote = stripped.split(":", 1)[1].strip()
        elif line.startswith("  specs:"):
            specs = True
        elif specs and line.startswith("    ") and not line.startswith("     "):
            match = re.match(r"^(\S+) \(([^)]+)\)$", stripped)
            if match and remote and "rubygems.org" in remote:
                found.append(Package("rubygems", match.group(1), match.group(2), lockfile))
    return found


def conan(text: str, lockfile: str) -> list[Package]:
    """
    conan.lock, from Conan 2 (lists of references) or Conan 1 (a graph of
    nodes). The version keeps its recipe revision, since a new revision of an
    old version is new code. References with a user and channel are not on
    ConanCenter and are skipped.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a conan lockfile")
    references: list[str] = []
    for section in ("requires", "build_requires", "python_requires"):
        references += [str(entry) for entry in data.get(section) or []]
    for node in ((data.get("graph_lock") or {}).get("nodes") or {}).values():
        if isinstance(node, dict) and node.get("ref"):
            references.append(str(node["ref"]))
    found: dict[tuple[str, str, str], Package] = {}
    for reference in references:
        match = CONANREF.match(reference.strip())
        if not match or match.group(3) not in (None, "_/_"):
            continue
        name, version, revision = match.group(1), match.group(2), match.group(4)
        package = Package("conan", name, f"{version}#{revision}" if revision else version, lockfile)
        found.setdefault(package.key, package)
    return list(found.values())


def rna(text: str) -> str:
    """renv.lock writes R's NA bare, which is not JSON. Strings are left alone."""
    return R_NA.sub(lambda match: match.group(0) if match.group(0).startswith('"') else "null", text)


def renv(text: str, lockfile: str) -> list[Package]:
    """
    renv.lock: R packages installed from CRAN. renv names CRAN as the
    repository for a package from it, whether it writes "CRAN" or
    "Repository" as the source, and a mirror is named for whatever it was
    called, with the URL it points at in R.Repositories. Packages from
    Bioconductor, GitHub, a local path or any other repository are skipped.
    """
    data = json.loads(rna(text))
    if not isinstance(data, dict):
        raise ValueError("not a renv lockfile")
    packages = data.get("Packages")
    if not isinstance(packages, dict):
        raise ValueError("not a renv lockfile")
    section = data.get("R")
    # where each repository name points, so a mirror can be recognised by its URL
    repos = cranrepos(section.get("Repositories") if isinstance(section, dict) else None)
    found = []
    for entry in packages.values():
        if not isinstance(entry, dict):
            continue
        # Bioconductor, GitHub, a local path or another repository has no CRAN
        # publish time to look up, and a private mirror named CRAN is fine to
        # keep: its packages come back unknown, which fails the safe way
        if not cransource(entry, repos):
            continue
        name, version = entry.get("Package"), entry.get("Version")
        if isinstance(name, str) and isinstance(version, str) and name and version:
            found.append(Package("cran", name, version, lockfile))
    return found
