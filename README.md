# IVS Player + Service Worker — тест на Render Free

Отдельный эксперимент для проверки официального Amazon IVS Web Player SDK и Service Worker на iPhone Safari. Проект не меняет и не использует существующие приложения в `/root/ocr/groq`.

## Что внутри

- `Dockerfile` и `render.yaml` для Render.
- Минимальный Python HTTP-сервер с health check `/healthz`.
- Страница диагностики с адаптацией под телефон.
- Официальный Amazon IVS Player SDK **1.57.0** и его Service Worker loader.
- Лог браузерных событий, ошибок, состояний плеера и времени до первого события `playing`.

Версия 1.57.0 сверена с официальными AWS IVS Web Player docs / release notes от 1 октября 2026.

## Развёртывание

1. Скопируй содержимое этой папки в **новый отдельный GitHub-репозиторий**. Не добавляй приватные playback URL или любые токены в Git.
2. В Render выбери **New → Blueprint** и подключи репозиторий с `render.yaml`. Альтернатива — создать Web Service с Dockerfile.
3. Оставь Free plan. Render выдаст адрес вида `https://ИМЯ.onrender.com`; кастомный домен для этого теста не нужен.
4. Открой HTTPS-адрес в Safari на iPhone.
5. Вставь HLS playback URL (обычно заканчивается на `.m3u8`) и нажми «Запустить». Если Safari блокирует автоматическое воспроизведение, нажми Play на самом видео.
6. Нажми «Проверить Service Worker» и смотри лог. Для диагностики можно скопировать лог вручную.

## Важные ограничения

- Service Worker должен быть доступен с того же origin, что и страница; поэтому файл `amazon-ivs-service-worker-loader.js` лежит в `public`.
- В официальной инструкции AWS IVS Service Worker нацелен на снижение задержки в браузерах с нативным воспроизведением, прежде всего iOS Safari. Это **не гарантия** минимальной задержки.
- IVS Player оптимизирован прежде всего для Amazon IVS. Он может не воспроизвести произвольный Kick HLS URL или не дать ожидаемого выигрыша задержки. Результат нужно измерить на конкретном потоке.
- Значение `FIRST_PLAYING_MS` — время от нажатия кнопки до события `playing`, а не точная задержка эфира.
- Render Free может усыплять сервис после простоя; первый запуск после сна может быть медленным.
- Не вводи stream key, cookie, пароль или токены аккаунта. Не публикуй приватный HLS URL в открытом репозитории и не пересылай его в логи.

## Локальная проверка (опционально)

```bash
python3 server.py
```

Открой `http://127.0.0.1:10000/`. Локальный loopback допускается для тестирования Service Worker; публичный HTTP IP — нет.

## Официальные ссылки

- AWS IVS Web Player: https://docs.aws.amazon.com/ivs/latest/LowLatencyUserGuide/web-getting-started.html
- AWS IVS release notes: https://docs.aws.amazon.com/ivs/latest/LowLatencyUserGuide/release-notes.html
- AWS IVS Player guide: https://docs.aws.amazon.com/ivs/latest/LowLatencyUserGuide/player.html
