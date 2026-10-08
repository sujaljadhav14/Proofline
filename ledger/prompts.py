ROUTER_SYSTEM_PROMPT = """You are the parser for a personal work-tracking bot used by a software developer. The user types short, messy messages in English about tasks on their projects.
Tasks come from managers, clients, testers and meetings, or are the user's own notes.
Answer with exactly one call to `respond`. Fill only the parts that apply:
- **tasks:** NEW tasks to add. One entry per distinct task; split meeting dumps. Never invent a project, deadline or source the message does not state or clearly imply. Titles are short, specific, imperative.
- **deadline_text:** copy the user's own words for the date ("friday", "tomorrow", "15 oct"). Do not convert it. Omit if none.
- **done:** existing OPEN tasks the user says they finished. Use ONLY ids from the open task list. confidence is "high" only if exactly one listed task clearly matches; otherwise "low".
- **edits:** changes to existing open tasks (deadline, priority, title, type, source, notes).
- **query:** when the user asks what is pending, what to do first, what they finished, or what is due. Set project only if they name one; set days only if they give a period ("this week" = 7).
- **question:** ONE short clarifying question, only if something important is genuinely ambiguous. Otherwise omit.
- **reply:** a short friendly reply only if the message is none of the above.
A message can combine these. For project, use the name exactly as listed when the message names it or an alias."""

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "type": {"type": "string", "enum": ["bug", "enhancement", "task"]},
                    "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                    "project": {"type": "string"},
                    "source": {"type": "string"},
                    "deadline_text": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["title", "type", "priority"],
            },
        },
        "done": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                },
                "required": ["task_id", "confidence"],
            },
        },
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "field": {
                        "type": "string",
                        "enum": ["deadline", "priority", "title", "type", "source", "notes"],
                    },
                    "value": {"type": "string"},
                },
                "required": ["task_id", "field", "value"],
            },
        },
        "query": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["pending", "first", "finished", "due"]},
                "project": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["kind"],
        },
        "question": {"type": "string"},
        "reply": {"type": "string"},
    },
}

DUPLICATE_SYSTEM_PROMPT = """Compare a NEW task with EXISTING tasks from the same project. Answer with one call to `respond`. Set `duplicate_of` to the id of an existing task ONLY if the new task is clearly the same piece of work (same bug or same change), not merely the same area. If unsure, omit it."""

DUPLICATE_SCHEMA = {
    "type": "object",
    "properties": {
        "duplicate_of": {"type": "integer"},
    },
}


def build_router_user_prompt(projects_text: str, open_tasks_text: str, user_message: str) -> str:
    parts = []
    parts.append("Projects:")
    parts.append(projects_text if projects_text else "(none)")
    parts.append("\nOpen tasks (id | project | title):")
    parts.append(open_tasks_text if open_tasks_text else "(none)")
    parts.append("\nMessage:")
    parts.append(user_message)
    return "\n".join(parts)


def build_duplicate_user_prompt(new_title: str, existing_tasks_text: str) -> str:
    return f"""New task:
{new_title}

Existing tasks from this project (id | status | title):
{existing_tasks_text}"""
