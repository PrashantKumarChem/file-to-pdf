' Starts the File to PDF window from source, with no console window.
Set shell = CreateObject("WScript.Shell")

' The Python environment that has requirements.txt installed; change this
' line if yours lives elsewhere.
pythonw = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\Miniconda3\pythonw.exe"

' Run from the repository folder so the topdf package is importable.
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & pythonw & """ -m topdf.gui", 0, False
