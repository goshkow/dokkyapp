"""Вход работника PDF в собранном приложении, у которого одна исполняемая программа.

Импортируется первой строкой web_host: работнику незачем загружать Qt и сервер.
"""
import sys

if "--read-pdf" in sys.argv:
    from koddi.documents.pdf_worker import main

    sys.exit(main())

# проверка сборки: читает ли HEIC и другие картинки то, что в неё попало
if "--check-image" in sys.argv:
    from koddi.attachments.recognize import check_image

    sys.exit(check_image(sys.argv[sys.argv.index("--check-image") + 1]))
