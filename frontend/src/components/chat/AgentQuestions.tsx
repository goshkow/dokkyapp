import { Check, MessageCircleQuestion } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { respondRequest } from '../../state/chatAgent';
import type { AgentQuestion, AgentRequest } from '../../state/chatTypes';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';

interface Answer { selected: string[]; text: string; other: boolean }

/** Вопросы из запроса; старый одиночный вопрос - один вопрос без вариантов. */
export function questionsOf(request: AgentRequest): AgentQuestion[] {
  const items = request.payload.questions?.filter((item) => item.question?.trim());
  if (items?.length) return items;
  const text = request.payload.question?.trim();
  return text ? [{ question: text, header: '', options: [], multi_select: false }] : [];
}

/** Свой текст считается, только если выбрано «Другое» или у вопроса нет вариантов. */
const ownText = (answer: Answer, item: AgentQuestion): string => (answer.other || !item.options?.length ? answer.text.trim() : '');
const answered = (answer: Answer, item: AgentQuestion): boolean => answer.selected.length > 0 || ownText(answer, item).length > 0;
const OTHER = 'Другое';

/** Поле своего ответа растёт вместе с текстом, как поле ввода чата, до восьми строк. */
function AnswerField({ value, label, placeholder, onChange, onSubmit }: {
  value: string; label: string; placeholder: string; onChange: (text: string) => void; onSubmit: () => void;
}) {
  const node = useRef<HTMLTextAreaElement>(null);
  const enterSends = useData((s) => s.settings.enter_sends);
  useLayoutEffect(() => {
    const field = node.current;
    if (!field) return;
    field.style.height = 'auto';
    field.style.height = `${field.scrollHeight}px`;
  }, [value]);
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) return;
    const send = enterSends ? !event.shiftKey : event.ctrlKey || event.metaKey;
    if (!send) return;
    event.preventDefault();
    onSubmit();
  };
  return (
    <textarea
      ref={node}
      className="question-text"
      rows={2}
      aria-label={label}
      placeholder={placeholder}
      value={value}
      maxLength={4000}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={onKeyDown}
    />
  );
}

/** Уточнение от агента: до четырёх вопросов по одному, варианты, «Другое» со своим текстом, как в Claude Code. */
export function AgentQuestions({ request }: { request: AgentRequest }) {
  const questions = questionsOf(request);
  const [answers, setAnswers] = useState<Answer[]>(() => questions.map(() => ({ selected: [], text: '', other: false })));
  const [current, setCurrent] = useState(0);
  const [busy, setBusy] = useState(false);
  const stepNode = useRef<HTMLFieldSetElement>(null);
  const moved = useRef(false);
  const total = questions.length;
  const last = current >= total - 1;
  const item = questions[current] as AgentQuestion;
  const answer = answers[current] as Answer;
  const doneAt = (index: number) => answered(answers[index] as Answer, questions[index] as AgentQuestion);
  const ready = total > 0 && questions.every((_, index) => doneAt(index));
  const update = (index: number, change: (value: Answer) => Answer) =>
    setAnswers((list) => list.map((value, at) => (at === index ? change(value) : value)));
  // после перехода фокус на новом вопросе, чтобы клавиши и скринридер продолжали с него
  useEffect(() => {
    if (!moved.current) { moved.current = true; return; }
    stepNode.current?.focus();
  }, [current]);
  const go = (index: number) => setCurrent(Math.min(Math.max(index, 0), total - 1));
  const nextOpen = (from: number) => {
    for (let step = 1; step < total; step += 1) {
      const index = (from + step) % total;
      if (!doneAt(index)) return index;
    }
    return -1;
  };
  const submit = () => {
    if (!ready || busy) return;
    setBusy(true);
    guarded(async () => {
      try {
        await respondRequest(request, { answers: answers.map((value, index) => ({
          selected: value.selected,
          text: ownText(value, questions[index] as AgentQuestion),
        })) });
      } finally {
        setBusy(false);
      }
    })();
  };
  const advance = () => {
    if (!doneAt(current)) return;
    if (last) submit();
    else go(current + 1);
  };
  const options = item?.options ?? [];
  const multi = Boolean(item?.multi_select);
  const pick = (label: string) => {
    const on = answer.selected.includes(label);
    if (multi) {
      update(current, (value) => ({ ...value, selected: on ? value.selected.filter((x) => x !== label) : [...value.selected, label] }));
      return;
    }
    update(current, (value) => ({ ...value, selected: on ? [] : [label], other: false }));
    if (!on && total > 1) {
      // автопереход к следующему неотвеченному; если все отвечены - остаёмся на месте
      const open = nextOpen(current);
      const later = open > current ? open : questions.findIndex((_, index) => index > current && !doneAt(index));
      if (later >= 0) go(later);
      else if (!last) go(current + 1);
    }
  };
  const pickOther = () => update(current, (value) => (
    multi ? { ...value, other: !value.other } : { ...value, selected: [], other: !value.other }
  ));
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const target = event.target as HTMLElement;
    if (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT' || event.nativeEvent.isComposing) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key >= '1' && event.key <= '9') {
      const number = Number(event.key) - 1;
      if (number < options.length) pick((options[number] as { label: string }).label);
      else if (number === options.length && options.length) pickOther();
      else return;
      event.preventDefault();
    } else if (event.key === 'ArrowRight' && total > 1) {
      event.preventDefault();
      go(current + 1);
    } else if (event.key === 'ArrowLeft' && total > 1) {
      event.preventDefault();
      go(current - 1);
    } else if (event.key === 'Enter' && target.tagName !== 'BUTTON' && doneAt(current)) {
      event.preventDefault();
      advance();
    }
  };
  if (!item) return null;
  const title = `question-${String(request.id)}-${current}`;
  const showText = answer.other || options.length === 0;
  return (
    <section className="approval-panel question-panel" onKeyDown={onKeyDown}>
      <b><MessageCircleQuestion aria-hidden="true" />Докки спрашивает{total > 1 && <span className="question-count">{current + 1} из {total}</span>}</b>
      <form onSubmit={(event) => { event.preventDefault(); advance(); }}>
        {total > 1 && (
          <div className="question-tabs" role="group" aria-label="Вопросы">
            {questions.map((entry, index) => (
              <button
                key={`${String(request.id)}-${index}`}
                type="button"
                className={`question-tab${index === current ? ' current' : ''}${doneAt(index) ? ' done' : ''}`}
                aria-current={index === current ? 'step' : undefined}
                aria-label={`${entry.header?.trim() || `Вопрос ${index + 1}`}${doneAt(index) ? ', отвечен' : ''}`}
                onClick={() => go(index)}
              >
                {doneAt(index) && <Check aria-hidden="true" />}
                <span>{entry.header?.trim() || `Вопрос ${index + 1}`}</span>
              </button>
            ))}
          </div>
        )}
        <fieldset key={title} ref={stepNode} tabIndex={-1} className="question-item" aria-labelledby={title}>
          <div className="question-head">
            {item.header && total === 1 && <span className="question-chip">{item.header}</span>}
            <p id={title} className="approval-question">{item.question}</p>
          </div>
          {options.length > 0 && (
            <div className="question-options" role={multi ? 'group' : 'radiogroup'} aria-labelledby={title}>
              {options.map((option, index) => {
                const on = answer.selected.includes(option.label);
                return (
                  <button
                    key={option.label}
                    type="button"
                    className={`question-option${on ? ' selected' : ''}`}
                    role={multi ? 'checkbox' : 'radio'}
                    aria-checked={on}
                    onClick={() => pick(option.label)}
                  >
                    <span className={`question-mark${multi ? ' square' : ''}`} aria-hidden="true">{on && <Check />}</span>
                    <span className="question-option-copy">
                      <span>{option.label}</span>
                      {option.description && <small>{option.description}</small>}
                    </span>
                    {index < 9 && <kbd className="question-key" aria-hidden="true">{index + 1}</kbd>}
                  </button>
                );
              })}
              <button
                type="button"
                className={`question-option${answer.other ? ' selected' : ''}`}
                role={multi ? 'checkbox' : 'radio'}
                aria-checked={answer.other}
                onClick={pickOther}
              >
                <span className={`question-mark${multi ? ' square' : ''}`} aria-hidden="true">{answer.other && <Check />}</span>
                <span className="question-option-copy"><span>{OTHER}</span></span>
                {options.length < 9 && <kbd className="question-key" aria-hidden="true">{options.length + 1}</kbd>}
              </button>
            </div>
          )}
          {showText && (
            <AnswerField
              value={answer.text}
              label={options.length ? `Свой ответ: ${item.question}` : `Ответ: ${item.question}`}
              placeholder="Ваш ответ"
              onChange={(text) => update(current, (value) => ({ ...value, text }))}
              onSubmit={advance}
            />
          )}
        </fieldset>
        <div className="actions">
          {total > 1 && last && !ready && <span className="question-hint">Ответьте на каждый вопрос</span>}
          {total > 1 && <button type="button" onClick={() => go(current - 1)} disabled={current === 0}><span>Назад</span></button>}
          {last
            ? <button type="submit" className="primary" disabled={!ready || busy}><span>Ответить</span></button>
            : <button type="submit" className="primary" disabled={!doneAt(current)}><span>Далее</span></button>}
        </div>
      </form>
    </section>
  );
}
