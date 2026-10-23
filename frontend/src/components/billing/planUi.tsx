import DOMPurify from 'dompurify';
import { useState } from 'react';
import { money, type Plan, type PlanFeature, type PlanPrice } from '../../state/plans';

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
  return <span className="plan-badge" data-plan={id}>{name}</span>;
}

export function Price({ plan, months }: { plan: Plan; months: number }) {
  const price: PlanPrice | null | undefined = plan.price;
  if (!price) return null;
  const old = price.original_amount != null && price.original_amount > price.amount ? price.original_amount : null;
  const period = (price.period_months ?? months) === 1 ? 'за месяц' : `за ${price.period_months ?? months} мес.`;
  return (
    <div className="plan-price">
      <div className="plan-price-line">
        <strong>{money(price.amount, price.currency)}</strong>
        {old != null && <s>{money(old, price.currency)}</s>}
        {(price.discount_percent ?? 0) > 0 && <em>-{price.discount_percent}%</em>}
      </div>
      <span>{period}</span>
    </div>
  );
}

/** Состав плана в порядке сервера: сначала то, что добавляет план, затем под заголовком то, что пришло из прежнего. */
export function FeatureList({ plan, heading }: { plan: Plan; heading: string }) {
  const [open, setOpen] = useState<string>('');
  const fresh = plan.features.filter((feature) => !feature.from_previous_plan);
  const inherited = plan.features.filter((feature) => feature.from_previous_plan);
  const row = (feature: PlanFeature, muted: boolean) => (
    <li key={`${muted ? 'i' : 'n'}-${feature.id}`} className={muted ? 'is-included' : undefined}>
      <button type="button" className="plan-feature" aria-expanded={feature.description ? open === feature.id : undefined} disabled={!feature.description} title={feature.description} onClick={() => setOpen(open === feature.id ? '' : feature.id)}>
        <PlanIconSvg feature={feature} />
        <span>{feature.label}</span>
      </button>
      {feature.description && open === feature.id && <p className="plan-feature-note">{feature.description}</p>}
    </li>
  );
  return (
    <ul className="plan-features">
      {fresh.map((feature) => row(feature, false))}
      {inherited.length > 0 && <li className="plan-included-heading">{heading}</li>}
      {inherited.map((feature) => row(feature, true))}
    </ul>
  );
}
