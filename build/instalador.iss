; instalador.iss — Inno Setup 6.3+ para o GSPHosp
; Compilar (na raiz do projeto, depois do PyInstaller):
;   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.0 build\instalador.iss
; Resultado: dist\GSPHosp-Setup-1.0.0.exe
;
; Instala por usuário (sem pedir administrador) em %LOCALAPPDATA%\Programs\GSPHosp.
; Dados e saídas ficam fora da pasta do programa (ver build\launcher.py).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "GSPHosp"
#define AppPublisher "NESCON/UFMG - Orbitaai"
#define AppURL "https://github.com/chicocard/GSPHosp"
#define AppExe "GSPHosp.exe"

[Setup]
AppId={{ED196681-0520-4D8A-A33D-4F2BB689E30B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=icone.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=yes
RestartApplications=no
MinVersion=10.0

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\GSPHosp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{localappdata}\{#AppName}\logs"
Name: "{localappdata}\{#AppName}\cache"
Name: "{localappdata}\{#AppName}\cnes"
Name: "{userdocs}\{#AppName}\output"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; o cache pode ser recriado; logs, CNES e saídas ficam com o usuário
Type: filesandordirs; Name: "{localappdata}\{#AppName}\cache"
