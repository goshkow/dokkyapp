import { cancelLogin, login, useData } from '../../state/data';
import { guarded } from '../../state/overlay';

/** Экран входа: пока сессия не готова, интерфейс закрыт. */
export function AuthScreen({ pending }: { pending: boolean }) {
  const busy = useData((state) => state.loginBusy);
  const expired = useData((state) => state.loginExpired);
  const message = useData((state) => state.authMessage);
  return (
    <section id="auth-screen" aria-labelledby="auth-brand" data-pending={pending || undefined}>
      <div className="auth-content">
        <img src="/ui/assets/mark.png" className="auth-mark" alt="" draggable={false} />
        {!pending && (
          <>
            <h1 id="auth-brand">Докки</h1>
            <button id="auth-login" className="btn btn-primary" type="button" disabled={busy} onClick={guarded(login)}>
              {busy ? 'Ожидание входа…' : expired ? 'Войти снова' : 'Войти с помощью Докки'}
            </button>
            {busy && (
              <button id="auth-cancel" className="auth-link" type="button" onClick={() => void cancelLogin()}>
                Отмена
              </button>
            )}
            <button
              id="auth-register"
              className="auth-link"
              type="button"
              onClick={() => {
                const url = 'https://dokky.goshkow.com/?auth=register';
                if (window.koddiDesktop) window.location.href = `koddi://open-url?url=${encodeURIComponent(url)}`;
                else window.open(url, '_blank', 'noopener,noreferrer');
              }}
            >
              Регистрация
            </button>
            <p id="auth-status" role="status" aria-live="polite" data-waiting={busy || undefined}>
              {message || (busy ? 'Завершите вход в открывшемся браузере' : '')}
            </p>
          </>
        )}
      </div>
    </section>
  );
}
