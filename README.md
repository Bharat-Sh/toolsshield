# AgentShield

**The safety layer between AI agents and the real world.**

AgentShield intercepts every tool call your AI agent makes and provides rollback, cost control, rate limiting, and testing — so agents can work autonomously without causing damage.

**Zero mandatory dependencies. One `pip install`. Works with every major agent framework.**

## Why AgentShield?

AI agents are powerful but dangerous. They can delete databases, overspend budgets, and send wrong emails — all in seconds. AgentShield solves this:

| Problem | AgentShield Solution |
|---------|---------------------|
| Agent runs `DROP TABLE` | Auto-blocked by risk classification |
| Agent overspends API budget | Budget limits with per-action cost tracking |
| Want to test without side effects | Test mode simulates everything |
| Agent made a mistake | One-call rollback via state snapshots |
| Agent spams an API | Rate limiting (RPM/RPH per tool) |
| Need audit trail | SQLite logging + Prometheus metrics |
| Multiple agents, different rules | AgentPool with isolated budgets per agent |
| Sensitive data in logs | Automatic PII/secret masking |

## Install

```bash
pip install agentshield
```

With framework integrations:

```bash
pip install agentshield[openai]      # OpenAI Agents SDK
pip install agentshield[anthropic]   # Anthropic Claude SDK
pip install agentshield[langchain]   # LangChain
pip install agentshield[crewai]      # CrewAI
pip install agentshield[autogen]     # Microsoft AutoGen
pip install agentshield[yaml]        # YAML policy files
pip install agentshield[all]         # Everything
```

## Quick Start

```python
from agentshield import AgentShield

shield = AgentShield(budget=5.00, auto_snapshot=True)

@shield.tool(cost=0.03)
def query_db(sql: str) -> str:
    return db.execute(sql)

# Normal execution
result = query_db(sql="SELECT * FROM users")
print(result.output)  # Works fine

# Destructive SQL is auto-blocked
result = query_db(sql="DROP TABLE users")
print(result.blocked)       # True
print(result.block_reason)  # "CRITICAL risk blocked: Destructive SQL detected"

# Undo the last action
shield.rollback()
```

## Features

### Policy-as-Code

Define rules in JSON or YAML files:

```json
{
  "budget": 25.00,
  "mode": "production",
  "global_rpm": 120,
  "tool_limits": {"send_email": {"rpm": 10, "rph": 100}},
  "rules": [
    {
      "name": "no_prod_access",
      "match": {"args_regex": "production|prod-db"},
      "risk": "critical",
      "reason": "Production database access blocked"
    }
  ],
  "webhooks": [
    {"url": "https://hooks.slack.com/...", "events": ["action_blocked"]}
  ]
}
```

```python
from agentshield import load_policy
shield = load_policy("policy.json")
```

### Multi-Agent Support

```python
from agentshield import AgentPool

pool = AgentPool(default_budget=5.00)
researcher = pool.register("researcher", budget=3.00)
coder = pool.register("coder", budget=10.00)
reviewer = pool.register("reviewer", budget=1.00, mode="test")

# Each agent has isolated budget, rules, and audit trail
pool.stats()        # Aggregated view across all agents
pool.over_budget()  # ["researcher"] if over budget
```

### Rate Limiting

```python
from agentshield import AgentShield, ToolLimit

shield = AgentShield(
    budget=10.00,
    global_rpm=60,                                    # 60 calls/min globally
    tool_limits={"send_email": ToolLimit(rpm=5)},     # 5 emails/min
)
```

### Retry with Backoff

```python
from agentshield import AgentShield, RetryPolicy

policy = RetryPolicy(max_retries=3, backoff_base=1.0, jitter=0.5)

@shield.tool(cost=0.05, retry=policy)
def flaky_api(query: str) -> str:
    return requests.get(f"https://api.example.com?q={query}").text
```

### Sensitive Data Masking

```python
from agentshield import AgentShield, DataMasker

masker = DataMasker()
shield = AgentShield(budget=10.0, db_path="audit.db")
shield.set_masker(masker)

# Emails, API keys, credit cards, SSNs, JWTs are auto-masked in logs
# Custom patterns:
masker.add_pattern("project_id", r"PROJ-\d{6}", "PROJ-******")
```

### Webhook Notifications

```python
shield.notifier.add_webhook(
    url="https://hooks.slack.com/services/xxx",
    events={"action_blocked", "budget_exceeded"},
)

shield.notifier.on("action_blocked", lambda event, data: print(f"Alert: {data}"))
```

### Async Support

```python
from agentshield import AsyncAgentShield

shield = AsyncAgentShield(budget=5.00)

@shield.async_tool(cost=0.05)
async def async_query(sql: str) -> str:
    return await db.execute(sql)
```

### Action Replay (Debug/Audit)

```python
from agentshield.replay import ActionReplayer

replayer = ActionReplayer(db_path="audit.db", executors={"run_sql": my_fn})
report = replayer.replay_all(shield)
print(f"Match rate: {report.success_rate:.0%}")
```

## Framework Integrations

### OpenAI Agents SDK

```python
from agentshield.integrations.openai_agents import OpenAIShieldMiddleware

middleware = OpenAIShieldMiddleware(shield=shield, executors={...}, cost_map={...})
result = middleware.handle_tool_call(tool_call)
```

### Anthropic Claude SDK

```python
from agentshield.integrations.anthropic_sdk import AnthropicShieldMiddleware

middleware = AnthropicShieldMiddleware(shield=shield, executors={...})
result = middleware.handle_tool_use(tool_use_block)
tool_result = middleware.format_tool_result(result)  # Ready for Messages API
```

### LangChain

```python
from agentshield.integrations.langchain import shield_langchain_tools
shielded = shield_langchain_tools(shield, existing_tools)
```

### CrewAI

```python
from agentshield.integrations.crewai import shield_crewai_tools
shielded = shield_crewai_tools(shield, crew_tools, cost_map={"search": 0.05})
```

### Microsoft AutoGen

```python
from agentshield.integrations.autogen import shield_autogen_functions
wrapped = shield_autogen_functions(shield, {"search": search_fn}, cost_map={...})
```

## Monitoring

### Web Dashboard

```bash
agentshield --db audit.db dashboard --port 8080
```

Real-time web UI showing actions, blocks, budget, and rate limits.

### Prometheus Metrics

```python
from agentshield.metrics import start_metrics_server
start_metrics_server(shield, port=9090)
# Scrape http://localhost:9090/metrics
```

Exports: `agentshield_actions_total`, `agentshield_actions_blocked_total`, `agentshield_budget_spent_dollars`, per-tool and per-agent labels.

## CLI

```bash
agentshield stats                    # Action statistics
agentshield logs                     # View action log
agentshield logs --blocked           # Only blocked actions
agentshield export audit.json        # Export to JSON
agentshield validate policy.json     # Validate a policy file
agentshield dashboard --port 8080    # Start web dashboard
```

## Risk Levels

| Level | Behavior | Examples |
|-------|----------|----------|
| LOW | Execute normally | SELECT queries, file reads |
| MEDIUM | Execute with logging | Outbound messages, moderate operations |
| HIGH | Ask user for approval | Bulk operations (>100 items) |
| CRITICAL | Auto-block | DROP TABLE, TRUNCATE, DELETE FROM |

## Architecture

```
Agent → AgentShield → Tool
         ├── Risk Classification (rules engine)
         ├── Budget Check
         ├── Rate Limit Check
         ├── Snapshot (for rollback)
         ├── Data Masking (for logs)
         ├── Execute (or simulate in test mode)
         ├── Retry (on failure, with backoff)
         ├── Log to SQLite
         ├── Emit webhook/callback events
         └── Prometheus metrics
```

## License

MIT
