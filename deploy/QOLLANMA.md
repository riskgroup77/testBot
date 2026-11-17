# Botni serverga joylash: qo'llanma

Bu papkadagi `deploy.bat` botni Linux serverga **o'zi** o'rnatadi. Siz faqat server parolini yozasiz.
O'rnatilgandan keyin bot serverda **24/7** ishlaydi: server qayta yoqilsa yoki bot xato bersa, u o'zi qayta ishga tushadi.

## Kerak bo'ladigan ma'lumotlar

Server egasidan quyidagilarni oling:

| Nima | Misol |
|---|---|
| Server IP manzili | `185.12.34.56` |
| Foydalanuvchi nomi | `root` yoki `ubuntu` |
| SSH port | odatda `22` |
| Parol | parolni hech kimga yozib yubormang, faqat oynaga o'zingiz yozasiz |

Server talablari (1-menyu buni avtomatik tekshiradi):
- Ubuntu 22.04 / 24.04 yoki Debian 12;
- kamida 1 GB RAM (2 GB va undan ko'pi yaxshi) va 3 GB bo'sh joy;
- foydalanuvchi `root` bo'lishi yoki unda `sudo` huquqi bo'lishi kerak.

## Qadamlar

1. `deploy` papkasidagi **`deploy.bat`** ni ikki marta bosing.
2. **`7`**: server IP manzili, foydalanuvchi nomi va portini kiriting. Ular shu kompyuterda `server.conf` fayliga saqlanadi va GitHub'ga chiqmaydi.
3. **`1` — Serverni tekshirish.**
   - Birinchi marta server **paroli** so'raladi. Parol yozilayotganda ekranda ko'rinmaydi, bu normal.
   - Shundan keyin kompyuter va server o'rtasida xavfsiz **kalit** o'rnatiladi va bundan keyin parol boshqa so'ralmaydi.
   - Server haqida hisobot chiqadi: tizim, RAM, disk, Python, Telegram'ga ulanish, serverda ishlayotgan boshqa dasturlar.
   - Oxirida `Server botga MOS` deb yozilsa, davom eting.
4. **`2` — BIRINCHI O'RNATISH.** Bu qadam hammasini o'zi qiladi:
   - kompyuterdagi botni to'xtatadi (bitta bot ikki joyda ishlay olmaydi);
   - kodni, bazani (o'quvchilar, natijalar, sertifikatlar), `.env` faylini va test/videolarni serverga yuboradi (~135 MB);
   - serverga Python va sertifikat uchun Google Chrome o'rnatadi;
   - botni `akmaljon-bot` nomli xizmat qilib yoqadi;
   - bot Telegram'ga ulanganini va sertifikat chiqayotganini tekshiradi.

   Agar `[sudo] password for ...` deb so'rasa, server foydalanuvchisining parolini yozing.
5. Oxirida **`BOT: ISHLAYAPTI`** chiqsa, hammasi tayyor. Telegram'da botga `/start` yozib tekshiring.

> ⚠️ Shundan keyin kompyuterdagi `ishga_tushirish.bat` ni **yoqmang**, aks holda ikki bot to'qnashadi. U ochilsa ham, endi avval ogohlantirish chiqaradi.

## Keyinchalik

| Menyu | Qachon ishlatiladi |
|---|---|
| **3 — Kodni yangilash** | Bot kodida o'zgarish qilinganda. Serverdagi baza, testlar va natijalar **o'zgarmaydi**. |
| **4 — Bot holati** | Bot ishlayaptimi, oxirgi xabarlarini ko'rish uchun. |
| **5 — Qayta ishga tushirish** | Bot javob bermay qolsa. |
| **6 — Bazani yuklab olish** | Serverdagi bazani kompyuterga zaxira qilib olish uchun (`backups/server_baza_...db`). |

Yangi testlar va videolar endi **admin panel orqali** qo'shiladi va to'g'ridan-to'g'ri serverga saqlanadi.
2-menyuni (birinchi o'rnatish) qayta bosmang: u serverdagi bazani kompyuterdagi eski baza bilan almashtiradi. Shuning uchun u avval ogohlantiradi va serverdagi eski bazani zaxiraga oladi.

## Serverda nima o'zgaradi

Bot serverdagi boshqa dasturlarga **tegmaydi**:
- **papka:** faqat `/opt/akmaljon-bot`;
- **foydalanuvchi:** alohida `akmaljonbot` (root emas, bot cheklangan huquq bilan ishlaydi);
- **xizmat:** `akmaljon-bot` (systemd);
- **port:** bot hech qanday port ochmaydi, Telegram'ga o'zi ulanadi;
- **o'rnatiladigan dasturlar:** Python, Google Chrome (faqat sertifikat uchun);
- **swap:** RAM 2 GB dan kam bo'lsa, 1 GB swap fayl (`/swapfile_akmaljon`) qo'shiladi.

Server egasi uchun buyruqlar:

```bash
systemctl status akmaljon-bot        # holati
journalctl -u akmaljon-bot -f        # jonli jurnal
systemctl restart akmaljon-bot       # qayta ishga tushirish
systemctl disable --now akmaljon-bot # butunlay o'chirish
```

## Muammo bo'lsa

- **Serverga ulanib bo'lmadi:** IP, port, foydalanuvchi nomi yoki parol noto'g'ri. 7-menyudan tekshiring.
- **Kalit bilan kirib bo'lmadi:** serverda kalit bilan kirish o'chirilgan. Server egasidan `PubkeyAuthentication yes` qilishni so'rang.
- **Python eski:** server Ubuntu 20.04 yoki undan eski. Ubuntu 22.04+ kerak.
- **BOT: ISHGA TUSHMADI:** 4-menyudagi xabarni ko'ring (masalan, token noto'g'ri yoki internet yo'q).
- **SERTIFIKAT: brauzer ishga tushmadi:** bot ishlayveradi, faqat sertifikat chiqmaydi. Jurnalni ko'rsating, birga tuzatamiz.
