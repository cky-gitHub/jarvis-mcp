# jarvis-mcp

An MCP server that gives Claude full control of a Windows desktop — open apps, type text, read the clipboard, and control Spotify, all from natural-language prompts in Claude Desktop.

> Status: early development. MVP in progress.

## What it is

`jarvis-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server that exposes a small set of desktop-control tools to Claude. The goal is the Tony Stark experience: you talk, your computer responds.

Current tools (in progress):
- `open_app(name)` — open any installed application
- `type_text(text)` — type into the focused window
- `read_clipboard()` — read current clipboard contents
- `control_music(action, query)` — control Spotify (play, pause, skip, search)

## Why it's interesting (research direction)

Most MCP servers today treat each tool call as stateless. The LLM has to reconstruct world state from text on every turn, which wastes tool calls on re-discovery — "what app is focused?", "what's playing?", "what window am I in?".

The next phase of this project explores a `world_state` MCP **resource** that auto-updates on every tool call, giving the LLM a live world model to read from instead of reasoning from scratch. The hypothesis: this cuts tool calls per task by ~30% on representative desktop workflows. Benchmarks and a write-up are coming.

## Install

Requires Python 3.12+ and Windows.

```bash
git clone https://github.com/cky-gitHub/jarvis-mcp
cd jarvis-mcp
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## Run with Claude Desktop

Add the following to your Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json`):

See `claude_desktop_config.example.json` for the exact snippet.

Restart Claude Desktop. The Jarvis tools should appear in the tools menu.

## What's next

- [ ] All four MVP tools working end-to-end
- [ ] `world_state` resource — live world model exposed to the LLM
- [ ] Benchmarks: tool-calls-per-task on 10 representative workflows, baseline vs. world_state
- [ ] Blog post on the findings

## License

MIT. See `LICENSE`.