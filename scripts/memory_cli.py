#!/usr/bin/env python3
"""
memory_cli.py — terminal / harness inspection for the agent's cross-call memory.

The voice agent's memory lives in a private SQLite DB (data/agent_memory.db): finished CALLS
(date, caller, direction, summary), the durable FACTS it has learned, and per-call TURNS +
tool ACTIONS. This CLI is the human-readable window into that store — for debugging what the
agent remembers, eyeballing the latest call, or jotting a fact by hand. It uses ONLY the
agent_memory public API + stdlib (argparse), so there are no new pip deps.

Subcommands:
    calls  [--limit N]            recent calls (date, caller, direction, summary)
    facts  [--limit N]            durable facts (text, kind, weight, last_seen)
    search <query>                facts + summaries matching a substring
    add    <text> [--kind K]      add a durable fact (secret-scrubbed before storing)
    show   <call_sid>             one call's turns + tool actions
Global:
    --db PATH                     override the DB path (default: <project>/data/agent_memory.db)

Examples:
    python scripts/memory_cli.py calls --limit 5
    python scripts/memory_cli.py search dog
    python scripts/memory_cli.py add "Prefers tea over coffee" --kind preference
    python scripts/memory_cli.py show CA1234... --db /tmp/mem.db
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Make `import agent_memory` work whether or not PYTHONPATH=src is set: prepend the project's
# src/ to sys.path (this file is at <project>/scripts/memory_cli.py, so ../src).
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from agent_memory import scrub, search  # noqa: E402
from agent_memory import store  # noqa: E402


# --------------------------------------------------------------------------- formatting

def _fmt_ts(ts, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format an epoch timestamp; '-' if missing/unparseable."""
    if not ts:
        return "-"
    try:
        return time.strftime(fmt, time.localtime(float(ts)))
    except (ValueError, OSError, OverflowError):
        return "-"


def _truncate(text, width: int) -> str:
    s = " ".join(str(text or "").split())  # collapse whitespace/newlines for table rows
    if len(s) <= width:
        return s
    return s[: max(0, width - 1)] + "…"  # ellipsis


def _print_table(headers: list[str], rows: list[list[str]], widths: list[int]) -> None:
    """Print a simple fixed-width table. Each cell is truncated to its column width."""
    def fmt_row(cells: list[str]) -> str:
        return "  ".join(_truncate(c, w).ljust(w) for c, w in zip(cells, widths))

    print(fmt_row(headers))
    print(fmt_row(["-" * w for w in widths]))
    for r in rows:
        print(fmt_row([str(c) for c in r]))


# --------------------------------------------------------------------------- subcommands

def cmd_calls(args) -> int:
    store.init_store()
    calls = store.recent_calls(limit=args.limit)
    if not calls:
        print("No calls recorded yet.")
        return 0
    rows = [
        [
            _fmt_ts(c.get("ended_at") or c.get("started_at")),
            c.get("caller") or "-",
            c.get("direction") or "-",
            c.get("call_sid") or "-",
            c.get("summary") or "",
        ]
        for c in calls
    ]
    _print_table(
        ["DATE", "CALLER", "DIR", "CALL_SID", "SUMMARY"],
        rows,
        [16, 15, 12, 18, 60],
    )
    print(f"\n{len(calls)} call(s).")
    return 0


def cmd_facts(args) -> int:
    store.init_store()
    facts = store.all_facts()
    # Rank like retrieval surfaces them: heaviest, then most recently seen.
    facts.sort(
        key=lambda f: (float(f.get("weight") or 0), float(f.get("last_seen") or 0)),
        reverse=True,
    )
    facts = facts[: args.limit]
    if not facts:
        print("No durable facts stored yet.")
        return 0
    rows = [
        [
            f.get("text") or "",
            f.get("kind") or "-",
            f"{float(f.get('weight') or 0):.1f}",
            _fmt_ts(f.get("last_seen")),
        ]
        for f in facts
    ]
    _print_table(["TEXT", "KIND", "WEIGHT", "LAST_SEEN"], rows, [56, 12, 7, 16])
    print(f"\n{len(facts)} fact(s).")
    return 0


def cmd_search(args) -> int:
    store.init_store()
    hits = search(args.query, limit=args.limit)
    facts = hits.get("facts", [])
    calls = hits.get("calls", [])
    if not facts and not calls:
        print(f"Nothing matching {args.query!r}.")
        return 0

    if facts:
        print(f"FACTS matching {args.query!r}:")
        rows = [
            [
                f.get("text") or "",
                f.get("kind") or "-",
                f"{float(f.get('weight') or 0):.1f}",
                _fmt_ts(f.get("last_seen")),
            ]
            for f in facts
        ]
        _print_table(["TEXT", "KIND", "WEIGHT", "LAST_SEEN"], rows, [56, 12, 7, 16])
        print()

    if calls:
        print(f"CALL SUMMARIES matching {args.query!r}:")
        rows = [
            [
                _fmt_ts(c.get("ended_at") or c.get("started_at")),
                c.get("caller") or "-",
                c.get("call_sid") or "-",
                c.get("summary") or "",
            ]
            for c in calls
        ]
        _print_table(["DATE", "CALLER", "CALL_SID", "SUMMARY"], rows, [16, 15, 18, 60])
    return 0


def cmd_add(args) -> int:
    text = (args.text or "").strip()
    if not text:
        print("Nothing to add (empty text).", file=sys.stderr)
        return 2
    # SECRETS carve-out: scrub before storing, exactly like the live tool path.
    clean = scrub(text)
    if not clean or "[REDACTED]" in clean:
        print("Refused: that looks like a secret, which is never stored.", file=sys.stderr)
        return 2
    store.init_store()
    store.upsert_fact(args.kind, clean)
    if clean != text:
        print(f"Added (scrubbed) [{args.kind}]: {clean}")
    else:
        print(f"Added [{args.kind}]: {clean}")
    return 0


def cmd_show(args) -> int:
    store.init_store()
    call = store.get_call(args.call_sid)
    if call is None:
        print(f"No call with sid {args.call_sid!r}.", file=sys.stderr)
        return 1

    print(f"Call {call.get('call_sid')}")
    print(f"  caller    : {call.get('caller') or '-'}")
    print(f"  direction : {call.get('direction') or '-'}")
    print(f"  started   : {_fmt_ts(call.get('started_at'), '%Y-%m-%d %H:%M:%S')}")
    print(f"  ended     : {_fmt_ts(call.get('ended_at'), '%Y-%m-%d %H:%M:%S')}")
    print(f"  summary   : {call.get('summary') or '-'}")

    turns = store.get_turns(args.call_sid)
    print(f"\nTURNS ({len(turns)}):")
    if turns:
        rows = [[_fmt_ts(t.get("ts"), "%H:%M:%S"), t.get("role") or "-", t.get("text") or ""]
                for t in turns]
        _print_table(["TIME", "ROLE", "TEXT"], rows, [10, 10, 70])
    else:
        print("  (none)")

    actions = store.get_actions(args.call_sid)
    print(f"\nACTIONS ({len(actions)}):")
    if actions:
        rows = [
            [
                _fmt_ts(a.get("ts"), "%H:%M:%S"),
                a.get("tool") or "-",
                a.get("args") or "",
                a.get("result") or "",
            ]
            for a in actions
        ]
        _print_table(["TIME", "TOOL", "ARGS", "RESULT"], rows, [10, 18, 30, 40])
    else:
        print("  (none)")
    return 0


# --------------------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memory_cli",
        description="Inspect the voice agent's cross-call memory (SQLite).",
    )
    p.add_argument("--db", help="Override the DB path (default: <project>/data/agent_memory.db).")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("calls", help="List recent calls.")
    sp.add_argument("--limit", type=int, default=10, help="Max calls to show (default 10).")
    sp.set_defaults(func=cmd_calls)

    sp = sub.add_parser("facts", help="List durable facts.")
    sp.add_argument("--limit", type=int, default=20, help="Max facts to show (default 20).")
    sp.set_defaults(func=cmd_facts)

    sp = sub.add_parser("search", help="Search facts + call summaries.")
    sp.add_argument("query", help="Substring to search for (case-insensitive).")
    sp.add_argument("--limit", type=int, default=10, help="Max hits per kind (default 10).")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("add", help="Add a durable fact (secret-scrubbed).")
    sp.add_argument("text", help="The fact/preference text to remember.")
    sp.add_argument("--kind", default="note", help="Category for the fact (default 'note').")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("show", help="Show one call's turns + actions.")
    sp.add_argument("call_sid", help="The call_sid to show.")
    sp.set_defaults(func=cmd_show)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.db:
        store.set_db_path(args.db)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
