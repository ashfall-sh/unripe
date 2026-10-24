"""
`unripe mcp`: an MCP server on stdin and stdout, so coding agents (Claude,
ChatGPT's Codex, Cursor, Copilot and others) can check how old a package
version is before they add it, and check lockfiles after they change them.

Standard library only, like the rest of unripe. It speaks both MCP eras:
the stateless revision (2026-07-28, every request carries its version in
`_meta`) and the older `initialize` handshake that most clients still use.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from unripe import __version__, native
from unripe.check import Problem, check, instead, judge, releases
from unripe.lockfiles.python import normal
from unripe.model import ECOSYSTEMS, REGISTRY, Package
from unripe.policy import PolicyError, build, config, spoken
from unripe.registries import Cache, Missing, Registries, Unreachable, cachedir
from unripe.report import jsondoc

MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER = {"name": "unripe", "title": "unripe", "version": __version__}

INSTRUCTIONS = (
    "unripe says how long ago a package version was published and whether that is older than the "
    "project's minimum age (3 days unless the project's .unripe.toml says otherwise). Most malicious "
    "releases are found and pulled within days, so a version that is too new is one to avoid for now, "
    "not one known to be bad. Before adding or upgrading a dependency, call check_package without a "
    "version and use the newest version it says is old enough. After changing a lockfile, call "
    "check_lockfiles with since set to the base branch. Never treat an unknown age as fine."
)

ECOSYSTEM = {
    "type": "string",
    "enum": list(ECOSYSTEMS),
    "description": "The registry: npm (also pnpm, yarn, bun), pypi, cargo, go, composer, rubygems, nuget, maven "
    "(group:artifact), jsr, pub (Dart and Flutter), cocoapods, swift (owner/repo on GitHub), terraform "
    "(namespace/provider), conan (C and C++ from ConanCenter), cran (R packages on CRAN), actions (owner/repo).",
}
MIN_AGE = {"type": "string", "description": "Override the minimum age, like 72h, 3d or 1w. Leave out to use the project's policy."}

TOOLS = [
    {
        "name": "check_package",
        "title": "Check a package version's age",
        "description": (
            "Say when a package version was published and whether it is old enough under the project's minimum "
            "age. Leave version out to list recent releases and get the newest version that is old enough: use "
            "that version when adding or upgrading a dependency. A version that is too new comes with the "
            "newest older version that passes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ecosystem": ECOSYSTEM,
                "name": {"type": "string", "description": "The package name, exactly as the registry knows it, like left-pad, @types/node or requests."},
                "version": {"type": "string", "description": "The exact version. Leave out to list recent releases."},
                "min_age": MIN_AGE,
            },
            "required": ["ecosystem", "name"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    },
    {
        "name": "check_lockfiles",
        "title": "Check lockfiles for versions that are too new",
        "description": (
            "Check every lockfile under a directory (package-lock.json, pnpm-lock.yaml, yarn.lock, bun.lock, "
            "deno.lock, uv.lock, poetry.lock, requirements.txt, Cargo.lock, go.mod, composer.lock, Gemfile.lock, "
            "packages.lock.json, gradle.lockfile, pubspec.lock, Podfile.lock, Package.resolved, "
            ".terraform.lock.hcl, conan.lock and more) and report every locked version that is younger than the "
            "minimum age or whose age is unknown. Set since to a git ref, like origin/main, to check only what "
            "changed since then."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "A directory or lockfile. Defaults to the current directory."},
                "since": {"type": "string", "description": "Only check versions added since this git ref, like origin/main or HEAD."},
                "min_age": MIN_AGE,
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    },
    {
        "name": "cooldown_settings",
        "title": "Show cooldown settings",
        "description": (
            "Show the unripe policy for a directory, the minimum release age settings its package managers and "
            "update bots already have (npm, pnpm, yarn, bun, uv, Poetry, Deno, Cargo, Bundler, Dependabot, "
            "Renovate), where they disagree, and the lines that would add the missing ones."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "The project directory. Defaults to the current directory."}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]


class Refusal(Exception):
    """A request the server will not serve, as a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class Wrong(Exception):
    """Arguments a tool cannot use. Reported to the model as a tool error it can correct."""


def stamp(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def policy_for(directory: Path, min_age: str | None):
    settings, source = config(directory)
    return build(settings, min_age, (), None, source)


def where(path: str | None) -> Path:
    target = Path(path or ".").expanduser()
    if not target.exists():
        raise Wrong(f"no such file or directory: {target}")
    return target


def check_package(arguments: dict) -> tuple[str, dict]:
    ecosystem, name, version = arguments.get("ecosystem"), arguments.get("name"), arguments.get("version")
    if ecosystem not in ECOSYSTEMS:
        raise Wrong(f"ecosystem must be one of {', '.join(ECOSYSTEMS)}")
    if not isinstance(name, str) or not name.strip():
        raise Wrong("name is required")
    name = normal(name) if ecosystem == "pypi" else name.strip()
    policy = policy_for(Path.cwd(), arguments.get("min_age"))
    found = Registries(Cache(cachedir()), mavenrepos=policy.maven_repositories, mirrors=policy.registries)
    now = datetime.now(timezone.utc)
    try:
        if version:
            version = str(version).strip()
            version = version.lstrip("v") if ecosystem == "composer" else version
            finding = judge(Package(ecosystem, name, version), policy, found, now)
            better = instead(finding, found, now) if finding.state == "unripe" else None
            data = {
                "ecosystem": ecosystem,
                "name": name,
                "version": version,
                "state": finding.state,
                "old_enough": finding.state in ("ok", "allowed", "excluded"),
                "published": stamp(finding.published),
                "age_hours": round(finding.age_seconds / 3600, 1) if finding.age_seconds is not None else None,
                "dated_by": (finding.basis or "registry") if finding.published else None,
                "min_age": spoken(finding.minimum or policy.min_age),
                "old_enough_at": stamp(finding.ripe),
                "reason": finding.reason or None,
                "use_instead": {"version": better[0], "published": stamp(better[1])} if better else None,
            }
            if finding.state == "unknown":
                text = f"{ecosystem} {name} {version}: publish time unknown ({finding.reason}). Do not treat it as old enough."
            elif finding.state == "unripe":
                text = (
                    f"{ecosystem} {name} {version} was published {data['published']}, less than the {data['min_age']} minimum. "
                    f"It is old enough at {data['old_enough_at']}."
                )
                text += f" Use {better[0]} instead (published {stamp(better[1])})." if better else " No older version in the same line is old enough."
            elif finding.state == "excluded":
                text = f"{ecosystem} {name} {version} is excluded by the project's config ({finding.reason})."
            else:
                text = f"{ecosystem} {name} {version} was published {data['published']} and is old enough (minimum {data['min_age']})."
                if finding.basis:
                    text += f" That date is its {finding.basis} time, which the author sets, not a registry's."
                if finding.state == "allowed":
                    text += f" It is too new, but {finding.reason}."
            return text, data
        minimum = policy.age(ecosystem, name, "")
        try:
            listed = releases(found, ecosystem, name, minimum, now)
        except Missing as error:
            if "cannot list" in str(error):
                raise Wrong(f"unripe cannot list every version on {ecosystem}; give a version to check") from error
            raise
        newest = listed.newest
        data = {
            "ecosystem": ecosystem,
            "name": name,
            "min_age": spoken(minimum),
            "newest_old_enough": {"version": newest[0], "published": stamp(newest[1])} if newest else None,
            "recent": [
                {
                    "version": release,
                    "published": stamp(published),
                    "old_enough": published <= now - minimum and release not in listed.pulled,
                    "pulled": release in listed.pulled,
                    "deprecated": release in listed.deprecated,
                }
                for release, published in listed.shown
            ],
        }
        if newest:
            text = f"Use {ecosystem} {name} {newest[0]}, published {stamp(newest[1])}: the newest release at least {spoken(minimum)} old."
            if newest[0] in listed.deprecated:
                text += " Its maintainers have deprecated it; check whether the package has a replacement."
        else:
            text = f"No release of {ecosystem} {name} is {spoken(minimum)} old yet."
        return text, data
    except Missing:
        raise Wrong(f"{name} was not found on {REGISTRY.get(ecosystem, ecosystem)}") from None
    except Unreachable as error:
        raise Wrong(f"{REGISTRY.get(ecosystem, ecosystem)} did not answer ({error}); treat the age as unknown") from None
    finally:
        found.cache.save()


def check_lockfiles(arguments: dict) -> tuple[str, dict]:
    target = where(arguments.get("path"))
    directory = target if target.is_dir() else target.parent
    policy = policy_for(directory, arguments.get("min_age"))
    found = Registries(Cache(cachedir()), mavenrepos=policy.maven_repositories, mirrors=policy.registries)
    try:
        result = check([str(target)], policy, found, since=arguments.get("since") or None)
    except Problem as error:
        raise Wrong(str(error)) from None
    data = json.loads(jsondoc(result))
    # the passing versions are noise to an agent; the counts say how many there were
    data["findings"] = [f for f in data["findings"] if f["state"] in ("unripe", "unknown", "allowed")]
    problems = [f for f in data["findings"] if f["state"] in ("unripe", "unknown")]
    lines = [f"{data['result']}: {', '.join(f'{count} {state}' for state, count in data['summary'].items())} in {len(result.lockfiles)} lockfiles."]
    for f in problems[:50]:
        detail = f"published {f['published']}, old enough at {f['old_enough_at']}" if f["state"] == "unripe" else f["reason"]
        line = f"- {f['state']}: {f['ecosystem']} {f['name']} {f['version']} ({detail})"
        if f.get("instead"):
            line += f"; {f['instead']['version']} is old enough"
        lines.append(line)
    if not result.lockfiles:
        lines.append("No lockfiles were found there.")
    lines.extend(f"Could not read {message}" for _, message in result.unreadable)
    return "\n".join(lines), data


def cooldown_settings(arguments: dict) -> tuple[str, dict]:
    target = where(arguments.get("path"))
    directory = target if target.is_dir() else target.parent
    policy = policy_for(directory, None)
    settings = native.find(directory)
    hints = native.hints(directory, settings, policy.min_age)
    data = {
        "min_age": spoken(policy.min_age),
        "min_age_for": {key: spoken(age) for key, age in policy.min_age_for.items()},
        "unknown": policy.unknown,
        "exclude": policy.exclude,
        "native": [
            {"tool": s.tool, "file": s.file, "setting": s.name, "value": s.raw, "age": spoken(s.age) if s.age else None}
            for s in settings
        ],
        "could_add": [{"tool": h.tool, "file": h.file, "lines": h.line} for h in hints],
    }
    lines = [f"unripe minimum age: {data['min_age']}."]
    lines += [f"{s['tool']} {s['file']}: {s['setting']} = {s['value']}" for s in data["native"]]
    lines += [f"could add to {h['file']} for {h['tool']}: {h['lines']}" for h in data["could_add"]]
    return "\n".join(lines), data


HANDLERS = {"check_package": check_package, "check_lockfiles": check_lockfiles, "cooldown_settings": cooldown_settings}


class Server:
    def __init__(self) -> None:
        # set once a client opens with `initialize`: it is served by the older protocol from then on
        self.legacy: str | None = None

    def handle(self, message: Any) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return error(None, -32600, "not a JSON-RPC 2.0 message")
        if "method" not in message:
            return None  # a response to something we never ask
        ident, method = message.get("id"), message["method"]
        params = message.get("params") or {}
        if "id" not in message:
            return None  # notifications need no answer
        try:
            if not isinstance(params, dict):
                raise Refusal(-32602, "params must be an object")
            modern = self.version(method, params)
            result = self.dispatch(method, params)
            if modern:
                result = {"resultType": "complete", **result}
            return {"jsonrpc": "2.0", "id": ident, "result": result}
        except Refusal as refusal:
            return error(ident, refusal.code, refusal.message, refusal.data)
        except Exception as problem:  # a bug should cost one answer, not the whole session
            return error(ident, -32603, f"unripe failed: {problem}")

    def version(self, method: str, params: dict) -> bool:
        """Whether this request uses the stateless protocol. Refuses versions this server does not speak."""
        if method == "initialize":
            return False
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        requested = meta.get(VERSION_KEY)
        if requested is None:
            return False  # an initialized older client, or one that skipped the handshake: served the older way
        if requested != MODERN:
            raise Refusal(-32022, "Unsupported protocol version", {"supported": [MODERN], "requested": requested})
        return True

    def dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            requested = params.get("protocolVersion")
            self.legacy = requested if requested in LEGACY else LEGACY[0]
            return {"protocolVersion": self.legacy, "capabilities": {"tools": {}}, "serverInfo": SERVER, "instructions": INSTRUCTIONS}
        if method == "server/discover":
            return {
                "supportedVersions": [MODERN],
                "capabilities": {"tools": {}},
                "_meta": {"io.modelcontextprotocol/serverInfo": SERVER},
                "instructions": INSTRUCTIONS,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            name = params.get("name")
            if name not in HANDLERS:
                raise Refusal(-32602, f"Unknown tool: {name}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise Refusal(-32602, "arguments must be an object")
            try:
                text, data = HANDLERS[name](arguments)
            except (Wrong, PolicyError) as wrong:
                return {"content": [{"type": "text", "text": str(wrong)}], "isError": True}
            return {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False}
        raise Refusal(-32601, f"Method not found: {method}")


def error(ident: Any, code: int, message: str, data: Any = None) -> dict:
    body: dict = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": ident, "error": body}


def serve(stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> int:
    """Read one JSON-RPC message per line until stdin closes. Nothing but MCP messages goes to stdout."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    server = Server()
    sys.stderr.write(f"unripe {__version__} MCP server ready on stdio\n")
    for raw in stdin:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            answer: dict | None = error(None, -32700, "parse error")
        else:
            if isinstance(message, list):
                answers = [a for a in (server.handle(m) for m in message) if a]
                answer = answers or None  # type: ignore[assignment]
            else:
                answer = server.handle(message)
        if answer:
            stdout.write(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            stdout.flush()
    return 0
