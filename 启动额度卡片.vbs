Option Explicit
Dim sh, fso, folder
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "wscript.exe " & Chr(34) & folder & "\launch.vbs" & Chr(34), 0, False
