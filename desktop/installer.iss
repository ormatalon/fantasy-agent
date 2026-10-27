; Windows installer (Inno Setup). Build the app with PyInstaller first, then:
;   iscc /DAppVersion=1.0.0 desktop/installer.iss
; Output: dist/FantasyAgent-Setup.exe. Installs per user: no admin needed.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{4CA3BEDD-6D5B-45BB-8A8E-0F1038B4920E}
AppName=Fantasy Agent
AppVersion={#AppVersion}
AppPublisher=Fantasy Agent
DefaultDirName={autopf}\Fantasy Agent
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=FantasyAgent-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\FantasyAgent.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\FantasyAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Fantasy Agent"; Filename: "{app}\FantasyAgent.exe"
Name: "{autodesktop}\Fantasy Agent"; Filename: "{app}\FantasyAgent.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\FantasyAgent.exe"; Description: "{cm:LaunchProgram,Fantasy Agent}"; Flags: nowait postinstall skipifsilent
