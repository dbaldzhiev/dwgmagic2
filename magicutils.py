import logging
import os
import re
import shlex
import subprocess as sp
import sys
import time
from shutil import copy

import joblib as jb
from colorama import Back

import config as cfg


# Getting dwg files in a path
def getDir(path):
    output = [l for l in os.listdir(path) if l.endswith(".dwg")]
    if len(output) < 1:
        sys.exit('THERE ARE NO FILES')
    return output


# ordering the folder so it has the folders scripts, originals and derevitized and copying the dwgs in the proper places
def preprocess():
    path = os.getcwd()
    fns = getDir(os.getcwd())
    if not os.path.exists(str(path + "/scripts")):
        try:
            os.mkdir("scripts")
        except:
            print("Scripts folder already exists")

    if not os.path.exists(str(path + "/originals")):
        try:
            os.mkdir("originals")
        except:
            print("Originals folder already exists")

    if not os.path.exists(str(path + "/derevitized")):
        try:
            os.mkdir("derevitized")
        except:
            print("Derevitized folder already exists")

    for fn in fns:
        # print("COPYING " + fn)
        copy(path + "/" + fn, path + "/originals/" + fn)
        copy(path + "/" + fn, path + "/derevitized/" + fn)
        os.remove(path + "/" + fn)
    print("TIDY COMPLETE")
    logging.debug("TIDY COMPLETE")


#The general PROJECT CLASS
class Project:
    #The scrtipt that attaches all xrefs and explodes them saves MASTERMERGED.DWG
    def PScript(self):
        scr = open("./scripts/DWGMAGIC.scr", "w+")
        scr.write("INSUNITS 5\n")
        for sheet in self.sheetNamesList:
            scr.write("xref\n")
            scr.write("attach\n")
            scr.write("\"{p}/derevitized/{s}_xrefed.dwg\"\n".format(p=os.getcwd(), s=sheet[:-4]))
            scr.write("0,0,0\n")
            scr.write("\n")  # x scalefactor
            scr.write("\n")  # y scalefactor
            scr.write("\n")  # rotation
        scr.write("xref t * r\n")
        scr.write("netload {0}/tectonica.dll\n".format(cfg.paths["dmm"]))
        scr.write("tecarxref\n")
        scr.write("zoom e\n")
        scr.write("lwdisplay on\n")
        scr.write("filedia 0\n")
        scr.write("saveas\n")
        scr.write("2007\n")
        scr.write("\"{0}\{1}_MXR.dwg\"\n".format(os.getcwd(), os.path.basename(os.getcwd())))
        scr.write("visretain 0\n")
        scr.write("xbind d *\n")
        scr.write("xbind s *\n")
        scr.write("xbind lt *\n")
        if self.xrefXplodeToggle:
            scr.write("tecbxt\n")
        if not self.xrefXplodeToggle:
            scr.write("bindtype 1\n")
            scr.write("xref bind *\n")
            scr.write("xplode all\n")
            scr.write("\n")
            scr.write("g\n")
            scr.write("\n")
            scr.write("(load(findfile \"ssx.lsp\"))\n")
            for sheet in self.sheets:
                for view in sheet.viewsOnSheet:
                    scr.write("ssx\n")
                    scr.write("\n")
                    scr.write("block\n")
                    scr.write("{0}-View-{1}\n".format(str(sheet.sheetName), view.viewIndx))
                    scr.write("\n")
                    scr.write("xplode p\n")
                    scr.write("\n")
                    scr.write("\n")
        scr.write("-purge all * n\n")
        scr.write("audit y\n")
        scr.write("zoom all\n")
        scr.write("saveas\n")
        scr.write("2007\n")
        scr.write("\"{0}\{1}_MM.dwg\"\n".format(os.getcwd(), os.path.basename(os.getcwd())))
        scr.write("filedia 1\n")
        scr.write("qsave\n")
        scr.close()

    #Making MANUAL master merge script
    def MMMScript(self):
        scr = open("./scripts/MMM.scr", "w+")
        scr.write("netload {0}/tectonica.dll\n".format(cfg.paths["dmm"]))
        scr.write("visretain 0\n")
        scr.write("xbind d *\n")
        scr.write("xbind s *\n")
        scr.write("xbind lt *\n")
        if self.xrefXplodeToggle:
            scr.write("tecbxt\n")
        if not self.xrefXplodeToggle:
            scr.write("bindtype 1\n")
            scr.write("xref bind *\n")
            scr.write("xplode all\n")
            scr.write("\n")
            scr.write("g\n")
            scr.write("\n")
            scr.write("(load(findfile \"ssx.lsp\"))\n")
            for sheet in self.sheets:
                for view in sheet.viewsOnSheet:
                    scr.write("ssx\n")
                    scr.write("\n")
                    scr.write("block\n")
                    scr.write("{0}-View-{1}\n".format(str(sheet.sheetName), view.viewIndx))
                    scr.write("\n")
                    scr.write("xplode p\n")
                    scr.write("\n")
                    scr.write("\n")
        scr.write("-purge all * n\n")
        scr.write("audit y\n")
        scr.write("zoom all\n")
        scr.write("filedia 0\n")
        scr.write("saveas\n")
        scr.write("2007\n")
        scr.write("\"{0}\{1}_MMM.dwg\"\n".format(os.getcwd(), os.path.basename(os.getcwd())))
        scr.write("filedia 1\n")
        scr.write("qsave\n")
        scr.close()

    #Making the bat file that uses manual master merge script
    def MMMBAT(self):
        scr = open("./MANUALMERGE.bat", "w+")
        scr.write("pushd %~d1%~p1\n")
        scr.write("\"{acc}\" /i \"%cd%/MASTERXREFED.dwg\" /s \"%cd%/scripts/MMM.scr\"\n".format(acc=cfg.paths["acc"]))
        scr.write("popd\n")
        scr.write("pause\n")

    #Running the master merge script
    def runPScript(self):
        print("STARTING DWGMAGIC: ")
        command = "\"{acc}\" /s \"{path}/scripts/DWGMAGIC.scr\"".format(acc=cfg.paths["acc"], path=os.getcwd())
        print("RUNNING: " + command)
        logging.debug("RUNNING: {}".format(command))
        process = sp.Popen(shlex.split(command), stdout=sp.PIPE, shell=True, encoding='utf-16-le', errors='replace')
        while process.poll() is None:
            line = process.stdout.readline()
            if line != "":
                if line != "\n":
                    print(line.strip("\n"))
        output, err = process.communicate()
        logging.debug(output)
        try:
            os.remove("{0}\{1}_MM.bak".format(os.getcwd(), os.path.basename(os.getcwd())))

        except Exception as e:
            pass

    #checkin if all xrefs are done derevitizing and can be passed to master merge
    def cleanSheetsExistenceChecker(self):
        timeout = time.time() + 120
        while True:
            existance = list(zip(self.sheets, [os.path.isfile(s.cleanSheetFilePath) for s in self.sheets]))
            if all([ex for sh, ex in existance]) or time.time() > timeout:
                print("\n".join(["{0} is {1}".format(e[0].cleanSheetFilePath, e[1]) for e in existance]))
                break
            else:
                print("Time left: {0}".format(timeout - time.time()))
                print("\n".join([Back.GREEN + "{0} is {1}".format(e[0].cleanSheetFilePath, e[1]) if e[
                    1] else Back.RED + "{0} is {1}".format(e[0].cleanSheetFilePath, e[1]) for e in existance]))
                time.sleep(1)
                print(Back.RESET)
                os.system('cls')


    def __init__(self):
        self.filenames = os.listdir("{0}/derevitized/".format(os.getcwd()))
        # snl = [fname for fname in self.filenames if re.compile("^\d+\.dwg").match(fname) is not None]  # before 220314
        snl = [fname for fname in self.filenames if
               re.compile("^((?![-View-]|[-rvt-]).)+(\.dwg)").match(fname) is not None]
        # snlIndx = list(map(int, (list(map(lambda x: x[:-4], snl)))))  # befor 220314
        snlIndx = [s.replace(".dwg", "") for s in snl]
        self.sheetNamesList = [x for y, x in sorted(zip(snlIndx, snl))]
        # self.xrefXplodeToggle = click.confirm('Do you want to explode the Xrefs in Views?', default=True) #before 220314
        self.xrefXplodeToggle = True
        self.sheets = jb.Parallel(n_jobs=-1, batch_size=1, verbose=100)(
            jb.delayed(Sheet)(s, self) for s in self.sheetNamesList)
        self.PScript()
        self.MMMScript()
        self.MMMBAT()
        self.cleanSheetsExistenceChecker()
        self.runPScript()
        logging.debug("COMPLETE")
        print("DWG MAGIC COMPLETE")

class Sheet:

    def SScript(self):
        scr = open("./scripts/{0}".format(self.sheetCleanerScript), "w+")
        if len(self.viewsOnSheet) > 0:
            scr.write("netload {0}/tectonica.dll\n".format(cfg.paths["dmm"]))
            scr.write("tecrnxref\n")
            scr.write("tecfixms\n")
        scr.write("layout set Layout1\n")
        scr.write("zoom all\n")
        scr.write("chspace all\n")
        scr.write("\n")
        scr.write("\n")
        scr.write("(command)\n")
        scr.write("model\n")
        if len(self.viewsOnSheet) > 0:
            scr.write("xref t * r\n")
        scr.write("zoom all\n")
        scr.write("save ./derevitized/{0}_xrefed.dwg\n".format(self.sheetName))
        scr.write("exit\n")
        scr.close()

    def runSheetCleaner(self):
        command = "{acc} /i \"{path}/derevitized/{sheet}.dwg\" /s \"{path}/scripts/{script}\"".format(
            acc=cfg.paths["acc"],
            path=os.getcwd(),
            sheet=self.sheetName,
            script=self.sheetCleanerScript)
        if cfg.verbose:
            print("###############")
            print(
                "CLEANING SHEET {sheet} with SCRIPT {script}".format(sheet=self.sheetName,
                                                                     script=self.sheetCleanerScript))
            print(Back.GREEN + command)

        process = sp.Popen(command, stdout=sp.PIPE)

        output, err = process.communicate()
        os.remove("{0}/derevitized/{1}".format(os.getcwd(), self.workingFile))
        if cfg.verbose:
            print(output.decode("utf-16"))


    def __init__(self, sn, project):
        self.sheetName = sn.replace(".dwg", "")
        self.workingFile = sn
        self.sheetCleanerScript = "SHEET_{0}_cln.scr".format(self.sheetName)
        self.viewNamesOnSheetList = list(filter(re.compile(str(self.sheetName) + "-View-\d+").match, project.filenames))
        self.viewsOnSheet = jb.Parallel(n_jobs=-1, batch_size=1)(jb.delayed(View)(v) for v in self.viewNamesOnSheetList)
        self.SScript()
        self.runSheetCleaner()
        self.cleanSheetFilePath = "{0}/derevitized/{1}_xrefed.dwg".format(os.getcwd(), self.sheetName)

class View:

    def VScript(self):
        scr = open("./scripts/{0}".format(self.viewCleanerScript), "w+")
        scr.write("(command)\n")
        scr.write("model\n")
        scr.write("xref t * r\n")
        scr.write("zoom all\n")
        scr.write("qsave\n")
        scr.close()

    def runViewCleaner(self):
        command = "{acc} /i \"{path}/derevitized/{view}.dwg\" /s \"{path}/scripts/{script}\"".format(
            acc=cfg.paths["acc"],
            path=os.getcwd(),
            view=self.viewName,
            script=self.viewCleanerScript)
        if cfg.verbose:
            print("CLEANING VIEW {view} with SCRIPT {script}".format(view=self.viewName, script=self.viewCleanerScript))
        process = sp.Popen(command, stdout=sp.PIPE)
        output, err = process.communicate()
        if cfg.verbose:
            print(output.decode("utf-16"))

    def getXfromV(self):
        command = "{acc} /s {path}/scripts/CHECKER.scr /i {path}\derevitized\{view}.dwg".format(acc=cfg.paths["acc"],
                                                                                                path=os.getcwd(),
                                                                                                view=self.viewName)

        try:
            process = sp.Popen(command, stdout=sp.PIPE)
            cmdoutput, err = process.communicate()
            cmdoutput = cmdoutput.decode("utf-16")
            xrefsRegex = re.compile("\"(.*)\" loaded: (.*)")
            xrefsList = xrefsRegex.findall(cmdoutput)
            output = xrefsList
        except Exception as e:
            print(command)
            print(e)

        if cfg.verbose:
            print("VIEW NAMED: {v} has the following XREFS:{x}".format(v=self.viewName, x=output))
        return output

    def __init__(self, vn):
        self.viewName = vn[:-4]
        self.viewIndx = str(re.compile("\d+-View-(\d+).dwg").search(vn).group(1))
        self.parentSheetIndx = str(re.compile("(\d+)-View-\d+.dwg").search(vn).group(1))
        self.viewPath = ""
        self.viewCleanerScript = "VIEW_{0}-{1}.scr".format(self.parentSheetIndx, self.viewIndx)
        self.xrefs = [Xref(x[0], x[1]) for x in self.getXfromV()]
        self.VScript()
        self.runViewCleaner()


class Xref:
    def __init__(self, name, path):
        self.xrefName = name
        self.xrefPath = path
