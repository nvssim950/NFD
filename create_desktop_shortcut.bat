@echo off
echo Updating NFD Desktop Shortcut with custom icon...
(
echo Set oWS = CreateObject("WScript.Shell"^)
echo Set oLink = oWS.CreateShortcut("%USERPROFILE%\Desktop\NFD.lnk"^)
echo oLink.TargetPath = "%~dp0NFD.bat"
echo oLink.WorkingDirectory = "%~dp0"
echo oLink.Description = "NFD - Nassim Fast Downloader"
echo oLink.IconLocation = "%~dp0NFD.ico,0"
echo oLink.Save
) > "%TEMP%\make_nfd_lnk.vbs"
cscript //nologo "%TEMP%\make_nfd_lnk.vbs"
del "%TEMP%\make_nfd_lnk.vbs" >nul 2>&1
echo Done! NFD shortcut updated with icon.
