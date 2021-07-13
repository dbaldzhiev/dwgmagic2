import logging
import os
import sys

import magicutils as mu

try:
    import debugscripts as deb
    debugflag=True
except:
    debugflag=False

def main():
    path = sys.argv[1]
    os.chdir(path)
    logging.basicConfig(filename='acclog.log', level=logging.DEBUG)
    logging.debug(path)
    logging.debug("Chnaging DIR to " + path)
    mu.preprocess()
    mu.Project()

if __name__ == "__main__":
    if debugflag:
        deb.removePrevPP(sys.argv[1])
    main()
