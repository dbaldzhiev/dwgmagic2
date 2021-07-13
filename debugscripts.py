import os
import shutil
def removePrevPP(path):
    if os.path.exists(str(path + "/originals")):
        file_used_error = 0
        if os.path.exists(str(path + "/acclog.log")):
            try:
                os.remove(path + "/acclog.log")
            except:
                file_used_error = 1
                pass
        if os.path.exists(str(path + "/MANUALMERGE.bat")):
            try:
                os.remove(path + "/MANUALMERGE.bat")
            except:
                file_used_error = 1
                pass
        if os.path.exists(str(path + "/MASTERMERGED.dwg")):
            try:
                os.remove(path + "/MASTERMERGED.dwg")
            except:
                file_used_error = 1
                pass
        if os.path.exists(str(path + "/MASTERXREFED.dwg")):
            try:
                os.remove(path + "/MASTERXREFED.dwg")
            except:
                file_used_error = 1
                pass
        if os.path.exists(str(path + "/scripts")):
            try:
                shutil.rmtree(path + "/scripts")
            except:
                file_used_error = 1
                pass
        if os.path.exists(str(path + "/derevitized")):
            try:
                shutil.rmtree(path + "/derevitized")
            except:
                file_used_error = 1
                pass

        if not file_used_error:
            goodsList = os.listdir("{0}/originals".format(path))
            for file in goodsList:
                shutil.copy("{p}/originals/{f}".format(p=path, f=file), "{p}/{f}".format(p=path, f=file))
            try:
                shutil.rmtree("{0}/originals".format(path))
            except:
                pass
