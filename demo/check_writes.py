#!/usr/bin/env python3
"""Drive the write tools over stdio against the demo OJS instance.

Separate from `check_api.py` because this one *changes* the journal: it
publishes and unpublishes an article, edits metadata, records an editorial
decision, and posts an announcement. It refuses to run against anything but
the local demo instance -- these tools exist to modify production journals,
and a script that fires all of them must not be one typo away from doing so.

Each step reads the affected object back afterwards, so the output shows the
effect OJS actually applied, not just the status code it returned.

Usage:
    OJS_API_TOKEN=... python demo/check_writes.py
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

# The submission these tools are aimed at: the one still sitting in the
# submission stage, so publishing and unpublishing it disturbs nothing else.
DRAFT_SUBMISSION = 8
DRAFT_PUBLICATION = 8

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def guard() -> None:
    """Refuse to touch anything that is not the local demo."""
    allowed = ("http://localhost:8081", "http://127.0.0.1:8081")
    if BASE_URL not in allowed:
        sys.exit(
            f"refusing to run: OJS_BASE_URL is {BASE_URL!r}.\n"
            "This script modifies journal data and only runs against the "
            f"demo instance ({' or '.join(allowed)})."
        )


async def main() -> int:
    if not TOKEN:
        print("Set OJS_API_TOKEN (see demo/README.md).", file=sys.stderr)
        return 2
    guard()

    env = dict(os.environ)
    env.update(
        {
            "OJS_BASE_URL": BASE_URL,
            "OJS_JOURNAL": JOURNAL,
            "OJS_API_TOKEN": TOKEN,
            # Without this the five write tools are not registered at all.
            "OJS_ALLOW_WRITES": "1",
        }
    )
    command = os.environ.get("OJS_MCP_COMMAND") or str(
        pathlib.Path(sys.executable).with_name("ojs-mcp")
    )
    params = StdioServerParameters(command=command, args=[], env=env)

    failures = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            print(f"write tools registered: {sorted(names & WRITE_TOOLS)}\n")

            async def call(name, **kwargs):
                nonlocal failures
                label = f"{name}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})"
                if len(label) > 110:
                    label = label[:107] + "...)"
                result = await session.call_tool(name, kwargs)
                text = "".join(
                    b.text for b in result.content if getattr(b, "text", None)
                )
                bad = getattr(result, "is_error", getattr(result, "isError", False))
                if bad:
                    failures += 1
                    print(f"{RED}ERR {RESET} {label}\n     {text[:400]}")
                    return None
                print(f"{GREEN}ok  {RESET} {label}")
                try:
                    return json.loads(text)
                except ValueError:
                    return text

            async def status_of(submission):
                data = await call("get_submission", submission=submission)
                return (data or {}).get("status"), (data or {}).get("stage_name")

            print("--- 1. create_announcement")
            made = await call(
                "create_announcement",
                title={"en": "Call for papers: the issue that never was"},
                content={"en": "<p>Submissions are welcome and will be ignored.</p>"},
            )
            if made:
                print(f"     {DIM}created announcement id={made.get('id')}{RESET}")
            back = await call("ojs_request", path="/announcements")
            if isinstance(back, dict):
                print(f"     {DIM}announcements now: {back.get('itemsMax')}{RESET}")

            print("\n--- 2. edit_publication_metadata")
            await call(
                "edit_publication_metadata",
                submission=DRAFT_SUBMISSION,
                publication=DRAFT_PUBLICATION,
                fields={"pages": "33-48"},
            )
            pub = await call(
                "get_publication",
                submission=DRAFT_SUBMISSION,
                publication=DRAFT_PUBLICATION,
            )
            print(f"     {DIM}pages now: {(pub or {}).get('pages')!r}{RESET}")

            print("\n--- 3. add_editorial_decision (twice, to reach Production)")
            # OJS refuses to publish anything that has not reached Copyediting
            # or Production, so the decisions are not an optional extra here --
            # they are how a submission legally becomes publishable.
            before = await status_of(DRAFT_SUBMISSION)
            await call(
                "add_editorial_decision",
                submission=DRAFT_SUBMISSION,
                decision="skip_external_review",
            )
            await call(
                "add_editorial_decision",
                submission=DRAFT_SUBMISSION,
                decision="send_to_production",
            )
            after = await status_of(DRAFT_SUBMISSION)
            print(f"     {DIM}submission stage: {before} -> {after}{RESET}")

            print("\n--- 4. publish_publication")
            await call(
                "edit_publication_metadata",
                submission=DRAFT_SUBMISSION,
                publication=DRAFT_PUBLICATION,
                fields={"issueId": 2},
            )
            await call(
                "publish_publication",
                submission=DRAFT_SUBMISSION,
                publication=DRAFT_PUBLICATION,
            )
            published = await status_of(DRAFT_SUBMISSION)
            print(f"     {DIM}submission status: {after} -> {published}{RESET}")

            print("\n--- 5. unpublish_publication")
            await call(
                "unpublish_publication",
                submission=DRAFT_SUBMISSION,
                publication=DRAFT_PUBLICATION,
            )
            reverted = await status_of(DRAFT_SUBMISSION)
            print(f"     {DIM}submission status: {published} -> {reverted}{RESET}")

    print(f"\n{failures} failed" if failures else "\nall write tools succeeded")
    return 1 if failures else 0


WRITE_TOOLS = {
    "add_editorial_decision",
    "edit_publication_metadata",
    "publish_publication",
    "unpublish_publication",
    "create_announcement",
}


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
