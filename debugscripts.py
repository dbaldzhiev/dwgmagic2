import os
import shutil
import sys


def removePrevPP(path):
    if os.path.exists(str(path + "/originals")):
        for e in os.listdir(path):
            if os.path.isfile("{0}/{1}".format(path, e)):
                try:
                    os.remove("{0}/{1}".format(path, e))
                except:
                    print("{0} is is use! CAN'T REMOVE".format(e))
                    sys.exit(1)
            if os.path.isdir("{0}/{1}".format(path, e)):
                if e != "originals":
                    try:
                        shutil.rmtree(("{0}/{1}".format(path, e)))
                    except:
                        print("{0} is is use! CAN'T REMOVE".format(e))
                        sys.exit(1)

        ([shutil.copy("{p}/originals/{f}".format(p=path, f=file), "{p}/{f}".format(p=path, f=file)) for file in
          os.listdir("{0}/originals".format(path))])
        try:
            shutil.rmtree("{0}/originals".format(path))
        except:
            sys.exit(1)
