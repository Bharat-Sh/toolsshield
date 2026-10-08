"""Prometheus-compatible metrics export for AgentShield.

Exposes a /metrics endpoint in OpenMetrics/Prometheus text format.
Zero dependencies — uses Python's built-in http.server.

Usage:
    from agentshield.metrics import start_metrics_server

    shield = AgentShield(budget=10.0)
    server = start_metrics_server(shield, port=9090)
    # Scrape http://localhost:9090/metrics with Prometheus

    # Or add metrics endpoint to the dashboard:
    from agentshield.metrics import prometheus_metrics
    text = prometheus_metrics(shield)
"""

from __future__ import annotations

import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentshield.shield import AgentShield


def prometheus_metrics(shield: AgentShield) -> str:
    """Generate Prometheus text-format metrics from shield state."""
    stats = shield.stats()
    budget = stats["budget"]
    rl = stats["rate_limits"]

    lines = [
        "# HELP agentshield_actions_total Total number of agent actions.",
        "# TYPE agentshield_actions_total counter",
        f'agentshield_actions_total {stats["total_actions"]}',
        "",
        "# HELP agentshield_actions_blocked_total Total blocked actions.",
        "# TYPE agentshield_actions_blocked_total counter",
        f'agentshield_actions_blocked_total {stats["blocked"]}',
        "",
        "# HELP agentshield_actions_errors_total Total actions with errors.",
        "# TYPE agentshield_actions_errors_total counter",
        f'agentshield_actions_errors_total {stats["errors"]}',
        "",
        "# HELP agentshield_actions_successful_total Total successful actions.",
        "# TYPE agentshield_actions_successful_total counter",
        f'agentshield_actions_successful_total {stats["successful"]}',
        "",
        "# HELP agentshield_budget_spent_dollars Total budget spent.",
        "# TYPE agentshield_budget_spent_dollars gauge",
        f'agentshield_budget_spent_dollars {budget["spent"]}',
        "",
        "# HELP agentshield_budget_remaining_dollars Budget remaining.",
        "# TYPE agentshield_budget_remaining_dollars gauge",
        f'agentshield_budget_remaining_dollars {budget["remaining"] if budget["remaining"] is not None else -1}',
        "",
        "# HELP agentshield_snapshots_count Current number of snapshots.",
        "# TYPE agentshield_snapshots_count gauge",
        f'agentshield_snapshots_count {stats["snapshots"]}',
        "",
        "# HELP agentshield_rate_calls_last_minute Global calls in last minute.",
        "# TYPE agentshield_rate_calls_last_minute gauge",
        f'agentshield_rate_calls_last_minute {rl["global_last_minute"]}',
        "",
        "# HELP agentshield_rate_calls_last_hour Global calls in last hour.",
        "# TYPE agentshield_rate_calls_last_hour gauge",
        f'agentshield_rate_calls_last_hour {rl["global_last_hour"]}',
        "",
    ]

    by_tool = rl.get("by_tool", {})
    if by_tool:
        lines.append("# HELP agentshield_tool_calls_last_minute Per-tool calls in last minute.")
        lines.append("# TYPE agentshield_tool_calls_last_minute gauge")
        for tool_name, tool_stats in by_tool.items():
            lines.append(f'agentshield_tool_calls_last_minute{{tool="{tool_name}"}} {tool_stats["last_minute"]}')
        lines.append("")
        lines.append("# HELP agentshield_tool_calls_last_hour Per-tool calls in last hour.")
        lines.append("# TYPE agentshield_tool_calls_last_hour gauge")
        for tool_name, tool_stats in by_tool.items():
            lines.append(f'agentshield_tool_calls_last_hour{{tool="{tool_name}"}} {tool_stats["last_hour"]}')
        lines.append("")

    return "\n".join(lines) + "\n"


def multiagent_metrics(pool) -> str:
    """Generate Prometheus metrics for an AgentPool."""
    stats = pool.stats()
    lines = [
        "# HELP agentshield_pool_agents_total Number of registered agents.",
        "# TYPE agentshield_pool_agents_total gauge",
        f'agentshield_pool_agents_total {stats["total_agents"]}',
        "",
        "# HELP agentshield_pool_actions_total Total actions across all agents.",
        "# TYPE agentshield_pool_actions_total counter",
        f'agentshield_pool_actions_total {stats["total_actions"]}',
        "",
        "# HELP agentshield_pool_blocked_total Total blocked actions across all agents.",
        "# TYPE agentshield_pool_blocked_total counter",
        f'agentshield_pool_blocked_total {stats["total_blocked"]}',
        "",
        "# HELP agentshield_pool_spent_dollars Total spent across all agents.",
        "# TYPE agentshield_pool_spent_dollars gauge",
        f'agentshield_pool_spent_dollars {stats["total_spent"]}',
        "",
    ]

    for agent_name, agent_stats in stats["per_agent"].items():
        b = agent_stats["budget"]
        lines.append(f'agentshield_agent_actions_total{{agent="{agent_name}"}} {agent_stats["total_actions"]}')
        lines.append(f'agentshield_agent_blocked_total{{agent="{agent_name}"}} {agent_stats["blocked"]}')
        lines.append(f'agentshield_agent_spent_dollars{{agent="{agent_name}"}} {b["spent"]}')

    lines.append("")
    return "\n".join(lines) + "\n"


class _MetricsHandler(BaseHTTPRequestHandler):
    shield: AgentShield

    def do_GET(self):
        if self.path == "/metrics":
            body = prometheus_metrics(self.shield)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_metrics_server(
    shield: AgentShield,
    port: int = 9090,
    host: str = "127.0.0.1",
    background: bool = True,
) -> HTTPServer:
    """Start a Prometheus-compatible metrics server.

    Returns the HTTPServer instance (call .shutdown() to stop).
    """
    handler = type("MetricsHandler", (_MetricsHandler,), {"shield": shield})
    server = HTTPServer((host, port), handler)

    if background:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    else:
        server.serve_forever()

    return server
