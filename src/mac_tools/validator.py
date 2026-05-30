"""
validator.py — AST/static security validation of factory-generated tool code.

The factory (factory.py) asks Z.AI/GLM to author a brand-new Mac tool mid-call. We must
NEVER trust that generated code blindly — the factory IMPORTS the module (Python runs every
top-level statement at import), and it runs on the owner's machine with the same privileges as
the daemon. `validate_tool_code` is the gate that decides whether a candidate module is safe to
write + import.

DESIGN: STRUCTURAL ALLOWLIST, not a denylist (the old denylist was trivially bypassed — see
docs/tooling/reviews/security.md). A module only PASSES if its *shape* is provably safe to
import:

  * TOP-LEVEL ALLOWLIST: the module body may contain ONLY a docstring, allowlisted imports,
    function/async-function definitions, and assignments whose value is a literal constant (or a
    literal list/dict/tuple/set of constants). ANYTHING else at module level — a bare call, a
    non-docstring expression, If/For/While/With/Try/ClassDef/Lambda — is REJECTED. This kills
    import-time side effects: there is no top-level code path that can DO anything at import.
  * IMPORT ALLOWLIST: only `mac_tools.runner` (run_osa/run_shell/audit/clamp/app_is_running/
    frontmost_app), `mac_tools.registry.tool`, `mac_tools.policy.Risk`, and a tiny pure-data
    stdlib subset (re/json/math/time/datetime/urllib.parse/textwrap/string/html). Everything
    else — os, subprocess, io, pathlib, urllib.request/error, socket, http, requests,
    importlib, ctypes, pickle, etc. — is REJECTED. Generated tools touch the system ONLY through
    mac_tools.runner.
  * NAME BANS (anywhere): eval/exec/compile/__import__/getattr/setattr/delattr/vars/globals/
    locals/input/breakpoint/open/memoryview/__builtins__, ANY dunder attribute access
    (`__globals__`, `__subclasses__`, `__class__`, ...), and subscripting of __builtins__ /
    globals(). These are the introspection/escape primitives a denylist can't otherwise pin
    down once string-concatenation is in play.
  * SECRETS HARD carve-out (non-negotiable, owner: "never"): we CONSTANT-FOLD adjacent string
    concatenations of literals FIRST (so `"~/.s" + "sh/id_rsa"` becomes one literal), then scan
    every folded string literal for secret markers (ssh/gpg keys, .env, ~/.aws, ~/.netrc,
    .gnupg, *.pem/*.key, login.keychain, browser credential DBs, .npmrc/.pypirc/.docker/.kube,
    etc.). Any hit REJECTS the whole module.
  * RISK CLASSIFICATION (not rejection): send/delete(Trash)/disruptive/network-toggle, a
    non-allowlisted network host literal, or a self-declared Risk.CONFIRM/gated_if_generated →
    RISKY → the factory registers the tool DISABLED / CONFIRM-gated pending owner approval.

`validate_tool_code(code) -> (ok, reason, meta)`:
    ok    : bool  — True iff the module is safe to write+import.
    reason: str   — human/spoken-friendly explanation (esp. on rejection).
    meta  : dict  — {"risky": bool, "tool_names": [...], "reasons": [...risky reasons...]}.

Pure-stdlib (`ast` + `re`); NEVER executes the candidate code. Default-deny on ANY internal
or parse error.
"""

import ast
import re

# --------------------------------------------------------------------------------------------
# IMPORT ALLOWLIST. Anything not here is rejected outright. We allowlist EXACT dotted module
# paths (so a dangerous submodule of an otherwise-fine root — e.g. urllib.request — can't ride
# in on the root's name). For mac_tools we allowlist the three safe submodules; everything is
# enforced together with the per-name import check below.
# --------------------------------------------------------------------------------------------

# Pure-data stdlib modules a tool may import (no I/O, no network, no process control).
_ALLOWED_STDLIB = {
    "re",
    "json",
    "math",
    "time",
    "datetime",
    "textwrap",
    "string",
    "html",
    "urllib.parse",   # EXACT — urllib.request/error/etc. are NOT allowed
}

# The project's own safe surface. Generated tools reach the system ONLY through these.
_ALLOWED_MACTOOLS = {
    "mac_tools.runner",
    "mac_tools.registry",
    "mac_tools.policy",
}

# Symbols allowed in `from <module> import <name>` for each project module. (We don't restrict
# stdlib symbol-imports beyond the module allowlist — those modules are pure-data.)
_ALLOWED_FROM_RUNNER = {
    "run_osa", "run_shell", "audit", "clamp", "app_is_running", "frontmost_app",
}
_ALLOWED_FROM_REGISTRY = {"tool"}
_ALLOWED_FROM_POLICY = {"Risk"}

# --------------------------------------------------------------------------------------------
# BANNED NAMES (used anywhere — as an ast.Name id OR an ast.Attribute attr). These are the
# code-exec / introspection / file-open / escape primitives.
# --------------------------------------------------------------------------------------------
_BANNED_NAMES = {
    "eval", "exec", "compile", "__import__",
    "getattr", "setattr", "delattr",
    "vars", "globals", "locals",
    "input", "breakpoint",
    "open", "memoryview",
    "__builtins__",
}

# Dunder attribute access (e.g. obj.__globals__, cls.__subclasses__, x.__class__) is a classic
# sandbox escape — ban any attribute whose name is a dunder.
_DUNDER_ATTR_RE = re.compile(r"^__.+__$")

# --------------------------------------------------------------------------------------------
# SECRETS — HARD carve-out. Scanned against CONSTANT-FOLDED string literals (so split strings
# can't hide a secret path). ANY hit rejects the whole module. (owner: "never")
# --------------------------------------------------------------------------------------------
_SECRET_DENY = [
    r"keychain",
    r"login\.keychain",
    r"security\s+find-generic-password",
    r"find-generic-password",
    # SSH / private keys
    r"\.ssh/",
    r"\.ssh\\",
    r"id_rsa",
    r"id_ed25519",
    r"id_dsa",
    r"id_ecdsa",
    r"\.pem\b",
    r"\.key\b",
    r"\.p12\b",
    r"\.pfx\b",
    r"\.p8\b",
    r"\.ppk\b",
    r"\.jks\b",
    r"\bprivate[_ -]?key\b",
    # GPG
    r"\.gnupg",
    r"\bgpg\b",
    r"gnupg",
    # dotfile credential stores
    r"\.env\b",
    r"\.netrc\b",
    r"~?/?\.aws\b",
    r"\.aws/",
    r"\.npmrc\b",
    r"\.pypirc\b",
    r"\.docker/config\.json",
    r"\.kube/config",
    r"\.config/gh/hosts",
    # browser / app credential databases
    r"login data",          # Chrome/Chromium
    r"key4\.db",            # Firefox NSS
    r"key3\.db",
    r"logins\.json",        # Firefox
    r"cookies\.sqlite",
    # generic credential words
    r"\bpassword\b",
    r"\bpasswd\b",
    r"\bsecret\b",
    r"\btoken\b",
    r"\bcredential",
    r"\bapi[_-]?key\b",
    r"\baws_secret",
    r"\bbearer\b",
]

# Network-host allowlist. A literal URL/host NOT on this list is flagged RISKY (the factory
# gates the tool) — not an automatic reject, but it can't auto-activate either.
_HOST_ALLOWLIST = {
    "localhost",
    "127.0.0.1",
    "build.nvidia.com",
    "api.z.ai",
}

# Risk signal words: presence (in source text) means the tool is likely send/delete/disruptive
# /network-toggle and so must be CONFIRM-gated when generated.
_RISK_SIGNALS = {
    "send": [r"\bsend\b", r"\bcompose\b", r"\bemail\b", r"\bimessage\b", r"\bmessage\b",
             r"\bslack\b", r"\bpost\b", r"\btweet\b", r"\bsms\b", r"\bmail\b"],
    "delete": [r"\btrash\b", r"\bdelete\b", r"move to trash", r"\bdiscard\b"],
    "disruptive": [r"\bsleep\b", r"\block\b", r"\blogout\b", r"\blog out\b", r"\brestart\b",
                   r"\bshut ?down\b", r"\breboot\b", r"\bquit\b", r"\bkill\b", r"\bterminate\b"],
    "network_toggle": [r"\bwi-?fi\b", r"\bbluetooth\b", r"\bairport\b", r"networksetup",
                       r"\bairplane\b"],
}


def _scan_raw(text: str, patterns) -> str | None:
    """Return the first matching pattern (the trigger) or None. Case-insensitive."""
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return pat
    return None


# --------------------------------------------------------------------------------------------
# Constant-folding of adjacent string concatenations (BinOp Add of str Constants), so a secret
# path split across `"~/.s" + "sh/id_rsa"` is reconstructed BEFORE the secret scan.
# --------------------------------------------------------------------------------------------
def _fold_str(node: ast.AST) -> str | None:
    """If `node` is a string Constant, or an Add-chain of string Constants, return the folded
    string. Otherwise None (mixed/dynamic expression — can't fold)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_str(node.left)
        right = _fold_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _collect_string_literals(tree: ast.AST) -> list[str]:
    """All string literals in the tree, INCLUDING folded Add-chains of string constants, so the
    split-string secret bypass is caught. Returns the list of folded/standalone strings."""
    out: list[str] = []
    for node in ast.walk(tree):
        # Folded concatenations (covers the split-string bypass).
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            folded = _fold_str(node)
            if folded is not None:
                out.append(folded)
        # Every standalone string constant.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def _decorator_is_tool(dec: ast.AST) -> bool:
    """True if a decorator expression is the @tool decorator (bare `@tool` or `@tool(...)`,
    possibly attribute-accessed like `@registry.tool` / `@mac_tools.tool`)."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id == "tool"
    if isinstance(target, ast.Attribute):
        return target.attr == "tool"
    return False


def _extract_tool_meta(tree: ast.Module):
    """Find @tool-decorated TOP-LEVEL functions; return their declared tool names (from the
    decorator's `name=` kwarg or first positional arg, falling back to the function name)."""
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tool_decs = [d for d in node.decorator_list if _decorator_is_tool(d)]
            if not tool_decs:
                continue
            tname = None
            dec = tool_decs[0]
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        tname = kw.value.value
                if tname is None and dec.args:
                    first = dec.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        tname = first.value
            if tname is None:
                tname = node.name
            names.append(str(tname))
    return names


# --------------------------------------------------------------------------------------------
# Top-level structural allowlist.
# --------------------------------------------------------------------------------------------
def _is_docstring_expr(node: ast.AST) -> bool:
    return (isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def _is_literal_constant(node: ast.AST) -> bool:
    """True if `node` is a literal constant, or a list/dict/tuple/set of literal constants
    (recursively). NO calls, names, comprehensions, or operators allowed."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal_constant(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (k is None or _is_literal_constant(k)) and _is_literal_constant(v)
            for k, v in zip(node.keys, node.values)
        )
    # Allow a unary minus on a numeric constant (e.g. -1) for ergonomics.
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_literal_constant(node.operand)
    return False


def _check_top_level(tree: ast.Module) -> str | None:
    """Walk the MODULE BODY and reject anything that isn't a docstring, an import, a
    function/async-function def, or a literal-valued assignment. This is the keystone: it makes
    importing the module a no-op (no import-time side effects). Returns a reject reason or None.
    """
    for i, node in enumerate(tree.body):
        if i == 0 and _is_docstring_expr(node):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            # AnnAssign may have no value (`x: int`); a bare annotation is harmless.
            if value is None or _is_literal_constant(value):
                continue
            return ("module-level assignment whose value isn't a literal constant "
                    "(no computed/import-time values allowed)")
        # Everything else at module level is forbidden: bare calls, non-docstring expressions,
        # If/For/While/With/Try/ClassDef, etc. — i.e. import-time side effects.
        kind = type(node).__name__
        return (f"forbidden top-level statement ({kind}); the module body may contain only a "
                "docstring, imports, function definitions, and constant assignments")
    return None


# --------------------------------------------------------------------------------------------
# Import allowlist (exact dotted paths) + per-symbol checks for the project modules.
# --------------------------------------------------------------------------------------------
def _check_imports(tree: ast.Module) -> str | None:
    """Enforce the import allowlist. Imports are top-level (guaranteed by _check_top_level) but
    we walk the whole tree defensively in case a function-local import slips by. Returns a reject
    reason or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                full = alias.name
                if full in _ALLOWED_STDLIB or full in _ALLOWED_MACTOOLS:
                    continue
                # `import urllib` (root) is NOT allowed — only the exact `urllib.parse`.
                return (f"imports {full!r}, which isn't on the allowlist "
                        "(generated tools may only use safe stdlib + mac_tools.runner/"
                        "registry/policy)")
        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            mod = node.module or ""
            if level > 0:
                # Relative import inside the package — resolve to mac_tools.<mod>.
                full = f"mac_tools.{mod}" if mod else "mac_tools"
            else:
                full = mod
            names = {a.name for a in node.names}
            if full in _ALLOWED_MACTOOLS:
                if full == "mac_tools.runner" and not names <= _ALLOWED_FROM_RUNNER:
                    bad = sorted(names - _ALLOWED_FROM_RUNNER)
                    return (f"imports {bad} from mac_tools.runner, which isn't an allowed "
                            "runner symbol (use run_osa/run_shell/audit/clamp/app_is_running/"
                            "frontmost_app)")
                if full == "mac_tools.registry" and not names <= _ALLOWED_FROM_REGISTRY:
                    return "imports a disallowed symbol from mac_tools.registry (use tool only)"
                if full == "mac_tools.policy" and not names <= _ALLOWED_FROM_POLICY:
                    return "imports a disallowed symbol from mac_tools.policy (use Risk only)"
                continue
            if full in _ALLOWED_STDLIB:
                continue
            # `from urllib import parse` — the source module is `urllib`, allow ONLY if the only
            # name imported is `parse`.
            if full == "urllib" and names == {"parse"}:
                continue
            return (f"imports from {full!r}, which isn't on the allowlist "
                    "(generated tools may only use safe stdlib + mac_tools.runner/registry/"
                    "policy)")
    return None


# --------------------------------------------------------------------------------------------
# Banned-name pass: eval/getattr/__builtins__/open/... as Name or Attribute, dunder attribute
# access, and subscripting of __builtins__/globals().
# --------------------------------------------------------------------------------------------
def _check_names(tree: ast.Module) -> str | None:
    """Reject any banned name (as a Name id or Attribute attr), any dunder attribute access, and
    any subscript of __builtins__ / globals(). Returns a reject reason or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            return f"uses banned name {node.id!r}"
        if isinstance(node, ast.Attribute):
            if node.attr in _BANNED_NAMES:
                return f"uses banned attribute {node.attr!r}"
            if _DUNDER_ATTR_RE.match(node.attr):
                return f"uses dunder attribute access {node.attr!r} (sandbox-escape primitive)"
        # Subscript of __builtins__["..."] or globals()[...].
        if isinstance(node, ast.Subscript):
            base = node.value
            if isinstance(base, ast.Name) and base.id in {"__builtins__", "globals"}:
                return f"subscripts {base.id!r} (sandbox-escape primitive)"
            if (isinstance(base, ast.Call) and isinstance(base.func, ast.Name)
                    and base.func.id in {"globals", "vars", "locals"}):
                return f"subscripts {base.func.id}() (sandbox-escape primitive)"
        # Any call to shell=True (defense in depth — subprocess can't be imported anyway).
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (kw.arg == "shell" and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True):
                    return "calls a subprocess with shell=True"
    return None


def _check_network_hosts(strings: list[str]) -> list[str]:
    """Find literal URLs / hostnames in folded string literals that aren't on the host
    allowlist. Returns risky-reason strings (empty if none). Does NOT reject — non-allowlisted
    hosts are treated as RISKY so the factory gates the tool."""
    risky = []
    url_re = re.compile(r"https?://([^/\s\"']+)", re.IGNORECASE)
    for s in strings:
        for m in url_re.finditer(s):
            host = m.group(1).split(":")[0].lower()
            if host not in _HOST_ALLOWLIST:
                risky.append(f"references non-allowlisted host {host!r}")
    return sorted(set(risky))


def validate_tool_code(code: str) -> tuple[bool, str, dict]:
    """Statically validate a candidate generated tool module via a STRUCTURAL ALLOWLIST.

    Returns (ok, reason, meta):
      ok    : bool  — safe to write+import? (a PASS guarantees import is side-effect-free)
      reason: str   — friendly explanation (rejection cause, or accept/risk note)
      meta  : dict  — {"risky": bool, "tool_names": [...], "reasons": [...]}.

    Never executes the code. On ANY internal/parse error, default-deny (ok=False).
    """
    meta = {"risky": False, "tool_names": [], "reasons": []}

    try:
        if not isinstance(code, str) or not code.strip():
            return False, "No code was produced.", meta

        # 1) Must parse.
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Rejected: the generated code didn't parse ({e.msg}).", meta
        if not isinstance(tree, ast.Module):
            return False, "Rejected: not a module.", meta

        # 2) STRUCTURAL top-level allowlist — kills import-time side effects (the keystone).
        reason = _check_top_level(tree)
        if reason:
            return False, f"Rejected: {reason}.", meta

        # 3) Import allowlist (exact dotted paths + per-symbol for project modules).
        reason = _check_imports(tree)
        if reason:
            return False, f"Rejected: {reason}.", meta

        # 4) Banned names / dunder attrs / __builtins__|globals() subscripts / shell=True.
        reason = _check_names(tree)
        if reason:
            return False, f"Rejected: {reason}.", meta

        # 5) SECRETS HARD carve-out — constant-fold string concatenations FIRST, then scan.
        folded_strings = _collect_string_literals(tree)
        for s in folded_strings:
            if _scan_raw(s, _SECRET_DENY):
                return (False,
                        "Rejected: the generated tool touches secrets/credentials, which is "
                        "never allowed.",
                        meta)

        # 6) Must define at least one @tool function (top-level).
        tool_names = _extract_tool_meta(tree)
        if not tool_names:
            return (False,
                    "Rejected: no @tool-decorated function was defined, so there's nothing to "
                    "register.",
                    meta)
        meta["tool_names"] = tool_names

        # 7) RISK CLASSIFICATION (does NOT reject — flags so the factory gates it). Keyword scan
        #    over raw source PLUS folded-string host check PLUS self-declared CONFIRM.
        risky_reasons: list[str] = []
        for label, pats in _RISK_SIGNALS.items():
            if _scan_raw(code, pats):
                risky_reasons.append(f"{label} action detected")
        risky_reasons.extend(_check_network_hosts(folded_strings))
        if re.search(r"risk\s*=\s*Risk\.CONFIRM", code) or re.search(
            r"gated_if_generated\s*=\s*True", code
        ):
            risky_reasons.append("declares itself CONFIRM/gated")

        if risky_reasons:
            meta["risky"] = True
            meta["reasons"] = sorted(set(risky_reasons))
            return (True,
                    "Accepted, but classified RISKY (will be gated for owner approval): "
                    + "; ".join(meta["reasons"])
                    + ".",
                    meta)

        return True, "Accepted: safe tool, no risky operations detected.", meta

    except Exception as e:  # noqa: BLE001 — NEVER raise out of the validator; default-deny.
        return False, f"Rejected: validation error ({e}).", meta
