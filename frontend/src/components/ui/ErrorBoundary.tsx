import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Что показать вместо упавшей части. */
  title?: string;
  /** Упало всё окно: сообщение на весь экран, повтор перезагружает страницу. */
  fatal?: boolean;
}

/** Сбой одной страницы не роняет всё приложение: показывается сообщение и кнопка «Повторить». */
export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Сбой интерфейса', error, info.componentStack);
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <div className={this.props.fatal ? 'error-state is-fatal' : 'error-state'} role="alert">
        <p>{this.props.title ?? 'Не удалось показать эту часть окна'}</p>
        <button
          type="button"
          className="btn"
          onClick={() => (this.props.fatal ? window.location.reload() : this.setState({ failed: false }))}
        >
          Повторить
        </button>
      </div>
    );
  }
}
