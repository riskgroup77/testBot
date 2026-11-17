#!/usr/bin/env bash
# Serverdagi bazaning ishlab turgan paytdagi to'g'ri nusxasini /tmp/akmaljon_baza.db ga oladi.
set -euo pipefail
python3 - <<'EOF'
import sqlite3
src = sqlite3.connect("/opt/akmaljon-bot/users.db")
dst = sqlite3.connect("/tmp/akmaljon_baza.db")
src.backup(dst)
dst.close()
src.close()
EOF
chmod 644 /tmp/akmaljon_baza.db
echo "Nusxa tayyor: $(du -h /tmp/akmaljon_baza.db | cut -f1)"
