import DOMPurify from 'dompurify';
import { useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { money, usePlans, type Plan, type PlanFeature, type PlanPrice } from '../../state/plans';

const icons = new Map<string, string>();

/** Иконка приходит от сервера готовым SVG (Lucide, `currentColor`): вставляется только после очистки. */
export function sanitizeIcon(svg: string): string {
  const cached = icons.get(svg);
  if (cached !== undefined) return cached;
  const clean = DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true },
    FORBID_TAGS: ['style', 'script', 'foreignObject', 'use', 'image', 'a', 'animate', 'animateTransform', 'set'],
    FORBID_ATTR: ['style', 'href', 'xlink:href'],
  });
  const safe = clean.trimStart().startsWith('<svg') ? clean : '';
  icons.set(svg, safe);
  return safe;
}

export function PlanIconSvg({ feature }: { feature: PlanFeature }) {
  const svg = feature.icon?.svg ? sanitizeIcon(feature.icon.svg) : '';
  // без иконки от сервера остаётся пустое место того же размера: строки не прыгают
  return <span className="plan-icon" aria-hidden="true" dangerouslySetInnerHTML={svg ? { __html: svg } : undefined} />;
}

/** Название плана как на сайте: градиентным текстом, цвет по идентификатору; неизвестный план - обычным цветом. */
export function PlanName({ id, name }: { id: string; name: string }) {
  const badge = usePlans((state) => state.catalog?.plans.find((plan) => plan.id === id)?.badge);
  const colors = badge ? { '--plan-a': badge.dark[0], '--plan-b': badge.dark[1], '--plan-light-a': badge.light[0], '--plan-light-b': badge.light[1] } as CSSProperties : undefined;
  return <span className="plan-badge" data-plan={id} data-colored={badge ? '' : undefined} style={colors}>{name}</span>;
}

/** Шапка плана как на сайте: крупная цена, рядом скидка, под ней название плана и срок. */
export function PlanHero({ plan, months }: { plan: Plan; months: number }) {
  const price: PlanPrice | null | undefined = plan.price;
  const old = price?.original_amount != null && price.original_amount > price.amount ? price.original_amount : null;
  const span = price?.period_months ?? months;
  return (
    <>
      <div className="plan-price-line">
        <strong>{price ? money(price.amount, price.currency) : '0 ₽'}</strong>
        {price && (price.discount_percent ?? 0) > 0 && (
          <span className="plan-discount">-{price.discount_percent}%{old != null && <s>{money(old, price.currency)}</s>}</span>
        )}
      </div>
      <p className="plan-for"><PlanName id={plan.id} name={plan.name} /> {price ? (span === 1 ? 'за месяц' : `за ${span} мес.`) : 'без оплаты'}</p>
    </>
  );
}

/** Состав плана в порядке сервера: сначала то, что добавляет план, затем под заголовком то, что есть в предыдущих. Описание - подсказкой, как на сайте: высота плана при этом не меняется. */
export function FeatureList({ plan, heading }: { plan: Plan; heading: string }) {
  const [tip, setTip] = useState<{ text: string; left: number; bottom: number; host: Element } | null>(null);
  const fresh = plan.features.filter((feature) => !feature.from_previous_plan);
  const inherited = plan.features.filter((feature) => feature.from_previous_plan);
  const show = (node: HTMLElement, text: string | null | undefined) => {
    if (!text) return;
    const box = node.getBoundingClientRect();
    // настройки - модальный <dialog>: в верхнем слое подсказку из body не видно
    setTip({ text, left: Math.max(10, Math.min(box.left, window.innerWidth - 300)), bottom: window.innerHeight - box.top + 8, host: node.closest('dialog') ?? document.body });
  };
  const hide = () => setTip(null);
  const row = (feature: PlanFeature, muted: boolean) => (
    <li key={`${muted ? 'i' : 'n'}-${feature.id}`} className={muted ? 'is-included' : undefined}>
      <button
        type="button"
        className="plan-feature"
        aria-label={feature.description ? `${feature.label}: ${feature.description}` : undefined}
        disabled={!feature.description}
        onPointerEnter={(event) => { if (event.pointerType === 'mouse') show(event.currentTarget, feature.description); }}
        onPointerLeave={hide}
        onFocus={(event) => show(event.currentTarget, feature.description)}
        onBlur={hide}
        onClick={(event) => show(event.currentTarget, feature.description)}
      >
        <PlanIconSvg feature={feature} />
        <span>{feature.label}</span>
      </button>
    </li>
  );
  return (
    <ul className="plan-features">
      {fresh.map((feature) => row(feature, false))}
      {inherited.length > 0 && <li className="plan-included-heading">{heading}</li>}
      {inherited.map((feature) => row(feature, true))}
      {tip && createPortal(<div className="plan-tip" role="tooltip" style={{ left: tip.left, bottom: tip.bottom }}>{tip.text}</div>, tip.host)}
    </ul>
  );
}
