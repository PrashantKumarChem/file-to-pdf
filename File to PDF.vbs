' Starts the File to PDF window from source, with no console window.
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
here = files.GetParentFolderName(WScript.ScriptFullName)

' The environment "uv sync" creates in the repository folder.
pythonw = here & "\.venv\Scripts\pythonw.exe"
If Not files.FileExists(pythonw) Then
    MsgBox "There is no Python environment in " & here & "\.venv." & vbCrLf & vbCrLf & _
        "Run ""uv sync"" in that folder, then start File to PDF again.", vbExclamation, "File to PDF"
    WScript.Quit 1
End If

' Run from the repository folder so the topdf package is importable.
shell.CurrentDirectory = here
shell.Run """" & pythonw & """ -m topdf.gui", 0, False
