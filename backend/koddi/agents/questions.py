"""Вопросы агента пользователю: несколько вопросов за раз, у каждого варианты ответа и свой ответ текстом.

Модель зовёт task.ask_user с `questions` (или со старым одиночным `question`), окно показывает их в чате,
а ответ сервер сверяет с вопросами, прежде чем отдать модели.
"""

from __future__ import annotations

MAX_QUESTIONS = 4
MAX_OPTIONS = 6
QUESTION_CHARS = 1000
HEADER_CHARS = 40
LABEL_CHARS = 120
DESCRIPTION_CHARS = 300
ANSWER_CHARS = 4000

OPTION = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "minLength": 1, "maxLength": LABEL_CHARS},
        "description": {"type": "string", "maxLength": DESCRIPTION_CHARS},
    },
    "required": ["label"],
    "additionalProperties": False,
}
QUESTION = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": QUESTION_CHARS},
        "header": {"type": "string", "maxLength": HEADER_CHARS},
        "options": {"type": "array", "items": OPTION, "maxItems": MAX_OPTIONS},
        "multi_select": {"type": "boolean"},
    },
    "required": ["question"],
    "additionalProperties": False,
}
PROPERTIES = {
    "questions": {
        "type": "array",
        "items": QUESTION,
        "minItems": 1,
        "maxItems": MAX_QUESTIONS,
        "description": "1-4 questions asked together. Give 2-4 short options when the likely answers are known; "
        "the user can always type their own answer.",
    },
    # схема одиночного вопроса закреплена в старых сессиях Докки: менять в ней можно только описание
    "question": {"type": "string", "description": "Single question without options (legacy)."},
}


def normalize(args: dict) -> list[dict]:
    """Вопросы из аргументов инструмента: без пустых строк и повторов вариантов."""
    items = args.get("questions")
    if not isinstance(items, list) or not items:
        text = str(args.get("question") or "").strip()
        return [{"question": text[:QUESTION_CHARS], "header": "", "options": [], "multi_select": False}] if text else []
    result = []
    for item in items[:MAX_QUESTIONS]:
        if not isinstance(item, dict) or not str(item.get("question") or "").strip():
            continue
        options, seen = [], set()
        for option in item.get("options") or []:
            label = str(option.get("label") or "").strip() if isinstance(option, dict) else ""
            if label and label.casefold() not in seen:
                seen.add(label.casefold())
                options.append({"label": label, "description": str(option.get("description") or "").strip()})
        result.append({
            "question": str(item["question"]).strip(),
            "header": str(item.get("header") or "").strip(),
            "options": options[:MAX_OPTIONS],
            "multi_select": bool(item.get("multi_select")) and bool(options),
        })
    return result


def summary(questions: list[dict]) -> str:
    """Одной строкой: для телефона, консоли и меню чатов, которые знают только текст вопроса."""
    return "\n".join(item["question"] for item in questions)


def accept(questions: list[dict], payload: dict) -> dict:
    """Проверить ответ окна или телефона. ValueError - ответ не подходит к вопросам."""
    if not isinstance(payload, dict):
        raise ValueError("Ответ должен быть объектом")
    answers = payload.get("answers")
    if answers is None:
        # телефон и старые окна присылают один текст на все вопросы
        text = payload.get("answer")
        if not isinstance(text, str) or not text.strip() or len(text) > ANSWER_CHARS:
            raise ValueError("Напишите ответ")
        result = [{"question": summary(questions), "selected": [], "text": text.strip()}]
        return {"answers": result, "answer": render(result)}
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise ValueError("Ответьте на каждый вопрос")
    result = []
    for question, answer in zip(questions, answers):
        if not isinstance(answer, dict):
            raise ValueError("Некорректный ответ")
        selected = answer.get("selected") or []
        text = answer.get("text") or ""
        if not isinstance(selected, list) or not isinstance(text, str) or len(text) > ANSWER_CHARS:
            raise ValueError("Некорректный ответ")
        labels = {option["label"] for option in question.get("options", [])}
        if any(not isinstance(item, str) or item not in labels for item in selected) or len(set(selected)) != len(selected):
            raise ValueError("Выбран вариант, которого нет в вопросе")
        if len(selected) > 1 and not question.get("multi_select"):
            raise ValueError("В этом вопросе можно выбрать только один вариант")
        if not selected and not text.strip():
            raise ValueError("Ответьте на каждый вопрос")
        result.append({"question": question["question"], "selected": selected, "text": text.strip()})
    return {"answers": result, "answer": render(result)}


def render(answers: list[dict]) -> str:
    """Ответ для модели и для истории чата: вопрос и что выбрал или написал человек."""
    lines = []
    for item in answers:
        parts = [*item["selected"], *([item["text"]] if item["text"] else [])]
        lines.append(f"{item['question']}\n→ {'; '.join(parts)}")
    return "\n\n".join(lines)
