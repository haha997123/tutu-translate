$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
foreach ($requiredModel in @('models/en_zh/model/model.bin','models/en_zh/sentencepiece.model','models/zh_en/model/model.bin','models/zh_en/sentencepiece.model')) {
    if (-not (Test-Path -LiteralPath $requiredModel)) { throw 'Prepare the model weights and tokenizers with python scripts/setup_models.py before building' }
}
python scripts/setup_models.py --verify
if ($LASTEXITCODE -ne 0) { throw 'Model verification failed' }
python scripts/setup_voice_model.py --verify
if ($LASTEXITCODE -ne 0) { throw 'Voice model verification failed' }
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
python -m pip install --no-deps rapidocr-onnxruntime==1.4.4
if ($LASTEXITCODE -ne 0) { throw 'OCR installation failed' }
python -m PyInstaller --noconfirm --onedir --windowed --name TutuTranslate --icon assets/icon.ico --add-data "$PSScriptRoot/assets;assets" --additional-hooks-dir hooks --collect-data customtkinter --collect-binaries ctranslate2 --collect-data ctranslate2 --collect-binaries sentencepiece --collect-data rapidocr_onnxruntime --collect-binaries onnxruntime --collect-data onnxruntime --collect-all sherpa_onnx --copy-metadata sherpa-onnx --copy-metadata sounddevice --copy-metadata ctranslate2 --copy-metadata sentencepiece --copy-metadata rapidocr-onnxruntime --copy-metadata onnxruntime --hidden-import pystray._win32 --exclude-module cryptography --exclude-module OpenSSL --exclude-module sympy --exclude-module onnxruntime.tools --exclude-module onnxruntime.quantization --exclude-module onnxruntime.transformers --exclude-module ctranslate2.converters --exclude-module PIL.AvifImagePlugin --exclude-module PIL._avif --exclude-module PIL.WebPImagePlugin --exclude-module PIL._webp app.py
if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
# OCR only uses still images; do not ship OpenCV's unused video decoder.
$unusedVideoLibrary = Join-Path $PSScriptRoot 'dist/TutuTranslate/_internal/cv2/opencv_videoio_ffmpeg4100_64.dll'
if (Test-Path -LiteralPath $unusedVideoLibrary) { Remove-Item -LiteralPath $unusedVideoLibrary -Force }
$exeName = ([char]0x5154).ToString() + [char]0x5154 + [char]0x7FFB + [char]0x8BD1 + '.exe'
Move-Item -LiteralPath (Join-Path $PSScriptRoot 'dist/TutuTranslate/TutuTranslate.exe') -Destination (Join-Path $PSScriptRoot "dist/TutuTranslate/$exeName") -Force
Copy-Item -LiteralPath README.md,THIRD_PARTY_NOTICES.md -Destination dist/TutuTranslate
if (Test-Path -LiteralPath licenses) { Copy-Item -LiteralPath licenses -Destination dist/TutuTranslate -Recurse -Force }
if (Test-Path -LiteralPath models) { Copy-Item -LiteralPath models -Destination dist/TutuTranslate -Recurse -Force } else { throw 'Copy the installed models directory beside the source before building' }
Write-Host "Done: dist/TutuTranslate/$exeName"
