Option Explicit
Dim sh, fso, folder, pythonw, part, candidate
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = folder & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    pythonw = ""
    For Each part In Split(sh.Environment("PROCESS")("PATH"), ";")
        part = Replace(part, Chr(34), "")
        If Len(part) > 0 Then
            candidate = fso.BuildPath(part, "pythonw.exe")
            If fso.FileExists(candidate) Then
                pythonw = candidate
                Exit For
            End If
        End If
    Next
End If
If Len(pythonw) = 0 Then
    MsgBox "Python was not found. Follow the setup steps in README.md first.", 48, "QuotaLeaf"
    WScript.Quit 1
End If
sh.CurrentDirectory = folder
sh.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & folder & "\quota_card.py" & Chr(34), 0, False
