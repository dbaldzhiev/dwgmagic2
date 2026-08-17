; Inno Setup script for DWGMAGIC.
;
; Packages the PyInstaller onedir bundle in dist\dwgmagic2 into a single
; setup executable. Everything the app needs — Python included — is already
; inside that bundle, so the install itself touches the network at no point
; and does not care whether the machine has Python.
;
; Build with:  iscc /DAppVersion=1.0.0 installer\dwgmagic2.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "DWGMAGIC"
#define AppPublisher "Dimitar Baldzhiev"
#define AppUrl "https://github.com/dbaldzhiev/dwgmagic2"
#define GuiExe "dwgmagic2w.exe"

[Setup]
; Never change AppId — it is how Windows recognises an upgrade of this app.
AppId={{8E3D9F42-6C7B-4A15-9E2D-1F0B7C4A5D63}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases

; Per-user install under %LOCALAPPDATA%: no administrator rights, no UAC prompt.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\dwgmagic2
DisableDirPage=auto
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#GuiExe}
UninstallDisplayName={#AppName} {#AppVersion}

OutputDir=Output
OutputBaseFilename=dwgmagic2-setup-v{#AppVersion}
SetupIconFile=..\magic.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "contextmenu"; Description: "Add ""Run with DWGMAGIC"" to folder right-click menus"; GroupDescription: "Shell integration:"

[Files]
Source: "..\dist\dwgmagic2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#GuiExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#GuiExe}"; Tasks: desktopicon

[Registry]
; Right-click a folder, or a folder's background, to run that folder as a project.
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DWGMAGIC"; ValueType: string; ValueName: ""; ValueData: "Run with DWGMAGIC"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DWGMAGIC"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#GuiExe}"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DWGMAGIC\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#GuiExe}"" ""%1"" --autorun"; Flags: uninsdeletekey; Tasks: contextmenu

Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DWGMAGIC"; ValueType: string; ValueName: ""; ValueData: "Run with DWGMAGIC"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DWGMAGIC"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#GuiExe}"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DWGMAGIC\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#GuiExe}"" ""%V"" --autorun"; Flags: uninsdeletekey; Tasks: contextmenu

[Run]
Filename: "{app}\{#GuiExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Written at runtime, so Setup does not know about them and would leave them behind.
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\_internal"
