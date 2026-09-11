Set shell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
pythonw = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\Miniconda3\pythonw.exe"
shell.Run """" & pythonw & """ """ & scriptDir & "\notebook_to_pdf_gui.py""", 0, False
