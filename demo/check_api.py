#!/usr/bin/env python3
"""Drive ojs-mcp over stdio against the demo OJS instance.

This is a real MCP client: it launches the server exactly the way Claude
Desktop would (`ojs-mcp` on stdio, configured through the environment),
lists what the server advertises, then calls the read tools one by one and
prints a compact summary of what OJS actually answered.

Usage:
    OJS_API_TOKEN=... python demo/check_api.py
"""

import asyncio
import json
import os
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_URL = os.environ.get("OJS_BASE_URL", "http://localhost:8081")
JOURNAL = os.environ.get("OJS_JOURNAL", "demojournal")
TOKEN = os.environ.get("OJS_API_TOKEN")

# (tool name, arguments) — the read-only surface, in roughly the order a
# user would explore a journal they had never seen before.
CALLS = [
    ("list_journals", {}),
    ("whoami", {}),
    ("list_sections", {}),
    ("list_issues", {}),
    ("get_current_issue", {}),
    ("get_issue", {"issue": 1}),
    ("search_submissions", {}),
    ("search_submissions", {"status": ["published"]}),
    ("search_submissions", {"phrase": "Deadlines"}),
    ("search_submissions", {"stage": ["external_review"]}),
    ("get_submission", {"submission": 1}),
    ("get_publication", {"submission": 1, "publication": 1}),
    ("list_submission_files", {"submission": 1}),
    ("get_submission_reviews", {"submission": 7}),
    ("search_users", {}),
    ("list_reviewers", {}),
    ("editorial_stats", {}),
    ("publication_stats", {}),
    ("list_dois", {}),
    # The escape hatch: an arbitrary API path, for endpoints with no tool.
    ("ojs_request", {"path": "/vocabs", "params": {"vocab": "submissionKeyword"}}),
]

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def summarize(payload: str, limit: int = 220) -> str:
    """One line describing what came back, without dumping whole objects."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return payload[:limit].replace("\n", " ")
    if isinstance(data, dict):
        for key in (
            "items",
            "journals",
            "submissions",
            "issues",
            "sections",
            "users",
            "reviewers",
            "files",
            "reviews",
            "dois",
        ):
            if isinstance(data.get(key), list):
                head = json.dumps(data[key][:2], ensure_ascii=False)[:limit]
                return f"{key}: {len(data[key])} — {head}"
        return json.dumps(data, ensure_ascii=False)[:limit]
    return json.dumps(data, ensure_ascii=False)[:limit]


async def main() -> int:
    if not TOKEN:
        print("Set OJS_API_TOKEN (see demo/README.md).", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env.update(
        {"OJS_BASE_URL": BASE_URL, "OJS_JOURNAL": JOURNAL, "OJS_API_TOKEN": TOKEN}
    )
    # The console script, because that is what an MCP client actually puts in
    # its configuration. `python -m ojs_mcp.server` works too, but only since
    # the `__main__` block this demo run is what prompted.
    command = os.environ.get("OJS_MCP_COMMAND") or str(
        pathlib.Path(sys.executable).with_name("ojs-mcp")
    )
    params = StdioServerParameters(command=command, args=[], env=env)

    failures = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            resources = (await session.list_resources()).resources
            prompts = (await session.list_prompts()).prompts
            print(
                f"server advertises: {len(tools)} tools, "
                f"{len(resources)} resources, {len(prompts)} prompts"
            )
            print(f"{DIM}tools: {', '.join(t.name for t in tools)}{RESET}\n")

            for name, args in CALLS:
                label = f"{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"
                try:
                    result = await session.call_tool(name, args)
                except Exception as exc:  # transport-level failure
                    failures += 1
                    print(f"{RED}FAIL{RESET} {label}\n     {exc}")
                    continue
                text = "".join(
                    block.text
                    for block in result.content
                    if getattr(block, "text", None)
                )
                if getattr(result, "is_error", getattr(result, "isError", False)):
                    failures += 1
                    print(f"{RED}ERR {RESET} {label}\n     {text[:300]}")
                else:
                    print(
                        f"{GREEN}ok  {RESET} {label}\n"
                        f"     {DIM}{summarize(text)}{RESET}"
                    )

    print(
        f"\n{len(CALLS) - failures}/{len(CALLS)} calls returned data; {failures} failed"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
