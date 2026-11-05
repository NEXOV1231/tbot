# TBank Cloudflare Tunnel with auto-retry.
# Starts cloudflared, waits for the public URL; if it does not appear
# within 60 seconds, restarts it (quick tunnels are flaky from some networks).
$cf   = Join-Path $PSScriptRoot "cloudflared\extracted\cloudflared\cloudflared.exe"
$out  = Join-Path $env:TEMP "tbank_cf_out.log"
$err  = Join-Path $env:TEMP "tbank_cf_err.log"

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  CLOUDFLARE TUNNEL - waiting for URL..."
Write-Host "  (keep this window open while using)"
Write-Host "========================================" -ForegroundColor Yellow

for ($attempt = 1; $attempt -le 6; $attempt++) {
    Write-Host ""
    Write-Host "Attempt $attempt/6 ..."
    Remove-Item $out, $err -ErrorAction SilentlyContinue
    $proc = Start-Process -FilePath $cf `
        -ArgumentList "tunnel","--url","http://localhost:8000","--no-autoupdate","--protocol","http2" `
        -WorkingDirectory $PSScriptRoot -PassThru -NoNewWindow `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $url = $null
    foreach ($i in 1..20) {
        Start-Sleep -Seconds 3
        $line = Get-Content $out, $err -Encoding UTF8 -ErrorAction SilentlyContinue |
                Where-Object { $_ -match "trycloudflare\.com" } | Select-Object -First 1
        if ($line) {
            $url = ([regex]::Match($line, "https://[a-z0-9-]+\.trycloudflare\.com")).Value
            break
        }
        if ($proc.HasExited) { break }
    }
    if ($url) {
        Write-Host ""
        Write-Host "  TUNNEL IS UP!" -ForegroundColor Green
        Write-Host ""
        Write-Host "  Public URL:  $url" -ForegroundColor Cyan
        Write-Host "  Mini App:    $url/app" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  Put the /app address into BotFather (/setmenubutton)."
        Write-Host "  The tunnel lives while this window is open."
        Write-Host ""
        Wait-Process -Id $proc.Id
        Write-Host "Tunnel closed."
        exit 0
    }
    $log = Get-Content $out, $err -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if ($log -match "status 429") {
        Write-Host "  Cloudflare rate-limited quick tunnels for your IP (429)." -ForegroundColor Red
        Write-Host "  Your provider shares this IP with other people who exhaust the quota." -ForegroundColor Red
        Write-Host "  Options: try again in 30-60 min, or share internet FROM YOUR PHONE" -ForegroundColor Yellow
        Write-Host "  (USB modem / Wi-Fi hotspot) and run public.bat again - mobile IP has its own quota." -ForegroundColor Yellow
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        exit 2
    }
    Write-Host "  no URL yet, restarting tunnel..." -ForegroundColor DarkYellow
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

Write-Host "Could not establish tunnel in 6 attempts." -ForegroundColor Red
Write-Host "Check your internet connection and try again later."
exit 1
