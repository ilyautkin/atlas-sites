# Atlas Sites MCP

## Обзор

MCP сервер для работы с сайтами из базы данных Atlas. Объединяет поиск сайтов и работу с файлами через SSH.

## Работа с сайтами MODX

### Определение CMS

Если сайт найден в базе Atlas (через `resolve_site`), значит он разработан на **MODX**.

### Темы MODX

Существует две темы:
- **Старая тема** (`modx3-circle`) — файл `assets/scss/override.scss`
- **Новая тема** (`theme`) — файл `assets/scss/style.scss`

Для определения темы использовать команду `detect_theme(domain)`.

### Редактируемые файлы

В новой теме (`theme`):
- `assets/scss/style.scss` — основные стили, кастомизация компонентов
- `assets/scss/settings/_variables.scss` — переменные: цвета, шрифты, размеры

В старой теме (`modx3-circle`):
- `assets/scss/override.scss` — переопределение стилей
- `assets/scss/_vars_override.scss` — переменные: цвета, шрифты, размеры

### Изменение цветов

Primary цвет и другие цвета темы определяются в `_variables.scss`:

```scss
$primary:    #50348f;
$secondary:  #02c39a;
$success:    #198754;
$info:       #0dcaf0;
$warning:    #ffc107;
$danger:     #dc3545;
$light:      #f8f9fa;
$dark:       #212529;
```

Hover-вариант для primary:
```scss
$hover-colors: (
    'primary': #261749,
);
```

## Автоматические бэкапы

Функция `write_file` автоматически создаёт `.backup` копию файла перед первой записью в сессии.

- Если бэкап уже существует (от предыдущей сессии) — он **не перезаписывается**
- Бэкап защищает оригинальное состояние файла
- Claude не нужно думать о бэкапах — они создаются автоматически

## Команды MCP

### Поиск сайтов

| Команда | Описание |
|---------|----------|
| `resolve_site(search)` | Поиск сайта в базе Atlas по домену |
| `resolve_site_from_text(text)` | Извлечь домен из текста и найти сайт |

### Работа с файлами

| Команда | Параметры | Описание |
|---------|-----------|----------|
| `detect_theme(domain)` | домен | Определить тему: `old`, `new`, `unknown` |
| `read_file(domain, path)` | домен, путь | Прочитать файл (путь от httpdocs/) |
| `write_file(domain, path, content)` | домен, путь, содержимое | Записать файл (автобэкап) |
| `file_exists(domain, path)` | домен, путь | Проверить существование файла |
| `list_files(domain, path, pattern)` | домен, путь, glob | Список файлов в директории |
| `clear_cache(domain)` | домен | Очистить MODX кэш (core/cache/) |
| `delete_backups(domain)` | домен | Удалить .backup файлы созданные в сессии |

### Заполнение контента (ContentBlocks)

| Команда | Параметры | Описание |
|---------|-----------|----------|
| `fill_site_content(domain, resource_id, rows)` | домен, ID ресурса, массив строк | Заполнить ContentBlocks контент ресурса |

Требует установленной темы (installer деплоит PHP runner автоматически).

### Примечания к путям

- Все пути указываются **относительно `httpdocs/`**
- Примеры: `assets/scss/style.scss`, `assets/scss/settings/_variables.scss`

## fill_site_content — JSON-схема

### Структура вызова

```python
fill_site_content(
    domain="example.nl",
    resource_id=1,          # homepage всегда 1
    rows=[...],             # массив строк (секций) страницы
)
```

### Строка (row)

```json
{
  "id": "r1",
  "layout": 1,
  "settings": {"paddingTop": "pt-0", "paddingBottom": "pb-0"},
  "columns": {
    "column": [ ]
  }
}
```

| layout ID | column refs |
|-----------|-------------|
| 1 (full-width) | `column` |
| 2 (one-column) | `one_column` |
| 3 (narrow-column) | `narrow_column` |
| 4 (two-columns 50/50) | `left`, `right` |
| 5 (two-columns 33/66) | `left`, `right` |
| 6 (two-columns 66/33) | `left`, `right` |
| 7 (three-columns) | `left`, `center`, `right` |

settings: `appearance` (bg-light/bg-dark/…), `paddingTop` (pt-0/pt-5/…), `paddingBottom`, `justify` (align-items-center/…), `classes`

### Типы полей (fields)

**title** (field 100)
```json
{"type": "title", "id": "f1", "value": "Heading", "settings": {"alignment": "text-center"}}
```

**richtext** (field 200)
```json
{"type": "richtext", "id": "f2", "value": "<p>HTML.</p>"}
```

**image** (field 300)
```json
{"type": "image", "id": "f3", "path": "/uploads/afbeelding/website-test.jpg", "alt": "Alt"}
```

**simple** — для одиночных полей: googlemap (1200), formalicious (700), partners (800), reviews (1600), gallery (600), resources (1000)
```json
{"type": "simple", "id": "f4", "field": 700, "value": "1"}
```
partners и reviews: `"value": "all"` ; formalicious/googlemap: `"value": "1"`

**buttons** — standalone кнопки (btn-container, field 400)
```json
{
  "type": "buttons", "id": "f5",
  "settings": {"alignment": "justify-content-center"},
  "buttons": [
    {"link": "/contact", "text": "Contact", "appearance": "btn-primary", "target": "_self"}
  ]
}
```

**repfield** — repeater (hero-slider 500, blocks 1100, faq 900, usp 1300)
```json
{"type": "repfield", "id": "f6", "field": 500, "settings": {}, "rows": [...]}
```

### Строки repeater по field ID

**hero-slider (500)**
```json
{
  "image": {"path": "/uploads/afbeelding/website-test.jpg", "alt": "", "container": "1920"},
  "title": {"value": "Hero title", "level": "h1", "alignment": "text-start"},
  "richtext": "<p>Subtext.</p>",
  "buttons": {"alignment": "justify-content-start", "items": [
    {"link": "/contact", "text": "CTA", "appearance": "btn-primary"}
  ]}
}
```

**blocks (1100)**
```json
{
  "image": {"path": "/uploads/afbeelding/website-test.jpg", "alt": "", "container": "420"},
  "title": {"value": "Card title"},
  "richtext": "<p>Card text.</p>",
  "buttons": {"items": [{"link": "/dienst", "text": "Meer info", "appearance": "btn-outline-primary"}]}
}
```

**faq (900)**
```json
{
  "title": {"value": "Vraag?"},
  "richtext": "<p>Antwoord.</p>"
}
```

**usp (1300)**
```json
{
  "image": {"path": "/uploads/afbeelding/website-test.jpg", "alt": "icon", "container": "80"},
  "text": "USP tekst",
  "link": "/diensten"
}
```

### Полный пример вызова

```python
fill_site_content("example.nl", 1, [
    # Hero — full-width, без отступов
    {
        "id": "r1", "layout": 1,
        "settings": {"paddingTop": "pt-0", "paddingBottom": "pb-0"},
        "columns": {"column": [
            {"type": "repfield", "id": "f1", "field": 500, "rows": [{
                "image": {"path": "/uploads/afbeelding/website-test.jpg", "alt": "", "container": "1920"},
                "title": {"value": "Welkom bij ons", "level": "h1"},
                "richtext": "<p>Korte introductie.</p>",
                "buttons": {"items": [{"link": "/contact", "text": "Neem contact op", "appearance": "btn-primary"}]}
            }]}
        ]}
    },
    # Tekst + foto — two-columns 50/50
    {
        "id": "r2", "layout": 4,
        "settings": {"justify": "align-items-center"},
        "columns": {
            "left": [
                {"type": "title", "id": "f2", "value": "Over ons"},
                {"type": "richtext", "id": "f3", "value": "<p>Introductietekst.</p>"}
            ],
            "right": [
                {"type": "image", "id": "f4", "path": "/uploads/afbeelding/website-test.jpg", "alt": "Over ons"}
            ]
        }
    }
])
```

## SSH подключение (внутренняя логика)

- **Порт по умолчанию:** 22622 (если не указан в API)
- **Credentials кэшируются** в памяти MCP сервера на время сессии
- При первом обращении к сайту credentials запрашиваются из Atlas API автоматически

## Типичный workflow для Claude

### Работа с файлами
1. Пользователь упоминает сайт → `detect_theme(domain)` (определит тему и закэширует credentials)
2. Прочитать нужные файлы → `read_file(domain, path)`
3. Записать изменения → `write_file(domain, path, content)` (бэкап создастся автоматически)

### ОБЯЗАТЕЛЬНО после завершения работы с файлами

**ВАЖНО: Claude НИКОГДА не должен выполнять `clear_cache` или `delete_backups` без явного подтверждения пользователя!**

Claude ДОЛЖЕН задать два вопроса последовательно и ДОЖДАТЬСЯ ответа:

1. **Спросить:** "Очистить кэш сайта?"
   - ДОЖДАТЬСЯ ответа пользователя
   - Только если пользователь ответил "да" → выполнить `clear_cache(domain)`
   - Если "нет" → НЕ выполнять, перейти к следующему вопросу

2. **Спросить:** "Удалить резервные копии изменённых файлов?"
   - ДОЖДАТЬСЯ ответа пользователя
   - Только если пользователь ответил "да" → выполнить `delete_backups(domain)`
   - Если "нет" → НЕ выполнять

**ЗАПРЕЩЕНО:**
- Выполнять `clear_cache` без подтверждения
- Выполнять `delete_backups` без подтверждения
- Пропускать эти вопросы
