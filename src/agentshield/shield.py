"""AgentShield — the main class that ties together rollback, cost control, and testing."""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from agentshield.action import Action, ActionResult, RiskLevel
from agentshield.budget import BudgetTracker
from agentshield.config import parse_config, rules_from_config
from agentshield.logger import ActionLogger
from agentshield.rules import RuleEngine
from agentshield.snapshot import SnapshotStore


class Mode(Enum):
    PRODUCTION = "production"
    TEST = "test"


OnBlockCallback = Callable[[Action, list[str]], bool]


class AgentShield:
    """Wrap agent tools with rollback, cost control, and testing.

    Usage:
        shield = AgentShield(budget=5.00, auto_snapshot=True)

        @shield.tool(cost=0.03)
        def query_db(sql: str) -> str:
            ...

        # Undo the last action
        shield.rollback()

        # See what happened
        shield.history()
    """

    def __init__(
        self,
        budget: float | None = None,
        auto_snapshot: bool = True,
        mode: str = "production",
        on_block: OnBlockCallback | None = None,
        max_snapshots: int = 1000,
        db_path: str | None = None,
    ) -> None:
        self._mode = Mode(mode)
        self._auto_snapshot = auto_snapshot
        self._on_block = on_block
        self._rules = RuleEngine()
        self._budget = BudgetTracker(budget=budget)
        self._snapshots = SnapshotStore(max_snapshots=max_snapshots)
        self._history: list[ActionResult] = []
        self._tool_states: dict[str, Any] = {}
        self._logger = ActionLogger(db_path) if db_path else None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "AgentShield":
        """Create an AgentShield from a config dict."""
        parsed = parse_config(config)
        instance = cls(
            budget=parsed["budget"],
            auto_snapshot=parsed["auto_snapshot"],
            mode=parsed["mode"],
            max_snapshots=parsed["max_snapshots"],
            db_path=parsed.get("db_path"),
        )
        for rule in parsed["extra_rules"]:
            instance._rules.add_rule(rule)
        return instance

    # -- Properties --

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def is_test_mode(self) -> bool:
        return self._mode == Mode.TEST

    @property
    def budget_summary(self) -> dict:
        return self._budget.summary()

    # -- Tool decorator --

    def tool(
        self,
        fn: Callable | None = None,
        *,
        cost: float = 0.0,
        risk: RiskLevel | None = None,
        snapshot_state: Callable[[], Any] | None = None,
        restore_state: Callable[[Any], None] | None = None,
    ) -> Callable:
        """Decorator that wraps an agent tool with shield protection.

        Args:
            fn: The tool function.
            cost: Estimated cost per call (in dollars).
            risk: Override risk level (otherwise auto-detected by rules).
            snapshot_state: Callable that returns current state for rollback.
            restore_state: Callable that restores state from a snapshot.
        """

        def decorator(func: Callable) -> Callable:
            tool_name = func.__name__

            if snapshot_state and restore_state:
                self._tool_states[tool_name] = {
                    "capture": snapshot_state,
                    "restore": restore_state,
                }

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> ActionResult:
                action = Action(
                    tool_name=tool_name,
                    args=kwargs if kwargs else {"args": list(args)},
                    cost=cost,
                )

                return self._execute(action, func, args, kwargs, cost, risk)

            wrapper._shield_tool = True
            wrapper._tool_name = tool_name
            return wrapper

        if fn is not None:
            return decorator(fn)
        return decorator

    # -- Core execution --

    def _execute(
        self,
        action: Action,
        func: Callable,
        args: tuple,
        kwargs: dict,
        cost: float,
        risk_override: RiskLevel | None,
    ) -> ActionResult:
        detected_risk, reasons = self._rules.evaluate(action)
        action.risk = risk_override or detected_risk

        if action.risk == RiskLevel.CRITICAL:
            result = ActionResult(
                action=action,
                blocked=True,
                block_reason=f"CRITICAL risk blocked: {'; '.join(reasons)}",
            )
            self._history.append(result)
            if self._logger:
                self._logger.log(result)
            return result

        if action.risk == RiskLevel.HIGH and self._on_block:
            allowed = self._on_block(action, reasons)
            if not allowed:
                result = ActionResult(
                    action=action,
                    blocked=True,
                    block_reason=f"Blocked by user: {'; '.join(reasons)}",
                )
                self._history.append(result)
                if self._logger:
                    self._logger.log(result)
                return result

        if self._budget.would_exceed(cost):
            result = ActionResult(
                action=action,
                blocked=True,
                block_reason=f"Budget exceeded (spent: ${self._budget.spent:.2f}, "
                f"budget: ${self._budget.budget:.2f}, action cost: ${cost:.2f})",
            )
            self._history.append(result)
            if self._logger:
                self._logger.log(result)
            return result

        snapshot_id = None
        if self._auto_snapshot and action.tool_name in self._tool_states:
            state = self._tool_states[action.tool_name]["capture"]()
            snap = self._snapshots.capture(action.id, action.tool_name, state)
            snapshot_id = snap.id

        if self.is_test_mode:
            result = ActionResult(
                action=action,
                output=f"[TEST MODE] Would execute: {action.description}",
                snapshot_id=snapshot_id,
                cost=cost,
            )
            self._budget.record(action.id, action.tool_name, cost)
            self._history.append(result)
            if self._logger:
                self._logger.log(result)
            return result

        try:
            output = func(*args, **kwargs)
            result = ActionResult(
                action=action,
                output=output,
                snapshot_id=snapshot_id,
                cost=cost,
            )
        except Exception as e:
            result = ActionResult(
                action=action,
                error=str(e),
                snapshot_id=snapshot_id,
                cost=cost,
            )

        self._budget.record(action.id, action.tool_name, cost)
        self._history.append(result)
        if self._logger:
            self._logger.log(result)
        return result

    # -- Rollback --

    def rollback(self, action_id: str | None = None) -> bool:
        """Rollback to the snapshot before the given action (or the most recent one)."""
        if action_id:
            snap = self._snapshots.get_by_action(action_id)
        else:
            snap = self._snapshots.get_latest()

        if snap is None:
            return False

        tool_name = snap.tool_name
        if tool_name not in self._tool_states:
            return False

        restore_fn = self._tool_states[tool_name]["restore"]
        restore_fn(snap.state)
        snap.restored = True
        return True

    # -- History --

    def history(self) -> list[dict]:
        """Return a summary of all actions taken."""
        results = []
        for r in self._history:
            results.append({
                "id": r.action.id,
                "tool": r.action.tool_name,
                "risk": r.action.risk.value,
                "blocked": r.blocked,
                "block_reason": r.block_reason,
                "cost": r.cost,
                "success": r.success,
                "has_snapshot": r.snapshot_id is not None,
                "timestamp": r.action.timestamp,
            })
        return results

    def last_result(self) -> ActionResult | None:
        return self._history[-1] if self._history else None

    # -- Stats --

    def stats(self) -> dict:
        total = len(self._history)
        blocked = sum(1 for r in self._history if r.blocked)
        errors = sum(1 for r in self._history if r.error)
        return {
            "total_actions": total,
            "blocked": blocked,
            "errors": errors,
            "successful": total - blocked - errors,
            "snapshots": self._snapshots.count,
            "budget": self._budget.summary(),
        }
