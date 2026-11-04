import type { SVGProps } from 'react';

/* значки, нарисованные для Докки: их нет в lucide. Пути те же, что в наборе сайта (assets/index.html) */
type IconProps = SVGProps<SVGSVGElement>;

const Svg = (props: IconProps) => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" {...props} />;

/** новый чат: перерисованный lucide square-pen (ISC) */
export const NewChatPen = (props: IconProps) => (
  <Svg fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" {...props}>
    <path d="M10.5 4H7a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-3.500" />
    <path d="M17.6 3.800a1.950 1.950 0 0 1 2.750 2.750L13 13.900l-3.400.7.7-3.400z" />
  </Svg>
);

export const FillEdit = (props: IconProps) => (
  <Svg fill="currentColor" {...props}>
    <path d="M16.6 3.3a2.4 2.4 0 0 1 3.4 0l.7.7a2.4 2.4 0 0 1 0 3.4L19.1 9 15 4.9z" />
    <path d="m13.7 6.2 4.1 4.1-9.5 9.5a2 2 0 0 1-.9.5l-3.9 1.1a.8.8 0 0 1-1-1l1.1-3.9a2 2 0 0 1 .5-.9z" />
  </Svg>
);

export const FillDrop = (props: IconProps) => (
  <Svg fill="currentColor" {...props}>
    <path d="M12 3s6.5 6.4 6.5 11.3a6.5 6.5 0 0 1-13 0C5.5 9.4 12 3 12 3Z" />
  </Svg>
);

export const FillPin = (props: IconProps) => (
  <Svg fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" {...props}>
    <g transform="rotate(45 12 12)">
      <path d="M12 17v5" />
      <path fill="currentColor" strokeWidth={1.2} d="M9 10.8a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2v.8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9a2 2 0 0 1-1.1-1.8V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
    </g>
  </Svg>
);

export const FillTrash = (props: IconProps) => (
  <Svg fill="currentColor" {...props}>
    <path d="M9.5 3h5A1.5 1.5 0 0 1 16 4.5V6h4a1 1 0 1 1 0 2H4a1 1 0 0 1 0-2h4V4.5A1.5 1.5 0 0 1 9.500 3Z" />
    <path fillRule="evenodd" d="M5.6 9.5h12.8l-.8 10.300a2 2 0 0 1-2 1.700H8.400a2 2 0 0 1-2-1.700zM10 12.300a.8.8 0 0 0-.8.8v4.800a.8.8 0 0 0 1.600 0v-4.800a.8.8 0 0 0-.8-.8Zm4 0a.8.8 0 0 0-.8.8v4.800a.8.8 0 0 0 1.600 0v-4.800a.8.8 0 0 0-.8-.8Z" />
  </Svg>
);

export const FillDownload = (props: IconProps) => (
  <Svg fill="currentColor" {...props}>
    <path d="M10.500 3.500a1.500 1.500 0 0 1 3 0V10h2.600a.9.9 0 0 1 .7 1.500l-4.100 4.600a.9.9 0 0 1-1.400 0l-4.100-4.600a.9.9 0 0 1 .7-1.500h2.600z" />
    <path d="M4 17a1 1 0 0 1 1 1v1.500h14V18a1 1 0 1 1 2 0v2a1.500 1.500 0 0 1-1.500 1.500h-15A1.500 1.500 0 0 1 3 20v-2a1 1 0 0 1 1-1Z" />
  </Svg>
);

export const FillChat = (props: IconProps) => (
  <Svg fill="currentColor" {...props}>
    <path d="M12 3c5.200 0 9.500 3.700 9.500 8.500S17.200 20 12 20a11 11 0 0 1-3-.42l-4.800 1.400a.6.6 0 0 1-.74-.78l1.200-3.500A8 8 0 0 1 2.500 11.500C2.500 6.700 6.800 3 12 3Z" />
  </Svg>
);

/** значок пункта меню по действию, которое прислал сайт. Новое действие просто останется без значка */
export function actionIcon(action: string) {
  const Icon = { rename: FillEdit, appearance: FillDrop, pin: FillPin, unpin: FillPin, delete: FillTrash, download: FillDownload, open: FillChat }[action];
  return Icon ? <Icon aria-hidden="true" /> : null;
}
