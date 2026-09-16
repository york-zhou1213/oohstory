#define MyAppName "OOHStory"
#define MyAppPublisher "OOHStory"
#define MyAppURL "https://oohstory.com"
#define MyAppExeName "oohstory.exe"
#define MyAppVersion GetEnv("OOHSTORY_VERSION")
#define MyOutputDir GetEnv("OOHSTORY_OUTPUT_DIR")
#define MyBuildDir SourcePath + "\..\..\build\windows\x64\runner\Release"

[Setup]
AppId={{C21E6A62-2C95-4776-A66F-E4F8F820CE61}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\OOHStory
DefaultGroupName=OOHStory
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#MyOutputDir}
OutputBaseFilename=OOHStory-v{#MyAppVersion}-Windows-x64-Setup-unsigned
SetupIconFile={#SourcePath}\..\..\windows\runner\resources\app_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OOHStory"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\OOHStory"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Registry]
Root: HKA; Subkey: "Software\Classes\Applications\oohstory.exe\SupportedTypes"; ValueType: string; ValueName: ".mobi"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\oohstory.exe\SupportedTypes"; ValueType: string; ValueName: ".azw"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\oohstory.exe\SupportedTypes"; ValueType: string; ValueName: ".azw3"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\oohstory.exe\SupportedTypes"; ValueType: string; ValueName: ".cbr"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\oohstory.exe\SupportedTypes"; ValueType: string; ValueName: ".cbt"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\oohstory.exe\SupportedTypes"; ValueType: string; ValueName: ".cb7"; ValueData: ""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch OOHStory"; Flags: nowait postinstall skipifsilent
