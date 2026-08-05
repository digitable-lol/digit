"""Machine verification of flang source — a verdict, not an opinion.

flang is days old. Nothing in a language model's weights describes it, so
anything it writes in flang is a guess dressed as knowledge, and the usual
tell (code that *looks* plausible) is exactly what a generator is best at
producing. The language happens to ship the antidote: a checker that answers
in milliseconds and does not care how confident the author was.

    node flang/bin/flang.mjs check <file>   parse + types + totality
    node flang/bin/flang.mjs test  <file>   run every declared example

This module wraps both and reports **three** outcomes, not two:

``ok``
    ``check`` passed *and* ``test`` passed. The code was executed.
``failed``
    the checker ran and rejected the code. Diagnostics carry ``code`` and,
    when the parser knows it, ``span`` — the line and column.
``unavailable``
    the checker could not run at all: no flang checkout, no ``node``, a
    timeout, or the CLI rejecting our own invocation.

The third state is the point. A boolean forces "could not verify" to alias
either "verified" or "rejected", and the first is a lie that costs the user
their trust in every other green result. Callers must be able to say "I did
not check this" — so ``unavailable`` is a distinct verdict, a distinct exit
code, and never satisfies :attr:`VerifyReport.ok`.

Pure stdlib, and it imports without a flang checkout present: the whole
value of the module is being able to report its own absence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

OK = "ok"
FAILED = "failed"
UNAVAILABLE = "unavailable"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNAVAILABLE = 4
"""Distinct from the CLI's generic error (1).

A script that treats "the checker is missing" as "your code is wrong" will
send someone hunting a bug in correct code; one that treats it as "fine"
ships unverified code. Neither is available if the exit codes differ.
"""

DEFAULT_TIMEOUT = 30.0

#: Extensions ``flang.mjs`` dispatches on. Anything else is sniffed as FTS,
#: which turns a flang syntax error into a confusing FTS one — so a snippet
#: written to a temporary file must be named ``.flang``.
FLANG_SUFFIXES = (".flang", ".fl")
KNOWN_SUFFIXES = FLANG_SUFFIXES + (".fts", ".json")

VERDICT_PREFIXES = ("FLANG_", "FTS_")
"""Diagnostic families that mean *the program was judged*.

``flang.mjs`` funnels every failure through one JSON shape, so a broken
toolchain and a broken program come back looking alike — an ``.fts`` model
in a checkout that was never built reports
``ERR_MODULE_NOT_FOUND: Cannot find module …/dist/src/index.js``, which is
Node failing to load the FTS core and says nothing at all about the source.
Blaming the author for that is the same error as passing unchecked code,
pointed the other way: it sends someone debugging code that is fine.

Codes outside these families are Node's, so they mark a tool failure. The
two below are inside the family but are still about the tool — ``fail()``
with no code degrades to ``FLANG_INTERNAL``, and ``FLANG_CLI`` is a bad
invocation.
"""

NOT_A_VERDICT = frozenset({"FLANG_INTERNAL", "FLANG_CLI"})


@dataclass
class Diagnostic:
    """One complaint from the checker, with its place in the source."""

    code: str
    message: str
    severity: str = "error"
    line: Optional[int] = None
    column: Optional[int] = None
    stage: str = "check"

    @property
    def where(self) -> str:
        if self.line is None:
            return ""
        if self.column is None:
            return f"{self.line}"
        return f"{self.line}:{self.column}"

    def as_dict(self) -> dict:
        return {
            "stage": self.stage, "code": self.code, "message": self.message,
            "severity": self.severity, "line": self.line, "column": self.column,
        }


@dataclass
class ExampleFailure:
    function: str
    example: str
    expected: object = None
    actual: object = None
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "function": self.function, "example": self.example,
            "expected": self.expected, "actual": self.actual,
            "error": self.error,
        }


@dataclass
class VerifyReport:
    """What the checker said. ``verdict`` is the only thing to branch on."""

    verdict: str
    path: str = ""
    reason: str = ""
    module: Optional[str] = None
    functions: List[dict] = field(default_factory=list)
    types: List[str] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)
    failures: List[ExampleFailure] = field(default_factory=list)
    examples_total: int = 0
    examples_passed: int = 0
    examples_failed: int = 0
    checker: str = ""
    node: str = ""
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        """True only when the code was actually executed and accepted."""
        return self.verdict == OK

    @property
    def available(self) -> bool:
        return self.verdict != UNAVAILABLE

    @property
    def untested(self) -> bool:
        """Passed ``check`` but declares no examples — nothing was run.

        Not a failure (a type declaration file has nothing to run), but it
        must be visible: "verified" over zero executed examples means the
        types agree, not that the code computes the right thing.
        """
        return self.verdict == OK and self.examples_total == 0

    @property
    def exit_code(self) -> int:
        if self.verdict == OK:
            return EXIT_OK
        if self.verdict == FAILED:
            return EXIT_FAILED
        return EXIT_UNAVAILABLE

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "ok": self.ok,
            "available": self.available,
            "path": self.path,
            "reason": self.reason,
            "module": self.module,
            "functions": self.functions,
            "types": self.types,
            "diagnostics": [d.as_dict() for d in self.diagnostics],
            "examples": {
                "total": self.examples_total,
                "passed": self.examples_passed,
                "failed": self.examples_failed,
                "failures": [f.as_dict() for f in self.failures],
            },
            "checker": self.checker,
            "node": self.node,
            "elapsed": round(self.elapsed, 3),
        }


# --------------------------------------------------------------------------
# Locating the checker
# --------------------------------------------------------------------------


@dataclass
class Checker:
    """A runnable flang CLI: an interpreter plus the entry script."""

    node: str
    script: Path
    node_version: str = ""

    @property
    def label(self) -> str:
        return f"{self.script} (node {self.node_version or '?'})"


class CheckerUnavailable(Exception):
    """The verifier cannot run. Always surfaced, never swallowed."""


def find_checker(root: Optional[str] = None) -> Checker:
    """Locate ``flang/bin/flang.mjs`` and a ``node`` to run it with.

    The checkout is resolved by the same rules the indexer uses for the
    flang corpus (``--flang`` → ``$DIGIT_KB_FLANG`` → conventional paths),
    so the documents the KB answers from and the checker that grades the
    answers cannot drift apart into two different checkouts.
    """
    # Imported here, not at module scope: ``verify`` must stay importable on
    # a machine with no corpus at all, and ``indexer`` pulls in the store.
    from digit_cli.kb import indexer, store

    try:
        checkout = indexer._resolve_root(indexer.FLANG_REPO, root)
    except indexer.MissingCheckout as exc:
        raise CheckerUnavailable(str(exc)) from None
    except store.KBError as exc:
        raise CheckerUnavailable(str(exc)) from None

    script = checkout / "flang" / "bin" / "flang.mjs"
    if not script.is_file():
        raise CheckerUnavailable(
            f"{checkout} has no flang/bin/flang.mjs — not a flang checkout"
        )

    node = os.environ.get("DIGIT_FLANG_NODE", "").strip() or shutil.which("node")
    if not node:
        raise CheckerUnavailable(
            "node is not on PATH; flang's checker is a Node script "
            "(set DIGIT_FLANG_NODE to an interpreter)"
        )

    version = ""
    try:
        probe = subprocess.run(
            [node, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        version = probe.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckerUnavailable(f"cannot run {node}: {exc}") from None

    return Checker(node=node, script=script, node_version=version)


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------


def _parse_payload(proc: subprocess.CompletedProcess) -> Optional[dict]:
    """Read the CLI's JSON.

    The contract mirrors the FTS core: the result goes to stdout when the
    command succeeded and to stderr when it did not, so both streams are
    candidates and neither is a fallback for a parse failure of the other.
    """
    for stream in (proc.stdout, proc.stderr):
        text = (stream or "").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _diagnostics_from(payload: dict, stage: str) -> List[Diagnostic]:
    out: List[Diagnostic] = []
    for raw in payload.get("diagnostics") or []:
        if not isinstance(raw, dict):
            continue
        span = raw.get("span") or {}
        out.append(Diagnostic(
            code=str(raw.get("code") or "FLANG"),
            message=str(raw.get("message") or ""),
            severity=str(raw.get("severity") or "error"),
            line=span.get("line") if isinstance(span, dict) else None,
            column=span.get("column") if isinstance(span, dict) else None,
            stage=stage,
        ))
    # ``{"error": "…"}`` with no diagnostics array happens for failures raised
    # before the parser has anything to point at. Losing the text would leave
    # a "failed" verdict with nothing to act on. ``FLANG_INTERNAL`` is what
    # the CLI itself uses for a coded-less error, and it deliberately does
    # not count as a verdict about the program.
    if not out and payload.get("error"):
        out.append(Diagnostic(
            code="FLANG_INTERNAL", message=str(payload["error"]), stage=stage,
        ))
    return out


def _is_a_verdict(diagnostics: Sequence[Diagnostic]) -> bool:
    """Did the checker judge the program, or did it fall over?

    No diagnostics at all is still a verdict: ``flang test`` reports a failing
    example through ``results``, not through ``diagnostics``.
    """
    if not diagnostics:
        return True
    return any(
        d.code.startswith(VERDICT_PREFIXES) and d.code not in NOT_A_VERDICT
        for d in diagnostics
    )


def _run(
    checker: Checker, command: str, path: Path, timeout: float
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [checker.node, str(checker.script), command, str(path)],
        capture_output=True,
        text=True,
        # The CLI emits UTF-8 JSON with Cyrillic identifiers and messages.
        # Decoding by the ambient locale would mangle every diagnostic on a
        # non-UTF-8 host; ``replace`` keeps a mangled stream from raising and
        # lets it fall through to the honest "returned no JSON" verdict.
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        # Imports (``использует «Списки» из "../stdlib/lists.flang"``) resolve
        # against the *file*, not the process, so cwd is only about where a
        # relative argument points. Anchoring it to the file's directory keeps
        # the two consistent.
        cwd=str(path.parent),
    )


def verify_file(
    path: Path,
    *,
    checker: Optional[Checker] = None,
    root: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    run_tests: bool = True,
) -> VerifyReport:
    """Run ``check`` and then ``test`` over ``path``. Never raises for a verdict."""
    started = time.time()
    path = Path(path)
    report = VerifyReport(verdict=UNAVAILABLE, path=str(path))

    if checker is None:
        try:
            checker = find_checker(root)
        except CheckerUnavailable as exc:
            report.reason = str(exc)
            report.elapsed = time.time() - started
            return report
    report.checker = str(checker.script)
    report.node = checker.node_version

    if not path.is_file():
        report.reason = f"no such file: {path}"
        report.elapsed = time.time() - started
        return report
    if path.suffix not in KNOWN_SUFFIXES:
        # The CLI dispatches on the extension and sniffs anything it does not
        # recognise as FTS — which reports an FTS parse error for a perfectly
        # ordinary flang file. Refusing beats explaining that later.
        report.reason = (
            f"{path.name}: flang dispatches on the extension; use one of "
            + ", ".join(KNOWN_SUFFIXES)
        )
        report.elapsed = time.time() - started
        return report

    for stage in ("check", "test") if run_tests else ("check",):
        try:
            proc = _run(checker, stage, path, timeout)
        except subprocess.TimeoutExpired:
            # A verdict was not reached. Calling it "failed" would assert
            # something about the code that was never established — the run
            # may have been starved rather than divergent.
            report.verdict = UNAVAILABLE
            report.reason = f"`flang {stage}` exceeded {timeout:g}s — no verdict"
            break
        except OSError as exc:
            report.verdict = UNAVAILABLE
            report.reason = f"cannot run the checker: {exc}"
            break

        payload = _parse_payload(proc)
        if payload is None:
            report.verdict = UNAVAILABLE
            report.reason = (
                f"`flang {stage}` returned {proc.returncode} with no JSON: "
                + ((proc.stderr or proc.stdout or "").strip()[:400] or "(no output)")
            )
            break
        if proc.returncode == 2:
            # Exit 2 is the CLI's "you called me wrong" — our bug, not the
            # user's code. Reporting it as a rejection would blame the author.
            report.verdict = UNAVAILABLE
            report.reason = (
                f"`flang {stage}` rejected the invocation: "
                + str(payload.get("error") or payload)
            )
            break

        if stage == "check":
            report.module = payload.get("module")
            report.functions = [
                f for f in (payload.get("functions") or []) if isinstance(f, dict)
            ]
            report.types = [str(t) for t in (payload.get("types") or [])]

        if stage == "test":
            report.examples_total = int(payload.get("total") or 0)
            report.examples_passed = int(payload.get("passed") or 0)
            report.examples_failed = int(payload.get("failed") or 0)
            for raw in payload.get("results") or []:
                if isinstance(raw, dict) and not raw.get("passed"):
                    report.failures.append(ExampleFailure(
                        function=str(raw.get("function") or "?"),
                        example=str(raw.get("example") or "?"),
                        expected=raw.get("expected"),
                        actual=raw.get("actual"),
                        error=str(raw.get("error") or ""),
                    ))

        failed = proc.returncode != 0 or payload.get("valid") is False
        if failed:
            found = _diagnostics_from(payload, stage)
            if not _is_a_verdict(found) or (
                stage == "test" and not found and not report.failures
            ):
                # The checker fell over instead of judging. Reporting this as
                # a rejection would be a false accusation, and the author
                # would go looking for a bug that is not in their file.
                report.verdict = UNAVAILABLE
                report.reason = (
                    f"`flang {stage}` failed before judging the program: "
                    + "; ".join(f"{d.code}: {d.message}" for d in found)[:400]
                    if found else
                    f"`flang {stage}` exited {proc.returncode} without a verdict"
                )
                report.diagnostics.extend(found)
                break
            report.verdict = FAILED
            report.diagnostics.extend(found)
            if not report.reason:
                report.reason = (
                    f"`flang {stage}` rejected the program"
                    if stage == "check"
                    else f"{report.examples_failed} of {report.examples_total} "
                         f"example(s) failed"
                )
            break
    else:
        report.verdict = OK
        report.reason = (
            f"check passed; {report.examples_passed}/{report.examples_total} "
            f"example(s) passed" if run_tests and report.examples_total
            else "check passed"
        )

    report.elapsed = time.time() - started
    return report


def verify_source(
    source: str,
    *,
    directory: Optional[Path] = None,
    name: str = "snippet.flang",
    checker: Optional[Checker] = None,
    root: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    run_tests: bool = True,
) -> VerifyReport:
    """Verify flang text that is not on disk yet.

    ``directory`` decides where the temporary file lands, and that is not a
    detail: module imports resolve relative to the file, so a snippet with
    ``использует … из "../stdlib/lists.flang"`` only resolves if it is
    written next to the checkout it means. Default is the working directory,
    which is what a caller pasting a snippet from a repository expects.
    """
    directory = Path(directory) if directory else Path.cwd()
    if not directory.is_dir():
        return VerifyReport(
            verdict=UNAVAILABLE, path=name,
            reason=f"no such directory for the snippet: {directory}",
        )
    suffix = name if name.startswith(".") else Path(name).suffix or ".flang"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=suffix, dir=str(directory),
        prefix="digit-flang-", delete=False,
    ) as fh:
        fh.write(source if source.endswith("\n") else source + "\n")
        tmp = Path(fh.name)
    try:
        report = verify_file(
            tmp, checker=checker, root=root, timeout=timeout, run_tests=run_tests
        )
    finally:
        try:
            tmp.unlink()
        except OSError:  # pragma: no cover - best effort
            pass
    report.path = f"<snippet in {directory}>"
    return report


def verify_paths(
    paths: Sequence[Path],
    *,
    root: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    run_tests: bool = True,
) -> List[VerifyReport]:
    """Verify several files with one checker lookup.

    If the checker itself is missing, every file gets an ``unavailable``
    report rather than the batch aborting: a caller must be able to see that
    *nothing* was verified, per file, and not infer it from an exception.
    """
    try:
        checker = find_checker(root)
    except CheckerUnavailable as exc:
        return [
            VerifyReport(verdict=UNAVAILABLE, path=str(p), reason=str(exc))
            for p in paths
        ]
    return [
        verify_file(Path(p), checker=checker, timeout=timeout, run_tests=run_tests)
        for p in paths
    ]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

_MARK: Dict[str, str] = {OK: "OK", FAILED: "FAILED", UNAVAILABLE: "UNVERIFIED"}


def render(report: VerifyReport) -> List[str]:
    """Human-readable lines. Kept next to the data so both stay honest."""
    lines = [f"{_MARK[report.verdict]:<11}{report.path}"]
    if report.verdict == UNAVAILABLE:
        lines.append(f"  не проверено: {report.reason}")
        lines.append("  код НЕ подтверждён — не выдавайте его за проверенный.")
        return lines

    if report.module:
        lines.append(f"  модуль: {report.module}")
    if report.functions:
        shown = ", ".join(
            f"{f.get('name')}{'' if f.get('total') else ' (обычная)'}"
            for f in report.functions[:12]
        )
        more = "" if len(report.functions) <= 12 else f", … ещё {len(report.functions) - 12}"
        lines.append(f"  функции: {shown}{more}")

    for d in report.diagnostics:
        place = f" {report.path}:{d.where}" if d.where else ""
        lines.append(f"  [{d.stage}] {d.code}{place}")
        lines.append(f"      {d.message}")
    for f in report.failures:
        lines.append(f"  [test] пример «{f.example}» функции «{f.function}»")
        if f.error:
            lines.append(f"      ошибка: {f.error}")
        else:
            lines.append(
                f"      ожидалось {json.dumps(f.expected, ensure_ascii=False)}, "
                f"получено {json.dumps(f.actual, ensure_ascii=False)}"
            )

    if report.verdict == OK:
        lines.append(
            f"  примеры: {report.examples_passed}/{report.examples_total} прошли"
            if report.examples_total else
            "  примеры: не объявлено ни одного — типы сошлись, но ничего не исполнялось"
        )
    elif report.reason:
        lines.append(f"  итог: {report.reason}")
    lines.append(f"  проверял: {report.checker} (node {report.node}), "
                 f"{report.elapsed:.2f}s")
    return lines
