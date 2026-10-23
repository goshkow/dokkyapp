(() => {
  if (!window.qt?.webChannelTransport || typeof QWebChannel !== 'function') return;
  new QWebChannel(qt.webChannelTransport, channel => {
    const bridge = channel.objects.koddiWindow;
    if (!bridge) return;
    window.koddiWindowBridge = bridge;
    // на macOS кнопки окна системные (их ставит хост), своих не рисуем
    const mac = document.documentElement.classList.contains('platform-mac');
    const controls = mac ? null : document.createElement('nav');
    if (controls) {
      controls.className = 'desktop-window-controls';
      document.documentElement.dataset.captionMode = 'windows-caption-glyph';
      controls.setAttribute('aria-label', 'Управление окном');
      for (const [action, label, symbol] of [
        ['minimize', 'Свернуть', '\uE921'], ['maximize', 'Развернуть', '\uE922'], ['close', 'Закрыть', '\uE8BB'],
      ]) {
        const button = document.createElement('button');
        button.type = 'button'; button.dataset.windowAction = action;
        button.setAttribute('aria-label', label); button.title = label;
        button.textContent = symbol;
        button.onclick = () => bridge.command(action);
        controls.append(button);
      }
      document.body.append(controls);
    }
    // окно тянется за всю верхнюю полосу, кроме кнопок, полей, диалогов и меню
    const skip = 'button,a,input,select,textarea,label,summary,dialog,[role="button"],[role="combobox"],[role="tab"],[role="menu"],[role="dialog"],[contenteditable="true"],.popover,.ai-product-switch,.desktop-window-controls';
    const isDrag = event => event.clientY < 46 && !event.target.closest?.(skip);
    // без preventDefault страница начинает выделять текст под курсором, пока окно тянется
    document.addEventListener('mousedown', event => {
      if (event.button !== 0 || !isDrag(event)) return;
      event.preventDefault();
      getSelection()?.removeAllRanges();
    });
    document.addEventListener('pointerdown', event => {
      if (event.button === 0 && isDrag(event)) { getSelection()?.removeAllRanges(); bridge.command('move'); }
    });
    document.addEventListener('dblclick', event => {
      if (event.button === 0 && isDrag(event)) bridge.command('maximize');
    });
    window.koddiWindowState = maximized => {
      if (!controls) return;
      const button = controls.querySelector('[data-window-action="maximize"]');
      button.setAttribute('aria-label', maximized ? 'Восстановить' : 'Развернуть');
      button.title = maximized ? 'Восстановить' : 'Развернуть';
      button.textContent = maximized ? '\uE923' : '\uE922';
    };
    const updateFrame = frame => {
      const root = document.documentElement;
      root.dataset.captionMode = frame.mode;
      for (const button of controls?.querySelectorAll('button') ?? []) {
        button.classList.toggle('native-hover', button.dataset.windowAction === frame.hover);
      }
      window.koddiWindowState(frame.maximized);
    };
    bridge.frameChanged.connect(updateFrame);
    bridge.requestState();
    document.documentElement.dataset.windowControls = 'ready';
    window.dispatchEvent(new Event('koddi-bridge'));
  });
})();
