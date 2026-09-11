# Kaizen 1% — Telegram-бот

Той самий трекер трьох звичок (Тригер / Двигун / Замок), що і HTML-версія,
але як Telegram-бот з нагадуваннями о 09:00 / 12:00 / 21:00 (Europe/Kyiv).

## 1. Отримати токен бота (обов'язково зробити самому — я не можу створити бота за вас)

1. Відкрийте Telegram, знайдіть **@BotFather**.
2. Надішліть `/newbot`, дайте боту ім'я та username (має закінчуватись на `bot`, напр. `kaizen1_tracker_bot`).
3. BotFather видасть токен виду `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Збережіть його — це секрет, не публікуйте.

## 2. Встановити залежності

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Запустити локально (найпростіший варіант — перевірити, що працює)

```bash
export KAIZEN_BOT_TOKEN="ваш_токен_від_BotFather"   # Windows (PowerShell): $env:KAIZEN_BOT_TOKEN="..."
python bot.py
```

Відкрийте бота в Telegram і надішліть `/start`. Поки термінал з `python bot.py` відкритий —
бот працює і надсилатиме нагадування. Закриєте термінал — бот зупиниться.

## 4. Зробити так, щоб бот працював цілодобово (обов'язково для реальних нагадувань)

Бот має бути постійно запущений на якомусь сервері — телефон чи ноутбук, який іноді
вимикається, для цього не підходить. Реалістичні варіанти на 2026 рік:

### Варіант А — безкоштовний хмарний хостинг (найпростіше для одного бота)
Railway.app, Render.com або Fly.io — усі мають безкоштовний або дешевий (~$5/міс) план,
що тримає процес живим 24/7. Загальний процес однаковий для всіх трьох:
1. Заведіть акаунт, підключіть GitHub-репозиторій із цими файлами (або завантажте файли напряму).
2. Вкажіть команду запуску: `python bot.py`.
3. У розділі Environment Variables додайте `KAIZEN_BOT_TOKEN` зі значенням вашого токена.
4. Задеплойте — сервіс сам перезапускатиме бота, якщо він впаде.

### Варіант Б — власний VPS (Ubuntu), якщо вже є сервер
```bash
sudo apt update && sudo apt install python3-venv -y
git clone <ваш репозиторій> kaizen_bot && cd kaizen_bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```
Створіть systemd-сервіс `/etc/systemd/system/kaizen-bot.service`:
```ini
[Unit]
Description=Kaizen 1% Telegram bot
After=network.target

[Service]
WorkingDirectory=/home/USER/kaizen_bot
Environment=KAIZEN_BOT_TOKEN=ваш_токен
ExecStart=/home/USER/kaizen_bot/venv/bin/python bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kaizen-bot
```

## 5. Команди бота

- `/start` — реєструє чат, показує старт кривої та розклад нагадувань
- `/today` — показує всі три пункти зараз (якщо пропустили нагадування)
- `/status` — поточна серія, найкраща серія, % виконаних днів, ціль на сьогодні

## 6. Дані

Все зберігається у файлі `kaizen.db` (SQLite) поруч зі скриптом — регулярно робіть
резервну копію цього файлу (наприклад, щотижня копіюйте на Google Drive), оскільки
на безкоштовних хостингах диск іноді очищається при redeploy.

## 7. Чесно про обмеження

- Я не можу створити самого бота у BotFather чи задеплоїти його за вас — це вимагає
  ваших особистих дій (крок 1 і крок 4), оскільки токен і хостинг прив'язані до вашого акаунта.
- Формула кривої та часи нагадувань (09:00 / 12:00 / 21:00) — ті самі, що в
  Google Calendar і HTML-трекері, які вже налаштовані раніше.
