import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NOW
from unripe import registries
from unripe.model import Package
from unripe.registries import Cache, Missing, Registries, Unreachable, crateindex, goescape, goproxy, gosumdb


@pytest.mark.parametrize(
    "ecosystem, name, version",
    [
        ("npm", "@types/node", "20.11.0"),
        ("pypi", "django", "5.0.2"),
        ("cargo", "serde", "1.0.197"),
        ("go", "github.com/BurntSushi/toml", "v1.3.2"),
        ("composer", "monolog/monolog", "3.5.0"),
        ("rubygems", "nokogiri", "1.16.0-x86_64-linux"),
    ],
)
def test_every_ecosystem_answers(fake, lookups, ecosystem, name, version):
    moment = fake.add(ecosystem, name, version, timedelta(days=10))
    assert lookups.published(Package(ecosystem, name, version)) == moment


def test_npm_scoped_names_are_one_path_segment(fake, lookups):
    fake.add("npm", "@types/node", "20.11.0")
    lookups.published(Package("npm", "@types/node", "20.11.0"))
    assert fake.urls == ["https://registry.npmjs.org/@types%2Fnode"]


def test_npm_fetches_a_package_once_for_all_its_versions(fake, lookups):
    for version in ("2.1.2", "2.1.3"):
        fake.add("npm", "ms", version)
    lookups.published(Package("npm", "ms", "2.1.2"))
    lookups.published(Package("npm", "ms", "2.1.3"))
    assert len(fake.urls) == 1


def test_pypi_takes_the_first_file_uploaded(fake, lookups):
    moment = fake.add("pypi", "django", "5.0.2")
    # the fake uploads a wheel an hour after the sdist
    assert lookups.published(Package("pypi", "django", "5.0.2")) == moment


def test_cargo_reads_the_sparse_index(fake, lookups):
    fake.add("cargo", "syn", "2.0.50")
    lookups.published(Package("cargo", "syn", "2.0.50"))
    assert fake.urls == ["https://index.crates.io/3/s/syn"]


@pytest.mark.parametrize(
    "name, path",
    [("a", "1/a"), ("cc", "2/cc"), ("syn", "3/s/syn"), ("serde", "se/rd/serde"), ("Inflector", "in/fl/inflector")],
)
def test_crate_index_paths(name, path):
    assert crateindex(name) == path


def test_go_escapes_capitals(fake, lookups):
    fake.add("go", "github.com/BurntSushi/toml", "v1.3.2")
    lookups.published(Package("go", "github.com/BurntSushi/toml", "v1.3.2"))
    assert fake.urls[0] == "https://proxy.golang.org/github.com/!burnt!sushi/toml/@v/v1.3.2.info"
    assert "https://sum.golang.org/lookup/github.com/!burnt!sushi/toml@v1.3.2" in fake.urls
    assert goescape("github.com/Azure/Go") == "github.com/!azure/!go"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", "https://proxy.golang.org"),
        ("direct", "https://proxy.golang.org"),
        ("https://goproxy.example.com,direct", "https://goproxy.example.com"),
        ("off", "https://proxy.golang.org"),
        ("direct|https://goproxy.example.com/", "https://goproxy.example.com"),
    ],
)
def test_goproxy(monkeypatch, value, expected):
    monkeypatch.setenv("GOPROXY", value)
    assert goproxy() == expected


def test_composer_expands_minified_entries(fake, lookups):
    older = fake.add("composer", "psr/log", "2.0.0", timedelta(days=900))
    fake.add("composer", "psr/log", "3.0.0", timedelta(days=800))
    assert lookups.published(Package("composer", "psr/log", "2.0.0")) == older


def test_composer_unset_fields_are_dropped(monkeypatch, lookups):
    payload = {
        "packages": {
            "a/b": [
                {"name": "a/b", "version": "v2.0.0", "time": "2024-01-02T00:00:00+00:00"},
                {"version": "v1.0.0", "time": "__unset"},
            ]
        }
    }
    monkeypatch.setattr(registries, "fetchtext", lambda url, accept="*/*": json.dumps(payload))
    with pytest.raises(Missing):
        lookups.published(Package("composer", "a/b", "1.0.0"))


def test_rubygems_matches_platform(fake, lookups):
    plain = fake.add("rubygems", "nokogiri", "1.16.0", timedelta(days=5))
    linux = fake.add("rubygems", "nokogiri", "1.16.0-x86_64-linux", timedelta(days=4))
    assert lookups.published(Package("rubygems", "nokogiri", "1.16.0")) == plain
    assert lookups.published(Package("rubygems", "nokogiri", "1.16.0-x86_64-linux")) == linux


def test_missing_and_unreachable_are_different(fake, lookups):
    with pytest.raises(Missing):
        lookups.published(Package("npm", "nope", "1.0.0"))
    fake.add("cargo", "serde", "1.0.0")
    fake.down.add("cargo")
    with pytest.raises(Unreachable):
        lookups.published(Package("cargo", "serde", "1.0.0"))


def test_a_version_the_registry_lacks_is_missing(fake, lookups):
    fake.add("npm", "left-pad", "1.3.0")
    with pytest.raises(Missing):
        lookups.published(Package("npm", "left-pad", "9.9.9"))


def test_times_carried_by_the_lockfile_need_no_registry(fake, lookups):
    moment = NOW - timedelta(days=2)
    assert lookups.published(Package("composer", "x/y", "1.0.0", published=moment)) == moment
    assert fake.urls == []


def test_cache_survives_a_restart(fake, tmp_path):
    moment = fake.add("npm", "left-pad", "1.3.0")
    first = Registries(Cache(tmp_path))
    first.published(Package("npm", "left-pad", "1.3.0"))
    first.cache.save()
    asked = len(fake.urls)
    second = Registries(Cache(tmp_path))
    assert second.published(Package("npm", "left-pad", "1.3.0")) == moment
    assert len(fake.urls) == asked
    assert json.loads((tmp_path / "npm.json").read_text())["left-pad@1.3.0"].startswith("2025-")


def test_a_corrupt_cache_is_ignored(fake, tmp_path):
    (tmp_path / "npm.json").write_text("{broken")
    fake.add("npm", "left-pad", "1.3.0")
    assert Registries(Cache(tmp_path)).published(Package("npm", "left-pad", "1.3.0"))


def test_registry_urls_from_the_environment(monkeypatch):
    monkeypatch.setenv("UNRIPE_NPM_REGISTRY", "https://npm.example.com/")
    monkeypatch.setenv("UNRIPE_PYPI_URL", "https://pypi.example.com")
    found = Registries(Cache(None))
    assert found.npmurl == "https://npm.example.com"
    assert found.pypiurl == "https://pypi.example.com"


class Response(io.BytesIO):
    def __init__(self, body: bytes, headers: dict | None = None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_retries_server_errors_then_answers(monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        assert request.get_header("User-agent").startswith("unripe/")
        return Response(b'{"ok": true}')

    monkeypatch.setattr(registries, "send", urlopen)
    monkeypatch.setattr(registries.time, "sleep", lambda seconds: None)
    assert registries.fetch("https://example.com/x") == {"ok": True}
    assert len(calls) == 3


def test_fetch_gives_up_as_unreachable(monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(registries, "send", urlopen)
    monkeypatch.setattr(registries.time, "sleep", lambda seconds: None)
    with pytest.raises(Unreachable, match="no route"):
        registries.fetch("https://example.com/x")


def test_fetch_404_is_missing_without_retrying(monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(registries, "send", urlopen)
    with pytest.raises(Missing):
        registries.fetch("https://example.com/x")
    assert calls == [1]


def test_fetch_non_json_is_unreachable(monkeypatch):
    monkeypatch.setattr(registries, "send", lambda request, timeout: Response(b"<html>"))
    with pytest.raises(Unreachable, match="not JSON"):
        registries.fetch("https://example.com/x")


def test_when_reads_every_registry_style():
    utc = timezone.utc
    assert registries.when("2024-01-02T03:04:05.678Z") == datetime(2024, 1, 2, 3, 4, 5, 678000, tzinfo=utc)
    assert registries.when("2024-01-02T03:04:05+00:00") == datetime(2024, 1, 2, 3, 4, 5, tzinfo=utc)
    assert registries.when("2024-01-02T03:04:05") == datetime(2024, 1, 2, 3, 4, 5, tzinfo=utc)


def test_only_checked_versions_are_saved(fake, tmp_path):
    for version in ("2.1.1", "2.1.2", "2.1.3"):
        fake.add("npm", "ms", version)
    found = Registries(Cache(tmp_path))
    found.published(Package("npm", "ms", "2.1.2"))
    found.published(Package("npm", "ms", "2.1.3"))
    found.cache.save()
    assert len(fake.urls) == 1
    assert sorted(json.loads((tmp_path / "npm.json").read_text())) == ["ms@2.1.2", "ms@2.1.3"]


def test_an_unchanged_cache_is_not_rewritten(fake, tmp_path):
    fake.add("npm", "ms", "2.1.3")
    first = Registries(Cache(tmp_path))
    first.published(Package("npm", "ms", "2.1.3"))
    first.cache.save()
    second = Registries(Cache(tmp_path))
    second.published(Package("npm", "ms", "2.1.3"))
    assert second.cache.dirty == set()


def test_go_distrusts_a_commit_older_than_the_version(fake, lookups):
    # an old commit (or a commit time set back on purpose), tagged and first seen five hours ago
    fake.add("go", "example.com/sneaky", "v1.0.0", timedelta(days=200), seen=timedelta(hours=5))
    published = lookups.published(Package("go", "example.com/sneaky", "v1.0.0"))
    assert abs(published - (NOW - timedelta(hours=5))) < timedelta(minutes=1)


def test_go_trusts_a_commit_inside_the_window(fake, lookups):
    commit = fake.add("go", "example.com/honest", "v1.0.0", timedelta(days=2), seen=timedelta(days=2) - timedelta(minutes=3))
    assert lookups.published(Package("go", "example.com/honest", "v1.0.0")) == commit


def test_go_in_a_wide_window_takes_the_youngest_possible_age(fake, lookups):
    # first seen 20 days ago, from a commit 300 days old: the window around 20 days is days wide
    fake.add("go", "example.com/later", "v1.0.0", timedelta(days=300), seen=timedelta(days=20))
    published = lookups.published(Package("go", "example.com/later", "v1.0.0"))
    assert NOW - timedelta(days=20) <= published <= NOW - timedelta(days=17)


def test_go_versions_over_a_year_old_keep_the_commit_time(fake, lookups):
    commit = fake.add("go", "example.com/old", "v1.0.0", timedelta(days=900), seen=timedelta(days=800))
    assert lookups.published(Package("go", "example.com/old", "v1.0.0")) == commit


def test_go_versions_the_checksum_database_lacks_keep_the_commit_time(fake, lookups):
    commit = fake.add("go", "example.com/private", "v1.0.0", timedelta(days=1))
    del fake.sumdb[("example.com/private", "v1.0.0")]
    assert lookups.published(Package("go", "example.com/private", "v1.0.0")) == commit


@pytest.mark.parametrize(
    "variable, value",
    [("GOSUMDB", "off"), ("GOSUMDB", "sum.example.com"), ("GONOSUMDB", "example.com"), ("GOPRIVATE", "*.example.com,example.com/team/*")],
)
def test_go_private_settings_skip_the_checksum_database(fake, lookups, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    commit = fake.add("go", "example.com/team/app", "v1.0.0", timedelta(days=200), seen=timedelta(hours=5))
    assert lookups.published(Package("go", "example.com/team/app", "v1.0.0")) == commit
    assert not any("golang.org/lookup" in url or "index.golang.org" in url for url in fake.urls)


@pytest.mark.parametrize(
    "patterns, module, private",
    [
        ("example.com", "example.com/a/b", True),
        ("example.com/team/*", "example.com/team/app/sub", True),
        ("example.com/team/*", "example.com/other/app", False),
        ("*.corp.io", "git.corp.io/x", True),
        ("", "github.com/x/y", False),
    ],
)
def test_gosumdb_patterns(monkeypatch, patterns, module, private):
    monkeypatch.delenv("GOSUMDB", raising=False)
    monkeypatch.delenv("GONOSUMDB", raising=False)
    monkeypatch.setenv("GOPRIVATE", patterns)
    assert gosumdb(module) is not private


def test_go_fails_closed_when_the_checksum_database_is_down(fake, lookups):
    fake.add("go", "example.com/x", "v1.0.0", timedelta(days=200), seen=timedelta(hours=5))
    fake.down.add("sumdb")
    with pytest.raises(Unreachable):
        lookups.published(Package("go", "example.com/x", "v1.0.0"))


def test_go_calibration_is_shared_and_cached(fake, tmp_path):
    for number in range(5):
        fake.add("go", f"example.com/m{number}", "v1.0.0", timedelta(days=30 + number))
    first = Registries(Cache(tmp_path), now=NOW)
    for number in range(5):
        first.published(Package("go", f"example.com/m{number}", "v1.0.0"))
    first.cache.save()
    asked = sum("index.golang.org" in url for url in fake.urls)
    # a binary search over the ladder, shared by every module, not a walk per module
    assert 0 < asked <= 10
    assert json.loads((tmp_path / "gosumdb.json").read_text())
    fake.urls.clear()
    second = Registries(Cache(tmp_path), now=NOW)
    fake.add("go", "example.com/m9", "v1.0.0", timedelta(days=31))
    second.published(Package("go", "example.com/m9", "v1.0.0"))
    assert not any("index.golang.org" in url for url in fake.urls)


def test_fetch_undoes_gzip(monkeypatch):
    import gzip

    body = gzip.compress(b'{"zipped": true}')
    monkeypatch.setattr(registries, "send", lambda request, timeout: Response(body, {"Content-Encoding": "gzip"}))
    assert registries.fetch("https://example.com/x") == {"zipped": True}


def test_fetchmodified_reads_last_modified(monkeypatch):
    def urlopen(request, timeout):
        assert request.get_method() == "HEAD"
        return Response(b"", {"Last-Modified": "Mon, 18 Dec 2023 20:49:31 GMT"})

    monkeypatch.setattr(registries, "send", urlopen)
    assert registries.fetchmodified("https://example.com/x.pom") == datetime(2023, 12, 18, 20, 49, 31, tzinfo=timezone.utc)


def test_fetchmodified_without_the_header_is_unreachable(monkeypatch):
    monkeypatch.setattr(registries, "send", lambda request, timeout: Response(b""))
    with pytest.raises(Unreachable, match="Last-Modified"):
        registries.fetchmodified("https://example.com/x.pom")


def test_nuget_asks_for_the_version_in_lower_case(fake, lookups):
    moment = fake.add("nuget", "Newtonsoft.Json", "13.0.1", timedelta(days=50))
    assert lookups.published(Package("nuget", "Newtonsoft.Json", "13.0.1")) == moment
    assert fake.urls == ["https://api.nuget.org/v3/registration5-gz-semver2/newtonsoft.json/13.0.1.json"]


def test_nuget_unlisted_versions_use_the_catalog_time(fake, lookups):
    # nuget.org says 1900 for an unlisted version, which would make a fresh one look ancient
    moment = fake.add("nuget", "Some.Package", "2.0.0", timedelta(hours=6))
    fake.unlisted.add(("some.package", "2.0.0"))
    assert lookups.published(Package("nuget", "Some.Package", "2.0.0")) == moment
    assert any("/catalog0/" in url for url in fake.urls)


def test_nuget_missing_version(fake, lookups):
    fake.add("nuget", "Some.Package", "2.0.0")
    with pytest.raises(Missing):
        lookups.published(Package("nuget", "Some.Package", "9.9.9"))


def test_maven_central(fake, lookups):
    moment = fake.add("maven", "com.google.guava:guava", "33.0.0-jre", timedelta(days=300))
    assert lookups.published(Package("maven", "com.google.guava:guava", "33.0.0-jre")) == moment
    assert fake.urls == ["https://repo1.maven.org/maven2/com/google/guava/guava/33.0.0-jre/guava-33.0.0-jre.pom"]


def test_maven_android_artifacts_try_google_first(fake, lookups):
    fake.add("maven", "androidx.core:core", "1.12.0")
    fake.mavenrepo["androidx.core:core"] = registries.GOOGLE
    lookups.published(Package("maven", "androidx.core:core", "1.12.0"))
    assert fake.urls == ["https://dl.google.com/dl/android/maven2/androidx/core/core/1.12.0/core-1.12.0.pom"]


def test_maven_plugin_markers_try_the_plugin_portal_first(fake, lookups):
    fake.add("maven", "com.gradle.develocity:com.gradle.develocity.gradle.plugin", "3.18.1")
    fake.mavenrepo["com.gradle.develocity:com.gradle.develocity.gradle.plugin"] = registries.PLUGINS
    lookups.published(Package("maven", "com.gradle.develocity:com.gradle.develocity.gradle.plugin", "3.18.1"))
    assert fake.urls[0].startswith("https://plugins.gradle.org/m2/com/gradle/develocity/")
    assert len(fake.urls) == 1


def test_maven_falls_through_to_other_repositories_then_missing(fake, lookups):
    fake.add("maven", "com.example:lib", "1.0.0")
    fake.mavenrepo["com.example:lib"] = "https://elsewhere.example.com/maven"
    with pytest.raises(Missing):
        lookups.published(Package("maven", "com.example:lib", "1.0.0"))
    assert len(fake.urls) == 3


def test_maven_extra_repositories_from_config_and_environment(fake, monkeypatch):
    moment = fake.add("maven", "io.confluent:common-utils", "8.1.0")
    fake.mavenrepo["io.confluent:common-utils"] = "https://packages.confluent.io/maven"
    found = Registries(Cache(None), now=NOW, mavenrepos=["https://packages.confluent.io/maven/"])
    assert found.published(Package("maven", "io.confluent:common-utils", "8.1.0")) == moment
    monkeypatch.setenv("UNRIPE_MAVEN_REPOS", "https://packages.confluent.io/maven")
    assert Registries(Cache(None), now=NOW).published(Package("maven", "io.confluent:common-utils", "8.1.0")) == moment


def test_maven_an_unreachable_repository_is_not_a_missing_version(fake, lookups):
    fake.add("maven", "com.google.guava:guava", "33.0.0-jre")
    fake.down.add("maven")
    with pytest.raises(Unreachable):
        lookups.published(Package("maven", "com.google.guava:guava", "33.0.0-jre"))


def test_nuget_falls_back_to_the_package_file_while_the_registration_lags(fake, lookups):
    fake.nupkgs[("brand.new", "1.0.0")] = NOW - timedelta(minutes=20)
    assert lookups.published(Package("nuget", "Brand.New", "1.0.0")) == NOW - timedelta(minutes=20)
    assert fake.urls[-1] == "https://api.nuget.org/v3-flatcontainer/brand.new/1.0.0/brand.new.1.0.0.nupkg"
    with pytest.raises(Missing):
        lookups.published(Package("nuget", "Brand.New", "2.0.0"))


def test_jsr_versions_too_old_for_meta_json_are_dated_by_the_api(fake, lookups):
    old = fake.add("jsr", "@std/bytes", "1.0.6", at=datetime(2024, 5, 13, tzinfo=timezone.utc))
    new = fake.add("jsr", "@std/bytes", "1.0.7", timedelta(days=10))
    assert lookups.published(Package("jsr", "@std/bytes", "1.0.7")) == new
    assert lookups.published(Package("jsr", "@std/bytes", "1.0.6")) == old
    with pytest.raises(Missing):
        lookups.published(Package("jsr", "@std/bytes", "9.9.9"))



@pytest.mark.parametrize("ecosystem, name, version", [("pub", "sqlite3", "2.9.4"), ("cocoapods", "GRDB.swift", "5.26.0"), ("terraform", "hashicorp/aws", "5.31.0"), ("cran", "jsonlite", "1.8.4")])
def test_app_registries_answer_and_suggest(fake, lookups, ecosystem, name, version):
    fresh = fake.add(ecosystem, name, version, timedelta(hours=3))
    fake.add(ecosystem, name, "0.0.1", timedelta(days=90))
    assert lookups.published(Package(ecosystem, name, version)) == fresh
    assert set(lookups.versions(ecosystem, name)) == {version, "0.0.1"}


def test_cran_prefers_date_publication_over_the_timeline(fake, lookups):
    """crandb's timeline sits an hour before each version's own Date/Publication, which is what a lookup goes by."""
    moment = fake.add("cran", "jsonlite", "1.8.4", at=datetime(2022, 12, 6, 8, 10, 2, tzinfo=timezone.utc))
    assert lookups.cranall("jsonlite") == {"1.8.4": moment}
    # an hour early is crandb's own offset; a wider one must not be believed either
    fake.timelineearly[("jsonlite", "1.8.4")] = timedelta(hours=5)
    assert lookups.cranall("jsonlite") == {"1.8.4": moment}


def test_cran_falls_back_to_the_timeline_when_date_publication_is_missing(fake, lookups):
    """A few very old versions carry no Date/Publication; the timeline is all there is for them."""
    moment = fake.add("cran", "MASS", "7.3-0", at=datetime(2009, 5, 7, 11, 20, 43, tzinfo=timezone.utc))
    fake.nopublication.add(("MASS", "7.3-0"))
    assert lookups.cranall("MASS") == {"7.3-0": moment - timedelta(hours=1)}


def test_swift_is_dated_by_the_release_of_its_tag(fake, lookups):
    fake.tags["apple/swift-argument-parser"] = {"1.7.0": "c" * 40, "1.6.0": "d" * 40}
    fake.releases[("apple/swift-argument-parser", "1.7.0")] = NOW - timedelta(days=2)
    fake.tags["kylef/PathKit"] = {"v1.0.1": "e" * 40}
    fake.commits[("kylef/PathKit", "e" * 40)] = NOW - timedelta(days=900)
    assert lookups.published(Package("swift", "apple/swift-argument-parser", "1.7.0")) == NOW - timedelta(days=2)
    # a tag with a v in front, and no release: the commit time
    assert lookups.published(Package("swift", "kylef/PathKit", "1.0.1")) == NOW - timedelta(days=900)
    with pytest.raises(Missing):
        lookups.published(Package("swift", "kylef/PathKit", "9.9.9"))


def test_conan_is_dated_by_the_locked_recipe_revision(fake, lookups):
    fake.add("conan", "zlib", "1.3.1#aaaa", timedelta(days=300))
    newer = fake.add("conan", "zlib", "1.3.1#bbbb", timedelta(hours=6))
    old = lookups.published(Package("conan", "zlib", "1.3.1#aaaa"))
    assert old == NOW - timedelta(days=300)
    assert fake.urls[-1] == "https://center2.conan.io/v2/conans/zlib/1.3.1/_/_/revisions"
    # without a revision, the latest one: what conan would install
    assert lookups.published(Package("conan", "zlib", "1.3.1")) == newer
    with pytest.raises(Missing, match="no recipe revision cccc"):
        lookups.published(Package("conan", "zlib", "1.3.1#cccc"))
    with pytest.raises(Missing):
        lookups.published(Package("conan", "nothing", "1.0#aaaa"))


def test_dates_from_commit_times_are_marked_until_a_release_appears(fake, lookups):
    fake.tags["acme/tool"] = {"v1": "a" * 40, "v2": "b" * 40}
    fake.releases[("acme/tool", "v2")] = NOW - timedelta(days=30)
    fake.commits[("acme/tool", "a" * 40)] = NOW - timedelta(days=60)
    first, second = Package("actions", "acme/tool", "v1"), Package("actions", "acme/tool", "v2")
    assert lookups.published(first) == NOW - timedelta(days=60)
    assert lookups.published(second) == NOW - timedelta(days=30)
    assert lookups.basis(first) == "commit" and lookups.basis(second) == ""
    fake.releases[("acme/tool", "v1")] = NOW - timedelta(days=50)
    assert lookups.published(first) == NOW - timedelta(days=50)
    assert lookups.basis(first) == ""


def test_a_go_module_outside_the_checksum_database_is_dated_by_its_commit(fake, lookups, monkeypatch):
    monkeypatch.setenv("GONOSUMDB", "git.example.com/*")
    fake.add("go", "git.example.com/team/lib", "v1.0.0", timedelta(days=40))
    package = Package("go", "git.example.com/team/lib", "v1.0.0")
    lookups.published(package)
    assert lookups.basis(package) == "commit"
    fake.add("go", "github.com/BurntSushi/toml", "v1.3.2", timedelta(days=10))
    public = Package("go", "github.com/BurntSushi/toml", "v1.3.2")
    lookups.published(public)
    assert lookups.basis(public) == ""


def test_a_configured_registry_is_asked_with_its_own_credentials(fake, monkeypatch):
    from unripe.policy import Mirror

    monkeypatch.delenv("UNRIPE_NPM_REGISTRY", raising=False)
    monkeypatch.setenv("NPM_TOKEN", "s3cret")
    monkeypatch.setenv("PYPI_AUTH", "me:pw")
    lookups = Registries(Cache(None), now=NOW, mirrors={
        "npm": Mirror("https://npm.example.com/api/npm", token_env="NPM_TOKEN"),
        "pypi": Mirror("https://pypi.example.com", basic_env="PYPI_AUTH"),
        "go": Mirror("https://goproxy.example.com"),
    })
    assert lookups.npmurl == "https://npm.example.com/api/npm" and lookups.gourl == "https://goproxy.example.com"
    assert registries.authorization("https://npm.example.com/api/npm/left-pad") == "Bearer s3cret"
    assert registries.authorization("https://pypi.example.com/pypi/idna/json") == "Basic bWU6cHc="
    # never to another host, another scheme, a neighbouring path or the public registry
    for elsewhere in ("https://registry.npmjs.org/left-pad", "http://npm.example.com/api/npm/x", "https://npm.example.com/api/npmx/y", "https://evil.example.com/api/npm/x"):
        assert registries.authorization(elsewhere) is None
    # a variable that isn't set sends nothing
    monkeypatch.delenv("NPM_TOKEN")
    assert registries.authorization("https://npm.example.com/api/npm/left-pad") is None
    # the environment's address wins, and the configured credentials don't follow it there
    monkeypatch.setenv("NPM_TOKEN", "s3cret")
    monkeypatch.setenv("UNRIPE_NPM_REGISTRY", "https://mirror.example.org")
    moved = Registries(Cache(None), now=NOW, mirrors={"npm": Mirror("https://npm.example.com/api/npm", token_env="NPM_TOKEN")})
    assert moved.npmurl == "https://mirror.example.org"
    assert registries.authorization("https://mirror.example.org/left-pad") is None
    # a run without the config forgets them
    Registries(Cache(None), now=NOW)
    assert registries.authorization("https://npm.example.com/api/npm/left-pad") is None


def test_goproxy_from_the_environment_wins_over_the_config(fake, monkeypatch):
    from unripe.policy import Mirror

    monkeypatch.setenv("GOPROXY", "https://corp.example.com/go,direct")
    assert registries.goproxy("https://goproxy.example.com") == "https://corp.example.com/go"
    monkeypatch.delenv("GOPROXY")
    lookups = Registries(Cache(None), now=NOW, mirrors={"go": Mirror("https://goproxy.example.com")})
    assert registries.goproxy(lookups.gourl) == "https://goproxy.example.com"
