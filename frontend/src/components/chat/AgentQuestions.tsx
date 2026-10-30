import { Check, MessageCircleQuestion } from 'lucide-react';
import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { respondRequest } from '../../state/chatAgent';
import type { AgentQuestion, AgentRequest } from '../../state/chatTypes';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';

interface Answer { selected: string[]; text: string }

/** Вопросы из запроса; старый одиночный вопрос - один вопрос без вариантов. */
export function questionsOf(request: AgentRequest): AgentQuestion[] {
  const items = request.payload.questions?.filter((item) => item.question?.trim());
  if (items?.length) return items;
  const text = request.payload.question?.trim();
  return text ? [{ question: text, header: '', options: [], multi_select: false }] : [];
}

const answered = (answer: Answer): boolean => answer.selected.length > 0 || answer.text.trim().length > 0;

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

/** Уточнение от агента: до четырёх вопросов разом, варианты ответа и свой ответ текстом, как в Claude Code. */
export function AgentQuestions({ request }: { request: AgentRequest }) {
  const questions = questionsOf(request);
  const [answers, setAnswers] = useState<Answer[]>(() => questions.map(() => ({ selected: [], text: '' })));
  const [busy, setBusy] = useState(false);
  const ready = questions.length > 0 && answers.every(answered);
  const update = (index: number, change: (answer: Answer) => Answer) =>
    setAnswers((list) => list.map((answer, at) => (at === index ? change(answer) : answer)));
  const toggle = (index: number, label: string, multi: boolean) => update(index, (answer) => {
    const on = answer.selected.includes(label);
    if (multi) return { ...answer, selected: on ? answer.selected.filter((item) => item !== label) : [...answer.selected, label] };
    return { ...answer, selected: on ? [] : [label] };
  });
  const submit = () => {
    if (!ready || busy) return;
    setBusy(true);
    guarded(async () => {
      try {
        await respondRequest(request, { answers: answers.map((answer) => ({ selected: answer.selected, text: answer.text.trim() })) });
      } finally {
        setBusy(false);
      }
    })();
  };
  return (
    <section className="approval-panel question-panel">
      <b><MessageCircleQuestion aria-hidden="true" />{questions.length > 1 ? `Докки спрашивает (${questions.length})` : 'Докки спрашивает'}</b>
      <form onSubmit={(event) => { event.preventDefault(); submit(); }}>
        {questions.map((item, index) => {
          const answer = answers[index] as Answer;
          const options = item.options ?? [];
          const multi = Boolean(item.multi_select);
          const title = `question-${String(request.id)}-${index}`;
          return (
            <fieldset key={title} className="question-item" aria-labelledby={title}>
              <div className="question-head">
                {item.header && <span className="question-chip">{item.header}</span>}
                <p id={title} className="approval-question">{item.question}</p>
              </div>
              {options.length > 0 && (
                <div className="question-options" role={multi ? 'group' : 'radiogroup'} aria-labelledby={title}>
                  {options.map((option) => {
                    const on = answer.selected.includes(option.label);
                    return (
                      <button
                        key={option.label}
                        type="button"
                        className={`question-option${on ? ' selected' : ''}`}
                        role={multi ? 'checkbox' : 'radio'}
                        aria-checked={on}
                        onClick={() => toggle(index, option.label, multi)}
                      >
                        <span className={`question-mark${multi ? ' square' : ''}`} aria-hidden="true">{on && <Check />}</span>
                        <span className="question-option-copy">
                          <span>{option.label}</span>
                          {option.description && <small>{option.description}</small>}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
              <AnswerField
                value={answer.text}
                label={options.length ? `Свой ответ: ${item.question}` : `Ответ: ${item.question}`}
                placeholder={options.length ? 'Или напишите свой ответ' : 'Ваш ответ'}
                onChange={(text) => update(index, (value) => ({ ...value, text }))}
                onSubmit={submit}
              />
            </fieldset>
          );
        })}
        <div className="actions">
          {!ready && questions.length > 1 && <span className="question-hint">Ответьте на каждый вопрос</span>}
          <button type="submit" className="primary" disabled={!ready || busy}><span>Ответить</span></button>
        </div>
      </form>
    </section>
  );
}
