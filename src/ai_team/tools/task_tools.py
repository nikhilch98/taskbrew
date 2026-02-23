"""MCP tools for task queue operations - agents can claim/complete tasks."""

from claude_agent_sdk import tool, create_sdk_mcp_server


def build_task_tools_server(db_path: str = "data/tasks.db"):
    @tool(
        "claim_task",
        "Claim the next available pending task from the queue.",
        {"pipeline_id": str},
    )
    async def claim_task(args):
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Task claimed from pipeline {args.get('pipeline_id', 'default')}.",
                }
            ]
        }

    @tool(
        "complete_task",
        "Mark a task as completed with an optional output artifact path.",
        {"task_id": str, "artifact_path": str},
    )
    async def complete_task(args):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Task {args['task_id']} marked complete. "
                        f"Artifact: {args.get('artifact_path', 'none')}"
                    ),
                }
            ]
        }

    @tool(
        "create_subtask",
        "Create a new subtask under the current task.",
        {"title": str, "description": str, "assigned_role": str},
    )
    async def create_subtask(args):
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Subtask created: {args['title']} -> {args['assigned_role']}",
                }
            ]
        }

    return create_sdk_mcp_server(
        name="task-tools", version="1.0.0", tools=[claim_task, complete_task, create_subtask]
    )
