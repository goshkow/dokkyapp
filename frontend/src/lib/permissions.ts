import type { PermissionProfile } from './types';

/** Режимы подтверждения действий агента в том порядке, в каком их видит человек. */
export const PERMISSION_OPTIONS: { value: 'ask' | 'trusted' | 'full'; label: string }[] = [
  { value: 'ask', label: 'Вручную' },
  { value: 'trusted', label: 'Автоматически' },
  { value: 'full', label: 'Разрешить всё' },
];

export function permissionLabel(profile: PermissionProfile | undefined): string {
  if (profile === 'read-only') return 'Только чтение';
  return PERMISSION_OPTIONS.find((item) => item.value === profile)?.label ?? 'Вручную';
}
