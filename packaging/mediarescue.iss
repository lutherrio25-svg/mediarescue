; Inno Setup script for the Windows build.
;
; The end goal is one file a non-technical person can double-click: mediarescue-<version>-setup.exe.
; It installs per-user (no UAC prompt, no admin), puts ffmpeg beside the exe, and creates a Start
; Menu shortcut. That shortcut opens the *window* build -- a program called from Explorer must not
; flash a console at someone who has never opened one -- while the command line build sits beside
; it for scripts and for anyone who prefers it.
;
; Build: ISCC.exe /DAppVersion=0.13.0 packaging\mediarescue.iss   (from a dist-exe\mediarescue folder)

#define AppName "mediarescue"
#define AppPublisher "Star Corps bks"
#define AppExe "mediarescue-window.exe"
#define CliExe "mediarescue.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{E9BDBDB1-7653-5DE3-B0B3-508E8946DBBC}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppSupportURL=https://github.com/lutherrio25-svg/mediarescue/issues
DefaultDirName={localappdata}\Programs\mediarescue
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-setup
OutputBaseFilename={#AppName}-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The icon file is what Windows shows in the wizard, in Add/Remove Programs and on the setup.exe
; itself. Without it both fall back to the Inno Setup default and the product looks unfinished.
SetupIconFile=mediarescue.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
ChangesEnvironment=no

[Files]
Source: "..\dist-exe\mediarescue\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; 0.13.0 shipped two static ffmpeg binaries (336 MB) and an older Python runtime; the shared ffmpeg
; build replaced them, and Inno would otherwise leave ~360 MB of orphans behind forever on upgrade.
; Wiping _internal before copying is the honest way to make an onedir upgrade equal a clean install.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} (command line)"; Filename: "{app}\{#CliExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; Flags: postinstall nowait skipifsilent
