"""
When was a version published? Asked of each ecosystem's own public registry.

A publish time never changes once a version exists, so every answer is cached
on disk for good, and a second run on the same lockfile asks nothing.

A registry that cannot answer (down, rate limited, private package) is an
"unknown", never an "ok": failing open is exactly what an attacker wants.
"""

from __future__ import annotations

import email.utils
import fnmatch
import gzip
import http.client
import io
import json
import os
import random
import re
import ssl
import threading
import time
import zlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from unripe import __version__
from unripe.model import Package

AGENT = f"unripe/{__version__} (+https://github.com/TexWoW/unripe)"
TIMEOUT = 20
TRIES = 4
# no registry answer unripe reads comes near this; a bigger one is refused rather than held in memory
LIMIT = 256 * 1024 * 1024
# what a finding dated by a commit time, rather than by a registry, says about it
COMMIT = "commit"
# how many requests this process has made, for --verbose
REQUESTS = [0]
COUNTING = threading.Lock()


class Missing(Exception):
    """The registry answered, and the version is not there."""


class Said(Missing):
    """Missing for a reason worth reporting as it is: an action used at a branch, an unpublished version."""


class Unreachable(Exception):
    """The registry could not be asked: network, rate limit or server error."""


def when(text: str) -> datetime:
    """An ISO timestamp as an aware datetime, whatever form the registry wrote it in."""
    stamp = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def tls() -> ssl.SSLContext | None:
    """
    A context for a corporate CA bundle, when one is set the way other tools
    set it. SSL_CERT_FILE needs nothing: Python reads it already.
    """
    bundle = os.environ.get("UNRIPE_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE")
    return ssl.create_default_context(cafile=bundle) if bundle else None


# each thread keeps one open connection per host: a new TLS handshake for
# every lookup made a cold run several times slower than it needs to be
POOL = threading.local()
DEFAULT: list[ssl.SSLContext] = []
REDIRECTS = (301, 302, 303, 307, 308)
# what a kept connection the server already closed looks like
STALE = (http.client.RemoteDisconnected, http.client.CannotSendRequest, http.client.ResponseNotReady, ConnectionResetError, BrokenPipeError)


def proxied(parts: urllib.parse.SplitResult) -> bool:
    """Whether a proxy is set for this url, in which case urllib does the request as it always has."""
    proxy = urllib.request.getproxies().get(parts.scheme)
    return bool(proxy) and not urllib.request.proxy_bypass(parts.hostname or "")


def connection(parts: urllib.parse.SplitResult, timeout: float, fresh: bool) -> http.client.HTTPConnection:
    connections = POOL.__dict__.setdefault("connections", {})
    key = (parts.scheme, parts.netloc)
    if fresh and key in connections:
        connections.pop(key).close()
    if key not in connections:
        if parts.scheme == "https":
            context = tls()
            if context is None:
                if not DEFAULT:
                    DEFAULT.append(ssl.create_default_context())
                context = DEFAULT[0]
            connections[key] = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=timeout, context=context)
        else:
            connections[key] = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    return connections[key]


def exchange(parts: urllib.parse.SplitResult, method: str, headers: dict[str, str], data: bytes | None, timeout: float) -> http.client.HTTPResponse:
    """One request on this thread's connection to the host, opening a new one if the kept one was closed."""
    target = parts.path or "/"
    if parts.query:
        target += f"?{parts.query}"
    for fresh in (False, True):
        kept = not fresh and (parts.scheme, parts.netloc) in POOL.__dict__.get("connections", {})
        conn = connection(parts, timeout, fresh)
        try:
            conn.request(method, target, body=data, headers=headers)
            return conn.getresponse()
        except STALE:
            POOL.connections.pop((parts.scheme, parts.netloc), None)
            conn.close()
            if not kept:
                raise
        except Exception:
            POOL.connections.pop((parts.scheme, parts.netloc), None)
            conn.close()
            raise
    raise http.client.CannotSendRequest(parts.netloc)


def send(request: urllib.request.Request, timeout: float):
    """
    Open a request like urlopen does, raising HTTPError for an error status,
    but on a kept connection, following redirects itself. Through a proxy it
    is urlopen.
    """
    url, method, data = request.full_url, request.get_method(), request.data
    headers = dict(request.header_items())
    for _ in range(6):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or proxied(parts):
            context = tls()
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            return urllib.request.urlopen(request, timeout=timeout, context=context) if context else urllib.request.urlopen(request, timeout=timeout)
        response = exchange(parts, method, headers, data, timeout)
        location = response.getheader("Location")
        if response.status in REDIRECTS and location:
            response.read()
            moved = urllib.parse.urljoin(url, location)
            if urllib.parse.urlsplit(moved).netloc != parts.netloc:
                # a token for one host never follows a redirect to another
                headers.pop("Authorization", None)
            if response.status == 303 or (response.status in (301, 302) and method == "POST"):
                method, data = "GET", None
                headers.pop("Content-type", None)
            url = moved
            continue
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, io.BytesIO(response.read()))
        return response
    raise urllib.error.URLError(f"too many redirects from {request.full_url}")


def ask(url: str, accept: str = "*/*", method: str = "GET", body: bytes | None = None) -> tuple[dict[str, str], bytes]:
    """
    One request, retrying the failures worth retrying, as the response headers
    (lower cased names) and body, gzip undone. A 404 is an answer, not a failure.
    """
    delay = 1.0
    last = ""
    for attempt in range(TRIES):
        headers = {"User-Agent": AGENT, "Accept": accept, "Accept-Encoding": "gzip"}
        # a token only ever goes to GitHub's own API, and only when one is set
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token and url.startswith("https://api.github.com/"):
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with COUNTING:
            REQUESTS[0] += 1
        try:
            with send(request, TIMEOUT) as response:
                found = {name.lower(): value for name, value in response.headers.items()}
                body = response.read(LIMIT + 1)
                if found.get("content-encoding") == "gzip" and body:
                    unzip = zlib.decompressobj(wbits=31)
                    body = unzip.decompress(body, LIMIT + 1)
                    if unzip.unconsumed_tail:
                        body += b"x"
                if len(body) > LIMIT:
                    raise Unreachable(f"{url}: answered with more than {LIMIT // 1024 // 1024} MB")
                return found, body
        except urllib.error.HTTPError as error:
            if error.code in (404, 410):
                raise Missing(url) from error
            last = f"HTTP {error.code}"
            if error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0":
                last = "HTTP 403, rate limited (set GITHUB_TOKEN for a higher limit)"
                break
            if error.code not in (429, 500, 502, 503, 504):
                break
            retry = error.headers.get("Retry-After")
            if retry and retry.isdigit():
                delay = max(delay, min(float(retry), 60.0))
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, EOFError, gzip.BadGzipFile, zlib.error, ssl.SSLError) as error:
            reason = getattr(error, "reason", error)
            last = str(reason)
            if isinstance(reason, ssl.SSLCertVerificationError):
                # retrying does not help; a proxy that inspects TLS needs its CA bundle
                last += " (behind a proxy that inspects TLS? set UNRIPE_CA_BUNDLE or SSL_CERT_FILE to its CA bundle)"
                break
        if attempt < TRIES - 1:
            time.sleep(delay + random.random() * 0.5)
            delay *= 2
    raise Unreachable(f"{url}: {last}")


def fetchtext(url: str, accept: str = "*/*") -> str:
    """GET a body as text."""
    return ask(url, accept)[1].decode("utf-8-sig", errors="replace")


def fetchmodified(url: str) -> datetime:
    """When a file was last changed, from a HEAD request's Last-Modified header."""
    headers, _ = ask(url, method="HEAD")
    try:
        stamp = email.utils.parsedate_to_datetime(headers["last-modified"])
    except (KeyError, TypeError, ValueError) as error:
        raise Unreachable(f"{url}: no usable Last-Modified header") from error
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def fetch(url: str) -> dict | list:
    """GET some JSON. A body that is not JSON means the registry is not answering properly."""
    text = fetchtext(url, "application/json")
    try:
        return json.loads(text)
    except ValueError as error:
        raise Unreachable(f"{url}: answered with something that is not JSON") from error


def post(url: str, payload: dict) -> dict:
    """POST some JSON and read JSON back."""
    _, body = ask(url, "application/json", "POST", json.dumps(payload).encode("utf-8"))
    try:
        answer = json.loads(body.decode("utf-8-sig", errors="replace"))
    except ValueError as error:
        raise Unreachable(f"{url}: answered with something that is not JSON") from error
    if not isinstance(answer, dict):
        raise Unreachable(f"{url}: answered with something unripe does not know")
    return answer


# what OSV calls each ecosystem
OSV = {
    "npm": "npm",
    "pypi": "PyPI",
    "cargo": "crates.io",
    "go": "Go",
    "composer": "Packagist",
    "rubygems": "RubyGems",
    "nuget": "NuGet",
    "maven": "Maven",
    "pub": "Pub",
}


class Cache:
    """Publish times on disk, one JSON file per ecosystem. Safe to delete at any time."""

    def __init__(self, directory: Path | None):
        self.directory = directory
        self.lock = threading.Lock()
        self.data: dict[str, dict[str, str]] = {}
        self.dirty: set[str] = set()
        # versions learned in passing (an npm document lists every version);
        # kept for this run only, so the files on disk hold what was checked
        self.passing: dict[tuple[str, str, str], datetime] = {}

    def table(self, ecosystem: str) -> dict[str, str]:
        if ecosystem not in self.data:
            self.data[ecosystem] = {}
            if self.directory:
                try:
                    loaded = json.loads((self.directory / f"{ecosystem}.json").read_text("utf-8"))
                    if isinstance(loaded, dict):
                        self.data[ecosystem] = loaded
                except (OSError, ValueError):
                    pass
        return self.data[ecosystem]

    def get(self, package: Package) -> datetime | None:
        with self.lock:
            value = self.table(package.ecosystem).get(f"{package.name}@{package.version}")
            if value is None:
                return self.passing.get(package.key)
        try:
            return when(value) if value else None
        except (TypeError, ValueError):
            return None

    def put(self, ecosystem: str, name: str, version: str, published: datetime) -> None:
        with self.lock:
            table, value = self.table(ecosystem), published.isoformat()
            if table.get(f"{name}@{version}") != value:
                table[f"{name}@{version}"] = value
                self.dirty.add(ecosystem)

    def recall(self, table: str, key: str) -> str | None:
        with self.lock:
            return self.table(table).get(key)

    def note(self, table: str, key: str, value: str) -> None:
        with self.lock:
            if self.table(table).get(key) != value:
                self.table(table)[key] = value
                self.dirty.add(table)

    def forget(self, table: str, key: str) -> None:
        with self.lock:
            if self.table(table).pop(key, None) is not None:
                self.dirty.add(table)

    def learn(self, ecosystem: str, name: str, version: str, published: datetime) -> None:
        with self.lock:
            self.passing[(ecosystem, name, version)] = published

    def save(self) -> None:
        if not self.directory:
            return
        with self.lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                for ecosystem in self.dirty:
                    target = self.directory / f"{ecosystem}.json"
                    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
                    temporary.write_text(json.dumps(self.data[ecosystem], sort_keys=True), "utf-8")
                    os.replace(temporary, target)
                self.dirty.clear()
            except OSError:
                pass


def cachedir() -> Path:
    if os.environ.get("UNRIPE_CACHE"):
        return Path(os.environ["UNRIPE_CACHE"])
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "unripe"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "unripe" / "cache"
    return Path.home() / ".cache" / "unripe"


def goescape(module: str) -> str:
    """The Go module proxy spells capital letters as `!` plus the lower case letter."""
    return "".join(f"!{c.lower()}" if c.isupper() else c for c in module)


def goproxy() -> str:
    """The first real proxy in GOPROXY, or the public one."""
    for entry in re.split(r"[,|]", os.environ.get("GOPROXY") or ""):
        entry = entry.strip()
        if entry and entry not in ("direct", "off"):
            return entry.rstrip("/")
    return "https://proxy.golang.org"


def gosumdb(module: str) -> bool:
    """Whether the public Go checksum database should be asked about a module, as the go command would."""
    setting = (os.environ.get("GOSUMDB") or "sum.golang.org").strip()
    if setting == "off" or not setting.startswith("sum.golang.org"):
        return False
    private = os.environ.get("GONOSUMDB") or os.environ.get("GOPRIVATE") or ""
    for pattern in filter(None, (part.strip() for part in private.split(","))):
        # like the go command: a pattern matches a path prefix with as many elements as it has
        prefix = "/".join(module.split("/")[: pattern.count("/") + 1])
        if fnmatch.fnmatchcase(prefix, pattern):
            return False
    return True


class SumDB:
    """
    When a Go module version first appeared, from the public checksum database.

    sum.golang.org gives every module version a record number, in the order it
    first saw them, and nobody can change that order afterwards. It keeps no
    times, but index.golang.org lists versions with the time the module proxy
    first fetched them. Looking a few of those up gives record numbers at known
    times, and a version's record number can be placed between them.

    The record number at a past moment never changes, so calibration points are
    cached like publish times. They sit on whole hours (whole days further back)
    so that runs close together share them.
    """

    # hours back that calibration points sit at, newest first. dense in the
    # first month, where policies live, because the database grows unevenly
    LADDER = [1, 3, 6, 12, 24, 36, 48, 60, 72, 84, 96, 108, 120, 144, 168, 192, 216, 240, 264, 288, 336,
              384, 432, 504, 600, 720, 1080, 1440, 2160, 2880, 4320, 6480, 8760]

    def __init__(self, now: datetime, cache: Cache | None = None, url: str = "https://sum.golang.org", index: str = "https://index.golang.org/index"):
        self.now = now.astimezone(timezone.utc)
        self.cache = cache or Cache(None)
        self.url = url
        self.index = index
        self.lock = threading.Lock()
        hour = self.now.replace(minute=0, second=0, microsecond=0)
        day = hour.replace(hour=0)
        # on whole hours, and whole days further back, so runs close together share points
        self.moments = [self.now] + [(hour if hours < 48 else day) - timedelta(hours=hours) for hours in self.LADDER]
        self.numbers: dict[int, int] = {}

    def record(self, module: str, version: str) -> int:
        body = fetchtext(f"{self.url}/lookup/{goescape(module)}@{goescape(version)}")
        try:
            return int(body.splitlines()[0])
        except (IndexError, ValueError) as error:
            raise Unreachable(f"{self.url} answered a lookup in a form unripe does not know") from error

    def latest(self) -> int:
        lines = fetchtext(f"{self.url}/latest").splitlines()
        try:
            return int(lines[1])
        except (IndexError, ValueError) as error:
            raise Unreachable(f"{self.url} answered /latest in a form unripe does not know") from error

    def seen(self, moment: datetime) -> int:
        """The record number of the first version the proxy fetched at or after a moment."""
        since = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        remembered = self.cache.recall("gosumdb", since)
        if remembered is not None:
            return int(remembered)
        for line in fetchtext(f"{self.index}?since={since}&limit=20").splitlines():
            try:
                entry = json.loads(line)
                number = self.record(entry["Path"], entry["Version"])
            except (ValueError, KeyError, TypeError, Missing):
                # a version the checksum database refused: try the next one
                continue
            self.cache.note("gosumdb", since, str(number))
            return number
        raise Unreachable(f"{self.index} gave nothing to calibrate against at {since}")

    def point(self, rung: int) -> tuple[datetime, int]:
        """The time and record number at one rung of the ladder; rung 0 is the database as it is now."""
        with self.lock:
            if rung not in self.numbers:
                self.numbers[rung] = self.latest() if rung == 0 else self.seen(self.moments[rung])
            return self.moments[rung], self.numbers[rung]

    def first(self, record: int) -> tuple[datetime, datetime, datetime] | None:
        """
        When the version with this record number first appeared: the earliest
        and latest it can have been, and a best guess. None if over a year ago.
        Inside a narrow gap between two points the guess is interpolated; inside
        a wide one it is the latest, the youngest the version could be.
        """
        # the newest rung the record is not older than, by binary search, since numbers fall as rungs go back
        low, high = 1, len(self.moments) - 1
        if record < self.point(high)[1]:
            return None
        while low < high:
            middle = (low + high) // 2
            if record >= self.point(middle)[1]:
                high = middle
            else:
                low = middle + 1
        older, newer = self.point(low), self.point(low - 1)
        width = newer[0] - older[0]
        if width > max(timedelta(days=1), (self.now - older[0]) * 0.1):
            return older[0], newer[0], newer[0]
        span = max(newer[1] - older[1], 1)
        fraction = min(max((record - older[1]) / span, 0.0), 1.0)
        return older[0], newer[0], older[0] + width * fraction


SHA = re.compile(r"^[0-9a-f]{40}$")


def specific(tag: str) -> tuple[int, int]:
    """How exact a tag is, so `v4.2.0` is preferred over `v4` when both point at one commit."""
    return tag.count("."), len(tag)


CENTRAL = "https://repo1.maven.org/maven2"
GOOGLE = "https://dl.google.com/dl/android/maven2"
PLUGINS = "https://plugins.gradle.org/m2"
ANDROID = ("androidx.", "com.android.", "com.google.android.", "com.google.firebase", "com.google.gms", "com.google.mlkit", "com.google.ar.")


def mavenrepos(group: str, artifact: str, extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """
    The Maven repositories to try for an artifact, most likely first, then any
    extra ones from the config file or UNRIPE_MAVEN_REPOS (comma separated).
    """
    if artifact.endswith(".gradle.plugin"):
        usual = [PLUGINS, CENTRAL, GOOGLE]
    elif group.startswith(ANDROID) or group in ("com.google.firebase", "com.google.gms"):
        usual = [GOOGLE, CENTRAL, PLUGINS]
    else:
        usual = [CENTRAL, GOOGLE, PLUGINS]
    more = list(extra) + (os.environ.get("UNRIPE_MAVEN_REPOS") or "").split(",")
    for url in (url.strip().rstrip("/") for url in more):
        if url and url not in usual:
            usual.append(url)
    return usual


def crateindex(name: str) -> str:
    """Where a crate lives in the sparse index: `se/rd/serde`, `3/s/syn`, `2/cc`, `1/a`."""
    name = name.lower()
    if len(name) <= 2:
        return f"{len(name)}/{name}"
    if len(name) == 3:
        return f"3/{name[0]}/{name}"
    return f"{name[:2]}/{name[2:4]}/{name}"


class Registries:
    """Looks publish times up, through the cache. Thread-safe: unripe asks many at once."""

    def __init__(
        self,
        cache: Cache,
        npmurl: str | None = None,
        pypiurl: str | None = None,
        now: datetime | None = None,
        mavenrepos: list[str] | tuple[str, ...] = (),
        offline: bool = False,
    ):
        self.cache = cache
        # answer only from the cache: anything not in it is unknown
        self.offline = offline
        self.mavenextra = list(mavenrepos)
        self.sumdb = SumDB(now or datetime.now(timezone.utc), cache)
        # checking as of a past moment (--as-of): what was taken down since then was still up then
        self.replaying = now is not None and now < datetime.now(timezone.utc) - timedelta(hours=1)
        self.npmurl = (npmurl or os.environ.get("UNRIPE_NPM_REGISTRY") or "https://registry.npmjs.org").rstrip("/")
        self.pypiurl = (pypiurl or os.environ.get("UNRIPE_PYPI_URL") or "https://pypi.org").rstrip("/")
        self.conanurl = (os.environ.get("UNRIPE_CONAN_REMOTE") or "https://center2.conan.io").rstrip("/")
        self.lookups: dict[str, Callable[[Package], datetime]] = {
            "npm": self.npm,
            "pypi": self.pypi,
            "cargo": self.cargo,
            "go": self.go,
            "composer": self.composer,
            "rubygems": self.rubygems,
            "nuget": self.nuget,
            "maven": self.maven,
            "jsr": self.jsr,
            "actions": self.actions,
            "pub": self.pub,
            "cocoapods": self.cocoapods,
            "swift": self.swift,
            "terraform": self.terraform,
            "conan": self.conan,
            "cran": self.cran,
        }
        # registries whose one answer covers every version are asked once per package
        self.pending: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()
        # every version of a package and its publish time, for the ones fetched whole
        self.documents: dict[tuple[str, str], dict[str, datetime]] = {}
        # versions that were yanked or unlisted, never suggested
        self.pulled: dict[tuple[str, str], set[str]] = {}
        # versions the maintainers mark deprecated (or abandoned): still installable, suggested only when nothing else is
        self.deprecated: dict[tuple[str, str], set[str]] = {}
        # npm versions that were unpublished: the registry keeps their time but not the version
        self.gone: set[tuple[str, str]] = set()
        # jsr versions listed without a time
        self.undated: set[tuple[str, str]] = set()
        self.listings: dict[str, Callable[[str], dict[str, datetime]]] = {
            "npm": self.npmall,
            "pypi": self.pypiall,
            "cargo": self.cargoall,
            "composer": self.composerall,
            "rubygems": self.rubygemsall,
            "nuget": self.nugetall,
            "jsr": self.jsrall,
            "pub": self.puball,
            "cocoapods": self.cocoapodsall,
            "terraform": self.terraformall,
            "cran": self.cranall,
        }

    def published(self, package: Package) -> datetime:
        if package.published:
            return package.published
        if package.ecosystem == "actions" and not SHA.match(package.version):
            # a tag like v4 moves to newer commits, so what it points at is looked up every run
            if self.offline:
                raise Unreachable("not in the cache, and --offline is set")
            return self.actions(package)
        stamp = self.cache.get(package)
        if stamp is None:
            if self.offline:
                raise Unreachable("not in the cache, and --offline is set")
            lookup = self.lookups.get(package.ecosystem)
            if lookup is None:
                raise Missing(f"unripe has no registry for {package.ecosystem}")
            stamp = lookup(package)
        self.cache.put(package.ecosystem, package.name, package.version, stamp)
        return stamp

    def once(self, key: str) -> threading.Lock:
        with self.guard:
            return self.pending.setdefault(key, threading.Lock())

    def versions(self, ecosystem: str, name: str) -> dict[str, datetime]:
        """Every version of a package with its publish time, fetched once per run. Not every registry can say."""
        listing = self.listings.get(ecosystem)
        if listing is None or self.offline:
            raise Missing(f"unripe cannot list every version on {ecosystem}")
        with self.once(f"{ecosystem}:{name}"):
            if (ecosystem, name) not in self.documents:
                self.documents[(ecosystem, name)] = listing(name)
            return self.documents[(ecosystem, name)]

    def every(self, package: Package, fetchall: Callable[[], dict[str, datetime]] | None = None) -> datetime:
        """
        For a registry whose one answer covers every version of a package: fetch
        it once, cache every version's time, then answer for the version asked about.
        The answer comes from the ecosystem's entry in `self.listings`, or from `fetchall`.
        """
        with self.once(f"{package.ecosystem}:{package.name}"):
            cached = self.cache.get(package)
            if cached:
                return cached
            key = (package.ecosystem, package.name)
            if key not in self.documents:
                self.documents[key] = fetchall() if fetchall else self.listings[package.ecosystem](package.name)
                for version, stamp in self.documents[key].items():
                    # an unpublished npm version has to come back through npm() to be reported
                    if (package.name, version) not in self.gone:
                        self.cache.learn(package.ecosystem, package.name, version, stamp)
            times = self.documents[key]
            if package.version not in times:
                raise Missing(f"{package} is not in the registry")
            return times[package.version]

    def basis(self, package: Package) -> str:
        """COMMIT for a version dated by a commit time, which its author sets; empty for one dated by a registry."""
        return self.cache.recall("basis", f"{package.ecosystem}:{package.name}@{package.version}") or ""

    def dated(self, ecosystem: str, name: str, version: str, basis: str) -> None:
        key = f"{ecosystem}:{name}@{version}"
        if basis:
            self.cache.note("basis", key, basis)
        else:
            self.cache.forget("basis", key)

    def pull(self, ecosystem: str, name: str, version: str) -> None:
        self.pulled.setdefault((ecosystem, name), set()).add(version)

    def deprecate(self, ecosystem: str, name: str, version: str) -> None:
        self.deprecated.setdefault((ecosystem, name), set()).add(version)

    def npm(self, package: Package) -> datetime:
        stamp = self.every(package)
        if (package.name, package.version) in self.gone and not self.replaying:
            # npm takes malicious versions down this way, so a lockfile that still has one should hear about it
            raise Said("unpublished from the npm registry, which is how npm takes down malicious versions")
        return stamp

    def npmall(self, name: str) -> dict[str, datetime]:
        data = fetch(f"{self.npmurl}/{urllib.parse.quote(name, safe='@')}")
        times = {}
        for version, stamp in ((data.get("time") if isinstance(data, dict) else None) or {}).items():
            if version in ("created", "modified"):
                continue
            try:
                times[version] = when(stamp)
            except (TypeError, ValueError, AttributeError):
                continue
        # the time document keeps versions that were unpublished; only those still listed can be suggested
        listed = data.get("versions") if isinstance(data, dict) else None
        if isinstance(listed, dict):
            for version in times:
                if version not in listed:
                    self.gone.add((name, version))
                    self.pull("npm", name, version)
                elif isinstance(listed[version], dict) and listed[version].get("deprecated"):
                    self.deprecate("npm", name, version)
        return times

    def pypi(self, package: Package) -> datetime:
        name, version = urllib.parse.quote(package.name), urllib.parse.quote(package.version)
        data = fetch(f"{self.pypiurl}/pypi/{name}/{version}/json")
        files = data.get("urls") if isinstance(data, dict) else None
        stamps = [when(f["upload_time_iso_8601"]) for f in files or [] if f.get("upload_time_iso_8601")]
        if not stamps:
            raise Missing(f"{package} has no files on PyPI")
        # the first file uploaded is when the version became installable
        return min(stamps)

    def pypiall(self, name: str) -> dict[str, datetime]:
        data = fetch(f"{self.pypiurl}/pypi/{urllib.parse.quote(name)}/json")
        times = {}
        for version, files in ((data.get("releases") if isinstance(data, dict) else None) or {}).items():
            stamps = [when(f["upload_time_iso_8601"]) for f in files or [] if isinstance(f, dict) and f.get("upload_time_iso_8601")]
            if not stamps:
                continue
            times[version] = min(stamps)
            if all(f.get("yanked") for f in files if isinstance(f, dict)):
                self.pull("pypi", name, version)
        return times

    def cargo(self, package: Package) -> datetime:
        return self.every(package)

    def cargoall(self, name: str) -> dict[str, datetime]:
        # the sparse index is a CDN that carries every version's publish time,
        # and is what crates.io asks tools to use instead of its rate limited API
        body = fetchtext(f"https://index.crates.io/{crateindex(name)}")
        times = {}
        for line in body.splitlines():
            try:
                entry = json.loads(line)
                times[str(entry["vers"])] = when(entry["pubtime"])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if entry.get("yanked"):
                self.pull("cargo", name, str(entry["vers"]))
        return times

    def go(self, package: Package) -> datetime:
        data = fetch(f"{goproxy()}/{goescape(package.name)}/@v/{goescape(package.version)}.info")
        if not isinstance(data, dict) or not data.get("Time"):
            raise Missing(f"{package} has no time in the module proxy")
        # the proxy's time is the commit's, which the author chooses
        committed = when(data["Time"])
        self.dated("go", package.name, package.version, COMMIT)
        if not gosumdb(package.name):
            return committed
        try:
            record = self.sumdb.record(package.name, package.version)
        except Missing:
            # not in the public checksum database (a private module, say): the commit time is all there is
            return committed
        window = self.sumdb.first(record)
        if window is None:
            return committed
        self.dated("go", package.name, package.version, "")
        earliest, _, guess = window
        # a commit inside the window is the usual case, a version tagged soon after it was made.
        # a commit from before the window is the one to distrust: an old commit tagged
        # recently, or a commit time set back on purpose
        return committed if committed >= earliest else guess

    def composer(self, package: Package) -> datetime:
        return self.every(package)

    def composerall(self, name: str) -> dict[str, datetime]:
        data = fetch(f"https://repo.packagist.org/p2/{name}.json")
        versions = ((data.get("packages") if isinstance(data, dict) else None) or {}).get(name) or []
        times = {}
        # p2 files are minified: each entry lists only what changed since the one before
        current: dict = {}
        for entry in versions:
            for key, value in entry.items():
                if value == "__unset":
                    current.pop(key, None)
                else:
                    current[key] = value
            if current.get("version") and current.get("time"):
                try:
                    times[str(current["version"]).lstrip("v")] = when(str(current["time"]))
                except ValueError:
                    continue
                if current.get("abandoned"):
                    self.deprecate("composer", name, str(current["version"]).lstrip("v"))
        return times

    def rubygems(self, package: Package) -> datetime:
        return self.every(package)

    def rubygemsall(self, name: str) -> dict[str, datetime]:
        data = fetch(f"https://rubygems.org/api/v1/versions/{urllib.parse.quote(name)}.json")
        times = {}
        for entry in data if isinstance(data, list) else []:
            number, platform = entry.get("number"), entry.get("platform") or "ruby"
            if not number or not entry.get("created_at"):
                continue
            # Gemfile.lock writes platform gems as `1.2.3-x86_64-linux`
            times[number if platform == "ruby" else f"{number}-{platform}"] = when(entry["created_at"])
        return times

    def nuget(self, package: Package) -> datetime:
        name, version = urllib.parse.quote(package.name.lower()), urllib.parse.quote(package.version.lower())
        try:
            leaf = fetch(f"https://api.nuget.org/v3/registration5-gz-semver2/{name}/{version}.json")
        except Missing:
            # the registration can lag a new version by an hour or more while it is already
            # installable. the package file's Last-Modified is a few minutes after publishing,
            # never before, so it can only make a version look younger
            return fetchmodified(f"https://api.nuget.org/v3-flatcontainer/{name}/{version}/{name}.{version}.nupkg")
        if not isinstance(leaf, dict) or not leaf.get("published"):
            raise Missing(f"{package} is not on nuget.org")
        published = when(str(leaf["published"]))
        if published.year > 1900:
            return published
        # nuget.org rewrites an unlisted version's publish time to 1900; the catalog keeps when it was created
        entry = leaf.get("catalogEntry")
        catalog = fetch(entry) if isinstance(entry, str) and entry.startswith("https://api.nuget.org/") else {}
        if not isinstance(catalog, dict) or not catalog.get("created"):
            raise Missing(f"{package} is unlisted on nuget.org and its catalog entry has no time")
        return when(str(catalog["created"]))

    def nugetall(self, name: str) -> dict[str, datetime]:
        """Every listed version from the registration index, whose big packages page their versions out."""
        index = fetch(f"https://api.nuget.org/v3/registration5-gz-semver2/{urllib.parse.quote(name.lower())}/index.json")
        times = {}
        for page in (index.get("items") if isinstance(index, dict) else None) or []:
            leaves = page.get("items") if isinstance(page, dict) else None
            if leaves is None and isinstance(page, dict) and str(page.get("@id", "")).startswith("https://api.nuget.org/"):
                leaves = (fetch(page["@id"]) or {}).get("items")
            for leaf in leaves or []:
                entry = leaf.get("catalogEntry") if isinstance(leaf, dict) else None
                if not isinstance(entry, dict) or not entry.get("version") or not entry.get("published"):
                    continue
                version = str(entry["version"]).lower()
                try:
                    stamp = when(str(entry["published"]))
                except ValueError:
                    continue
                # unlisted versions are dated 1900 here; they are never suggested anyway
                times[version] = stamp
                if entry.get("listed") is False or stamp.year <= 1900:
                    self.pull("nuget", name, version)
        return times

    def fixes(self, package: Package) -> list[str]:
        """
        The advisories that name this exact version as the fix. Malware reports
        (MAL- ids) are left out: they list bad versions, not fixed ones.
        """
        ecosystem = OSV.get(package.ecosystem)
        if ecosystem is None or self.offline:
            return []
        version = package.version.removeprefix("v") if package.ecosystem == "go" else package.version
        found: list[str] = []
        query: dict = {"package": {"name": package.name, "ecosystem": ecosystem}}
        for _ in range(10):
            answer = post("https://api.osv.dev/v1/query", query)
            for vuln in answer.get("vulns") or []:
                ident = str(vuln.get("id") or "")
                if not ident or ident.startswith("MAL-") or vuln.get("withdrawn"):
                    continue
                for affected in vuln.get("affected") or []:
                    for span in affected.get("ranges") or []:
                        if any(str(event.get("fixed", "")).removeprefix("v") == version.removeprefix("v") for event in span.get("events") or []):
                            if ident not in found:
                                found.append(ident)
            token = answer.get("next_page_token")
            if not token:
                break
            query = {**query, "page_token": token}
        return found

    def jsr(self, package: Package) -> datetime:
        try:
            return self.every(package)
        except Missing:
            if (package.name, package.version) not in self.undated:
                raise
        # meta.json only dates versions published since mid 2025; the API dates every one
        scope, _, short = package.name.removeprefix("@").partition("/")
        data = fetch(f"https://api.jsr.io/scopes/{scope}/packages/{short}/versions/{urllib.parse.quote(package.version)}")
        if not isinstance(data, dict) or not data.get("createdAt"):
            raise Missing(f"{package} has no time on jsr.io")
        return when(str(data["createdAt"]))

    def jsrall(self, name: str) -> dict[str, datetime]:
        # one file per package lists every version, with when it was created for the newer ones
        data = fetch(f"https://jsr.io/{name}/meta.json")
        times = {}
        for version, entry in ((data.get("versions") if isinstance(data, dict) else None) or {}).items():
            if not isinstance(entry, dict) or not entry.get("createdAt"):
                self.undated.add((name, version))
                continue
            try:
                times[version] = when(str(entry["createdAt"]))
            except ValueError:
                continue
            if entry.get("yanked"):
                self.pull("jsr", name, version)
        return times

    def tags(self, repository: str) -> dict[str, str]:
        """A repository's tags and the commit each points at, newest pages first, fetched once per run."""
        with self.once(f"tags:{repository}"):
            if ("tags", repository) not in self.documents:
                found: dict[str, str] = {}
                for page in range(1, 4):
                    batch = fetch(f"https://api.github.com/repos/{repository}/tags?per_page=100&page={page}")
                    for entry in batch if isinstance(batch, list) else []:
                        commit = entry.get("commit") if isinstance(entry, dict) else None
                        if isinstance(commit, dict) and entry.get("name") and commit.get("sha"):
                            found[str(entry["name"])] = str(commit["sha"])
                    if not isinstance(batch, list) or len(batch) < 100:
                        break
                self.documents[("tags", repository)] = found  # type: ignore[assignment]
            return self.documents[("tags", repository)]  # type: ignore[return-value]

    def actions(self, package: Package) -> datetime:
        """
        When the commit an action is pinned to was released: the publish time of
        the GitHub release whose tag points at that commit, preferring the most
        exact tag. A commit with no release falls back to its commit time,
        which the author controls, like the Go module proxy's.
        """
        repository, ref = package.name, package.version
        tags = self.tags(repository)
        commit = ref if SHA.match(ref) else tags.get(ref)
        if commit is None:
            raise Said(f"{ref} is a branch or an unknown tag, which has no release to date it by; pin a tag or a commit")
        return self.released(repository, commit, ref, "actions")

    def swift(self, package: Package) -> datetime:
        """A Swift package at a version is dated like an action at a tag; the tag may have a `v` in front."""
        repository, ref = package.name, package.version
        if SHA.match(ref):
            return self.released(repository, ref, ref, "swift")
        tags = self.tags(repository)
        tag = next((name for name in (ref, f"v{ref}") if name in tags), None)
        if tag is None:
            raise Said(f"{repository} has no tag {ref}")
        return self.released(repository, tags[tag], ref, "swift")

    def released(self, repository: str, commit: str, ref: str, ecosystem: str) -> datetime:
        """When a commit was released on GitHub, or failing that, its commit time."""
        tags = self.tags(repository)
        for tag in sorted((tag for tag, sha in tags.items() if sha == commit), key=specific, reverse=True):
            try:
                release = fetch(f"https://api.github.com/repos/{repository}/releases/tags/{urllib.parse.quote(tag)}")
            except Missing:
                continue
            if isinstance(release, dict) and release.get("published_at"):
                self.dated(ecosystem, repository, ref, "")
                return when(str(release["published_at"]))
        data = fetch(f"https://api.github.com/repos/{repository}/commits/{commit}")
        stamp = ((data.get("commit") or {}).get("committer") or {}).get("date") if isinstance(data, dict) else None
        if not stamp:
            raise Missing(f"{repository}@{ref} has no release and no commit time")
        self.dated(ecosystem, repository, ref, COMMIT)
        return when(str(stamp))

    def pub(self, package: Package) -> datetime:
        return self.every(package)

    def puball(self, name: str) -> dict[str, datetime]:
        data = fetch(f"https://pub.dev/api/packages/{urllib.parse.quote(name)}")
        times = {}
        for entry in (data.get("versions") if isinstance(data, dict) else None) or []:
            if isinstance(entry, dict) and entry.get("version") and entry.get("published"):
                try:
                    times[str(entry["version"])] = when(str(entry["published"]))
                except ValueError:
                    continue
                if entry.get("retracted"):
                    self.pull("pub", name, str(entry["version"]))
        return times

    def cocoapods(self, package: Package) -> datetime:
        return self.every(package)

    def cocoapodsall(self, name: str) -> dict[str, datetime]:
        data = fetch(f"https://trunk.cocoapods.org/api/v1/pods/{urllib.parse.quote(name)}")
        times = {}
        for entry in (data.get("versions") if isinstance(data, dict) else None) or []:
            if not isinstance(entry, dict) or not entry.get("name") or not entry.get("created_at"):
                continue
            stamp = str(entry["created_at"])
            try:
                # trunk writes "2024-03-31 01:24:57 UTC"
                times[str(entry["name"])] = datetime.strptime(stamp.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return times

    def terraform(self, package: Package) -> datetime:
        return self.every(package)

    def terraformall(self, name: str) -> dict[str, datetime]:
        data = fetch(f"https://registry.terraform.io/v2/providers/{urllib.parse.quote(name)}?include=provider-versions")
        times = {}
        for entry in (data.get("included") if isinstance(data, dict) else None) or []:
            attributes = entry.get("attributes") if isinstance(entry, dict) and entry.get("type") == "provider-versions" else None
            if isinstance(attributes, dict) and attributes.get("version") and attributes.get("published-at"):
                try:
                    times[str(attributes["version"])] = when(str(attributes["published-at"]))
                except ValueError:
                    continue
        return times

    def conan(self, package: Package) -> datetime:
        """
        When the locked recipe revision reached the remote, as the remote
        recorded it. Without a revision, the latest one, which is what Conan
        would install.
        """
        version, _, revision = package.version.partition("#")
        base = f"{self.conanurl}/v2/conans/{urllib.parse.quote(package.name)}/{urllib.parse.quote(version)}/_/_"
        if not revision:
            latest = fetch(f"{base}/latest")
            if not isinstance(latest, dict) or not latest.get("time"):
                raise Missing(f"{package} has no revision on {self.conanurl}")
            return when(str(latest["time"]))
        data = fetch(f"{base}/revisions")
        for entry in (data.get("revisions") if isinstance(data, dict) else None) or []:
            if isinstance(entry, dict) and entry.get("revision") == revision and entry.get("time"):
                return when(str(entry["time"]))
        raise Said(f"{package.name}/{version} has no recipe revision {revision} on {self.conanurl}")

    def maven(self, package: Package) -> datetime:
        group, _, artifact = package.name.partition(":")
        path = f"{group.replace('.', '/')}/{artifact}/{package.version}/{artifact}-{package.version}.pom"
        unreachable = None
        for repository in mavenrepos(group, artifact, self.mavenextra):
            try:
                # repositories never change a published file, so its Last-Modified is when it was published
                return fetchmodified(f"{repository}/{path}")
            except Missing:
                continue
            except Unreachable as error:
                unreachable = error
        if unreachable:
            raise unreachable
        raise Missing(f"{package} is in none of the Maven repositories unripe knows")

    def cran(self, package: Package) -> datetime:
        return self.every(package)

    def cranall(self, name: str) -> dict[str, datetime]:
        # crandb.r-pkg.org keeps every version CRAN published, archived ones included
        data = fetch(f"https://crandb.r-pkg.org/{urllib.parse.quote(name)}/all")
        data = data if isinstance(data, dict) else {}
        # the same answer also carries each version's own DESCRIPTION, whose
        # Date/Publication CRAN wrote in UTC
        versions = data.get("versions") if isinstance(data.get("versions"), dict) else {}
        timeline = data.get("timeline") if isinstance(data.get("timeline"), dict) else {}
        times = {}
        for version in set(timeline) | set(versions):
            entry = versions.get(version)
            try:
                if isinstance(entry, dict) and entry.get("Date/Publication"):
                    # CRAN writes "2026-07-15 10:58:26 UTC", and the oldest
                    # versions write it with no zone at all
                    times[str(version)] = datetime.strptime(str(entry["Date/Publication"]).replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                else:
                    # a few very old versions have no Date/Publication; their
                    # timeline is an hour early, which no longer matters by now
                    times[str(version)] = when(str(timeline[version]))
            except (KeyError, TypeError, ValueError):
                continue
        return times
