<p align="center">
  <img src="docs/Sawyer_Agent_Github_Readme_Logo.png" width="480" alt="Sawyer Agent">
</p>

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

Sawyer is a standalone AI agent that runs on your machine with no telemetry, no phone-home, and no data leaving your network. It ships with 19 tools, real token counting, sub-agent templates, goal-seeking loops, streaming chat, seizure detection, and a one-click tool creator -- connect any OpenAI-compatible LLM and go.

**Principles:** Secure by default. Model-agnostic. Self-hosted. Observable. Self-improving.

## What's New in v0.8

**CompactionPolicy** -- context compression is now configurable. Three presets (conservative, balanced, aggressive) control when to compact, what to keep, and how aggressive the compression is. Override individual thresholds in config.yaml or change at runtime from Settings. Balanced (the default) matches the old 80% behavior for full backward compatibility.

**PermissionMode** -- tools are now classified by capability (Read, Write, Shell, Network, Memory, Skill, System) and grouped into three permission levels. ReadOnly agents can read files and search but cannot write, execute, or make network calls. ReadWrite adds file editing, shell commands, and git. All gives unrestricted access. Change permission mode at runtime from Settings -- the tool registry rebuilds instantly.

**ToolBridge + kill_foreground** -- shell and code_execute handlers now track their subprocess via Popen, and a new `/api/kill-foreground` endpoint lets you cancel a running command mid-execution. The Stop button in the UI calls this directly. SIGTERM first, SIGKILL after 2 seconds if still alive.

**Agent Discovery** -- define agents as `.md` files with YAML frontmatter. Drop them in `.sawyer/agents/` inside any project for project-specific agents, or `~/.sawyer-harness/user/agents/` for global ones. Project-level agents shadow global ones by name. Frontmatter supports name, description, model, permission_mode, tools, skills, and rules. The body becomes the system prompt. API endpoints: `GET /api/discovered-agents`, `GET /api/discovered-agents/{name}`, `POST /api/discovered-agents/reload`.

**Hybrid Memory** -- memory search now uses FTS5 BM25 ranking instead of simple LIKE queries, with automatic index sync via triggers. Falls back gracefully if FTS5 is unavailable. sqlite-vec stub is in place for future KNN vector similarity (384-dim embeddings) when the extension is available.

## What's New in v0.7.4

**Streaming chat with WebSocket** -- responses stream in real time instead of waiting for the entire reply. See tool calls as they execute, read the agent's thinking summary (not raw reasoning), and watch results arrive live. No more staring at a blank screen while the agent works.

**Seizure detection** -- if the agent calls the same tool with the same arguments 3+ times in a row, it stops automatically with a clear warning. No infinite loops. No wasted tokens. The agent recognizes when it's stuck and tells you why.

**Stop button** -- a Stop button appears during execution. Click it to cancel mid-stream. No more waiting for a runaway agent to finish.

**One-click Tool Creator** -- describe what you want a tool to do, click Create Tool, and it's done. Sawyer generates the Python code, writes it to `~/.sawyer-harness/tools/`, and registers it immediately. No Python knowledge required. Custom tools survive upgrades because they live outside the pip package.

**User tools directory** -- `~/.sawyer-harness/tools/` is where custom tools live. Drop a `.py` file with a `register()` function and it loads on startup. Hit "Reload User Tools" in the Tools panel to pick up new ones without restarting. Everything in `~/.sawyer-harness/` (tools, skills, memory, keys, config, cron) survives `pip install --upgrade`.

**Skills panel** -- renamed from "Skill Creator" to "Skills". The Browse tab now explains that these are custom instruction sets injected into the agent's context, not built-in tools (those are in the Tools panel).

## What's New in v0.7.3

**Real token counting** -- every context meter, compression decision, and pressure threshold now uses actual tiktoken BPE tokenization instead of the old `len(text)//4` heuristic. Chinese text was off by 400%. Repetitive text by 87%. Now you get real numbers you can count on.

**Goal-seeking loops** -- the Goals panel now runs goal-seeking loops with hard stops. Create a goal, decompose it into subtasks, then start a loop that works through each subtask automatically. Each loop has a configurable max-iteration ceiling so it can never run forever. If a loop stalls, the Diagnose button tells you why.

**Main Model & Agent Mode settings** -- the Settings panel lets you pick your LLM provider and model, and choose between three agent modes:

- **Direct** -- the agent answers you directly using its own tools. Fast, cheap, good for 90% of conversations.
- **Orchestrator** -- decomposes goals into subtasks, delegates to specialized sub-agents, evaluates results. Slower but more capable for complex multi-step work.
- **Auto** -- starts direct, escalates to orchestration when it hits something too big.

**Sub-Agents panel** -- create specialized agent templates with their own system prompt, personality, rules, and model settings. Spawn a session from any template to get a focused agent ready for a specific task.

**Orchestrate panel** -- coordinate multiple sub-agents in structured workflows. Decompose goals, assign tasks to workers, evaluate results, and drive improvement loops.

**Rules Engine** -- priority-ranked behavioral constraints (P0-P3) that supersede defaults. Define what the agent must always do, should do, may do, or must never do.

**Keys & Secrets** -- encrypted credential storage with permission levels. Store SSH keys, API tokens, and service credentials without leaking them into chat context.

**Cron Scheduler** -- schedule recurring or one-shot agent jobs. Runs persist in SQLite so they survive restarts.

**Better skill tools** -- `skill_load` and `skill_list` now reload from disk at runtime and give clear guidance when no skills are installed. `clawhub_import` catches network errors and says "ClawHub is not reachable" instead of dumping a Python traceback.

## Install

**One-click (Windows):**

```powershell
irm https://raw.githubusercontent.com/drc10101/sawyer-agent/master/install-sawyer.bat -OutFile install-sawyer.bat; .\install-sawyer.bat
```

Or download `install-sawyer.bat` and double-click it. Creates a desktop shortcut with the Sawyer icon.

**Manual (any platform):**

```bash
pip install git+https://github.com/drc10101/sawyer-agent.git
```

Requires Python 3.11 or later.

## Quick Start

After installing, run:

```bash
python -m sawyer_harness
```

The server starts and **automatically opens your browser** once it's ready -- no more racing to a dead page.

On first run, Sawyer prompts you to configure your AI provider. Mistyped your key? Reconfigure anytime:

```bash
python -m sawyer_harness setup
```

All commands:

| Command | What it does |
|---------|--------------|
| `python -m sawyer_harness` | Start the web server |
| `python -m sawyer_harness web` | Start the web server |
| `python -m sawyer_harness setup` | Configure or reconfigure API key and provider |
| `python -m sawyer_harness uninstall` | Remove Sawyer completely (data, config, package) |
| `python -m sawyer_harness version` | Show version |
| `sawyer-web` | Start the web server (short form, if on PATH) |

**Installer commands** (Windows, via `install-sawyer.bat`):

| Command | What it does |
|---------|--------------|
| `install-sawyer.bat` | Full install: pip install + setup + desktop shortcut |
| `install-sawyer.bat reinstall` | Update package and reconfigure |
| `install-sawyer.bat setup` | Reconfigure API key and provider |
| `install-sawyer.bat uninstall` | Remove Sawyer completely |
| `install-sawyer.bat start` | Start the server |

On first run, Sawyer prompts you interactively:

```
=== Sawyer Agent Setup ===

First run detected -- let's configure your AI provider.

  1. Ollama (cloud or local)
  2. OpenAI (GPT-4o, GPT-4.1, etc.)
  3. Anthropic (Claude)
  4. Custom OpenAI-compatible endpoint

Provider [1]: 1
Model [glm-5.1:cloud]:
Base URL [https://ollama.com/v1]:
API Key: sk-...

Config saved to ~/.sawyer-harness/config.yaml
```

The setup wizard also offers to create a desktop shortcut with the Sawyer icon. Double-clicking it starts the server and opens your browser when ready.

## Uninstall

```bash
python -m sawyer_harness uninstall
```

This stops any running server, deletes `~/.sawyer-harness/` (config, memory, skills, keys), removes the desktop shortcut, and uninstalls the pip package. On Windows it handles the locked-exe problem automatically.

## Configuration

```yaml
# ~/.sawyer-harness/config.yaml
llm:
  provider: ollama           # ollama, openai, anthropic
  model: glm-5.1:cloud
  api_key: YOUR_KEY_HERE
  base_url: https://ollama.com/v1
  max_tokens: 4096
  temperature: 0.7
agent:
  max_tool_rounds: 20        # Safety ceiling for tool-call loops
  verbosity: normal           # concise | normal | thorough
  stream_tool_output: true    # Show tool results in chat
  mode: direct                # direct | orchestrator | auto
security:
  sandbox: true
  max_command_timeout: 300
memory:
  backend: sqlite
  path: ~/.sawyer-harness/memory.db
```

You can also change the model, provider, and agent mode at runtime from the Settings panel in the web UI. Changes take effect on the next message.

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
| `clawhub_import` | Import skills from ClawHub.ai or GitHub |

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

## Sub-Agents

The Sub-Agents panel lets you create specialized agent templates. Each sub-agent has its own:

- **System prompt** -- personality, domain expertise, behavioral rules
- **Model settings** -- different LLM for different tasks
- **Soul** -- identity, strengths, personality traits, quirks
- **Rules** -- custom behavior rules that supersede defaults

Create a template, then spawn a session from it to get a focused agent. In orchestrator mode, the main agent delegates subtasks to these sub-agents automatically.

## User Tools

Custom tools live in `~/.sawyer-harness/tools/` and survive upgrades. Two ways to create one:

1. **One-click Tool Creator** -- open the Tools panel, describe what you want, click Create Tool. Sawyer generates the Python code, writes the file, and registers it immediately. No Python knowledge required.
2. **Manual** -- drop a `.py` file in `~/.sawyer-harness/tools/` that defines a `register(registry)` function. Hit "Reload User Tools" to pick it up without restarting.

All user data in `~/.sawyer-harness/` (tools, skills, memory, keys, config, cron) is preserved across `pip install --upgrade`. Only the Python package code changes -- your data stays.

## Streaming Chat

The chat interface streams responses in real time via WebSocket. Instead of waiting for the entire reply, you see:

- **Thinking summary** -- one-line status like "Reading file..." or "Running shell..." (not raw reasoning)
- **Tool calls** -- `[file_read]`, `[shell]`, `[patch]` as they execute
- **Tool results** -- abbreviated output from each tool
- **Text response** -- the agent's answer, streamed as it arrives
- **Seizure warning** -- red alert if the agent loops on the same tool+args 3+ times
- **Stop button** -- appears during execution, click to cancel

If WebSocket is unavailable, it falls back to the REST endpoint automatically.

## Goals & Goal-Seeking Loops

The Goals panel decomposes goals into subtasks and runs them in an automated loop with hard stops.

1. **Create a goal** -- describe what you want to accomplish
2. **Decompose** -- the agent breaks it into concrete subtasks
3. **Start a loop** -- the agent works through each subtask automatically
4. **Hard stop** -- every loop has a max-iteration ceiling so it can never run forever
5. **Diagnose** -- if a loop stalls, the Diagnose button tells you why
6. **Stop or reset** -- stop a running loop or delete a completed goal

Goal-seeking loops persist across server restarts. Status badges show whether a goal is pending, decomposed, looping, or complete.

## Orchestrate

The Orchestrate panel coordinates multiple sub-agents in structured workflows. Decompose a goal, assign tasks to workers with specific agent types, evaluate results, and drive improvement loops. Each orchestration run is persisted and can be inspected in detail.

## Context Window

Sawyer uses real tiktoken BPE tokenization to track context usage -- not the `len(text)//4` heuristic that most agents rely on. This means:

- Context pressure detection is accurate (not off by 400% on Chinese text)
- Compression triggers at the right time
- Budget allocation reflects actual token costs
- API-reported token counts feed back as ground truth

The context meter in the header shows real numbers. When context gets tight, Sawyer compresses older messages while preserving decisions and corrections.

## Architecture

```
Web UI (FastAPI + static HTML/CSS/JS at localhost:8765)
   15 panels (Chat, Goals, Skills, Tools, Files, Models, Sessions,
               Projects, Cron, Memory, Keys, Rules, Sub-Agents, Orchestrate, Settings)
        |
   Channel Layer (Telegram, Discord, CLI, API)
        |
   Router/Dispatcher (ModelRouter: Sawyer priority, health, fallback)
        |
   WebSocket Layer (streaming events: thinking, tool_call, tool_result, text, seizure, done)
        |
    Agent Core
    |-- Memory (SQLite)
    |-- Skills (YAML+Markdown, find_relevant, self-patch)
    |-- Scheduler (APScheduler: interval/cron/one-shot, SQLite persist)
    |-- Tool Registry (19 built-in + N user tools, sandboxed, audit logged)
    |-- User Tools (~/.sawyer-harness/tools/, auto-loaded, survive upgrades)
    |-- Tool Creator (describe what you want, generates .py, one-click)
    |-- Goal Engine (decompose, loop with hard stops, diagnose stalls)
    |-- Seizure Detection (same tool+args 3x = auto-stop with warning)
    |-- Orchestrator (goal decomposition, dependency tracking, session notes)
    |-- Skill Creator (5-phase collaborative skill design)
    |-- Key Storage (encrypted credentials, permission levels)
    |-- Rules Engine (custom rules supersede defaults)
    |-- Agent Creator (8 built-in templates with soul, CRUD, spawn)
    |-- Context Manager (tiktoken BPE token counting, pressure detection)
    |-- Context Compressor (priority-aware, decision-preserving)
    +-- LLM Client (Sawyer/OpenAI/Anthropic/Ollama via httpx)
```

## License

MIT