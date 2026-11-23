# Развертывание

## Требования

- Python 3.11 или новее.
- 1С:УНФ с включённым OData v3 и пользователем с минимально необходимыми правами.
- Загруженное и активированное расширение `TEMA_API` для маршрутов, помеченных
  как bridge в [контракте API](API.md).
- Windows и установленная платформа 1С требуемой версии для операций Designer и
  COM-скриптов публикации/настройки безопасного режима.
- ReportLab 4.4.9 для создания спецификации.

## Настройка Python

1. Создайте виртуальное окружение и установите `requirements.txt`.
2. Скопируйте `config/settings.example.json` в `config/settings.local.json`.
3. Укажите URL OData и доступы. Если используется файл профиля учётных данных,
   настройте `credentials_file` и `credentials_path` вместо копирования пароля.
4. Заполните `bridge_url` после публикации HTTP-сервиса расширения.
5. Установите случайный `TEMA_API_TOKEN` длиной не менее 32 символов в окружении
   процесса. Не храните его в исходниках и не отправляйте в Git.
6. Запустите `python -m tema_api.server --config config/settings.local.json`.

Для внешнего браузерного клиента точно укажите разрешённый HTTPS origin в
`cors_allowed_origins`. Добавьте обратный прокси с TLS и направьте его на
`127.0.0.1:8770`. `/health` не проверяет доступность 1С.

## Установка расширения

Исходная конфигурация расширения расположена в `tema_api/bridge`. Задайте в
локальном конфиге путь `platform` к `1cv8.exe` и `infobase` к тестовой базе. Команды
выполняются только при явном запуске:

```powershell
python -m tema_api.manage load --config config/settings.local.json
python -m tema_api.manage check --config config/settings.local.json
python -m tema_api.manage activate --config config/settings.local.json
python -m tema_api.manage export --config config/settings.local.json
```

Скрипт `tema_api/Publish-Bridge.ps1` добавляет HTTP-сервис `TEMA_API` в файл
публикации и сохраняет резервную копию. Скрипт `tema_api/Enable-Printing.ps1`
отключает безопасный режим только для расширения с именем `TEMA_API` в выбранной
базе. В тестовой базе такое разрешение ранее было выдано пользователем; для каждой
новой базы решение оценивается и принимается отдельно администратором 1С.

Перед публикацией проверьте резервную копию, права пользователя, URL сервиса,
сертификат HTTPS, firewall, CORS и доступность API с клиентской сети. Не публикуйте
OData или HTTP-расширение напрямую в интернет без отдельной защиты.

