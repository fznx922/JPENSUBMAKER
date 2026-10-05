; Inno Setup script → dist\JPENSubMaker-Setup.exe
; Per-user install (no admin prompt) into %LOCALAPPDATA%\Programs\JPEN SubMaker.
; Build:  set APP_VERSION=0.2.0 && iscc packaging\installer.iss   (after PyInstaller has produced dist\JPENSubMaker)

#define AppVersion GetEnv("APP_VERSION")
#if AppVersion == ""
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B0E9C4F-3A1D-4E57-9B8E-2F4C7D1A9E21}
AppName=JPEN SubMaker
AppVersion={#AppVersion}
AppVerName=JPEN SubMaker {#AppVersion}
AppPublisher=JPEN SubMaker
AppPublisherURL=https://github.com/fznx922/JPENSUBMAKER
DefaultDirName={localappdata}\Programs\JPEN SubMaker
DefaultGroupName=JPEN SubMaker
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=JPENSubMaker-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\JPENSubMaker.exe
UninstallDisplayName=JPEN SubMaker
Compression=lzma2/fast
SolidCompression=no
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\JPENSubMaker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\JPEN SubMaker"; Filename: "{app}\JPENSubMaker.exe"
Name: "{autodesktop}\JPEN SubMaker"; Filename: "{app}\JPENSubMaker.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\JPENSubMaker.exe"; Description: "{cm:LaunchProgram,JPEN SubMaker}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; downloaded models live in %LOCALAPPDATA%\JPENSubMaker (several GB); removed on uninstall
Type: filesandordirs; Name: "{localappdata}\JPENSubMaker"
