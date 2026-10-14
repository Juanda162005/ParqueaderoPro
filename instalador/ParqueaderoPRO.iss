#define AppName "ParqueaderoPRO"
#define AppVersion "1.0.0"
#define AppPublisher "ParqueaderoPRO"
#define AppDir ".."

[Setup]
AppId={{B6D7D1F4-7BB5-4B54-9A10-8F2D6902C0AD}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\ParqueaderoPRO
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=.
OutputBaseFilename=ParqueaderoPRO-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".venv\*;.venv_lpr\*;.git\*;.env.local;backend\*.db;backend\backups\*"

[Icons]
Name: "{autoprograms}\ParqueaderoPRO"; Filename: "{app}\Abrir_ParqueaderoPRO.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\ParqueaderoPRO"; Filename: "{app}\Abrir_ParqueaderoPRO.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\ParqueaderoPRO - Entrada"; Filename: "{app}\LPR_Cliente\Iniciar_LPR_Entrada.bat"; WorkingDir: "{app}\LPR_Cliente"
Name: "{autoprograms}\ParqueaderoPRO - Salida"; Filename: "{app}\LPR_Cliente\Iniciar_LPR_Salida.bat"; WorkingDir: "{app}\LPR_Cliente"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\instalador\Preparar-ParqueaderoPRO.ps1"""; WorkingDir: "{app}"; Flags: waituntilterminated; StatusMsg: "Preparando Docker y el cliente OCR..."
