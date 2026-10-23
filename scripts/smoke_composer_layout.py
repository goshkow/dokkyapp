"""Геометрия настоящего поля ввода при вложениях, переносах и смене масштаба."""
from smoke_next import check, development_api, open_chat
from smoke_pasted_text import paste
from ui_testkit import browser_page


def geometry(page):
    return page.evaluate("""() => {
      const rect = n => { const r=n.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom,right:r.right,cy:r.y+r.height/2}; };
      const input=document.querySelector('#prompt'), field=input.closest('.composer-field');
      return {field:rect(field),input:rect(input),scroll:input.scrollHeight, multi:field.dataset.multiline,
        buttons:[...field.querySelectorAll('button')].filter(n=>n.getBoundingClientRect().width).map(rect)};
    }""")


def centered(page, label):
    box = geometry(page)
    check(box['multi'] == 'false', f'{label}: однострочное поле помечено многострочным')
    check(abs(box['input']['cy'] - box['field']['cy']) <= 1.5, f'{label}: строка съехала {box}')
    check(all(abs(button['cy'] - box['field']['cy']) <= 1.5 for button in box['buttons']), f'{label}: иконки съехали')


def scenario_composer_alignment_and_attachment_font():
    api = development_api()
    with browser_page(api) as page:
        page.wait_for_selector('#prompt')
        open_chat(page)
        page.route('**/api/attachments', lambda route: route.fulfill(status=503, json={'detail': 'Тест ошибки'}))
        centered(page, 'пустое')
        page.fill('#prompt', 'Одна строка')
        centered(page, 'строка')
        paste(page, '# Задача\n' + 'Текст. ' * 1000)
        page.wait_for_selector('.attachment-card.upload-error')
        for theme in ['dark', 'light']:
            for size in [8, 14, 20]:
                page.evaluate("""({size,theme}) => {document.documentElement.dataset.theme=theme;
                  document.documentElement.style.setProperty('--base',size+'px');dispatchEvent(new Event('resize'));} """, {'size': size, 'theme': theme})
                page.wait_for_timeout(150)
                centered(page, f'{theme}/{size}/вложение')
                boxes = page.locator('.attachment-card').evaluate("""n => {
                  const r=e=>{const b=e.getBoundingClientRect();return {y:b.y,b:b.bottom,right:b.right};};
                  return {card:r(n), name:r(n.querySelector('.file-name')),status:r(n.querySelector('.attachment-state')),button:r(n.querySelector('.attachment-as-text'))};
                }""")
                check(boxes['name']['b'] <= boxes['status']['y'] + 1, f'имя налезло на статус: {boxes}')
                check(boxes['status']['b'] <= boxes['button']['y'] + 1, f'статус налез на кнопку: {boxes}')
                check(boxes['button']['b'] <= boxes['card']['b'] and boxes['button']['right'] <= boxes['card']['right'], 'кнопка обрезана карточкой')
        page.evaluate("document.documentElement.dataset.theme='dark';document.documentElement.style.setProperty('--base','14px');dispatchEvent(new Event('resize'))")
        page.wait_for_timeout(200)
        page.screenshot(path='/tmp/dokky-composer-aligned.png')
        check(not page.errors, str(page.errors))


def scenario_composer_rewrap_on_resize():
    with browser_page(development_api()) as page:
        page.wait_for_selector('#prompt')
        open_chat(page)
        page.fill('#prompt', 'Текст для проверки переносов и пересчёта высоты. ' * 4)
        page.wait_for_timeout(200)
        wide = geometry(page)
        page.set_viewport_size({'width': 900, 'height': 860})
        page.wait_for_timeout(200)
        narrow = geometry(page)
        check(narrow['input']['h'] > wide['input']['h'], f'при сужении высота не пересчиталась: {wide} / {narrow}')
        for box in [wide, narrow]:
            check(box['input']['b'] <= box['field']['b'] and box['input']['y'] >= box['field']['y'], 'текст за рамкой поля')
            check(all(button['right'] <= box['field']['right'] + 1 for button in box['buttons']), 'иконка за рамкой')
        page.set_viewport_size({'width': 1280, 'height': 860})
        page.wait_for_timeout(200)
        check(abs(geometry(page)['input']['h'] - wide['input']['h']) <= 1, 'поле не уменьшилось после расширения')
        page.fill('#prompt', '')
        centered(page, 'пустое после переносов')
        check(not page.errors, str(page.errors))


SCENARIOS = [scenario_composer_alignment_and_attachment_font, scenario_composer_rewrap_on_resize]
