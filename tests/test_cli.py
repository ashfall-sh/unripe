import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import TESTDATA
from unripe import __version__, cli

LOCK = (TESTDATA / "npm-v3" / "package-lock.json").read_text("utf-8")
NAMES = [
    ("@types/node", "20.11.0"),
    ("ansi-regex", "5.0.1"),
    ("emoji-regex", "8.0.0"),
    ("is-fullwidth-code-point", "3.0.0"),
    ("is-number", "7.0.0"),
    ("left-pad", "1.3.0"),
    ("ms", "2.1.2"),
    ("ms", "2.1.3"),
    ("string-width", "4.2.3"),
    ("strip-ansi", "6.0.1"),
    ("undici-types", "5.26.5"),
]


@pytest.fixture
def project(fake, tmp_path, monkeypatch):
    """A directory with one npm lockfile whose versions the fake registry knows, all old."""
    (tmp_path / "package-lock.json").write_text(LOCK)
    fake.base = datetime.now(timezone.utc)
    for name, version in NAMES:
        fake.add("npm", name, version, timedelta(days=400))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_clean_run_exits_zero(project, capsys):
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "checked 11 versions in 1 lockfile" in out
    assert "pass: 0 unripe, 0 unknown, 11 ok, 0 excluded" in out


def test_check_is_the_default_command(project, capsys):
    assert cli.main([]) == 0
    assert cli.main(["."]) == 0
    assert cli.main(["--min-age", "2d"]) == 0


def test_a_fresh_version_exits_one(project, fake, capsys):
    fake.add("npm", "left-pad", "1.3.0", timedelta(hours=2))
    assert cli.main(["check", "."]) == 1
    out = capsys.readouterr().out
    assert "left-pad 1.3.0" in out and "2h old" in out
    assert cli.main(["check", "--min-age", "1h"]) == 0
    assert cli.main(["check", "--exclude", "left-pad"]) == 0


def test_unknown_exits_one_unless_warned(project, fake, capsys):
    fake.times.pop(("npm", "ms"))
    assert cli.main(["check"]) == 1
    assert cli.main(["check", "--unknown", "warn"]) == 0


def test_json_output_to_a_file_with_summary_and_sarif(project, fake, capsys):
    fake.add("npm", "ms", "2.1.3", timedelta(hours=1))
    code = cli.main(["check", "--format", "json", "--output", "out.json", "--summary", "summary.md", "--sarif", "out.sarif"])
    assert code == 1
    assert capsys.readouterr().out == ""
    data = json.loads((project / "out.json").read_text())
    assert data["summary"]["unripe"] == 1
    assert (project / "summary.md").read_text().startswith("### unripe: 1 version too new")
    assert json.loads((project / "out.sarif").read_text())["runs"][0]["results"][0]["ruleId"] == "unripe/too-new"


def test_summary_appends(project):
    (project / "summary.md").write_text("earlier step\n")
    cli.main(["check", "--summary", "summary.md"])
    assert (project / "summary.md").read_text().startswith("earlier step\n### unripe:")


def test_config_file_is_read(project, fake, capsys):
    fake.add("npm", "left-pad", "1.3.0", timedelta(days=5))
    assert cli.main(["check"]) == 0
    (project / ".unripe.toml").write_text('min-age = "1w"\n')
    assert cli.main(["check"]) == 1
    assert cli.main(["check", "--min-age", "3d"]) == 0


def test_config_paths_are_used_when_none_are_given(project, capsys):
    (project / "web").mkdir()
    (project / "package-lock.json").rename(project / "web" / "package-lock.json")
    (project / ".unripe.toml").write_text('paths = ["web"]\n')
    assert cli.main(["check"]) == 0
    assert "web/package-lock.json" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, message",
    [
        (["check", "--min-age", "soon"], "not a duration"),
        (["check", "missing-dir"], "no such file or directory: missing-dir"),
        (["check", "--config", "nope.toml"], "no such config file"),
    ],
)
def test_usage_errors_exit_two(project, capsys, argv, message):
    assert cli.main(argv) == 2
    assert message in capsys.readouterr().err


def test_no_lockfiles_exits_two(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["check"]) == 2
    assert "no lockfiles found" in capsys.readouterr().out


def test_bad_since_ref_exits_two(project, capsys):
    assert cli.main(["check", "--since", "no-such-ref"]) == 2
    assert "--since no-such-ref" in capsys.readouterr().err


def test_explain(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fake.base = datetime.now(timezone.utc)
    fake.add("pypi", "typing-extensions", "4.9.0", timedelta(days=400))
    fake.add("npm", "fresh", "2.0.0", timedelta(hours=3))
    assert cli.main(["explain", "pypi", "Typing_Extensions", "4.9.0"]) == 0
    assert "old enough" in capsys.readouterr().out
    assert cli.main(["explain", "npm", "fresh", "2.0.0"]) == 1
    assert "unripe: the minimum age is 3d" in capsys.readouterr().out
    assert cli.main(["explain", "npm", "missing", "1.0.0"]) == 1
    assert "publish time unknown" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main(["--version"])
    assert exit.value.code == 0
    assert capsys.readouterr().out.strip() == f"unripe {__version__}"


def test_exit_zero_reports_but_passes(project, fake, capsys):
    fake.add("npm", "left-pad", "1.3.0", timedelta(hours=2))
    assert cli.main(["check", "--exit-zero"]) == 0
    assert "left-pad 1.3.0" in capsys.readouterr().out


def test_as_of_checks_as_if_it_were_then(project, fake, capsys):
    published = fake.add("npm", "left-pad", "1.3.0", timedelta(days=10))
    assert cli.main(["check"]) == 0
    assert cli.main(["check", "--as-of", (published + timedelta(days=1)).isoformat()]) == 1
    assert cli.main(["check", "--as-of", (published + timedelta(days=4)).strftime("%Y-%m-%dT%H:%MZ")]) == 0
    capsys.readouterr()
    assert cli.main(["check", "--as-of", "last tuesday"]) == 2
    assert "--as-of 'last tuesday' is not a date" in capsys.readouterr().err


def test_offline_uses_only_the_cache(project, fake, capsys):
    assert cli.main(["check"]) == 0
    fake.down.add("npm")
    assert cli.main(["check", "--offline"]) == 0
    (project / "package-lock.json").write_text(LOCK.replace("1.3.0", "1.3.1"))
    fake.down.discard("npm")
    fake.add("npm", "left-pad", "1.3.1", timedelta(days=400))
    assert cli.main(["check", "--offline"]) == 1
    assert "not in the cache, and --offline is set" in capsys.readouterr().out


def test_annotate_and_json_side_output(project, fake, capsys):
    fake.add("npm", "ms", "2.1.3", timedelta(hours=1))
    assert cli.main(["check", "--annotate", "--json", "out.json"]) == 1
    out = capsys.readouterr().out
    assert "::error file=package-lock.json,line=" in out and out.index("fail: ") < out.index("::error")
    assert json.loads((project / "out.json").read_text())["summary"]["unripe"] == 1
    assert cli.main(["check", "--annotate", "--exit-zero"]) == 0
    assert "::warning file=package-lock.json" in capsys.readouterr().out


@pytest.mark.parametrize("form", ["github", "codequality", "junit"])
def test_ci_formats_run(project, fake, capsys, form):
    fake.add("npm", "ms", "2.1.3", timedelta(hours=1))
    assert cli.main(["check", "--format", form]) == 1
    assert "ms" in capsys.readouterr().out


def test_no_colour_when_not_a_terminal_or_no_color_is_set(project, fake, capsys, monkeypatch):
    fake.add("npm", "ms", "2.1.3", timedelta(hours=1))
    # NO_COLOR wins over FORCE_COLOR, so one left in the environment must not leak in
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    cli.main(["check"])
    assert "\033[31m" in capsys.readouterr().out
    monkeypatch.setenv("NO_COLOR", "1")
    cli.main(["check"])
    assert "\033[" not in capsys.readouterr().out


def test_a_config_further_up_sets_the_policy_but_not_the_paths(project, fake, capsys):
    (project / ".git").mkdir()
    (project / ".unripe.toml").write_text('min-age = "1w"\npaths = ["elsewhere"]\n')
    (project / "web").mkdir()
    (project / "package-lock.json").rename(project / "web" / "package-lock.json")
    fake.add("npm", "left-pad", "1.3.0", timedelta(days=5))
    import os

    os.chdir(project / "web")
    assert cli.main(["check"]) == 1
    assert "minimum age 1w" in capsys.readouterr().out


def test_explain_lists_recent_versions(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fake.base = datetime.now(timezone.utc)
    fake.add("npm", "lib", "2.0.0", timedelta(hours=3))
    fake.add("npm", "lib", "1.2.0", timedelta(days=30))
    fake.add("npm", "lib", "1.1.0", timedelta(days=60))
    fake.add("npm", "lib", "2.0.0-rc.1", timedelta(days=5))
    assert cli.main(["explain", "npm", "lib"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("npm lib: 4 versions, minimum age 3d, 1 prerelease not shown")
    assert "too new, old enough at" in out and "1.2.0" in out and "<- newest old enough" in out
    assert [row for row in out.splitlines() if "newest" in row][0].lstrip().startswith("1.2.0")
    assert cli.main(["explain", "go", "golang.org/x/net"]) == 2
    assert "give a version" in capsys.readouterr().err


def test_config_shows_the_policy_and_native_settings(project, capsys):
    (project / ".unripe.toml").write_text('min-age = "3d"\n[min-age-for]\nnpm = "7d"\n[[allow]]\npackage = "npm:axios@1.13.2"\nreason = "CVE"\nuntil = 2026-01-01\n')
    (project / "pnpm-workspace.yaml").write_text("packages:\n  - web\nminimumReleaseAge: 1440\n")
    (project / "uv.lock").write_text("version = 1\n")
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert "min-age-for npm      1w" in out and out.startswith("unripe settings from .unripe.toml")
    assert "allowed until 2026-01-01: CVE (ended)" in out
    assert "pnpm       pnpm-workspace.yaml: minimumReleaseAge = 1440 (1d), shorter than unripe's 3d" in out
    assert "npm, in .npmrc:\n      min-release-age=3" in out
    assert 'exclude-newer = "3 days"' in out


def test_init_writes_a_config_and_a_workflow(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/trunk\n")
    assert cli.main(["init", "--github", "--min-age", "72h"]) == 0
    assert 'min-age = "3d"' in (tmp_path / ".unripe.toml").read_text()
    flow = (tmp_path / ".github" / "workflows" / "unripe.yml").read_text()
    assert "branches: [trunk]" in flow and f"TexWoW/unripe@v{__version__}" in flow
    from unripe.policy import build, config

    settings, source = config(tmp_path)
    assert build(settings, source=source).min_age == timedelta(days=3)
    assert cli.main(["init"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert cli.main(["init", "--force"]) == 0


def test_report_writes_extra_formats(project, fake, capsys):
    fake.add("npm", "ms", "2.1.3", timedelta(hours=1))
    assert cli.main(["check", "--report", "codequality:cq.json", "--report", "junit:out.xml"]) == 1
    assert json.loads((project / "cq.json").read_text())[0]["check_name"] == "unripe/too-new"
    assert (project / "out.xml").read_text().startswith("<?xml")
    assert cli.main(["check", "--report", "pdf:out.pdf"]) == 2
    assert "use FORMAT:FILE" in capsys.readouterr().err


def test_quiet_prints_nothing_when_the_check_passes(project, fake, capsys):
    assert cli.main(["check", "--quiet"]) == 0
    assert capsys.readouterr().out == ""
    fake.add("npm", "left-pad", "1.3.0", timedelta(hours=2))
    assert cli.main(["check", "-q", "--no-cache"]) == 1
    assert "unripe: 1 version" in capsys.readouterr().out


def test_json_says_its_schema(project, capsys):
    assert cli.main(["check", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == 1 and data["findings"][0]["dated_by"] == "registry"


def test_init_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--github", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would write .unripe.toml:" in out and "would write .github/workflows/unripe.yml:" in out
    assert list(tmp_path.iterdir()) == []


def test_doctor(project, fake, capsys):
    fake.add("npm", "left-pad", "1.3.0", timedelta(days=3000))
    (project / "Cargo.lock").write_text("[[package]\n")
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "lockfiles  2 lockfiles found: Cargo.lock, package-lock.json" in out
    assert "could not read Cargo.lock" in out
    assert "  npm       11 versions locked, registry answers (left-pad 1.3.0 published" in out
    assert out.rstrip().endswith("doctor: 1 problem")
    (project / "Cargo.lock").unlink()
    fake.down.add("npm")
    assert cli.main(["doctor"]) == 1
    assert "registry did not answer" in capsys.readouterr().out
    fake.down.clear()
    assert cli.main(["doctor"]) == 0
    assert "doctor: everything works" in capsys.readouterr().out


def test_config_and_doctor_show_configured_registries_without_secrets(project, fake, capsys, monkeypatch):
    monkeypatch.delenv("NPM_TOKEN", raising=False)
    (project / ".unripe.toml").write_text('[registries]\nnpm = { url = "https://npm.example.com", token-env = "NPM_TOKEN" }\n')
    assert cli.main(["config"]) == 0
    assert "registries npm       https://npm.example.com (token from NPM_TOKEN, NOT SET)" in capsys.readouterr().out
    monkeypatch.setenv("NPM_TOKEN", "s3cret")
    cli.main(["config"])
    out = capsys.readouterr().out
    assert "(token from NPM_TOKEN, set)" in out and "s3cret" not in out
    monkeypatch.delenv("NPM_TOKEN")
    assert cli.main(["doctor"]) == 1
    assert "registry   npm at https://npm.example.com (token from NPM_TOKEN, NOT SET)" in capsys.readouterr().out
