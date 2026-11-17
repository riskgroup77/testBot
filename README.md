# testBot

Telegram bot **Akmaljon yordamchi**: fizika testlari (Attestatsiya va Milliy sertifikat), javoblarni avtomatik tekshirish, video tahlillar va sertifikat berish.

## Ishga tushirish

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
cp .env.example .env                          # keyin .env ichiga tokenni yozing
.venv/bin/python main.py --no-browser
```

Sertifikatlar uchun Chrome yoki Chromium kerak (serverda: `apt install chromium`).

## Repoda nimalar yo'q

Quyidagilar maxfiy yoki katta bo'lgani uchun repoga qo'shilmagan:

- `.env`: bot tokeni va admin;
- `users.db`, `backups/`: o'quvchilar ma'lumotlari va natijalari;
- `content/`: test PDF'lari va videolar.

Ular serverga alohida ko'chiriladi.
