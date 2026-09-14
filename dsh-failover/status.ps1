# status.ps1 — 查看 ECHO 模型路由状态与组成员健康（隐藏密钥）
#   用法: powershell -ExecutionPolicy Bypass -File dsh-failover\status.ps1
$ErrorActionPreference = 'SilentlyContinue'
$probe = 8899
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$probe/health" -TimeoutSec 3
    Write-Host "运行中: http://127.0.0.1:$probe/health" -ForegroundColor Green
    $conn = Get-NetTCPConnection -LocalPort $probe -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { Write-Host "PID: $($conn.OwningProcess)" }
    Write-Host ""
    foreach ($g in $h.groups) {
        Write-Host ("[" + $g.id + "] " + $g.display_name + "   启用 " + $g.active + " 个 / 健康 " + $g.healthy + " 个") -ForegroundColor Cyan
        foreach ($m in $g.members) {
            $state = if ($m.enabled -eq $false) { "停用" }
                     elseif ($m.state -eq 'open') { "熔断" }
                     elseif ($m.reachable -eq $true) { "可达" }
                     elseif ($m.reachable -eq $false) { "不可达" }
                     else { "未判" }
            $color = if ($state -eq "可达") { "Green" } elseif ($state -eq "停用") { "DarkGray" } else { "Yellow" }
            $ttfb = if ($null -ne $m.last_ttfb_ms) { "$($m.last_ttfb_ms)ms" } else { "—" }
            $key = if ($m.has_token) { "" } else { "  [缺凭据]" }
            Write-Host ("  通道" + $m.priority + " " + $m.name.PadRight(16) + " " + $state.PadRight(4) +
                        " 成功/失败 " + $m.ok + "/" + $m.fail + "  首字节 " + $ttfb + "  " + $m.detail + $key) -ForegroundColor $color
            if ($m.last_error) { Write-Host ("       最近错误: " + $m.last_error) -ForegroundColor DarkYellow }
        }
    }
    Write-Host ""
    Write-Host "派发统计（自本次启动累计）:"
    $r = $h.routes
    if ($r) {
        Write-Host ("  请求总数:   " + $r.requests)
        Write-Host ("  失败:       " + $r.failed)
        if ($r.last_route_at) {
            if ($r.last_channel) {
                Write-Host ("  最近命中:   通道 " + $r.last_channel + " " + $r.last_member + "   时间 " + $r.last_route_at) -ForegroundColor Green
            } else {
                Write-Host ("  最近一次:   没有通道接住   时间 " + $r.last_route_at) -ForegroundColor Red
            }
        } else {
            Write-Host "  最近一次:   尚无模型请求"
        }
    }
    Write-Host ""
    Write-Host "改通道昵称/顺序: ECHO 面板 →「模型路由」页；命令行自检: python dsh-failover\check.py --call"
} catch {
    Write-Host "模型路由未运行 (端口 $probe 无 /health 响应)" -ForegroundColor Yellow
    Write-Host "启动:  powershell -ExecutionPolicy Bypass -File dsh-failover\start.ps1"
    exit 1
}
