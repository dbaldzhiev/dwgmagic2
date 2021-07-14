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
    os.system('clear')

    print("\t**********************************************")
    print("\t***  Greeter - Hello old and new friends!  ***")
    print("\t**********************************************")


def main():
    path = sys.argv[1]
    display_title_bar()
    os.chdir(path)
    logging.basicConfig(filename='acclog.log', level=logging.DEBUG)
    logging.debug(path)
    logging.debug("Chnaging DIR to " + path)
    mu.preprocess()
    mu.Project()
    print("bp")

if __name__ == "__main__":
    if debugflag:
        deb.removePrevPP(sys.argv[1])
    main()
