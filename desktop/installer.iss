; Per-user installer, no admin rights: iscc /DAppVersion=0.1.0 desktop\installer.iss
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F1D2B7C-3B7E-4C57-9A5E-2D9C8A41F0E3}
AppName=Fantasy Agent
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\FantasyAgent
DefaultGroupName=Fantasy Agent
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=FantasyAgent-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\FantasyAgent.exe

[Files]
Source: "..\dist\FantasyAgent\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Fantasy Agent"; Filename: "{app}\FantasyAgent.exe"
Name: "{autodesktop}\Fantasy Agent"; Filename: "{app}\FantasyAgent.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\FantasyAgent.exe"; Description: "Start Fantasy Agent"; Flags: nowait postinstall skipifsilent
