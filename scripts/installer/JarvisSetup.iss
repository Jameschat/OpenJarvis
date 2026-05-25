#define MyAppName "J.A.R.V.I.S."
#define MyAppVersion "1.0.0"
#define MyAppPublisher "OpenJarvis Local"

[Setup]
AppId={{F170DC64-58E8-4B90-BBB6-113018C9A58D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=E:\Claude
DisableDirPage=no
DefaultGroupName=J.A.R.V.I.S.
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=JarvisSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\OpenJarvis\jarvis.bat

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "install-jarvis.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\..\dist\installer\JarvisPayload.zip"; DestDir: "{app}\installer"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\install-jarvis.ps1"" -PayloadZip ""{app}\installer\JarvisPayload.zip"" -InstallRoot ""{app}"""; Description: "Install and restore Jarvis now"; Flags: postinstall runascurrentuser
