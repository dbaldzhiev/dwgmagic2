=====TECTONICA=======

======DWGMAGIC=======
=====version 0.3=====
by Dimitar Baldzhiev


to be written in the future...
no documentation at this stage :)

some old jibberish:

The following scripts deal with automatically combining sheets exported form Autodesk Revit into single dwg.

For reasons that are not of importance my practice is required to deliver all the sheets of a project in modelspace. I am aware that it is a very obsolete workflow but this means spending 40 minutes to an hour and a half depending on the size of the project to CHSPACE the paperspace elements in model space, xref all the dwgs, arrange, bind and clean. This is the state of things and please do not explain that using BIM requires to do things differently, I know, it is not up to me.

Some people may be laughing but I am very serious. So serious that I have made a couple of scripts that automatically combines the files exported from revit into one single dwg.

I am writing this post in hope that other damned souls like me may want to use it and contribute. This is my first time writing c# code for autocad, there are bugs, but so far it works well.

Few words about the procedure:

• From Revit sheets are exported with “Export views on sheets and links as external references” switched on and Naming: “Automatic-Short”. My sheets are numbered 1,2,3,4,… and the code works for it. If you need support of different naming convention the regex in the python script have to be edited. (Would be happy if somebody suggests more elegant solution). The files have to be exported to an empty directory

• The script is executed with “python dwgmagic.py TARGETDIR”, example “python dwgmagic.py C:\Users\User\Desktop\my_project”

• The script generates several .scr files that are automatically executed with accoreconsole.exe over the exported dwgs

• During the execution tectonica.dll is netloaded – make sure you set it up properly TECTONICA.dll handles renaming the xrefs, reordering multiviewport sheets and arranging the sheets neatly.

• When the script is complete the target folder would contain: /derevitized – all the dwgs as changed by the scripts /originals – unchanged original exported dwgs /scripts – the .scr files that are generated and executed by accoreconsole acclog.log – log that may or may not contain everything MasterXref.dwg – dwg file with all the sheets xref-ed into it MasterMerged.dwg – dwg file with all the sheets bound and exploded “smartly”

I would be happy if anybody shows interest in this and contributes with code.
