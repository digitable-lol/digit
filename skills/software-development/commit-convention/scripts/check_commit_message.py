#!/usr/bin/env python3
"""Check a commit message against the house convention — and against its own diff.

Two kinds of finding, deliberately separated:

* **Style** — decidable from the message alone (length, trailing period, blank
  line, low-information wording). These are errors; exit code 1.
* **Grounding** — facts the message states that do not appear in the diff. These
  are *not* errors. A commit body is supposed to say why, and the why routinely
  lives outside the diff: a CI run you watched, an outage you saw, a decision you
  made. The check cannot tell a fact you observed from one you invented, so it
  prints them and asks. Exit code stays 0.

The grounding check exists because that is the measured failure mode. Across 266
house commits with a real body, a mean 54% of the body's content words appear
nowhere in the diff; a quarter of the numbers and 43% of the identifiers in those
messages are absent from the change they describe. Anything generating a message
from the diff alone must invent that half. This script makes the invention
visible instead of letting it ship as a confident measurement.

Usage:
    check_commit_message.py --message-file MSG [--diff-file DIFF]
    check_commit_message.py --message-file MSG --staged      # diff from git
    git diff --cached | check_commit_message.py --message-file MSG --diff-file -
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import List, Tuple

# --- measured from 554 non-merge house commits (digit, courses, flang-v6) ---
SUBJECT_SOFT_LIMIT = 80   # p90 = 78; only 10% of house commits exceed this
SUBJECT_HARD_LIMIT = 95   # observed maximum
BODY_WRAP_SOFT = 84       # p90 of longest body line = 81

#: Subjects that describe the commit's paperwork instead of the change.
#: Every one of these is a real string autocomitter emits; none appears in the
#: house corpus.
LOW_INFORMATION = {
    "update", "updates", "update code", "update documentation",
    "update existing code", "update project files", "update configuration",
    "update configuration settings", "add new functionality", "add new features",
    "add multiple new features", "enhance core functionality", "fix", "fixes",
    "bug fix", "wip", "misc", "cleanup", "changes", "various changes",
    "add python functionality", "resolve issue", "resolve specific issue",
    "remove unused code", "add new test cases", "add new imports",
    "фикс", "правка", "правки", "обновление", "изменения", "мелочи",
}

CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|chore|ci|build|revert)"
    r"(\([^()]+\))?!?: (?P<rest>.+)$"
)

# Numbers of 2+ digits, and code-shaped identifiers: the parts of a message that
# assert a specific checkable fact.
NUMBER = re.compile(r"(?<![\w.])\d{2,}(?![\w])")
IDENTIFIER = re.compile(
    r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:[./-][a-zA-Z0-9_]+)+\b"   # a/b, a.b, a-b, paths
    r"|\b[a-z]+[A-Z][a-zA-Z0-9]*\b"                          # camelCase
    r"|\b[a-z][a-z0-9]*_[a-z0-9_]+\b"                        # snake_case
)

# Bare words that look like identifiers but carry no claim.
IDENT_STOPWORDS = {
    "e.g", "i.e", "etc", "self", "true", "false", "none", "null",
}


def read_diff(args: argparse.Namespace) -> str:
    if args.staged:
        out = subprocess.run(
            ["git", "diff", "--cached"], capture_output=True, text=True
        )
        return out.stdout
    if args.diff_file == "-":
        return sys.stdin.read()
    if args.diff_file:
        with open(args.diff_file, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return ""


def split_message(text: str) -> Tuple[str, List[str]]:
    lines = text.replace("\r\n", "\n").rstrip().split("\n")
    # A trailing comment block (git's commit template) is not part of the message.
    lines = [l for l in lines if not l.startswith("#")]
    while lines and not lines[-1].strip():
        lines.pop()
    subject = lines[0].strip() if lines else ""
    return subject, lines[1:]


#: `add <symbol> function` / `add <Symbol> class` — the subject restates the diff
#: instead of saying what the change makes true. Autocomitter emits these; the
#: house corpus never does.
RESTATES_DIFF = re.compile(
    r"^(?:add|added|adds|update|updated|remove|removed)\s+"
    r"(?P<sym>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?:function|class|method|file|module)$",
    re.IGNORECASE,
)


def check_style(subject: str, body: List[str]) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings).

    Errors are decidable from the text: nothing about the change could make them
    right. Warnings are shape advice the corpus supports but does not settle —
    8.5% of the owner's own subjects run past the soft length limit, so failing
    on it would reject the very style this file encodes.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not subject:
        return ["the message is empty"], []

    # A Conventional-Commits prefix is allowed (21.5% of house commits use one)
    # but never required. What is judged is the sentence either way.
    match = CONVENTIONAL.match(subject)
    payload = match.group("rest") if match else subject
    naked = payload.strip().rstrip(".")

    if subject.endswith("."):
        errors.append("subject ends with a period (0 of 554 house commits do)")

    if " | " in subject:
        errors.append(
            "subject joins several messages with ' | ' — one commit, one claim. "
            "Split the change into separate commits instead."
        )

    if naked.lower() in LOW_INFORMATION:
        errors.append(
            f"subject says only {naked!r} — that names the paperwork, not the "
            f"change. Say what became true that was not true before."
        )

    if RESTATES_DIFF.match(naked):
        errors.append(
            f"subject {naked!r} restates the diff: the reader can see a symbol was "
            f"added. Say what it lets the code do, or what it stops going wrong."
        )

    if len(subject) > SUBJECT_HARD_LIMIT:
        errors.append(
            f"subject is {len(subject)} chars, past the {SUBJECT_HARD_LIMIT}-char "
            f"maximum seen in the corpus — split the thought, or move half to the body"
        )
    elif len(subject) > SUBJECT_SOFT_LIMIT:
        warnings.append(
            f"subject is {len(subject)} chars; 90% of house commits fit in "
            f"{SUBJECT_SOFT_LIMIT}"
        )

    if match and payload and payload[0].isupper() and not payload[:2].isupper():
        warnings.append(
            "a Conventional-Commits summary usually starts lower-case after the colon"
        )

    if body and body[0].strip():
        errors.append("body must be separated from the subject by a blank line")

    for i, line in enumerate(body, start=2):
        if len(line) > BODY_WRAP_SOFT and " " in line.strip():
            warnings.append(
                f"body line {i} is {len(line)} chars; house bodies wrap near "
                f"{BODY_WRAP_SOFT}"
            )
            break

    return errors, warnings


def check_grounding(message: str, diff: str) -> Tuple[List[str], List[str]]:
    """Return (numbers, identifiers) asserted by the message but absent from the diff."""
    if not diff.strip():
        return [], []

    # Compare against the diff's added/removed/context text plus its file paths.
    haystack = diff

    nums = []
    for n in dict.fromkeys(NUMBER.findall(message)):
        if n not in haystack:
            nums.append(n)

    idents = []
    for m in dict.fromkeys(x.group(0) for x in IDENTIFIER.finditer(message)):
        if m.lower() in IDENT_STOPWORDS:
            continue
        if m not in haystack:
            idents.append(m)

    return nums, idents


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--message-file", required=True,
                    help="file holding the commit message ('-' for stdin)")
    ap.add_argument("--diff-file", help="file holding the diff ('-' for stdin)")
    ap.add_argument("--staged", action="store_true",
                    help="take the diff from `git diff --cached`")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing when the message is clean")
    args = ap.parse_args()

    if args.message_file == "-":
        message = sys.stdin.read()
    else:
        with open(args.message_file, encoding="utf-8", errors="replace") as fh:
            message = fh.read()

    subject, body = split_message(message)
    errors, warnings = check_style(subject, body)
    diff = read_diff(args)
    nums, idents = check_grounding(message, diff)

    if errors:
        print("ERRORS — the message is wrong regardless of the change:")
        for p in errors:
            print(f"  - {p}")

    if warnings:
        print(("\n" if errors else "") + "WARNINGS — shape, judge for yourself:")
        for p in warnings:
            print(f"  - {p}")

    if nums or idents:
        print(("\n" if errors or warnings else "")
              + "GROUNDING — stated by the message, absent from the diff.")
        if nums:
            print(f"  numbers:     {', '.join(nums[:15])}"
                  + (f"  (+{len(nums) - 15} more)" if len(nums) > 15 else ""))
        if idents:
            print(f"  identifiers: {', '.join(idents[:15])}"
                  + (f"  (+{len(idents) - 15} more)" if len(idents) > 15 else ""))
        print("  Not a defect: half of a house body legitimately comes from outside\n"
              "  the diff. But each of these has to be something you *observed* this\n"
              "  session — a run you read, an outage you saw. A number you inferred\n"
              "  is a fabricated measurement. Drop it or go measure it.")

    if not (errors or warnings or nums or idents) and not args.quiet:
        print("OK — style matches the house convention; every stated fact appears"
              " in the diff.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
