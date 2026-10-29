"""Проверка интерфейса в живой странице: шаги, утверждения, консоль, сеть, вёрстка на нескольких ширинах.

Работает поверх встроенного браузера (browser/service.py) с его ограничениями: только страницы проекта
(свой localhost после devserver.start или файл проекта). Скриншоты сохраняются для пользователя; модель
их не видит (транспорт изображений не подтверждён), поэтому оценка внешнего вида - это сравнение с
эталоном попиксельно и проверки DOM, а не «AI посмотрел». Эталоны лежат в данных Докки, не в git.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

DEFAULT_WIDTHS = (1280, 768, 375)
HEIGHT = 900
MAX_STEPS = 20
MAX_EXPECT = 20
BUDGET_SECONDS = 180
DEFAULT_TOLERANCE = 0.005
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
STEP_ACTIONS = {"click", "type", "press", "select", "hover", "wait", "navigate", "scroll"}
IGNORED_NETWORK = re.compile(r"/favicon\.ico(\?|$)|/__vite_ping|/@vite/client|hot-update", re.I)

LAYOUT_JS = r"""(() => {
  const doc = document.documentElement, vw = window.innerWidth;
  const describe = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    (el.classList && el.classList.length ? '.' + [...el.classList].slice(0, 2).join('.') : '');
  const out = {viewport: vw, scroll_width: doc.scrollWidth, overflow_x: doc.scrollWidth > vw + 1, offenders: [],
               unlabeled: [], unlabeled_count: 0, images_without_alt: 0, small_targets: 0};
  if (out.overflow_x) {
    for (const el of document.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > vw + 1 && getComputedStyle(el).position !== 'fixed') {
        out.offenders.push(describe(el) + ' right=' + Math.round(r.right));
        if (out.offenders.length >= 5) break;
      }
    }
  }
  for (const el of document.querySelectorAll('button, a[href], [role=button], input:not([type=hidden]), select, textarea')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const name = (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || el.value ||
                  el.getAttribute('placeholder') || '').trim();
    const labelled = (el.labels && el.labels.length) || el.getAttribute('aria-labelledby');
    if (!name && !labelled && !el.querySelector('img[alt]:not([alt=""]), svg title')) {
      out.unlabeled_count++;
      if (out.unlabeled.length < 5) out.unlabeled.push(describe(el));
    }
    if (vw <= 480 && (r.width < 24 || r.height < 24)) out.small_targets++;
  }
  out.images_without_alt = [...document.images].filter((img) => !img.hasAttribute('alt')).length;
  return out;
})()"""


class UiVerifyError(ValueError):
    pass


def _json_eval(session_id: int, expression: str) -> Any:
    from koddi.browser.service import browser_event

    result = browser_event(session_id, "eval", {"expression": expression})
    if not result.get("ok"):
        raise UiVerifyError(result.get("error") or "eval failed")
    return json.loads(result.get("result") or "null")


def _expect_js(item: dict) -> str:
    """Утверждение о странице; текст и селектор передаются как JSON-литералы, не как код."""
    text, selector = json.dumps(item.get("text")), json.dumps(item.get("selector"))
    absent = "true" if item.get("absent") else "false"
    return (f"(() => {{ const text = {text}, selector = {selector}, absent = {absent};"
            " let found;"
            " if (selector) { const el = document.querySelector(selector);"
            "   found = !!el && (!text || (el.innerText || el.textContent || '').includes(text)); }"
            " else { found = (document.body.innerText || '').includes(text); }"
            " return {ok: absent ? !found : found, found}; })()")


def baseline_dir(project_id: int | None, owner: str) -> Path:
    from koddi.config import data_dir

    return data_dir() / "ui-baselines" / str(project_id or 0) / hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]


def compare_images(current: Path, baseline: Path, diff_path: Path, tolerance: float) -> dict[str, Any]:
    """Доля заметно отличающихся пикселей; другой размер страницы - тоже расхождение."""
    from PIL import Image, ImageChops

    with Image.open(current) as now_image, Image.open(baseline) as base_image:
        now, base = now_image.convert("RGB"), base_image.convert("RGB")
        if now.size != base.size:
            return {"match": False, "reason": f"size changed {base.size[0]}x{base.size[1]} → {now.size[0]}x{now.size[1]}",
                    "ratio": 1.0}
        difference = ImageChops.difference(now, base).convert("L").point(lambda value: 255 if value > 24 else 0)
        histogram = difference.histogram()
        changed = sum(histogram[1:])
        ratio = changed / float(now.size[0] * now.size[1] or 1)
        if ratio > tolerance:
            box = difference.getbbox()
            overlay = now.copy()
            red = Image.new("RGB", now.size, (255, 0, 80))
            overlay.paste(red, mask=difference)
            overlay.save(diff_path)
            return {"match": False, "ratio": round(ratio, 5), "box": list(box) if box else None}
        return {"match": True, "ratio": round(ratio, 5)}


def _normalize(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "page").strip().lower()
    if not NAME.fullmatch(name):
        raise UiVerifyError("name: латиница, цифры и дефис, до 41 символа")
    widths = [int(value) for value in (args.get("widths") or DEFAULT_WIDTHS)][:4]
    if any(not 200 <= value <= 2560 for value in widths):
        raise UiVerifyError("widths: от 200 до 2560")
    steps = args.get("steps") or []
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise UiVerifyError(f"steps: не больше {MAX_STEPS}")
    for step in steps:
        if not isinstance(step, dict) or step.get("action") not in STEP_ACTIONS:
            raise UiVerifyError("каждый шаг: action из " + ", ".join(sorted(STEP_ACTIONS)))
        if step["action"] == "navigate" and not _local(str(step.get("url") or "")):
            raise UiVerifyError("ui.verify переходит только по страницам проекта (localhost или файл проекта)")
    expect = args.get("expect") or []
    if not isinstance(expect, list) or len(expect) > MAX_EXPECT:
        raise UiVerifyError(f"expect: не больше {MAX_EXPECT}")
    for item in expect:
        if not isinstance(item, dict) or not (item.get("text") or item.get("selector")):
            raise UiVerifyError("каждое утверждение: text и/или selector")
    baseline = str(args.get("baseline") or "off")
    if baseline not in {"off", "compare", "update"}:
        raise UiVerifyError("baseline: off, compare или update")
    tolerance = float(args.get("tolerance") if args.get("tolerance") is not None else DEFAULT_TOLERANCE)
    return {"name": name, "widths": widths, "steps": steps, "expect": expect, "baseline": baseline,
            "tolerance": max(0.0, min(tolerance, 0.2)), "full_page": bool(args.get("full_page")),
            "keep_open": bool(args.get("keep_open"))}


def _local(url: str) -> bool:
    from koddi.browser.service import is_loopback_url

    value = url.strip()
    if not value:
        return False
    if "://" not in value and not value.lower().startswith("file:"):
        return True
    return value.lower().startswith("file:") or is_loopback_url(value)


def verify(runner, args: dict[str, Any]) -> dict[str, Any]:
    from koddi.browser.service import browser_event
    from koddi.policy.account import local_owner

    url = str(args.get("url") or "").strip()
    if not _local(url):
        raise UiVerifyError("ui.verify проверяет только страницы проекта: свой localhost (devserver.start) или файл проекта")
    options = _normalize(args)
    started = time.monotonic()
    opened = runner.browser_tool("browser.open", {"url": url})
    if not opened.get("ok", True) or not opened.get("session_id"):
        return {"ok": False, "status": "failed", "error": opened.get("error") or "страница не открылась", "opened": opened}
    session = int(opened["session_id"])
    failures: list[str] = []
    warnings: list[str] = []
    steps_report, expect_report, widths_report = [], [], []
    try:
        browser_event(session, "resize", {"width": options["widths"][0], "height": HEIGHT})
        for index, step in enumerate(options["steps"], start=1):
            if runner.cancel_event.is_set() or time.monotonic() - started > BUDGET_SECONDS:
                failures.append("проверка остановлена: отмена или лимит времени")
                break
            payload = {key: value for key, value in step.items() if key != "action"}
            if step["action"] == "wait":
                payload.setdefault("timeout_seconds", 10)
            result = browser_event(session, step["action"], payload)
            entry = {"step": index, "action": step["action"], "ok": bool(result.get("ok"))}
            if not result.get("ok"):
                entry["error"] = str(result.get("error") or "")[:300]
                failures.append(f"шаг {index} ({step['action']}): {entry['error']}")
            steps_report.append(entry)
            if not result.get("ok"):
                break
        for item in options["expect"]:
            try:
                verdict = _json_eval(session, _expect_js(item))
            except (UiVerifyError, ValueError) as exc:
                verdict = {"ok": False, "error": str(exc)[:200]}
            entry = {**{key: item[key] for key in ("text", "selector", "absent") if key in item}, "ok": bool(verdict.get("ok"))}
            expect_report.append(entry)
            if not entry["ok"]:
                failures.append("не выполнено утверждение: " + json.dumps({key: item.get(key) for key in ("text", "selector", "absent")
                                                                         if item.get(key)}, ensure_ascii=False))
        directory = baseline_dir(runner.project_id, local_owner())
        for width in options["widths"]:
            if runner.cancel_event.is_set() or time.monotonic() - started > BUDGET_SECONDS:
                warnings.append(f"ширина {width} не проверена: отмена или лимит времени")
                continue
            browser_event(session, "resize", {"width": width, "height": HEIGHT})
            report: dict[str, Any] = {"width": width}
            try:
                layout = _json_eval(session, LAYOUT_JS)
            except (UiVerifyError, ValueError) as exc:
                layout = {"error": str(exc)[:200]}
            report["layout"] = layout
            if layout.get("overflow_x"):
                failures.append(f"горизонтальная прокрутка на ширине {width}: {', '.join(layout.get('offenders') or [])}")
            if layout.get("unlabeled_count"):
                warnings.append(f"{layout['unlabeled_count']} элементов управления без доступного имени на {width}: "
                                + ", ".join(layout.get("unlabeled") or []))
            if layout.get("images_without_alt"):
                warnings.append(f"{layout['images_without_alt']} изображений без alt")
            if layout.get("small_targets"):
                warnings.append(f"{layout['small_targets']} элементов меньше 24px для касания на {width}")
            shot = browser_event(session, "screenshot", {"full_page": options["full_page"]})
            if shot.get("ok"):
                path = Path(shot["path"])
                report.update(runner.attach_image(path, f"ui-{options['name']}-{width}.png"))
                if options["baseline"] != "off":
                    report["baseline"] = _baseline(runner, directory, options, width, path, failures)
            else:
                warnings.append(f"скриншот на ширине {width} не снят: {shot.get('error')}")
            widths_report.append(report)
        console = browser_event(session, "console", {"level": "error", "limit": 50})
        errors = [item for item in console.get("messages", []) if item.get("type") in {"error", "pageerror"}]
        for item in errors[:5]:
            failures.append(f"ошибка в консоли: {item.get('text', '')[:300]}")
        network = browser_event(session, "network", {"failed_only": True, "limit": 100})
        broken = [item for item in network.get("requests", []) if not IGNORED_NETWORK.search(item.get("url", ""))]
        for item in broken[:5]:
            failures.append(f"запрос не удался: {item.get('method')} {item.get('url', '')[:200]} "
                            f"{item.get('status') or item.get('failure') or ''}".rstrip())
    finally:
        if not options["keep_open"]:
            browser_event(session, "close", {})
    status = "failed" if failures else "passed"
    check_id = f"ui:{options['name']}"
    summary = (f"{'✓' if status == 'passed' else '✕'} UI {options['name']}: {len(options['widths'])} ширины, "
               f"{len(steps_report)} шагов, {len(expect_report)} утверждений" + (f", проблем: {len(failures)}" if failures else ""))
    from koddi.quality.gate import record_ui

    record_ui(runner.turn_id, runner._require_workspace(), check_id, {"status": status, "summary": summary})
    return {"ok": status == "passed", "status": status, "check_id": check_id, "summary": summary, "url": url,
            "steps": steps_report, "expect": expect_report, "widths": widths_report, "failures": failures, "warnings": warnings,
            "vision": "not_available: screenshots are saved for the user; the model did not see them, appearance is "
                      "checked only by DOM checks and baseline comparison",
            "duration_seconds": round(time.monotonic() - started, 1)}


def _baseline(runner, directory: Path, options: dict, width: int, path: Path, failures: list[str]) -> dict[str, Any]:
    import shutil

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{options['name']}-{width}.png"
    if options["baseline"] == "update" or not target.exists():
        shutil.copyfile(path, target)
        return {"status": "saved" if options["baseline"] == "update" else "created"}
    diff_path = path.with_name(path.stem + "-diff.png")
    result = compare_images(path, target, diff_path, options["tolerance"])
    if result["match"]:
        return {"status": "match", "ratio": result["ratio"]}
    failures.append(f"скриншот на ширине {width} отличается от эталона ({result.get('reason') or result['ratio']})")
    report = {"status": "mismatch", **result}
    if diff_path.exists():
        report["diff"] = runner.attach_image(diff_path, f"ui-{options['name']}-{width}-diff.png")
    return report
