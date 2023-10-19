import os
import shutil
import sys

def removePrevPP(path):
    # Check if originals folder exists
    if os.path.exists(str(path + "/originals")):
        # Loop through all files in path
        for e in os.listdir(path):
            # Check if file
            if os.path.isfile("{0}/{1}".format(path, e)):
                # Try to remove file
                try:
                    os.remove("{0}/{1}".format(path, e))
                # Catch error
                except:
                    print("{0} is is use! CAN'T REMOVE".format(e))
                    sys.exit(1)
            # Check if directory
            if os.path.isdir("{0}/{1}".format(path, e)):
                # Check if directory is not originals
                if e != "originals":
                    # Try to remove directory
                    try:
                        shutil.rmtree(("{0}/{1}".format(path, e)))
                    # Catch error
                    except:
                        print("{0} is is use! CAN'T REMOVE".format(e))
                        sys.exit(1)

        # Copy all files from originals to path
        ([shutil.copy("{p}/originals/{f}".format(p=path, f=file), "{p}/{f}".format(p=path, f=file)) for file in
          os.listdir("{0}/originals".format(path))])
        # Try to remove originals directory
        try:
            shutil.rmtree("{0}/originals".format(path))
        # Catch error
        except:
            sys.exit(1)