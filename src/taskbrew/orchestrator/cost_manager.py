"""Cost budget management and enforcement."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class CostManager:
    """Track and enforce cost budgets at global, group, and role scopes."""

    def __init__(self, db) -> None:
        self._db = db

    async def check_budget(
        self, role: str | None = None, group_id: str | None = None
    ) -> dict:
        """Check if spending is within budget.

        Returns
        -------
        dict
            ``{allowed, remaining, budget, spent, scope}``
        """
        now = datetime.now(timezone.utc).isoformat()

        # Check global budget first
        global_budget = await self._db.execute_fetchone(
            "SELECT * FROM cost_budgets WHERE scope = 'global' "
            "AND (reset_at IS NULL OR reset_at > ?)",
            (now,),
        )
        if global_budget and global_budget["spent_usd"] >= global_budget["budget_usd"]:
            return {
                "allowed": False,
                "remaining": 0,
                "budget": global_budget["budget_usd"],
                "spent": global_budget["spent_usd"],
                "scope": "global",
            }

        # Check role budget
        if role:
            role_budget = await self._db.execute_fetchone(
                "SELECT * FROM cost_budgets WHERE scope = 'role' AND scope_id = ? "
                "AND (reset_at IS NULL OR reset_at > ?)",
                (role, now),
            )
            if role_budget and role_budget["spent_usd"] >= role_budget["budget_usd"]:
                return {
                    "allowed": False,
                    "remaining": 0,
                    "budget": role_budget["budget_usd"],
                    "spent": role_budget["spent_usd"],
                    "scope": "role",
                }

        # Check group budget
        if group_id:
            group_budget = await self._db.execute_fetchone(
                "SELECT * FROM cost_budgets WHERE scope = 'group' AND scope_id = ? "
                "AND (reset_at IS NULL OR reset_at > ?)",
                (group_id, now),
            )
            if group_budget and group_budget["spent_usd"] >= group_budget["budget_usd"]:
                return {
                    "allowed": False,
                    "remaining": 0,
                    "budget": group_budget["budget_usd"],
                    "spent": group_budget["spent_usd"],
                    "scope": "group",
                }

        return {
            "allowed": True,
            "remaining": None,
            "budget": None,
            "spent": None,
            "scope": None,
        }

    async def record_spend(
        self, cost_usd: float, role: str | None = None, group_id: str | None = None
    ) -> None:
        """Record spending against applicable budgets."""
        now = datetime.now(timezone.utc).isoformat()

        # Update global budget
        await self._db.execute(
            "UPDATE cost_budgets SET spent_usd = spent_usd + ? "
            "WHERE scope = 'global' AND (reset_at IS NULL OR reset_at > ?)",
            (cost_usd, now),
        )

        if role:
            await self._db.execute(
                "UPDATE cost_budgets SET spent_usd = spent_usd + ? "
                "WHERE scope = 'role' AND scope_id = ? "
                "AND (reset_at IS NULL OR reset_at > ?)",
                (cost_usd, role, now),
            )

        if group_id:
            await self._db.execute(
                "UPDATE cost_budgets SET spent_usd = spent_usd + ? "
                "WHERE scope = 'group' AND scope_id = ? "
                "AND (reset_at IS NULL OR reset_at > ?)",
                (cost_usd, group_id, now),
            )

        # Check for budget warnings (80% threshold)
        budgets = await self._db.execute_fetchall(
            "SELECT * FROM cost_budgets "
            "WHERE spent_usd >= budget_usd * 0.8 "
            "AND (reset_at IS NULL OR reset_at > ?)",
            (now,),
        )
        for b in budgets:
            pct = (b["spent_usd"] / b["budget_usd"] * 100) if b["budget_usd"] > 0 else 0
            severity = "critical" if b["spent_usd"] >= b["budget_usd"] else "warning"

            # Check for existing unread notification for the same budget
            # to avoid duplicate warnings.  A new notification is only
            # created once the previous one has been read/dismissed.
            existing = await self._db.execute_fetchone(
                "SELECT id FROM notifications WHERE type = 'budget_warning' "
                "AND read = 0 AND data LIKE ?",
                (f'%{b["id"]}%',),
            )
            if existing:
                continue  # Skip duplicate notification

            await self._db.create_notification(
                type="budget_warning",
                title=f"Budget {severity}: {b['scope']} {b.get('scope_id') or 'global'}",
                message=f"Spent ${b['spent_usd']:.2f} of ${b['budget_usd']:.2f} ({pct:.0f}%)",
                severity=severity,
                data=json.dumps({"budget_id": b["id"], "scope": b["scope"], "pct": pct}),
            )

    async def get_budgets(self) -> list[dict]:
        """Return all budgets ordered by scope."""
        return await self._db.execute_fetchall(
            "SELECT * FROM cost_budgets ORDER BY scope, scope_id"
        )

    async def create_budget(
        self,
        scope: str,
        budget_usd: float,
        scope_id: str | None = None,
        period: str = "daily",
    ) -> dict:
        """Create a new cost budget.

        Parameters
        ----------
        scope:
            One of ``'global'``, ``'group'``, or ``'role'``.
        budget_usd:
            Maximum spend in USD for the period.
        scope_id:
            Group ID or role name (required for non-global scopes).
        period:
            One of ``'daily'``, ``'weekly'``, ``'monthly'``.
        """
        budget_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc)

        if period == "daily":
            reset_at = (
                (now + timedelta(days=1))
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .isoformat()
            )
        elif period == "weekly":
            reset_at = (
                (now + timedelta(days=7 - now.weekday()))
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .isoformat()
            )
        elif period == "monthly":
            if now.month == 12:
                reset_at = now.replace(
                    year=now.year + 1, month=1, day=1,
                    hour=0, minute=0, second=0, microsecond=0,
                ).isoformat()
            else:
                reset_at = now.replace(
                    month=now.month + 1, day=1,
                    hour=0, minute=0, second=0, microsecond=0,
                ).isoformat()
        else:
            reset_at = None

        await self._db.execute(
            "INSERT INTO cost_budgets "
            "(id, scope, scope_id, budget_usd, period, reset_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (budget_id, scope, scope_id, budget_usd, period, reset_at, now.isoformat()),
        )
        return {
            "id": budget_id,
            "scope": scope,
            "scope_id": scope_id,
            "budget_usd": budget_usd,
            "period": period,
        }

    async def delete_budget(self, budget_id: str) -> None:
        """Delete a cost budget by ID."""
        await self._db.execute("DELETE FROM cost_budgets WHERE id = ?", (budget_id,))
