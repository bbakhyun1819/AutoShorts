$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
py -3.11 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 설치 여부를 확인하세요.' }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip 업데이트 실패' }
& .\.venv\Scripts\python.exe -m pip install -e '.[gpu,ai]'
if ($LASTEXITCODE -ne 0) { throw '패키지 설치 실패' }
& .\.venv\Scripts\python.exe -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-nvrtc-cu12
if ($LASTEXITCODE -ne 0) { throw 'CUDA 런타임 설치 실패' }
& .\.venv\Scripts\chzzk-demo.exe doctor
