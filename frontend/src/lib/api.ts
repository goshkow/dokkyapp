export class ApiError extends Error {
  status: number;
  code?: string;
  /** Уточнение отказа (например `already_included` у оплаты) и время ожидания при частых запросах. */
  reason?: string;
  retryAfter?: number;
  constructor(message: string, status: number, code?: string, extra: { reason?: string; retryAfter?: number } = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.reason = extra.reason;
    this.retryAfter = extra.retryAfter;
  }
}

export const LOCAL_FAILURE = 'Докки не отвечает. Перезапустите приложение, если повтор не поможет.';

let onUnauthorized: () => void = () => undefined;
export const setUnauthorizedHandler = (handler: () => void) => { onUnauthorized = handler; };

/** Любой отказ `plan_required` закрывает запись в режиме разработки; обработчик ставит экран «нужен Plus». */
let planRequiredHandler: (error: ApiError) => void = () => undefined;
export const setPlanRequiredHandler = (handler: (error: ApiError) => void) => { planRequiredHandler = handler; };

const message = (detail: unknown, status: number): string => {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const value = (detail as { message?: unknown }).message;
    if (typeof value === 'string') return value;
  }
  return status >= 500 ? 'Что-то пошло не так. Попробуйте ещё раз.' : 'Запрос не выполнен. Попробуйте ещё раз.';
};

/** JSON-запрос к локальному серверу приложения. */
export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData) && !headers.has('content-type')) headers.set('content-type', 'application/json');
  let response: Response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    // до сайта запрос не дошёл бы и без интернета: не отвечает программа на этом компьютере
    throw new ApiError(LOCAL_FAILURE, 0, 'local_unavailable');
  }
  if (!response.ok) {
    if (response.status === 401) onUnauthorized();
    let detail: unknown;
    let code: string | undefined;
    let reason: string | undefined;
    let retryAfter: number | undefined;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = body.detail;
      if (detail && typeof detail === 'object') {
        const fields = detail as { code?: string; reason?: string; retry_after_seconds?: number };
        code = fields.code;
        reason = typeof fields.reason === 'string' ? fields.reason : undefined;
        retryAfter = typeof fields.retry_after_seconds === 'number' ? fields.retry_after_seconds : undefined;
      }
    } catch {
      detail = undefined;
    }
    const failure = new ApiError(message(detail, response.status), response.status, code, { reason, retryAfter });
    if (code === 'plan_required') planRequiredHandler(failure);
    throw failure;
  }
  return (await response.json()) as T;
}

export const post = <T = unknown>(path: string, body: unknown = {}, options: RequestInit = {}) =>
  api<T>(path, { ...options, method: 'POST', body: JSON.stringify(body) });

export const put = <T = unknown>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) });
export const patch = <T = unknown>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
export const del = <T = unknown>(path: string) => api<T>(path, { method: 'DELETE' });

export const cloudPath = (path: string) => `/api/dokky/cloud/${path}`;
