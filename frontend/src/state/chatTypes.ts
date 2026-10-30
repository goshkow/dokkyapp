import type { Change } from '../lib/diff';
import type { Message } from '../lib/journal';

export type { Change, Message };

export type StepStatus = 'pending' | 'running' | 'completed' | 'blocked' | 'cancelled' | 'failed' | 'interrupted';

export interface PlanStep {
  title: string;
  status: StepStatus;
}

export interface PlanSnapshot {
  turn_id?: number | string;
  tracking_turn_id?: number | string;
  tracking_status?: string;
  steps?: PlanStep[];
  tracking_steps?: PlanStep[];
  document?: string;
  ready?: boolean;
  executed?: boolean;
}

export interface AgentRequest {
  id: string | number;
  kind: 'approval' | 'question' | string;
  chat_id?: number | string;
  turn_id?: number | string;
  payload: { question?: string; questions?: AgentQuestion[]; name?: string; arguments?: unknown; action?: ActionSummary; review?: ActionReview | null };
}

/** Вопрос агента (task.ask_user): варианты необязательны, свой ответ текстом есть всегда. */
export interface AgentQuestion {
  question: string;
  header?: string;
  options?: { label: string; description?: string }[];
  multi_select?: boolean;
}

/** Понятное описание действия агента с сервера (policy/describe.py). */
export interface ActionSummary {
  title: string;
  target: string;
  detail: string;
  kind: 'file' | 'command' | 'other' | string;
  outside_project: string[];
  dangerous: boolean;
}

/** Итог автопроверки действия в режиме «Автоматически». */
export interface ActionReview {
  available: boolean;
  allowed: boolean;
  necessity: number | null;
  safety: number | null;
  reason: string;
}

export interface QueueItem {
  id: number | string;
  version: number;
  status: 'queued' | 'running' | 'failed' | string;
  content: string;
}

export interface QueueSnapshot {
  paused: boolean;
  items: QueueItem[];
}

export interface UsageBundle {
  usage?: { cached_tokens?: number; prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; context_tokens?: number };
  dokky?: {
    usage?: { cached_tokens?: number; prompt_tokens?: number; context_tokens?: number };
    cache?: { prompt_tokens?: number; valid_until?: string };
    billing?: { window_used_percent?: number; window_resets_at?: string | null; remaining_percent?: number };
    context?: { used_tokens?: number; window_tokens?: number; percent?: number };
  };
}

export interface CodingSession {
  version: number;
  reasoning_mode: 'light' | 'pro' | 'max' | string;
  pending_reasoning_mode?: string | null;
  history?: unknown[];
  last_usage?: UsageBundle;
  compact_at_tokens?: number;
  /** Оценка размера контекста по истории, когда точных токенов от модели ещё нет. */
  estimated_context_tokens?: number;
  /** Модель сервера меняет режим без потери кэша: подтверждение смены не нужно. */
  cache_preserved?: boolean;
}

export interface Attachment {
  id: number | string;
  original_name?: string;
  name?: string;
  media_type?: string;
  mime_type?: string;
  size?: number;
  /** Локальная ссылка на превью выбранной картинки; на сервер не уходит. */
  preview?: string;
  /** исходник автоматической вставки, только в памяти черновика; API получает лишь id */
  pastedText?: string;
}

export interface UploadJob {
  key: string;
  file: File;
  error: string | null;
  running: boolean;
  preview?: string;
  path: string;
  pastedText?: string;
}

export interface ChatStatusLine {
  text: string;
}

export type { PlanSnapshot as Plan };
