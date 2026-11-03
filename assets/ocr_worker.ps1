# Native Windows OCR. Images and text stay in memory and cross stdin/stdout only.
# ASCII source deliberately supports Windows PowerShell 5.1 without a BOM.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Send-Result($value) {
    [Console]::WriteLine(($value | ConvertTo-Json -Compress -Depth 8))
    [Console]::Out.Flush()
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Foundation, ContentType=WindowsRuntime]
    $null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
    $null = [Windows.Storage.Streams.DataWriter, Windows.Storage.Streams, ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
    $script:AsyncMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
    function Await-Result($operation, [Type]$resultType) {
        $task = $script:AsyncMethod.MakeGenericMethod($resultType).Invoke($null, @($operation))
        $task.Wait()
        return $task.Result
    }
    $languages = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
    $engines = @{}
    foreach ($tag in @('zh-Hans-CN', 'en-US')) {
        $language = New-Object Windows.Globalization.Language($tag)
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        if ($null -ne $engine) { $engines[$tag] = $engine }
    }
    Send-Result @{state='ready'; languages=$languages; max_dimension=[Windows.Media.Ocr.OcrEngine]::MaxImageDimension}
    while ($null -ne ($line = [Console]::ReadLine())) {
        $bitmap = $null
        $stream = $null
        $writer = $null
        try {
            $request = $line | ConvertFrom-Json
            if ($request.action -eq 'quit') { break }
            if ($request.action -eq 'probe') {
                Send-Result @{state='probe'; languages=$languages; max_dimension=[Windows.Media.Ocr.OcrEngine]::MaxImageDimension}
                continue
            }
            if ($request.action -ne 'recognize') { throw 'invalid_request' }
            if ($engines.Count -eq 0) { throw 'no_language' }
            $bytes = [Convert]::FromBase64String($request.image)
            $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
            $writer = New-Object Windows.Storage.Streams.DataWriter($stream)
            $writer.WriteBytes($bytes)
            $null = Await-Result ($writer.StoreAsync()) ([uint32])
            $null = $writer.DetachStream()
            $writer.Dispose()
            $writer = $null
            $stream.Seek(0)
            $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            # The Chinese recognizer handles mixed Chinese + English. English is
            # also evaluated to preserve punctuation and spacing in English-only UI.
            $results = @()
            foreach ($tag in @('zh-Hans-CN', 'en-US')) {
                if (-not $engines.ContainsKey($tag)) { continue }
                $result = Await-Result ($engines[$tag].RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
                $text = (($result.Lines | ForEach-Object { $_.Text }) -join "`n")
                $results += @{language=$tag; text=$text}
            }
            Send-Result @{state='recognized'; results=$results}
        } catch {
            $code = 'recognition_failed'
            if ($_.Exception.Message -eq 'no_language') { $code = 'no_language' }
            Send-Result @{state='error'; error=$code}
        } finally {
            if ($null -ne $bitmap) { $bitmap.Dispose() }
            if ($null -ne $writer) { $writer.Dispose() }
            if ($null -ne $stream) { $stream.Dispose() }
            $line = $null
            $request = $null
            $bytes = $null
        }
    }
} catch {
    Send-Result @{state='error'; error='native_unavailable'}
    exit 1
}
