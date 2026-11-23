param([Parameter(Mandatory=$true)][string]$Config, [string]$ResultPath, [string]$ConnectorProgId='V85.COMConnector')
$ErrorActionPreference='Stop'
$Config=(Resolve-Path -LiteralPath $Config).Path
if(-not $ResultPath){$ResultPath=Join-Path (Split-Path -Parent $Config) 'allow-printing-result.json'}
$stage='start'
try {
    $cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
    $credentials=$cfg
    if($cfg.credentials_file) {
        $credentialFile=[string]$cfg.credentials_file
        if(-not [IO.Path]::IsPathRooted($credentialFile)) {
            $credentialFile=Join-Path (Split-Path -Parent $Config) $credentialFile
        }
        $credentials=Get-Content -LiteralPath $credentialFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $credentialPath=if($cfg.credentials_path){[string]$cfg.credentials_path}else{'profiles.default.agents.odata'}
        foreach($part in $credentialPath.Split('.')){$credentials=$credentials.$part}
    }
    $userText=([string]$credentials.odata_user).Replace('"','""')
    $passwordText=([string]$credentials.odata_password).Replace('"','""')
    $stage='connector'
    $connector=New-Object -ComObject $ConnectorProgId
    $connection=$connector.Connect(('File="'+$cfg.infobase+'";Usr="'+$userText+'";Pwd="'+$passwordText+'";'))
    $stage='connection-check'
    if($null -eq $connection){throw 'Connect returned null'}
    $metadata=$connection.GetType().InvokeMember('Metadata',[Reflection.BindingFlags]::GetProperty,$null,$connection,$null)
    $metadataName=$metadata.GetType().InvokeMember('Name',[Reflection.BindingFlags]::GetProperty,$null,$metadata,$null)
    $stage='extension-manager'
    $manager=$connection.GetType().InvokeMember('ConfigurationExtensions',[Reflection.BindingFlags]::GetProperty,$null,$connection,$null)
    $stage='extension-list'
    $extensions=$manager.GetType().InvokeMember('Get',[Reflection.BindingFlags]::InvokeMethod,$null,$manager,$null)
    $stage='extension-enumeration'
    $found=0
    $before=$null
    $after=$null
    $count=$extensions.GetType().InvokeMember('Count',[Reflection.BindingFlags]::InvokeMethod,$null,$extensions,$null)
    for($index=0;$index -lt $count;$index++) {
        $extension=$extensions.GetType().InvokeMember('Get',[Reflection.BindingFlags]::InvokeMethod,$null,$extensions,@($index))
        $extensionName=$extension.GetType().InvokeMember('Name',[Reflection.BindingFlags]::GetProperty,$null,$extension,$null)
        if($extensionName -eq 'TEMA_API') {
            $found++
            $before=[bool]$extension.GetType().InvokeMember('SafeMode',[Reflection.BindingFlags]::GetProperty,$null,$extension,$null)
            $extension.GetType().InvokeMember('SafeMode',[Reflection.BindingFlags]::SetProperty,$null,$extension,@($false))
            $extension.GetType().InvokeMember('Write',[Reflection.BindingFlags]::InvokeMethod,$null,$extension,$null)
            $after=[bool]$extension.GetType().InvokeMember('SafeMode',[Reflection.BindingFlags]::GetProperty,$null,$extension,$null)
        }
    }
    if($found -ne 1) { throw 'Expected exactly one TEMA_API extension.' }
    @{ok=$true;extension='TEMA_API';safeModeBefore=$before;safeModeAfter=$after} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($connection)
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($connector)
} catch {
    @{ok=$false;stage=$stage;metadata=$metadataName;error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    exit 1
}
