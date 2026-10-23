"""
A fake of every registry unripe asks, answering in each registry's real shape,
so tests run with no network and still cover how URLs are built and answers read.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from unripe import registries
from unripe.registries import Cache, Missing, Registries, Unreachable

TESTDATA = Path(__file__).parent / "testdata"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
# the fake checksum database numbers one record per second from here
EPOCH = datetime(2015, 1, 1, tzinfo=timezone.utc)


def record(moment: datetime) -> int:
    return int((moment - EPOCH).total_seconds())


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeRegistry:
    def __init__(self) -> None:
        self.times: dict[tuple[str, str], dict[str, datetime]] = {}
        self.down: set[str] = set()  # ecosystems that answer 503
        self.urls: list[str] = []
        # ages are measured back from here; the cli tests move it to the real clock
        self.base = NOW
        # when the go checksum database first saw a module version
        self.sumdb: dict[tuple[str, str], datetime] = {}
        # nuget versions that are unlisted, which nuget.org dates 1900
        self.unlisted: set[tuple[str, str]] = set()
        # nuget versions the registration doesn't know yet, with their package file's time
        self.nupkgs: dict[tuple[str, str], datetime] = {}
        # which repository holds a maven artifact, when it isn't maven central
        self.mavenrepo: dict[str, str] = {}
        # versions yanked, unpublished or deprecated, as (ecosystem, name, version)
        self.yanked: set[tuple[str, str, str]] = set()
        # crandb versions whose DESCRIPTION carries no Date/Publication, and
        # how far earlier than it each version's timeline entry sits
        self.nopublication: set[tuple[str, str]] = set()
        self.timelineearly: dict[tuple[str, str], timedelta] = {}
        # github: a repository's tags and the commit each points at, releases by tag, and commit times
        self.tags: dict[str, dict[str, str]] = {}
        self.releases: dict[tuple[str, str], datetime] = {}
        self.commits: dict[tuple[str, str], datetime] = {}
        # osv.dev advisories, in OSV's own shape
        self.advisories: list[dict] = []
        self.posts: list[dict] = []

    def add(self, ecosystem: str, name: str, version: str, age: timedelta | None = None, at: datetime | None = None, seen: timedelta | None = None) -> datetime:
        """A version published `age` ago. For go, `seen` is how long ago the checksum database first saw it (default: then too)."""
        moment = at or self.base - (age if age is not None else timedelta(days=400))
        if ecosystem == "nuget":
            # nuget.org files ids and versions in lower case
            name, version = name.lower(), version.lower()
        self.times.setdefault((ecosystem, name), {})[version] = moment
        if ecosystem == "go":
            self.sumdb[(name, version)] = self.base - seen if seen is not None else moment
        return moment

    def versions(self, ecosystem: str, name: str) -> dict[str, datetime]:
        if ecosystem in self.down:
            raise Unreachable(f"{ecosystem}: HTTP 503")
        found = self.times.get((ecosystem, name))
        if found is None:
            raise Missing(name)
        return found

    def fetchtext(self, url: str, accept: str = "*/*") -> str:
        self.urls.append(url)
        if url.startswith("https://index.crates.io/"):
            return self.crates(url)
        if url.startswith("https://sum.golang.org/"):
            return self.checksums(url)
        return json.dumps(self.answer(url))

    def modified(self, url: str) -> datetime:
        """A HEAD request's Last-Modified, for maven repositories."""
        self.urls.append(url)
        if "maven" in self.down:
            raise Unreachable(f"{url}: HTTP 503")
        flat = re.fullmatch(r"https://api\.nuget\.org/v3-flatcontainer/([^/]+)/([^/]+)/([^/]+)\.nupkg", url)
        if flat:
            name, version = flat.group(1), flat.group(2)
            assert flat.group(3) == f"{name}.{version}"
            if (name, version) not in self.nupkgs:
                raise Missing(url)
            return self.nupkgs[(name, version)]
        for repository in (registries.CENTRAL, registries.GOOGLE, registries.PLUGINS, *set(self.mavenrepo.values())):
            if url.startswith(repository + "/"):
                path = url[len(repository) + 1:].split("/")
                group, artifact, version = ".".join(path[:-3]), path[-3], path[-2]
                assert path[-1] == f"{artifact}-{version}.pom"
                if self.mavenrepo.get(f"{group}:{artifact}", registries.CENTRAL) != repository:
                    raise Missing(url)
                moment = self.versions("maven", f"{group}:{artifact}").get(version)
                if moment is None:
                    raise Missing(url)
                return moment
        raise Missing(url)

    def checksums(self, url: str) -> str:
        if "sumdb" in self.down:
            raise Unreachable("sum.golang.org: HTTP 503")
        path = urllib.parse.urlsplit(url).path
        if path == "/latest":
            return "\n".join(["go.sum database tree", str(record(self.base)), "AAAA=", "", "- sum.golang.org signature", ""])
        unescape = lambda text: re.sub(r"!([a-z])", lambda m: m.group(1).upper(), text)  # noqa: E731
        module, _, version = unescape(path.removeprefix("/lookup/")).rpartition("@")
        if module == "index.example/filler":
            number = int(version.rsplit("-", 1)[1])
        elif (module, version) in self.sumdb:
            number = record(self.sumdb[(module, version)])
        else:
            raise Missing(url)
        return "\n".join([str(number), f"{module} {version} h1:AAAA=", f"{module} {version}/go.mod h1:AAAA=", "", "go.sum database tree", ""])

    def post(self, url: str, payload: dict) -> dict:
        """osv.dev's query endpoint."""
        assert url == "https://api.osv.dev/v1/query"
        self.posts.append(payload)
        wanted = payload["package"]
        hits = [
            vuln
            for vuln in self.advisories
            if any(a["package"]["name"] == wanted["name"] and a["package"]["ecosystem"] == wanted["ecosystem"] for a in vuln.get("affected", []))
        ]
        return {"vulns": hits} if hits else {}

    def answer(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(parsed.path)
        if parsed.netloc == "registry.npmjs.org":
            name = path.lstrip("/")
            times = {"created": "2010-01-01T00:00:00.000Z", "modified": iso(NOW)}
            times.update({version: iso(moment) for version, moment in self.versions("npm", name).items()})
            # unpublished versions stay in the time document and leave the version list
            listed = {version: {"version": version} for version in self.versions("npm", name) if ("npm", name, version) not in self.yanked}
            return {"name": name, "time": times, "versions": listed}
        if parsed.netloc == "jsr.io":
            name = re.fullmatch(r"/(@[^/]+/[^/]+)/meta\.json", path).group(1)
            versions = {}
            for version, moment in self.versions("jsr", name).items():
                # like jsr.io, versions from before mid 2025 have no time here, only in the API
                versions[version] = {"createdAt": moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")} if moment.year >= 2025 else {}
                if ("jsr", name, version) in self.yanked:
                    versions[version]["yanked"] = True
            return {"scope": name[1:].split("/")[0], "name": name.split("/")[1], "versions": versions}
        if parsed.netloc == "pub.dev":
            name = re.fullmatch(r"/api/packages/([^/]+)", path).group(1)
            versions = []
            for version, moment in self.versions("pub", name).items():
                entry = {"version": version, "published": moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
                if ("pub", name, version) in self.yanked:
                    entry["retracted"] = True
                versions.append(entry)
            return {"name": name, "versions": versions}
        if parsed.netloc == "trunk.cocoapods.org":
            name = re.fullmatch(r"/api/v1/pods/([^/]+)", path).group(1)
            versions = [{"name": version, "created_at": moment.strftime("%Y-%m-%d %H:%M:%S UTC")} for version, moment in self.versions("cocoapods", name).items()]
            return {"versions": versions, "owners": []}
        if parsed.netloc == "center2.conan.io":
            name, version, ask = re.fullmatch(r"/v2/conans/([^/]+)/([^/]+)/_/_/(revisions|latest)", path).groups()
            revisions = sorted(
                ({"revision": key.partition("#")[2] or "0" * 32, "time": moment.strftime("%Y-%m-%dT%H:%M:%S.000+0000")} for key, moment in self.versions("conan", name).items() if key.partition("#")[0] == version),
                key=lambda entry: entry["time"],
                reverse=True,
            )
            if not revisions:
                raise Missing(url)
            return revisions[0] if ask == "latest" else {"reference": f"{name}/{version}@_/_", "revisions": revisions}
        if parsed.netloc == "registry.terraform.io":
            name = re.fullmatch(r"/v2/providers/([^/]+/[^/]+)", path).group(1)
            assert urllib.parse.parse_qs(parsed.query) == {"include": ["provider-versions"]}
            included = [
                {"type": "provider-versions", "id": str(number), "attributes": {"version": version, "published-at": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}}
                for number, (version, moment) in enumerate(self.versions("terraform", name).items())
            ]
            return {"data": {"type": "providers", "attributes": {"full-name": name}}, "included": included}
        if parsed.netloc == "api.jsr.io":
            scope, name, version = re.fullmatch(r"/scopes/([^/]+)/packages/([^/]+)/versions/([^/]+)", path).groups()
            moment = self.versions("jsr", f"@{scope}/{name}").get(version)
            if moment is None:
                raise Missing(url)
            return {"scope": scope, "package": name, "version": version, "createdAt": moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        if parsed.netloc == "api.github.com":
            match = re.fullmatch(r"/repos/([^/]+/[^/]+)/(tags|releases/tags/(.+)|commits/(.+))", path)
            repository = match.group(1)
            if match.group(2) == "tags":
                if repository not in self.tags:
                    raise Missing(url)
                page = int(urllib.parse.parse_qs(parsed.query).get("page", ["1"])[0])
                listed = [{"name": tag, "commit": {"sha": sha}} for tag, sha in self.tags[repository].items()]
                return listed[(page - 1) * 100 : page * 100]
            if match.group(3):
                moment = self.releases.get((repository, match.group(3)))
                if moment is None:
                    raise Missing(url)
                return {"tag_name": match.group(3), "published_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}
            moment = self.commits.get((repository, match.group(4)))
            if moment is None:
                raise Missing(url)
            return {"sha": match.group(4), "commit": {"committer": {"date": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}}}
        if parsed.netloc == "pypi.org" and re.fullmatch(r"/pypi/[^/]+/json", path):
            name = path.split("/")[2]
            releases = {}
            for version, moment in self.versions("pypi", name).items():
                yanked = ("pypi", name, version) in self.yanked
                releases[version] = [{"filename": "x.tar.gz", "upload_time_iso_8601": iso(moment), "yanked": yanked}]
            return {"info": {"name": name}, "releases": releases}
        if parsed.netloc == "pypi.org":
            match = re.fullmatch(r"/pypi/([^/]+)/([^/]+)/json", path)
            moment = self.versions("pypi", match.group(1)).get(match.group(2))
            if moment is None:
                raise Missing(url)
            # a wheel uploaded an hour after the sdist; the sdist is the publish time
            return {
                "info": {"name": match.group(1), "version": match.group(2)},
                "urls": [
                    {"filename": "x.whl", "upload_time_iso_8601": iso(moment + timedelta(hours=1))},
                    {"filename": "x.tar.gz", "upload_time_iso_8601": iso(moment)},
                ],
            }
        if parsed.netloc == "proxy.golang.org" or parsed.netloc == "goproxy.example.com":
            match = re.fullmatch(r"/(.+)/@v/(.+)\.info", path)
            unescape = lambda text: re.sub(r"!([a-z])", lambda m: m.group(1).upper(), text)  # noqa: E731
            moment = self.versions("go", unescape(match.group(1))).get(unescape(match.group(2)))
            if moment is None:
                raise Missing(url)
            return {"Version": unescape(match.group(2)), "Time": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if parsed.netloc == "api.nuget.org" and path.endswith("/index.json"):
            name = path.split("/")[-2]
            leaves = []
            for version, moment in self.versions("nuget", name).items():
                hidden = (name, version) in self.unlisted
                published = "1900-01-01T00:00:00+00:00" if hidden else moment.isoformat()
                leaves.append({"catalogEntry": {"id": name, "version": version, "published": published, "listed": not hidden}})
            return {"count": 1, "items": [{"count": len(leaves), "items": leaves}]}
        if parsed.netloc == "api.nuget.org":
            leaf = re.fullmatch(r"/v3/registration5-gz-semver2/([^/]+)/([^/]+)\.json", path)
            if leaf:
                name, version = leaf.groups()
                assert name == name.lower() and version == version.lower(), "nuget.org paths are lower case"
                moment = self.versions("nuget", name).get(version)
                if moment is None:
                    raise Missing(url)
                hidden = (name, version) in self.unlisted
                return {
                    "@id": url,
                    "catalogEntry": f"https://api.nuget.org/v3/catalog0/data/2026.01.01.00.00.00/{name}.{version}.json",
                    "listed": not hidden,
                    "published": "1900-01-01T00:00:00+00:00" if hidden else moment.isoformat(),
                }
            catalog = re.fullmatch(r"/v3/catalog0/data/[^/]+/(.+)\.json", path)
            for (ecosystem, name), versions in self.times.items():
                for version, moment in versions.items():
                    if ecosystem == "nuget" and catalog and catalog.group(1) == f"{name}.{version}":
                        return {"id": name, "version": version, "created": iso(moment), "published": "1900-01-01T00:00:00Z", "listed": False}
            raise Missing(url)
        if parsed.netloc == "index.golang.org":
            if "sumdb" in self.down:
                raise Unreachable("index: HTTP 429")
            since = datetime.fromisoformat(urllib.parse.parse_qs(parsed.query)["since"][0].replace("Z", "+00:00"))
            return {"Path": "index.example/filler", "Version": f"v0.0.0-{record(since)}", "Timestamp": iso(since)}
        if parsed.netloc == "repo.packagist.org":
            name = re.fullmatch(r"/p2/(.+)\.json", path).group(1)
            entries = []
            for number, (version, moment) in enumerate(sorted(self.versions("composer", name).items(), reverse=True)):
                # minified like the real p2 files: the first entry is full, later ones list changes
                entry = {"version": f"v{version}", "version_normalized": f"{version}.0", "time": moment.isoformat()}
                if number == 0:
                    entry.update({"name": name, "type": "library", "license": ["MIT"]})
                entries.append(entry)
            return {"minified": "composer/2.0", "packages": {name: entries}}
        if parsed.netloc == "rubygems.org":
            name = re.fullmatch(r"/api/v1/versions/(.+)\.json", path).group(1)
            out = []
            for version, moment in self.versions("rubygems", name).items():
                number, _, platform = version.partition("-")
                out.append({"number": number, "platform": platform or "ruby", "created_at": iso(moment)})
            return out
        if parsed.netloc == "crandb.r-pkg.org":
            name = re.fullmatch(r"/([^/]+)/all", path).group(1)
            times = self.versions("cran", name)
            # each version's own DESCRIPTION, which is what a lookup prefers,
            # and a timeline that sits an hour earlier than it does
            return {
                "name": name,
                "versions": {
                    version: {"Date/Publication": moment.strftime("%Y-%m-%d %H:%M:%S") + " UTC"}
                    for version, moment in times.items()
                    if (name, version) not in self.nopublication
                },
                "timeline": {
                    version: (moment - self.timelineearly.get((name, version), timedelta(hours=1))).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                    for version, moment in times.items()
                },
            }
        raise AssertionError(f"the fake registry was asked an unexpected url: {url}")

    def crates(self, url: str) -> str:
        name = url.rsplit("/", 1)[1]
        assert url == f"https://index.crates.io/{registries.crateindex(name)}"
        lines = []
        for version, moment in self.versions("cargo", name).items():
            yanked = ("cargo", name, version) in self.yanked
            lines.append(json.dumps({"name": name, "vers": version, "deps": [], "cksum": "00", "features": {}, "yanked": yanked, "pubtime": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}))
        return "\n".join(lines) + "\n"


@pytest.fixture
def fake(monkeypatch) -> FakeRegistry:
    registry = FakeRegistry()
    monkeypatch.setattr(registries, "fetchtext", registry.fetchtext)
    monkeypatch.setattr(registries, "fetchmodified", registry.modified)
    monkeypatch.setattr(registries, "post", registry.post)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("UNRIPE_CONFIG", raising=False)
    monkeypatch.delenv("UNRIPE_MAVEN_REPOS", raising=False)
    for name in ("GOPROXY", "GOSUMDB", "GONOSUMDB", "GOPRIVATE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("UNRIPE_NPM_REGISTRY", raising=False)
    monkeypatch.delenv("UNRIPE_PYPI_URL", raising=False)
    return registry


@pytest.fixture
def lookups(fake) -> Registries:
    """Registries backed by the fake, with an in-memory cache, on the fake's clock."""
    return Registries(Cache(None), now=NOW)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path_factory, monkeypatch):
    """Never touch the real cache directory from a test."""
    monkeypatch.setenv("UNRIPE_CACHE", str(tmp_path_factory.mktemp("cache")))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Unit tests never reach a real registry; a test that wants urlopen patches it itself."""

    def refuse(request, timeout=None):
        raise AssertionError(f"a test tried to reach the network: {getattr(request, 'full_url', request)}")

    monkeypatch.setattr(registries.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(registries, "send", refuse)
