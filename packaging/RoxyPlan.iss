; Compile with ISCC /DBundleDir=... /DOutputDir=... RoxyPlan.iss
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#define ProductVersion "0.9.0-aic2026-rc.1"

[Setup]
AppId={{95F06D28-0A3C-48DF-979F-B2B18F44D2C8}
AppName=RoxyPlan
AppVersion={#ProductVersion}
AppPublisher=pluto525748
AppPublisherURL=https://github.com/pluto525748/-RoxyPlan
AppSupportURL=https://github.com/pluto525748/-RoxyPlan/issues
DefaultDirName={localappdata}\Programs\RoxyPlan
DefaultGroupName=RoxyPlan
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=RoxyPlan-{#ProductVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile={#BundleDir}\DISTRIBUTION_TERMS.txt
InfoBeforeFile={#BundleDir}\QUICK_START.txt
UninstallDisplayIcon={app}\RoxyPlan.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut / 创建桌面快捷方式"; Flags: checkedonce

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\RoxyPlan"; Filename: "{app}\RoxyPlan.exe"
Name: "{autodesktop}\RoxyPlan"; Filename: "{app}\RoxyPlan.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RoxyPlan.exe"; Description: "Start RoxyPlan / 打开 RoxyPlan"; Flags: nowait postinstall skipifsilent unchecked

; Deliberately no UninstallDelete: user records under LocalAppData\RoxyPlan
; are separate from program files and must survive reinstall/uninstall.
