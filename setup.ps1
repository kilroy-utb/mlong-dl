# PowerShell 用的設定檔（等同 setup.sh 但 syntax 對 PS）

# 用法：
#   . .\setup.ps1    （注意前面的 . 和 \）

$env:MLONG_API_KEY = '190e8568de6f41f691012b7357572465'
Write-Host '✓ MLONG_API_KEY 已設到這個 PowerShell session' -ForegroundColor Green
Write-Host ''
Write-Host '現在可以跑:'
Write-Host '  python mlong-dl.py --api-key $env:MLONG_API_KEY gui'
Write-Host ''
Write-Host '或要永久存 (寫到 PowerShell profile):'
Write-Host '  [Environment]::SetEnvironmentVariable("MLONG_API_KEY", "190e8568de6f41f691012b7357572465", "User")'
Write-Host ''

# 嘗試自動更新 Python path（避免以後 "python not found"）
$python = (Get-Command python -ErrorAction SilentlyContinue)
if ($python) {
    Write-Host "Python 找到: $($python.Source)" -ForegroundColor Green
} else {
    Write-Host "✗ 找不到 python" -ForegroundColor Red
    Write-Host '  試: py --version 或 python3 --version'
}