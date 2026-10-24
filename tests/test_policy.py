from datetime import timedelta

import pytest

from unripe.policy import Policy, PolicyError, Rule, build, config, duration, spoken


@pytest.mark.parametrize(
    "text, expected",
    [
        ("3d", timedelta(days=3)),
        ("72h", timedelta(hours=72)),
        ("1w", timedelta(weeks=1)),
        ("90m", timedelta(minutes=90)),
        ("1.5d", timedelta(hours=36)),
        (" 2D ", timedelta(days=2)),
        ("7", timedelta(days=7)),
        (5, timedelta(days=5)),
        ("0d", timedelta(0)),
    ],
)
def test_durations(text, expected):
    assert duration(text) == expected


@pytest.mark.parametrize("text", ["", "3 days", "-1d", "1y", "d3", True])
def test_bad_durations(text):
    with pytest.raises(PolicyError):
        duration(text)


@pytest.mark.parametrize(
    "age, said",
    [(timedelta(days=3), "3d"), (timedelta(hours=36), "36h"), (timedelta(weeks=2), "2w"), (timedelta(minutes=90), "90m")],
)
def test_spoken(age, said):
    assert spoken(age) == said


@pytest.mark.parametrize(
    "pattern, ecosystem, name, version, hit",
    [
        ("left-pad", "npm", "left-pad", "1.0.0", True),
        ("left-pad", "cargo", "left-pad", "1.0.0", True),
        ("npm:left-pad", "cargo", "left-pad", "1.0.0", False),
        ("npm:@types/*", "npm", "@types/node", "20.0.0", True),
        ("@types/*", "npm", "@typescript/x", "1.0.0", False),
        ("left-pad@1.3.0", "npm", "left-pad", "1.3.0", True),
        ("left-pad@1.3.0", "npm", "left-pad", "1.3.1", False),
        ("@types/node@20.*", "npm", "@types/node", "20.11.0", True),
        ("golang.org/x/*", "go", "golang.org/x/net", "v0.30.0", True),
        ("Typing_Extensions", "pypi", "typing-extensions", "4.9.0", True),
        ("pypi:django", "pypi", "django", "5.0.2", True),
        ("Left-Pad", "npm", "left-pad", "1.0.0", False),
    ],
)
def test_exclude_rules(pattern, ecosystem, name, version, hit):
    assert Rule.parse(pattern).matches(ecosystem, name, version) is hit


def test_excluded_names_the_rule():
    policy = Policy(exclude=["npm:@myorg/*", " "])
    assert policy.excluded("npm", "@myorg/ui", "1.0.0") == "npm:@myorg/*"
    assert policy.excluded("npm", "react", "18.2.0") is None


def test_policy_checks_its_values():
    with pytest.raises(PolicyError):
        Policy(unknown="ignore")
    with pytest.raises(PolicyError):
        Policy(min_age=timedelta(days=-1))


def test_config_from_unripe_toml(tmp_path):
    (tmp_path / ".unripe.toml").write_text('min-age = "7d"\nexclude = ["npm:@myorg/*"]\nunknown = "warn"\npaths = ["web"]\n')
    (tmp_path / "pyproject.toml").write_text('[tool.unripe]\nmin-age = "1d"\n')
    settings, source = config(tmp_path)
    assert source.name == ".unripe.toml"
    policy = build(settings, source=source)
    assert policy.min_age == timedelta(days=7)
    assert policy.unknown == "warn"
    assert policy.paths == ["web"]
    assert policy.excluded("npm", "@myorg/x", "1.0.0")


def test_config_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n\n[tool.unripe]\nmin-age = "36h"\n')
    settings, source = config(tmp_path)
    assert build(settings, source=source).min_age == timedelta(hours=36)


def test_pyproject_without_a_table_is_no_config(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert config(tmp_path) == ({}, None)


def test_command_line_wins_and_excludes_add_up(tmp_path):
    (tmp_path / ".unripe.toml").write_text('min-age = "7d"\nexclude = "left-pad"\n')
    settings, source = config(tmp_path)
    policy = build(settings, "1d", ["ms"], "warn", source)
    assert policy.min_age == timedelta(days=1)
    assert policy.unknown == "warn"
    assert policy.exclude == ["left-pad", "ms"]


@pytest.mark.parametrize(
    "content, message",
    [
        ('min-age = "soon"', "not a duration"),
        ('minage = "3d"', "does not know minage"),
        ("exclude = [1, 2]", "list of strings"),
        ('unknown = "maybe"', "fail or warn"),
        ("min-age = ", "could not be read"),
    ],
)
def test_bad_config_is_an_error(tmp_path, content, message):
    (tmp_path / ".unripe.toml").write_text(content)
    with pytest.raises(PolicyError, match=message):
        settings, source = config(tmp_path)
        build(settings, source=source)


def test_an_explicit_config_must_exist(tmp_path):
    with pytest.raises(PolicyError, match="no such config"):
        config(tmp_path, tmp_path / "missing.toml")


def test_nuget_excludes_ignore_case():
    assert Rule.parse("nuget:newtonsoft.*").matches("nuget", "Newtonsoft.Json", "13.0.1")
    assert Rule.parse("maven:com.mycorp:*").matches("maven", "com.mycorp:core", "1.0.0")
    assert not Rule.parse("maven:com.mycorp:*").matches("maven", "com.other:core", "1.0.0")


def test_config_maven_repositories(tmp_path):
    (tmp_path / ".unripe.toml").write_text('maven-repositories = ["https://packages.confluent.io/maven"]\n')
    settings, source = config(tmp_path)
    assert build(settings, source=source).maven_repositories == ["https://packages.confluent.io/maven"]


def test_min_age_for_an_ecosystem_and_a_package():
    policy = build({"min-age": "3d", "min-age-for": {"npm": "7d", "npm:@types/*": "1d", "pypi:Django": "2w"}})
    assert policy.age("npm", "react", "19.0.0") == timedelta(days=7)
    assert policy.age("npm", "@types/node", "22.0.0") == timedelta(days=1)
    assert policy.age("pypi", "django", "5.0.0") == timedelta(weeks=2)
    assert policy.age("cargo", "serde", "1.0.0") == timedelta(days=3)
    assert policy.varies()
    assert not build({"min-age": "3d"}).varies()


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"min-age-for": "7d"}, "should be a table"),
        ({"min-age-for": {"npm": "soon"}}, "min-age-for npm"),
        ({"allow": [{"reason": "x"}]}, "needs a package"),
        ({"allow": [{"package": "a", "until": "next week"}]}, "should be a date"),
        ({"allow": [{"package": "a", "why": "x"}]}, "takes package, reason and until"),
        ({"allow-security-fixes": "yes"}, "true or false"),
    ],
)
def test_bad_new_settings(settings, message):
    with pytest.raises(PolicyError, match=message):
        build(settings)


def test_allow_entries_end_after_their_last_day():
    from datetime import date, datetime, timezone

    policy = build({"allow": [{"package": "npm:axios@1.13.2", "reason": "CVE fix", "until": date(2026, 10, 31)}, "pypi:requests"]})
    entry, stale = policy.allowed("npm", "axios", "1.13.2", datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))
    assert entry and entry.describe() == "allowed until 2026-10-31: CVE fix" and stale == []
    entry, stale = policy.allowed("npm", "axios", "1.13.2", datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc))
    assert entry is None and [str(s.rule) for s in stale] == ["npm:axios@1.13.2"]
    assert policy.allowed("npm", "axios", "1.13.3", datetime(2026, 10, 1, tzinfo=timezone.utc))[0] is None
    assert policy.allowed("pypi", "requests", "2.0", datetime(2030, 1, 1, tzinfo=timezone.utc))[0].describe() == "allowed"


def test_config_is_found_further_up_the_repository(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".unripe.toml").write_text('min-age = "5d"\n')
    (tmp_path / "web" / "src").mkdir(parents=True)
    monkeypatch.delenv("UNRIPE_CONFIG", raising=False)
    settings, source = config(tmp_path / "web" / "src")
    assert settings == {"min-age": "5d"} and source == tmp_path.resolve() / ".unripe.toml"


def test_config_is_not_looked_for_outside_a_repository(tmp_path, monkeypatch):
    (tmp_path / ".unripe.toml").write_text('min-age = "5d"\n')
    (tmp_path / "web").mkdir()
    monkeypatch.delenv("UNRIPE_CONFIG", raising=False)
    assert config(tmp_path / "web") == ({}, None)


def test_unripe_config_names_a_file(tmp_path, monkeypatch):
    (tmp_path / "policy.toml").write_text('min-age = "2d"\n')
    monkeypatch.setenv("UNRIPE_CONFIG", str(tmp_path / "policy.toml"))
    assert config(tmp_path)[0] == {"min-age": "2d"}


def test_registries_in_the_config():
    from unripe.policy import Mirror

    policy = build({"registries": {"npm": "https://npm.example.com/", "pypi": {"url": "https://pypi.example.com/simple-api", "basic-env": "PYPI_AUTH"}, "go": {"url": "https://goproxy.example.com", "token-env": "GO_TOKEN"}}})
    assert policy.registries == {
        "npm": Mirror("https://npm.example.com"),
        "pypi": Mirror("https://pypi.example.com/simple-api", basic_env="PYPI_AUTH"),
        "go": Mirror("https://goproxy.example.com", token_env="GO_TOKEN"),
    }


@pytest.mark.parametrize(
    "table, said",
    [
        ({"maven": "https://repo.example.com"}, "not maven"),
        ({"npm": {"token-env": "X"}}, "needs a url"),
        ({"npm": "ftp://npm.example.com"}, "not an http or https URL"),
        ({"npm": {"url": "http://npm.example.com", "token-env": "NPM_TOKEN"}}, "only sent over https"),
        ({"npm": {"url": "https://npm.example.com", "token-env": "A", "basic-env": "B"}}, "not both"),
        ({"npm": {"url": "https://npm.example.com", "token": "abc"}}, "it takes url, token-env and basic-env"),
        ("https://npm.example.com", "should be a table"),
    ],
)
def test_registries_mistakes_are_explained(table, said):
    with pytest.raises(PolicyError, match=said):
        build({"registries": table})
