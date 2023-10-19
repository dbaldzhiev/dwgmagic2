=====TECTONICA=======

======DWGMAGIC=======
=====version 0.4=====
by Dimitar Baldzhiev
Overview:

This script is designed to automatically combine sheets exported from Autodesk Revit into a single DWG file. It is intended for situations where the practice requires delivering all sheets of a project in modelspace, despite it being an obsolete workflow. The script takes exported sheets from Revit, arranges them in modelspace, XREFs all the DWGs, binds them, and cleans up the resulting file.

Procedure:

Export sheets from Revit with the "Export views on sheets and links as external references" option enabled, and select the "Automatic-Short" naming convention. The sheets should be numbered 1, 2, 3, 4, etc. and saved to an empty directory.

Execute the script with the command "python dwgmagic.py TARGETDIR" where "TARGETDIR" is the path to the directory where the exported sheets are located.

The script generates several .SCR files that are automatically executed with accoreconsole.exe, a command-line version of AutoCAD.

The script loads the tectonica.dll, which handles renaming the XREFs, reordering multiviewport sheets, and arranging the sheets neatly. Make sure to set up the tectonica.dll properly.

Once the script is complete, the target folder will contain the following:

/derevitized: All the DWG files as modified by the script.
/originals: Unchanged original exported DWG files.
/scripts: The .SCR files that were generated and executed by accoreconsole.
acclog.log: A log file that may or may not contain everything.
MasterXref.dwg: A DWG file with all the sheets XREFed into it.
MasterMerged.dwg: A DWG file with all the sheets bound and exploded "smartly".
Contributions:

This script was written by the original author and may have some bugs. The author welcomes contributions and suggestions for improvement. If you are interested in using the script or contributing code, please express your interest and contribute to the project.
