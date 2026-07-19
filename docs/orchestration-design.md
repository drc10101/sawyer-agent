# Sawyer Orchestration Architecture

## Philosophy

Every task is an opportunity to improve, not just complete. The orchestrating agent
drives subagents to completion AND evaluates whether the result can be better.
A creative agent identifies improvement opportunities, just like a human would
in a vibe coding session.

## Agent Types

### Orchestrator Agent
- Receives a goal, decomposes into subtasks
- Spawns worker agents via delegation
- Monitors progress, handles failures
- Invokes Creative Agent for evaluation after task completion
- Has Ralph loop: complete → evaluate → improve → repeat
- Cannot execute tasks directly (leaf-only execution)

### Creative Agent (Reviewer/Improver)
- Reviews completed work output
- Identifies improvement opportunities
- Suggests specific, actionable changes
- Prioritizes improvements by impact
- Like Dave in a vibe coding session: "this works, but what if we also..."
- Does NOT implement -- suggests, then workers implement

### Worker Agent (Executor)
- Receives a briefing: goal, rules, permissions, success criteria
- Executes the task
- Reports results back to orchestrator
- Can self-patch skills on success (no human correction = auto-patch)
- Cannot spawn subagents (leaf node)

## The Improvement Loop (Ralph Loop)

```
Goal → Decompose → Delegate → Execute → Evaluate → Improve → Repeat
                                    ↑                              |
                                    └──────── re-delegate ────────┘
```

1. **Decompose**: Orchestrator breaks goal into subtasks
2. **Delegate**: Spawn workers with pre-loaded briefings
3. **Execute**: Workers complete their tasks
4. **Evaluate**: Creative agent reviews output quality
5. **Improve**: If improvements found, spawn new workers or patch skills
6. **Repeat**: Until quality threshold met or no more improvements found

## Agent Briefing (Launch Configuration)

Every agent launches with:
- **Purpose**: What this agent exists to accomplish
- **Rules**: Priority-ranked behavioral constraints (from rules engine)
- **Permissions**: What tools/APIs this agent can access
- **Success Criteria**: How the agent knows it's done
- **Context**: Relevant project state, files, prior work
- **Timeout**: Maximum execution time

No agent asks "what should I do?" -- the briefing provides everything needed.

## Implementation Plan

### Phase 1: Agent Templates (DONE)
- Agent Creator with YAML templates
- Pre-defined purpose, rules, model config per template type

### Phase 2: Orchestration Engine (NEXT)
- Orchestrator template: decomposition logic, delegation, monitoring
- Creative template: review patterns, improvement identification
- Worker template: execution with briefing injection
- Briefing assembly from rules + template + goal context

### Phase 3: The Loop (DONE)
- Post-task evaluation hook in session_engine.py -- after each tool call round, checks if the task is complete and whether quality meets the bar
- Quality scoring: compare output against success criteria with 5 weighted dimensions (completeness, correctness, quality, coverage, efficiency)
- Auto-patch skills on success (no human correction = the skill gets better)
- Improvement suggestions → new creative evaluator tasks spawned automatically
- Quality threshold config: stop the loop when score >= threshold or max iterations reached
- Ralph Loop engine: ralph_loop_step (evaluate → pass/improve/max-out), ralph_loop_status, apply_improvement
- 7 API endpoints: POST /api/orchestrations/{run_id}/ralph/{task_id}, GET /api/orchestrations/{run_id}/ralph, POST /api/orchestrations/{run_id}/apply-improvement/{task_id}, POST /api/quality-score, GET /api/quality-dimensions, GET /api/ralph-defaults
- Web UI: Evaluate button on completed tasks, Ralph Loop panel with status/config view, quality score display with dimension breakdown

### Phase 4: Persistence & Learning
- Task outcomes stored with quality scores
- Pattern recognition across tasks
- Skill evolution over time
- Kanban integration (auto-create improvement tasks)

## Key Principle

Quality projects and improvement actions with every task. An agent that only
completes the task is a cost center. An agent that completes the task AND
makes the system better is a force multiplier.