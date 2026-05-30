"""
files.py — category="files": open, reveal, search, list, info, make-folder, move, read,
and (Trash-only) delete of files & folders.

House style mirrors src/mac_actions.py and the runner contract exactly:
  * Every handler is a sync fn(**args) -> SHORT spoken-friendly string. It NEVER raises into
    the pipeline — every expected failure is caught and turned into a friendly string.
  * Caller/LLM-supplied paths never get string-interpolated into AppleScript or a shell line.
    The two handlers that reach AppleScript (reveal_in_finder, move_to_trash) pass the path as
    trailing argv via `on run argv`. The shell handlers (open, mdfind) pass paths as separate
    list args to run_shell — there is no shell, so no glob/quote/injection surface.
  * audit() on every action.
  * DELETION IS TRASH-ONLY. move_to_trash is Risk.CONFIRM and implemented via Finder's
    AppleScript `delete` (which moves to the Trash — recoverable). We NEVER use rm / os.remove
    / shutil.rmtree / any permanent delete anywhere in this module.

SECRETS HARD carve-out (docs/tooling/CONTRACT.md, non-negotiable): read_text_file REFUSES,
BY NAME PATTERN, any path that looks like a secret store (.env, .ssh/, *.pem, *.key,
*id_rsa*, *keychain*, *credential*, *secret*, *token*, *.p12). It refuses on the NAME before
ever opening the file, so the bytes are never read.
"""

import os
import re
import subprocess
from pathlib import Path

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_osa, run_shell

CATEGORY = "files"

# Absolute binaries (resolved via run_shell, but we keep the canonical paths explicit).
_OPEN = "/usr/bin/open"
_MDFIND = "/usr/bin/mdfind"

# read_text_file cap: never slurp more than this into a spoken context. 20 KB per the contract.
_READ_CAP_BYTES = 20 * 1024

# search_files hard ceiling regardless of the caller's requested limit.
_SEARCH_MAX = 50

# --- SECRETS carve-out -------------------------------------------------------------------
# We refuse to read anything whose path matches one of these patterns. The match is on the
# FULL expanded path string, lower-cased, so it catches `~/.ssh/id_rsa`, `/tmp/fake.env`,
# `My.Keychain`, `prod_token.txt`, etc. This is deny-by-NAME — we never open the file to
# decide; the pattern alone refuses. Mirrors the validator deny-list in the contract.
_SECRET_PATTERNS = (
    r"\.env(\.|$)",          # .env, .env.local, ...
    r"(^|/)\.env$",          # a file literally named .env
    r"/\.ssh/",              # anything under an .ssh directory
    r"\.pem$",
    r"\.key$",
    r"id_rsa",               # *id_rsa* anywhere (id_rsa, id_rsa.pub, my_id_rsa_backup)
    r"keychain",
    r"credential",
    r"secret",
    r"token",
    r"\.p12$",
    # Cloud / package / container credential stores.
    r"/\.aws/",              # ~/.aws/config and ~/.aws/credentials
    r"(^|/)\.netrc$",        # ~/.netrc
    r"/\.gnupg/",            # GPG keyring dir
    r"(^|/)\.npmrc$",        # npm auth token
    r"(^|/)\.pypirc$",       # PyPI upload creds
    r"/\.docker/config\.json$",   # docker registry auth
    r"/\.kube/config$",      # kubernetes credentials
    # Browser credential / cookie databases.
    r"login data",           # Chrome/Chromium saved logins
    r"key4\.db",             # Firefox NSS key store
    r"key3\.db",
    r"logins\.json",         # Firefox saved logins
    r"(^|/)cookies$",        # generic cookies store
    r"cookies\.sqlite",      # Firefox cookies
)
_SECRET_RE = re.compile("|".join(_SECRET_PATTERNS), re.IGNORECASE)

_REFUSE_SECRET = "I'm not allowed to read that one."


def _is_secret_path(expanded: str) -> bool:
    """True iff the (already ~-expanded) path string matches the secrets deny-list."""
    return bool(_SECRET_RE.search(expanded))


def _expand(path: str) -> str:
    """Expand ~ and environment vars to an absolute-ish path string. Pure string work — does
    NOT touch the filesystem, so it's safe to run even on a path we're about to refuse."""
    return os.path.expanduser(os.path.expandvars(str(path)))


def _shorten(path: str) -> str:
    """A short, spoken-friendly name for a path (the basename, or the path if no basename)."""
    p = str(path).rstrip("/")
    base = os.path.basename(p)
    return base or p


# ---------------------------------------------------------------------------------------
# open_path — SAFE
# ---------------------------------------------------------------------------------------
@tool(
    "open_path",
    "Open a file, folder, or app at the given path with its default application.",
    properties={"path": {"type": "string", "description": "The file or folder path to open."}},
    required=["path"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def open_path(path: str) -> str:
    p = _expand(path)
    if not p:
        msg = "Tell me what to open."
        audit("open_path", {"path": path}, msg)
        return msg
    if not os.path.exists(p):
        msg = f"I couldn't find {_shorten(p)}."
        audit("open_path", {"path": p}, msg)
        return msg
    try:
        # Path is a separate list arg — no shell, nothing to inject.
        run_shell([_OPEN, p])
        msg = f"Opened {_shorten(p)}."
        audit("open_path", {"path": p}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't open {_shorten(p)}."
        audit("open_path", {"path": p}, f"error: {e}")
        return msg


# ---------------------------------------------------------------------------------------
# reveal_in_finder — SAFE
# ---------------------------------------------------------------------------------------
@tool(
    "reveal_in_finder",
    "Reveal a file or folder in the Finder, selecting it.",
    properties={"path": {"type": "string", "description": "The path to reveal in Finder."}},
    required=["path"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def reveal_in_finder(path: str) -> str:
    p = _expand(path)
    if not p:
        msg = "Tell me what to reveal."
        audit("reveal_in_finder", {"path": path}, msg)
        return msg
    if not os.path.exists(p):
        msg = f"I couldn't find {_shorten(p)}."
        audit("reveal_in_finder", {"path": p}, msg)
        return msg
    try:
        # The caller path reaches AppleScript ONLY as trailing argv (on run argv) — never
        # interpolated. `reveal` + `activate` selects it in a Finder window.
        run_osa(
            "on run argv",
            "set p to POSIX file (item 1 of argv) as alias",
            'tell application "Finder" to reveal p',
            'tell application "Finder" to activate',
            "end run",
            args=[p],
        )
        msg = f"Revealed {_shorten(p)} in Finder."
        audit("reveal_in_finder", {"path": p}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't reveal {_shorten(p)}."
        audit("reveal_in_finder", {"path": p}, f"error: {e}")
        return msg


# ---------------------------------------------------------------------------------------
# search_files — SAFE
# ---------------------------------------------------------------------------------------
@tool(
    "search_files",
    "Search the Mac for files matching a query (Spotlight). Returns up to a few names.",
    properties={
        "query": {"type": "string", "description": "Words or filename to search for."},
        "limit": {"type": "integer", "description": "Max results to return (default 10)."},
    },
    required=["query"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def search_files(query: str, limit: int = 10) -> str:
    q = str(query).strip()
    if not q:
        msg = "Tell me what to search for."
        audit("search_files", {"query": query}, msg)
        return msg
    # Clamp the requested limit to [1, _SEARCH_MAX]; bad input collapses to a sane default.
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 10
    lim = max(1, min(_SEARCH_MAX, lim))
    try:
        # Query is a separate list arg to mdfind — no shell. mdfind prints one path per line.
        out = run_shell([_MDFIND, q])
    except subprocess.SubprocessError as e:
        msg = "Sorry, that search didn't work."
        audit("search_files", {"query": q}, f"error: {e}")
        return msg

    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        msg = f"I didn't find anything matching '{q}'."
        audit("search_files", {"query": q}, msg)
        return msg

    capped = lines[:lim]
    names = ", ".join(_shorten(ln) for ln in capped)
    total = len(lines)
    if total > len(capped):
        msg = f"Found {total} matches. Top {len(capped)}: {names}."
    else:
        msg = f"Found {total}: {names}."
    audit("search_files", {"query": q, "limit": lim, "total": total}, msg)
    return msg


# ---------------------------------------------------------------------------------------
# list_dir — SAFE
# ---------------------------------------------------------------------------------------
@tool(
    "list_dir",
    "List the contents of a folder (up to a few entries).",
    properties={"path": {"type": "string", "description": "The folder path to list."}},
    required=["path"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def list_dir(path: str) -> str:
    p = _expand(path)
    if not p:
        msg = "Tell me which folder to list."
        audit("list_dir", {"path": path}, msg)
        return msg
    if not os.path.exists(p):
        msg = f"I couldn't find {_shorten(p)}."
        audit("list_dir", {"path": p}, msg)
        return msg
    if not os.path.isdir(p):
        msg = f"{_shorten(p)} isn't a folder."
        audit("list_dir", {"path": p}, msg)
        return msg
    try:
        # Pure stdlib read — no shell, no AppleScript needed for a local listing.
        entries = sorted(os.listdir(p))
    except OSError as e:
        msg = f"Sorry, I couldn't read {_shorten(p)}."
        audit("list_dir", {"path": p}, f"error: {e}")
        return msg

    if not entries:
        msg = f"{_shorten(p)} is empty."
        audit("list_dir", {"path": p}, msg)
        return msg

    shown = entries[:15]
    listing = ", ".join(shown)
    if len(entries) > len(shown):
        msg = f"{len(entries)} items. First {len(shown)}: {listing}."
    else:
        msg = f"{len(entries)} items: {listing}."
    audit("list_dir", {"path": p, "count": len(entries)}, msg)
    return msg


# ---------------------------------------------------------------------------------------
# make_folder — SAFE
# ---------------------------------------------------------------------------------------
@tool(
    "make_folder",
    "Create a new folder at the given path (including any missing parent folders).",
    properties={"path": {"type": "string", "description": "The folder path to create."}},
    required=["path"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def make_folder(path: str) -> str:
    p = _expand(path)
    if not p:
        msg = "Tell me where to make the folder."
        audit("make_folder", {"path": path}, msg)
        return msg
    if os.path.isdir(p):
        msg = f"{_shorten(p)} already exists."
        audit("make_folder", {"path": p}, msg)
        return msg
    try:
        # Create dir (+ parents) via stdlib. This only ever CREATES — never removes anything.
        Path(p).mkdir(parents=True, exist_ok=True)
        msg = f"Created folder {_shorten(p)}."
        audit("make_folder", {"path": p}, msg)
        return msg
    except OSError as e:
        msg = f"Sorry, I couldn't create {_shorten(p)}."
        audit("make_folder", {"path": p}, f"error: {e}")
        return msg


# ---------------------------------------------------------------------------------------
# get_info — SAFE (size / kind / modified)
# ---------------------------------------------------------------------------------------
def _human_size(n: int) -> str:
    """Spoken-friendly size string."""
    units = ["bytes", "KB", "MB", "GB", "TB"]
    size = float(n)
    for u in units:
        if size < 1024 or u == units[-1]:
            if u == "bytes":
                return f"{int(size)} bytes"
            return f"{size:.1f} {u}"
        size /= 1024
    return f"{int(n)} bytes"


@tool(
    "get_info",
    "Report a file or folder's size, kind, and last-modified time.",
    properties={"path": {"type": "string", "description": "The path to describe."}},
    required=["path"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def get_info(path: str) -> str:
    p = _expand(path)
    if not p:
        msg = "Tell me which file to describe."
        audit("get_info", {"path": path}, msg)
        return msg
    if not os.path.exists(p):
        msg = f"I couldn't find {_shorten(p)}."
        audit("get_info", {"path": p}, msg)
        return msg
    try:
        st = os.stat(p)
        kind = "folder" if os.path.isdir(p) else "file"
        import time as _time

        modified = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(st.st_mtime))
        if kind == "folder":
            msg = f"{_shorten(p)} is a folder, last modified {modified}."
        else:
            msg = f"{_shorten(p)} is a file, {_human_size(st.st_size)}, last modified {modified}."
        audit("get_info", {"path": p}, msg)
        return msg
    except OSError as e:
        msg = f"Sorry, I couldn't read info for {_shorten(p)}."
        audit("get_info", {"path": p}, f"error: {e}")
        return msg


# ---------------------------------------------------------------------------------------
# move_file — SAFE
# ---------------------------------------------------------------------------------------
@tool(
    "move_file",
    "Move or rename a file or folder from one path to another.",
    properties={
        "src": {"type": "string", "description": "The source path to move."},
        "dst": {"type": "string", "description": "The destination path."},
    },
    required=["src", "dst"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def move_file(src: str, dst: str) -> str:
    s = _expand(src)
    d = _expand(dst)
    if not s or not d:
        msg = "Tell me the source and destination."
        audit("move_file", {"src": src, "dst": dst}, msg)
        return msg
    if not os.path.exists(s):
        msg = f"I couldn't find {_shorten(s)}."
        audit("move_file", {"src": s, "dst": d}, msg)
        return msg
    # If dst is an existing directory, move INTO it (preserve the source's basename) — the
    # natural "move X to folder Y" behaviour. Otherwise treat dst as the new full path.
    target = os.path.join(d, os.path.basename(s.rstrip("/"))) if os.path.isdir(d) else d
    if os.path.exists(target):
        msg = f"There's already something at {_shorten(target)}, so I didn't move it."
        audit("move_file", {"src": s, "dst": target}, msg)
        return msg
    try:
        # os.rename moves in-place (same volume) and never deletes data; we guarded against
        # clobbering an existing target above. Cross-volume moves fall back to shutil.move,
        # which copies-then-unlinks the SOURCE only (the destination is brand new) — still no
        # permanent delete of any pre-existing file.
        try:
            os.rename(s, target)
        except OSError:
            import shutil

            shutil.move(s, target)
        msg = f"Moved {_shorten(s)} to {_shorten(target)}."
        audit("move_file", {"src": s, "dst": target}, msg)
        return msg
    except OSError as e:
        msg = f"Sorry, I couldn't move {_shorten(s)}."
        audit("move_file", {"src": s, "dst": target}, f"error: {e}")
        return msg


# ---------------------------------------------------------------------------------------
# read_text_file — SAFE, but secrets-refusing + 20 KB cap + ~ expansion
# ---------------------------------------------------------------------------------------
@tool(
    "read_text_file",
    "Read the text contents of a small file (up to 20 KB). Refuses anything that looks like "
    "a secret or credential file.",
    properties={"path": {"type": "string", "description": "The text file path to read."}},
    required=["path"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def read_text_file(path: str) -> str:
    p = _expand(path)  # expand ~ FIRST so the secrets check sees the real path
    if not p:
        msg = "Tell me which file to read."
        audit("read_text_file", {"path": path}, msg)
        return msg

    # SECRETS carve-out: refuse BY NAME before opening the file. The bytes are never read.
    if _is_secret_path(p):
        audit("read_text_file", {"path": p}, "refused: secret-pattern")
        return _REFUSE_SECRET

    if not os.path.exists(p):
        msg = f"I couldn't find {_shorten(p)}."
        audit("read_text_file", {"path": p}, msg)
        return msg
    if os.path.isdir(p):
        msg = f"{_shorten(p)} is a folder, not a file."
        audit("read_text_file", {"path": p}, msg)
        return msg
    try:
        # Read at most the cap + 1 byte so we can tell whether it was truncated, decoding as
        # UTF-8 and replacing undecodable bytes (never raising on binary content).
        with open(p, "rb") as f:
            raw = f.read(_READ_CAP_BYTES + 1)
        truncated = len(raw) > _READ_CAP_BYTES
        text = raw[:_READ_CAP_BYTES].decode("utf-8", errors="replace").strip()
        if not text:
            msg = f"{_shorten(p)} is empty."
            audit("read_text_file", {"path": p}, msg)
            return msg
        if truncated:
            text += " ...(truncated at 20 KB)"
        audit("read_text_file", {"path": p, "truncated": truncated}, f"{len(text)} chars")
        return text
    except OSError as e:
        msg = f"Sorry, I couldn't read {_shorten(p)}."
        audit("read_text_file", {"path": p}, f"error: {e}")
        return msg


# ---------------------------------------------------------------------------------------
# move_to_trash — CONFIRM (Trash-only, via Finder `delete`; NEVER rm / permanent delete)
# ---------------------------------------------------------------------------------------
@tool(
    "move_to_trash",
    "Move a file or folder to the Trash (recoverable). Never permanently deletes.",
    properties={"path": {"type": "string", "description": "The path to move to the Trash."}},
    required=["path"],
    risk=Risk.CONFIRM,
    category=CATEGORY,
    confirm_summary=lambda path="": f"Move {path} to the Trash?",
)
def move_to_trash(path: str) -> str:
    p = _expand(path)
    if not p:
        msg = "Tell me what to move to the Trash."
        audit("move_to_trash", {"path": path}, msg)
        return msg
    if not os.path.exists(p):
        msg = f"I couldn't find {_shorten(p)}."
        audit("move_to_trash", {"path": p}, msg)
        return msg
    try:
        # Finder's `delete` moves the item to the Trash (RECOVERABLE). The caller path reaches
        # AppleScript only as trailing argv (on run argv) — never interpolated. We deliberately
        # do NOT use rm / os.remove / shutil.rmtree anywhere: deletion is Trash-only.
        run_osa(
            "on run argv",
            "set p to POSIX file (item 1 of argv) as alias",
            'tell application "Finder" to delete p',
            "end run",
            args=[p],
        )
        msg = f"Moved {_shorten(p)} to the Trash."
        audit("move_to_trash", {"path": p}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't move {_shorten(p)} to the Trash."
        audit("move_to_trash", {"path": p}, f"error: {e}")
        return msg
