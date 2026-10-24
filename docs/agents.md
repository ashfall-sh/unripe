# unripe for AI coding agents

Coding agents add dependencies all the time, and they pick versions from
whatever they remember or whatever the registry says is latest. Latest is
exactly the version a cooldown is meant to avoid. `unripe mcp` runs a
[Model Context Protocol](https://modelcontextprotocol.io) server so an agent
can ask before it adds something, and check the lockfile after.

It is the same unripe: same registries, same `.unripe.toml`, same rule that
an unknown age is never treated as fine. It only reads. It never installs,
edits files or sends anything about your project anywhere except the
package names it looks up.

## The tools

| Tool | What it does |
|---|---|
| `check_package` | With a version: when it was published and whether it is old enough, plus an older version that is if it isn't. Without a version: the recent releases and the newest one that is old enough. |
| `check_lockfiles` | Checks every lockfile under a path, optionally only versions added since a git ref. Same result as `unripe check`. |
| `cooldown_settings` | The cooldown settings the package managers and bots in a directory already have, and the ones that could be added. |

Every tool answers with a short sentence for the model and structured data
for the client. A version that is too new comes back with the version to use
instead, so the agent can just use that.

## Setup

Each of these runs `uvx unripe@0.7.0 mcp`. Pinning the version means the
server doesn't change under you; bump it when you upgrade. If you installed
unripe with pipx or pip, use `unripe` with the argument `mcp` instead.

### Claude Code

```bash
claude mcp add unripe -- uvx unripe@0.7.0 mcp
```

Add `--scope project` to write it to `.mcp.json` in the repository, so
everyone working on it gets it.

### Codex

```bash
codex mcp add unripe -- uvx unripe@0.7.0 mcp
```

or in `~/.codex/config.toml`:

```toml
[mcp_servers.unripe]
command = "uvx"
args = ["unripe@0.7.0", "mcp"]
```

### VS Code (Copilot agent mode)

`.vscode/mcp.json`:

```json
{
  "servers": {
    "unripe": {
      "type": "stdio",
      "command": "uvx",
      "args": ["unripe@0.7.0", "mcp"]
    }
  }
}
```

### Cursor, Claude Desktop, Windsurf, Gemini CLI

These all use the same `mcpServers` block:

```json
{
  "mcpServers": {
    "unripe": {
      "command": "uvx",
      "args": ["unripe@0.7.0", "mcp"]
    }
  }
}
```

It goes in `.cursor/mcp.json` (or `~/.cursor/mcp.json`) for Cursor,
`claude_desktop_config.json` for Claude Desktop (Settings, Developer, Edit
Config), `~/.codeium/windsurf/mcp_config.json` for Windsurf, and
`~/.gemini/settings.json` for Gemini CLI.

### ChatGPT

ChatGPT's connectors talk to servers on the internet. unripe runs on your
own machine and reads your own files, so it doesn't fit there. For OpenAI
models, use it through Codex.

## Telling the agent to use it

Agents call tools when the task seems to need them, and "add a date library"
doesn't obviously need a release age check. A line in `AGENTS.md`,
`CLAUDE.md` or your editor's rules file makes it a habit:

```markdown
Before adding or upgrading a dependency, call unripe's check_package without
a version and use the newest version it says is old enough. After changing a
lockfile, call check_lockfiles with since set to the base branch. If a
version's age is unknown, say so instead of using it.
```

## What it looks like

Asked about `left-pad` with no version, the server answers:

```
Use npm left-pad 1.3.0, published 2018-04-09T01:10:45.796000+00:00: the newest release at
least 3d old. Its maintainers have deprecated it; check whether the package has a replacement.
```

with `newest_old_enough`, `recent` (each release with `old_enough`,
`pulled` and `deprecated`) and `min_age` in the structured result.

## Protocol details

The server speaks MCP over stdio, one JSON message per line. It supports the
`2026-07-28` protocol, where every request carries its version and there is
no handshake, and the older `initialize` handshake for versions
`2025-11-25`, `2025-06-18`, `2025-03-26` and `2024-11-05`. Nothing but
protocol messages is written to stdout; the one log line goes to stderr.
