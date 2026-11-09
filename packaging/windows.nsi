; NSIS script: turns build\win\Veuly (made by packaging/build_windows.py, which calls this) into dist\Veuly-Setup.exe.
; Installs for the current user only: no administrator rights, no UAC question.
; Texts are in English, Spanish and Catalan; the installer asks which.
Unicode true
!include "MUI2.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef SIZE_KB
  !define SIZE_KB 70000
!endif
!define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\Veuly"

Name "Veuly"
OutFile "..\dist\Veuly-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\Veuly"
InstallDirRegKey HKCU "${UNINST}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ManifestDPIAware true
BrandingText "Veuly ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "Veuly"
VIAddVersionKey "FileDescription" "Veuly Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" ""

!define MUI_ICON "veuly.ico"
!define MUI_UNICON "veuly.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "$(WelcomeText)"
!define MUI_FINISHPAGE_TEXT "$(FinishText)"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "$(RunText)"
!define MUI_FINISHPAGE_RUN_FUNCTION StartApp

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Spanish"
!insertmacro MUI_LANGUAGE "Catalan"

LangString WelcomeText ${LANG_ENGLISH} "Veuly records a class or a meeting, transcribes it on this computer and writes the notes.$\r$\n$\r$\nThis installs the app for your user only. Nothing else is needed: Python and the transcriber come inside.$\r$\n$\r$\nThe first time Veuly opens it shows a Setup page in your browser and downloads the transcription model (about 570 MB)."
LangString WelcomeText ${LANG_SPANISH} "Veuly graba una clase o una reunión, la transcribe en este ordenador y escribe los apuntes.$\r$\n$\r$\nSe instala solo para tu usuario. No hace falta nada más: Python y el transcriptor vienen dentro.$\r$\n$\r$\nLa primera vez que se abre, Veuly muestra una página de Configuración en el navegador y descarga el modelo de transcripción (unos 570 MB)."
LangString WelcomeText ${LANG_CATALAN} "Veuly enregistra una classe o una reunió, la transcriu en aquest ordinador i escriu els apunts.$\r$\n$\r$\nS'instal·la només per al teu usuari. No cal res més: Python i el transcriptor venen a dins.$\r$\n$\r$\nLa primera vegada que s'obre, Veuly mostra una pàgina de Configuració al navegador i baixa el model de transcripció (uns 570 MB)."
LangString FinishText ${LANG_ENGLISH} "Veuly is installed.$\r$\n$\r$\nIt has no window of its own: it opens in your browser. The Setup page comes first. It downloads the transcription model by itself (about 570 MB, a few minutes) and asks who writes the notes: your Claude subscription or an API key.$\r$\n$\r$\nTo close Veuly use Quit inside the app."
LangString FinishText ${LANG_SPANISH} "Veuly está instalado.$\r$\n$\r$\nNo tiene ventana propia: se abre en el navegador. Primero sale la página de Configuración. Descarga sola el modelo de transcripción (unos 570 MB, unos minutos) y pregunta quién escribe los apuntes: tu suscripción de Claude o una clave de API.$\r$\n$\r$\nPara cerrar Veuly usa Salir dentro de la app."
LangString FinishText ${LANG_CATALAN} "Veuly està instal·lat.$\r$\n$\r$\nNo té finestra pròpia: s'obre al navegador. Primer surt la pàgina de Configuració. Baixa sola el model de transcripció (uns 570 MB, uns minuts) i pregunta qui escriu els apunts: la teva subscripció de Claude o una clau d'API.$\r$\n$\r$\nPer tancar Veuly fes servir Surt dins de l'app."
LangString RunText ${LANG_ENGLISH} "Open Veuly now"
LangString RunText ${LANG_SPANISH} "Abrir Veuly ahora"
LangString RunText ${LANG_CATALAN} "Obre Veuly ara"
LangString KeepText ${LANG_ENGLISH} "Your notes, settings and the transcription model are kept in $APPDATA\Veuly. Delete that folder yourself if you want them gone."
LangString KeepText ${LANG_SPANISH} "Tus apuntes, los ajustes y el modelo de transcripción se conservan en $APPDATA\Veuly. Borra esa carpeta si quieres eliminarlos."
LangString KeepText ${LANG_CATALAN} "Els teus apunts, la configuració i el model de transcripció es conserven a $APPDATA\Veuly. Esborra aquesta carpeta si els vols eliminar."

Function .onInit
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Function un.onInit
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

Function StartApp
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\python\Veuly.exe" "$INSTDIR\app.py"'
FunctionEnd

; The app's own folders, and nothing else of the folder the user chose.
!macro RemoveAppFiles
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\transcapp"
  RMDir /r "$INSTDIR\web"
  RMDir /r "$INSTDIR\bin"
  Delete "$INSTDIR\app.py"
  Delete "$INSTDIR\bundle.json"
  Delete "$INSTDIR\veuly.ico"
  Delete "$INSTDIR\Veuly.bat"
  Delete "$INSTDIR\Veuly-debug.bat"
  Delete "$INSTDIR\THIRD-PARTY.txt"
!macroend

Section "Veuly" SecApp
  SectionIn RO
  ; A copy that is running holds its files open: stop it before replacing them (an update over an old version).
  nsExec::Exec 'taskkill /F /IM Veuly.exe'
  Pop $0
  Sleep 500
  !insertmacro RemoveAppFiles
  SetOutPath "$INSTDIR"
  File /r "..\build\win\Veuly\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\Veuly.lnk" "$INSTDIR\python\Veuly.exe" '"$INSTDIR\app.py"' "$INSTDIR\veuly.ico"
  CreateShortcut "$DESKTOP\Veuly.lnk" "$INSTDIR\python\Veuly.exe" '"$INSTDIR\app.py"' "$INSTDIR\veuly.ico"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "Veuly"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\veuly.ico"
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST}" "EstimatedSize" ${SIZE_KB}
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /F /IM Veuly.exe'
  Pop $0
  Sleep 500
  !insertmacro RemoveAppFiles
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\Veuly.lnk"
  Delete "$DESKTOP\Veuly.lnk"
  DeleteRegKey HKCU "${UNINST}"
  IfSilent +2
  MessageBox MB_OK|MB_ICONINFORMATION "$(KeepText)"
SectionEnd
