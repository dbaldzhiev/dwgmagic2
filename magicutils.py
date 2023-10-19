import os
import re
import shlex
import subprocess as sp
import sys
import time
from shutil import copy
from threading import Timer

import joblib as jb
from colorama import Back

import config as cfg


def get_dwg_files_in_directory(path):
    output = [file for file in os.listdir(path) if file.endswith(".dwg")]
    if len(output) < 1:
        sys.exit('THERE ARE NO FILES')
    return output

# ordering the folder so it has the folders scripts, originals and derevitized and copying the dwgs in the proper places
def preprocess():
    path = os.getcwd()
    fns = get_dwg_files_in_directory(os.getcwd())
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


#The general PROJECT CLASS
class Project:
    #The scrtipt that attaches all xrefs and explodes them saves MASTERMERGED.DWG
    def generate_Project_Script(self):
        scr = open("./scripts/DWGMAGIC.scr", "w+")
        scr.write("INSUNITS 5\n")
        for sheet in self.sheetNamesList:
            scr.write("xref\n")
            scr.write("attach\n")
            scr.write("\"./derevitized/{s}_xrefed.dwg\"\n".format(p=os.getcwd(), s=sheet[:-4]))
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
        scr.write("\"./{0}_MXR.dwg\"\n".format(os.path.basename(os.getcwd())))
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
        scr.write("\"./{0}_MM.dwg\"\n".format(os.path.basename(os.getcwd())))
        scr.write("filedia 1\n")
        scr.write("qsave\n")
        scr.close()

    #Making MANUAL master merge script
    def generate_Manual_Master_Merge_Script(self):
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
        scr.write("saveas\n")
        scr.write("2007\n")
        scr.write("\"./{0}_MMM.dwg\"\n".format(os.path.basename(os.getcwd())))
        scr.write("filedia 1\n")
        scr.write("qsave\n")
        scr.close()

    #Making the bat file that uses manual master merge script
    def generate_Manual_Master_Merge_bat(self):
        scr = open("./MANUALMERGE.bat", "w+")
        scr.write("pushd %~d1%~p1\n")
        scr.write("\"{acc}\" /i \"%cd%/{n}_MXR.dwg\" /s \"%cd%/scripts/MMM.scr\"\n".format(acc=self.accpath,
                                                                                           n=os.path.basename(
                                                                                               os.getcwd())))
        scr.write("popd\n")
        scr.write("pause\n")

    #Running the master merge script

    def run_Project_script(self):
        command = "\"{acc}\" /s \"{path}/scripts/DWGMAGIC.scr\"".format(acc=self.accpath, path=os.getcwd())
        print("======================================")
        print("+++++ RUNNING: {} ++++++".format(command))
        print("======================================")
        process = sp.Popen(shlex.split(command), stdout=sp.PIPE, shell=True, encoding='utf-16-le', errors='replace')
        lines = []
        maxl = 10
        writtenlines = 0
        aa = []
        bb = []
        while process.poll() is None:
            line = process.stdout.readline()
            if line != "":
                if line != "\n":
                    lines.append(line[:100] + ".." if len(line) > 100 else line.strip("\n"))
                    if len(lines) > maxl:
                        if writtenlines != 0:
                            code = f"\033[{writtenlines}A"
                            sys.stdout.write(code)
                            sys.stdout.write("\033[J")
                        ll = lines[-maxl:]
                        aa.append(ll)
                        writtenlines = len(lines[-maxl:])
                        bb.append(writtenlines)
                        print(*ll, sep="\n")
                    if cfg.vverbose:
                        print(line.strip("\n"))
        output, err = process.communicate()
        if cfg.vverbose:
            print(output)
        try:
            os.remove("{0}_MM.bak".format(os.path.basename(os.getcwd())))
        except Exception as e:
            pass
        print("DWG MAGIC COMPLETE")

    #checkin if all xrefs are done derevitizing and can be passed to master merge
    def cleanSheetsExistenceChecker(self):
        timeout = time.time() + cfg.deadline
        while True:
            existance = list(zip(self.sheets, [os.path.isfile(s.cleanSheetFilePath) for s in self.sheets]))
            if all([ex for sh, ex in existance]) or time.time() > timeout:
                if cfg.verbose:
                    print("\n".join(["{0} is {1}".format(e[0].cleanSheetFilePath, e[1]) for e in existance]))
                break
            else:
                if cfg.verbose:
                    print("Time left: {0}".format(timeout - time.time()))
                    print("\n".join([Back.GREEN + "{0} is {1}".format(e[0].cleanSheetFilePath, e[1]) if e[
                        1] else Back.RED + "{0} is {1}".format(e[0].cleanSheetFilePath, e[1]) for e in existance]))
                    time.sleep(1)
                    print(Back.RESET)
                    os.system('cls')
                pass

    def accVersion(self):
        for key in cfg.accpathv:
            if os.path.exists(cfg.accpathv[key]):
                return cfg.accpathv[key]

    def __init__(self): 
        os.system("")
        self.filenames = os.listdir("{0}/derevitized/".format(os.getcwd()))
        self.accpath = self.accVersion()
        print(self.accpath)
        rgx_str = "(?!(?:.*-View-\d*)|(?:.*-rvt-))(^.*)(?:\.dwg$)"
        snl = [fname for fname in self.filenames if re.compile(rgx_str).match(fname) is not None]
        snlIndx = [s.replace(".dwg", "") for s in snl]
        self.sheetNamesList = [x for y, x in sorted(zip(snlIndx, snl))]
        self.xrefXplodeToggle = True
        if cfg.threaded:
            self.sheets = jb.Parallel(n_jobs=-1, batch_size=1, verbose=100)(
                jb.delayed(Sheet)(s, self) for s in self.sheetNamesList)
        else:
            self.sheets = [Sheet(s, self) for s in self.sheetNamesList]
        self.generate_Project_Script()
        self.generate_Manual_Master_Merge_Script()
        self.generate_Manual_Master_Merge_bat()
        self.cleanSheetsExistenceChecker()
        print("======================================")
        timeout = 3
        t = Timer(timeout, self.run_Project_script)
        t.start()

        prompt = "PRESS ENTER TO stop the automerge.\n"
        answer = input(prompt)
        if answer is not None:
            t.cancel()


class Sheet:

    def generate_Sheet_script(self):
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

    def run_Sheet_cleaner(self):
        command = "{acc} /i \"{path}/derevitized/{sheet}.dwg\" /s \"{path}/scripts/{script}\"".format(
            acc=self.acc,
            path=os.getcwd(),
            sheet=self.sheetName,
            script=self.sheetCleanerScript)
        if cfg.verbose:
            print(Back.GREEN)
            print("###############")
            print(
                "CLEANING SHEET {sheet} with SCRIPT {script}".format(sheet=self.sheetName,
                                                                     script=self.sheetCleanerScript))
            print(command)
            print(Back.RESET)

        process = sp.Popen(command, stdout=sp.PIPE)

        output, err = process.communicate()
        os.remove("{0}/derevitized/{1}".format(os.getcwd(), self.workingFile))
        if cfg.vverbose:
            print(output.decode("utf-16"))


    def __init__(self, sn, project):
        self.acc = project.accpath
        self.sheetName = sn.replace(".dwg", "")
        self.workingFile = sn
        self.sheetCleanerScript = "{0}_SHEET.scr".format(self.sheetName.upper())
        self.viewNamesOnSheetList = list(filter(re.compile(str(self.sheetName) + "-View-\d+").match, project.filenames))
        print("SHEET {sheetname} ->> {views}".format(sheetname=sn, views=self.viewNamesOnSheetList))
        if cfg.threaded:
            self.viewsOnSheet = jb.Parallel(n_jobs=-1, batch_size=1)(
                jb.delayed(View)(v, project) for v in self.viewNamesOnSheetList)
        else:
            self.viewsOnSheet = [View(v) for v in self.viewNamesOnSheetList]
        self.generate_Sheet_script()
        self.run_Sheet_cleaner()
        self.cleanSheetFilePath = "{0}/derevitized/{1}_xrefed.dwg".format(os.getcwd(), self.sheetName)

class View:

    def generate_View_script(self):
        scr = open("./scripts/{0}".format(self.viewCleanerScript), "w+")
        scr.write("(command)\n")
        scr.write("model\n")
        scr.write("xref t * r\n")
        scr.write("zoom all\n")
        scr.write("qsave\n")
        scr.close()

    def run_View_cleaner(self):
        command = "{acc} /i \"{path}/derevitized/{view}.dwg\" /s \"{path}/scripts/{script}\"".format(
            acc=self.acc,
            path=os.getcwd(),
            view=self.viewName,
            script=self.viewCleanerScript)
        if cfg.verbose:
            print("CLEANING VIEW {view} with SCRIPT {script}".format(view=self.viewName, script=self.viewCleanerScript))
        process = sp.Popen(command, stdout=sp.PIPE)
        output, err = process.communicate()
        if cfg.vverbose:
            output.decode("utf-16")

    def getXfromV(self):
        command = "{acc} /s {path}/scripts/CHECKER.scr /i {path}\derevitized\{view}.dwg".format(acc=self.acc,
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

    def __init__(self, vn, project):
        self.acc = project.accpath
        self.viewName = vn.replace(".dwg", "")
        self.viewIndx = str(re.compile("\d+-View-(\d+).dwg").search(vn).group(1))
        self.parentSheetIndx = str(re.compile("(\d+)-View-\d+.dwg").search(vn).group(1))
        self.viewCleanerScript = "{0}.scr".format(self.viewName.upper())
        self.xrefs = [Xref(x[0], x[1]) for x in self.getXfromV()]
        self.generate_View_script()
        self.run_View_cleaner()

class Xref:
    def __init__(self, name, path):
        self.xrefName = name
        self.xrefPath = path
