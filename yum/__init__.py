#!/usr/bin/python -tt
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

"""
The Yum RPM software updater.
"""

import os
import os.path
import rpm
import re
import types
import errno
import time
import glob
import fnmatch
import operator
import gzip

try:
    from iniparse.compat import ParsingError, ConfigParser
except ImportError:
    from ConfigParser import ParsingError, ConfigParser
import Errors
import rpmsack
import rpmUtils.updates
import rpmUtils.arch
import rpmUtils.transaction
import comps
import config
from repos import RepoStorage
import misc
from parser import ConfigPreProcessor
import transactioninfo
import urlgrabber
from urlgrabber.grabber import URLGrabError
from packageSack import ListPackageSack
import depsolve
import plugins
import logginglevels
from logginglevels import info,info1,info2, warn,err,crit, dbg,dbg1,dbg2,dbg3
from logginglevels import vinfo,vinfo1,vinfo2, vwarn,verr,vcrit
from logginglevels import vdbg,vdbg1,vdbg2,vdbg3
import yumRepo
import callbacks

import warnings
warnings.simplefilter("ignore", Errors.YumFutureDeprecationWarning)

from packages import parsePackages, YumAvailablePackage, YumLocalPackage, YumInstalledPackage
from constants import *
from yum.rpmtrans import RPMTransaction,SimpleCliCallBack
from yum.i18n import _

import string

from urlgrabber.grabber import default_grabber as urlgrab

__version__ = '3.2.12'

class YumBase(depsolve.Depsolve):
    """This is a primary structure and base class. It houses the objects and
       methods needed to perform most things in yum. It is almost an abstract
       class in that you will need to add your own class above it for most
       real use."""
    
    def __init__(self):
        depsolve.Depsolve.__init__(self)
        self._conf = None
        self._tsInfo = None
        self._rpmdb = None
        self._up = None
        self._comps = None
        self._pkgSack = None
        
        self.log  = logginglevels.log
        self.vlog = logginglevels.vlog

        # FIXME: backwards compat. with plugins etc., remove in next API bump
        self.logger         = self.log.logger
        self.verbose_logger = self.vlog.logger
        
        self._repos = RepoStorage(self)

        # Start with plugins disabled
        self.disablePlugins()

        self.localPackages = [] # for local package handling

        self.mediagrabber = None

    def __del__(self):
        self.close()

    def close(self):
        if self._repos:
            self._repos.close()

    def _transactionDataFactory(self):
        """Factory method returning TransactionData object"""
        return transactioninfo.TransactionData()

    def doGenericSetup(self, cache=0):
        """do a default setup for all the normal/necessary yum components,
           really just a shorthand for testing"""
        
        self._getConfig(init_plugins=False)
        self.conf.cache = cache

    def doConfigSetup(self, fn='/etc/yum/yum.conf', root='/', init_plugins=True,
            plugin_types=(plugins.TYPE_CORE,), optparser=None, debuglevel=None,
            errorlevel=None):
        warnings.warn(_('doConfigSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)
                
        return self._getConfig(fn=fn, root=root, init_plugins=init_plugins,
             plugin_types=plugin_types, optparser=optparser, debuglevel=debuglevel,
             errorlevel=errorlevel)
        
    def _getConfig(self, fn='/etc/yum/yum.conf', root='/', init_plugins=True,
            plugin_types=(plugins.TYPE_CORE,), optparser=None, debuglevel=None,
            errorlevel=None,disabled_plugins=None):
        '''
        Parse and load Yum's configuration files and call hooks initialise
        plugins and logging.

        @param fn: Path to main configuration file to parse (yum.conf).
        @param root: Filesystem root to use.
        @param init_plugins: If False, plugins will not be loaded here. If
            True, plugins will be loaded if the "plugins" option is enabled in
            the configuration file.
        @param plugin_types: As per doPluginSetup()
        @param optparser: As per doPluginSetup()
        @param debuglevel: Debug level to use for logging. If None, the debug
            level will be read from the configuration file.
        @param errorlevel: Error level to use for logging. If None, the debug
            level will be read from the configuration file.
        @param disabled_plugins: Plugins to be disabled    
        '''

        if self._conf:
            return self._conf
        conf_st = time.time()            
        # TODO: Remove this block when we no longer support configs outside
        # of /etc/yum/
        if fn == '/etc/yum/yum.conf' and not os.path.exists(fn):
            # Try the old default
            fn = '/etc/yum.conf'

        startupconf = config.readStartupConfig(fn, root)

        
        if debuglevel != None:
            startupconf.debuglevel = debuglevel
        if errorlevel != None:
            startupconf.errorlevel = errorlevel

        self.doLoggingSetup(startupconf.debuglevel, startupconf.errorlevel)

        if init_plugins and startupconf.plugins:
            self.doPluginSetup(optparser, plugin_types, startupconf.pluginpath,
                    startupconf.pluginconfpath,disabled_plugins)

        self._conf = config.readMainConfig(startupconf)

        #  Setup a default_grabber UA here that says we are yum, done this way
        # so that other API users can add to it if they want.
        add_ua = " yum/" + __version__
        urlgrab.opts.user_agent += add_ua

        # run the postconfig plugin hook
        self.plugins.run('postconfig')
        self.yumvar = self.conf.yumvar

        self.getReposFromConfig()

        # who are we:
        self.conf.uid = os.geteuid()
        
        
        self.doFileLogSetup(self.conf.uid, self.conf.logfile)
        vdbg_tm(conf_st, 'Config')
        self.plugins.run('init')
        return self._conf
        

    def doLoggingSetup(self, debuglevel, errorlevel):
        '''
        Perform logging related setup.

        @param debuglevel: Debug logging level to use.
        @param errorlevel: Error logging level to use.
        '''
        logginglevels.doLoggingSetup(debuglevel, errorlevel)

    def doFileLogSetup(self, uid, logfile):
        logginglevels.setFileLog(uid, logfile)

    def getReposFromConfigFile(self, repofn, repo_age=None, validate=None):
        """read in repositories from a config .repo file"""

        if repo_age is None:
            repo_age = os.stat(repofn)[8]
        
        confpp_obj = ConfigPreProcessor(repofn, vars=self.yumvar)
        parser = ConfigParser()
        try:
            parser.readfp(confpp_obj)
        except ParsingError, e:
            msg = str(e)
            raise Errors.ConfigError, msg

        # Check sections in the .repo file that was just slurped up
        for section in parser.sections():

            if section == 'main':
                continue

            # Check the repo.id against the valid chars
            bad = None
            for byte in section:
                if byte in string.ascii_letters:
                    continue
                if byte in string.digits:
                    continue
                if byte in "-_.":
                    continue
                
                bad = byte
                break

            if bad:
                warn("Bad name for repo: %s, byte = %s %d",
                     section, bad, section.find(byte))
                continue

            try:
                thisrepo = self.readRepoConfig(parser, section)
            except (Errors.RepoError, Errors.ConfigError), e:
                warn(e)
                continue
            else:
                thisrepo.repo_config_age = repo_age
                thisrepo.repofile = repofn

            if validate and not validate(thisrepo):
                continue
                    
            # Got our list of repo objects, add them to the repos
            # collection
            try:
                self._repos.add(thisrepo)
            except Errors.RepoError, e:
                warn(e)
        
    def getReposFromConfig(self):
        """read in repositories from config main and .repo files"""

        # Read .repo files from directories specified by the reposdir option
        # (typically /etc/yum/repos.d)
        repo_config_age = self.conf.config_file_age
        
        # Get the repos from the main yum.conf file
        self.getReposFromConfigFile(self.conf.config_file_path, repo_config_age)

        for reposdir in self.conf.reposdir:
            if os.path.exists(self.conf.installroot+'/'+reposdir):
                reposdir = self.conf.installroot + '/' + reposdir

            if os.path.isdir(reposdir):
                for repofn in glob.glob('%s/*.repo' % reposdir):
                    thisrepo_age = os.stat(repofn)[8]
                    if thisrepo_age < repo_config_age:
                        thisrepo_age = repo_config_age
                    self.getReposFromConfigFile(repofn, repo_age=thisrepo_age)

    def readRepoConfig(self, parser, section):
        '''Parse an INI file section for a repository.

        @param parser: ConfParser or similar to read INI file values from.
        @param section: INI file section to read.
        @return: YumRepository instance.
        '''
        repo = yumRepo.YumRepository(section)
        repo.populate(parser, section, self.conf)

        # Ensure that the repo name is set
        if not repo.name:
            repo.name = section
            err(_('Repository %r is missing name in configuration, '
                  'using id'), section)

        # Set attributes not from the config file
        repo.basecachedir = self.conf.cachedir
        repo.yumvar.update(self.conf.yumvar)
        repo.cfg = parser

        return repo

    def disablePlugins(self):
        '''Disable yum plugins
        '''
        self.plugins = plugins.DummyYumPlugins()
    
    def doPluginSetup(self, optparser=None, plugin_types=None, searchpath=None,
            confpath=None,disabled_plugins=None):
        '''Initialise and enable yum plugins. 

        Note: _getConfig() will initialise plugins if instructed to. Only
        call this method directly if not calling _getConfig() or calling
        doConfigSetup(init_plugins=False).

        @param optparser: The OptionParser instance for this run (optional)
        @param plugin_types: A sequence specifying the types of plugins to load.
            This should be sequnce containing one or more of the
            yum.plugins.TYPE_...  constants. If None (the default), all plugins
            will be loaded.
        @param searchpath: A list of directories to look in for plugins. A
            default will be used if no value is specified.
        @param confpath: A list of directories to look in for plugin
            configuration files. A default will be used if no value is
            specified.
        @param disabled_plugins: Plugins to be disabled    
        '''
        if isinstance(self.plugins, plugins.YumPlugins):
            raise RuntimeError(_("plugins already initialised"))

        self.plugins = plugins.YumPlugins(self, searchpath, optparser,
                plugin_types, confpath, disabled_plugins)

    
    def doRpmDBSetup(self):
        warnings.warn(_('doRpmDBSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getRpmDB()
    
    def _getRpmDB(self):
        """sets up a holder object for important information from the rpmdb"""

        if self._rpmdb is None:
            rpmdb_st = time.time()
            vdbg(_('Reading Local RPMDB'))
            self._rpmdb = rpmsack.RPMDBPackageSack(root=self.conf.installroot)
            vdbg_tm(rpmdb_st, 'rpmdb')
        return self._rpmdb

    def closeRpmDB(self):
        """closes down the instances of the rpmdb we have wangling around"""
        self._rpmdb = None
        self._ts = None
        self._tsInfo = None
        self._up = None
        self.comps = None
    
    def _deleteTs(self):
        del self._ts
        self._ts = None

    def doRepoSetup(self, thisrepo=None):
        warnings.warn(_('doRepoSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getRepos(thisrepo, True)

    def _getRepos(self, thisrepo=None, doSetup = False):
        """ For each enabled repository set up the basics of the repository. """
        self._getConfig() # touch the config class first

        if doSetup:
            repo_st = time.time()        
            self._repos.doSetup(thisrepo)
            vdbg_tm(repo_st, 'repo')
        return self._repos

    def _delRepos(self):
        del self._repos
        self._repos = RepoStorage(self)
    
    def doSackSetup(self, archlist=None, thisrepo=None):
        warnings.warn(_('doSackSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getSacks(archlist=archlist, thisrepo=thisrepo)
        
    def _getSacks(self, archlist=None, thisrepo=None):
        """populates the package sacks for information from our repositories,
           takes optional archlist for archs to include"""

        if self._pkgSack and thisrepo is None:
            return self._pkgSack
        
        if thisrepo is None:
            repos = 'enabled'
        else:
            repos = self.repos.findRepos(thisrepo)
        
        vdbg(_('Setting up Package Sacks'))
        sack_st = time.time()
        if not archlist:
            archlist = rpmUtils.arch.getArchList()
        
        archdict = {}
        for arch in archlist:
            archdict[arch] = 1
        
        self.repos.getPackageSack().setCompatArchs(archdict)
        self.repos.populateSack(which=repos)
        self._pkgSack = self.repos.getPackageSack()
        
        self.excludePackages()
        self._pkgSack.excludeArchs(archlist)
        
        #FIXME - this could be faster, too.
        if repos == 'enabled':
            repos = self.repos.listEnabled()
        for repo in repos:
            self.excludePackages(repo)
            self.includePackages(repo)
        self.plugins.run('exclude')
        self._pkgSack.buildIndexes()

        # now go through and kill pkgs based on pkg.repo.cost()
        self.costExcludePackages()
        vdbg_tm(sack_st, 'pkgsack')
        return self._pkgSack
    
    
    def _delSacks(self):
        """reset the package sacks back to zero - making sure to nuke the ones
           in the repo objects, too - where it matters"""
           
        # nuke the top layer
        
        self._pkgSack = None
           
        for repo in self.repos.repos.values():
            if hasattr(repo, '_resetSack'):
                repo._resetSack()
            else:
                warnings.warn(_('repo object for repo %s lacks a _resetSack method\n') +
                        _('therefore this repo cannot be reset.\n'),
                        Errors.YumFutureDeprecationWarning, stacklevel=2)
            
           
    def doUpdateSetup(self):
        warnings.warn(_('doUpdateSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getUpdates()
        
    def _getUpdates(self):
        """setups up the update object in the base class and fills out the
           updates, obsoletes and others lists"""
        
        if self._up:
            return self._up
        
        vdbg(_('Building updates object'))
        sack_pkglist = self.pkgSack.simplePkgList()
        rpmdb_pkglist = self.rpmdb.simplePkgList()        

        up_st = time.time()
        self._up = rpmUtils.updates.Updates(rpmdb_pkglist, sack_pkglist)
        del rpmdb_pkglist
        del sack_pkglist
        if self.conf.debuglevel >= 6:
            self._up.debug = 1
        
        if self.conf.obsoletes:
            obs_init = time.time()    
            self._up.rawobsoletes = self.pkgSack.returnObsoletes(newest=True)
            vdbg_tm(obs_init, 'up:Obs Init')
            
        self._up.exactarch = self.conf.exactarch
        self._up.exactarchlist = self.conf.exactarchlist
        up_pr_st = time.time()
        self._up.doUpdates()
        vdbg_tm(up_pr_st, 'up:simple updates')

        if self.conf.obsoletes:
            obs_st = time.time()
            self._up.doObsoletes()
            vdbg_tm(obs_st, 'up:obs')

        cond_up_st = time.time()                    
        self._up.condenseUpdates()
        vdbg_tm(cond_up_st, 'up:condense')
        vdbg_tm(up_st, 'updates')
        return self._up
    
    def doGroupSetup(self):
        warnings.warn(_('doGroupSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        self.comps = None
        return self._getGroups()

    def _setGroups(self, val):
        if val is None:
            # if we unset the comps object, we need to undo which repos have
            # been added to the group file as well
            if self._repos:
                for repo in self._repos.listGroupsEnabled():
                    repo.groups_added = False
        self._comps = val
    
    def _getGroups(self):
        """create the groups object that will store the comps metadata
           finds the repos with groups, gets their comps data and merge it
           into the group object"""
        
        if self._comps:
            return self._comps

        group_st = time.time()            
        vdbg(_('Getting group metadata'))
        reposWithGroups = []
        self.repos.doSetup()
        for repo in self.repos.listGroupsEnabled():
            if repo.groups_added: # already added the groups from this repo
                reposWithGroups.append(repo)
                continue
                
            if not repo.ready():
                raise Errors.RepoError, "Repository '%s' not yet setup" % repo
            try:
                groupremote = repo.getGroupLocation()
            except Errors.RepoMDError, e:
                pass
            else:
                reposWithGroups.append(repo)
                
        # now we know which repos actually have groups files.
        overwrite = self.conf.overwrite_groups
        self._comps = comps.Comps(overwrite_groups = overwrite)

        for repo in reposWithGroups:
            if repo.groups_added: # already added the groups from this repo
                continue
                
            vdbg1(_('Adding group file from repository: %s'), repo)
            groupfile = repo.getGroups()
            # open it up as a file object so iterparse can cope with our gz file
            if groupfile is not None and groupfile.endswith('.gz'):
                groupfile = gzip.open(groupfile)
                
            try:
                self._comps.add(groupfile)
            except (Errors.GroupsError,Errors.CompsException), e:
                msg = _('Failed to add groups file for repository: %s - %s') % (repo, str(e))
                crit(msg)
            else:
                repo.groups_added = True

        if self._comps.compscount == 0:
            raise Errors.GroupsError, _('No Groups Available in any repository')
        
        pkglist = self.rpmdb.simplePkgList()
        self._comps.compile(pkglist)

        vdbg_tm(group_st, 'group')
        return self._comps
    
    # properties so they auto-create themselves with defaults
    repos = property(fget=lambda self: self._getRepos(),
                     fset=lambda self, value: setattr(self, "_repos", value),
                     fdel=lambda self: self._delRepos())
    pkgSack = property(fget=lambda self: self._getSacks(),
                       fset=lambda self, value: setattr(self, "_pkgSack", value),
                       fdel=lambda self: self._delSacks())
    conf = property(fget=lambda self: self._getConfig(),
                    fset=lambda self, value: setattr(self, "_conf", value),
                    fdel=lambda self: setattr(self, "_conf", None))
    rpmdb = property(fget=lambda self: self._getRpmDB(),
                     fset=lambda self, value: setattr(self, "_rpmdb", value),
                     fdel=lambda self: setattr(self, "_rpmdb", None))
    tsInfo = property(fget=lambda self: self._getTsInfo(), 
                      fset=lambda self,value: self._setTsInfo(value), 
                      fdel=lambda self: self._delTsInfo())
    ts = property(fget=lambda self: self._getActionTs(), fdel=lambda self: self._deleteTs())
    up = property(fget=lambda self: self._getUpdates(),
                  fset=lambda self, value: setattr(self, "_up", value),
                  fdel=lambda self: setattr(self, "_up", None))
    comps = property(fget=lambda self: self._getGroups(),
                     fset=lambda self, value: self._setGroups(value),
                     fdel=lambda self: setattr(self, "_comps", None))
    
    
    def doSackFilelistPopulate(self):
        """convenience function to populate the repos with the filelist metadata
           it also is simply to only emit a log if anything actually gets populated"""
        
        necessary = False
        
        # I can't think of a nice way of doing this, we have to have the sack here
        # first or the below does nothing so...
        if self.pkgSack:
            for repo in self.repos.listEnabled():
                if repo in repo.sack.added:
                    if 'filelists' in repo.sack.added[repo]:
                        continue
                    else:
                        necessary = True
                else:
                    necessary = True

        if necessary:
            msg = _('Importing additional filelist information')
            info2(msg)
            self.repos.populateSack(mdtype='filelists')
           
    def buildTransaction(self):
        """go through the packages in the transaction set, find them in the
           packageSack or rpmdb, and pack up the ts accordingly"""
        self.plugins.run('preresolve')
        ds_st = time.time()

        (rescode, restring) = self.resolveDeps()
        self.plugins.run('postresolve', rescode=rescode, restring=restring)
        self._limit_installonly_pkgs()
        
        if self.tsInfo.changed:
            (rescode, restring) = self.resolveDeps()

        # if depsolve failed and skipbroken is enabled
        # The remove the broken packages from the transactions and
        # Try another depsolve
        if self.conf.skip_broken and rescode==1:
            rescode, restring = self._skipPackagesWithProblems(rescode, restring)

        vdbg_tm(ds_st, 'Depsolve')
        return rescode, restring

    def _skipPackagesWithProblems(self, rescode, restring):
        ''' Remove the packages with depsolve errors and depsolve again '''

        def _remove(po, depTree, toRemove):
            if not po:
                return
            self._getPackagesToRemove(po, depTree, toRemove)
            # Only remove non installed packages from pkgSack
            if not po.repoid == 'installed':
                self.pkgSack.delPackage(po)
                self.up.delPackage(po.pkgtup)

        # Keep removing packages & Depsolve until all errors is gone
        # or the transaction is empty
        count = 0
        skipped_po = set()
        orig_restring = restring    # Keep the old error messages
        while len(self.po_with_problems) > 0 and rescode == 1:
            count += 1
            vdbg(_("Skip-broken round %i"), count)
            depTree = self._buildDepTree()
            startTs = set(self.tsInfo)
            toRemove = set()
            for po,wpo,err in self.po_with_problems:
                # check if the problem is caused by a package in the transaction
                if not self.tsInfo.exists(po.pkgtup):
                    _remove(wpo, depTree, toRemove)
                else:
                    _remove(po,  depTree, toRemove)
            for po in toRemove:
                skipped = self._skipFromTransaction(po)
                for skip in skipped:
                    skipped_po.add(skip)
            if not toRemove: # Nothing was removed, so we still got a problem
                break # Bail out
            rescode, restring = self.resolveDeps()
            endTs = set(self.tsInfo)
             # Check if tsInfo has changes since we started to skip packages
             # if there is no changes then we got a loop.
            if startTs-endTs == set():
                break    # bail out
        if rescode != 1:
            vdbg(_("Skip-broken took %i rounds "), count)
            vinfo(_('\nPackages skipped because of dependency problems:'))
            skipped_list = [p for p in skipped_po]
            skipped_list.sort()
            for po in skipped_list:
                msg = _("    %s from %s") % (str(po),po.repo.id)
                vinfo(msg)
        else:
            # If we cant solve the problems the show the original error messages.
            vinfo("Skip-broken could not solve problems")
            return 1, orig_restring
        
        return rescode, restring

    def _skipFromTransaction(self,po):
        skipped =  []
        if rpmUtils.arch.isMultiLibArch():
            archs = rpmUtils.arch.getArchList() 
            n,a,e,v,r = po.pkgtup
            # skip for all combat archs
            for a in archs:
                pkgtup = (n,a,e,v,r)
                if self.tsInfo.exists(pkgtup):
                    for txmbr in self.tsInfo.getMembers(pkgtup):
                        pkg = txmbr.po
                        skip = self._removePoFromTransaction(pkg)
                        skipped.extend(skip)
        else:
            msgs = self._removePoFromTransaction(po)
            skipped.extend(msgs)
        return skipped

    def _removePoFromTransaction(self,po):
        skip =  []
        if self.tsInfo.exists(po.pkgtup):
            self.tsInfo.remove(po.pkgtup)
            if not po.repoid == 'installed':
                skip.append(po)
        return skip 
              
    def _buildDepTree(self):
        ''' create a dictionary with po and deps '''
        depTree = { }
        for txmbr in self.tsInfo:
            for dep in txmbr.depends_on:
                depTree.setdefault(dep, []).append(txmbr.po)
        # self._printDepTree(depTree)
        return depTree

    def _printDepTree(self, tree):
        for pkg, l in tree.iteritems():
            print pkg
            for p in l:
                print "\t", p

    def _getPackagesToRemove(self,po,deptree,toRemove):
        '''
        get the (related) pos to remove.
        '''
        toRemove.add(po)
        for txmbr in self.tsInfo.getMembers(po.pkgtup):
            for pkg in (txmbr.updates + txmbr.obsoletes):
                toRemove.add(pkg)
                self._getDepsToRemove(pkg, deptree, toRemove)
        self._getDepsToRemove(po, deptree, toRemove)

    def _getDepsToRemove(self,po, deptree, toRemove):
        for dep in deptree.get(po, []): # Loop trough all deps of po
            for txmbr in self.tsInfo.getMembers(dep.pkgtup):
                for pkg in (txmbr.updates + txmbr.obsoletes):
                    toRemove.add(pkg)
            toRemove.add(dep)
            self._getDepsToRemove(dep, deptree, toRemove)

    def runTransaction(self, cb):
        """takes an rpm callback object, performs the transaction"""

        self.plugins.run('pretrans')

        errors = self.ts.run(cb.callback, '')
        if errors:
            raise Errors.YumBaseError, errors

        if not self.conf.keepcache:
            self.cleanUsedHeadersPackages()
        
        for i in ('ts_all_fn', 'ts_done_fn'):
            if hasattr(cb, i):
                fn = getattr(cb, i)
                if os.path.exists(fn):
                    try:
                        os.unlink(fn)
                    except (IOError, OSError), e:
                        crit(_('Failed to remove transaction file %s'), fn)

        self.plugins.run('posttrans')
    
    def costExcludePackages(self):
        """exclude packages if they have an identical package in another repo
        and their repo.cost value is the greater one"""
        
        # check to see if the cost per repo is anything other than equal
        # if all the repo.costs are equal then don't bother running things
        costs = {}
        for r in self.repos.listEnabled():
            costs[r.cost] = 1

        if len(costs) <= 1: # if all of our costs are the same then return
            return
            
        def _sort_by_cost(a, b):
            if a.repo.cost < b.repo.cost:
                return -1
            if a.repo.cost == b.repo.cost:
                return 0
            if a.repo.cost > b.repo.cost:
                return 1
                
        pkgdict = {}
        for po in self.pkgSack:
            if not pkgdict.has_key(po.pkgtup):
                pkgdict[po.pkgtup] = []
            pkgdict[po.pkgtup].append(po)
        
        for pkgs in pkgdict.values():
            if len(pkgs) == 1:
                continue
                
            pkgs.sort(_sort_by_cost)
            lowcost = pkgs[0].repo.cost
            #print '%s : %s : %s' % (pkgs[0], pkgs[0].repo, pkgs[0].repo.cost)
            for pkg in pkgs[1:]:
                if pkg.repo.cost > lowcost:
                    vdbg3(_('excluding for cost: %s from %s'), pkg, pkg.repo.id)
                    pkg.repo.sack.delPackage(pkg)
            

    def excludePackages(self, repo=None):
        """removes packages from packageSacks based on global exclude lists,
           command line excludes and per-repository excludes, takes optional 
           repo object to use."""

        if "all" in self.conf.disable_excludes:
            return
        
        # if not repo: then assume global excludes, only
        # if repo: then do only that repos' packages and excludes
        
        if not repo: # global only
            if "main" in self.conf.disable_excludes:
                return
            excludelist = self.conf.exclude
            repoid = None
        else:
            if repo.id in self.conf.disable_excludes:
                return
            excludelist = repo.getExcludePkgList()
            repoid = repo.id

        if len(excludelist) == 0:
            return

        if not repo:
            vinfo2(_('Excluding Packages in global exclude list'))
        else:
            vinfo2(_('Excluding Packages from %s'), repo.name)

        pkgs = self._pkgSack.returnPackages(repoid, patterns=excludelist)
        exactmatch, matched, unmatched = \
           parsePackages(pkgs, excludelist, casematch=1, unique='repo-pkgkey')

        for po in exactmatch + matched:
            vdbg('Excluding %s', po)
            po.repo.sack.delPackage(po)
            
        
        vinfo2('Finished')

    def includePackages(self, repo):
        """removes packages from packageSacks based on list of packages, to include.
           takes repoid as a mandatory argument."""
        
        includelist = repo.getIncludePkgList()
        
        if len(includelist) == 0:
            return
        
        pkglist = self.pkgSack.returnPackages(repo.id)
        exactmatch, matched, unmatched = \
           parsePackages(pkglist, includelist, casematch=1)
        
        vinfo2(_('Reducing %s to included packages only'), repo.name)
        rmlist = []
        
        for po in pkglist:
            if po in exactmatch + matched:
                vdbg(_('Keeping included package %s'), po)
                continue
            else:
                rmlist.append(po)
        
        for po in rmlist:
            vdbg(_('Removing unmatched package %s'), po)
            po.repo.sack.delPackage(po)
            
        vinfo2(_('Finished'))
        
    def doLock(self, lockfile = YUM_PID_FILE):
        """perform the yum locking, raise yum-based exceptions, not OSErrors"""
        
        # if we're not root then we don't lock - just return nicely
        if self.conf.uid != 0:
            return
            
        root = self.conf.installroot
        lockfile = root + '/' + lockfile # lock in the chroot
        lockfile = os.path.normpath(lockfile) # get rid of silly preceding extra /
        
        mypid=str(os.getpid())    
        while not self._lock(lockfile, mypid, 0644):
            fd = open(lockfile, 'r')
            try: oldpid = int(fd.readline())
            except ValueError:
                # bogus data in the pid file. Throw away.
                self._unlock(lockfile)
            else:
                if oldpid == os.getpid(): # if we own the lock, we're fine
                    return
                try: os.kill(oldpid, 0)
                except OSError, e:
                    if e[0] == errno.ESRCH:
                        # The pid doesn't exist
                        self._unlock(lockfile)
                    else:
                        # Whoa. What the heck happened?
                        msg = _('Unable to check if PID %s is active') % oldpid
                        raise Errors.LockError(1, msg)
                else:
                    # Another copy seems to be running.
                    msg = _('Existing lock %s: another copy is running as pid %s.') % (lockfile, oldpid)
                    raise Errors.LockError(0, msg)
    
    def doUnlock(self, lockfile = YUM_PID_FILE):
        """do the unlock for yum"""
        
        # if we're not root then we don't lock - just return nicely
        if self.conf.uid != 0:
            return
        
        root = self.conf.installroot
        lockfile = root + '/' + lockfile # lock in the chroot
        
        self._unlock(lockfile)
        
    def _lock(self, filename, contents='', mode=0777):
        lockdir = os.path.dirname(filename)
        try:
            if not os.path.exists(lockdir):
                os.makedirs(lockdir, mode=0755)
            fd = os.open(filename, os.O_EXCL|os.O_CREAT|os.O_WRONLY, mode)    
        except OSError, msg:
            if not msg.errno == errno.EEXIST: raise msg
            return 0
        else:
            os.write(fd, contents)
            os.close(fd)
            return 1
    
    def _unlock(self, filename):
        try:
            os.unlink(filename)
        except OSError, msg:
            pass


    def verifyPkg(self, fo, po, raiseError):
        """verifies the package is what we expect it to be
           raiseError  = defaults to 0 - if 1 then will raise
           a URLGrabError if the file does not check out.
           otherwise it returns false for a failure, true for success"""

        if type(fo) is types.InstanceType:
            fo = fo.filename
            
        if not po.verifyLocalPkg():
            if raiseError:
                raise URLGrabError(-1, _('Package does not match intended download'))
            else:
                return False

        ylp = YumLocalPackage(self.rpmdb.readOnlyTS(), fo)
        if ylp.pkgtup != po.pkgtup:
            if raiseError:
                raise URLGrabError(-1, _('Package does not match intended download'))
            else:
                return False
        
        return True
        
        
    def verifyChecksum(self, fo, checksumType, csum):
        """Verify the checksum of the file versus the 
           provided checksum"""

        try:
            filesum = misc.checksum(checksumType, fo)
        except Errors.MiscError, e:
            raise URLGrabError(-3, _('Could not perform checksum'))
            
        if filesum != csum:
            raise URLGrabError(-1, _('Package does not match checksum'))
        
        return 0
            
           
    def downloadPkgs(self, pkglist, callback=None):
        def mediasort(a, b):
            # FIXME: we should probably also use the mediaid; else we
            # could conceivably ping-pong between different disc1's
            a = a.getDiscNum()
            b = b.getDiscNum()
            if a is None:
                return -1
            if b is None:
                return 1
            if a < b:
                return -1
            elif a > b:
                return 1
            return 0
        
        """download list of package objects handed to you, output based on
           callback, raise yum.Errors.YumBaseError on problems"""

        errors = {}
        def adderror(po, msg):
            errors.setdefault(po, []).append(msg)

        self.plugins.run('predownload', pkglist=pkglist)
        repo_cached = False
        remote_pkgs = []
        for po in pkglist:
            if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
                continue
                    
            local = po.localPkg()
            if os.path.exists(local):
                cursize = os.stat(local)[6]
                totsize = long(po.size)
                if not po.verifyLocalPkg():
                    if po.repo.cache:
                        repo_cached = True
                        adderror(po, _('package fails checksum but caching is '
                            'enabled for %s') % po.repo.id)
                        
                    if cursize >= totsize: # otherwise keep it around for regetting
                        os.unlink(local)
                else:
                    vdbg(_("using local copy of %s"), po)
                    continue
                        
            remote_pkgs.append(po)
            
            # caching is enabled and the package 
            # just failed to check out there's no 
            # way to save this, report the error and return
            if (self.conf.cache or repo_cached) and errors:
                return errors
                

        remote_pkgs.sort(mediasort)
        i = 0
        for po in remote_pkgs:
            i += 1
            checkfunc = (self.verifyPkg, (po, 1), {})
            dirstat = os.statvfs(po.repo.pkgdir)
            if (dirstat.f_bavail * dirstat.f_bsize) <= long(po.size):
                adderror(po, _('Insufficient space in download directory %s '
                        'to download') % po.repo.pkgdir)
                continue
            
            try:
                text = '(%s/%s): %s' % (i, len(remote_pkgs),
                                        os.path.basename(po.relativepath))
                mylocal = po.repo.getPackage(po,
                                   checkfunc=checkfunc,
                                   text=text,
                                   cache=po.repo.http_caching != 'none',
                                   )
            except Errors.RepoError, e:
                adderror(po, str(e))
            else:
                po.localpath = mylocal
                if errors.has_key(po):
                    del errors[po]

        self.plugins.run('postdownload', pkglist=pkglist, errors=errors)

        return errors

    def verifyHeader(self, fo, po, raiseError):
        """check the header out via it's naevr, internally"""
        if type(fo) is types.InstanceType:
            fo = fo.filename
            
        try:
            hlist = rpm.readHeaderListFromFile(fo)
            hdr = hlist[0]
        except (rpm.error, IndexError):
            if raiseError:
                raise URLGrabError(-1, _('Header is not complete.'))
            else:
                return 0
                
        yip = YumInstalledPackage(hdr) # we're using YumInstalledPackage b/c
                                       # it takes headers <shrug>
        if yip.pkgtup != po.pkgtup:
            if raiseError:
                raise URLGrabError(-1, 'Header does not match intended download')
            else:
                return 0
        
        return 1
        
    def downloadHeader(self, po):
        """download a header from a package object.
           output based on callback, raise yum.Errors.YumBaseError on problems"""

        if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
            return
                
        errors = {}
        local =  po.localHdr()
        repo = self.repos.getRepo(po.repoid)
        if os.path.exists(local):
            try:
                result = self.verifyHeader(local, po, raiseError=1)
            except URLGrabError, e:
                # might add a check for length of file - if it is < 
                # required doing a reget
                try:
                    os.unlink(local)
                except OSError, e:
                    pass
            else:
                po.hdrpath = local
                return
        else:
            if self.conf.cache:
                raise Errors.RepoError, \
                _('Header not in local cache and caching-only mode enabled. Cannot download %s') % po.hdrpath
        
        if self.dsCallback: self.dsCallback.downloadHeader(po.name)
        
        try:
            if not os.path.exists(repo.hdrdir):
                os.makedirs(repo.hdrdir)
            checkfunc = (self.verifyHeader, (po, 1), {})
            hdrpath = repo.getHeader(po, checkfunc=checkfunc,
                    cache=repo.http_caching != 'none',
                    )
        except Errors.RepoError, e:
            saved_repo_error = e
            try:
                os.unlink(local)
            except OSError, e:
                raise Errors.RepoError, saved_repo_error
            else:
                raise
        else:
            po.hdrpath = hdrpath
            return

    def sigCheckPkg(self, po):
        '''
        Take a package object and attempt to verify GPG signature if required

        Returns (result, error_string) where result is:
            - 0 - GPG signature verifies ok or verification is not required.
            - 1 - GPG verification failed but installation of the right GPG key
                  might help.
            - 2 - Fatal GPG verifcation error, give up.
        '''
        if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
            check = self.conf.gpgcheck
            hasgpgkey = 0
        else:
            repo = self.repos.getRepo(po.repoid)
            check = repo.gpgcheck
            hasgpgkey = not not repo.gpgkey 
        
        if check:
            ts = self.rpmdb.readOnlyTS()
            sigresult = rpmUtils.miscutils.checkSig(ts, po.localPkg())
            localfn = os.path.basename(po.localPkg())
            
            if sigresult == 0:
                result = 0
                msg = ''

            elif sigresult == 1:
                if hasgpgkey:
                    result = 1
                else:
                    result = 2
                msg = _('Public key for %s is not installed') % localfn

            elif sigresult == 2:
                result = 2
                msg = _('Problem opening package %s') % localfn

            elif sigresult == 3:
                if hasgpgkey:
                    result = 1
                else:
                    result = 2
                result = 1
                msg = _('Public key for %s is not trusted') % localfn

            elif sigresult == 4:
                result = 2 
                msg = _('Package %s is not signed') % localfn
            
        else:
            result =0
            msg = ''

        return result, msg

    def cleanUsedHeadersPackages(self):
        filelist = []
        for txmbr in self.tsInfo:
            if txmbr.po.state not in TS_INSTALL_STATES:
                continue
            if txmbr.po.repoid == "installed":
                continue
            if not self.repos.repos.has_key(txmbr.po.repoid):
                continue
            
            # make sure it's not a local file
            repo = self.repos.repos[txmbr.po.repoid]
            local = False
            for u in repo.baseurl:
                if u.startswith("file:"):
                    local = True
                    break
                
            if local:
                filelist.extend([txmbr.po.localHdr()])
            else:
                filelist.extend([txmbr.po.localPkg(), txmbr.po.localHdr()])

        # now remove them
        for fn in filelist:
            if not os.path.exists(fn):
                continue
            try:
                os.unlink(fn)
            except OSError, e:
                warn(_('Cannot remove %s'), fn)
                continue
            else:
                vdbg4(_('%s removed'), fn)
        
    def cleanHeaders(self):
        exts = ['hdr']
        return self._cleanFiles(exts, 'hdrdir', 'header')

    def cleanPackages(self):
        exts = ['rpm']
        return self._cleanFiles(exts, 'pkgdir', 'package')

    def cleanSqlite(self):
        exts = ['sqlite', 'sqlite.bz2']
        return self._cleanFiles(exts, 'cachedir', 'sqlite')

    def cleanMetadata(self):
        exts = ['xml.gz', 'xml', 'cachecookie', 'mirrorlist.txt']
        return self._cleanFiles(exts, 'cachedir', 'metadata') 

    def _cleanFiles(self, exts, pathattr, filetype):
        filelist = []
        removed = 0
        for ext in exts:
            for repo in self.repos.listEnabled():
                repo.dirSetup()
                path = getattr(repo, pathattr)
                if os.path.exists(path) and os.path.isdir(path):
                    filelist = misc.getFileList(path, ext, filelist)

        for item in filelist:
            try:
                os.unlink(item)
            except OSError, e:
                crit(_('Cannot remove %s file %s'), filetype, item)
                continue
            else:
                vdbg4(_('%s file %s removed'), filetype, item)
                removed+=1
        msg = _('%d %s files removed') % (removed, filetype)
        return 0, [msg]

    def doPackageLists(self, pkgnarrow='all', patterns=None):
        """generates lists of packages, un-reduced, based on pkgnarrow option"""
        
        ygh = misc.GenericHolder()
        
        installed = []
        available = []
        updates = []
        obsoletes = []
        obsoletesTuples = []
        recent = []
        extras = []

        # list all packages - those installed and available, don't 'think about it'
        if pkgnarrow == 'all': 
            dinst = {}
            for po in self.rpmdb:
                dinst[po.pkgtup] = po;
            installed = dinst.values()
                        
            if self.conf.showdupesfromrepos:
                avail = self.pkgSack.returnPackages(patterns=patterns)
            else:
                avail = self.pkgSack.returnNewestByNameArch(patterns=patterns)
            
            for pkg in avail:
                if not dinst.has_key(pkg.pkgtup):
                    available.append(pkg)

        # produce the updates list of tuples
        elif pkgnarrow == 'updates':
            for (n,a,e,v,r) in self.up.getUpdatesList():
                matches = self.pkgSack.searchNevra(name=n, arch=a, epoch=e, 
                                                   ver=v, rel=r)
                if len(matches) > 1:
                    updates.append(matches[0])
                    vdbg1(_('More than one identical match in sack for %s'), 
                          matches[0])
                elif len(matches) == 1:
                    updates.append(matches[0])
                else:
                    vdbg1(_('Nothing matches %s.%s %s:%s-%s from update'),
                          n,a,e,v,r)

        # installed only
        elif pkgnarrow == 'installed':
            installed = self.rpmdb.returnPackages(patterns=patterns)
        
        # available in a repository
        elif pkgnarrow == 'available':

            if self.conf.showdupesfromrepos:
                avail = self.pkgSack.returnPackages(patterns=patterns)
            else:
                avail = self.pkgSack.returnNewestByNameArch(patterns=patterns)
            
            for pkg in avail:
                if not self.rpmdb.contains(po=pkg):
                    available.append(pkg)


        # not in a repo but installed
        elif pkgnarrow == 'extras':
            # we must compare the installed set versus the repo set
            # anything installed but not in a repo is an extra
            avail = self.pkgSack.simplePkgList(patterns=patterns)
            for po in self.rpmdb:
                if po.pkgtup not in avail:
                    extras.append(po)

        # obsoleting packages (and what they obsolete)
        elif pkgnarrow == 'obsoletes':
            self.conf.obsoletes = 1

            for (pkgtup, instTup) in self.up.getObsoletesTuples():
                (n,a,e,v,r) = pkgtup
                pkgs = self.pkgSack.searchNevra(name=n, arch=a, ver=v, rel=r, epoch=e)
                instpo = self.rpmdb.searchPkgTuple(instTup)[0] # the first one
                for po in pkgs:
                    obsoletes.append(po)
                    obsoletesTuples.append((po, instpo))
        
        # packages recently added to the repositories
        elif pkgnarrow == 'recent':
            now = time.time()
            recentlimit = now-(self.conf.recent*86400)
            ftimehash = {}
            if self.conf.showdupesfromrepos:
                avail = self.pkgSack.returnPackages(patterns=patterns)
            else:
                avail = self.pkgSack.returnNewestByNameArch(patterns=patterns)
            
            for po in avail:
                ftime = int(po.filetime)
                if ftime > recentlimit:
                    if not ftimehash.has_key(ftime):
                        ftimehash[ftime] = [po]
                    else:
                        ftimehash[ftime].append(po)

            for sometime in ftimehash:
                for po in ftimehash[sometime]:
                    recent.append(po)
        
        
        ygh.installed = installed
        ygh.available = available
        ygh.updates = updates
        ygh.obsoletes = obsoletes
        ygh.obsoletesTuples = obsoletesTuples
        ygh.recent = recent
        ygh.extras = extras

        
        return ygh


        
    def findDeps(self, pkgs):
        """
        Return the dependencies for a given package object list, as well
        possible solutions for those dependencies.
           
        Returns the deps as a dict of dicts::
            packageobject = [reqs] = [list of satisfying pkgs]
        """
        
        results = {}

        for pkg in pkgs:
            results[pkg] = {} 
            reqs = pkg.requires
            reqs.sort()
            pkgresults = results[pkg] # shorthand so we don't have to do the
                                      # double bracket thing
            
            for req in reqs:
                (r,f,v) = req
                if r.startswith('rpmlib('):
                    continue
                
                satisfiers = []

                for po in self.whatProvides(r, f, v):
                    satisfiers.append(po)

                pkgresults[req] = satisfiers
        
        return results
    
    # pre 3.2.10 API used to always showdups, so that's the default atm.
    def searchGenerator(self, fields, criteria, showdups=True):
        """Generator method to lighten memory load for some searches.
           This is the preferred search function to use."""
        sql_fields = []
        for f in fields:
            if RPM_TO_SQLITE.has_key(f):
                sql_fields.append(RPM_TO_SQLITE[f])
            else:
                sql_fields.append(f)

        matched_values = {}

        # yield the results in order of most terms matched first
        sorted_lists = {}
        tmpres = []
        real_crit = []
        for s in criteria:
            if s.find('%') == -1:
                real_crit.append(s)
        real_crit_lower = [] # Take the s.lower()'s out of the loop
        for s in criteria:
            if s.find('%') == -1:
                real_crit_lower.append(s.lower())

        for sack in self.pkgSack.sacks.values():
            tmpres.extend(sack.searchPrimaryFieldsMultipleStrings(sql_fields, real_crit))

        for (po, count) in tmpres:
            # check the pkg for sanity
            # pop it into the sorted lists
            tmpvalues = []
            if count not in sorted_lists: sorted_lists[count] = []
            for s in real_crit_lower:
                for field in fields:
                    value = getattr(po, field)
                    if value and value.lower().find(s) != -1:
                        tmpvalues.append(value)

            if len(tmpvalues) > 0:
                sorted_lists[count].append((po, tmpvalues))

            
        
        for po in self.rpmdb:
            tmpvalues = []
            criteria_matched = 0
            for s in real_crit_lower:
                matched_s = False
                for field in fields:
                    value = getattr(po, field)
                    # make sure that string are in unicode
                    if isinstance(value, str):
                        value = unicode(value,'unicode-escape')
                    if value and value.lower().find(s) != -1:
                        if not matched_s:
                            criteria_matched += 1
                            matched_s = True
                        
                        tmpvalues.append(value)


            if len(tmpvalues) > 0:
                if criteria_matched not in sorted_lists: sorted_lists[criteria_matched] = []
                sorted_lists[criteria_matched].append((po, tmpvalues))
                

        # close our rpmdb connection so we can ctrl-c, kthxbai                    
        self.closeRpmDB()
        
        yielded = {}
        for val in reversed(sorted(sorted_lists)):
            for (po, matched) in sorted(sorted_lists[val], key=operator.itemgetter(0)):
                if (po.name, po.arch) not in yielded:
                    yield (po, matched)
                    if not showdups:
                        yielded[(po.name, po.arch)] = 1


    def searchPackages(self, fields, criteria, callback=None):
        """Search specified fields for matches to criteria
           optional callback specified to print out results
           as you go. Callback is a simple function of:
           callback(po, matched values list). It will 
           just return a dict of dict[po]=matched values list"""
        warnings.warn(_('searchPackages() will go away in a future version of Yum.\
                      Use searchGenerator() instead. \n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)           
        matches = {}
        match_gen = self.searchGenerator(fields, criteria)
        
        for (po, matched_strings) in match_gen:
            if callback:
                callback(po, matched_strings)
            if not matches.has_key(po):
                matches[po] = []
            
            matches[po].extend(matched_strings)
        
        return matches
    
    def searchPackageProvides(self, args, callback=None):
        
        matches = {}
        for arg in args:
            if not re.match('.*[\*\?\[\]].*', arg):
                isglob = False
                if arg[0] != '/':
                    canBeFile = False
                else:
                    canBeFile = True
            else:
                isglob = True
                canBeFile = True
                
            if not isglob:
                usedDepString = True
                where = self.returnPackagesByDep(arg)
            else:
                usedDepString = False
                where = self.pkgSack.searchAll(arg, False)
            vdbg1(_('Searching %d packages'), len(where))
            
            for po in where:
                vdbg2(_('searching package %s'), po)
                tmpvalues = []
                
                if usedDepString:
                    tmpvalues.append(arg)

                if not isglob and canBeFile:
                    # then it is not a globbed file we have matched it precisely
                    tmpvalues.append(arg)
                    
                if isglob:
                    vdbg2(_('searching in file entries'))
                    for thisfile in po.dirlist + po.filelist + po.ghostlist:
                        if fnmatch.fnmatch(thisfile, arg):
                            tmpvalues.append(thisfile)
                

                vdbg2(_('searching in provides entries'))
                for (p_name, p_flag, (p_e, p_v, p_r)) in po.provides:
                    prov = misc.prco_tuple_to_string((p_name, p_flag, (p_e, p_v, p_r)))
                    if not usedDepString:
                        if fnmatch.fnmatch(p_name, arg) or fnmatch.fnmatch(prov, arg):
                            tmpvalues.append(prov)

                if len(tmpvalues) > 0:
                    if callback:
                        callback(po, tmpvalues)
                    matches[po] = tmpvalues
        
        # installed rpms, too
        taglist = ['filelist', 'dirnames', 'provides_names']
        for arg in args:
            if not re.match('.*[\*\?\[\]].*', arg):
                isglob = False
                if arg[0] != '/':
                    canBeFile = False
                else:
                    canBeFile = True
            else:
                isglob = True
                canBeFile = True
            
            if not isglob:
                where = self.returnInstalledPackagesByDep(arg)
                usedDepString = True
                for po in where:
                    tmpvalues = []
                    msg = _('Provides-match: %s') % arg
                    tmpvalues.append(msg)

                    if len(tmpvalues) > 0:
                        if callback:
                            callback(po, tmpvalues)
                        matches[po] = tmpvalues

            else:
                usedDepString = False
                where = self.rpmdb
                
                for po in where:
                    searchlist = []
                    tmpvalues = []
                    for tag in taglist:
                        tagdata = getattr(po, tag)
                        if tagdata is None:
                            continue
                        if type(tagdata) is types.ListType:
                            searchlist.extend(tagdata)
                        else:
                            searchlist.append(tagdata)
                    
                    for item in searchlist:
                        if fnmatch.fnmatch(item, arg):
                            tmpvalues.append(item)
                
                    if len(tmpvalues) > 0:
                        if callback:
                            callback(po, tmpvalues)
                        matches[po] = tmpvalues
            
            
        return matches

    def doGroupLists(self, uservisible=0):
        """returns two lists of groups, installed groups and available groups
           optional 'uservisible' bool to tell it whether or not to return
           only groups marked as uservisible"""
        
        
        installed = []
        available = []
        
        for grp in self.comps.groups:
            if grp.installed:
                if uservisible:
                    if grp.user_visible:
                        installed.append(grp)
                else:
                    installed.append(grp)
            else:
                if uservisible:
                    if grp.user_visible:
                        available.append(grp)
                else:
                    available.append(grp)
            
        return installed, available
    
    
    def groupRemove(self, grpid):
        """mark all the packages in this group to be removed"""
        
        txmbrs_used = []
        
        thisgroup = self.comps.return_group(grpid)
        if not thisgroup:
            raise Errors.GroupsError, _("No Group named %s exists") % grpid

        thisgroup.toremove = True
        pkgs = thisgroup.packages
        for pkg in thisgroup.packages:
            txmbrs = self.remove(name=pkg)
            txmbrs_used.extend(txmbrs)
            for txmbr in txmbrs:
                txmbr.groups.append(thisgroup.groupid)
        
        return txmbrs_used

    def groupUnremove(self, grpid):
        """unmark any packages in the group from being removed"""
        

        thisgroup = self.comps.return_group(grpid)
        if not thisgroup:
            raise Errors.GroupsError, _("No Group named %s exists") % grpid

        thisgroup.toremove = False
        pkgs = thisgroup.packages
        for pkg in thisgroup.packages:
            for txmbr in self.tsInfo:
                if txmbr.po.name == pkg and txmbr.po.state in TS_INSTALL_STATES:
                    try:
                        txmbr.groups.remove(grpid)
                    except ValueError:
                        vdbg1(_("package %s was not marked in group %s"),
                              txmbr.po, grpid)
                        continue
                    
                    # if there aren't any other groups mentioned then remove the pkg
                    if len(txmbr.groups) == 0:
                        self.tsInfo.remove(txmbr.po.pkgtup)
        
        
    def selectGroup(self, grpid):
        """mark all the packages in the group to be installed
           returns a list of transaction members it added to the transaction 
           set"""
        
        txmbrs_used = []
        
        if not self.comps.has_group(grpid):
            raise Errors.GroupsError, _("No Group named %s exists") % grpid
            
        thisgroup = self.comps.return_group(grpid)
        
        if not thisgroup:
            raise Errors.GroupsError, _("No Group named %s exists") % grpid
        
        if thisgroup.selected:
            return txmbrs_used
        
        thisgroup.selected = True
        
        pkgs = []
        if 'mandatory' in self.conf.group_package_types:
            pkgs.extend(thisgroup.mandatory_packages)
        if 'default' in self.conf.group_package_types:
            pkgs.extend(thisgroup.default_packages)
        if 'optional' in self.conf.group_package_types:
            pkgs.extend(thisgroup.optional_packages)

        for pkg in pkgs:
            vdbg2(_('Adding package %s from group %s'), pkg, thisgroup.groupid)
            try:
                txmbrs = self.install(name = pkg)
            except Errors.InstallError, e:
                vdbg(_('No package named %s available to be installed'), pkg)
            else:
                txmbrs_used.extend(txmbrs)
                for txmbr in txmbrs:
                    txmbr.groups.append(thisgroup.groupid)
        
        if self.conf.enable_group_conditionals:
            for condreq, cond in thisgroup.conditional_packages.iteritems():
                if self.isPackageInstalled(cond):
                    try:
                        txmbrs = self.install(name = condreq)
                    except Errors.InstallError:
                        # we don't care if the package doesn't exist
                        continue
                    txmbrs_used.extend(txmbrs)
                    for txmbr in txmbrs:
                        txmbr.groups.append(thisgroup.groupid)
                    continue
                # Otherwise we hook into tsInfo.add
                pkgs = self.pkgSack.searchNevra(name=condreq)
                if pkgs:
                    pkgs = self.bestPackagesFromList(pkgs)
                if self.tsInfo.conditionals.has_key(cond):
                    self.tsInfo.conditionals[cond].extend(pkgs)
                else:
                    self.tsInfo.conditionals[cond] = pkgs

        return txmbrs_used

    def deselectGroup(self, grpid):
        """de-mark all the packages in the group for install"""
        
        if not self.comps.has_group(grpid):
            raise Errors.GroupsError, _("No Group named %s exists") % grpid
            
        thisgroup = self.comps.return_group(grpid)
        if not thisgroup:
            raise Errors.GroupsError, _("No Group named %s exists") % grpid
        
        thisgroup.selected = False
        
        for pkgname in thisgroup.packages:
        
            for txmbr in self.tsInfo:
                if txmbr.po.name == pkgname and txmbr.po.state in TS_INSTALL_STATES:
                    try: 
                        txmbr.groups.remove(grpid)
                    except ValueError:
                        vdbg1(_("package %s was not marked in group %s"),
                              txmbr.po, grpid)
                        continue
                    
                    # if there aren't any other groups mentioned then remove the pkg
                    if len(txmbr.groups) == 0:
                        self.tsInfo.remove(txmbr.po.pkgtup)

                    
        
    def getPackageObject(self, pkgtup):
        """retrieves a packageObject from a pkgtuple - if we need
           to pick and choose which one is best we better call out
           to some method from here to pick the best pkgobj if there are
           more than one response - right now it's more rudimentary."""
           
        
        # look it up in the self.localPackages first:
        for po in self.localPackages:
            if po.pkgtup == pkgtup:
                return po
                
        pkgs = self.pkgSack.searchPkgTuple(pkgtup)

        if len(pkgs) == 0:
            raise Errors.DepError, _('Package tuple %s could not be found in packagesack') % str(pkgtup)
            return None
            
        if len(pkgs) > 1: # boy it'd be nice to do something smarter here FIXME
            result = pkgs[0]
        else:
            result = pkgs[0] # which should be the only
        
            # this is where we could do something to figure out which repository
            # is the best one to pull from
        
        return result

    def getInstalledPackageObject(self, pkgtup):
        """returns a YumInstallPackage object for the pkgtup specified"""
        
        #FIXME - this should probably emit a deprecation warning telling
        # people to just use the command below
        
        po = self.rpmdb.searchPkgTuple(pkgtup)[0] # take the first one
        return po
        
    def gpgKeyCheck(self):
        """checks for the presence of gpg keys in the rpmdb
           returns 0 if no keys returns 1 if keys"""

        gpgkeyschecked = self.conf.cachedir + '/.gpgkeyschecked.yum'
        if os.path.exists(gpgkeyschecked):
            return 1
            
        myts = rpmUtils.transaction.initReadOnlyTransaction(root=self.conf.installroot)
        myts.pushVSFlags(~(rpm._RPMVSF_NOSIGNATURES|rpm._RPMVSF_NODIGESTS))
        idx = myts.dbMatch('name', 'gpg-pubkey')
        keys = idx.count()
        del idx
        del myts
        
        if keys == 0:
            return 0
        else:
            mydir = os.path.dirname(gpgkeyschecked)
            if not os.path.exists(mydir):
                os.makedirs(mydir)
                
            fo = open(gpgkeyschecked, 'w')
            fo.close()
            del fo
            return 1

    def returnPackagesByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the packages it finds providing that dep."""
        
        results = []
        # parse the string out
        #  either it is 'dep (some operator) e:v-r'
        #  or /file/dep
        #  or packagename
        depname = depstring
        depflags = None
        depver = None
        
        if depstring[0] != '/':
            # not a file dep - look at it for being versioned
            if re.search('[>=<]', depstring):  # versioned
                try:
                    depname, flagsymbol, depver = depstring.split()
                except ValueError, e:
                    raise Errors.YumBaseError, _('Invalid versioned dependency string, try quoting it.')
                if not SYMBOLFLAGS.has_key(flagsymbol):
                    raise Errors.YumBaseError, _('Invalid version flag')
                depflags = SYMBOLFLAGS[flagsymbol]
                
        sack = self.whatProvides(depname, depflags, depver)
        results = sack.returnPackages()
        return results
        

    def returnPackageByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the best(or first) package it finds providing that dep."""
        
        try:
            pkglist = self.returnPackagesByDep(depstring)
        except Errors.YumBaseError:
            raise Errors.YumBaseError, _('No Package found for %s') % depstring
        
        result = self._bestPackageFromList(pkglist)
        if result is None:
            raise Errors.YumBaseError, _('No Package found for %s') % depstring
        
        return result

    def returnInstalledPackagesByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the installed packages it finds providing that dep."""
        
        # parse the string out
        #  either it is 'dep (some operator) e:v-r'
        #  or /file/dep
        #  or packagename
        depname = depstring
        depflags = None
        depver = None
        
        if depstring[0] != '/':
            # not a file dep - look at it for being versioned
            if re.search('[>=<]', depstring):  # versioned
                try:
                    depname, flagsymbol, depver = depstring.split()
                except ValueError:
                    raise Errors.YumBaseError, _('Invalid versioned dependency string, try quoting it.')
                if not SYMBOLFLAGS.has_key(flagsymbol):
                    raise Errors.YumBaseError, _('Invalid version flag')
                depflags = SYMBOLFLAGS[flagsymbol]
        
        return self.rpmdb.getProvides(depname, depflags, depver).keys()

    def _bestPackageFromList(self, pkglist):
        """take list of package objects and return the best package object.
           If the list is empty, return None. 
           
           Note: this is not aware of multilib so make sure you're only
           passing it packages of a single arch group."""
        
        
        if len(pkglist) == 0:
            return None
            
        if len(pkglist) == 1:
            return pkglist[0]
        
        mysack = ListPackageSack()
        mysack.addList(pkglist)
        bestlist = mysack.returnNewestByNameArch() # get rid of all lesser vers
        
        best = bestlist[0]
        for pkg in bestlist[1:]:
            if len(pkg.name) < len(best.name): # shortest name silliness
                best = pkg
                continue
            elif len(pkg.name) > len(best.name):
                continue

            # compare arch
            arch = rpmUtils.arch.getBestArchFromList([pkg.arch, best.arch])
            if arch == pkg.arch:
                best = pkg
                continue

        return best

    def bestPackagesFromList(self, pkglist, arch=None):
        """Takes a list of packages, returns the best packages.
           This function is multilib aware so that it will not compare
           multilib to singlelib packages""" 
    
        returnlist = []
        compatArchList = rpmUtils.arch.getArchList(arch)
        multiLib = []
        singleLib = []
        noarch = []
        for po in pkglist:
            if po.arch not in compatArchList:
                continue
            elif po.arch in ("noarch"):
                noarch.append(po)
            elif rpmUtils.arch.isMultiLibArch(arch=po.arch):
                multiLib.append(po)
            else:
                singleLib.append(po)
                
        # we now have three lists.  find the best package(s) of each
        multi = self._bestPackageFromList(multiLib)
        single = self._bestPackageFromList(singleLib)
        no = self._bestPackageFromList(noarch)

        # now, to figure out which arches we actually want
        # if there aren't noarch packages, it's easy. multi + single
        if no is None:
            if multi: returnlist.append(multi)
            if single: returnlist.append(single)
        # if there's a noarch and it's newer than the multilib, we want
        # just the noarch.  otherwise, we want multi + single
        elif multi:
            best = self._bestPackageFromList([multi,no])
            if best.arch == "noarch":
                returnlist.append(no)
            else:
                if multi: returnlist.append(multi)
                if single: returnlist.append(single)
        # similar for the non-multilib case
        elif single:
            best = self._bestPackageFromList([single,no])
            if best.arch == "noarch":
                returnlist.append(no)
            else:
                returnlist.append(single)
        # if there's not a multi or single lib, then we want the noarch
        else:
            returnlist.append(no)

        return returnlist


    def install(self, po=None, **kwargs):
        """try to mark for install the item specified. Uses provided package 
           object, if available. If not it uses the kwargs and gets the best
           packages from the keyword options provided 
           returns the list of txmbr of the items it installs
           
           """
        
        pkgs = []
        was_pattern = False
        if po:
            if isinstance(po, YumAvailablePackage) or isinstance(po, YumLocalPackage):
                pkgs.append(po)
            else:
                raise Errors.InstallError, _('Package Object was not a package object instance')
            
        else:
            if not kwargs:
                raise Errors.InstallError, _('Nothing specified to install')

            if kwargs.has_key('pattern'):
                was_pattern = True
                pats = [kwargs['pattern']]
                exactmatch, matched, unmatched = \
                    parsePackages(self.pkgSack.returnPackages(patterns=pats),
                                  pats, casematch=1)
                pkgs.extend(exactmatch)
                pkgs.extend(matched)
                # if we have anything left unmatched, let's take a look for it
                # being a dep like glibc.so.2 or /foo/bar/baz
                
                if len(unmatched) > 0:
                    arg = unmatched[0] #only one in there
                    vdbg(_('Checking for virtual provide or file-provide for %s'), 
                         arg)

                    try:
                        mypkgs = self.returnPackagesByDep(arg)
                    except yum.Errors.YumBaseError, e:
                        crit(_('No Match for argument: %s') % arg)
                    else:
                        if mypkgs:
                            pkgs.extend(self.bestPackagesFromList(mypkgs))
                        
            else:
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                pkgs = self.pkgSack.searchNevra(name=nevra_dict['name'],
                     epoch=nevra_dict['epoch'], arch=nevra_dict['arch'],
                     ver=nevra_dict['version'], rel=nevra_dict['release'])
                
            if pkgs:
                # if was_pattern or nevra-dict['arch'] is none, take the list
                # of arches based on our multilib_compat config and 
                # toss out any pkgs of any arch NOT in that arch list

                
                # only do these things if we're multilib
                if rpmUtils.arch.isMultiLibArch():
                    if was_pattern or not nevra_dict['arch']: # and only if they
                                                              # they didn't specify an arch
                       if self.conf.multilib_policy == 'best':
                           pkgs_by_name = {}
                           use = []
                           not_added = []
                           for pkg in pkgs:
                               if pkg.arch in rpmUtils.arch.legitMultiArchesInSameLib():
                                   pkgs_by_name[pkg.name] = 1    
                                   use.append(pkg)  
                               else:
                                   not_added.append(pkg)
                           for pkg in not_added:
                               if not pkg.name in pkgs_by_name:
                                   use.append(pkg)
                           
                           pkgs = use
                           
                pkgSack = ListPackageSack(pkgs)
                pkgs = pkgSack.returnNewestByName()
                del(pkgSack)

                pkgbyname = {}
                for pkg in pkgs:
                    if not pkgbyname.has_key(pkg.name):
                        pkgbyname[pkg.name] = [ pkg ]
                    else:
                        pkgbyname[pkg.name].append(pkg)

                lst = []
                for pkgs in pkgbyname.values():
                    lst.extend(self.bestPackagesFromList(pkgs))
                pkgs = lst

        if len(pkgs) == 0:
            #FIXME - this is where we could check to see if it already installed
            # for returning better errors
            raise Errors.InstallError, _('No package(s) available to install')
        
        # FIXME - lots more checking here
        #  - install instead of erase
        #  - better error handling/reporting


        tx_return = []
        for po in pkgs:
            if self.tsInfo.exists(pkgtup=po.pkgtup):
                if self.tsInfo.getMembersWithState(po.pkgtup, TS_INSTALL_STATES):
                    vdbg1(_('Package: %s  - already in transaction set'), po)
                    tx_return.extend(self.tsInfo.getMembers(pkgtup=po.pkgtup))
                    continue
            
            # make sure this shouldn't be passed to update:
            if self.up.updating_dict.has_key(po.pkgtup):
                txmbrs = self.update(po=po)
                tx_return.extend(txmbrs)
                continue
            
            # make sure it's not already installed
            if self.rpmdb.contains(po=po):
                if not self.tsInfo.getMembersWithState(po.pkgtup, TS_REMOVE_STATES):
                    vwarn(_('Package %s already installed and latest version'),
                          po)
                    continue

            
            # make sure we're not installing a package which is obsoleted by something
            # else in the repo
            thispkgobsdict = self.up.checkForObsolete([po.pkgtup])
            if thispkgobsdict.has_key(po.pkgtup):
                obsoleting = thispkgobsdict[po.pkgtup][0]
                obsoleting_pkg = self.getPackageObject(obsoleting)
                self.install(po=obsoleting_pkg)
                continue
                
            txmbr = self.tsInfo.addInstall(po)
            tx_return.append(txmbr)
        
        return tx_return

    
    def update(self, po=None, requiringPo=None, **kwargs):
        """try to mark for update the item(s) specified. 
            po is a package object - if that is there, mark it for update,
            if possible
            else use **kwargs to match the package needing update
            if nothing is specified at all then attempt to update everything
            
            returns the list of txmbr of the items it marked for update"""
        
        # check for args - if no po nor kwargs, do them all
        # if po, do it, ignore all else
        # if no po do kwargs
        # uninstalled pkgs called for update get returned with errors in a list, maybe?

        updates = self.up.getUpdatesTuples()
        if self.conf.obsoletes:
            obsoletes = self.up.getObsoletesTuples(newest=1)
        else:
            obsoletes = []

        tx_return = []
        if not po and not kwargs: # update everything (the easy case)
            vdbg2(_('Updating Everything'))
            for (obsoleting, installed) in obsoletes:
                obsoleting_pkg = self.getPackageObject(obsoleting)
                installed_pkg =  self.rpmdb.searchPkgTuple(installed)[0]
                txmbr = self.tsInfo.addObsoleting(obsoleting_pkg, installed_pkg)
                self.tsInfo.addObsoleted(installed_pkg, obsoleting_pkg)
                if requiringPo:
                    txmbr.setAsDep(requiringPo)
                tx_return.append(txmbr)
                
            for (new, old) in updates:
                if self.tsInfo.isObsoleted(pkgtup=old):
                    vdbg2(_('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s'), 
                          old)
                else:
                    updating_pkg = self.getPackageObject(new)
                    updated_pkg = self.rpmdb.searchPkgTuple(old)[0]
                    txmbr = self.tsInfo.addUpdate(updating_pkg, updated_pkg)
                    if requiringPo:
                        txmbr.setAsDep(requiringPo)
                    tx_return.append(txmbr)
            
            return tx_return

        else:
            instpkgs = []
            availpkgs = []
            if po: # just a po
                if po.repoid == 'installed':
                    instpkgs.append(po)
                else:
                    availpkgs.append(po)
            elif kwargs.has_key('pattern'):
                (e, m, u) = self.pkgSack.matchPackageNames([kwargs['pattern']])
                availpkgs.extend(e)
                availpkgs.extend(m)
                (e, m, u) = self.rpmdb.matchPackageNames([kwargs['pattern']])
                instpkgs.extend(e)
                instpkgs.extend(m)
                
            else: # we have kwargs, sort them out.
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                availpkgs = self.pkgSack.searchNevra(name=nevra_dict['name'],
                          epoch=nevra_dict['epoch'], arch=nevra_dict['arch'],
                        ver=nevra_dict['version'], rel=nevra_dict['release'])
                
                instpkgs = self.rpmdb.searchNevra(name=nevra_dict['name'], 
                            epoch=nevra_dict['epoch'], arch=nevra_dict['arch'], 
                            ver=nevra_dict['version'], rel=nevra_dict['release'])
            
            # for any thing specified
            # get the list of available pkgs matching it (or take the po)
            # get the list of installed pkgs matching it (or take the po)
            # go through each list and look for:
               # things obsoleting it if it is an installed pkg
               # things it updates if it is an available pkg
               # things updating it if it is an installed pkg
               # in that order
               # all along checking to make sure we:
                # don't update something that's already been obsoleted
            
            # TODO: we should search the updates and obsoletes list and
            # mark the package being updated or obsoleted away appropriately
            # and the package relationship in the tsInfo
            
            if self.conf.obsoletes:
                for installed_pkg in instpkgs:
                    for obsoleting in self.up.obsoleted_dict.get(installed_pkg.pkgtup, []):
                        obsoleting_pkg = self.getPackageObject(obsoleting)
                        # FIXME check for what might be in there here
                        txmbr = self.tsInfo.addObsoleting(obsoleting_pkg, installed_pkg)
                        self.tsInfo.addObsoleted(installed_pkg, obsoleting_pkg)
                        if requiringPo:
                            txmbr.setAsDep(requiringPo)
                        tx_return.append(txmbr)
                for available_pkg in availpkgs:
                    for obsoleted in self.up.obsoleting_dict.get(available_pkg.pkgtup, []):
                        obsoleted_pkg = self.getInstalledPackageObject(obsoleted)
                        txmbr = self.tsInfo.addObsoleting(available_pkg, obsoleted_pkg)
                        if requiringPo:
                            txmbr.setAsDep(requiringPo)
                        tx_return.append(txmbr)
                        if self.tsInfo.isObsoleted(obsoleted):
                            vdbg2(_('Package is already obsoleted: %s.%s %s:%s-%s'), obsoleted)
                        else:
                            txmbr = self.tsInfo.addObsoleted(obsoleted_pkg, available_pkg)
                            tx_return.append(txmbr)
            for available_pkg in availpkgs:
                for updated in self.up.updating_dict.get(available_pkg.pkgtup, []):
                    if self.tsInfo.isObsoleted(updated):
                        self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s'), 
                                                updated)
                    else:
                        updated_pkg =  self.rpmdb.searchPkgTuple(updated)[0]
                        txmbr = self.tsInfo.addUpdate(available_pkg, updated_pkg)
                        if requiringPo:
                            txmbr.setAsDep(requiringPo)
                        tx_return.append(txmbr)
            for installed_pkg in instpkgs:
                for updating in self.up.updatesdict.get(installed_pkg.pkgtup, []):
                    updating_pkg = self.getPackageObject(updating)
                    if self.tsInfo.isObsoleted(installed_pkg.pkgtup):
                        self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s'), 
                                                installed_pkg.pkgtup)
                    else:
                        txmbr = self.tsInfo.addUpdate(updating_pkg, installed_pkg)
                        if requiringPo:
                            txmbr.setAsDep(requiringPo)
                        tx_return.append(txmbr)

        return tx_return
        
        
    def remove(self, po=None, **kwargs):
        """try to find and mark for remove the specified package(s) -
            if po is specified then that package object (if it is installed) 
            will be marked for removal.
            if no po then look at kwargs, if neither then raise an exception"""

        if not po and not kwargs:
            raise Errors.RemoveError, 'Nothing specified to remove'
        
        tx_return = []
        pkgs = []
        
        
        if po:
            pkgs = [po]  
        else:
            if kwargs.has_key('pattern'):
                (e,m,u) = self.rpmdb.matchPackageNames([kwargs['pattern']])
                pkgs.extend(e)
                pkgs.extend(m)
                if u:
                    depmatches = []
                    arg = u[0]
                    try:
                        depmatches = self.returnInstalledPackagesByDep(arg)
                    except yum.Errors.YumBaseError, e:
                        crit(_('%s') % e)
                    
                    if not depmatches:
                        crit(_('No Match for argument: %s') % arg)
                    else:
                        pkgs.extend(depmatches)
                
            else:    
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                pkgs = self.rpmdb.searchNevra(name=nevra_dict['name'], 
                            epoch=nevra_dict['epoch'], arch=nevra_dict['arch'], 
                            ver=nevra_dict['version'], rel=nevra_dict['release'])

                if len(pkgs) == 0:
                    warn(_("No package matched to remove"))

        for po in pkgs:
            txmbr = self.tsInfo.addErase(po)
            tx_return.append(txmbr)
        
        return tx_return

    def installLocal(self, pkg, po=None, updateonly=False):
        """
        handles installs/updates of rpms provided on the filesystem in a
        local dir (ie: not from a repo)

        Return the added transaction members.

        @param pkg: a path to an rpm file on disk.
        @param po: A YumLocalPackage
        @param updateonly: Whether or not true installs are valid.
        """

        # read in the package into a YumLocalPackage Object
        # append it to self.localPackages
        # check if it can be installed or updated based on nevra versus rpmdb
        # don't import the repos until we absolutely need them for depsolving

        tx_return = []
        installpkgs = []
        updatepkgs = []
        donothingpkgs = []

        if not po:
            try:
                po = YumLocalPackage(ts=self.rpmdb.readOnlyTS(), filename=pkg)
            except Errors.MiscError:
                crit(_('Cannot open file: %s. Skipping.'), pkg)
                return tx_return
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Examining %s: %s'), po.localpath, po)

        # everything installed that matches the name
        installedByKey = self.rpmdb.searchNevra(name=po.name)
        # go through each package
        if len(installedByKey) == 0: # nothing installed by that name
            if updateonly:
                warn(_('Package %s not installed, cannot update it. Run yum install to install it instead.'), po.name)
                return tx_return
            else:
                installpkgs.append(po)

        for installed_pkg in installedByKey:
            if po.EVR > installed_pkg.EVR: # we're newer - this is an update, pass to them
                if installed_pkg.name in self.conf.exactarchlist:
                    if po.arch == installed_pkg.arch:
                        updatepkgs.append((po, installed_pkg))
                    else:
                        donothingpkgs.append(po)
                else:
                    updatepkgs.append((po, installed_pkg))
            elif po.EVR == installed_pkg.EVR:
                if po.arch != installed_pkg.arch and (isMultiLibArch(po.arch) or
                          isMultiLibArch(installed_pkg.arch)):
                    installpkgs.append(po)
                else:
                    donothingpkgs.append(po)
            else:
                donothingpkgs.append(po)

        # handle excludes for a localinstall
        toexc = []
        if len(self.conf.exclude) > 0:
           exactmatch, matched, unmatched = \
                   parsePackages(installpkgs + map(lambda x: x[0], updatepkgs),
                                 self.conf.exclude, casematch=1)
           toexc = exactmatch + matched

        if po in toexc:
           self.verbose_logger.debug(_('Excluding %s'), po)
           return tx_return

        for po in installpkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Marking %s to be installed'), po.localpath)
            self.localPackages.append(po)
            tx_return.extend(self.install(po=po))

        for (po, oldpo) in updatepkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Marking %s as an update to %s'), po.localpath, oldpo)
            self.localPackages.append(po)
            txmbr = self.tsInfo.addUpdate(po, oldpo)
            tx_return.append(txmbr)

        for po in donothingpkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('%s: does not update installed package.'), po.localpath)

        return tx_return

    def reinstall(self, po=None, **kwargs):
        """Setup the problem filters to allow a reinstall to work, then
           pass everything off to install"""
           
        if rpm.RPMPROB_FILTER_REPLACEPKG not in self.tsInfo.probFilterFlags:
            self.tsInfo.probFilterFlags.append(rpm.RPMPROB_FILTER_REPLACEPKG)
        if rpm.RPMPROB_FILTER_REPLACENEWFILES not in self.tsInfo.probFilterFlags:
            self.tsInfo.probFilterFlags.append(rpm.RPMPROB_FILTER_REPLACENEWFILES)
        if rpm.RPMPROB_FILTER_REPLACEOLDFILES not in self.tsInfo.probFilterFlags:
            self.tsInfo.probFilterFlags.append(rpm.RPMPROB_FILTER_REPLACEOLDFILES)

        tx_mbrs = []
        tx_mbrs.extend(self.remove(po, **kwargs))
        if not tx_mbrs:
            raise Errors.RemoveError, _("Problem in reinstall: no package matched to remove")
        templen = len(tx_mbrs)
        # this is a reinstall, so if we can't reinstall exactly what we uninstalled
        # then we really shouldn't go on
        new_members = []
        for item in tx_mbrs:
            members = self.install(name=item.name, arch=item.arch,
                           ver=item.version, release=item.release, epoch=item.epoch)
            if len(members) == 0:
                raise Errors.RemoveError, _("Problem in reinstall: no package matched to install")
            new_members.extend(members)

        tx_mbrs.extend(new_members)            
        return tx_mbrs
        

        
    def _nevra_kwarg_parse(self, kwargs):
            
        returndict = {}
        
        returndict['name'] = kwargs.get('name')
        returndict['epoch'] = kwargs.get('epoch')
        returndict['arch'] = kwargs.get('arch')
        # get them as ver, version and rel, release - if someone
        # specifies one of each then that's kinda silly.
        returndict['version'] = kwargs.get('version')
        if returndict['version'] is None:
            returndict['version'] = kwargs.get('ver')

        returndict['release'] = kwargs.get('release')
        if returndict['release'] is None:
            returndict['release'] = kwargs.get('rel')

        return returndict

    def getKeyForPackage(self, po, askcb = None, fullaskcb = None):
        """
        Retrieve a key for a package. If needed, prompt for if the key should
        be imported using askcb.
        
        @param po: Package object to retrieve the key of.
        @param askcb: Callback function to use for asking for verification.
                      Takes arguments of the po, the userid for the key, and
                      the keyid.
        @param fullaskcb: Callback function to use for asking for verification
                          of a key. Differs from askcb in that it gets passed
                          a dictionary so that we can expand the values passed.
        """
        
        repo = self.repos.getRepo(po.repoid)
        keyurls = repo.gpgkey
        key_installed = False

        ts = rpmUtils.transaction.TransactionWrapper(self.conf.installroot)

        for keyurl in keyurls:
            info(_('Retrieving GPG key from %s'), keyurl)

            # Go get the GPG key from the given URL
            try:
                rawkey = urlgrabber.urlread(keyurl, limit=9999)
            except urlgrabber.grabber.URLGrabError, e:
                raise Errors.YumBaseError(_('GPG key retrieval failed: ') +
                                          str(e))

            # Parse the key
            try:
                keyinfo = misc.getgpgkeyinfo(rawkey)
                keyid = keyinfo['keyid']
                hexkeyid = misc.keyIdToRPMVer(keyid).upper()
                timestamp = keyinfo['timestamp']
                userid = keyinfo['userid']
                fingerprint = keyinfo['fingerprint']
            except ValueError, e:
                raise Errors.YumBaseError, \
                      _('GPG key parsing failed: ') + str(e)

            # Check if key is already installed
            if misc.keyInstalled(ts, keyid, timestamp) >= 0:
                info(_('GPG key at %s (0x%s) is already installed'),
                    keyurl, hexkeyid)
                continue

            # Try installing/updating GPG key
            crit(_('Importing GPG key 0x%s "%s" from %s'),
                 hexkeyid, userid, keyurl.replace("file://",""))
            rc = False
            if self.conf.assumeyes:
                rc = True
            elif fullaskcb:
                rc = fullaskcb({"po": po, "userid": userid,
                                "hexkeyid": hexkeyid, "keyurl": keyurl,
                                "fingerprint": fingerprint, "timestamp": timestamp})
            elif askcb:
                rc = askcb(po, userid, hexkeyid)

            if not rc:
                raise Errors.YumBaseError, _("Not installing key")
            
            # Import the key
            result = ts.pgpImportPubkey(misc.procgpgkey(rawkey))
            if result != 0:
                raise Errors.YumBaseError, \
                      _('Key import failed (code %d)') % result
            misc.import_key_to_pubring(rawkey, po.repo.cachedir)
            
            info(_('Key imported successfully'))
            key_installed = True

            if not key_installed:
                raise Errors.YumBaseError, \
                      _('The GPG keys listed for the "%s" repository are ' \
                      'already installed but they are not correct for this ' \
                      'package.\n' \
                      'Check that the correct key URLs are configured for ' \
                      'this repository.') % (repo.name)

        # Check if the newly installed keys helped
        result, errmsg = self.sigCheckPkg(po)
        if result != 0:
            info(_("Import of key(s) didn't help, wrong key(s)?"))
            raise Errors.YumBaseError, errmsg
    def _limit_installonly_pkgs(self):
        if self.conf.installonly_limit < 1 :
            return 
            
        toremove = []
        (cur_kernel_v, cur_kernel_r) = misc.get_running_kernel_version_release(self.ts)
        for instpkg in self.conf.installonlypkgs:
            for m in self.tsInfo.getMembers():
                if (m.name == instpkg or instpkg in m.po.provides_names) \
                       and m.ts_state in ('i', 'u'):
                    installed = self.rpmdb.searchNevra(name=m.name)
                    if len(installed) >= self.conf.installonly_limit - 1: # since we're adding one
                        numleft = len(installed) - self.conf.installonly_limit + 1
                        installed.sort(packages.comparePoEVR)
                        for po in installed:
                            if (po.version, po.release) == (cur_kernel_v, cur_kernel_r): 
                                # don't remove running
                                continue
                            if numleft == 0:
                                break
                            toremove.append(po)
                            numleft -= 1
                        
        map(lambda x: self.tsInfo.addErase(x), toremove)

    def processTransaction(self, callback=None,rpmTestDisplay=None, rpmDisplay=None):
        '''
        Process the current Transaction
        - Download Packages
        - Check GPG Signatures.
        - Run Test RPM Transaction
        - Run RPM Transaction
        
        callback.event method is called at start/end of each process.
        
        @param callback: callback object (must have an event method)
        @param rpmTestDisplay: Name of display class to use in RPM Test Transaction 
        @param rpmDisplay: Name of display class to use in RPM Transaction 
        '''
        
        if not callback:
            callback = callbacks.ProcessTransNoOutputCallback()
        
        # Download Packages
        callback.event(callbacks.PT_DOWNLOAD)
        pkgs = self._downloadPackages(callback)
        # Check Package Signatures
        if pkgs != None:
            callback.event(callbacks.PT_GPGCHECK)
            self._checkSignatures(pkgs,callback)
        # Run Test Transaction
        callback.event(callbacks.PT_TEST_TRANS)
        self._doTestTransaction(callback,display=rpmTestDisplay)
        # Run Transaction
        callback.event(callbacks.PT_TRANSACTION)
        self._doTransaction(callback,display=rpmDisplay)
    
    def _downloadPackages(self,callback):
        ''' Download the need packages in the Transaction '''
        # This can be overloaded by a subclass.    
        dlpkgs = map(lambda x: x.po, filter(lambda txmbr:
                                            txmbr.ts_state in ("i", "u"),
                                            self.tsInfo.getMembers()))
        # Check if there is something to do
        if len(dlpkgs) == 0:
            return None
        # make callback with packages to download                                    
        callback.event(callbacks.PT_DOWNLOAD_PKGS,dlpkgs)
        try:
            probs = self.downloadPkgs(dlpkgs)

        except IndexError:
            raise Errors.YumBaseError, [_("Unable to find a suitable mirror.")]
        if len(probs) > 0:
            errstr = [_("Errors were encountered while downloading packages.")]
            for key in probs:
                errors = misc.unique(probs[key])
                for error in errors:
                    errstr.append("%s: %s" %(key, error))

            raise Errors.YumDownloadError, errstr
        return dlpkgs

    def _checkSignatures(self,pkgs,callback):
        ''' The the signatures of the downloaded packages '''
        # This can be overloaded by a subclass.    
        for po in pkgs:
            result, errmsg = self.sigCheckPkg(po)
            if result == 0:
                # Verified ok, or verify not req'd
                continue            
            elif result == 1:
               self.getKeyForPackage(po, self._askForGPGKeyImport)
            else:
                raise Errors.YumGPGCheckError, errmsg

        return 0
        
    def _askForGPGKeyImport(self, po, userid, hexkeyid):
        ''' 
        Ask for GPGKeyImport 
        This need to be overloaded in a subclass to make GPG Key import work
        '''
        return False

    def _doTestTransaction(self,callback,display=None):
        ''' Do the RPM test transaction '''
        # This can be overloaded by a subclass.    
        if self.conf.rpm_check_debug:
            self.verbose_logger.log(logginglevels.INFO_2, 
                 _('Running rpm_check_debug'))
            msgs = self._run_rpm_check_debug()
            if msgs:
                retmsgs = [_('ERROR with rpm_check_debug vs depsolve:')]
                retmsgs.extend(msgs) 
                retmsgs.append(_('Please report this error in bugzilla'))
                raise Errors.YumRPMCheckError,retmsgs
        
        tsConf = {}
        for feature in ['diskspacecheck']: # more to come, I'm sure
            tsConf[feature] = getattr( self.conf, feature )
        #
        testcb = RPMTransaction(self, test=True)
        # overwrite the default display class
        if display:
            testcb.display = display
        # clean out the ts b/c we have to give it new paths to the rpms 
        del self.ts
  
        self.initActionTs()
        # save our dsCallback out
        dscb = self.dsCallback
        self.dsCallback = None # dumb, dumb dumb dumb!
        self.populateTs( keepold=0 ) # sigh
        tserrors = self.ts.test( testcb, conf=tsConf )
        del testcb
  
        if len( tserrors ) > 0:
            errstring =  _('Test Transaction Errors: ')
            for descr in tserrors:
                 errstring += '  %s\n' % descr 
            raise Errors.YumTestTransactionError, errstring

        del self.ts
        # put back our depcheck callback
        self.dsCallback = dscb


    def _doTransaction(self,callback,display=None):
        ''' do the RPM Transaction '''
        # This can be overloaded by a subclass.    
        self.initActionTs() # make a new, blank ts to populate
        self.populateTs( keepold=0 ) # populate the ts
        self.ts.check() # required for ordering
        self.ts.order() # order
        cb = RPMTransaction(self,display=SimpleCliCallBack)
        # overwrite the default display class
        if display:
            cb.display = display
        self.runTransaction( cb=cb )

    def _run_rpm_check_debug(self):
        import rpm
        results = []
        # save our dsCallback out
        dscb = self.dsCallback
        self.dsCallback = None # dumb, dumb dumb dumb!
        self.populateTs(test=1)
        deps = self.ts.check()
        for deptuple in deps:
            ((name, version, release), (needname, needversion), flags,
              suggest, sense) = deptuple
            if sense == rpm.RPMDEP_SENSE_REQUIRES:
                msg = _('Package %s needs %s, this is not available.') % \
                      (name, rpmUtils.miscutils.formatRequire(needname, 
                                                              needversion, flags))
                results.append(msg)
            elif sense == rpm.RPMDEP_SENSE_CONFLICTS:
                msg = _('Package %s conflicts with %s.') % \
                      (name, rpmUtils.miscutils.formatRequire(needname, 
                                                              needversion, flags))
                results.append(msg)
        self.dsCallback = dscb
        return results
       
