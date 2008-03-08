# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.


"""
Custom logging levels for finer-grained logging using python's standard
logging module.
"""

import os
import socket
import sys
import logging
import logging.handlers
import time

INFO_1 = 19
INFO_2 = 18

DEBUG_1 = 9
DEBUG_2 = 8
DEBUG_3 = 7
DEBUG_4 = 6

logging.addLevelName(INFO_1, "INFO_1")
logging.addLevelName(INFO_2, "INFO_2")

logging.addLevelName(DEBUG_1, "DEBUG_1")
logging.addLevelName(DEBUG_2, "DEBUG_2")
logging.addLevelName(DEBUG_3, "DEBUG_3")
logging.addLevelName(DEBUG_4, "DEBUG_4")

# High level to effectively turn off logging.
# For compatability with the old logging system.
__NO_LOGGING = 100
logging.raiseExceptions = False

syslog = None

def logLevelFromErrorLevel(error_level):
    """ Convert an old-style error logging level to the new style. """
    error_table = { -1 : __NO_LOGGING, 0 : logging.CRITICAL, 1 : logging.ERROR,
        2 : logging.WARNING}
    
    return __convertLevel(error_level, error_table)

def logLevelFromDebugLevel(debug_level):
    """ Convert an old-style debug logging level to the new style. """
    debug_table = {-1 : __NO_LOGGING, 0 : logging.INFO, 1 : INFO_1, 2 : INFO_2,
        3 : logging.DEBUG, 4 : DEBUG_1, 5 : DEBUG_2, 6 : DEBUG_3, 7 : DEBUG_4}

    return __convertLevel(debug_level, debug_table)

def __convertLevel(level, table):
    """ Convert yum logging levels using a lookup table. """
    # Look up level in the table.
    try:
        new_level = table[level]
    except KeyError:
        keys = table.keys()
        # We didn't find the level in the table, check if it's smaller
        # than the smallest level
        if level < keys[0]:
            new_level = table[keys[0]]
        # Nope. So it must be larger.
        else:
            new_level =  table[keys[-2]]

    return new_level

def setDebugLevel(level):
    converted_level = logLevelFromDebugLevel(level)
    logging.getLogger("yum.verbose").setLevel(converted_level)
    
def setErrorLevel(level):
    converted_level = logLevelFromErrorLevel(level)
    logging.getLogger("yum").setLevel(converted_level)

_added_handlers = False
def doLoggingSetup(debuglevel, errorlevel):
    """
    Configure the python logger.
    
    errorlevel is optional. If provided, it will override the logging level
    provided in the logging config file for error messages.
    debuglevel is optional. If provided, it will override the logging level
    provided in the logging config file for debug messages.
    """
    global _added_handlers

    logging.basicConfig()

    if _added_handlers:
        if debuglevel is not None:
            setDebugLevel(debuglevel)
        if errorlevel is not None:  
            setErrorLevel(errorlevel)
        return

    plainformatter = logging.Formatter("%(message)s")
    syslogformatter = logging.Formatter("yum: %(message)s")
    
    console_stdout = logging.StreamHandler(sys.stdout)
    console_stdout.setFormatter(plainformatter)
    verbose = logging.getLogger("yum.verbose")
    verbose.propagate = False
    verbose.addHandler(console_stdout)
        
    console_stderr = logging.StreamHandler(sys.stderr)
    console_stderr.setFormatter(plainformatter)
    logger = logging.getLogger("yum")
    logger.propagate = False
    logger.addHandler(console_stderr)
   
    filelogger = logging.getLogger("yum.filelogging")
    filelogger.setLevel(logging.INFO)
    filelogger.propagate = False

    log_dev = '/dev/log'
    global syslog
    if os.path.exists(log_dev):
        try:
            syslog = logging.handlers.SysLogHandler(log_dev)
            syslog.setFormatter(syslogformatter)
            filelogger.addHandler(syslog)
        except socket.error:
            if syslog is not None:
                syslog.close()
    _added_handlers = True

    if debuglevel is not None:
        setDebugLevel(debuglevel)
    if errorlevel is not None:  
        setErrorLevel(errorlevel)

def setFileLog(uid, logfile):
    # TODO: When python's logging config parser doesn't blow up
    # when the user is non-root, put this in the config file.
    # syslog-style log
    if uid == 0:
        try:
            # For installroot etc.
            logdir = os.path.dirname(logfile)
            if not os.path.exists(logdir):
                os.makedirs(logdir, mode=0755)

            filelogger = logging.getLogger("yum.filelogging")
            filehandler = logging.FileHandler(logfile)
            formatter = logging.Formatter("%(asctime)s %(message)s",
                "%b %d %H:%M:%S")
            filehandler.setFormatter(formatter)
            filelogger.addHandler(filehandler)
        except IOError:
            logging.getLogger("yum").critical('Cannot open logfile %s', logfile)

def setLoggingApp(app):
    if syslog:
        syslogformatter = logging.Formatter("yum(%s): "% (app,) + "%(message)s")
        syslog.setFormatter(syslogformatter)


class EasyLogger:
    """ Smaller to use logger for yum, wraps "logging.getLogger" module. """

    def __init__(self, name="main"):
        self.name   = name
        self.logger = logging.getLogger(name)

    def info(msg, *args):
        """ Log a message as info. """

        self.logger.info(msg % args)

    def info1(msg, *args):
        """ Log a message as log.INFO_1. """

        self.logger.log(logginglevels.INFO_1, msg % args)

    def info2(msg, *args):
        """ Log a message as log.INFO_2. """

        self.logger.log(logginglevels.INFO_2, msg % args)

    def warn(msg, *args):
        """ Log a message as warning. """

        self.logger.warning(msg % args)

    def err(msg, *args):
        """ Log a message as error. """

        self.logger.error(msg % args)

    def crit(msg, *args):
        """ Log a message as critical. """

        self.logger.critical(msg % args)

    def dbg(msg, *args):
        """ Log a message as debug. """

        self.logger.debug(msg % args)

    def dbg_tm(oldtm, msg, *args):
        """ Log a message as debug, with a timestamp delta. """

        now = time.time()
        out = msg % args
        self.logger.debug(out + " time: %.4f" (now - old_tm))

    def dbg1(msg, *args):
        """ Log a message as log.DEBUG_1. """

        self.logger.log(DEBUG_1, msg % args)

    def dbg2(msg, *args):
        """ Log a message as log.DEBUG_2. """

        self.logger.log(DEBUG_2, msg % args)

    def dbg3(msg, *args):
        """ Log a message as log.DEBUG_3. """

        self.logger.log(DEBUG_3, msg % args)

log  = EasyLogger(logging.getLogger("yum.YumBase"))
vlog = EasyLogger(logging.getLogger("yum.verbose.YumBase"))

info   = log.info
info1  = log.info1
info2  = log.info2
warn   = log.warn
err    = log.err
crit   = log.crit
dbg    = log.dbg
dbg1   = log.dbg1
dbg2   = log.dbg2
dbg3   = log.dbg3
dbg_tm = log.dbgtm

vinfo   = vlog.info
vinfo1  = vlog.info1
vinfo2  = vlog.info2
vwarn   = vlog.warn
verr    = vlog.err
vcrit   = vlog.crit
vdbg    = vlog.dbg
vdbg1   = vlog.dbg1
vdbg2   = vlog.dbg2
vdbg3   = vlog.dbg3
vdbg_tm = vlog.dbgtm
