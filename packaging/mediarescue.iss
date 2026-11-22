; Inno Setup script for the Windows build.
;
; The end goal is one file a non-technical person can double-click: mediarescue-<version>-setup.exe.
; It installs per-user (no UAC prompt, no admin), puts ffmpeg beside the exe, and creates a Start
; Menu shortcut -- which is where the all-in-one menu shows up when the program is started with no
; arguments.
;
; Build: ISCC.exe /DAppVersion=0.11.0 packaging\mediarescue.iss   (from a dist-exe\mediarescue folder)

#define AppName "mediarescue"
#define AppPublisher "lutherrio25-svg"
#define AppExe "mediarescue.exe"
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
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
ChangesEnvironment=no

[Files]
Source: "..\dist-exe\mediarescue\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; Flags: postinstall nowait skipifsilent
