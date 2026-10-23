# The JSON report

`unripe check --format json` (or `--json FILE`) writes one JSON object. This
page describes schema 1.

Fields may be added in any release. A field changing meaning or going away
bumps `schema`, so a script can check it:

```python
import json, subprocess

report = json.loads(subprocess.run(["unripe", "check", "--format", "json"], capture_output=True, text=True).stdout)
assert report["schema"] == 1
```

## Top level

| Field | Type | |
|---|---|---|
| `schema` | number | `1` |
| `unripe` | string | the unripe version that wrote it |
| `checked_at` | string | ISO 8601 time the check ran as of (`--as-of` sets it) |
| `result` | string | `pass`, `fail` or `error` |
| `exit_code` | number | `0`, `1` or `2`, as the command exits (before `--exit-zero`) |
| `policy` | object | `min_age` (like `"3d"`), `min_age_seconds`, `unknown` (`fail` or `warn`), `exclude` (list) |
| `since` | string or null | the `--since` ref |
| `lockfiles` | list of strings | every lockfile read |
| `unreadable` | list | `{"lockfile", "error"}` for each lockfile that couldn't be read |
| `notes` | list of strings | things worth knowing, like an allow entry that is no longer needed |
| `summary` | object | how many findings in each state: `unripe`, `unknown`, `allowed`, `ok`, `excluded` |
| `findings` | list | one per package version, below |

## A finding

| Field | Type | |
|---|---|---|
| `ecosystem` | string | `npm`, `pypi`, `cargo`, `go`, `composer`, `rubygems`, `nuget`, `maven`, `jsr`, `actions`, `pub`, `cocoapods`, `swift`, `terraform`, `conan` or `cran` |
| `name` | string | as the registry names it (PyPI names normalised, Maven as `group:artifact`) |
| `version` | string | as locked; Conan versions include the recipe revision after `#` |
| `state` | string | `ok`, `unripe`, `unknown`, `allowed` or `excluded` |
| `published` | string or null | ISO 8601 publish time, when known |
| `dated_by` | string or null | `registry`, or `commit` when the time is a git commit's, which its author sets |
| `age_seconds` | number or null | age at `checked_at` |
| `old_enough_at` | string or null | when the version reaches its minimum age |
| `min_age_seconds` | number | the minimum age that applied to this package |
| `reason` | string or null | why it is unknown, allowed or excluded |
| `instead` | object or null | for a version that is too new: `{"version", "published"}` of the newest older version in the same line that is old enough |
| `locations` | list | `{"lockfile", "line"}` for every lockfile that locks it; `line` may be null |

## Other formats

`--format sarif` is SARIF 2.1.0, with rules `unripe/too-new`,
`unripe/unknown-age` and `unripe/unreadable`. `codequality` is GitLab's code quality format, `junit`
is JUnit XML, `github` is workflow commands, `markdown` is the table the
Action posts. Those follow their own specifications.
