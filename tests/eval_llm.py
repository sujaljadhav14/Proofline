import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from ledger.config import get_settings
from ledger.llm import GroqLLM, OllamaLLM, AnthropicLLM, LLM
from ledger.prompts import ROUTER_SYSTEM_PROMPT, ROUTER_SCHEMA, build_router_user_prompt


def evaluate_case(case: dict, result: dict) -> tuple[bool, str]:
    check = case["check"]
    tasks = result.get("tasks", [])
    done = result.get("done", [])
    edits = result.get("edits", [])
    query = result.get("query") or {}

    if check.get("has_tasks") and not tasks:
        return False, "Expected new tasks but got none"

    if check.get("no_tasks") and tasks:
        return False, f"Expected no new tasks for chit-chat but got {len(tasks)}"

    if "task_count" in check and len(tasks) != check["task_count"]:
        return False, f"Expected {check['task_count']} tasks, got {len(tasks)}"

    if "project" in check:
        exp_proj = check["project"].lower()
        if not any(exp_proj in (t.get("project") or "").lower() for t in tasks):
            return False, f"Expected project '{check['project']}' not found in tasks: {tasks}"

    if "source" in check:
        exp_src = check["source"].lower()
        if not any(exp_src in (t.get("source") or "").lower() for t in tasks):
            return False, f"Expected source '{check['source']}' not found in tasks: {tasks}"

    if check.get("has_deadline_text"):
        if not any(t.get("deadline_text") for t in tasks):
            return False, "Expected deadline_text in tasks but got none"

    if "deadline_text_contains" in check:
        exp_sub = check["deadline_text_contains"].lower()
        if not any(exp_sub in (t.get("deadline_text") or "").lower() for t in tasks):
            return False, f"deadline_text did not contain '{exp_sub}'"

    if "done_task_id" in check:
        exp_id = check["done_task_id"]
        if not any(d.get("task_id") == exp_id for d in done):
            return False, f"done task_id {exp_id} not found in {done}"

    if "confidence" in check:
        exp_conf = check["confidence"]
        if not any(d.get("confidence") == exp_conf for d in done):
            return False, f"confidence '{exp_conf}' not found in {done}"

    if check.get("has_edits") and not edits:
        return False, "Expected edits but got none"

    if "edit_field" in check:
        exp_f = check["edit_field"]
        if not any(e.get("field") == exp_f for e in edits):
            return False, f"edit field '{exp_f}' not found in {edits}"

    if "edit_value" in check:
        exp_v = check["edit_value"]
        if not any(e.get("value") == exp_v for e in edits):
            return False, f"edit value '{exp_v}' not found in {edits}"

    if "query_kind" in check:
        exp_kind = check["query_kind"]
        if query.get("kind") != exp_kind:
            return False, f"Expected query kind '{exp_kind}', got '{query.get('kind')}'"

    if "query_project" in check:
        exp_qp = check["query_project"].lower()
        if exp_qp not in (query.get("project") or "").lower():
            return False, f"Expected query project '{check['query_project']}', got '{query.get('project')}'"

    return True, "Passed"


async def run_eval(provider_name: str, model_name: str = "") -> None:
    settings = get_settings()
    llm: LLM

    if provider_name == "groq":
        if not settings.GROQ_API_KEY:
            print("ERROR: GROQ_API_KEY is not set.")
            sys.exit(1)
        target_model = model_name or settings.GROQ_MODEL
        llm = GroqLLM(
            api_key=settings.GROQ_API_KEY,
            model=target_model,
            json_mode=settings.GROQ_JSON_MODE,
        )
    elif provider_name == "ollama":
        target_model = model_name or settings.OLLAMA_MODEL
        llm = OllamaLLM(
            url=settings.OLLAMA_URL,
            model=target_model,
        )
    elif provider_name == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            print("ERROR: ANTHROPIC_API_KEY is not set.")
            sys.exit(1)
        target_model = model_name or settings.ANTHROPIC_MODEL
        llm = AnthropicLLM(
            api_key=settings.ANTHROPIC_API_KEY,
            model=target_model,
        )
    else:
        print(f"Unknown provider '{provider_name}'. Choose groq, ollama, or anthropic.")
        sys.exit(1)

    eval_file = Path(__file__).parent / "eval_cases.json"
    with open(eval_file, "r", encoding="utf-8") as f:
        cases = json.load(f)

    print(f"Running LLM evaluation with provider: {provider_name.upper()} [model: {target_model}] ({len(cases)} cases)...\n")

    category_scores = defaultdict(lambda: {"passed": 0, "total": 0})
    total_passed = 0

    for c in cases:
        cid = c["id"]
        cat = c["category"]
        category_scores[cat]["total"] += 1

        projects_text = "\n".join(f"- {p}" for p in c.get("projects", []))
        open_tasks_text = "\n".join(c.get("open_tasks", []))
        user_prompt = build_router_user_prompt(projects_text, open_tasks_text, c["message"])

        try:
            res = await llm.structured(
                system=ROUTER_SYSTEM_PROMPT,
                user=user_prompt,
                name="respond",
                schema=ROUTER_SCHEMA,
            )
            passed, reason = evaluate_case(c, res)
        except Exception as e:
            passed = False
            reason = f"Exception: {e}"

        if passed:
            total_passed += 1
            category_scores[cat]["passed"] += 1
            print(f"  [PASS] {cid:16} ({cat}): {c['message'][:40]}")
        else:
            print(f"  [FAIL] {cid:16} ({cat}): {reason}")

    print("\n" + "=" * 50)
    print(f" EVALUATION SCORES: {provider_name.upper()}")
    print("=" * 50)
    for cat, sc in sorted(category_scores.items()):
        pct = (sc["passed"] / sc["total"]) * 100 if sc["total"] else 0
        print(f"  {cat:24}: {sc['passed']}/{sc['total']} ({pct:5.1f}%)")

    total_pct = (total_passed / len(cases)) * 100 if cases else 0
    print("-" * 50)
    print(f"  OVERALL SCORE           : {total_passed}/{len(cases)} ({total_pct:5.1f}%)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate LLM provider performance on Work Ledger cases.")
    parser.add_argument("--provider", default="groq", choices=["groq", "ollama", "anthropic"])
    parser.add_argument("--model", default="", help="Override model name")
    args = parser.parse_args()
    asyncio.run(run_eval(args.provider, args.model))


if __name__ == "__main__":
    main()
