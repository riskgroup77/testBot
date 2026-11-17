#!/usr/bin/env bash
# Serverni o'rganish: bot shu serverda ishlay oladimi? Hech narsani o'zgartirmaydi.
set -u
APP=/opt/akmaljon-bot
ok=0; warn=0; bad=0
good() { echo "  [OK]   $*"; ok=$((ok+1)); }
note() { echo "  [!]    $*"; warn=$((warn+1)); }
fail() { echo "  [XATO] $*"; bad=$((bad+1)); }

echo "=================== SERVER HAQIDA ==================="
. /etc/os-release 2>/dev/null || true
echo "  Tizim:      ${PRETTY_NAME:-aniqlanmadi}"
echo "  Yadro:      $(uname -r)   Arxitektura: $(uname -m)"
echo "  Protsessor: $(nproc 2>/dev/null || echo ?) yadro"
echo "  Server nomi: $(hostname)"
echo "  Ishlab turibdi: $(uptime -p 2>/dev/null || uptime)"
echo "  Foydalanuvchi: $(id -un) $( [ "$(id -u)" -eq 0 ] && echo '(root)' )"
echo

echo "=================== TEKSHIRUV ==================="
# 1) paket menejeri
if command -v apt-get >/dev/null; then good "Paket menejeri: apt (Debian/Ubuntu)"
elif command -v dnf >/dev/null || command -v yum >/dev/null; then good "Paket menejeri: dnf/yum (CentOS/Rocky/Alma)"
else fail "apt ham, dnf ham topilmadi — avtomatik o'rnatib bo'lmaydi"; fi

# 2) systemd
if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then good "systemd bor (bot avtomatik qayta yonadi)"
else fail "systemd yo'q — avtomatik ishga tushirish ishlamaydi"; fi

# 3) xotira
mem=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
swap=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem" -ge 1900 ]; then good "RAM: ${mem} MB (bo'sh: ${avail} MB), swap: ${swap} MB"
elif [ "$mem" -ge 900 ]; then note "RAM: ${mem} MB (bo'sh: ${avail} MB) — yetadi, lekin sertifikat paytida qiynalishi mumkin; swap qo'shiladi"
else fail "RAM: ${mem} MB — juda kam (kamida 1 GB kerak)"; fi
[ "$avail" -lt 400 ] && note "Hozir bo'sh RAM kam (${avail} MB) — serverda boshqa dasturlar ko'p xotira olyapti"

# 4) disk
free_mb=$(df -Pm /opt 2>/dev/null | awk 'NR==2 {print $4}')
if [ "${free_mb:-0}" -ge 3000 ]; then good "Bo'sh disk: $((free_mb/1024)) GB"
elif [ "${free_mb:-0}" -ge 1000 ]; then note "Bo'sh disk: ${free_mb} MB — hozircha yetadi, videolar ko'paysa tor bo'ladi"
else fail "Bo'sh disk: ${free_mb:-?} MB — kamida 1 GB kerak"; fi

# 5) python
if command -v python3 >/dev/null; then
  pv=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then good "Python $pv"
  else fail "Python $pv — eski (kamida 3.10 kerak). Ubuntu 22.04+ yoki Debian 12 kerak"; fi
else note "Python3 o'rnatilmagan — o'rnatish paytida qo'yiladi"; fi

# 6) brauzer (sertifikat uchun)
br=""
for n in google-chrome-stable google-chrome chromium chromium-browser; do
  p=$(command -v $n 2>/dev/null) || continue
  case "$(readlink -f "$p")" in */snap/*) continue ;; esac
  br=$p; break
done
if [ -n "$br" ]; then good "Brauzer bor: $br"
else note "Chrome/Chromium yo'q — o'rnatish paytida Google Chrome qo'yiladi ($(uname -m))"; fi
[ "$(uname -m)" != "x86_64" ] && note "Arxitektura $(uname -m): Google Chrome faqat x86_64 uchun, Chromium ishlatiladi"

# 7) Telegram'ga ulanish
code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://api.telegram.org 2>/dev/null || echo 000)
if [ "$code" != "000" ]; then good "api.telegram.org ga ulanish bor"
else fail "api.telegram.org ga ulanib bo'lmadi — server Telegram'ni ko'rmayapti"; fi

# 8) vaqt
if command -v timedatectl >/dev/null; then
  sync=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)
  [ "$sync" = "yes" ] && good "Soat sinxron" || note "Soat sinxron emas (muddatli testlarda vaqt biroz farq qilishi mumkin)"
fi

# 9) sudo huquqi
if [ "$(id -u)" -eq 0 ]; then good "root — o'rnatish huquqi bor"
elif sudo -n true 2>/dev/null; then good "sudo parolsiz ishlaydi"
elif id -nG | grep -qwE 'sudo|wheel'; then good "sudo bor (o'rnatishda parol so'raladi)"
else fail "Bu foydalanuvchida sudo huquqi yo'q — root yoki sudo'li foydalanuvchi kerak"; fi

echo
echo "=================== SERVERDA HOZIR NIMA BOR ==================="
if [ -d "$APP" ]; then
  echo "  Bot papkasi bor: $APP"
  systemctl is-active akmaljon-bot >/dev/null 2>&1 && echo "  akmaljon-bot xizmati: ISHLAYAPTI" || echo "  akmaljon-bot xizmati: to'xtagan / yo'q"
  [ -f "$APP/users.db" ] && echo "  Serverdagi baza: $(du -h "$APP/users.db" | cut -f1), oxirgi o'zgarish $(date -r "$APP/users.db" '+%d.%m.%Y %H:%M')"
else
  echo "  Bot hali o'rnatilmagan (yangi o'rnatish)"
fi
echo "  Boshqa ishlayotgan xizmatlar (bot ularga tegmaydi):"
systemctl list-units --type=service --state=running --no-legend 2>/dev/null \
  | awk '{print $1}' | grep -vE '^(systemd-|dbus|cron|ssh|sshd|getty|serial-getty|rsyslog|polkit|unattended|networkd|snapd|multipathd|udisks|ModemManager|qemu-guest|chrony|irqbalance|user@|accounts-daemon|packagekit|fwupd|atd|containerd)' \
  | head -25 | sed 's/^/     - /'
echo "  Ko'p xotira olayotgan jarayonlar:"
ps -eo rss,comm --sort=-rss 2>/dev/null | awk 'NR>1 && NR<=6 {printf "     - %-20s %d MB\n", $2, $1/1024}'
echo
echo "=================== XULOSA ==================="
echo "  OK: $ok   Ogohlantirish: $warn   Xato: $bad"
if [ "$bad" -eq 0 ]; then echo "  >>> Server botga MOS. O'rnatish mumkin."; exit 0
else echo "  >>> Yuqoridagi XATOlarni tuzatmasdan o'rnatib bo'lmaydi."; exit 2; fi
