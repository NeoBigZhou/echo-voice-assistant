# fix-ps1-encoding.ps1 — 给本目录的 .ps1 补回 UTF-8 BOM
# 为什么需要：Windows PowerShell 5.1 靠文件开头的 BOM 判断编码。用编辑器/工具改写 .ps1 时
# BOM 容易丢，中文注释就被当成乱码，报错形如：
#   The Try statement is missing its Catch or Finally block. / Unexpected token ...
# 改完任何 .ps1 之后跑一下这个脚本即可（幂等）：
#   powershell -ExecutionPolicy Bypass -File dsh-failover\fix-ps1-encoding.ps1
$dir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $dir) { $dir = (Get-Location).Path }
Write-Host ("目录: " + $dir)
$n = 0
foreach ($f in Get-ChildItem -Path $dir -Filter *.ps1 -File) {
    $t = [System.Text.Encoding]::UTF8.GetString([System.IO.File]::ReadAllBytes($f.FullName))
    if ($t.Length -gt 0 -and $t[0] -eq [char]0xFEFF) { $t = $t.Substring(1) }
    [System.IO.File]::WriteAllText($f.FullName, $t, (New-Object System.Text.UTF8Encoding($true)))
    $b = [System.IO.File]::ReadAllBytes($f.FullName)
    $ok = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
    $first = (($t -split "`r?`n")[0])
    $hash = if ($first.StartsWith('#')) { "首行#=OK" } else { "首行#=缺失!" }
    $n++
    Write-Host ("  {0,-26} BOM={1}  {2}" -f $f.Name, $ok, $hash)
}
Write-Host ("共处理 $n 个 .ps1")
