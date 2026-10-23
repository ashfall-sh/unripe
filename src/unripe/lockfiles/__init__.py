"""Finding lockfiles, and reading each one with the parser its name calls for."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from unripe.lockfiles import actions, apps, dotnet, javascript, jvm, others, python
from unripe.model import Package

Parser = Callable[[str, str], list[Package]]

PARSERS: dict[str, Parser] = {
    "package-lock.json": javascript.packagelock,
    "npm-shrinkwrap.json": javascript.packagelock,
    "pnpm-lock.yaml": javascript.pnpm,
    "yarn.lock": javascript.yarn,
    "bun.lock": javascript.bun,
    "bun.lockb": javascript.bunbinary,
    "deno.lock": javascript.deno,
    "uv.lock": python.uv,
    "poetry.lock": python.poetry,
    "Pipfile.lock": python.pipfile,
    "Cargo.lock": others.cargo,
    "go.mod": others.gomod,
    "composer.lock": others.composer,
    "Gemfile.lock": others.gemfile,
    "packages.lock.json": dotnet.nuget,
    "gradle.lockfile": jvm.gradle,
    "buildscript-gradle.lockfile": jvm.gradle,
    "settings-gradle.lockfile": jvm.gradle,
    "pubspec.lock": apps.pubspec,
    "Podfile.lock": apps.podfile,
    "Package.resolved": apps.swiftresolved,
    ".terraform.lock.hcl": apps.terraform,
    "conan.lock": others.conan,
    "renv.lock": others.renv,
}
PYLOCK = re.compile(r"^pylock\.([^.]+\.)?toml$")
REQUIREMENTS = re.compile(r"^(requirements|constraints)[\w.-]*\.txt$", re.IGNORECASE)
# never worth walking into: installed copies of dependencies, builds, caches, and go's test inputs
SKIP = {"node_modules", ".git", ".hg", ".venv", "venv", "env", "vendor", "target", "dist", "build", "__pycache__", ".tox", ".nox", ".cache", "testdata", "bin", "obj", ".gradle", ".terraform", "Pods", ".build", ".dart_tool", ".swiftpm"}


class Unreadable(Exception):
    """A lockfile that exists but could not be parsed."""


def workflowfile(path: Path) -> bool:
    """A workflow in .github/workflows, or a composite action's action.yml."""
    if path.suffix not in (".yml", ".yaml"):
        return False
    return (path.parent.name == "workflows" and path.parent.parent.name == ".github") or path.stem == "action"


def parser(path: str | Path) -> Parser | None:
    path = Path(path)
    if workflowfile(path):
        return actions.workflow
    if path.name in PARSERS:
        return PARSERS[path.name]
    if REQUIREMENTS.match(path.name):
        return python.requirements
    if PYLOCK.match(path.name):
        return python.pylock
    # gradle before 6 wrote one lockfile per configuration
    if path.suffix == ".lockfile" and path.parent.name == "dependency-locks":
        return jvm.gradle
    return None


def find(paths: list[str], workflows: bool = False) -> list[Path]:
    """
    Every lockfile in the given files and directories, directories walked
    recursively. GitHub Actions workflows count too when `workflows` is set.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        # the same file named twice, or inside two given directories, is read once
        real = path.resolve()
        if real not in seen:
            seen.add(real)
            found.append(path)

    for raw in paths or ["."]:
        path = Path(raw)
        if path.is_file():
            if parser(path) and (workflows or not workflowfile(path)):
                add(path)
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            for name in sorted(files):
                # bun 1.2 leaves the old binary lockfile next to the new text one
                if name == "bun.lockb" and "bun.lock" in files:
                    continue
                if parser(Path(root) / name) and (workflows or not workflowfile(Path(root) / name)):
                    add(Path(root) / name)
    return found


def read(text: str, path: str | Path) -> list[Package]:
    """The packages locked in one file's text. `path` decides the parser and is recorded on each package."""
    parse = parser(path)
    if parse is None:
        raise Unreadable(f"{path}: not a lockfile unripe knows")
    try:
        return parse(text, str(path).replace("\\", "/"))
    except Exception as error:  # a broken lockfile is reported, never mistaken for an empty one
        raise Unreadable(f"{path}: could not be read ({error})") from error
