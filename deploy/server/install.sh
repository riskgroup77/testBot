#!/usr/bin/env bash
# Botni serverga o'rnatadi / yangilaydi. deploy.bat avtomatik ishga tushiradi (root yoki sudo bilan).
#   install.sh full   — kod + baza + .env + testlar/videolar (birinchi ko'chirish)
#   install.sh code   — faqat kod (serverdagi baza va testlar o'zgarmaydi)
set -euo pipefail

MODE="${1:-code}"
SRC=/tmp/akmaljon_deploy
APP=/opt/akmaljon-bot
SVC=akmaljon-bot
RUNUSER=akmaljonbot
STAMP=$(date +%Y-%m-%d_%H-%M)

step() { echo; echo ">>> $*"; }
die()  { echo; echo "XATO: $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "root huquqi kerak"
[ -f "$SRC/code.tar.gz" ] || die "$SRC/code.tar.gz topilmadi"
[ "$MODE" = "code" ] || [ -f "$SRC/data.tar.gz" ] || die "$SRC/data.tar.gz topilmadi"
export DEBIAN_FRONTEND=noninteractive

# ------------------------------------------------------------------ 1. paketlar
step "1/8 Kerakli dasturlar tekshirilmoqda"
if command -v apt-get >/dev/null; then
  need=""
  for p in python3 python3-venv python3-pip ca-certificates curl tar; do
    dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"
  done
  if [ -n "$need" ]; then
    echo "O'rnatiladi:$need"
    apt-get update -qq
    apt-get install -y -qq $need
  fi
  # python3.X-venv alohida paket bo'lishi mumkin (Ubuntu)
  pv=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  python3 -m venv --help >/dev/null 2>&1 || apt-get install -y -qq "python${pv}-venv"
elif command -v dnf >/dev/null || command -v yum >/dev/null; then
  PM=$(command -v dnf || command -v yum)
  $PM install -y -q python3 python3-pip ca-certificates curl tar
else
  die "Faqat Debian/Ubuntu yoki CentOS/Rocky/Alma qo'llab-quvvatlanadi"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python $(python3 -V) eski — kamida 3.10 kerak (Ubuntu 22.04+ / Debian 12)"
echo "Python: $(python3 -V)"

# ------------------------------------------------------------------ 2. brauzer (sertifikat)
step "2/8 Sertifikat uchun brauzer"
find_browser() {
  for n in google-chrome-stable google-chrome chromium chromium-browser; do
    p=$(command -v $n 2>/dev/null) || continue
    case "$(readlink -f "$p")" in */snap/*) continue ;; esac   # snap versiya /tmp ni ko'rmaydi
    echo "$p"; return 0
  done
  return 1
}
BROWSER=$(find_browser || true)
if [ -z "$BROWSER" ]; then
  arch=$(uname -m)
  if command -v apt-get >/dev/null; then
    if [ "$arch" = "x86_64" ]; then
      echo "Google Chrome o'rnatilmoqda (rasmiy sayt: dl.google.com)..."
      curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
      apt-get install -y -qq /tmp/chrome.deb && rm -f /tmp/chrome.deb
    else
      . /etc/os-release
      if [ "${ID:-}" = "debian" ]; then apt-get install -y -qq chromium
      else echo "OGOHLANTIRISH: $arch + Ubuntu — Chromium faqat snap ko'rinishida, sertifikat ishlamasligi mumkin"; fi
    fi
  else
    [ "$arch" = "x86_64" ] && $PM install -y -q https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm \
      || $PM install -y -q chromium || true
  fi
  BROWSER=$(find_browser || true)
fi
if command -v apt-get >/dev/null; then
  dpkg -s fonts-liberation >/dev/null 2>&1 || apt-get install -y -qq fonts-liberation || true
fi
echo "Brauzer: ${BROWSER:-TOPILMADI (bot ishlaydi, faqat sertifikat chiqmaydi)}"

# ------------------------------------------------------------------ 3. swap (RAM kam bo'lsa)
step "3/8 Xotira"
mem=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
swap=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem" -lt 1900 ] && [ "$swap" -lt 500 ] && [ ! -f /swapfile_akmaljon ]; then
  echo "RAM ${mem} MB — 1 GB swap qo'shilmoqda (sertifikat paytida yordam beradi)"
  if fallocate -l 1G /swapfile_akmaljon 2>/dev/null || dd if=/dev/zero of=/swapfile_akmaljon bs=1M count=1024 status=none; then
    chmod 600 /swapfile_akmaljon && mkswap -q /swapfile_akmaljon && swapon /swapfile_akmaljon \
      && echo '/swapfile_akmaljon none swap sw 0 0' >> /etc/fstab || echo "swap qo'shilmadi (muhim emas)"
  fi
else
  echo "RAM ${mem} MB, swap ${swap} MB — yetarli"
fi

# ------------------------------------------------------------------ 4. foydalanuvchi va papka
step "4/8 Bot uchun alohida foydalanuvchi va papka ($APP)"
id "$RUNUSER" >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin "$RUNUSER"
mkdir -p "$APP" "$APP/backups"
if systemctl is-active --quiet "$SVC"; then
  echo "Ishlab turgan bot to'xtatilmoqda..."
  systemctl stop "$SVC"
fi

# ------------------------------------------------------------------ 5. kod va ma'lumotlar
step "5/8 Fayllar joylanmoqda (rejim: $MODE)"
tar -xzf "$SRC/code.tar.gz" -C "$APP"
echo "Kod yangilandi"
if [ "$MODE" = "full" ]; then
  if [ -f "$APP/users.db" ]; then
    cp -p "$APP/users.db" "$APP/backups/server_oldingi_baza_$STAMP.db"
    echo "Serverdagi eski baza saqlab qo'yildi: backups/server_oldingi_baza_$STAMP.db"
  fi
  rm -f "$APP/users.db-journal" "$APP/users.db-wal" "$APP/users.db-shm"
  tar -xzf "$SRC/data.tar.gz" -C "$APP"
  echo "Baza, .env va testlar/videolar ko'chirildi"
fi
[ -f "$APP/.env" ] || die "$APP/.env yo'q — avval 'Birinchi o'rnatish' ni bajaring"
grep -q '^BOT_TOKEN=.' "$APP/.env" || die ".env ichida BOT_TOKEN yo'q"

# ------------------------------------------------------------------ 6. python kutubxonalari
step "6/8 Python kutubxonalari"
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
grep -v imageio "$APP/requirements.txt" > /tmp/akmaljon_req.txt     # ffmpeg faqat kompyuterdagi vosita uchun
"$APP/.venv/bin/pip" install -q --disable-pip-version-check --upgrade pip
"$APP/.venv/bin/pip" install -q --disable-pip-version-check -r /tmp/akmaljon_req.txt
rm -f /tmp/akmaljon_req.txt
find "$APP" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
chown -R "$RUNUSER:$RUNUSER" "$APP"
chmod 700 "$APP"
chmod 600 "$APP/.env"

# ------------------------------------------------------------------ 7. systemd xizmati
step "7/8 Avtomatik ishga tushish (systemd: $SVC)"
cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=Akmaljon yordamchi Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUNUSER
Group=$RUNUSER
WorkingDirectory=$APP
Environment=HOME=$APP
Environment=SETUP_PORT=0
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=CERT_BROWSER=${BROWSER:-}
ExecStart=$APP/.venv/bin/python main.py --no-browser
Restart=always
RestartSec=10
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$SVC" >/dev/null 2>&1
systemctl restart "$SVC"

# ------------------------------------------------------------------ 8. tekshiruv
step "8/8 Tekshiruv"
started=""
for i in $(seq 1 30); do
  sleep 2
  if ! systemctl is-active --quiet "$SVC"; then continue; fi
  if journalctl -u "$SVC" --since "-2min" --no-pager 2>/dev/null | grep -q "Run polling"; then started=1; break; fi
done
journalctl -u "$SVC" -n 12 --no-pager -o cat 2>/dev/null | grep -v "^$" | tail -12 || true
echo
if [ -n "$started" ]; then echo "BOT: ISHLAYAPTI (Telegram'ga ulandi)"
elif systemctl is-active --quiet "$SVC"; then echo "BOT: ishga tushdi, Telegram'ga ulanishini kutyapti (tekshiring: 4-menyu)"
else echo "BOT: ISHGA TUSHMADI — yuqoridagi xatoni ko'ring"; fi

if [ -n "$BROWSER" ]; then
  if runuser -u "$RUNUSER" -- env HOME="$APP" CERT_BROWSER="$BROWSER" "$APP/.venv/bin/python" -c "
import asyncio, sys
sys.path.insert(0, '$APP')
import certificate
html = certificate.build_html(name='Sinov Sinovov', title='Sinov', header='Milliy sertifikat', subject='Fizika',
                              grade='A+', date='01.01.2026', code='AY-TEST', bot_username='test')
data = asyncio.run(certificate.render(html, 'png'))
print('SERTIFIKAT: ishlayapti (%d KB rasm)' % (len(data) // 1024))
" 2>/dev/null; then :; else echo "SERTIFIKAT: brauzer ishga tushmadi — sertifikatlar chiqmaydi"; fi
fi

rm -rf "$SRC"
echo
echo "Tayyor. Bot papkasi: $APP   Xizmat: $SVC"
