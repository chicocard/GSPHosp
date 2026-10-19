; instalador-docker.iss — Inno Setup 6.3+ : GSPHosp em Docker (o app roda num contêiner e aparece no navegador)
; Compilar (na raiz do projeto):
;   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.0 docker\instalador-docker.iss
;   & ... /DAppVersion=1.0.0 /DComImagem docker\instalador-docker.iss   → embute dist\gsphosp-image.tar (offline, ~1 GB)
; Resultado: dist\GSPHosp-Docker-Setup-1.0.0.exe
;
; O instalador NÃO instala o Docker Desktop (exige administrador, WSL2 e reinício, e tem licença própria).
; Ele verifica se o Docker está presente; se não estiver, abre a página de download e orienta.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "GSPHosp"
#define AppPublisher "NESCON/UFMG - Orbitaai"
#define AppURL "https://github.com/chicocard/GSPHosp"
#define Imagem "ghcr.io/chicocard/gsphosp:" + AppVersion

[Setup]
AppId={{3F6A1C2E-8D4B-4F0A-9E57-2B7C5D1A9F10}
AppName={#AppName} (Docker)
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} (Docker)
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={localappdata}\Programs\GSPHosp-Docker
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename={#AppName}-Docker-Setup-{#AppVersion}
SetupIconFile=..\build\icone.ico
UninstallDisplayIcon={app}\icone.ico
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
ShowLanguageDialog=no
MinVersion=10.0.19041

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\docker-compose.yml"; DestDir: "{app}"; Flags: ignoreversion
Source: "gsphosp-docker.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\icone.ico"; DestDir: "{app}"; Flags: ignoreversion
#ifdef ComImagem
Source: "..\dist\gsphosp-image.tar"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Dirs]
Name: "{userdocs}\{#AppName}\output"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\gsphosp-docker.ps1"" start"; WorkingDir: "{app}"; IconFilename: "{app}\icone.ico"; Comment: "Abre o GSPHosp no navegador"
Name: "{autoprograms}\{#AppName} - parar"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\gsphosp-docker.ps1"" stop"; WorkingDir: "{app}"; IconFilename: "{app}\icone.ico"
Name: "{autoprograms}\{#AppName} - atualizar"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\gsphosp-docker.ps1"" update"; WorkingDir: "{app}"; IconFilename: "{app}\icone.ico"
Name: "{autoprograms}\{#AppName} - logs"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\gsphosp-docker.ps1"" logs"; WorkingDir: "{app}"; IconFilename: "{app}\icone.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\gsphosp-docker.ps1"" start"; WorkingDir: "{app}"; IconFilename: "{app}\icone.ico"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\gsphosp-docker.ps1"" start"; WorkingDir: "{app}"; Description: "Abrir o GSPHosp agora (baixa a imagem na primeira vez)"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Set-Location '{app}'; docker compose down"""; RunOnceId: "down"; Flags: runhidden

[Code]
var
  DockerOk: Boolean;

function DockerInstalado(): Boolean;
var
  Res: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C docker --version >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, Res) and (Res = 0);
end;

function InitializeSetup(): Boolean;
var
  Err: Integer;
begin
  Result := True;
  DockerOk := DockerInstalado();
  if not DockerOk then
  begin
    if MsgBox('O GSPHosp em Docker precisa do Docker Desktop, que não foi encontrado neste computador.' + #13#10#13#10 +
              'Deseja abrir a página de download do Docker Desktop agora?' + #13#10 +
              '(Instale-o, reinicie o computador e execute este instalador de novo.)' + #13#10#13#10 +
              'Clique em Não para continuar a instalação mesmo assim.', mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://docs.docker.com/desktop/setup/install/windows-install/', '', '', SW_SHOWNORMAL, ewNoWait, Err);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Env: String;
begin
  if CurStep = ssPostInstall then
  begin
    // .env lido pelo docker-compose.yml e pelo gsphosp-docker.ps1
    Env := 'GSPHOSP_VERSION={#AppVersion}' + #13#10 +
           'GSPHOSP_IMAGE={#Imagem}' + #13#10 +
           'GSPHOSP_PORT=5800' + #13#10 +
           'GSPHOSP_RES=1600x900' + #13#10 +
           'GSPHOSP_OUTPUT_DIR=' + ExpandConstant('{userdocs}\{#AppName}\output') + #13#10;
    SaveStringToFile(ExpandConstant('{app}\.env'), Env, False);
  end;
end;
