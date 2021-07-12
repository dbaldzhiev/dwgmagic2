import os
import shutil
def removePrevPP(path):
    list = os.listdir(path)
    if "originals" in list:
        try:
            os.remove(path+"/acclog.log")
        except:
            pass
        try:
            os.remove(path+"/manualmerge.bat")
        except:
            pass
        try:
            os.remove(path+"/MasterMerged.dwg")
        except:
            pass
        try:
            os.remove(path+"/MasterXref.dwg")
        except:
            pass

        try:
            shutil.rmtree(path+"/scripts")
        except:
            pass
        try:
            shutil.rmtree(path+"/derevitized")
        except:
            pass
        goodsList = os.listdir(path+"/originals")
        for file in goodsList:
            shutil.copy(path+"/originals/"+file,path+"/"+file)
        try:
            shutil.rmtree(path+"/originals")
        except:
            pass

