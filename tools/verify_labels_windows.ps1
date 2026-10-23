param([string]$Directory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStreamWithContentType,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.DataWriter,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$bridge=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1)
function Wait-Operation($Operation,[Type]$ResultType) {
    $task=$bridge.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    try {$task.Wait()} catch {throw ("WinRT " + $ResultType.FullName + ": " + $_.Exception.GetBaseException().Message)}
    return $task.Result
}
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$output=New-Object System.IO.StreamWriter((Join-Path $Directory 'ocr-labels.jsonl'),$false,(New-Object System.Text.UTF8Encoding($false)))
try {
    $files=Get-ChildItem -LiteralPath (Join-Path $Directory 'labels') -Filter '*.png' | Sort-Object Name
    $count=0
    foreach($path in $files) {
        $stream=New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        $writer=New-Object Windows.Storage.Streams.DataWriter($stream)
        $writer.WriteBytes([System.IO.File]::ReadAllBytes($path.FullName))
        $null=Wait-Operation ($writer.StoreAsync()) ([uint32])
        $null=$writer.DetachStream();$writer.Dispose();$stream.Seek(0)
        $decoder=Wait-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=Wait-Operation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result=Wait-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $output.WriteLine((@{id=$path.BaseName;text=$result.Text} | ConvertTo-Json -Compress))
        $bitmap.Dispose();$stream.Dispose();$count++
        if($count % 100 -eq 0){Write-Output "OCR $count / $($files.Count)";$output.Flush()}
    }
} finally {$output.Dispose()}
