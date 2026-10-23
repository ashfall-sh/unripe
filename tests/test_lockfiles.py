import json
from datetime import datetime, timezone

import pytest

from conftest import TESTDATA
from unripe import lockfiles
from unripe.lockfiles.javascript import jsonc, pnpmkey


def parse(relative: str):
    path = TESTDATA / relative
    return lockfiles.read(path.read_text("utf-8"), path)


def pairs(relative: str) -> set[tuple[str, str]]:
    return {(p.name, p.version) for p in parse(relative)}


NPM_V3 = {
    ("@types/node", "20.11.0"),
    ("ansi-regex", "5.0.1"),
    ("emoji-regex", "8.0.0"),
    ("is-fullwidth-code-point", "3.0.0"),
    ("is-number", "7.0.0"),
    ("left-pad", "1.3.0"),
    ("ms", "2.1.2"),
    ("ms", "2.1.3"),
    # locked under the alias string-width-cjs
    ("string-width", "4.2.3"),
    ("strip-ansi", "6.0.1"),
    ("undici-types", "5.26.5"),
}


def test_package_lock_v3_skips_links_git_and_direct_tarballs():
    assert pairs("npm-v3/package-lock.json") == NPM_V3


def test_package_lock_v1_walks_nested_and_skips_bundled_and_git():
    assert pairs("npm-v1/package-lock.json") == {
        ("debug", "4.3.4"),
        ("ms", "2.1.2"),
        ("ms", "2.1.3"),
        ("left-pad", "1.3.0"),
        ("string-width", "4.2.3"),
        ("wrapper", "1.0.0"),
    }


def test_every_package_records_its_lockfile_and_ecosystem():
    for package in parse("npm-v3/package-lock.json"):
        assert package.ecosystem == "npm"
        assert package.lockfile.endswith("npm-v3/package-lock.json")
        assert "\\" not in package.lockfile


def test_pnpm_v9():
    assert pairs("pnpm-v9/pnpm-lock.yaml") == (NPM_V3 - {("ms", "2.1.2")}) | {("react", "18.2.0")}


def test_pnpm_v6_drops_peer_suffix_and_skips_git_and_file():
    assert pairs("pnpm-v6/pnpm-lock.yaml") == {
        ("@babel/core", "7.24.0"),
        ("js-tokens", "4.0.0"),
        ("loose-envify", "1.4.0"),
        ("react", "18.2.0"),
        ("react-dom", "18.2.0"),
        ("scheduler", "0.23.0"),
    }


def test_pnpm_v5_slash_keys_with_peer_and_hash_suffixes():
    assert pairs("pnpm-v5/pnpm-lock.yaml") == {
        ("@babel/core", "7.24.0"),
        ("js-tokens", "4.0.0"),
        ("react", "18.2.0"),
        ("react-dom", "18.2.0"),
        ("styled-jsx", "5.1.1"),
    }


@pytest.mark.parametrize(
    "key, expected",
    [
        ("foo/1.0.0_react@18.0.0", ("foo", "1.0.0")),
        ("@scope/name/1.0.0_a@1.0.0+b@2.0.0", ("@scope/name", "1.0.0")),
        ("@scope/name@1.0.0", ("@scope/name", "1.0.0")),
        ("name@1.0.0-beta.1", ("name", "1.0.0-beta.1")),
    ],
)
def test_pnpm_keys(key, expected):
    assert pnpmkey(key) == expected


def test_yarn_classic_skips_git_and_file_and_decodes_scopes():
    assert pairs("yarn-classic/yarn.lock") == {
        ("@babel/code-frame", "7.23.5"),
        ("@scope/encoded", "2.0.1"),
        ("left-pad", "1.3.0"),
        ("string-width", "4.2.3"),
    }


def test_yarn_berry_skips_workspace_git_and_patch():
    assert pairs("yarn-berry/yarn.lock") == {
        ("@babel/code-frame", "7.23.5"),
        ("left-pad", "1.3.0"),
        ("resolve", "1.22.8"),
        ("string-width", "4.2.3"),
    }


def test_bun_lock_with_comments_and_trailing_commas():
    assert pairs("bun/bun.lock") == {
        ("@types/node", "20.11.0"),
        ("left-pad", "1.3.0"),
        ("string-width", "4.2.3"),
        ("undici-types", "5.26.5"),
    }


def test_jsonc_keeps_strings_intact():
    text = '{"a": "// not a comment", "b": "/* nor this */", "c": [1, 2,], /* gone */ "d": "x\\"y",}'
    assert json.loads(jsonc(text)) == {"a": "// not a comment", "b": "/* nor this */", "c": [1, 2], "d": 'x"y'}


def test_rna_leaves_strings_alone():
    from unripe.lockfiles.others import rna

    text = '{"Description": "NA and more NA", "OS_type": NA}'
    assert json.loads(rna(text)) == {"Description": "NA and more NA", "OS_type": None}


def test_bun_lockb_is_reported_unreadable():
    path = TESTDATA / "bun-binary/bun.lockb"
    with pytest.raises(lockfiles.Unreadable, match="binary"):
        lockfiles.read(path.read_bytes().decode("utf-8", "replace"), path)


def test_uv_takes_upload_times_and_skips_editable_git_and_other_indexes():
    found = {p.name: p for p in parse("uv/uv.lock")}
    assert set(found) == {"idna", "six", "zope-interface"}
    # the earliest file: here the wheel, two seconds before the sdist
    assert found["idna"].published == datetime(2023, 11, 25, 15, 40, 52, 604000, tzinfo=timezone.utc)
    assert found["zope-interface"].published is None


def test_pylock_takes_upload_times_and_skips_vcs_directories_and_other_indexes():
    found = {p.name: p for p in parse("pylock/pylock.toml")}
    assert {(p.name, p.version) for p in found.values()} == {("attrs", "25.1.0"), ("idna", "3.10")}
    # the earliest file, the wheel, as a toml datetime
    assert found["attrs"].published == datetime(2025, 1, 25, 11, 30, 10, tzinfo=timezone.utc)
    assert found["idna"].published is None


@pytest.mark.parametrize("name, hit", [("pylock.toml", True), ("pylock.dev.toml", True), ("pylock.a.b.toml", False), ("mypylock.toml", False)])
def test_pylock_names(name, hit):
    assert (lockfiles.parser(name) is not None) is hit


def test_poetry_keeps_pypi_and_pypi_mirrors_only():
    assert pairs("poetry/poetry.lock") == {("certifi", "2024.2.2"), ("pytest", "8.0.0"), ("typing-extensions", "4.9.0")}


def test_pipfile_lock_exact_versions_only():
    assert pairs("pipenv/Pipfile.lock") == {("django", "5.0.2"), ("sqlparse", "0.4.4"), ("pytest", "8.0.0")}


def test_requirements_exact_pins_only():
    assert pairs("requirements/requirements.txt") == {("certifi", "2024.2.2"), ("django", "5.0.2"), ("zope-interface", "6.2")}


def test_cargo_lock_crates_io_only():
    assert pairs("cargo/Cargo.lock") == {("itoa", "1.0.10"), ("memchr", "2.7.1"), ("serde", "1.0.197")}


def test_go_mod_applies_replacements():
    assert pairs("go/go.mod") == {
        ("github.com/BurntSushi/toml", "v1.3.2"),
        ("github.com/new/thing", "v1.2.0"),
        ("github.com/pkg/errors", "v0.9.1"),
        ("golang.org/x/sync", "v0.6.0"),
        ("golang.org/x/text", "v0.14.0"),
    }


def test_composer_lock_carries_times_and_skips_dev_branches():
    found = {p.name: p for p in parse("composer/composer.lock")}
    assert set(found) == {"monolog/monolog", "old/no-zone", "psr/log", "symfony/console", "phpunit/phpunit"}
    assert found["monolog/monolog"].published == datetime(2023, 10, 27, 15, 32, 31, tzinfo=timezone.utc)
    assert found["old/no-zone"].published == datetime(2015, 10, 2, 12, 0, tzinfo=timezone.utc)
    assert found["old/no-zone"].version == "1.0.0"
    assert found["symfony/console"].published is None


def test_gemfile_lock_rubygems_org_only_with_platforms():
    assert pairs("ruby/Gemfile.lock") == {("nokogiri", "1.16.0-x86_64-linux"), ("racc", "1.7.3"), ("rack", "3.0.9")}


@pytest.mark.parametrize(
    "name, text",
    [
        ("package-lock.json", "{not json"),
        ("Cargo.lock", "[[package]\nname = "),
        ("uv.lock", "version = = 1"),
        ("renv.lock", "{not json"),
    ],
)
def test_broken_lockfiles_are_unreadable_not_empty(name, text):
    with pytest.raises(lockfiles.Unreadable, match="could not be read"):
        lockfiles.read(text, name)


def test_find_walks_directories_and_skips_installed_copies(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "package-lock.json").write_text("{}")
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "package-lock.json").write_text("{}")
    (tmp_path / "testdata").mkdir()
    (tmp_path / "testdata" / "go.mod").write_text("module x")
    (tmp_path / "requirements-dev.txt").write_text("")
    (tmp_path / "notes.txt").write_text("")
    found = {p.relative_to(tmp_path).as_posix() for p in lockfiles.find([str(tmp_path)])}
    assert found == {"app/package-lock.json", "requirements-dev.txt"}


def test_find_reads_a_file_named_twice_once(tmp_path):
    lock = tmp_path / "Cargo.lock"
    lock.write_text("")
    assert len(lockfiles.find([str(tmp_path), str(lock)])) == 1


def test_find_prefers_bun_lock_over_the_old_binary(tmp_path):
    (tmp_path / "bun.lock").write_text("{}")
    (tmp_path / "bun.lockb").write_bytes(b"\0")
    assert [p.name for p in lockfiles.find([str(tmp_path)])] == ["bun.lock"]


def test_nuget_lock_skips_projects_and_normalizes_versions():
    assert pairs("nuget/packages.lock.json") == {
        ("NETStandard.Library", "2.0.3"),
        ("Newtonsoft.Json", "13.0.1"),
        ("Microsoft.NETCore.Platforms", "1.1.0"),
        ("Old.Style.Version", "1.0.0"),
        ("With.Metadata", "2.1.0"),
        ("System.Text.Json", "8.0.5"),
        ("Azure.Core", "1.38.0-beta.1"),
    }


@pytest.mark.parametrize(
    "written, normal",
    [("1.0", "1.0.0"), ("1.0.0.0", "1.0.0"), ("1.2.3.4", "1.2.3.4"), ("01.002.3", "1.2.3"), ("2.0.0-Beta.1", "2.0.0-beta.1"), ("3.1.0+sha.abc", "3.1.0"), ("weird", "weird")],
)
def test_nuget_versions(written, normal):
    from unripe.lockfiles.dotnet import normal as nugetnormal

    assert nugetnormal(written) == normal


def test_gradle_lockfile():
    found = {(p.ecosystem, p.name, p.version) for p in parse("gradle/gradle.lockfile")}
    assert found == {
        ("maven", "androidx.core:core", "1.12.0"),
        ("maven", "com.fasterxml.jackson.core:jackson-annotations", "2.22"),
        ("maven", "com.fasterxml.jackson.core:jackson-core", "2.21.4"),
        ("maven", "com.fasterxml.jackson.core:jackson-core", "2.22.2"),
        ("maven", "com.google.guava:guava", "33.0.0-jre"),
        ("maven", "com.gradle.develocity:com.gradle.develocity.gradle.plugin", "3.18.1"),
        ("maven", "io.confluent:common-utils", "8.1.0"),
    }


def test_old_gradle_lockfiles_are_found_by_their_folder(tmp_path):
    found = lockfiles.find([str(TESTDATA / "gradle-legacy")])
    assert [p.name for p in found] == ["compileClasspath.lockfile"]
    assert pairs("gradle-legacy/gradle/dependency-locks/compileClasspath.lockfile") == {
        ("com.google.guava:failureaccess", "1.0.2"),
        ("com.google.guava:guava", "33.0.0-jre"),
    }
    # a .lockfile anywhere else is not a gradle lockfile
    (tmp_path / "other.lockfile").write_text("a:b:1.0\n")
    assert lockfiles.find([str(tmp_path)]) == []


def test_find_skips_dotnet_and_gradle_build_output(tmp_path):
    for folder in ("bin", "obj", ".gradle"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "packages.lock.json").write_text("{}")
    (tmp_path / "packages.lock.json").write_text("{}")
    assert [p.relative_to(tmp_path).as_posix() for p in lockfiles.find([str(tmp_path)])] == ["packages.lock.json"]


def test_deno_lock_npm_and_jsr_without_peers_remote_or_other_registries():
    assert {(p.ecosystem, p.name, p.version) for p in parse("deno/deno.lock")} == {
        ("npm", "@types/node", "22.15.15"),
        ("npm", "chalk", "5.4.1"),
        ("npm", "preact-render-to-string", "6.5.11"),
        ("npm", "preact", "10.24.3"),
        ("npm", "undici-types", "6.21.0"),
        ("jsr", "@std/assert", "1.0.13"),
        ("jsr", "@std/internal", "1.0.7"),
    }


def test_deno_lock_version_3_keeps_packages_in_one_table():
    text = '{"version": "3", "packages": {"specifiers": {}, "jsr": {"@std/path@0.220.1": {"integrity": "x"}}, "npm": {"ms@2.1.3": {"integrity": "x", "dependencies": {}}}}, "remote": {}}'
    assert {(p.ecosystem, p.name, p.version) for p in lockfiles.read(text, "deno.lock")} == {("jsr", "@std/path", "0.220.1"), ("npm", "ms", "2.1.3")}


WORKFLOW = """\
on: push
jobs:
  test:
    uses: octo/shared/.github/workflows/test.yml@v2
  build:
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - name: setup
        uses: "actions/setup-node@v5"
      - uses: ./.github/actions/local
      - uses: docker://alpine:3.20
      - run: echo "uses: fake/thing@v1"
"""


def test_workflows_list_actions_with_their_refs():
    found = [(p.ecosystem, p.name, p.version) for p in lockfiles.read(WORKFLOW, ".github/workflows/ci.yml")]
    assert found == [
        ("actions", "octo/shared", "v2"),
        ("actions", "actions/checkout", "3d3c42e5aac5ba805825da76410c181273ba90b1"),
        ("actions", "actions/setup-node", "v5"),
    ]


def test_workflows_are_only_found_when_asked(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
    (tmp_path / "action.yml").write_text("runs:\n  steps:\n    - uses: actions/cache@v4\n")
    (tmp_path / "docs.yml").write_text("uses: a/b@v1\n")
    assert lockfiles.find([str(tmp_path)]) == []
    names = sorted(p.name for p in lockfiles.find([str(tmp_path)], workflows=True))
    assert names == ["action.yml", "ci.yml"]



def test_pubspec_lock_keeps_pub_dev_only():
    assert pairs("pub/pubspec.lock") == {("args", "2.7.0"), ("collection", "1.19.1"), ("sqlite3", "2.9.4")}
    assert {p.ecosystem for p in parse("pub/pubspec.lock")} == {"pub"}


def test_podfile_lock_keeps_trunk_root_pods_only():
    assert pairs("cocoapods/Podfile.lock") == {
        ("CocoaLumberjack", "3.7.4"),
        ("GRDB.swift", "5.26.0"),
        ("Logging", "1.4.0"),
        ("SwiftProtobuf", "1.38.1"),
    }


def test_old_podfile_lock_without_spec_repos():
    text = "PODS:\n  - Alamofire (4.9.1)\n  - Local (1.0.0)\n\nEXTERNAL SOURCES:\n  Local:\n    :path: ../Local\n\nCOCOAPODS: 1.6.1\n"
    assert {(p.name, p.version) for p in lockfiles.read(text, "Podfile.lock")} == {("Alamofire", "4.9.1")}


def test_package_resolved_keeps_github_pins_with_versions_or_commits():
    assert pairs("swift/Package.resolved") == {
        ("kylef/PathKit", "1.0.1"),
        ("apple/swift-argument-parser", "1.7.0"),
        ("example/nightly", "0123456789abcdef0123456789abcdef01234567"),
    }
    assert pairs("swift-v1/Package.resolved") == {("maxdesiatov/XMLCoder", "0.14.0")}


def test_terraform_lock_keeps_the_public_registry_only():
    assert pairs("terraform/.terraform.lock.hcl") == {("hashicorp/aws", "5.31.0"), ("integrations/github", "5.42.0")}


def test_find_skips_downloaded_pods_and_providers(tmp_path):
    for folder in ("Pods", ".terraform/providers", ".dart_tool", ".build"):
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "Podfile.lock").write_text("PODS:\n")
    (tmp_path / "Podfile.lock").write_text("PODS:\n")
    assert [p.name for p in lockfiles.find([str(tmp_path)])] == ["Podfile.lock"]


def test_conan_lock_keeps_conancenter_references_with_their_revision():
    assert pairs("conan/conan.lock") == {
        ("zlib", "1.3.1#b8bc2603263cf7eccbd6e17e66b0ed76"),
        ("openssl", "3.3.2#9f9f130d58e7c13e76bb8a559f0a6a8b"),
        ("fmt", "11.0.2#5c7438ef4d5d69ab106a41e460ce11f3"),
        ("cmake", "3.30.5#a0fa3ff9e3a2ee6a1d4e8c6fa7b2c1d0"),
    }
    # conan 1 wrote a graph of nodes, here without revisions
    assert pairs("conan-v1/conan.lock") == {("zlib", "1.2.13")}
    with pytest.raises(lockfiles.Unreadable):
        lockfiles.read("[]", "conan.lock")


def test_renv_lock_keeps_cran_packages_only():
    # renv 1.0.7 is the entry with R's NA written bare, which is not JSON
    assert pairs("renv/renv.lock") == {("MASS", "7.3-54"), ("R6", "2.5.1"), ("fs", "1.6.4"), ("renv", "1.0.7")}
    assert {p.ecosystem for p in parse("renv/renv.lock")} == {"cran"}


def locked(url: str, entry: dict | None = None) -> list:
    """A renv.lock whose one repository is called RSPM and points at `url`."""
    return lockfiles.read(json.dumps({
        "R": {"Version": "4.4.1", "Repositories": [{"Name": "RSPM", "URL": url}]},
        "Packages": {"fs": entry or {"Package": "fs", "Version": "1.6.4", "Source": "Repository", "Repository": "RSPM"}},
    }), "renv.lock")


def test_renv_reads_a_posit_package_manager_url_by_its_path():
    """PPM serves CRAN under /cran/ and Bioconductor under /bioconductor/, so the path decides."""
    assert [p.name for p in locked("https://packagemanager.posit.co/cran/latest")] == ["fs"]
    assert [p.name for p in locked("https://packagemanager.posit.co/bioconductor/latest")] == []
    assert [p.name for p in locked("https://cran.r-project.org")] == ["fs"]
    assert [p.name for p in locked("https://cloud.r-project.org")] == ["fs"]
    # p3m.dev and the older rstudio host are Posit too, and a URL with no path is not CRAN
    assert [p.name for p in locked("https://p3m.dev/cran/latest")] == ["fs"]
    assert [p.name for p in locked("https://packagemanager.rstudio.com/cran/latest")] == ["fs"]
    assert [p.name for p in locked("https://packagemanager.posit.co/cran")] == []
    assert [p.name for p in locked("https://example.org/cran/latest")] == []


def test_renv_reads_paks_remote_repos():
    """pak writes the URL it installed from, once or as a list, under whichever name."""
    cran = {"Package": "fs", "Version": "1.6.4", "Source": "Repository", "RemoteRepos": "https://packagemanager.posit.co/cran/latest"}
    assert [p.name for p in locked("https://example.org", cran)] == ["fs"]
    bioc = dict(cran, RemoteRepos=["https://packagemanager.posit.co/bioconductor/latest"])
    assert [p.name for p in locked("https://example.org", bioc)] == []
    # a repository the lockfile names CRAN is CRAN whatever URL is written against it
    assert [p.name for p in locked("https://example.org", dict(cran, Source="CRAN", Repository="CRAN"))] == ["fs"]
    # a mirror that is not CRAN under any name, and a local path
    assert [p.name for p in locked("https://example.org")] == []
    assert [p.name for p in locked("https://example.org", {"Package": "fs", "Version": "1.6.4", "Source": "Repository"})] == []
    assert [p.name for p in locked("https://example.org", {"Package": "fs", "Version": "1.6.4", "Source": "GitHub", "RemoteRepos": "https://cran.r-project.org"})] == ["fs"]


def test_renv_reads_repositories_written_as_an_object():
    """renv wrote R.Repositories as a name to URL object before it wrote a list."""
    text = json.dumps({
        "R": {"Version": "4.4.1", "Repositories": {"CRAN": "https://cloud.r-project.org"}},
        "Packages": {"MASS": {"Package": "MASS", "Version": "7.3-54", "Source": "Repository", "Repository": "CRAN"}},
    })
    assert [(p.name, p.version) for p in lockfiles.read(text, "renv.lock")] == [("MASS", "7.3-54")]


def test_damaged_lockfiles_never_crash_or_invent_packages():
    """Every fixture cut short and scrambled: a parser either reads packages or says it cannot, never anything else."""
    import random

    chance = random.Random(20261022)
    for path in lockfiles.find([str(TESTDATA)]):
        if path.name == "bun.lockb":
            continue
        text = path.read_text("utf-8")
        damaged = [text[: len(text) * share // 8] for share in range(8)]
        for _ in range(6):
            letters = list(text)
            for spot in chance.sample(range(len(letters)), min(len(letters), 12)):
                letters[spot] = chance.choice("{}[]\":,=\n #@/\x00")
            damaged.append("".join(letters))
        damaged += ["", "\x00\x01\x02", "null", "[]", '{"packages": 1}', "﻿"]
        for broken in damaged:
            try:
                found = lockfiles.read(broken, path)
            except lockfiles.Unreadable:
                continue
            assert isinstance(found, list), path
            for package in found:
                assert package.ecosystem and package.name and package.version, (path, package)
