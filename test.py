import logging
import os
import shlex
import subprocess as sp

import config as cfg


def runPScript():
    print("STARTING DWGMAGIC: ")
    command = "'{acc}' /s '{path}/scripts/DWGMAGIC.scr'".format(acc=cfg.paths["acc"], path=os.getcwd())
    print("RUNNING: " + command)
    logging.debug("RUNNING: {}".format(command))
    process = sp.Popen(shlex.split(command), stdout=sp.PIPE, shell=True, encoding='utf-16-le', errors='replace')
    while process.poll() is None:
        output = process.stdout.readline()
        if output != "":
            print(output)
            logging.debug(output)


runPScript()
print("end")
