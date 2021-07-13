import logging
import os
import subprocess as sp

import config as cfg


def runPScript():
    print("STARTING DWGMAGIC: ")
    command = "{acc} /s {path}/scripts/DWGMAGIC.scr".format(acc=cfg.paths["acc"], path=os.getcwd())
    print("RUNNING: " + command)
    logging.debug("RUNNING: {}".format(command))
    if cfg.verbose:
        print(command)
    process = sp.Popen(command, stdout=sp.PIPE)
    while True:
        output = process.stdout.readline()
        if output == '' and process.poll() is not None:
            break
        if output:
            print(output.strip().decode("utf-16", errors="ignore"))
    # output, err = process.communicate()
    # logging.debug(output.decode("utf-16"))


runPScript()
