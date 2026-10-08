# AgentShield

Agent operations platform: **rollback**, **cost control**, and **testing** for AI agents.

> Let your agent work — but safely. Undo mistakes, stay on budget, test before production.

## Features

- **Rollback** — Snapshot every action, undo with one call
- **Cost Control** — Set budgets, track spending, stop before overspend
- **Test Mode** — Simulate agent actions without touching the real world
- **Action Log** — Full audit trail of every agent action

## Quick Start

```python
from agentshield import AgentShield

shield = AgentShield(budget=5.00, auto_snapshot=True)

# Wrap any tool/function
@shield.tool
def delete_rows(query: str) -> str:
    # AgentShield intercepts this automatically
    ...

# After a mistake
shield.rollback()

# Review what happened
shield.history()
```

## Installation

```bash
pip install agentshield
```

## License

MIT
