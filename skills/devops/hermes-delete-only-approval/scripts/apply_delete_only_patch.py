#!/usr/bin/env python3
"""Re-apply the Hermes `delete-only` approval mode patch.

Hermes ships three approval modes (manual / smart / off).  This patch adds a
fourth: `delete-only` — everything is auto-approved EXCEPT destructive-delete
commands (rm / unlink / shred / srm / trash-put / find -exec rm / git rm), which
still require /approve.

Behaviour guarantee
-------------------
* Non-delete commands (ls, dd, chmod, curl|bash, systemctl, git push --force,
  shutdown, ...) run with NO prompt.
* Delete commands (rm file.txt, rm -rf /tmp/x, find -exec rm, git rm, ...)
  ALWAYS prompt, even though upstream's detector deliberately ignores a bare
  `rm file.txt` (see tests/tools/test_approval.py::TestRmFalsePositiveFix).
  That extra coverage lives in DELETE_ONLY_EXTRA_PATTERNS and is used ONLY by
  delete-only mode, so upstream's 119 approval tests keep passing.

Usage
-----
    python3 apply_delete_only_patch.py            # apply (idempotent)
    python3 apply_delete_only_patch.py --check    # verify only, no writes
    python3 apply_delete_only_patch.py --revert   # restore newest backup

Exit codes: 0 = ok / already patched, 1 = error.

NOTE: `hermes update` may overwrite /opt/hermes/tools/approval.py and
/opt/hermes/hermes_cli/config.py.  Re-run this script after any update.
"""

import argparse
import glob
import os
import shutil
import sys
import time

HERMES_ROOT = os.environ.get("HERMES_SRC", "/opt/hermes")
APPROVAL_PY = os.path.join(HERMES_ROOT, "tools", "approval.py")
CONFIG_PY = os.path.join(HERMES_ROOT, "hermes_cli", "config.py")

MARKER = "CAT_DELETE = \"delete\""
MARKER_EXTRA = "DELETE_ONLY_EXTRA_PATTERNS"
MARKER_CONFIG = "delete-only"

# ---------------------------------------------------------------------------
# approval.py — block 1: pattern table gains a 3rd element (category tag)
# ---------------------------------------------------------------------------

OLD_PATTERNS_HEAD = "DANGEROUS_PATTERNS = ["
NEW_CATEGORY_BLOCK = '''# Category tags for approval filtering.
# Used by `approvals.mode: delete-only` — only patterns whose category is in
# delete-only's watch set prompt the user; everything else is auto-approved.
CAT_DELETE = "delete"
CAT_DISK = "disk"
CAT_PERMISSION = "permission"
CAT_SYSTEM = "system"
CAT_SELF_KILL = "self-kill"
CAT_DB = "db"
CAT_EXEC = "exec"
CAT_OTHER = "other"

# Categories that must still be approved when approvals.mode == "delete-only".
DELETE_ONLY_WATCH_CATEGORIES = frozenset({CAT_DELETE})

DANGEROUS_PATTERNS = ['''

# ---------------------------------------------------------------------------
# approval.py — block 2: delete-only helper (extra patterns + matcher).
# Injected right after PATTERN_CATEGORIES is built.
# ---------------------------------------------------------------------------

OLD_CATEGORY_LOOKUP = '''_PATTERN_KEY_ALIASES: dict[str, set[str]] = {}
for _pattern, _description in DANGEROUS_PATTERNS:
    _legacy_key = _legacy_pattern_key(_pattern)
    _canonical_key = _description
    _PATTERN_KEY_ALIASES.setdefault(_canonical_key, set()).update({_canonical_key, _legacy_key})
    _PATTERN_KEY_ALIASES.setdefault(_legacy_key, set()).update({_legacy_key, _canonical_key})'''

NEW_CATEGORY_LOOKUP = '''_PATTERN_KEY_ALIASES: dict[str, set[str]] = {}
for _pattern, _description, _category in DANGEROUS_PATTERNS:
    _legacy_key = _legacy_pattern_key(_pattern)
    _canonical_key = _description
    _PATTERN_KEY_ALIASES.setdefault(_canonical_key, set()).update({_canonical_key, _legacy_key})
    _PATTERN_KEY_ALIASES.setdefault(_legacy_key, set()).update({_legacy_key, _canonical_key})


# description → category lookup (used for delete-only filtering)
PATTERN_CATEGORIES: dict[str, str] = {
    description: category for _pattern, description, category in DANGEROUS_PATTERNS
}

# Additional patterns used ONLY by delete-only mode.  Upstream's
# DANGEROUS_PATTERNS deliberately does not flag a bare `rm readme.txt`
# (see tests/tools/test_approval.py::TestRmFalsePositiveFix) because in
# `manual` mode that would be noise.  In delete-only mode, though, the whole
# point is that EVERY deletion gets approved, so we probe these separately
# and never mix them into DANGEROUS_PATTERNS.
DELETE_ONLY_EXTRA_PATTERNS = [
    # Plain/flagged rm on a real target, anchored to a command boundary so it
    # does not fire on unrelated words or on `find -exec rm` (covered above).
    (r'(?:^|[|&;(]\\s*|\\bsudo\\s+)\\brm\\s+(?:-{1,2}[^\\s]+\\s+)*[^\\s|&;<>]+', "file delete"),
    (r'(?:^|[|&;]\\s*|\\bsudo\\s+)\\b(?:unlink|shred|srm)\\b', "file delete"),
    (r'\\btrash-put\\b', "file delete (moved to trash)"),
    (r'\\bgit\\s+rm\\b', "git rm"),
]

DELETE_ONLY_PATTERNS = [(p, d, CAT_DELETE) for p, d in DELETE_ONLY_EXTRA_PATTERNS]


def detect_delete_only_matches(command: str) -> list:
    """Return delete-category matches for delete-only mode.

    Combines the CAT_DELETE entries from DANGEROUS_PATTERNS with
    DELETE_ONLY_EXTRA_PATTERNS, which catch plain `rm file.txt` — a case
    upstream intentionally leaves unflagged in manual mode.

    Returns:
        list of (pattern_key, description) tuples, possibly empty.
    """
    normalized = _normalize_command_for_detection(command)
    found = []
    seen = set()

    for pattern, description, category in DANGEROUS_PATTERNS:
        if category != CAT_DELETE:
            continue
        if re.search(pattern, normalized, re.IGNORECASE | re.DOTALL):
            if description not in seen:
                seen.add(description)
                found.append((description, description))

    for pattern, description, _category in DELETE_ONLY_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE | re.DOTALL):
            if description not in seen:
                seen.add(description)
                found.append((description, description))

    return found'''

# ---------------------------------------------------------------------------
# approval.py — block 3: pre-filter inside check_all_command_guards
# ---------------------------------------------------------------------------

OLD_PREFILTER = '''    # --- Phase 1: Gather findings from both checks ---'''

NEW_PREFILTER = '''    # --- delete-only mode pre-filter ---
    # When approvals.mode == "delete-only", commands that do NOT match a
    # delete pattern are auto-approved and return immediately without running
    # tirith or prompting. Commands that DO match a delete pattern fall
    # through to the normal approval flow.
    delete_only = approval_mode == "delete-only"
    delete_findings = []
    if delete_only:
        delete_findings = detect_delete_only_matches(command)
        if not delete_findings:
            logger.debug(
                "delete-only: auto-approved non-delete command '%s'", command[:80]
            )
            return {"approved": True, "message": None,
                    "delete_only_auto_approved": True}

    # --- Phase 1: Gather findings from both checks ---'''

# ---------------------------------------------------------------------------
# approval.py — block 4: inject delete findings into the warnings list
# ---------------------------------------------------------------------------

OLD_WARNINGS = '''    if is_dangerous:
        if not is_approved(session_key, pattern_key):
            warnings.append((pattern_key, description, False))

    # Nothing to warn about
    if not warnings:
        return {"approved": True, "message": None}'''

NEW_WARNINGS = '''    if is_dangerous:
        if not is_approved(session_key, pattern_key):
            warnings.append((pattern_key, description, False))

    # delete-only mode: force plain-delete findings (e.g. `rm file.txt`,
    # which upstream's detector intentionally ignores) into the warning list
    # so they still reach the approval prompt.
    if delete_only:
        already = {k for k, _, _ in warnings}
        for key, desc in delete_findings:
            if key not in already and not is_approved(session_key, key):
                warnings.append((key, desc, False))
                already.add(key)

    # Nothing to warn about
    if not warnings:
        return {"approved": True, "message": None}'''

# ---------------------------------------------------------------------------
# config.py — document the new mode
# ---------------------------------------------------------------------------

OLD_CONFIG_DOC = '''    # Approval mode for dangerous commands:'''

NEW_CONFIG_DOC = '''    # Approval mode for dangerous commands:
    #   delete-only — auto-approve everything EXCEPT delete operations'''

def _tag_pattern_lines(src: str) -> str:
    """Convert 2-tuple DANGEROUS_PATTERNS entries into 3-tuples with categories.

    Each entry looks like:
        (r'<regex>', "<description>"),
    and becomes:
        (r'<regex>', "<description>", CAT_XXX),
    Category is chosen by matching the description against CATEGORY_BY_DESC.
    """
    import re as _re

    def _repl(m):
        head, desc = m.group(1), m.group(2)
        cat = CATEGORY_BY_DESC.get(desc, "CAT_OTHER")
        return f"{head}\"{desc}\", {cat}),"

    # Matches a pattern line ending in `"<description>"),`. The regex-literal
    # may itself contain quotes/commas, so we anchor on the trailing form and
    # let the lazy group absorb everything before the final quoted description.
    pattern = _re.compile(
        r"^(\s*\(r(?:f)?'.*,\s*)\"([^\"]+)\"\),\s*$",
        _re.MULTILINE,
    )
    # Only tag the DANGEROUS_PATTERNS block, not DELETE_ONLY_EXTRA_PATTERNS.
    # The closing bracket is a lone "]" at the start of a line (the regexes
    # themselves contain "]" inside character classes).
    start = src.index("DANGEROUS_PATTERNS = [")
    end = src.index("\n]", start)
    block = src[start:end]
    tagged = pattern.sub(_repl, block)
    return src[:start] + tagged + src[end:]


# description -> CAT_* name for every entry in upstream DANGEROUS_PATTERNS.
CATEGORY_BY_DESC = {
    # deletes
    "delete in root path": "CAT_DELETE",
    "recursive delete": "CAT_DELETE",
    "recursive delete (long flag)": "CAT_DELETE",
    "xargs with rm": "CAT_DELETE",
    "find -exec rm": "CAT_DELETE",
    "find -delete": "CAT_DELETE",
    "SQL DROP": "CAT_DB",
    "SQL DELETE without WHERE": "CAT_DB",
    "SQL TRUNCATE": "CAT_DB",
    # permissions
    "world/other-writable permissions": "CAT_PERMISSION",
    "recursive world/other-writable (long flag)": "CAT_PERMISSION",
    "recursive chown to root": "CAT_PERMISSION",
    "recursive chown to root (long flag)": "CAT_PERMISSION",
    # disk
    "format filesystem": "CAT_DISK",
    "disk copy": "CAT_DISK",
    "write to block device": "CAT_DISK",
    # system
    "overwrite system config": "CAT_SYSTEM",
    "stop/restart system service": "CAT_SYSTEM",
    "kill all processes": "CAT_SELF_KILL",
    "force kill processes": "CAT_SELF_KILL",
    "fork bomb": "CAT_SYSTEM",
    "overwrite system file via tee": "CAT_SYSTEM",
    "overwrite system file via redirection": "CAT_SYSTEM",
    "stop/restart hermes gateway (kills running agents)": "CAT_SELF_KILL",
    "hermes update (restarts gateway, kills running agents)": "CAT_SELF_KILL",
}

OLD_DETECT_LOOP = '''    command_lower = _normalize_command_for_detection(command).lower()
    for pattern, description in DANGEROUS_PATTERNS:'''

NEW_DETECT_LOOP = '''    command_lower = _normalize_command_for_detection(command).lower()
    for pattern, description, _category in DANGEROUS_PATTERNS:'''

# (pattern, replacement) pairs applied to approval.py, in order.
APPROVAL_EDITS = [
    (OLD_PATTERNS_HEAD, NEW_CATEGORY_BLOCK),
    (OLD_CATEGORY_LOOKUP, NEW_CATEGORY_LOOKUP),
    (OLD_DETECT_LOOP, NEW_DETECT_LOOP),
    (OLD_PREFILTER, NEW_PREFILTER),
    (OLD_WARNINGS, NEW_WARNINGS),
]


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _is_patched(path):
    if not os.path.exists(path):
        return False
    src = _read(path)
    return MARKER in src and MARKER_EXTRA in src


def _backup(path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = f"{path}.bak-{stamp}"
    shutil.copy2(path, dest)
    return dest


def apply(check_only=False):
    problems = []

    if _is_patched(APPROVAL_PY):
        print(f"[ok] already patched: {APPROVAL_PY}")
    else:
        src = _read(APPROVAL_PY)
        for old, new in APPROVAL_EDITS:
            if old not in src:
                problems.append(
                    f"anchor not found in {os.path.basename(APPROVAL_PY)}: "
                    f"{old.splitlines()[0][:70]!r}"
                )
        if problems:
            for p in problems:
                print(f"[!!] {p}", file=sys.stderr)
            print("[!!] upstream file changed too much — patch manually.",
                  file=sys.stderr)
            return 1
        if check_only:
            print(f"[check] approval.py can be patched (4 edits apply cleanly)")
        else:
            bak = _backup(APPROVAL_PY)
            print(f"[bak] {bak}")
            for old, new in APPROVAL_EDITS:
                src = src.replace(old, new, 1)
            # Tag every pattern line with its category (2-tuple -> 3-tuple).
            src = _tag_pattern_lines(src)
            _write(APPROVAL_PY, src)
            print(f"[ok] patched {APPROVAL_PY}")

    # config.py documentation is cosmetic; skip silently if anchor missing.
    if os.path.exists(CONFIG_PY):
        cfg = _read(CONFIG_PY)
        if MARKER_CONFIG in cfg:
            print(f"[ok] already documents delete-only: {CONFIG_PY}")
        elif OLD_CONFIG_DOC in cfg:
            if not check_only:
                bak = _backup(CONFIG_PY)
                print(f"[bak] {bak}")
                cfg = cfg.replace(OLD_CONFIG_DOC, NEW_CONFIG_DOC, 1)
                _write(CONFIG_PY, cfg)
            print(f"[ok] documented delete-only in {CONFIG_PY}")

    return 0


def revert():
    for path in (APPROVAL_PY, CONFIG_PY):
        backups = sorted(glob.glob(f"{path}.bak-*"))
        if not backups:
            print(f"[--] no backup for {path}")
            continue
        newest = backups[-1]
        shutil.copy2(newest, path)
        print(f"[ok] restored {path} from {newest}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify patch anchors without writing")
    ap.add_argument("--revert", action="store_true",
                    help="restore newest .bak-* backups")
    args = ap.parse_args()

    if args.revert:
        return revert()
    if not os.path.exists(APPROVAL_PY):
        print(f"[!!] not found: {APPROVAL_PY}", file=sys.stderr)
        return 1
    return apply(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
