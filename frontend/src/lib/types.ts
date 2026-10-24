/** Подтверждения действий агента: вручную, проверка Докки, без подтверждений, только чтение. */
export type PermissionProfile = 'ask' | 'trusted' | 'full' | 'read-only';

/** Контракт с бэкендом приложения. Денежных величин здесь нет: клиент их не получает и не хранит. */
export type Id = number | string;

export interface Project {
  id: number;
  name: string;
  path: string;
}

export interface Chat {
  id: Id;
  title: string;
  mode?: string;
  model?: string;
  project_id?: number | null;
  updated_at?: string;
  created_at?: string;
  version?: number;
  /** Закреплённые чаты Докки стоят в начале списка. */
  pinned?: boolean;
  active_run?: { id: string } | null;
}

export interface ReasoningModeInfo {
  id: string;
  name: string;
  compact_at_tokens: number;
}

export interface ModelOption {
  tag: string;
  name: string;
  source: string;
  context_tokens: number;
  /** У каждого режима своё окно контекста (порог сжатия). */
  reasoning_modes?: ReasoningModeInfo[];
}

export interface ModeOption {
  id: string;
  name: string;
  builtin?: boolean;
  tools?: string[];
}

export interface CloudModel {
  id: string;
  name: string;
  roles?: string[];
  available?: boolean;
}

export interface Account {
  authenticated: boolean;
  internal_test?: boolean;
  user?: { id?: string; display_name?: string };
  bootstrap?: {
    models?: CloudModel[];
    preferences?: Record<string, unknown>;
    capabilities?: Record<string, unknown>;
  };
}

export interface AppSettings {
  theme: 'dark' | 'light' | 'system';
  ui_motion: 'system' | 'full' | 'reduced';
  chat_width: 'compact' | 'standard' | 'wide';
  text_size: number;
  ui_surface: 'classic' | 'chat-dark' | 'side-dark';
  /** Отрисовка окна: программная нужна, когда оверлей видеокарты принимает приложение за игру. */
  render_mode: 'gpu' | 'software';
  code_font: string;
  /** Шрифт интерфейса: Inter из приложения или системный. */
  ui_font: 'inter' | 'system';
  /** Личные инструкции для режима разработки: уходят агенту как правила пользователя. */
  dev_instructions: string;
  launch_at_login: boolean;
  enter_sends: boolean;
  permission_profile: PermissionProfile;
  private_network: boolean;
  selected_model: string;
  left_panel_collapsed: boolean;
  right_panel_collapsed: boolean;
  coding_reasoning_mode: 'light' | 'pro' | 'max';
  coding_keepalive: boolean;
  coding_runtime_extensions: boolean;
}

export const defaultSettings: AppSettings = {
  theme: 'dark',
  ui_motion: 'system',
  chat_width: 'standard',
  text_size: 14,
  ui_surface: 'classic',
  render_mode: 'gpu',
  code_font: 'system',
  ui_font: 'inter',
  dev_instructions: '',
  launch_at_login: false,
  enter_sends: true,
  permission_profile: 'trusted',
  private_network: true,
  selected_model: '',
  left_panel_collapsed: false,
  right_panel_collapsed: false,
  coding_reasoning_mode: 'pro',
  coding_keepalive: true,
  coding_runtime_extensions: false,
};

/** Сброс использования: докупка процентов к остатку текущего плана (готовый блок сервера, ничего не считается в клиенте). */
export interface UsageReset {
  available: boolean;
  /** Почему недоступен: usage_above_threshold, no_paid_plan, unlimited. */
  reason: string | null;
  thresholdPercent: number;
  options: Array<{ percent: number; price: number | null; remainingAfter: number | null }>;
}

/** Остаток лимитов и состояние подписки: проценты, время освобождения и доступ к режимам. */
export interface Limits {
  windowRemaining: number | null;
  windowResetsAt: string | null;
  windowHours: number;
  remaining: number | null;
  expiresAt: string | null;
  /** Тариф с сервера: free, trial, base, super (Plus), premium, ultra. */
  plan: string | null;
  trial: boolean;
  paidActive: boolean;
  /** Остаток подписки закончился совсем. */
  usageExhausted: boolean;
  /** Хеш каталога планов: сменился - планы перечитываются. */
  catalogVersion: string | null;
  usageReset: UsageReset | null;
  /** Доступ к Докки Code: `allowed` false у Free, пробного периода и Base (и когда нет кредитов тестера). */
  developer: { allowed: boolean | null; reason: string; message: string };
}
