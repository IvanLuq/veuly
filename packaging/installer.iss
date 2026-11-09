; Inno Setup script: turns dist\Veuly (made by packaging/build.py) into dist\Veuly-Setup.exe.
; Installs for the current user only, so it needs no administrator rights.
#define AppVersion "0.3.0"

[Setup]
AppId={{6F0B1C7E-5A0B-4E0E-9B8B-7A1C2D3E4F50}
AppName=Veuly
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Veuly
DefaultGroupName=Veuly
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Veuly-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "catalan"; MessagesFile: "compiler:Languages\Catalan.isl"

[Files]
Source: "..\dist\Veuly\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Veuly"; Filename: "{app}\Veuly.exe"
Name: "{userdesktop}\Veuly"; Filename: "{app}\Veuly.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"

[Run]
Filename: "{app}\Veuly.exe"; Description: "{cm:LaunchProgram,Veuly}"; Flags: nowait postinstall skipifsilent
