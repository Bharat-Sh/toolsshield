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
from agentshield.masking import DataMasker
from agentshield.ratelimit import RateLimiter, ToolLimit
from agentshield.rules import RuleEngine
from agentshield.snapshot import SnapshotStore
from agentshield.retry import RetryPolicy, execute_with_retry
from agentshield.webhooks import EventNotifier


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
        global_rpm: int | None = None,
        global_rph: int | None = None,
        tool_limits: dict[str, ToolLimit] | None = None,
    ) -> None:
        self._mode = Mode(mode)
        self._auto_snapshot = auto_snapshot
        self._on_block = on_block
        self._rules = RuleEngine()
        self._budget = BudgetTracker(budget=budget)
        self._snapshots = SnapshotStore(max_snapshots=max_snapshots)
        self._history: list[ActionResult] = []
        self._tool_states: dict[str, Any] = {}
        self._logger = ActionLogger(db_path, masker=None) if db_path else None
        self._rate_limiter = RateLimiter(
            global_rpm=global_rpm,
            global_rph=global_rph,
            tool_limits=tool_limits or {},
        )
        self._notifier = EventNotifier()
        self._masker: DataMasker | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "AgentShield":
        """Create an AgentShield from a config dict."""
        parsed = parse_config(config)

        tool_limits = {}
        for name, lim in config.get("tool_limits", {}).items():
            tool_limits[name] = ToolLimit(
                rpm=lim.get("rpm"),
                rph=lim.get("rph"),
            )

        instance = cls(
            budget=parsed["budget"],
            auto_snapshot=parsed["auto_snapshot"],
            mode=parsed["mode"],
            max_snapshots=parsed["max_snapshots"],
            db_path=parsed.get("db_path"),
            global_rpm=config.get("global_rpm"),
            global_rph=config.get("global_rph"),
            tool_limits=tool_limits or None,
        )
        for rule in parsed["extra_rules"]:
            instance._rules.add_rule(rule)

        for wh in config.get("webhooks", []):
            instance._notifier.add_webhook(
                url=wh["url"],
                events=set(wh.get("events", [])) or None,
                headers=wh.get("headers", {}),
            )

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

    @property
    def notifier(self) -> EventNotifier:
        return self._notifier

    def set_masker(self, masker: DataMasker) -> None:
        """Attach a DataMasker for log/webhook PII redaction."""
        self._masker = masker
        if self._logger:
            self._logger._masker = masker

    def _mask_args(self, args: dict) -> dict:
        if self._masker:
            return self._masker.mask(args)
        return args

    # -- Tool decorator --

    def tool(
        self,
        fn: Callable | None = None,
        *,
        cost: float = 0.0,
        risk: RiskLevel | None = None,
        snapshot_state: Callable[[], Any] | None = None,
        restore_state: Callable[[Any], None] | None = None,
        retry: RetryPolicy | None = None,
    ) -> Callable:
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

                return self._execute(action, func, args, kwargs, cost, risk, retry)

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
        retry_policy: RetryPolicy | None = None,
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
            self._notifier.emit("action_blocked", {
                "tool": action.tool_name, "reason": result.block_reason,
            })
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
                self._notifier.emit("action_blocked", {
                    "tool": action.tool_name, "reason": result.block_reason,
                })
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
            self._notifier.emit("budget_exceeded", {
                "tool": action.tool_name, "spent": self._budget.spent,
                "budget": self._budget.budget,
            })
            return result

        exceeded, rate_reason = self._rate_limiter.would_exceed(action.tool_name)
        if exceeded:
            result = ActionResult(
                action=action,
                blocked=True,
                block_reason=rate_reason,
            )
            self._history.append(result)
            if self._logger:
                self._logger.log(result)
            self._notifier.emit("rate_limited", {
                "tool": action.tool_name, "reason": rate_reason,
            })
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
            self._rate_limiter.record(action.tool_name)
            self._history.append(result)
            if self._logger:
                self._logger.log(result)
            return result

        try:
            if retry_policy:
                output, _errors = execute_with_retry(func, args, kwargs, retry_policy)
            else:
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
            self._notifier.emit("action_error", {
                "tool": action.tool_name, "error": str(e),
            })

        self._budget.record(action.id, action.tool_name, cost)
        self._rate_limiter.record(action.tool_name)
        self._history.append(result)
        if self._logger:
            self._logger.log(result)

        if result.success:
            self._notifier.emit("action_completed", {
                "tool": action.tool_name, "cost": cost,
            })

        if self._budget.warning:
            self._notifier.emit("budget_warning", {
                "spent": self._budget.spent, "budget": self._budget.budget,
            })

        return result

    # -- Rollback --

    def rollback(self, action_id: str | None = None) -> bool:
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
            "rate_limits": self._rate_limiter.stats(),
        }
