# AgentShield

**Rollback, cost control, and testing for AI agents.**

AgentShield is the safety layer between your AI agent and the real world. It intercepts every tool call and provides:

- **Rollback** — Undo any agent action with automatic state snapshots
- **Cost Control** — Set budgets and stop agents before they overspend
- **Test Mode** — Simulate actions without touching real systems
- **Risk Classification** — Auto-detect destructive SQL, file deletion, bulk operations
- **Audit Logging** — SQLite-backed persistent logs of every action
- **Zero Dependencies** — Pure Python, no mandatory external packages

## Install

```bash
pip install agentshield
```

With framework integrations:

```bash
pip install agentshield[openai]     # OpenAI Agents SDK
pip install agentshield[langchain]  # LangChain
pip install agentshield[all]        # Everything
```

## Quick Start

```python
from agentshield import AgentShield

shield = AgentShield(budget=5.00, auto_snapshot=True)

@shield.tool(cost=0.03)
def query_db(sql: str) -> str:
    return db.execute(sql)

# Agent calls the tool — AgentShield intercepts
result = query_db(sql="SELECT * FROM users")
print(result.output)  # Normal result

# Destructive SQL is auto-blocked
result = query_db(sql="DROP TABLE users")
print(result.blocked)       # True
print(result.block_reason)  # "CRITICAL risk blocked: Destructive SQL detected"

# Undo the last action
shield.rollback()
```

## Configuration

```python
shield = AgentShield.from_config({
    "budget": 10.00,
    "mode": "production",   # or "test" for simulation
    "db_path": "audit.db",  # SQLite audit log
    "rules": [
        {
            "name": "no_prod_access",
            "match": {"args_regex": r"production|prod-db"},
            "risk": "critical",
            "reason": "Production database access blocked",
        }
    ],
})
```

## Framework Integrations

### OpenAI Agents SDK

```python
from agentshield import AgentShield
from agentshield.integrations.openai_agents import OpenAIShieldMiddleware

shield = AgentShield(budget=5.00)
middleware = OpenAIShieldMiddleware(
    shield=shield,
    executors={"query_db": query_db},
    cost_map={"query_db": 0.03},
)

# In your agent loop:
result = middleware.handle_tool_call(tool_call)
```

### LangChain

```python
from agentshield.integrations.langchain import shield_langchain_tools

shielded_tools = shield_langchain_tools(shield, existing_tools)
# Use shielded_tools in your LangChain agent
```

## CLI

```bash
agentshield --db audit.db stats     # View statistics
agentshield --db audit.db logs      # View action logs
agentshield --db audit.db export out.json  # Export to JSON
```

## Risk Levels

| Level | Behavior | Examples |
|-------|----------|----------|
| LOW | Execute normally | SELECT queries, file reads |
| MEDIUM | Execute with logging | Outbound messages, moderate operations |
| HIGH | Ask user for approval | Bulk operations (>100 items) |
| CRITICAL | Auto-block | DROP TABLE, TRUNCATE, DELETE FROM |

## License

MIT
