# Akmaljon yordamchi bot - serverga joylash (Windows tomoni).
# deploy.bat orqali ishga tushiriladi. Faqat Windows'ning o'z dasturlari ishlatiladi: ssh, scp, tar, robocopy.
# Eslatma: serverga yuboriladigan buyruqlarda qo'shtirnoq (") ishlatilmaydi - PowerShell 5.1 ularni buzadi.
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$Deploy    = $PSScriptRoot
$Bot       = Split-Path $Deploy -Parent
$ConfF     = Join-Path $Deploy 'server.conf'
$KeyF      = Join-Path $env:USERPROFILE '.ssh\akmaljon_deploy'
$Work      = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Temp\akmaljon_deploy'   # qisqa (~) yo'lsiz
$SshExe    = 'C:\Windows\System32\OpenSSH\ssh.exe'
$ScpExe    = 'C:\Windows\System32\OpenSSH\scp.exe'
$KeygenExe = 'C:\Windows\System32\OpenSSH\ssh-keygen.exe'
$TarExe    = 'C:\Windows\System32\tar.exe'
$Remote    = '/tmp/akmaljon_deploy'
$App       = '/opt/akmaljon-bot'

function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Title($t) { Write-Host ''; Write-Host "==== $t ====" -ForegroundColor Cyan }
function Fail($t) { throw $t }

foreach ($exe in $SshExe, $ScpExe, $KeygenExe, $TarExe) {
    if (-not (Test-Path $exe)) {
        Say "Kerakli dastur topilmadi: $exe" 'Red'
        Say "Windows Sozlamalar -> Ilovalar -> Qo'shimcha imkoniyatlar -> 'OpenSSH Client' ni o'rnating." 'Yellow'
        Read-Host 'Enter bosing' | Out-Null; exit 1
    }
}

# ------------------------------------------------------------------ server manzili (server.conf)
function Load-Conf {
    $c = @{ HOST = ''; USER = 'root'; PORT = '22'; DEPLOYED = '' }
    if (Test-Path $ConfF) {
        foreach ($line in Get-Content $ConfF) {
            $k, $v = $line -split '=', 2
            if ($v -ne $null -and $k.Trim()) { $c[$k.Trim()] = $v.Trim() }
        }
    }
    return $c
}

function Save-Conf {
    "HOST=$($Conf.HOST)`r`nUSER=$($Conf.USER)`r`nPORT=$($Conf.PORT)`r`nDEPLOYED=$($Conf.DEPLOYED)" |
        Set-Content -Path $ConfF -Encoding ASCII
}

function Ask-Conf {
    Title 'Server manzili'
    Say "Qavs ichidagi qiymat to'g'ri bo'lsa, shunchaki Enter bosing."
    $h = Read-Host "  Server IP manzili (masalan 185.12.34.56) [$($Conf.HOST)]"; if ($h) { $Conf.HOST = $h.Trim() }
    $u = Read-Host "  Foydalanuvchi [$($Conf.USER)]"; if ($u) { $Conf.USER = $u.Trim() }
    $p = Read-Host "  SSH port [$($Conf.PORT)]"; if ($p) { $Conf.PORT = $p.Trim() }
    if (-not $Conf.HOST) { Fail 'IP manzil kiritilmadi' }
    if ($Conf.PORT -notmatch '^\d+$') { Fail 'Port faqat raqam bo''lishi kerak' }
    Save-Conf
}

$Conf = Load-Conf

function Target { "$($Conf.USER)@$($Conf.HOST)" }
function Opts { @('-p', $Conf.PORT, '-o', 'StrictHostKeyChecking=accept-new', '-o', 'LogLevel=ERROR',
                  '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=30') }

# Dasturni shu oynada to'g'ridan-to'g'ri ishga tushiradi (parol so'rovlari va server javoblari ko'rinadi).
# Natija $LASTEXITCODE da. Argumentlarda qo'shtirnoq (") bo'lmasligi kerak.
function Run([string]$exe, [string[]]$argv) {
    $line = ($argv | ForEach-Object { if ($_ -match '[\s;|&<>]' -or $_ -eq '') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $p = Start-Process -FilePath $exe -ArgumentList $line -NoNewWindow -Wait -PassThru
    $global:LASTEXITCODE = $p.ExitCode
}

function Ssh([string]$cmd, [switch]$Tty) {
    $a = @('-i', $KeyF) + (Opts)
    if ($Tty) { $a += '-t' }
    Run $SshExe ($a + @((Target), $cmd))
}

function Root([string]$cmd) {
    # root bo'lsa to'g'ridan-to'g'ri, aks holda sudo (sudo paroli shu yerda so'raladi)
    "if [ `$(id -u) -eq 0 ]; then $cmd; else sudo $cmd; fi"
}

# ------------------------------------------------------------------ SSH kalit: server paroli faqat BIR MARTA so'raladi
function Key-Works {
    $a = @('-i', $KeyF, '-o', 'BatchMode=yes') + (Opts)
    $null = & $SshExe @a (Target) 'echo ok' 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Ensure-Key {
    if (-not $Conf.HOST) { Ask-Conf }
    if (-not (Test-Path $KeyF)) {
        Say "Xavfsiz ulanish kaliti yaratilmoqda (faqat shu kompyuterda saqlanadi)..."
        New-Item -ItemType Directory -Force (Split-Path $KeyF) | Out-Null
        & $KeygenExe -q -t ed25519 -N '""' -C 'akmaljon-deploy' -f $KeyF
        if (-not (Test-Path $KeyF)) { Fail 'Kalit yaratilmadi' }
    }
    if (Key-Works) { return }

    $pub = (Get-Content "$KeyF.pub" -Raw).Trim()
    $cmd = "umask 077; mkdir -p ~/.ssh; grep -qF '$pub' ~/.ssh/authorized_keys 2>/dev/null || echo '$pub' >> ~/.ssh/authorized_keys; echo Kalit serverga qo''shildi"

    # Radeski sayti uchun shu kompyuterda kalit bo'lsa - u orqali parolsiz ulanamiz
    $other = Join-Path $env:USERPROFILE '.ssh\radeski_deploy_ed25519'
    if (Test-Path $other) {
        $a = @('-i', $other, '-o', 'BatchMode=yes') + (Opts)
        $null = & $SshExe @a (Target) $cmd 2>$null
        if ($LASTEXITCODE -eq 0 -and (Key-Works)) { Say "Radeski kaliti orqali ulanildi - parol kerak bo'lmadi." 'Green'; return }
    }

    Title 'Serverga birinchi ulanish'
    Say "Server ($(Target)) parolini kiriting va Enter bosing." 'Yellow'
    Say "Parol yozilayotganda ekranda hech narsa ko'rinmaydi - bu normal." 'Yellow'
    $a =@('-o', 'PubkeyAuthentication=no', '-o', 'PreferredAuthentications=password,keyboard-interactive') + (Opts)
    Run $SshExe ($a + @((Target), $cmd))
    if ($LASTEXITCODE -ne 0) { Fail "Serverga ulanib bo'lmadi: parol, IP, port yoki foydalanuvchi nomi noto'g'ri (7-menyudan tekshiring)" }
    if (-not (Key-Works)) { Fail "Kalit qo'shildi, lekin u bilan kirib bo'lmadi (serverda kalit bilan kirish o'chirilgan bo'lishi mumkin)" }
    Say "Ulanish sozlandi. Bundan keyin server paroli so'ralmaydi." 'Green'
}

function Upload([string[]]$files) {
    Ssh "rm -rf $Remote; mkdir -p $Remote"
    if ($LASTEXITCODE -ne 0) { Fail "Serverda vaqtinchalik papka ochib bo'lmadi" }
    $a = @('-i', $KeyF, '-P', $Conf.PORT, '-o', 'StrictHostKeyChecking=accept-new', '-o', 'LogLevel=ERROR')
    Run $ScpExe ($a + $files + @("$(Target):$Remote/"))      # yuborish jarayoni (%) ko'rinib turadi
    if ($LASTEXITCODE -ne 0) { Fail "Fayllarni serverga yuborib bo'lmadi" }
    Ssh "sed -i 's/\r`$//' $Remote/*.sh"     # Windows qator oxirlarini tozalash
}

function Server-Files { @('install.sh', 'check.sh', 'backup.sh') | ForEach-Object { Join-Path $Deploy "server\$_" } }

# ------------------------------------------------------------------ kompyuterdagi botni to'xtatish
function Stop-LocalBot {
    $venv = (Join-Path $Bot '.venv').ToLower()
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue)
    $mine = @($procs | Where-Object { $_.CommandLine -and $_.CommandLine -like '*main.py*' -and (
        ($_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($venv)) -or $_.CommandLine.ToLower().Contains($venv)) })
    $ids  = @($mine | ForEach-Object { $_.ProcessId })
    $kids = @($procs | Where-Object { $ids -contains $_.ParentProcessId } | ForEach-Object { $_.ProcessId })
    $all  = @($ids + $kids | Select-Object -Unique)
    if ($all.Count -eq 0) { Say "Kompyuterda bot ishlamayapti - yaxshi."; return }
    foreach ($id in $all) { try { Stop-Process -Id $id -Force -ErrorAction Stop } catch {} }
    Start-Sleep -Seconds 2
    Say "Kompyuterdagi bot to'xtatildi." 'Green'
}

# ------------------------------------------------------------------ paketlar
function Pack-Code {
    if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
    $stage = Join-Path $Work 'code'
    New-Item -ItemType Directory -Force $stage | Out-Null
    $xd = @('.venv', '.git', 'backups', 'content', 'deploy') | ForEach-Object { Join-Path $Bot $_ }
    $null = & robocopy $Bot $stage /E /NFL /NDL /NJH /NJS /NP /XD @xd __pycache__ `
        /XF .env users.db users.db-journal bot.log *.mp4 *.pdf *.pyc _sim*.db
    if ($LASTEXITCODE -ge 8) { Fail "Kodni yig'ib bo'lmadi (robocopy $LASTEXITCODE)" }
    foreach ($bad in '.env', 'users.db') {
        if (Test-Path (Join-Path $stage $bad)) { Fail "Xavfsizlik: $bad kod paketiga tushib qoldi" }
    }
    $out = Join-Path $Work 'code.tar.gz'
    & $TarExe -czf $out -C $stage .
    if ($LASTEXITCODE -ne 0) { Fail 'code.tar.gz yaratilmadi' }
    $n = (Get-ChildItem $stage -Recurse -File).Count
    Say "Kod tayyor: $n ta fayl, $([math]::Round((Get-Item $out).Length / 1KB)) KB"
    return $out
}

function Pack-Data {
    foreach ($f in '.env', 'users.db') {
        if (-not (Test-Path (Join-Path $Bot $f))) { Fail "$f topilmadi - kompyuterda bot sozlanmagan" }
    }
    $out = Join-Path $Work 'data.tar.gz'
    $items = @('.env', 'users.db')
    if (Test-Path (Join-Path $Bot 'content')) { $items += 'content' }
    & $TarExe -czf $out -C $Bot @items
    if ($LASTEXITCODE -ne 0) { Fail 'data.tar.gz yaratilmadi' }
    Say "Ma'lumotlar tayyor (baza + .env + testlar/videolar): $([math]::Round((Get-Item $out).Length / 1MB, 1)) MB"
    return $out
}

# ------------------------------------------------------------------ amallar
function Do-Check {
    Ensure-Key
    Title "Server o'rganilmoqda"
    Upload (Server-Files)
    Ssh "bash $Remote/check.sh; rc=`$?; rm -rf $Remote; exit `$rc"
}

function Do-Full {
    Ensure-Key
    Title '1-qadam: server tekshiruvi'
    Upload (Server-Files)
    Ssh "bash $Remote/check.sh"
    if ($LASTEXITCODE -ne 0) { Fail "Server talablarga javob bermadi - yuqoridagi XATO qatorlarini ko'ring" }

    $a = @('-i', $KeyF) + (Opts)
    $has = & $SshExe @a (Target) "test -f $App/users.db && echo BOR"
    if ("$has" -match 'BOR') {
        Say ''
        Say "DIQQAT: serverda allaqachon bot bazasi bor (o'quvchilar va natijalar)." 'Yellow'
        Say "Davom etsangiz, u KOMPYUTERDAGI baza bilan almashtiriladi (eskisi serverda zaxiraga olinadi)." 'Yellow'
        if ((Read-Host "Davom etilsinmi? (ha / yo'q)") -notmatch '^\s*(ha|h|yes|y)\s*$') { Say 'Bekor qilindi.'; return }
    }

    Title "2-qadam: kompyuterdagi botni to'xtatish"
    Say "Bitta bot bir vaqtda ikki joyda ishlay olmaydi, shuning uchun kompyuterdagisi to'xtatiladi."
    Stop-LocalBot

    Title '3-qadam: fayllarni tayyorlash'
    $code = Pack-Code
    $data = Pack-Data

    Title '4-qadam: serverga yuborish'
    Say "Videolar ham yuborilmoqda - internet tezligiga qarab bir necha daqiqa olishi mumkin..."
    Upload ((Server-Files) + @($code, $data))

    Title "5-qadam: serverga o'rnatish"
    Say "Agar '[sudo] password for ...' deb so'rasa - server foydalanuvchisi parolini yozing." 'Yellow'
    Ssh (Root "bash $Remote/install.sh full") -Tty
    $rc = $LASTEXITCODE
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
    if ($rc -ne 0) { Fail "O'rnatishda xato (kod $rc) - yuqoridagi xabarni ko'ring. Kompyuterdagi botni qayta yoqish uchun ishga_tushirish.bat ni bosing." }
    $Conf.DEPLOYED = '1'; Save-Conf
    Say ''
    Say 'TAYYOR! Bot endi serverda doimiy ishlaydi (server qayta yonsa ham o''zi ishga tushadi).' 'Green'
    Say "Kompyuterdagi 'ishga_tushirish.bat' ni endi YOQMANG - bot ikki joyda to'qnashadi." 'Yellow'
}

function Do-Code {
    Ensure-Key
    $code = Pack-Code
    Title 'Yangi kod serverga yuborilmoqda'
    Upload ((Server-Files) + @($code))
    Say "Agar '[sudo] password for ...' deb so'rasa - server foydalanuvchisi parolini yozing." 'Yellow'
    Ssh (Root "bash $Remote/install.sh code") -Tty
    $rc = $LASTEXITCODE
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
    if ($rc -ne 0) { Fail "Yangilashda xato (kod $rc) - yuqoridagi xabarni ko'ring" }
    Say "Kod yangilandi va bot qayta ishga tushdi. Baza va testlar o'zgarmadi." 'Green'
}

function Do-Status {
    Ensure-Key
    Title 'Bot holati'
    Ssh (Root "bash -c 'systemctl --no-pager status akmaljon-bot | head -4; echo; echo Oxirgi xabarlar:; journalctl -u akmaljon-bot -n 25 --no-pager -o cat'") -Tty
}

function Do-Restart {
    Ensure-Key
    Title 'Bot qayta ishga tushirilmoqda'
    Ssh (Root "bash -c 'systemctl restart akmaljon-bot; sleep 6; systemctl is-active akmaljon-bot'") -Tty
}

function Do-Backup {
    Ensure-Key
    Title 'Serverdagi bazani kompyuterga yuklab olish'
    Upload (Server-Files)
    Ssh (Root "bash $Remote/backup.sh") -Tty
    if ($LASTEXITCODE -ne 0) { Fail "Serverda bazaning nusxasini olib bo'lmadi" }
    $dir = Join-Path $Bot 'backups'
    New-Item -ItemType Directory -Force $dir | Out-Null
    $dst = Join-Path $dir ('server_baza_' + (Get-Date -Format 'yyyy-MM-dd_HH-mm') + '.db')
    $a = @('-i', $KeyF, '-P', $Conf.PORT, '-o', 'LogLevel=ERROR')
    Run $ScpExe ($a + @("$(Target):/tmp/akmaljon_baza.db", $dst))
    Ssh (Root "rm -rf /tmp/akmaljon_baza.db $Remote") -Tty
    if (Test-Path $dst) { Say "Saqlandi: $dst" 'Green' } else { Fail 'Yuklab olinmadi' }
}

# ------------------------------------------------------------------ menyu
while ($true) {
    Clear-Host
    Say '============================================================' 'Cyan'
    Say '     AKMALJON YORDAMCHI BOT  -  serverga joylash' 'Cyan'
    Say '============================================================' 'Cyan'
    if ($Conf.HOST) { Say "   Server: $(Target)   port $($Conf.PORT)" } else { Say '   Server: hali kiritilmagan (birinchi amalda so''raladi)' 'Yellow' }
    if ($Conf.DEPLOYED -eq '1') { Say '   Holat:  bot serverga o''rnatilgan' 'Green' }
    Say ''
    Say '   1. Serverni tekshirish  (o''rganish, hech narsa o''zgarmaydi)'
    Say '   2. BIRINCHI O''RNATISH  (kod + baza + testlar/videolar)'
    Say '   3. Kodni yangilash      (baza va testlar serverdagicha qoladi)'
    Say '   4. Bot holati va oxirgi xabarlari'
    Say '   5. Botni qayta ishga tushirish'
    Say '   6. Serverdagi bazani kompyuterga yuklab olish (zaxira)'
    Say '   7. Server manzilini kiritish / o''zgartirish'
    Say '   0. Chiqish'
    Say ''
    $choice = (Read-Host 'Raqamni yozing va Enter bosing').Trim()
    if ($choice -eq '0') { break }
    try {
        switch ($choice) {
            '1' { Do-Check }
            '2' { Do-Full }
            '3' { Do-Code }
            '4' { Do-Status }
            '5' { Do-Restart }
            '6' { Do-Backup }
            '7' { Ask-Conf }
        }
    } catch {
        Say ''
        Say "XATO: $($_.Exception.Message)" 'Red'
    }
    Say ''
    Read-Host 'Menyuga qaytish uchun Enter bosing' | Out-Null
}
