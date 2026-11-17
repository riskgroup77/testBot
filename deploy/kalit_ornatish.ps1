# Serverga ulanish kalitini qo'shadi (server paroli faqat shu yerda, bir marta so'raladi).
$ssh  = 'C:\Windows\System32\OpenSSH\ssh.exe'
$key  = Join-Path $env:USERPROFILE '.ssh\akmaljon_deploy'
$conf = @{}
Get-Content (Join-Path $PSScriptRoot 'server.conf') | ForEach-Object { $k, $v = $_ -split '=', 2; if ($v -ne $null) { $conf[$k.Trim()] = $v.Trim() } }
$target = "$($conf.USER)@$($conf.HOST)"
$pub = (Get-Content "$key.pub" -Raw).Trim()
$cmd = "umask 077; mkdir -p ~/.ssh; grep -qF '$pub' ~/.ssh/authorized_keys 2>/dev/null || echo '$pub' >> ~/.ssh/authorized_keys; echo KALIT_QOSHILDI"

Write-Host ''
Write-Host "Server: $target" -ForegroundColor Cyan
Write-Host "Parolni qo'ying: Ctrl+V yoki sichqonchaning o'ng tugmasi. Ekranda ko'rinmaydi - bu normal. Keyin Enter." -ForegroundColor Yellow
& $ssh -p $conf.PORT -o StrictHostKeyChecking=accept-new -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive -o NumberOfPasswordPrompts=3 $target $cmd
if ($LASTEXITCODE -ne 0) { Write-Host "Ulanib bo'lmadi (kod $LASTEXITCODE)." -ForegroundColor Red; exit 1 }

& $ssh -i $key -p $conf.PORT -o BatchMode=yes $target 'echo KALIT_ISHLAYAPTI'
if ($LASTEXITCODE -eq 0) { Write-Host "TAYYOR: endi parolsiz ulanadi." -ForegroundColor Green }
else { Write-Host "Kalit qo'shildi, lekin u bilan kirib bo'lmadi." -ForegroundColor Red }
