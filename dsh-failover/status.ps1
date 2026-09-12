# status.ps1 — 查看 DSH 模型容灾代理状态与关键配置（隐藏密钥）
#   用法: powershell -ExecutionPolicy Bypass -File dsh-failover\status.ps1
$ErrorActionPreference = 'SilentlyContinue'
$probe = 8899
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$probe/health" -TimeoutSec 3
    Write-Host "运行中: http://127.0.0.1:$probe/health" -ForegroundColor Green
    $conn = Get-NetTCPConnection -LocalPort $probe -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue; Write-Host "PID: $($conn.OwningProcess)" }
    Write-Host ("内网: " + $h.internal)
    Write-Host ("公网: " + $h.public)
    Write-Host ("内网Token: " + $(if($h.has_internal_token){'已配置'}else{'缺失'}) + "  公网Key: " + $(if($h.has_public_key){'已配置'}else{'缺失'}))
    Write-Host ""
    Write-Host "最近路由统计（自本次启动累计）:"
    $r = $h.routes
    if ($r) {
        Write-Host ("  请求总数: " + $r.requests)
        Write-Host ("  走内网:   " + $r.internal + "   (深绿=internal)")
        Write-Host ("  走公网:   " + $r.public + "   (回退=public)")
        Write-Host ("  失败:     " + $r.failed)
        $route = $r.last_route
        if ($route -eq 'internal') {
            Write-Host ("  最近一次: 内网 (internal)  时间 " + $r.last_route_at) -ForegroundColor Cyan
        } elseif ($route -eq 'public') {
            Write-Host ("  最近一次: 公网 (public 回退)  时间 " + $r.last_route_at) -ForegroundColor Yellow
        } elseif ($route -eq 'failed') {
            Write-Host ("  最近一次: 失败 (failed)  时间 " + $r.last_route_at) -ForegroundColor Red
        } else {
            Write-Host "  最近一次: 尚无模型请求"
        }
    }
    Write-Host ""
    Write-Host "想实时看每条请求走哪条: 浏览器打开 http://127.0.0.1:$probe/health（刷新即最新累计）"
} catch {
    Write-Host "代理未运行 (端口 $probe 无 /health 响应)" -ForegroundColor Yellow
    Write-Host "启动:  powershell -ExecutionPolicy Bypass -File dsh-failover\start.ps1"
    exit 1
}
