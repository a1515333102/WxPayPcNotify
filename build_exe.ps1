# 重新打包 WxPayPcNotify.exe
# 用法：在 d:\wx 下执行 .\build_exe.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --console `
  --name WxPayPcNotify `
  --collect-all rapidocr_onnxruntime `
  --collect-all onnxruntime `
  --collect-all comtypes `
  --hidden-import=win32timezone `
  --hidden-import=pythoncom `
  --hidden-import=pywintypes `
  .\WxPayPcNotify.py

Write-Host ""
Write-Host "完成: $PSScriptRoot\dist\WxPayPcNotify.exe"
