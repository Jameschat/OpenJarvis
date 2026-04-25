# Claude Code hook -> Jarvis mission control bridge.
#
# Reads the hook JSON payload from stdin and POSTs it to the local Jarvis
# brain server as UTF-8 bytes. Silent on success, silent on failure (Jarvis
# being down must NEVER block Claude Code).
#
# Forces UTF-8 end-to-end because PowerShell 5.1 defaults to the console
# code page (CP1252) for both stdin reads and Invoke-WebRequest bodies,
# which corrupts any non-ASCII characters in the payload.

param(
    [string]$Url = 'http://127.0.0.1:7710/claude_event'
)

$ErrorActionPreference = 'SilentlyContinue'

try {
    # Read stdin as raw bytes so we preserve whatever encoding Claude sent
    # (almost always UTF-8). Skip the text path entirely.
    $stdin = [Console]::OpenStandardInput()
    $memStream = New-Object System.IO.MemoryStream
    $buffer = New-Object byte[] 8192
    while (($read = $stdin.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $memStream.Write($buffer, 0, $read)
    }
    $bytes = $memStream.ToArray()

    if ($null -eq $bytes -or $bytes.Length -eq 0) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes('{}')
    }

    # POST with an explicit byte-array body so PowerShell doesn't re-encode
    $null = Invoke-WebRequest `
        -Uri $Url `
        -Method POST `
        -Body $bytes `
        -ContentType 'application/json; charset=utf-8' `
        -TimeoutSec 2 `
        -UseBasicParsing `
        -ErrorAction SilentlyContinue
} catch {
    # Swallow everything - Jarvis downtime must not break Claude Code.
}

exit 0
