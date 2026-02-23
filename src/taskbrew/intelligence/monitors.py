"""Background monitoring tasks for the orchestrator."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def escalation_monitor(
    escalation_manager,
    check_interval: int = 300,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Background task that periodically checks for stuck tasks and auto-escalates.

    Args:
        escalation_manager: The EscalationManager instance
        check_interval: Seconds between checks (default 5 minutes)
        stop_event: Optional event to signal graceful shutdown
    """
    if stop_event is None:
        stop_event = asyncio.Event()

    while not stop_event.is_set():
        try:
            stuck_tasks = await escalation_manager.check_stuck_tasks()
            if stuck_tasks:
                logger.info(
                    "Escalation monitor: found %d stuck tasks", len(stuck_tasks)
                )
                for task in stuck_tasks:
                    task_id = task["id"]
                    claimed_by = task.get("claimed_by", "unknown")
                    try:
                        await escalation_manager.escalate(
                            task_id=task_id,
                            from_agent="escalation-monitor",
                            reason=f"Task stuck: no heartbeat from {claimed_by}",
                            severity="high",
                        )
                        logger.info("Escalated stuck task %s", task_id)
                    except Exception:
                        logger.warning(
                            "Failed to escalate task %s", task_id, exc_info=True
                        )
        except Exception:
            logger.warning("Escalation monitor check failed", exc_info=True)

        # Wait for interval or stop signal
        if check_interval > 0:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=check_interval)
            except asyncio.TimeoutError:
                pass  # Normal — timeout means we should check again
        else:
            # Yield control to allow other coroutines to run
            await asyncio.sleep(0)
