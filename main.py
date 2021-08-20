import logging
import os
import sys

import magicutils as mu

try:
    import debugscripts as deb

    debugflag = True
except:
    debugflag = False


def display_title_bar():
    # Clears the terminal screen, and displays a title bar.

    print("\t**********************************************")
    print("\t______________________________________________")
    print("\t**************   DWGMAGIC   ******************")
    print("\t______________________________________________")
    print("\t****  TECTONICA - Dimitar Baldzhiev  *********")
    print("\t______________________________________________")
    print("\t******************************************V2**")


def main():
    path = sys.argv[1]
    print(path)
    display_title_bar()
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
