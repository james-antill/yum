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

#
# Implementation of the YumPackageSack class that uses an sqlite backend
#

import os
import os.path
import fnmatch

import yumRepo
from packages import PackageObject, RpmBase, YumAvailablePackage
import Errors
import misc

from sqlutils import executeSQL
import rpmUtils.miscutils
import sqlutils

def catchSqliteException(func):
    """This decorator converts sqlite exceptions into RepoError"""
    def newFunc(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlutils.sqlite.Error:
            raise Errors.RepoError

    newFunc.__name__ = func.__name__
    newFunc.__doc__ = func.__doc__
    newFunc.__dict__.update(func.__dict__)
    return newFunc

def _share_data(value):
    return misc.share_data(value)

class YumAvailablePackageSqlite(YumAvailablePackage, PackageObject, RpmBase):
    def __init__(self, repo, db_obj):
        self.prco = { _share_data('obsoletes'): (),
                      _share_data('conflicts'): (),
                      _share_data('requires'): (),
                      _share_data('provides'): () }
        self.sack = repo.sack
        self.repoid = repo.id
        self.repo = repo
        self.state = None
        self._loadedfiles = False
        self._read_db_obj(db_obj)
        self.id = self.pkgId
        self.ver = self.version 
        self.rel = self.release 
        self.pkgtup = (self.name, self.arch, self.epoch, self.version, self.release)

        self._changelog = None
        self._hash = None

    files = property(fget=lambda self: self._loadFiles())

    def _read_db_obj(self, db_obj, item=None):
        """read the db obj. If asked for a specific item, return it.
           otherwise populate out into the object what exists"""
        if item:
            try:
                return db_obj[item]
            except (IndexError, KeyError):
                return None

        for item in ['name', 'arch', 'epoch', 'version', 'release', 'pkgKey']:
            try:
                setattr(self, item, _share_data(db_obj[item]))
            except (IndexError, KeyError):
                pass

        for item in ['pkgId']:
            try:
                setattr(self, item, db_obj[item])
            except (IndexError, KeyError):
                pass

        try:
            checksum_type = _share_data(db_obj['checksum_type'])
            check_sum = (checksum_type, db_obj['pkgId'], True)
            self._checksums = [ check_sum ]
        except (IndexError, KeyError):
            pass

    @catchSqliteException
    def _sql_MD(self, MD, sql, *args):
        """ Exec SQL against an MD of the repo, return a cursor. """

        cache = getattr(self.sack, MD + 'db')[self.repo]
        cur = cache.cursor()
        executeSQL(cur, sql, *args)
        return cur

    def __getattr__(self, varname):
        db2simplemap = { 'packagesize' : 'size_package',
                         'archivesize' : 'size_archive',
                         'installedsize' : 'size_installed',
                         'buildtime' : 'time_build',
                         'hdrstart' : 'rpm_header_start',
                         'hdrend' : 'rpm_header_end',
                         'basepath' : 'location_base',
                         'relativepath': 'location_href',
                         'filetime' : 'time_file',
                         'packager' : 'rpm_packager',
                         'group' : 'rpm_group',
                         'buildhost' : 'rpm_buildhost',
                         'sourcerpm' : 'rpm_sourcerpm',
                         'vendor' : 'rpm_vendor',
                         'license' : 'rpm_license',
                         'checksum_value' : 'pkgId',
                        }
        
        dbname = varname
        if db2simplemap.has_key(varname):
            dbname = db2simplemap[varname]
        r = self._sql_MD('primary',
                         "SELECT %s FROM packages WHERE pkgId = ?" % dbname,
                         (self.pkgId,)).fetchone()
        value = r[0]
        if varname in {'vendor' : 1, 'packager' : 1, 'buildhost' : 1,
                       'license' : 1, 'group' : 1,
                       'summary' : 1, 'description' : 1, 'sourcerpm' : 1,
                       'url' : 1}:
            value  = _share_data(value)
        setattr(self, varname, value)
            
        return value
        
    def _loadFiles(self):
        if self._loadedfiles:
            return self._files

        result = {}
        
        #FIXME - this should be try, excepting
        self.sack.populate(self.repo, mdtype='filelists')
        cur = self._sql_MD('filelists',
                           "SELECT dirname, filetypes, filenames " \
                           "FROM   filelist JOIN packages USING(pkgKey) " \
                           "WHERE  packages.pkgId = ?", (self.pkgId,))
        for ob in cur:
            dirname = ob['dirname']
            filetypes = decodefiletypelist(ob['filetypes'])
            filenames = decodefilenamelist(ob['filenames'])
            while(filetypes):
                if dirname:
                    filename = dirname+'/'+filenames.pop()
                else:
                    filename = filenames.pop()
                filetype = _share_data(filetypes.pop())
                result.setdefault(filetype,[]).append(filename)
        self._loadedfiles = True
        self._files = result

        return self._files

    def _loadChangelog(self):
        result = []
        if not self._changelog:
            if not self.sack.otherdb.has_key(self.repo):
                try:
                    self.sack.populate(self.repo, mdtype='otherdata')
                except Errors.RepoError:
                    self._changelog = result
                    return
            cur = self._sql_MD('other',
                               "SELECT date, author, changelog " \
                               "FROM   changelog JOIN packages USING(pkgKey) " \
                               "WHERE  pkgId = ?", (self.pkgId,))
            # Check count(pkgId) here, the same way we do in searchFiles()?
            # Failure mode is much less of a problem.
            for ob in cur:
                result.append( (ob['date'], _share_data(ob['author']),
                                ob['changelog']) )
            self._changelog = result
            return
    
    def dropCachedData(self):
        # del <non-default-attributes>
        if self._loadedfiles:
            del self._files
            self._loadedfiles = False
        self._changelog = None
        self._hash = None
        self.prco = { _share_data('obsoletes'): (),
                      _share_data('conflicts'): (),
                      _share_data('requires'): (),
                      _share_data('provides'): () }
        
    def returnIdSum(self):
            return (self.checksum_type, self.pkgId)
    
    def returnChangelog(self):
        self._loadChangelog()
        return self._changelog
    
    def returnFileEntries(self, ftype='file'):
        self._loadFiles()
        return RpmBase.returnFileEntries(self,ftype)
    
    def returnFileTypes(self):
        self._loadFiles()
        return RpmBase.returnFileTypes(self)

    def simpleFiles(self, ftype='file'):
        sql = "SELECT name as fname FROM files WHERE pkgKey = ? and type = ?"
        cur = self._sql_MD('primary', sql, (self.pkgKey, ftype))
        return map(lambda x: x['fname'], cur)

    def returnPrco(self, prcotype, printable=False):
        prcotype = _share_data(prcotype)
        if isinstance(self.prco[prcotype], tuple):
            sql = "SELECT name, version, release, epoch, flags " \
                  "FROM %s WHERE pkgKey = ?" % prcotype
            cur = self._sql_MD('primary', sql, (self.pkgKey,))
            self.prco[prcotype] = [ ]
            for ob in cur:
                prco_set = (_share_data(ob['name']), _share_data(ob['flags']),
                            (_share_data(ob['epoch']),
                             _share_data(ob['version']),
                             _share_data(ob['release'])))
                self.prco[prcotype].append(_share_data(prco_set))

        return RpmBase.returnPrco(self, prcotype, printable)

class YumSqlitePackageSack(yumRepo.YumPackageSack):
    """ Implementation of a PackageSack that uses sqlite cache instead of fully
    expanded metadata objects to provide information """

    def __init__(self, packageClass):
        # Just init as usual and create a dict to hold the databases
        yumRepo.YumPackageSack.__init__(self, packageClass)
        self.primarydb = {}
        self.filelistsdb = {}
        self.otherdb = {}
        self.excludes = {}
        self._excludes = set() # of (repo, pkgKey)
        self._all_excludes = {}
        self._search_cache = {
            'provides' : { },
            'requires' : { },
            }
        self._key2pkg = {}

    @catchSqliteException
    def _sql_MD(self, MD, repo, sql, *args):
        """ Exec SQL against an MD of the repo, return a cursor. """

        cache = getattr(self, MD + 'db')[repo]
        cur = cache.cursor()
        executeSQL(cur, sql, *args)
        return cur

    def _sql_MD_pkg_num(self, MD, repo):
        """ Give a count of pkgIds in the given repo DB """
        sql = "SELECT count(pkgId) FROM packages"
        return self._sql_MD('primary', repo, sql).fetchone()[0]
        
    def __len__(self):
        # First check if everything is excluded
        all_excluded = True
        for (repo, cache) in self.primarydb.items():
            if repo not in self._all_excludes:
                all_excluded = False
                break
        if all_excluded:
            return 0
            
        exclude_num = 0
        for repo in self.excludes:
            exclude_num += len(self.excludes[repo])
        if hasattr(self, 'pkgobjlist'):
            return len(self.pkgobjlist) - exclude_num
        
        pkg_num = 0
        sql = "SELECT count(pkgId) FROM packages"
        for repo in self.primarydb:
            pkg_num += self._sql_MD_pkg_num('primary', repo)
        return pkg_num - exclude_num

    def dropCachedData(self, pkgs=False):
        if hasattr(self, '_memoize_requires'):
            del self._memoize_requires
        if hasattr(self, '_memoize_provides'):
            del self._memoize_provides
        if hasattr(self, 'pkgobjlist'):
            del self.pkgobjlist
        if pkgs:
            for repo in self._key2pkg:
                for pkg in self._key2pkg[repo].itervalues():
                    pkg.dropCachedData()
        self._key2pkg = {}
        self._search_cache = {
            'provides' : { },
            'requires' : { },
            }
        misc._share_data_store = {}

    @catchSqliteException
    def close(self):
        self.dropCachedData()

        for dataobj in self.primarydb.values() + \
                       self.filelistsdb.values() + \
                       self.otherdb.values():
            dataobj.close()
        self.primarydb = {}
        self.filelistsdb = {}
        self.otherdb = {}
        self.excludes = {}
        self._excludes = set()
        self._all_excludes = {}

        yumRepo.YumPackageSack.close(self)

    def buildIndexes(self):
        # We don't need to play with returnPackages() caching as it handles
        # additions to excludes after the cache is built.
        pass

    def _checkIndexes(self, failure='error'):
        return

    # Remove a package
    # Because we don't want to remove a package from the database we just
    # add it to the exclude list
    def delPackage(self, obj):
        if not self.excludes.has_key(obj.repo):
            self.excludes[obj.repo] = {}
        self.excludes[obj.repo][obj.pkgId] = 1
        self._excludes.add( (obj.repo, obj.pkgKey) )

    def _delAllPackages(self, repo):
        """ Exclude all packages from the repo. """
        self._all_excludes[repo] = True
        if repo in self.excludes:
            del self.excludes[repo]
        if repo in self._key2pkg:
            del self._key2pkg[repo]

    def _excluded(self, repo, pkgId):
        if repo in self._all_excludes:
            return True
        
        if repo in self.excludes and pkgId in self.excludes[repo]:
            return True
                
        return False

    def _pkgKeyExcluded(self, repo, pkgKey):
        if repo in self._all_excludes:
            return True

        return (repo, pkgKey) in self._excludes

    def _pkgExcluded(self, po):
        return self._pkgKeyExcluded(po.repo, po.pkgKey)

    def _packageByKey(self, repo, pkgKey):
        if not self._key2pkg.has_key(repo):
            self._key2pkg[repo] = {}
        if not self._key2pkg[repo].has_key(pkgKey):
            sql = "SELECT pkgKey, pkgId, name, epoch, version, release " \
                  "FROM packages WHERE pkgKey = ?"
            cur = self._sql_MD('primary', repo, sql, (pkgKey,))
            po = self.pc(repo, cur.fetchone())
            self._key2pkg[repo][pkgKey] = po
        return self._key2pkg[repo][pkgKey]
        
    def addDict(self, repo, datatype, dataobj, callback=None):
        if self.added.has_key(repo):
            if datatype in self.added[repo]:
                return
        else:
            self.added[repo] = []

        if not self.excludes.has_key(repo): 
            self.excludes[repo] = {}

        if datatype == 'metadata':
            self.primarydb[repo] = dataobj
        elif datatype == 'filelists':
            self.filelistsdb[repo] = dataobj
        elif datatype == 'otherdata':
            self.otherdb[repo] = dataobj
        else:
            # We can not handle this yet...
            raise "Sorry sqlite does not support %s" % (datatype)
    
        self.added[repo].append(datatype)

        
    # Get all files for a certain pkgId from the filelists.xml metadata
    # Search packages that either provide something containing name
    # or provide a file containing name 
    def searchAll(self,name, query_type='like'):
        # this function is just silly and it reduces down to just this
        return self.searchPrco(name, 'provides')

    def _sql_pkgKey2po(self, repo, cur, pkgs=None):
        """ Takes a cursor and maps the pkgKey rows into a list of packages. """
        if pkgs is None: pkgs = []
        for ob in cur:
            if self._pkgKeyExcluded(repo, ob['pkgKey']):
                continue
            pkgs.append(self._packageByKey(repo, ob['pkgKey']))
        return pkgs

    @catchSqliteException
    def searchFiles(self, name, strict=False):
        """search primary if file will be in there, if not, search filelists, use globs, if possible"""
        
        # optimizations:
        # if it is not  glob, then see if it is in the primary.xml filelists, 
        # if so, just use those for the lookup
        
        glob = True
        querytype = 'glob'
        if strict or not misc.re_glob(name):
            glob = False
            querytype = '='

        # Take off the trailing slash to act like rpm
        if name[-1] == '/':
            name = name[:-1]
       
        pkgs = []
        if len(self.filelistsdb) == 0:
            # grab repo object from primarydb and force filelists population in this sack using repo
            # sack.populate(repo, mdtype, callback, cacheonly)
            for (repo,cache) in self.primarydb.items():
                if repo in self._all_excludes:
                    continue

                self.populate(repo, mdtype='filelists')

        # Check to make sure the DB data matches, this should always pass but
        # we've had weird errors. So check it for a bit.
        for repo in self.filelistsdb:
            pri_pkgs = self._sql_MD_pkg_num('primary',   repo)
            fil_pkgs = self._sql_MD_pkg_num('filelists', repo)
            if pri_pkgs != fil_pkgs:
                raise Errors.RepoError

        for (rep,cache) in self.filelistsdb.items():
            if rep in self._all_excludes:
                continue

            cur = cache.cursor()

            if glob:
                dirname_check = ""
            else:
                dirname = os.path.dirname(name)
                dirname_check = "dirname = \"%s\" and " % dirname

            # grab the entries that are a single file in the 
            # filenames section, use sqlites globbing if it is a glob
            executeSQL(cur, "select pkgKey from filelist where \
                    %s length(filetypes) = 1 and \
                    dirname || ? || filenames \
                    %s ?" % (dirname_check, querytype), ('/', name))
            self._sql_pkgKey2po(rep, cur, pkgs)

            def filelist_globber(dirname, filenames):
                files = filenames.split('/')
                fns = map(lambda f: '%s/%s' % (dirname, f), files)
                if glob:
                    matches = fnmatch.filter(fns, name)
                else:
                    matches = filter(lambda x: name==x, fns)
                return len(matches)

            cache.create_function("filelist_globber", 2, filelist_globber)
            # for all the ones where filenames is multiple files, 
            # make the files up whole and use python's globbing method
            executeSQL(cur, "select pkgKey from filelist where \
                             %s length(filetypes) > 1 \
                             and filelist_globber(dirname,filenames)" % dirname_check)

            self._sql_pkgKey2po(rep, cur, pkgs)

        pkgs = misc.unique(pkgs)
        return pkgs
        
    @catchSqliteException
    def searchPrimaryFields(self, fields, searchstring):
        """search arbitrary fields from the primarydb for a string"""
        result = []
        if len(fields) < 1:
            return result
        
        basestring="select DISTINCT pkgKey from packages where %s like '%%%s%%' " % (fields[0], searchstring)
        
        for f in fields[1:]:
            basestring = "%s or %s like '%%%s%%' " % (basestring, f, searchstring)
        
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, basestring)
            self._sql_pkgKey2po(rep, cur, result)
        return result    

    @catchSqliteException
    def searchPrimaryFieldsMultipleStrings(self, fields, searchstrings):
        """search arbitrary fields from the primarydb for a multiple strings
           return packages, number of items it matched as a list of tuples"""
           
        result = [] # (pkg, num matches)
        if len(fields) < 1:
            return result
        
       
        unionstring = "select pkgKey, SUM(cumul) AS total from ( "
        endunionstring = ")GROUP BY pkgKey ORDER BY total DESC"
                
        #SELECT pkgkey, SUM(cumul) AS total FROM (SELECT pkgkey, 1 
        #AS cumul FROM packages WHERE description LIKE '%foo%' UNION ... ) 
        #GROUP BY pkgkey ORDER BY total DESC;
        selects = []
        
        # select pkgKey, 1 AS cumul from packages where description 
        # like '%devel%' or description like '%python%' or description like '%ssh%'
#        for f in fields:
#            basestring = "select pkgKey, 1 AS cumul from packages where %s like '%%%s%%' " % (f,searchstrings[0]) 
#            for s in searchstrings[1:]:
#                basestring = "%s or %s like '%%%s%%' " % (basestring, f, s)
#            selects.append(basestring)
            
        for s in searchstrings:         
            basestring="select pkgKey,1 AS cumul from packages where %s like '%%%s%%' " % (fields[0], s)
            for f in fields[1:]:
                basestring = "%s or %s like '%%%s%%' " % (basestring, f, s)
            selects.append(basestring)
        
        totalstring = unionstring + " UNION ALL ".join(selects) + endunionstring
#        print totalstring
        
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, totalstring)
            for ob in cur:
                if self._pkgKeyExcluded(rep, ob['pkgKey']):
                    continue
                result.append((self._packageByKey(rep, ob['pkgKey']), ob['total']))
        return result
        
    @catchSqliteException
    def returnObsoletes(self, newest=False):
        if newest:
            raise NotImplementedError()

        obsoletes = {}
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, "select packages.name as name,\
                packages.pkgKey as pkgKey,\
                packages.arch as arch, packages.epoch as epoch,\
                packages.release as release, packages.version as version,\
                obsoletes.name as oname, obsoletes.epoch as oepoch,\
                obsoletes.release as orelease, obsoletes.version as oversion,\
                obsoletes.flags as oflags\
                from obsoletes,packages where obsoletes.pkgKey = packages.pkgKey")
            for ob in cur:
                # If the package that is causing the obsoletes is excluded
                # continue without processing the obsoletes
                if self._pkgKeyExcluded(rep, ob['pkgKey']):
                    continue
                    
                key = ( _share_data(ob['name']), _share_data(ob['arch']),
                        _share_data(ob['epoch']), _share_data(ob['version']),
                        _share_data(ob['release']))
                (n,f,e,v,r) = ( _share_data(ob['oname']),
                                _share_data(ob['oflags']),
                                _share_data(ob['oepoch']),
                                _share_data(ob['oversion']),
                                _share_data(ob['orelease']))

                key = _share_data(key)
                val = _share_data((n,f,(e,v,r)))
                obsoletes.setdefault(key,[]).append(val)

        return obsoletes

    @catchSqliteException
    def getPackageDetails(self,pkgId):
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, "select * from packages where pkgId = ?", (pkgId,))
            for ob in cur:
                return ob
    
    @catchSqliteException
    def _getListofPackageDetails(self, pkgId_list):
        pkgs = []
        if len(pkgId_list) == 0:
            return pkgs
        pkgid_query = str(tuple(pkgId_list))

        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, "select * from packages where pkgId in %s" %(pkgid_query,))
            #executeSQL(cur, "select * from packages where pkgId in %s" %(pkgid_query,))            
            for ob in cur:
                pkgs.append(ob)
        
        return pkgs
        
    @catchSqliteException
    def _search_get_memoize(self, prcotype):
        if not hasattr(self, '_memoize_' + prcotype):
            memoize = {}

            for (rep,cache) in self.primarydb.items():
                if rep in self._all_excludes:
                    continue

                cur = cache.cursor()
                executeSQL(cur, "select * from %s" % prcotype)
                for x in cur:
                    val = (_share_data(x['name']), _share_data(x['flags']),
                           (_share_data(x['epoch']), _share_data(x['version']),
                            _share_data(x['release'])))
                    val = _share_data(val)
                    key = (rep, val[0])
                    pkgkey = _share_data(x['pkgKey'])
                    val = (pkgkey, val)
                    memoize.setdefault(key, []).append(val)
            setattr(self, '_memoize_' + prcotype, memoize)
        return getattr(self, '_memoize_' + prcotype)

    @catchSqliteException
    def _search(self, prcotype, name, flags, version):
        if flags == 0:
            flags = None
        if type(version) in (str, type(None), unicode):
            req = (name, flags, rpmUtils.miscutils.stringToVersion(
                version))
        elif type(version) in (tuple, list): # would this ever be a list?
            req = (name, flags, version)

        prcotype = _share_data(prcotype)
        req      = _share_data(req)
        if self._search_cache[prcotype].has_key(req):
            return self._search_cache[prcotype][req]

        result = { }

        # Requires is the biggest hit, pre-loading provides actually hurts
        if prcotype != 'requires':
            primarydb_items = self.primarydb.items()
            preload = False
        else:
            primarydb_items = []
            preload = True
            memoize = self._search_get_memoize(prcotype)
            for (rep,cache) in self.primarydb.items():
                if rep in self._all_excludes:
                    continue

                tmp = {}
                for x in memoize.get((rep, name), []):
                    pkgkey, val = x
                    if rpmUtils.miscutils.rangeCompare(req, val):
                        tmp.setdefault(pkgkey, []).append(val)
                for pkgKey, hits in tmp.iteritems():
                    if self._pkgKeyExcluded(rep, pkgKey):
                        continue
                    result[self._packageByKey(rep, pkgKey)] = hits

        for (rep,cache) in primarydb_items:
            if rep in self._all_excludes:
                continue

            cur = cache.cursor()
            executeSQL(cur, "select * from %s where name=?" % prcotype,
                       (name,))
            tmp = { }
            for x in cur:
                val = (_share_data(x['name']), _share_data(x['flags']),
                       (_share_data(x['epoch']), _share_data(x['version']),
                        _share_data(x['release'])))
                val = _share_data(val)
                if rpmUtils.miscutils.rangeCompare(req, val):
                    tmp.setdefault(x['pkgKey'], []).append(val)
            for pkgKey, hits in tmp.iteritems():
                if self._pkgKeyExcluded(rep, pkgKey):
                    continue
                result[self._packageByKey(rep, pkgKey)] = hits

        if prcotype != 'provides' or name[0] != '/':
            if not preload:
                self._search_cache[prcotype][req] = result
            return result

        if not misc.re_primary_filename(name):
            # if its not in the primary.xml files
            # search the files.xml file info
            for pkg in self.searchFiles(name, strict=True):
                result[pkg] = [(name, None, None)]
            if not preload:
                self._search_cache[prcotype][req] = result
            return result

        # If it is a filename, search the primary.xml file info
        for (rep,cache) in self.primarydb.items():
            if rep in self._all_excludes:
                continue

            cur = cache.cursor()
            executeSQL(cur, "select DISTINCT pkgKey from files where name = ?", (name,))
            for ob in cur:
                if self._pkgKeyExcluded(rep, ob['pkgKey']):
                    continue
                result[self._packageByKey(rep, ob['pkgKey'])] = [(name, None, None)]
        self._search_cache[prcotype][req] = result
        return result

    def getProvides(self, name, flags=None, version=(None, None, None)):
        return self._search("provides", name, flags, version)

    def getRequires(self, name, flags=None, version=(None, None, None)):
        return self._search("requires", name, flags, version)

    
    @catchSqliteException
    def searchPrco(self, name, prcotype):
        """return list of packages having prcotype name (any evr and flag)"""
        glob = True
        querytype = 'glob'
        if not misc.re_glob(name):
            glob = False
            querytype = '='

        results = []
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, "select DISTINCT pkgKey from %s where name %s ?" % (prcotype,querytype), (name,))
            self._sql_pkgKey2po(rep, cur, results)
        
        # If it's not a provides or a filename, we are done
        if prcotype != "provides" or name[0] != '/':
            if not glob:
                return results

        # If it is a filename, search the primary.xml file info
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, "select DISTINCT pkgKey from files where name %s ?" % querytype, (name,))
            self._sql_pkgKey2po(rep, cur, results)

        # if its in the primary.xml files then skip the other check
        if misc.re_primary_filename(name) and not glob:
            return misc.unique(results)

        # If it is a filename, search the files.xml file info
        results.extend(self.searchFiles(name))
        return misc.unique(results)
        
        
        #~ #FIXME - comment this all out below here
        #~ for (rep,cache) in self.filelistsdb.items():
            #~ cur = cache.cursor()
            #~ (dirname,filename) = os.path.split(name)
            #~ # FIXME: why doesn't this work???
            #~ if 0: # name.find('%') == -1: # no %'s in the thing safe to LIKE
                #~ executeSQL(cur, "select packages.pkgId as pkgId,\
                    #~ filelist.dirname as dirname,\
                    #~ filelist.filetypes as filetypes,\
                    #~ filelist.filenames as filenames \
                    #~ from packages,filelist where \
                    #~ (filelist.dirname LIKE ? \
                    #~ OR (filelist.dirname LIKE ? AND\
                    #~ filelist.filenames LIKE ?))\
                    #~ AND (filelist.pkgKey = packages.pkgKey)", (name,dirname,filename))
            #~ else: 
                #~ executeSQL(cur, "select packages.pkgId as pkgId,\
                    #~ filelist.dirname as dirname,\
                    #~ filelist.filetypes as filetypes,\
                    #~ filelist.filenames as filenames \
                    #~ from filelist,packages where dirname = ? AND filelist.pkgKey = packages.pkgKey" , (dirname,))

            #~ matching_ids = []
            #~ for res in cur:
                #~ if self._excluded(rep, res['pkgId']):
                    #~ continue
                
                #~ #FIXME - optimize the look up here by checking for single-entry filenames
                #~ quicklookup = {}
                #~ for fn in decodefilenamelist(res['filenames']):
                    #~ quicklookup[fn] = 1
                
                #~ # If it matches the dirname, that doesnt mean it matches
                #~ # the filename, check if it does
                #~ if filename and not quicklookup.has_key(filename):
                    #~ continue
                
                #~ matching_ids.append(str(res['pkgId']))
                
            
            #~ pkgs = self._getListofPackageDetails(matching_ids)
            #~ for pkg in pkgs:
                #~ results.append(self.pc(rep,pkg))
        
        #~ return results

    def searchProvides(self, name):
        """return list of packages providing name (any evr and flag)"""
        return self.searchPrco(name, "provides")
                
    def searchRequires(self, name):
        """return list of packages requiring name (any evr and flag)"""
        return self.searchPrco(name, "requires")

    def searchObsoletes(self, name):
        """return list of packages obsoleting name (any evr and flag)"""
        return self.searchPrco(name, "obsoletes")

    def searchConflicts(self, name):
        """return list of packages conflicting with name (any evr and flag)"""
        return self.searchPrco(name, "conflicts")


    def db2class(self, db, nevra_only=False):
        print 'die die die die die db2class'
        pass
        class tmpObject:
            pass
        y = tmpObject()
        
        y.nevra = (db['name'],db['epoch'],db['version'],db['release'],db['arch'])
        y.sack = self
        y.pkgId = db['pkgId']
        if nevra_only:
            return y
        
        y.hdrange = {'start': db['rpm_header_start'],'end': db['rpm_header_end']}
        y.location = {'href': db['location_href'],'value': '', 'base': db['location_base']}
        y.checksum = {'pkgid': 'YES','type': db['checksum_type'], 
                    'value': db['pkgId'] }
        y.time = {'build': db['time_build'], 'file': db['time_file'] }
        y.size = {'package': db['size_package'], 'archive': db['size_archive'], 'installed': db['size_installed'] }
        y.info = {'summary': db['summary'], 'description': db['description'],
                'packager': db['rpm_packager'], 'group': db['rpm_group'],
                'buildhost': db['rpm_buildhost'], 'sourcerpm': db['rpm_sourcerpm'],
                'url': db['url'], 'vendor': db['rpm_vendor'], 'license': db['rpm_license'] }
        return y

    @catchSqliteException
    def returnNewestByNameArch(self, naTup=None, patterns=None):

        # If naTup is set do it from the database otherwise use our parent's
        # returnNewestByNameArch
        if (not naTup):
            return yumRepo.YumPackageSack.returnNewestByNameArch(self, naTup,
                                                                 patterns)

        # First find all packages that fulfill naTup
        allpkg = []
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, "select pkgKey from packages where name=? and arch=?",naTup)
            self._sql_pkgKey2po(rep, cur, allpkg)
        
        # if we've got zilch then raise
        if not allpkg:
            raise Errors.PackageSackError, 'No Package Matching %s.%s' % naTup
        return misc.newestInList(allpkg)

    @catchSqliteException
    def returnNewestByName(self, name=None):
        # If name is set do it from the database otherwise use our parent's
        # returnNewestByName
        if (not name):
            return yumRepo.YumPackageSack.returnNewestByName(self, name)

        # First find all packages that fulfill name
        allpkg = []
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, "select pkgKey from packages where name=?", (name,))
            self._sql_pkgKey2po(rep, cur, allpkg)
        
        # if we've got zilch then raise
        if not allpkg:
            raise Errors.PackageSackError, 'No Package Matching %s' % name
        return misc.newestInList(allpkg)

    # Do what packages.matchPackageNames does, but query the DB directly
    @catchSqliteException
    def matchPackageNames(self, pkgspecs):
        matched = []
        exactmatch = []
        unmatched = list(pkgspecs)

        for p in pkgspecs:
            if misc.re_glob(p):
                query = PARSE_QUERY % ({ "op": "glob", "q": p })
                matchres = matched
            else:
                query = PARSE_QUERY % ({ "op": "=", "q": p })
                matchres = exactmatch

            for (rep, db) in self.primarydb.items():
                cur = db.cursor()
                executeSQL(cur, query)
                pmatches = self._sql_pkgKey2po(rep, cur)
                if len(pmatches):
                    unmatched.remove(p)
                matchres.extend(pmatches)

        exactmatch = misc.unique(exactmatch)
        matched = misc.unique(matched)
        unmatched = misc.unique(unmatched)
        return exactmatch, matched, unmatched

    @catchSqliteException
    def _buildPkgObjList(self, repoid=None, patterns=None):
        """Builds a list of packages, only containing nevra information. No
           excludes are done at this stage. """

        if patterns is None:
            patterns = []
        
        returnList = []        
        for (repo,cache) in self.primarydb.items():
            if (repoid == None or repoid == repo.id):
                cur = cache.cursor()

                qsql = """select pkgId, pkgKey, name,epoch,version,release,arch 
                          from packages"""

                pat_sqls = []
                pat_data = []
                for pattern in patterns:
                    for field in ['name', 'sql_nameArch', 'sql_nameVerRelArch',
                                  'sql_nameVer', 'sql_nameVerRel',
                                  'sql_envra', 'sql_nevra']:
                        pat_sqls.append("%s GLOB ?" % field)
                        pat_data.append(pattern)
                if pat_sqls:
                    qsql = _FULL_PARSE_QUERY_BEG + " OR ".join(pat_sqls)
                executeSQL(cur, qsql, pat_data)
                for x in cur:
                    if self._key2pkg.get(repo, {}).has_key(x['pkgKey']):
                        po = self._key2pkg[repo][x['pkgKey']]
                    else:
                        po = self.pc(repo,x)
                        self._key2pkg.setdefault(repo, {})[po.pkgKey] = po
                    returnList.append(po)
        if not patterns:
            self.pkgobjlist = returnList
        return returnList
                
    def returnPackages(self, repoid=None, patterns=None):
        """Returns a list of packages, only containing nevra information. The
           packages are processed for excludes. Note that patterns is just
           a hint, we are free it ignore it. """

        # Skip unused repos completely, Eg. *-source
        skip_all = True
        for repo in self.added:
            if repo not in self._all_excludes:
                skip_all = False

        if skip_all:
            return []

        if hasattr(self, 'pkgobjlist'):
            pkgobjlist = self.pkgobjlist
        else:
            pkgobjlist = self._buildPkgObjList(repoid, patterns)

        returnList = []
        for po in pkgobjlist:
            if self._pkgExcluded(po):
                continue
            returnList.append(po)

        return returnList

    @catchSqliteException
    def searchNevra(self, name=None, epoch=None, ver=None, rel=None, arch=None):        
        """return list of pkgobjects matching the nevra requested"""
        returnList = []
        
        # make sure some dumbass didn't pass us NOTHING to search on
        empty = True
        for arg in (name, epoch, ver, rel, arch):
            if arg:
                empty = False
        if empty:
            return returnList
        
        # make up our execute string
        q = "select pkgKey from packages WHERE"
        for (col, var) in [('name', name), ('epoch', epoch), ('version', ver),
                           ('arch', arch), ('release', rel)]:
            if var:
                if q[-5:] != 'WHERE':
                    q = q + ' AND %s = "%s"' % (col, var)
                else:
                    q = q + ' %s = "%s"' % (col, var)
            
        # Search all repositories            
        for (rep,cache) in self.primarydb.items():
            cur = cache.cursor()
            executeSQL(cur, q)
            self._sql_pkgKey2po(rep, cur, returnList)
        return returnList
    
    @catchSqliteException
    def excludeArchs(self, archlist):
        """excludes incompatible arches - archlist is a list of compat arches"""
        
        sarchlist = map(lambda x: "'%s'" % x , archlist)
        arch_query = ",".join(sarchlist)

        for (rep, cache) in self.primarydb.items():
            cur = cache.cursor()

            # First of all, make sure this isn't a *-source repo or something
            # where we'll be excluding everything.
            has_arch = False
            executeSQL(cur, "SELECT DISTINCT arch FROM packages")
            for row in cur:
                if row[0] in archlist:
                    has_arch = True
                    break
            if not has_arch:
                self._delAllPackages(rep)
                return
            
            myq = "select pkgId, pkgKey from packages where arch not in (%s)" % arch_query
            executeSQL(cur, myq)
            for row in cur:
                obj = self.pc(rep, row)
                self.delPackage(obj)

# Simple helper functions

# Return a string representing filenamelist (filenames can not contain /)
def encodefilenamelist(filenamelist):
    return '/'.join(filenamelist)

# Return a list representing filestring (filenames can not contain /)
def decodefilenamelist(filenamestring):
    filenamestring = filenamestring.replace('//', '/')
    return filenamestring.split('/')

# Return a string representing filetypeslist
# filetypes should be file, dir or ghost
def encodefiletypelist(filetypelist):
    result = ''
    ft2string = {'file': 'f','dir': 'd','ghost': 'g'}
    for x in filetypelist:
        result += ft2string[x]
    return result

# Return a list representing filetypestring
# filetypes should be file, dir or ghost
def decodefiletypelist(filetypestring):
    string2ft = {'f':'file','d': 'dir','g': 'ghost'}
    return [string2ft[x] for x in filetypestring]


# Query used by matchPackageNames
# op is either '=' or 'like', q is the search term
# Check against name, nameArch, nameVerRelArch, nameVer, nameVerRel,
# envra, nevra
PARSE_QUERY = """
select pkgKey from packages
where name %(op)s '%(q)s'
   or name || '.' || arch %(op)s '%(q)s'
   or name || '-' || version %(op)s '%(q)s'
   or name || '-' || version || '-' || release %(op)s '%(q)s'
   or name || '-' || version || '-' || release || '.' || arch %(op)s '%(q)s'
   or epoch || ':' || name || '-' || version || '-' || release || '.' || arch %(op)s '%(q)s'
   or name || '-' || epoch || ':' || version || '-' || release || '.' || arch %(op)s '%(q)s'
"""

# This is roughly the same as above, and used by _buildPkgObjList().
#  Use " to quote because we using ? ... and sqlutils.QmarkToPyformat gets
# confused.
_FULL_PARSE_QUERY_BEG = """
SELECT pkgId,pkgKey,name,epoch,version,release,arch,
  name || "." || arch AS sql_nameArch,
  name || "-" || version || "-" || release || "." || arch AS sql_nameVerRelArch,
  name || "-" || version AS sql_nameVer,
  name || "-" || version || "-" || release AS sql_nameVerRel,
  epoch || ":" || name || "-" || version || "-" || release || "." || arch AS sql_envra,
  name || "-" || epoch || ":" || version || "-" || release || "." || arch AS sql_nevra
  FROM packages
  WHERE
"""
