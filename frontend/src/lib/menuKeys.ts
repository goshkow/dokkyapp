const ITEMS = '[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"],[role="option"]';

/** Стрелки, Home, End и поиск пункта по первой букве внутри меню; true - клавиша обработана. */
export function moveMenuFocus(event: { key: string; ctrlKey: boolean; metaKey: boolean; preventDefault(): void }, menu: HTMLElement | null): boolean {
  const items = [...(menu?.querySelectorAll<HTMLElement>(ITEMS) ?? [])].filter((item) => !(item as HTMLButtonElement).disabled);
  const index = items.indexOf(document.activeElement as HTMLElement);
  let next: HTMLElement | undefined;
  if (event.key === 'ArrowDown') next = items[(index + 1) % items.length];
  else if (event.key === 'ArrowUp') next = items[(index - 1 + items.length) % items.length];
  else if (event.key === 'Home') next = items[0];
  else if (event.key === 'End') next = items.at(-1);
  else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && event.key !== ' ') {
    next = items.find((item) => item.textContent?.trim().toLocaleLowerCase().startsWith(event.key.toLocaleLowerCase()));
  }
  if (!next) return false;
  event.preventDefault();
  next.focus({ preventScroll: true });
  return true;
}
