<p align="center">
  <img src="docs/sawyer-icon.ico" width="128" height="128" alt="Sawyer Agent">
</p>

<h1 align="center">Sawyer Agent</h1>

<p align="center">
  Secure, model-agnostic, self-hosted AI agent framework
</p>

<p align="center">
  <a href="#install"><strong>Install &rarr;</strong></a>
  &nbsp;&middot;&nbsp;
  <a href="#quick-start"><strong>Quick Start &rarr;</strong></a>
  &nbsp;&middot;&nbsp;
  <a href="#tools"><strong>19 Built-in Tools &rarr;</strong></a>
</p>

---

<p align="center">
  <img src="docs/sawyer-screenshot.png" alt="Sawyer Agent web UI" width="768">
</p>

Sawyer is a standalone AI agent that runs on your machine with no telemetry, no phone-home, and no data leaving your network. It ships with 19 tools, 5 built-in skills, and a ClawHub importer -- connect any OpenAI-compatible LLM and go.

**Principles:** Secure by default. Model-agnostic. Self-hosted. Observable. Self-improving.

## Install

One line:

```bash
pip install git+https://github.com/drc10101/sawyer-harness.git
```

Or clone and install editable:

```bash
git clone https://github.com/drc10101/sawyer-harness.git
cd sawyer-harness
pip install -e .
```

## Quick Start

```bash
# Copy the example config and add your API key
cp config.example.yaml config.yaml

# Start the web UI
sawyer-web --config config.yaml --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765 in your browser.

## Configuration

```yaml
# config.yaml
llm:
  provider: ollama           # ollama, openai, anthropic
  model: glm-5.1:cloud
  api_key: YOUR_KEY_HERE
  base_url: https://ollama.com/v1

server:
  host: 127.0.0.1
  port: 8765
```

## Tools

All major tools and files are accessible right from the GUI -- no CLI required. The sidebar gives you full control over every tool, skill, and project file without leaving the browser.

| Tool | What It Does |
|------|--------------|
| `shell` | Execute shell commands -- builds, installs, git, processes |
| `file_read` | Read files with line numbers and pagination |
| `file_write` | Create or overwrite files entirely |
| `file_search` | Find files by name or search inside file contents |
| `web_search` | Search the web via DuckDuckGo (no API key needed) |
| `web_fetch` | Fetch and extract readable text from any URL |
| `code_execute` | Run Python code in a sandboxed subprocess |
| `memory_search` | Search persistent memory for stored facts and preferences |
| `memory_store` | Save facts that survive across sessions |
| `memory_delete` | Remove memory entries you no longer need |
| `skill_search` | Search available skills by keyword |
| `skill_load` | Load a skill's full instructions into context |
| `skill_list` | List all installed skills with descriptions |
| `git` | Git operations: status, diff, log, commit, branch, push, pull, stash |
| `patch` | Surgical find/replace edits in files (no full rewrites) |
| `http_request` | REST API calls: GET, POST, PUT, DELETE with headers and body |
| `clipboard` | Copy text to the system clipboard for sharing |
| `project_create` | Scaffold new projects from built-in templates |
| `clawhub_import` | Import skills from ClawHub.ai or GitHub (68K+ available) |

## Skills

5 built-in skills plus ClawHub import:

| Skill | Description |
|-------|-------------|
| `code-review` | Systematic review with quality gates and security checks |
| `debugging` | 4-phase root cause analysis |
| `tdd` | Red-green-refactor test-driven development |
| `writing-plans` | Actionable implementation plans with bite-sized tasks |
| `git-workflow` | Branching, committing, PR patterns, emergency recovery |

Import any of ClawHub's 68,000+ skills:

```
You: Import the handoff skill from ClawHub
Sawyer: [clawhub_import] Imported 'handoff' -- use skill_load('handoff') to activate it.
```

## Architecture

```
Channel Layer (Telegram, Discord, CLI, Web UI)
        |
   Router/Dispatcher (auth, sessions, rate limit)
        |
    Agent Core
    |-- Memory (SQLite)
    |-- Skills (YAML+Markdown)
    |-- Scheduler (APScheduler)
    |-- Tool Registry (19 tools, sandboxed)
    |-- LLM Client (OpenAI-compatible)
    |-- ClawHub Importer
    +-- Context Manager (token tracking, compression)
```

## License

MIT