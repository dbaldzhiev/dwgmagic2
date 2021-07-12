import logging
import os
import re
import subprocess as sp
import sys
from shutil import copy

import click
import joblib as jb

import config as cfg


def getDir(path):
    list = os.listdir(path)
    dwglist = []
    for l in list:
        if l.endswith(".dwg"):
            dwglist.append(l)
    if len(dwglist) < 1:
        sys.exit('THERE ARE NO FILES')
    return dwglist


def tidy():
    path = os.getcwd()
    fns = getDir(os.getcwd())
    if not os.path.exists(str(path + "/scripts")):
        try:
            os.mkdir("scripts")
        except:
            print("Scripts folder already exists")

    if not os.path.exists(str(path + "originals")):
        try:
            os.mkdir("originals")
        except:
            print("Originals folder already exists")

    if not os.path.exists(str(path + "derevitized")):
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


class Project:
    def filenameParser(self):
        out = [fname for fname in self.filenames if re.compile("^\d+\.dwg").match(fname) is not None]
        return out

    def PScript(self, xexpld):
        scr = open("./scripts/DWGMAGIC.scr", "w+")
        scr.write("INSUNITS 5\n")
        for sheet in self.sheetNamesList:
            scr.write("xref\n")
            scr.write("attach\n")
            scr.write("{p}/derevitized/{s}_xrefed.dwg\n".format(p=os.getcwd(), s=sheet[:-4]))

            scr.write("0,0,0\n")
            scr.write("\n")  # x scalefactor
            scr.write("\n")  # y scalefactor
            scr.write("\n")  # rotation
        scr.write("netload {0}/tectonica.dll\n".format(cfg.paths["dmm"]))
        scr.write("tecarxref\n")
        scr.write("zoom e\n")
        scr.write("lwdisplay on\n")
        scr.write("save {p}/MASTERXREFED.dwg\n".format(p=os.getcwd()))
        scr.write("visretain 0\n")
        scr.write("xbind d *\n")
        scr.write("xbind s *\n")
        scr.write("xbind lt *\n")
        if xexpld:
            scr.write("bxt\n")
        if not xexpld:
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
        scr.write("save {0}/MASTERMERGED.dwg\n".format(os.getcwd()))
        scr.close()

    def MMMScript(self, xexpld):
        scr = open("./scripts/MMM.scr", "w+")
        scr.write("netload {0}/tectonica.dll\n".format(cfg.paths["dmm"]))
        scr.write("visretain 0\n")
        scr.write("xbind d *\n")
        scr.write("xbind s *\n")
        scr.write("xbind lt *\n")
        if xexpld:
            scr.write("bxt\n")
        if not xexpld:
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
        scr.write("save {0}/MANUALMASTERMERGED.dwg\n".format(os.getcwd()))
        scr.close()

    def MMMBAT(self):
        scr = open("./MANUALMERGE.bat", "w+")
        scr.write("pushd %~d1%~p1\n")
        scr.write("accoreconsole /i %cd%/MASTERXREFED.dwg /s %cd%/scripts/MMM.scr\n")
        scr.write("popd\n")
        scr.write("pause\n")

    def runPScript(self):
        print("STARTING DWGMAGIC: ")
        command = "{acc} /s {path}/scripts/DWGMAGIC.scr".format(acc=cfg.paths["acc"], path=os.getcwd())

        print("RUNNING: " + command)
        if cfg.verbose:
            print(command)
        process = sp.Popen(command, stdout=sp.PIPE)
        output, err = process.communicate()
        logging.debug(output.decode("utf-16"))

    def __init__(self):
        scr = open("./scripts/CHECKER.scr", "w+")
        scr.write("exit\n")
        scr.close()
        self.filenames = getDir("{0}/derevitized/".format(os.getcwd()))
        snl = self.filenameParser()
        snlindx = list(map(int, (list(map(lambda x: x[:-4], snl)))))
        self.sheetNamesList = [x for y, x in sorted(zip(snlindx, snl))]
        self.xrefXploder = click.confirm('Do you want to explode the Xrefs in Views?', default=True)
        self.sheets = jb.Parallel(n_jobs=jb.cpu_count())(
            jb.delayed(Sheet)(s, self.filenames) for s in self.sheetNamesList)
        self.PScript(self.xrefXploder)
        self.MMMScript(self.xrefXploder)
        self.MMMBAT()
        self.runPScript()
        logging.debug("COMPLETE")
        print("DWG MAGIC COMPLETE")


class Sheet:
    def viewsOnSheetGetter(self, ind, fns):
        floatRegex = re.compile(str(ind) + "-View-\d+")
        rawViewList = list(filter(floatRegex.match, fns))
        return rawViewList

    def SScript(self):
        scrMaster = open("./scripts/{0}".format(self.sheetCleanerScript), "w+")
        scrMaster.write("netload {0}/tectonica.dll\n".format(cfg.paths["dmm"]))
        scrMaster.write("tecrnxref\n")
        scrMaster.write("tecfixms\n")
        scrMaster.write("layout set Layout1\n")
        scrMaster.write("zoom all\n")
        scrMaster.write("chspace all\n")
        scrMaster.write("\n")
        scrMaster.write("\n")
        scrMaster.write("(command)\n")
        scrMaster.write("model\n")
        scrMaster.write("xref t * r\n")
        scrMaster.write("zoom all\n")
        scrMaster.write("save ./derevitized/{0}_xrefed.dwg\n".format(self.sheetName))
        scrMaster.close()

    def runSheetCleaner(self):
        command = "{acc} /i {path}/derevitized/{sheet}.dwg /s {path}/scripts/{script}".format(acc=cfg.paths["acc"],
                                                                                              path=os.getcwd(),
                                                                                              sheet=self.sheetName,
                                                                                              script=self.sheetCleanerScript)
        # print(command)
        print(
            "CLEANING SHEET {sheet} with SCRIPT {script}".format(sheet=self.sheetName, script=self.sheetCleanerScript))
        process = sp.Popen(command, stdout=sp.PIPE)
        output, err = process.communicate()
        if cfg.verbose:
            print(output.decode("utf-16"))
        logging.debug(output.decode("utf-16"))

    def __init__(self, sn, fns):
        self.sheetIndx = re.compile("\d+").search(sn)[0]
        self.sheetName = sn[:-4]
        self.sheetCleanerScript = "SHEET_{0}_cln.scr".format(self.sheetName)
        self.viewNamesOnSheetList = self.viewsOnSheetGetter(self.sheetIndx, fns)
        self.viewsOnSheet = [View(v) for v in self.viewNamesOnSheetList]
        self.SScript()
        self.runSheetCleaner()


class View:
    def VScriptOLD(self):
        scrSlave = open("./scripts/{0}".format(self.viewCleanerScript), "w+")
        scrSlave.write("(command)\n")
        scrSlave.write("model\n")
        scrSlave.write("xref t * r\n")
        # scrSlave.write("netload {0}/tectonica.dll\n".format(cfg.paths["dmm"]))
        # scrSlave.write("bxt\n")
        scrSlave.write("zoom all\n")
        scrSlave.write("qsave\n")
        scrSlave.close()

    def VScript(self):
        scrSlave = open("./scripts/{0}".format(self.viewCleanerScript), "w+")
        scrSlave.write("(command)\n")
        scrSlave.write("model\n")
        scrSlave.write("xref t * r\n")
        scrSlave.write("zoom all\n")
        scrSlave.write("qsave\n")
        scrSlave.close()

    def runViewCleaner(self):
        command = "{acc} /i {path}/derevitized/{view}.dwg /s {path}/scripts/{script}".format(acc=cfg.paths["acc"],
                                                                                             path=os.getcwd(),
                                                                                             view=self.viewName,
                                                                                             script=self.viewCleanerScript)
        # print(command)
        print("CLEANING VIEW {view} with SCRIPT {script}".format(view=self.viewName, script=self.viewCleanerScript))
        process = sp.Popen(command, stdout=sp.PIPE)
        output, err = process.communicate()
        if cfg.verbose:
            print(output.decode("utf-16"))
        logging.debug(output.decode("utf-16"))

    def getXfromV(self, view):
        command = "accoreconsole /s {p}/scripts/CHECKER.scr /i {p}\derevitized\{v}".format(p=os.getcwd(), v=view)
        process = sp.Popen(command, stdout=sp.PIPE)
        cmdoutput, err = process.communicate()
        cmdoutput = cmdoutput.decode("utf-16")
        xrefsRegex = re.compile("\"(.*)\" loaded: (.*)")
        xrefsList = xrefsRegex.findall(cmdoutput)
        output = [x[0] for x in xrefsList]
        if cfg.verbose:
            print("VIEW NAMED: {v} has the following XREFS:{x}".format(v=view, x=output))
        return output

    def __init__(self, vn):
        self.viewName = vn[:-4]
        self.viewIndx = str(re.compile("\d+-View-(\d+).dwg").search(vn).group(1))
        self.parentSheetIndx = str(re.compile("(\d+)-View-\d+.dwg").search(vn).group(1))
        self.viewPath = ""
        self.viewCleanerScript = "VIEW_{0}-{1}.scr".format(self.parentSheetIndx, self.viewIndx)
        self.xrefs = self.getXfromV(self.viewName)
        self.VScript()
        self.runViewCleaner()


class Xref:
    def __init__(self):
        self.xrefName = ""
        self.xrefPath = ""
