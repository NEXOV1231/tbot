import os
from pathlib import Path

# Загружаем локальный .env, если он есть (не публикуйте его!).
# Формат простой: KEY=VALUE по строкам.
_env_file = Path(__file__).with_name(".env")
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Секреты НЕ храним в коде. Задаются через .env или переменные окружения:
#   PowerShell:  $env:STATICGRAM_TOKEN = "..."
#   CMD:         set STATICGRAM_TOKEN=...
STATICGRAM_TOKEN = os.environ.get("STATICGRAM_TOKEN", "")

# Список админов через запятую, например: "1780244365,123456789"
ADMIN_IDS = [
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")

# Публичный URL Mini App (кнопка в боте). Для локальных тестов можно http://127.0.0.1:8000.
MINI_APP_URL = os.environ.get("MINI_APP_URL", "")

# Разрешённые источники для CORS через запятую. По умолчанию — всё (только для локальной разработки!).
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
