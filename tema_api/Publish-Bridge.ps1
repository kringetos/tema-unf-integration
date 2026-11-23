param(
    [string]$Publication = 'C:\inetpub\wwwroot\UNF\default.vrd',
    [Parameter(Mandatory = $true)][string]$ResultPath
)

$ErrorActionPreference = 'Stop'
try {
    $publicationPath = (Resolve-Path -LiteralPath $Publication).Path
    $source = [System.IO.File]::ReadAllText($publicationPath)
    [xml]$document = $source
    $namespace = New-Object System.Xml.XmlNamespaceManager($document.NameTable)
    $namespace.AddNamespace('v', 'http://v8.1c.ru/8.2/virtual-resource-system')
    $services = $document.SelectSingleNode('/v:point/v:httpServices', $namespace)
    if (-not $services) { throw 'The publication has no httpServices section.' }
    $existing = $services.SelectSingleNode('v:service[@name="TEMA_API"]', $namespace)
    if (-not $existing) {
        $backupPath = $publicationPath + '.before-tema-api.bak'
        if (-not (Test-Path -LiteralPath $backupPath)) {
            Copy-Item -LiteralPath $publicationPath -Destination $backupPath
        }
        $insertion = '<service name="TEMA_API" rootUrl="tema_bridge" enable="true" reuseSessions="dontuse" sessionMaxAge="20" poolSize="5" poolTimeout="5"/>'
        if (-not $source.Contains('</httpServices>')) { throw 'Unexpected publication format.' }
        $updated = $source.Replace('</httpServices>', ($insertion + [Environment]::NewLine + '</httpServices>'))
        [System.IO.File]::WriteAllText($publicationPath, $updated, (New-Object System.Text.UTF8Encoding($false)))
    }
    @{ok=$true; publication=$publicationPath; service='TEMA_API'} | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
} catch {
    @{ok=$false; error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    exit 1
}
