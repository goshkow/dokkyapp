(() => {
  if (!window.qt?.webChannelTransport || typeof QWebChannel !== 'function') return;
  new QWebChannel(qt.webChannelTransport, channel => {
    const bridge = channel.objects.koddiWindow;
    if (!bridge) return;
    window.koddiWindowBridge = bridge;
    const controls = document.createElement('nav');
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
    const strip = document.createElement('div');strip.className='desktop-drag-strip';document.body.append(strip);
    const isDrag = target => target.closest('.app-topbar,.chat-header,.sidebar-brand,.sidebar-top,.inspector-heading,.desktop-drag-strip') &&
      !target.closest('button,a,input,select,textarea,[role="button"],[role="combobox"]');
    // без preventDefault страница выделяет текст под курсором, пока окно тянется
    document.addEventListener('mousedown', event => {
      if (event.button === 0 && isDrag(event.target)) { event.preventDefault(); getSelection()?.removeAllRanges(); }
    });
    document.addEventListener('pointerdown', event => {
      if (event.button === 0 && isDrag(event.target)) bridge.command('move');
    });
    document.addEventListener('dblclick', event => {
      if (event.button === 0 && isDrag(event.target)) bridge.command('maximize');
    });
    window.koddiWindowState = maximized => {
      const button = controls.querySelector('[data-window-action="maximize"]');
      button.setAttribute('aria-label', maximized ? 'Восстановить' : 'Развернуть');
      button.title = maximized ? 'Восстановить' : 'Развернуть';
      button.textContent = maximized ? '\uE923' : '\uE922';
    };
    const updateFrame = frame => {
      const root = document.documentElement;
      root.dataset.captionMode = frame.mode;
      for (const button of controls.querySelectorAll('button')) {
        button.classList.toggle('native-hover', button.dataset.windowAction === frame.hover);
      }
      window.koddiWindowState(frame.maximized);
    };
    bridge.frameChanged.connect(updateFrame);
    bridge.requestState();
    document.documentElement.dataset.windowControls = 'ready';
  });
})();
