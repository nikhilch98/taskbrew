# Event-Driven Task Claims

**Status:** Approved 2026-04-24
**Author:** Brainstormed with user through the brainstorming skill.

## Problem

Every `AgentLoop` polls `TaskBoard.claim_task` every `poll_interval`
seconds (default 5 s). A task landing in the queue at the wrong
moment waits up to `poll_interval` before any agent sees it.
A 4-hop pipeline (pm → architect → coder → verifier) stacks this
into ~20 s of pure claim latency per feature, independent of LLM
execution time. The existing WS / event infrastructure already
supports in-process pub/sub (`task.claimed`, `task.completed`,
etc.) — we simply haven't wired agents to react to new tasks.

## Design

Add one new event (`task.available`) and subscribe each AgentLoop
to it. The poll loop waits on either the wake event OR
`poll_interval`, whichever comes first. Typical claim latency
drops to ~ms; poll stays as the crash-recovery backstop.

### The event

```python
await event_bus.emit("task.available", {
    "task_id":  ...,
    "role":     ...,   # assigned_to role, so agents filter cheaply
    "group_id": ...,
})
```

Emitted at every code path that transitions a task to
`status='pending'`:

1. **`TaskBoard.create_task`** — after the INSERT succeeds, iff
   `status == 'pending'` (not when `status == 'blocked'`, which
   can't be claimed until dependencies resolve).
2. **Dependency resolution** — in the existing code path that
   flips a blocked task to pending when its last `blocked_by`
   row resolves. The existing `task.unblocked` emit already fires
   there; we additionally emit `task.available`.
3. **`AgentLoop._requeue_for_fanout`** — when a design task is
   sent back to `pending` for the architect to try again.
4. **`AgentLoop._requeue_for_verification`** — when a task with
   failing completion checks is sent back to `pending` for the
   coder to fix.

The payload carries `role` (not the full task) because agents
filter in-memory before any DB touch; if they wake up, they
re-issue the same `claim_task` they would have polled.

### Agent subscription

```python
class AgentLoop:
    async def run(self) -> None:
        self._running = True
        self._wake_event = asyncio.Event()

        async def _wake_on_available(event):
            if event.get("role") == self.role_config.role:
                self._wake_event.set()

        self._wake_handler = _wake_on_available
        self.event_bus.subscribe("task.available", _wake_on_available)
        try:
            await self.instance_manager.register_instance(
                self.instance_id, self.role_config,
            )
            ...

            while self._running:
                try:
                    processed = await self.run_once()
                    if not processed:
                        try:
                            await asyncio.wait_for(
                                self._wake_event.wait(),
                                timeout=self.poll_interval,
                            )
                        except asyncio.TimeoutError:
                            pass
                        self._wake_event.clear()
                except Exception:
                    logger.exception("Agent %s crashed", self.instance_id)
                    await self.instance_manager.update_status(
                        self.instance_id, "idle", current_task=None,
                    )
                    await asyncio.sleep(self.poll_interval)
                await self.instance_manager.heartbeat(self.instance_id)
        finally:
            self.event_bus.unsubscribe("task.available", self._wake_handler)
            await self.instance_manager.update_status(
                self.instance_id, "stopped",
            )
            await self.event_bus.emit(
                "agent.stopped",
                {"instance_id": self.instance_id,
                 "model": self.role_config.model},
            )
```

Key properties:

- **Subscribe before register_instance**: so any `task.available`
  emitted *after* the agent is visible in the DB is also seen by
  its callback.
- **Unsubscribe in `finally`**: so a stopped agent doesn't leak
  a dangling callback in the event bus.
- **Callback is async** (matches EventBus contract). It only
  sets an `asyncio.Event`; no IO or DB work in the callback.
- **Role filtering inside the callback**: agents for other roles
  pay only a dict-compare on every emit. Negligible.
- **Thundering herd is bounded by N-per-role and safe**: N coder
  instances wake, one wins `claim_task`, the rest loop back to
  `wait_for` without visible spin.

### Interaction with poll

The poll interval is now the **worst-case** wake latency, not the
typical case. If an emit is missed (agent restart mid-emit,
subscription lost during reconnect), the poll catches the task
within `poll_interval`. No correctness regression; the change is
strictly additive.

### Edge cases

- **Callback raises**: `EventBus._safe_dispatch` already catches
  and logs. The handler itself does nothing IO-bound; the only
  failure mode is an attribute error on `event.get(...)`, which
  won't happen for well-formed emits.
- **Emit during startup window**: the first poll after registration
  sees the row. Maximum delay is `poll_interval`.
- **Multiple rapid emits**: `Event.set()` is idempotent; a single
  wake drains all accumulated "available" signals. After the
  agent's claim loop runs once, `clear()` resets for the next wake.
- **Thundering herd with many instances**: atomic claim keeps this
  correct; doesn't generate extra DB load beyond the N-per-role
  empty claim attempts.

### Testing

- **Wake-and-claim**: create_task for role `coder`, assert the
  agent claims within ~100 ms (not within poll_interval).
- **Role filtering**: emit `task.available(role='architect')` —
  coder agent must NOT wake.
- **Poll backstop**: insert a row directly via the DB (bypassing
  `create_task` so no emit fires), assert the agent still picks
  it up within `poll_interval`.

### Rollout

No schema change, no config change, no prompt change. One log
line at agent startup ("subscribed to task.available; poll
=backstop") so operators can confirm. Existing deployments with
`poll_interval=5` will see immediate latency reduction; operators
who want pure poll behaviour can raise `poll_interval` to any
value they like without affecting correctness.

## Out of scope

- Cross-process event delivery (Redis, SQS, etc.) — single-process
  assumption matches the whole TaskBrew architecture today.
- Adaptive poll intervals — unnecessary once events handle the
  hot path.
- Replacing the `task.unblocked` event; we *add* `task.available`
  alongside it so existing subscribers keep working.

## Decisions captured

- In-process EventBus subscription, not external message queue.
- Poll remains as the crash-recovery backstop, not removed.
- `role` in payload for cheap client-side filtering; full task
  fetched via `claim_task` as today.
- `task.available` is a new event; existing `task.unblocked` is
  preserved for subscribers that care specifically about
  dependency resolution.
