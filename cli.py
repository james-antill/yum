#!/usr/bin/python -t
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
# Copyright 2005 Duke University 
# Written by Seth Vidal

"""
Command line interface yum class and related.
"""

import os
import re
import sys
import time
import random
import logging
from yum import logginglevels

nelogger = logginglevels.EasyLogger("yum.cli")
velogger = logginglevels.EasyLogger("yum.verbose.cli")
(info,info1,info2,info3, warn,err,crit)  = nelogger.funcs("sc_info", "sc_main")
(vinfo,vinfo1,vinfo2,vinfo3, vwarn,verr,vcrit,
 vdbg,vdbg1,vdbg2,vdbg3,vdbg4,vdbg_tm)   = velogger.funcs("sc", "dbg_tm")

from optparse import OptionParser
import rpm

import output
import shell
import yum
import yum.Errors
import yum.misc
import yum.plugins
from yum.constants import TS_OBSOLETED
import rpmUtils.arch
from rpmUtils.arch import isMultiLibArch
import rpmUtils.miscutils
from yum.packages import parsePackages, YumLocalPackage
from yum.i18n import _
from yum.rpmtrans import RPMTransaction
import signal
import yumcommands

def sigquit(signum, frame):
    """ SIGQUIT handler for the yum cli. """
    print >> sys.stderr, "Quit signal sent - exiting immediately"
    sys.exit(1)

class CliError(yum.Errors.YumBaseError):

    """
    Command line interface related Exception.
    """

    def __init__(self, args=''):
        yum.Errors.YumBaseError.__init__(self)
        self.args = args

class YumBaseCli(yum.YumBase, output.YumOutput):
    """This is the base class for yum cli.
       Inherits from yum.YumBase and output.YumOutput """
       
    def __init__(self):
        # handle sigquit early on
        signal.signal(signal.SIGQUIT, sigquit)
        yum.YumBase.__init__(self)
        output.YumOutput.__init__(self)
        logging.basicConfig()
        self.logger         = nelogger.logger
        self.verbose_logger = velogger.logger
        self.yum_cli_commands = {}
        self.registerCommand(yumcommands.InstallCommand())
        self.registerCommand(yumcommands.UpdateCommand())
        self.registerCommand(yumcommands.InfoCommand())
        self.registerCommand(yumcommands.ListCommand())
        self.registerCommand(yumcommands.EraseCommand())
        self.registerCommand(yumcommands.GroupCommand())
        self.registerCommand(yumcommands.GroupListCommand())
        self.registerCommand(yumcommands.GroupInstallCommand())
        self.registerCommand(yumcommands.GroupRemoveCommand())
        self.registerCommand(yumcommands.GroupInfoCommand())
        self.registerCommand(yumcommands.MakeCacheCommand())
        self.registerCommand(yumcommands.CleanCommand())
        self.registerCommand(yumcommands.ProvidesCommand())
        self.registerCommand(yumcommands.CheckUpdateCommand())
        self.registerCommand(yumcommands.SearchCommand())
        self.registerCommand(yumcommands.UpgradeCommand())
        self.registerCommand(yumcommands.LocalInstallCommand())
        self.registerCommand(yumcommands.ResolveDepCommand())
        self.registerCommand(yumcommands.ShellCommand())
        self.registerCommand(yumcommands.DepListCommand())
        self.registerCommand(yumcommands.RepoListCommand())
        self.registerCommand(yumcommands.HelpCommand())
        self.registerCommand(yumcommands.ReInstallCommand())        

    def registerCommand(self, command):
        for name in command.getNames():
            if self.yum_cli_commands.has_key(name):
                raise yum.Errors.ConfigError(_('Command "%s" already defined') % name)
            self.yum_cli_commands[name] = command
            
    def doRepoSetup(self, thisrepo=None, dosack=1):
        """grabs the repomd.xml for each enabled repository 
           and sets up the basics of the repository"""
        
        if self._repos and thisrepo is None:
            return self._repos
            
        if not thisrepo:
            vinfo2(_('Setting up repositories'))

        # Call parent class to do the bulk of work 
        # (this also ensures that reposetup plugin hook is called)
        if thisrepo:
            yum.YumBase._getRepos(self, thisrepo=thisrepo, doSetup=True)
        else:
            yum.YumBase._getRepos(self, thisrepo=thisrepo)

        if dosack: # so we can make the dirs and grab the repomd.xml but not import the md
            vinfo2(_('Reading repository metadata in from local files'))
            self._getSacks(thisrepo=thisrepo)
        
        return self._repos

    def _makeUsage(self):
        """
        Format an attractive usage string for yum, listing subcommand
        names and summary usages.
        """
        usage = 'yum [options] COMMAND\n\nList of Commands:\n\n'
        commands = yum.misc.unique(self.yum_cli_commands.values())
        commands.sort(cmp=lambda x,y : cmp(x.getNames()[0], y.getNames()[0]))
        for command in commands:
            # XXX Remove this when getSummary is common in plugins
            try:
                summary = command.getSummary()
                usage += "%-14s %s\n" % (command.getNames()[0], summary)
            except (AttributeError, NotImplementedError):
                usage += "%s\n" % command.getNames()[0]

        return usage

    def getOptionsConfig(self, args):
        """parses command line arguments, takes cli args:
        sets up self.conf and self.cmds as well as logger objects 
        in base instance"""
       
        self.optparser = YumOptionParser(base=self, usage=self._makeUsage())
        
        # Parse only command line options that affect basic yum setup
        opts = self.optparser.firstParse(args)

        # Just print out the version if that's what the user wanted
        if opts.version:
            print yum.__version__
            sys.exit(0)

        # get the install root to use
        root = self.optparser.getRoot(opts)

        if opts.quiet:
            opts.debuglevel = logginglevels.DBG_QUIET_LEVEL
        if opts.verbose:
            opts.debuglevel = logginglevels.DBG_VERBOSE_LEVEL
            opts.errorlevel = logginglevels.ERR_VERBOSE_LEVEL
       
        # Read up configuration options and initialise plugins
        try:
            self._getConfig(opts.conffile, root, 
                    init_plugins=not opts.noplugins,
                    plugin_types=(yum.plugins.TYPE_CORE, yum.plugins.TYPE_INTERACTIVE),
                    optparser=self.optparser,
                    debuglevel=opts.debuglevel,
                    errorlevel=opts.errorlevel,
                    disabled_plugins=self.optparser._splitArg(opts.disableplugins))
                    
        except yum.Errors.ConfigError, e:
            crit(_('Config Error: %s'), e)
            sys.exit(1)
        except ValueError, e:
            crit(_('Options Error: %s'), e)
            sys.exit(1)

        # update usage in case plugins have added commands
        self.optparser.set_usage(self._makeUsage())
        
        # Now parse the command line for real and 
        # apply some of the options to self.conf
        (opts, self.cmds) = self.optparser.setupYumConfig()

        if opts.sleeptime is not None:
            sleeptime = random.randrange(opts.sleeptime*60)
        else:
            sleeptime = 0
        
        # save our original args out
        self.args = args
        # save out as a nice command string
        self.cmdstring = 'yum '
        for arg in self.args:
            self.cmdstring += '%s ' % arg

        try:
            self.parseCommands() # before we return check over the base command + args
                                 # make sure they match/make sense
        except CliError:
            sys.exit(1)
    
        # run the sleep - if it's unchanged then it won't matter
        time.sleep(sleeptime)
        
    def parseCommands(self):
        """reads self.cmds and parses them out to make sure that the requested 
        base command + argument makes any sense at all""" 

        vdbg('Yum Version: %s', yum.__version__)
        vdbg('COMMAND: %s', self.cmdstring)
        vdbg('Installroot: %s', self.conf.installroot)
        if len(self.conf.commands) == 0 and len(self.cmds) < 1:
            self.cmds = self.conf.commands
        else:
            self.conf.commands = self.cmds
        if len(self.cmds) < 1:
            crit(_('You need to give some command'))
            self.usage()
            raise CliError
            
        self.basecmd = self.cmds[0] # our base command
        self.extcmds = self.cmds[1:] # out extended arguments/commands
        
        if len(self.extcmds) > 0:
            vdbg('Ext Commands:\n')
            for arg in self.extcmds:
                vdbg('   %s', arg)
        
        if not self.yum_cli_commands.has_key(self.basecmd):
            self.usage()
            raise CliError
    
        self.yum_cli_commands[self.basecmd].doCheck(self, self.basecmd, self.extcmds)

    def doShell(self):
        """do a shell-like interface for yum commands"""

        yumshell = shell.YumShell(base=self)
        if len(self.extcmds) == 0:
            yumshell.cmdloop()
        else:
            yumshell.script()
        return yumshell.result, yumshell.resultmsgs

    def errorSummary(self, errstring):
        """ parse the error string for 'interesting' errors which can
            be grouped, such as disk space issues """
        summary = ''
        # do disk space report first
        p = re.compile('needs (\d+)MB on the (\S+) filesystem')
        disk = {}
        for m in p.finditer(errstring):
            if not disk.has_key(m.group(2)):
                disk[m.group(2)] = int(m.group(1))
            if disk[m.group(2)] < int(m.group(1)):
                disk[m.group(2)] = int(m.group(1))
                
        if disk:
           summary += _('Disk Requirements:\n')
           for k in disk:
              summary += _('  At least %dMB needed on the %s filesystem.\n') % (disk[k], k)

        # TODO: simplify the dependency errors?

        # Fixup the summary
        summary = _('Error Summary\n-------------\n') + summary
              
        return summary


    def doCommands(self):
        """
        Calls the base command passes the extended commands/args out to be
        parsed (most notably package globs).
        
        Returns a numeric result code and an optional string
           - 0 = we're done, exit
           - 1 = we've errored, exit with error string
           - 2 = we've got work yet to do, onto the next stage
        """
        
        # at this point we know the args are valid - we don't know their meaning
        # but we know we're not being sent garbage
        
        # setup our transaction set if the command we're using needs it
        # compat with odd modules not subclassing YumCommand
        needTs = True
        if hasattr(self.yum_cli_commands[self.basecmd], 'needTs'):
            needTs = self.yum_cli_commands[self.basecmd].needTs(self, self.basecmd, self.extcmds)
        
        if needTs:
            try:
                self._getTs()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]

        cmd_st = time.time()
        ret = self.yum_cli_commands[self.basecmd].doCommand(self, self.basecmd, self.extcmds)
        vdbg_tm(cmd_st, 'command')
        return ret

    def doTransaction(self):
        """takes care of package downloading, checking, user confirmation and actually
           RUNNING the transaction"""
    
        # just make sure there's not, well, nothing to do
        if len(self.tsInfo) == 0:
                vinfo(_('Trying to run the transaction but nothing to do. Exiting.'))
                return 1

        # output what will be done:
        vinfo1(self.listTransaction())
        
        # Check which packages have to be downloaded
        downloadpkgs = []
        stuff_to_download = False
        for txmbr in self.tsInfo.getMembers():
            if txmbr.ts_state in ['i', 'u']:
                stuff_to_download = True
                po = txmbr.po
                if po:
                    downloadpkgs.append(po)

        # Close the connection to the rpmdb so that rpm doesn't hold the SIGINT
        # handler during the downloads. self.ts is reinitialised later in this
        # function anyway (initActionTs). 
        self.ts.close()

        # Report the total download size to the user, so he/she can base
        # the answer on this info
        if stuff_to_download:
            self.reportDownloadSize(downloadpkgs)
        
        # confirm with user
        if self._promptWanted():
            if not self.userconfirm():
                vinfo(_('Exiting on user Command'))
                return 1

        vinfo2(_('Downloading Packages:'))
        problems = self.downloadPkgs(downloadpkgs) 

        if len(problems) > 0:
            errstring = ''
            errstring += _('Error Downloading Packages:\n')
            for key in problems:
                errors = yum.misc.unique(problems[key])
                for error in errors:
                    errstring += '  %s: %s\n' % (key, error)
            raise yum.Errors.YumBaseError, errstring

        # Check GPG signatures
        if self.gpgsigcheck(downloadpkgs) != 0:
            return 1
        
        if self.conf.rpm_check_debug:
            rcd_st = time.time()
            vinfo2(_('Running rpm_check_debug'))
            msgs = self._run_rpm_check_debug()
            if msgs:
                print _('ERROR with rpm_check_debug vs depsolve:')
                for msg in msgs:
                    print msg
    
                return 1, [_('Please report this error in bugzilla')]

            vdbg_tm(rcd_st, 'rpm_check_debug')

        tt_st = time.time()            
        vinfo2(_('Running Transaction Test'))
        if self.conf.diskspacecheck == False:
            self.tsInfo.probFilterFlags.append(rpm.RPMPROB_FILTER_DISKSPACE)
            
        
        testcb = RPMTransaction(self, test=True)
        
        self.initActionTs()
        # save our dsCallback out
        dscb = self.dsCallback
        self.dsCallback = None # dumb, dumb dumb dumb!
        self.populateTs(keepold=0) # sigh
        tserrors = self.ts.test(testcb)
        del testcb
        
        vinfo2(_('Finished Transaction Test'))
        if len(tserrors) > 0:
            errstring = _('Transaction Check Error:\n')
            for descr in tserrors:
                errstring += '  %s\n' % descr 
            
            raise yum.Errors.YumBaseError, errstring + '\n' + \
                 self.errorSummary(errstring)
        vinfo2(_('Transaction Test Succeeded'))
        del self.ts
        
        vdbg_tm(tt_st, 'Transaction Test')
        
        # unset the sigquit handler
        signal.signal(signal.SIGQUIT, signal.SIG_DFL)
        
        ts_st = time.time()
        self.initActionTs() # make a new, blank ts to populate
        self.populateTs(keepold=0) # populate the ts
        self.ts.check() #required for ordering
        self.ts.order() # order

        # put back our depcheck callback
        self.dsCallback = dscb
        # setup our rpm ts callback
        cb = RPMTransaction(self, display=output.YumCliRPMCallBack)
        if self.conf.debuglevel < 2:
            cb.display.output = False

        vinfo2(_('Running Transaction'))
        self.runTransaction(cb=cb)

        vdbg_tm(ts_st, 'Transaction')
        # close things
        vinfo1(self.postTransactionOutput())
        
        # put back the sigquit handler
        signal.signal(signal.SIGQUIT, sigquit)
        
        return 0
        
    def gpgsigcheck(self, pkgs):
        '''Perform GPG signature verification on the given packages, installing
        keys if possible

        Returns non-zero if execution should stop (user abort).
        Will raise YumBaseError if there's a problem
        '''
        for po in pkgs:
            result, errmsg = self.sigCheckPkg(po)

            if result == 0:
                # Verified ok, or verify not req'd
                continue            

            elif result == 1:
               if not sys.stdin.isatty() and not self.conf.assumeyes:
                  raise yum.Errors.YumBaseError, \
                        _('Refusing to automatically import keys when running ' \
                        'unattended.\nUse "-y" to override.')

               # the callback here expects to be able to take options which
               # userconfirm really doesn't... so fake it
               self.getKeyForPackage(po, lambda x, y, z: self.userconfirm())

            else:
                # Fatal error
                raise yum.Errors.YumBaseError, errmsg

        return 0

    
    def installPkgs(self, userlist):
        """Attempts to take the user specified list of packages/wildcards
           and install them, or if they are installed, update them to a newer
           version. If a complete version number if specified, attempt to 
           downgrade them to the specified version"""
        # get the list of available packages
        # iterate over the user's list
        # add packages to Transaction holding class if they match.
        # if we've added any packages to the transaction then return 2 and a string
        # if we've hit a snag, return 1 and the failure explanation
        # if we've got nothing to do, return 0 and a 'nothing available to install' string
        
        oldcount = len(self.tsInfo)
        
        toBeInstalled = {} # keyed on name
        passToUpdate = [] # list of pkgtups to pass along to updatecheck

        vinfo2(_('Parsing package install arguments'))
        for arg in userlist:
            if os.path.exists(arg) and arg.endswith('.rpm'): # this is hurky, deal w/it
                val, msglist = self.localInstall(filelist=[arg])
                continue # it was something on disk and it ended in rpm 
                         # no matter what we don't go looking at repos
            try:
                self.install(pattern=arg)
            except yum.Errors.InstallError:
                vinfo2(_('No package %s available.'), arg)


        if len(self.tsInfo) > oldcount:
            return 2, [_('Package(s) to install')]
        return 0, [_('Nothing to do')]
        
        
    def updatePkgs(self, userlist, quiet=0):
        """take user commands and populate transaction wrapper with 
           packages to be updated"""
        
        # if there is no userlist, then do global update below
        # this is probably 90% of the calls
        # if there is a userlist then it's for updating pkgs, not obsoleting
        
        oldcount = len(self.tsInfo)
        installed = self.rpmdb.simplePkgList()
        updates = self.up.getUpdatesTuples()
        if self.conf.obsoletes:
            obsoletes = self.up.getObsoletesTuples(newest=1)
        else:
            obsoletes = []

        if len(userlist) == 0: # simple case - do them all
            for (obsoleting, installed) in obsoletes:
                obsoleting_pkg = self.getPackageObject(obsoleting)
                installed_pkg =  self.rpmdb.searchPkgTuple(installed)[0]
                self.tsInfo.addObsoleting(obsoleting_pkg, installed_pkg)
                self.tsInfo.addObsoleted(installed_pkg, obsoleting_pkg)
                                
            for (new, old) in updates:
                txmbrs = self.tsInfo.getMembers(pkgtup=old)

                if txmbrs and txmbrs[0].output_state == TS_OBSOLETED: 
                    vdbg2(_('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s'), old)
                else:
                    updating_pkg = self.getPackageObject(new)
                    updated_pkg = self.rpmdb.searchPkgTuple(old)[0]
                    self.tsInfo.addUpdate(updating_pkg, updated_pkg)


        else:
            # go through the userlist - look for items that are local rpms. If we find them
            # pass them off to localInstall() and then move on
            localupdates = []
            for item in userlist:
                if os.path.exists(item) and item[-4:] == '.rpm': # this is hurky, deal w/it
                    localupdates.append(item)
            
            if len(localupdates) > 0:
                val, msglist = self.localInstall(filelist=localupdates, updateonly=1)
                for item in localupdates:
                    userlist.remove(item)
                
            # we've got a userlist, match it against updates tuples and populate
            # the tsInfo with the matches
            updatesPo = []
            for (new, old) in updates:
                (n,a,e,v,r) = new
                updatesPo.extend(self.pkgSack.searchNevra(name=n, arch=a, epoch=e, 
                                 ver=v, rel=r))
                                 
            exactmatch, matched, unmatched = yum.packages.parsePackages(
                                                updatesPo, userlist, casematch=1)
            for userarg in unmatched:
                if not quiet:
                    err(_('Could not find update match for %s'), userarg)

            updateMatches = yum.misc.unique(matched + exactmatch)
            for po in updateMatches:
                for (new, old) in updates:
                    if po.pkgtup == new:
                        updated_pkg = self.rpmdb.searchPkgTuple(old)[0]
                        self.tsInfo.addUpdate(po, updated_pkg)


        if len(self.tsInfo) > oldcount:
            change = len(self.tsInfo) - oldcount
            msg = _('%d packages marked for Update') % change
            return 2, [msg]
        else:
            return 0, [_('No Packages marked for Update')]


        
    
    def erasePkgs(self, userlist):
        """take user commands and populate a transaction wrapper with packages
           to be erased/removed"""
        
        oldcount = len(self.tsInfo)
        
        for arg in userlist:
            self.remove(pattern=arg)
        
        if len(self.tsInfo) > oldcount:
            change = len(self.tsInfo) - oldcount
            msg = _('%d packages marked for removal') % change
            return 2, [msg]
        else:
            return 0, [_('No Packages marked for removal')]
    
    def localInstall(self, filelist, updateonly=0):
        """handles installs/updates of rpms provided on the filesystem in a 
           local dir (ie: not from a repo)"""
           
        # read in each package into a YumLocalPackage Object
        # append it to self.localPackages
        # check if it can be installed or updated based on nevra versus rpmdb
        # don't import the repos until we absolutely need them for depsolving

        if len(filelist) == 0:
            return 0, [_('No Packages Provided')]

        installing = False
        for pkg in filelist:
            txmbrs = self.installLocal(pkg, updateonly=updateonly)
            if txmbrs:
                installing = True

        if installing:
            return 2, [_('Package(s) to install')]
        return 0, [_('Nothing to do')]

    def returnPkgLists(self, extcmds):
        """Returns packages lists based on arguments on the cli.returns a 
           GenericHolder instance with the following lists defined:
           available = list of packageObjects
           installed = list of packageObjects
           updates = tuples of packageObjects (updating, installed)
           extras = list of packageObjects
           obsoletes = tuples of packageObjects (obsoleting, installed)
           recent = list of packageObjects
           """
        
        special = ['available', 'installed', 'all', 'extras', 'updates', 'recent',
                   'obsoletes']
        
        pkgnarrow = 'all'
        if len(extcmds) > 0:
            if extcmds[0] in special:
                pkgnarrow = extcmds.pop(0)

        dpl_st = time.time()
        ypl = self.doPackageLists(pkgnarrow=pkgnarrow, patterns=extcmds)
        vdbg_tm(dpl_st, 'list:dPL')
        # rework the list output code to know about:
        # obsoletes output
        # the updates format

        def _shrinklist(lst, args):
            if len(lst) > 0 and len(args) > 0:
                vdbg1(_('Matching packages for package list to user args'))
                exactmatch, matched, unmatched = yum.packages.parsePackages(lst, args)
                return yum.misc.unique(matched + exactmatch)
            else:
                return lst
        
        shrink_st = time.time()
        ypl.updates = _shrinklist(ypl.updates, extcmds)
        ypl.installed = _shrinklist(ypl.installed, extcmds)
        ypl.available = _shrinklist(ypl.available, extcmds)
        ypl.recent = _shrinklist(ypl.recent, extcmds)
        ypl.extras = _shrinklist(ypl.extras, extcmds)
        ypl.obsoletes = _shrinklist(ypl.obsoletes, extcmds)
        vdbg_tm(shrink_st, 'list: shrink')
        
        vdbg_tm(dpl_st, 'list')
#        for lst in [ypl.obsoletes, ypl.updates]:
#            if len(lst) > 0 and len(extcmds) > 0:
#                vdbg1('Matching packages for tupled package list to user args')
#                for (pkg, instpkg) in lst:
#                    exactmatch, matched, unmatched = yum.packages.parsePackages(lst, extcmds)
                    
        return ypl

    def search(self, args):
        """cli wrapper method for module search function, searches simple
           text tags in a package object"""
        
        # call the yum module search function with lists of tags to search
        # and what to search for
        # display the list of matches
            
        searchlist = ['name', 'summary', 'description', 'url']
        matching = self.searchGenerator(searchlist, args, showdups=self.conf.showdupesfromrepos)
        
        total = 0
        for (po, matched_value) in matching:
            self.matchcallback(po, matched_value, args)
            total += 1
            
        if total == 0:
            return 0, [_('No Matches found')]
        return 0, matching

    def deplist(self, args):
        """cli wrapper method for findDeps method takes a list of packages and 
            returns a formatted deplist for that package"""
        
        for arg in args:
            pkgs = []
            ematch, match, unmatch = self.pkgSack.matchPackageNames([arg])
            for po in ematch + match:
                pkgs.append(po)
                
            results = self.findDeps(pkgs)
            self.depListOutput(results)

        return 0, []

    def provides(self, args):
        """use the provides methods in the rpmdb and pkgsack to produce a list 
           of items matching the provides strings. This is a cli wrapper to the 
           module"""
        
        matching = self.searchPackageProvides(args, callback=self.matchcallback)
        
        if len(matching) == 0:
            return 0, ['No Matches found']
        
        return 0, []
    
    def resolveDepCli(self, args):
        """returns a package (one per user arg) that provide the supplied arg"""
        
        for arg in args:
            try:
                pkg = self.returnPackageByDep(arg)
            except yum.Errors.YumBaseError:
                crit(_('No Package Found for %s'), arg)
            else:
                msg = '%s:%s-%s-%s.%s' % (pkg.epoch, pkg.name, pkg.version, pkg.release, pkg.arch)
                vinfo(msg)

        return 0, []
    
    def cleanCli(self, userlist):
        hdrcode = pkgcode = xmlcode = dbcode = 0
        pkgresults = hdrresults = xmlresults = dbresults = []
        if 'all' in userlist:
            vinfo2(_('Cleaning up Everything'))
            pkgcode, pkgresults = self.cleanPackages()
            hdrcode, hdrresults = self.cleanHeaders()
            xmlcode, xmlresults = self.cleanMetadata()
            dbcode, dbresults = self.cleanSqlite()
            self.plugins.run('clean')
            
            code = hdrcode + pkgcode + xmlcode + dbcode
            results = hdrresults + pkgresults + xmlresults + dbresults
            for msg in results:
                dbg(msg)
            return code, []
            
        if 'headers' in userlist:
            dbg(_('Cleaning up Headers'))
            hdrcode, hdrresults = self.cleanHeaders()
        if 'packages' in userlist:
            dbg(_('Cleaning up Packages'))
            pkgcode, pkgresults = self.cleanPackages()
        if 'metadata' in userlist:
            dbg(_('Cleaning up xml metadata'))
            xmlcode, xmlresults = self.cleanMetadata()
        if 'dbcache' in userlist or 'metadata' in userlist:
            dbg(_('Cleaning up database cache'))
            dbcode, dbresults =  self.cleanSqlite()
        if 'plugins' in userlist:
            dbg(_('Cleaning up plugins'))
            self.plugins.run('clean')

            
        code = hdrcode + pkgcode + xmlcode + dbcode
        results = hdrresults + pkgresults + xmlresults + dbresults
        for msg in results:
            vinfo2(msg)
        return code, []

    def returnGroupLists(self, userlist):

        uservisible=1
            
        if len(userlist) > 0:
            if userlist[0] == 'hidden':
                uservisible=0

        installed, available = self.doGroupLists(uservisible=uservisible)

        if len(installed) > 0:
            vinfo2(_('Installed Groups:'))
            for group in installed:
                vinfo2('   %s', group.name)
        
        if len(available) > 0:
            vinfo2(_('Available Groups:'))
            for group in available:
                vinfo2('   %s', group.name)
            
        return 0, [_('Done')]
    
    def returnGroupInfo(self, userlist):
        """returns complete information on a list of groups"""
        for strng in userlist:
            group = self.comps.return_group(strng)
            if group:
                self.displayPkgsInGroups(group)
            else:
                err(_('Warning: Group %s does not exist.'), strng)
        
        return 0, []
        
    def installGroups(self, grouplist):
        """for each group requested do 'selectGroup' on them."""
        
        pkgs_used = []
        
        for group_string in grouplist:
            group = self.comps.return_group(group_string)
            if not group:
                crit(_('Warning: Group %s does not exist.'), group_string)
                continue
            
            try:
                txmbrs = self.selectGroup(group.groupid)
            except yum.Errors.GroupsError:
                crit(_('Warning: Group %s does not exist.'), group_string)
                continue
            else:
                pkgs_used.extend(txmbrs)
            
        if not pkgs_used:
            return 0, [_('No packages in any requested group available to install or update')]
        else:
            return 2, [_('%d Package(s) to Install') % len(pkgs_used)]

    def removeGroups(self, grouplist):
        """Remove only packages of the named group(s). Do not recurse."""

        pkgs_used = []
        for group_string in grouplist:
            try:
                txmbrs = self.groupRemove(group_string)
            except yum.Errors.GroupsError:
                crit(_('No group named %s exists'), group_string)
                continue
            else:
                pkgs_used.extend(txmbrs)
                
        if not pkgs_used:
            return 0, [_('No packages to remove from groups')]
        else:
            return 2, [_('%d Package(s) to remove') % len(pkgs_used)]



    def _promptWanted(self):
        # shortcut for the always-off/always-on options
        if self.conf.assumeyes:
            return False
        if self.conf.alwaysprompt:
            return True
        
        # prompt if:
        #  package was added to fill a dependency
        #  package is being removed
        #  package wasn't explictly given on the command line
        for txmbr in self.tsInfo.getMembers():
            if txmbr.isDep or \
                   txmbr.ts_state == 'e' or \
                   txmbr.name not in self.extcmds:
                return True
        
        # otherwise, don't prompt        
        return False

    def usage(self):
        ''' Print out command line usage '''
        self.optparser.print_help()

    def shellUsage(self):
        ''' Print out the shell usage '''
        self.optparser.print_usage()
    
    def _installable(self, pkg, ematch=False):

        """check if the package is reasonably installable, true/false"""
        
        exactarchlist = self.conf.exactarchlist        
        # we look through each returned possibility and rule out the
        # ones that we obviously can't use
        
        if self.rpmdb.contains(po=pkg):
            dbg3(_('Package %s is already installed, skipping'), pkg)
            return False
        
        # everything installed that matches the name
        installedByKey = self.rpmdb.searchNevra(name=pkg.name)
        comparable = []
        for instpo in installedByKey:
            if rpmUtils.arch.isMultiLibArch(instpo.arch) == rpmUtils.arch.isMultiLibArch(pkg.arch):
                comparable.append(instpo)
            else:
                vdbg3(_('Discarding non-comparable pkg %s.%s'), instpo.name, instpo.arch)
                continue
                
        # go through each package 
        if len(comparable) > 0:
            for instpo in comparable:
                if pkg.EVR > instpo.EVR: # we're newer - this is an update, pass to them
                    if instpo.name in exactarchlist:
                        if pkg.arch == instpo.arch:
                            return True
                    else:
                        return True
                        
                elif pkg.EVR == instpo.EVR: # same, ignore
                    return False
                    
                elif pkg.EVR < instpo.EVR: # lesser, check if the pkgtup is an exactmatch
                                   # if so then add it to be installed
                                   # if it can be multiply installed
                                   # this is where we could handle setting 
                                   # it to be an 'oldpackage' revert.
                                   
                    if ematch and self.allowedMultipleInstalls(pkg):
                        return True
                        
        else: # we've not got any installed that match n or n+a
            vdbg1(_('No other %s installed, adding to list for potential install'), pkg.name)
            return True
        
        return False

class YumOptionParser(OptionParser):
    '''Subclass that makes some minor tweaks to make OptionParser do things the
    "yum way".
    '''

    def __init__(self,base, **kwargs):
        OptionParser.__init__(self, **kwargs)
        self.logger = nelogger.logger
        self.base = base
        self._addYumBasicOptions()

    def error(self, msg):
        '''This method is overridden so that error output goes to logger. '''
        self.print_usage()
        crit(_("Command line error: %s"), msg)
        sys.exit(1)

    def firstParse(self,args):
        # Parse only command line options that affect basic yum setup
        try:
            args = _filtercmdline(
                        ('--noplugins','--version','-q', '-v', "--quiet", "--verbose"), 
                        ('-c', '-d', '-e', '--installroot','--disableplugin'), 
                        args)
        except ValueError:
            self.base.usage()
            sys.exit(1)
        return self.parse_args(args=args)[0]

    @staticmethod
    def _splitArg(seq):
        """ Split all strings in seq, at "," and whitespace.
            Returns a new list. """
        ret = []
        for arg in seq:
            ret.extend(arg.replace(",", " ").split())
        return ret
        
    def setupYumConfig(self):
        # Now parse the command line for real
        (opts, cmds) = self.parse_args()

        # Let the plugins know what happened on the command line
        self.base.plugins.setCmdLine(opts, cmds)

        try:
            # config file is parsed and moving us forward
            # set some things in it.
                
            # Handle remaining options
            if opts.assumeyes:
                self.base.conf.assumeyes =1
            # seems a good place for it - to go back to yum 3.0.X behavior
            # if not root then caching is enabled
            if opts.cacheonly or self.base.conf.uid != 0:
                self.base.conf.cache = 1

            if opts.obsoletes:
                self.base.conf.obsoletes = 1

            if opts.installroot:
                self.base.conf.installroot = opts.installroot
                
            if opts.skipbroken:
                self.base.conf.skip_broken = True

            if opts.showdupesfromrepos:
                self.base.conf.showdupesfromrepos = True

            if opts.disableexcludes:
                disable_excludes = self._splitArg(opts.disableexcludes)
            else:
                disable_excludes = []
            self.base.conf.disable_excludes = disable_excludes

            for exclude in self._splitArg(opts.exclude):
                try:
                    excludelist = self.base.conf.exclude
                    excludelist.append(exclude)
                    self.base.conf.exclude = excludelist
                except yum.Errors.ConfigError, e:
                    crit(e)
                    self.base.usage()
                    sys.exit(1)

            # setup the progress bars/callbacks
            self.base.setupProgressCallbacks()
                    
            # Process repo enables and disables in order
            for opt, repoexp in opts.repos:
                try:
                    if opt == '--enablerepo':
                        self.base.repos.enableRepo(repoexp)
                    elif opt == '--disablerepo':
                        self.base.repos.disableRepo(repoexp)
                except yum.Errors.ConfigError, e:
                    crit(e)
                    self.base.usage()
                    sys.exit(1)

            # make sure the added repos are setup.        
            if len(opts.repos) > 0:
                self.base._getRepos(doSetup=True)

            # Disable all gpg key checking, if requested.
            if opts.nogpgcheck:
                self.base.conf.gpgcheck = False
                for repo in self.base.repos.listEnabled():
                    repo.gpgcheck = False
                            
        except ValueError, e:
            crit(_('Options Error: %s'), e)
            self.base.usage()
            sys.exit(1)
         
        return opts, cmds

    def getRoot(self,opts):
        # If the conf file is inside the  installroot - use that.
        # otherwise look for it in the normal root
        if opts.installroot:
            if os.access(opts.installroot+'/'+opts.conffile, os.R_OK):
                opts.conffile = opts.installroot+'/'+opts.conffile
            elif opts.conffile == '/etc/yum/yum.conf':
                # check if /installroot/etc/yum.conf exists.
                if os.access(opts.installroot+'/etc/yum.conf', os.R_OK):
                    opts.conffile = opts.installroot+'/etc/yum.conf'         
            root=opts.installroot
        else:
            root = '/'
        return root

    def _addYumBasicOptions(self):
        def repo_optcb(optobj, opt, value, parser):
            '''Callback for the enablerepo and disablerepo option. 
            
            Combines the values given for these options while preserving order
            from command line.
            '''
            dest = eval('parser.values.%s' % optobj.dest)
            dest.append((opt, value))

        
        self.add_option("-t", "--tolerant", action="store_true",
                help=_("be tolerant of errors"))
        self.add_option("-C", dest="cacheonly", action="store_true",
                help=_("run entirely from cache, don't update cache"))
        self.add_option("-c", dest="conffile", default='/etc/yum/yum.conf',
                help=_("config file location"), metavar=' [config file]')
        self.add_option("-R", dest="sleeptime", type='int', default=None,
                help=_("maximum command wait time"), metavar=' [minutes]')
        self.add_option("-d", dest="debuglevel", default=None,
                help=_("debugging output level"), type='int',
                metavar=' [debug level]')
        self.add_option("--showduplicates", dest="showdupesfromrepos",
                        action="store_true",
                help=_("show duplicates, in repos, in list/search commands"))
        self.add_option("-e", dest="errorlevel", default=None,
                help=_("error output level"), type='int',
                metavar=' [error level]')
        self.add_option("-q", "--quiet", dest="quiet", action="store_true",
                        help=_("quiet operation"))
        self.add_option("-v", "--verbose", dest="verbose", action="store_true",
                        help="verbose operation")
        self.add_option("-y", dest="assumeyes", action="store_true",
                help=_("answer yes for all questions"))
        self.add_option("--version", action="store_true", 
                help=_("show Yum version and exit"))
        self.add_option("--installroot", help=_("set install root"), 
                metavar='[path]')
        self.add_option("--enablerepo", action='callback',
                type='string', callback=repo_optcb, dest='repos', default=[],
                help=_("enable one or more repositories (wildcards allowed)"),
                metavar='[repo]')
        self.add_option("--disablerepo", action='callback',
                type='string', callback=repo_optcb, dest='repos', default=[],
                help=_("disable one or more repositories (wildcards allowed)"),
                metavar='[repo]')
        self.add_option("-x", "--exclude", default=[], action="append",
                help=_("exclude package(s) by name or glob"), metavar='[package]')
        self.add_option("", "--disableexcludes", default=[], action="append",
                help=_("disable exclude from main, for a repo or for everything"),
                        metavar='[repo]')
        self.add_option("--obsoletes", action="store_true", 
                help=_("enable obsoletes processing during updates"))
        self.add_option("--noplugins", action="store_true", 
                help=_("disable Yum plugins"))
        self.add_option("--nogpgcheck", action="store_true",
                help=_("disable gpg signature checking"))
        self.add_option("", "--disableplugin", dest="disableplugins", default=[], 
                action="append", help=_("disable plugins by name"),
                metavar='[plugin]')
        self.add_option("--skip-broken", action="store_true", dest="skipbroken",
                help=_("skip packages with depsolving problems"))


        
def _filtercmdline(novalopts, valopts, args):
    '''Keep only specific options from the command line argument list

    This function allows us to peek at specific command line options when using
    the optparse module. This is useful when some options affect what other
    options should be available.

    @param novalopts: A sequence of options to keep that don't take an argument.
    @param valopts: A sequence of options to keep that take a single argument.
    @param args: The command line arguments to parse (as per sys.argv[:1]
    @return: A list of strings containing the filtered version of args.

    Will raise ValueError if there was a problem parsing the command line.
    '''
    out = []
    args = list(args)       # Make a copy because this func is destructive

    while len(args) > 0:
        a = args.pop(0)
        if '=' in a:
            opt, _ = a.split('=', 1)
            if opt in valopts:
                out.append(a)

        elif a in novalopts:
            out.append(a)

        elif a in valopts:
            if len(args) < 1:
                raise ValueError
            next = args.pop(0)
            if next[0] == '-':
                raise ValueError

            out.extend([a, next])
       
        else:
            # Check for single letter options that take a value, where the
            # value is right up against the option
            for opt in valopts:
                if len(opt) == 2 and a.startswith(opt):
                    out.append(a)

    return out

