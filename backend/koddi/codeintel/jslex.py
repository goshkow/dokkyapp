"""Лексический слой JavaScript/TypeScript без парсера: маскирование комментариев и строк, идентификаторы.

Нужен там, где языкового сервиса TypeScript нет: совпадение имени вне комментариев и строк остаётся
приблизительным (`approximate`), но не ловит упоминания в тексте и строковых литералах.
"""

from __future__ import annotations

import re

IDENT_START = re.compile(r"[A-Za-z_$À-￿]")
IDENT_CHAR = re.compile(r"[\w$À-￿]")
# после этих символов и слов `/` начинает регулярное выражение, а не деление
REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}
REGEX_KEYWORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do", "else", "yield", "await"}
DECLARATION = re.compile(
    r"\b(?:(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(function\*?|class|interface|type|enum|const|let|var|namespace))\s+([A-Za-z_$][\w$]*)"
)

LINE_END = re.compile("[\r\n\u2028\u2029]")


def mask(text: str) -> str:
    """Тот же текст, где содержимое комментариев, строк и регулярных выражений заменено пробелами.

    Переводы строк сохраняются, поэтому номера строк и колонок совпадают с исходником. Выражения внутри
    `${...}` шаблонных строк остаются кодом.
    """
    out = list(text)
    length = len(text)
    index = 0
    last = ""  # последний значимый токен для различения деления и регулярного выражения
    stack: list[int] = []  # глубина фигурных скобок для вложенных ${...}

    def blank(start: int, end: int) -> None:
        for position in range(start, end):
            if out[position] not in "\r\n":
                out[position] = " "

    def template(start: int) -> int:
        position = start
        while position < length:
            char = text[position]
            if char == "\\":
                position += 2
                continue
            if char == "`":
                blank(start, position)
                return position + 1
            if char == "$" and position + 1 < length and text[position + 1] == "{":
                blank(start, position)
                stack.append(0)
                return -(position + 2)  # продолжение в коде выражения
            position += 1
        blank(start, length)
        return length

    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            # конец строки в JS - любой терминатор (\n, \r, U+2028, U+2029), а не только \n
            found = LINE_END.search(text, index)
            end = found.start() if found else length
            blank(index, end)
            index = end
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            blank(index, end)
            index = end
            continue
        if char in "'\"":
            position = index + 1
            while position < length and text[position] != char and text[position] not in "\r\n":
                position += 2 if text[position] == "\\" else 1
            blank(index + 1, min(position, length))
            index = position + 1
            last = "str"
            continue
        if char == "`" or (char == "}" and stack and stack[-1] == 0):
            if char == "}":
                stack.pop()
            result = template(index + 1)
            if result < 0:
                index = -result
                last = "("
            else:
                index = result
                last = "str"
            continue
        if char == "/" and (last in REGEX_AFTER or last in REGEX_KEYWORDS):
            position = index + 1
            in_class = False
            while position < length and text[position] not in "\r\n":
                current = text[position]
                if current == "\\":
                    position += 2
                    continue
                if current == "[":
                    in_class = True
                elif current == "]":
                    in_class = False
                elif current == "/" and not in_class:
                    break
                position += 1
            blank(index + 1, min(position, length))
            index = position + 1
            while index < length and IDENT_CHAR.match(text[index]):
                index += 1
            last = "str"
            continue
        if IDENT_START.match(char):
            position = index + 1
            while position < length and IDENT_CHAR.match(text[position]):
                position += 1
            last = text[index:position]
            index = position
            continue
        if char == "{" and stack:
            stack[-1] += 1
        elif char == "}" and stack:
            stack[-1] -= 1
        last = char if not char.isdigit() else "num"
        index += 1
    return "".join(out)


def identifier_positions(masked: str, name: str) -> list[tuple[int, int]]:
    """Строка (с 1) и колонка (с 0) каждого вхождения идентификатора в маскированном тексте."""
    pattern = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
    result = []
    line_starts = [0] + [match.end() for match in re.finditer(r"\r\n|\r|\n", masked)]
    import bisect

    for match in pattern.finditer(masked):
        # obj.name - свойство чужого объекта; приблизительно его всё равно показываем, роль отличаем
        line = bisect.bisect_right(line_starts, match.start())
        result.append((line, match.start() - line_starts[line - 1]))
    return result


def declarations(masked: str) -> list[tuple[str, str, int, int]]:
    """Объявления верхнего уровня и вложенные: (имя, вид, строка с 1, колонка с 0)."""
    line_starts = [0] + [match.end() for match in re.finditer(r"\r\n|\r|\n", masked)]
    import bisect

    result = []
    for match in DECLARATION.finditer(masked):
        kind, name = match.groups()
        start = match.start(2)
        line = bisect.bisect_right(line_starts, start)
        result.append((name, kind.rstrip("*"), line, start - line_starts[line - 1]))
    return result
