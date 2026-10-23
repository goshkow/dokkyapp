from __future__ import annotations

import re


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{16,})"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]


def secret_scan(text: str) -> dict:
    findings = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                {"span": match.span(), "preview": match.group(0)[:12] + "...", "severity": "high"}
            )
    return {"ok": True, "findings": findings}


def safety_review(diff: str = "", command: str = "") -> dict:
    risks = []
    lower = (diff + "\n" + command).lower()
    if any(word in lower for word in ["remove-item", "rm -rf", "del /s", "format "]):
        risks.append({"severity": "critical", "message": "Potential destructive command"})
    if secret_scan(diff + command)["findings"]:
        risks.append({"severity": "high", "message": "Potential secret in change"})
    if any(path in lower for path in ["windows/system32", "/etc/", "program files"]):
        risks.append({"severity": "high", "message": "System path modification"})
    action = (
        "allow"
        if not risks
        else ("block" if any(r["severity"] == "critical" for r in risks) else "ask")
    )
    return {"ok": True, "decision": action, "risks": risks}


def diff_review(diff: str) -> dict:
    findings = []
    task_marker = "TO" + "DO"
    if task_marker in diff:
        findings.append({"severity": "low", "message": "Task marker remains in diff"})
    if len(diff) > 100_000:
        findings.append({"severity": "medium", "message": "Large diff needs focused review"})
    return {"ok": True, "findings": findings}
