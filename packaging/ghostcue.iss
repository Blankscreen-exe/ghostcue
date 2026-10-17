; Inno Setup 6 script for the GhostCue installer. packaging\build.ps1 runs it after PyInstaller,
; passing the version: iscc /DAppVersion=1.2.3 packaging\ghostcue.iss
;
; It installs for the current user only, into %LOCALAPPDATA%\Programs\GhostCue, so it needs no
; administrator rights. Settings and call transcripts in %APPDATA%\GhostCue are left on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; The ID ties every version together, so a new one replaces the old. Never change it.
AppId={{B54B59DF-D7F8-4E0F-9BBC-A1BD3B09666F}
AppName=GhostCue
AppVersion={#AppVersion}
AppVerName=GhostCue {#AppVersion}
AppPublisher=Blankscreen-exe
AppPublisherURL=https://github.com/Blankscreen-exe/ghostcue
AppSupportURL=https://github.com/Blankscreen-exe/ghostcue/issues
AppUpdatesURL=https://github.com/Blankscreen-exe/ghostcue/releases
DefaultDirName={autopf}\GhostCue
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; Windows 10 version 2004, the first that can keep a window out of screen captures.
MinVersion=10.0.19041
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The tray app holds this mutex while it runs, so setup can ask for it to be closed first.
AppMutex=GhostCue.Assistant
SetupIconFile=ghostcue.ico
UninstallDisplayIcon={app}\GhostCue.exe
UninstallDisplayName=GhostCue
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=GhostCue-{#AppVersion}-setup

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start GhostCue when I sign in to Windows"; GroupDescription: "Other:"; Flags: unchecked

[InstallDelete]
; An upgrade would otherwise leave the old version's libraries beside the new ones.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\GhostCue\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GhostCue"; Filename: "{app}\GhostCue.exe"
Name: "{autoprograms}\GhostCue Teleprompter"; Filename: "{app}\GhostCue.exe"; Parameters: "prompter"
Name: "{autodesktop}\GhostCue"; Filename: "{app}\GhostCue.exe"; Tasks: desktopicon
Name: "{userstartup}\GhostCue"; Filename: "{app}\GhostCue.exe"; Tasks: startup

[Run]
Filename: "{app}\GhostCue.exe"; Description: "{cm:LaunchProgram,GhostCue}"; Flags: nowait postinstall skipifsilent
