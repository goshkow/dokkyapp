import { cancelLogin, login, useData } from '../../state/data';
import { guarded } from '../../state/overlay';

const REGISTER_URL = 'https://dokky.goshkow.com/?auth=register';

/** Экран входа: пока сессия не готова, интерфейс закрыт. Под кнопкой входа либо «Регистрация», либо «Отмена» ожидания. */
export function AuthScreen({ pending }: { pending: boolean }) {
  const busy = useData((state) => state.loginBusy);
  return (
    <section id="auth-screen" aria-labelledby="auth-brand" data-pending={pending || undefined}>
      <div className="auth-content">
        <img src="/ui/assets/mark.png" className="auth-mark" alt="" draggable={false} />
        {!pending && (
          <>
            <h1 id="auth-brand">Докки</h1>
            <button id="auth-login" className="btn btn-primary" type="button" disabled={busy} aria-busy={busy || undefined} onClick={guarded(login)}>
              {busy ? 'Ожидание входа…' : 'Войти с помощью Докки'}
            </button>
            {busy ? (
              <button id="auth-cancel" className="auth-link" type="button" onClick={() => void cancelLogin()}>
                Отмена
              </button>
            ) : (
              <button
                id="auth-register"
                className="auth-link"
                type="button"
                onClick={() => {
                  if (window.koddiDesktop) window.location.href = `koddi://open-url?url=${encodeURIComponent(REGISTER_URL)}`;
                  else window.open(REGISTER_URL, '_blank', 'noopener,noreferrer');
                }}
              >
                Регистрация
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}
