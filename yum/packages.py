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
# Copyright 2004 Duke University 
# Written by Seth Vidal <skvidal at phy.duke.edu>

"""
Classes and functions dealing with rpm package representations.
"""

import rpm
import os
import os.path
import misc
import re
import fnmatch
import stat
import warnings
from rpmUtils import RpmUtilsError
import rpmUtils.arch
import rpmUtils.miscutils
import Errors
import errno

import urlparse
urlparse.uses_fragment.append("media")

# For verify
import pwd
import grp

def comparePoEVR(po1, po2):
    """
    Compare two PackageEVR objects.
    """
    (e1, v1, r1) = (po1.epoch, po1.version, po1.release)
    (e2, v2, r2) = (po2.epoch, po2.version, po2.release)
    return rpmUtils.miscutils.compareEVR((e1, v1, r1), (e2, v2, r2))

def buildPkgRefDict(pkgs, casematch=True):
    """take a list of pkg objects and return a dict the contains all the possible
       naming conventions for them eg: for (name,i386,0,1,1)
       dict[name] = (name, i386, 0, 1, 1)
       dict[name.i386] = (name, i386, 0, 1, 1)
       dict[name-1-1.i386] = (name, i386, 0, 1, 1)       
       dict[name-1] = (name, i386, 0, 1, 1)       
       dict[name-1-1] = (name, i386, 0, 1, 1)
       dict[0:name-1-1.i386] = (name, i386, 0, 1, 1)
       dict[name-0:1-1.i386] = (name, i386, 0, 1, 1)
       """
    pkgdict = {}
    for pkg in pkgs:
        (n, a, e, v, r) = pkg.pkgtup
        if not casematch:
            n = n.lower()
            a = a.lower()
            e = e.lower()
            v = v.lower()
            r = r.lower()
        name = n
        nameArch = '%s.%s' % (n, a)
        nameVerRelArch = '%s-%s-%s.%s' % (n, v, r, a)
        nameVer = '%s-%s' % (n, v)
        nameVerRel = '%s-%s-%s' % (n, v, r)
        envra = '%s:%s-%s-%s.%s' % (e, n, v, r, a)
        nevra = '%s-%s:%s-%s.%s' % (n, e, v, r, a)
        for item in [name, nameArch, nameVerRelArch, nameVer, nameVerRel, envra, nevra]:
            if not pkgdict.has_key(item):
                pkgdict[item] = []
            pkgdict[item].append(pkg)
            
    return pkgdict
       
def parsePackages(pkgs, usercommands, casematch=0,
                  unique='repo-epoch-name-version-release-arch'):
    """matches up the user request versus a pkg list:
       for installs/updates available pkgs should be the 'others list' 
       for removes it should be the installed list of pkgs
       takes an optional casematch option to determine if case should be matched
       exactly. Defaults to not matching."""

    pkgdict = buildPkgRefDict(pkgs, bool(casematch))
    exactmatch = []
    matched = []
    unmatched = []
    for command in usercommands:
        if not casematch:
            command = command.lower()
        if pkgdict.has_key(command):
            exactmatch.extend(pkgdict[command])
            del pkgdict[command]
        else:
            # anything we couldn't find a match for
            # could mean it's not there, could mean it's a wildcard
            if misc.re_glob(command):
                trylist = pkgdict.keys()
                # command and pkgdict are already lowered if not casematch
                # so case sensitive is always fine
                restring = fnmatch.translate(command)
                regex = re.compile(restring)
                foundit = 0
                for item in trylist:
                    if regex.match(item):
                        matched.extend(pkgdict[item])
                        del pkgdict[item]
                        foundit = 1
 
                if not foundit:    
                    unmatched.append(command)
                    
            else:
                unmatched.append(command)

    unmatched = misc.unique(unmatched)
    if unique == 'repo-epoch-name-version-release-arch': # pkg.__hash__
        matched    = misc.unique(matched)
        exactmatch = misc.unique(exactmatch)
    elif unique == 'repo-pkgkey': # So we get all pkg entries from a repo
        def pkgunique(pkgs):
            u = {}
            for pkg in pkgs:
                mark = "%s%s" % (pkg.repo.id, pkg.pkgKey)
                u[mark] = pkg
            return u.values()
        matched    = pkgunique(matched)
        exactmatch = pkgunique(exactmatch)
    else:
        raise ValueError, "Bad value for unique: %s" % unique
    return exactmatch, matched, unmatched

class FakeRepository:
    """Fake repository class for use in rpmsack package objects"""
    def __init__(self, repoid):
        self.id = repoid

    def __cmp__(self, other):
        if self.id > other.id:
            return 1
        elif self.id < other.id:
            return -1
        else:
            return 0
        
    def __hash__(self):
        return hash(self.id)

    def __str__(self):
        return self.id


# goal for the below is to have a packageobject that can be used by generic
# functions independent of the type of package - ie: installed or available
class PackageObject(object):
    """Base Package Object - sets up the default storage dicts and the
       most common returns"""
       
    def __init__(self):
        self.name = None
        self.version = None
        self.release = None
        self.epoch = None
        self.arch = None
        # self.pkgtup = (self.name, self.arch, self.epoch, self.version, self.release)
        self._checksums = [] # (type, checksum, id(0,1)
        
    def __str__(self):
        if self.epoch == '0':
            out = '%s-%s-%s.%s' % (self.name, 
                                   self.version,
                                   self.release, 
                                   self.arch)
        else:
            out = '%s:%s-%s-%s.%s' % (self.epoch,
                                      self.name,  
                                      self.version, 
                                      self.release, 
                                      self.arch)
        return out

    def __cmp__(self, other):
        """ Compare packages. """
        if not other:
            return 1
        ret = cmp(self.name, other.name)
        if ret == 0:
            ret = comparePoEVR(self, other)
        if ret == 0:
            ret = cmp(self.arch, other.arch)
        return ret

    def __repr__(self):
        return "<%s : %s (%s)>" % (self.__class__.__name__, str(self),hex(id(self))) 

    def returnSimple(self, varname):
        warnings.warn("returnSimple() will go away in a future version of Yum.\n",
                      Errors.YumFutureDeprecationWarning, stacklevel=2)
        return getattr(self, varname)

    def returnChecksums(self):
        return self._checksums

    checksums = property(fget=lambda self: self.returnChecksums())
    
    def returnIdSum(self):
        for (csumtype, csum, csumid) in self.checksums:
            if csumid:
                return (csumtype, csum)

class RpmBase(object):
    """return functions and storage for rpm-specific data"""

    def __init__(self):
        self.prco = { misc.share_data('obsoletes'): (),
                      misc.share_data('conflicts'): (),
                      misc.share_data('requires') : (),
                      misc.share_data('provides') : () }
        self.files = { misc.share_data('file')  : [],
                       misc.share_data('dir')   : [],
                       misc.share_data('ghost') : [] }
        self._changelog = [] # (ctime, cname, ctext)
        self.licenses = []
        self._hash = None

    #  Do we still need __eq__ and __ne__ given that
    # PackageObject has a working __cmp__?
    def __eq__(self, other):
        if not other: # check if other not is a package object. 
            return False
        if comparePoEVR(self, other) == 0 and self.arch == other.arch and self.name == other.name:
            return True
        return False

    def __ne__(self, other):
        if not other:
            return True
        if comparePoEVR(self, other) != 0 or self.arch != other.arch or self.name != other.name:
            return True
        return False
       
    def returnEVR(self):
        return PackageEVR(self.epoch, self.version, self.release)
    
    def __hash__(self):
        if self._hash is None:
            mystr = '%s - %s:%s-%s-%s.%s' % (self.repo.id, self.epoch, self.name,
                                         self.version, self.release, self.arch)
            self._hash = hash(mystr)
        return self._hash
        
    def returnPrco(self, prcotype, printable=False):
        """return list of provides, requires, conflicts or obsoletes"""
        
        prcos = []
        if self.prco.has_key(prcotype):
            prcos = self.prco[prcotype]

        if printable:
            results = []
            for prco in prcos:
                results.append(misc.prco_tuple_to_string(prco))
            return results

        return prcos

    def checkPrco(self, prcotype, prcotuple):
        """returns 1 or 0 if the pkg contains the requested tuple/tuple range"""
        # get rid of simple cases - nothing
        if not self.prco.has_key(prcotype):
            return 0
        # exact match    
        if prcotuple in self.prco[prcotype]:
            return 1
        else:
            # make us look it up and compare
            (reqn, reqf, (reqe, reqv ,reqr)) = prcotuple
            if reqf is not None:
                return self.inPrcoRange(prcotype, prcotuple)
            else:
                for (n, f, (e, v, r)) in self.returnPrco(prcotype):
                    if reqn == n:
                        return 1

        return 0

    def inPrcoRange(self, prcotype, reqtuple):
        """returns true if the package has a the prco that satisfies 
           the reqtuple range, assume false.
           Takes: prcotype, requested prco tuple"""
        return bool(self.matchingPrcos(prcotype, reqtuple))

    def matchingPrcos(self, prcotype, reqtuple):
        # we only ever get here if we have a versioned prco
        # nameonly shouldn't ever raise it
        (reqn, reqf, (reqe, reqv, reqr)) = reqtuple
        # however, just in case
        # find the named entry in pkgobj, do the comparsion
        result = []
        for (n, f, (e, v, r)) in self.returnPrco(prcotype):
            if reqn != n:
                continue

            if f == '=':
                f = 'EQ'
            if f != 'EQ' and prcotype == 'provides':
                # isn't this odd, it's not 'EQ' and it is a provides
                # - it really should be EQ
                # use the pkgobj's evr for the comparison
                if e is None:
                    e = self.epoch
                if v is None:
                    v = self.ver
                if r is None:
                    r = self.rel
                #(e, v, r) = (self.epoch, self.ver, self.rel)

            matched = rpmUtils.miscutils.rangeCompare(
                reqtuple, (n, f, (e, v, r)))
            if matched:
                result.append((n, f, (e, v, r)))

        return result


        
    def returnChangelog(self):
        """return changelog entries"""
        return self._changelog
        
    def returnFileEntries(self, ftype='file'):
        """return list of files based on type"""
        # fixme - maybe should die - use direct access to attribute
        if self.files:
            if self.files.has_key(ftype):
                return self.files[ftype]
        return []
            
    def returnFileTypes(self):
        """return list of types of files in the package"""
        # maybe should die - use direct access to attribute
        return self.files.keys()

    def returnPrcoNames(self, prcotype):
        results = []
        lists = self.returnPrco(prcotype)
        for (name, flag, vertup) in lists:
            results.append(name)
        return results

    def getProvidesNames(self):
        warnings.warn('getProvidesNames() will go away in a future version of Yum.\n',
                      Errors.YumDeprecationWarning, stacklevel=2)
        return self.provides_names

    def simpleFiles(self, ftype='files'):
        if self.files and self.files.has_key(ftype):
            return self.files[ftype]
        return []
    
    filelist = property(fget=lambda self: self.returnFileEntries(ftype='file'))
    dirlist = property(fget=lambda self: self.returnFileEntries(ftype='dir'))
    ghostlist = property(fget=lambda self: self.returnFileEntries(ftype='ghost'))
    requires = property(fget=lambda self: self.returnPrco('requires'))
    provides = property(fget=lambda self: self.returnPrco('provides'))
    obsoletes = property(fget=lambda self: self.returnPrco('obsoletes'))
    conflicts = property(fget=lambda self: self.returnPrco('conflicts'))
    provides_names = property(fget=lambda self: self.returnPrcoNames('provides'))
    requires_names = property(fget=lambda self: self.returnPrcoNames('requires'))
    conflicts_names = property(fget=lambda self: self.returnPrcoNames('conflicts'))
    obsoletes_names = property(fget=lambda self: self.returnPrcoNames('obsoletes'))
    provides_print = property(fget=lambda self: self.returnPrco('provides', True))
    requires_print = property(fget=lambda self: self.returnPrco('requires', True))
    conflicts_print = property(fget=lambda self: self.returnPrco('conflicts', True))
    obsoletes_print = property(fget=lambda self: self.returnPrco('obsoletes', True))
    changelog = property(fget=lambda self: self.returnChangelog())
    EVR = property(fget=lambda self: self.returnEVR())
    
class PackageEVR:

    """
    A comparable epoch, version, and release representation.
    """
    
    def __init__(self,e,v,r):
        self.epoch = e
        self.ver = v
        self.rel = r
        
    def compare(self,other):
        return rpmUtils.miscutils.compareEVR((self.epoch, self.ver, self.rel), (other.epoch, other.ver, other.rel))
    
    def __lt__(self, other):
        if self.compare(other) < 0:
            return True
        return False

        
    def __gt__(self, other):
        if self.compare(other) > 0:
            return True
        return False

    def __le__(self, other):
        if self.compare(other) <= 0:
            return True
        return False

    def __ge__(self, other):
        if self.compare(other) >= 0:
            return True
        return False

    def __eq__(self, other):
        if self.compare(other) == 0:
            return True
        return False

    def __ne__(self, other):
        if self.compare(other) != 0:
            return True
        return False
    


class YumAvailablePackage(PackageObject, RpmBase):
    """derived class for the  packageobject and RpmBase packageobject yum
       uses this for dealing with packages in a repository"""

    def __init__(self, repo, pkgdict = None):
        PackageObject.__init__(self)
        RpmBase.__init__(self)
        
        self.repoid = repo.id
        self.repo = repo
        self.state = None
        self._loadedfiles = False

        if pkgdict != None:
            self.importFromDict(pkgdict)
            self.ver = self.version
            self.rel = self.release
        self.pkgtup = (self.name, self.arch, self.epoch, self.version, self.release)

    def dropCachedData(self):
        self.files = { misc.share_data('file')  : [],
                       misc.share_data('dir')   : [],
                       misc.share_data('ghost') : [] }
        self._loadedfiles = False

        # RpmBase, but screw it
        self._hash = None

    def exclude(self):
        """remove self from package sack"""
        self.repo.sack.delPackage(self)
        
    def printVer(self):
        """returns a printable version string - including epoch, if it's set"""
        if self.epoch != '0':
            ver = '%s:%s-%s' % (self.epoch, self.version, self.release)
        else:
            ver = '%s-%s' % (self.version, self.release)
        
        return ver
    
    def compactPrint(self):
        ver = self.printVer()
        return "%s.%s %s" % (self.name, self.arch, ver)

    def _size(self):
        return self.packagesize
    
    def _remote_path(self):
        return self.relativepath

    def _remote_url(self):
        """returns a URL that can be used for downloading the package.
        Note that if you're going to download the package in your tool,
        you should use self.repo.getPackage."""
        base = self.basepath
        if base:
            return urlparse.urljoin(base, self.remote_path)
        return urlparse.urljoin(self.repo.urls[0], self.remote_path)
    
    size = property(_size)
    remote_path = property(_remote_path)
    remote_url = property(_remote_url)

    def _committer(self):
        "Returns the name of the last person to do a commit to the changelog."

        if hasattr(self, '_committer_ret'):
            return self._committer_ret

        def _nf2ascii(x):
            """ does .encode("ascii", "replace") but it never fails. """
            ret = []
            for val in x:
                if ord(val) >= 128:
                    val = '?'
                ret.append(val)
            return "".join(ret)

        if not len(self.changelog): # Empty changelog is _possible_ I guess
            self._committer_ret = self.packager
            return self._committer_ret
        val = self.changelog[0][1]
        # Chagnelog data is in multiple locale's, so we convert to ascii
        # ignoring "bad" chars.
        val = _nf2ascii(val)
        # Hacky way to get rid of version numbers...
        self._committer_ret = re.sub("""> .*""", '>', val)
        return self._committer_ret

    committer  = property(_committer)
    
    def _committime(self):
        "Returns the time of the last commit to the changelog."

        if hasattr(self, '_committime_ret'):
            return self._committime_ret

        if not len(self.changelog): # Empty changelog is _possible_ I guess
            self._committime_ret = self.buildtime
            return self._committime_ret
        
        self._committime_ret = self.changelog[0][0]
        return self._committime_ret

    committime = property(_committime)

    def getDiscNum(self):
        if self.basepath is None:
            return None
        (scheme, netloc, path, query, fragid) = urlparse.urlsplit(self.basepath)
        if scheme == "media":
            if len(fragid) == 0:
                return 0
            return int(fragid)
        return None
    
    def returnHeaderFromPackage(self):
        rpmfile = self.localPkg()
        ts = rpmUtils.transaction.initReadOnlyTransaction()
        hdr = rpmUtils.miscutils.hdrFromPackage(ts, rpmfile)
        return hdr
        
    def returnLocalHeader(self):
        """returns an rpm header object from the package object's local
           header cache"""
        
        if os.path.exists(self.localHdr()):
            try: 
                hlist = rpm.readHeaderListFromFile(self.localHdr())
                hdr = hlist[0]
            except (rpm.error, IndexError):
                raise Errors.RepoError, 'Cannot open package header'
        else:
            raise Errors.RepoError, 'Package Header Not Available'

        return hdr

       
    def localPkg(self):
        """return path to local package (whether it is present there, or not)"""
        if not hasattr(self, 'localpath'):
            rpmfn = os.path.basename(self.remote_path)
            self.localpath = self.repo.pkgdir + '/' + rpmfn
        return self.localpath

    def localHdr(self):
        """return path to local cached Header file downloaded from package 
           byte ranges"""
           
        if not hasattr(self, 'hdrpath'):
            pkgname = os.path.basename(self.remote_path)
            hdrname = pkgname[:-4] + '.hdr'
            self.hdrpath = self.repo.hdrdir + '/' + hdrname

        return self.hdrpath
    
    def verifyLocalPkg(self):
        """check the package checksum vs the localPkg
           return True if pkg is good, False if not"""
           
        (csum_type, csum) = self.returnIdSum()
        
        try:
            filesum = misc.checksum(csum_type, self.localPkg())
        except Errors.MiscError:
            return False
        
        if filesum != csum:
            return False
        
        return True
        
    def prcoPrintable(self, prcoTuple):
        """convert the prco tuples into a nicer human string"""
        warnings.warn('prcoPrintable() will go away in a future version of Yum.\n',
                      Errors.YumDeprecationWarning, stacklevel=2)
        return misc.prco_tuple_to_string(prcoTuple)

    def requiresList(self):
        """return a list of requires in normal rpm format"""
        return self.requires_print

    def returnChecksums(self):
        return [(self.checksum_type, self.pkgId, 1)]
        
    def importFromDict(self, pkgdict):
        """handles an mdCache package dictionary item to populate out 
           the package information"""
        
        # translates from the pkgdict, populating out the information for the
        # packageObject
        
        if hasattr(pkgdict, 'nevra'):
            (n, e, v, r, a) = pkgdict.nevra
            self.name = n
            self.epoch = e
            self.version = v
            self.arch = a
            self.release = r
        
        if hasattr(pkgdict, 'time'):
            self.buildtime = pkgdict.time['build']
            self.filetime = pkgdict.time['file']
        
        if hasattr(pkgdict, 'size'):
            self.packagesize = pkgdict.size['package']
            self.archivesize = pkgdict.size['archive']
            self.installedsize = pkgdict.size['installed']
        
        if hasattr(pkgdict, 'location'):
            if not pkgdict.location.has_key('base'):
                url = None
            elif pkgdict.location['base'] == '':
                url = None
            else:
                url = pkgdict.location['base']

            self.basepath = url
            self.relativepath = pkgdict.location['href']

        if hasattr(pkgdict, 'hdrange'):
            self.hdrstart = pkgdict.hdrange['start']
            self.hdrend = pkgdict.hdrange['end']
        
        if hasattr(pkgdict, 'info'):
            for item in ['summary', 'description', 'packager', 'group',
                         'buildhost', 'sourcerpm', 'url', 'vendor']:
                setattr(self, item, pkgdict.info[item])
            self.summary = self.summary.replace('\n', '')
            
            self.licenses.append(pkgdict.info['license'])
        
        if hasattr(pkgdict, 'files'):
            for fn in pkgdict.files:
                ftype = pkgdict.files[fn]
                if not self.files.has_key(ftype):
                    self.files[ftype] = []
                self.files[ftype].append(fn)
        
        if hasattr(pkgdict, 'prco'):
            for rtype in pkgdict.prco:
                for rdict in pkgdict.prco[rtype]:
                    name = rdict['name']
                    f = e = v = r  = None
                    if rdict.has_key('flags'): f = rdict['flags']
                    if rdict.has_key('epoch'): e = rdict['epoch']
                    if rdict.has_key('ver'): v = rdict['ver']
                    if rdict.has_key('rel'): r = rdict['rel']
                    self.prco[rtype].append((name, f, (e,v,r)))

        if hasattr(pkgdict, 'changelog'):
            for cdict in pkgdict.changelog:
                date = text = author = None
                if cdict.has_key('date'): date = cdict['date']
                if cdict.has_key('value'): text = cdict['value']
                if cdict.has_key('author'): author = cdict['author']
                self._changelog.append((date, author, text))
        
        if hasattr(pkgdict, 'checksum'):
            ctype = pkgdict.checksum['type']
            csum = pkgdict.checksum['value']
            csumid = pkgdict.checksum['pkgid']
            if csumid is None or csumid.upper() == 'NO':
                csumid = 0
            elif csumid.upper() == 'YES':
                csumid = 1
            else:
                csumid = 0
            self._checksums.append((ctype, csum, csumid))


class YumHeaderPackage(YumAvailablePackage):
    """Package object built from an rpm header"""
    def __init__(self, repo, hdr):
        """hand in an rpm header, we'll assume it's installed and query from there"""
       
        YumAvailablePackage.__init__(self, repo)

        self.hdr = hdr
        self.name = misc.share_data(self.hdr['name'])
        self.arch = misc.share_data(self.hdr['arch'])
        self.epoch = misc.share_data(self.doepoch())
        self.version = misc.share_data(self.hdr['version'])
        self.release = misc.share_data(self.hdr['release'])
        self.ver = self.version
        self.rel = self.release
        self.pkgtup = (self.name, self.arch, self.epoch, self.version, self.release)
        self.summary = misc.share_data(self.hdr['summary'].replace('\n', ''))
        self.description = misc.share_data(self.hdr['description'])
        self.pkgid = self.hdr[rpm.RPMTAG_SHA1HEADER]
        if not self.pkgid:
            self.pkgid = "%s.%s" %(self.hdr['name'], self.hdr['buildtime'])
        self.packagesize = self.hdr['size']
        self.__mode_cache = {}
        self.__prcoPopulated = False
        
    def __str__(self):
        if self.epoch == '0':
            val = '%s-%s-%s.%s' % (self.name, self.version, self.release,
                                        self.arch)
        else:
            val = '%s:%s-%s-%s.%s' % (self.epoch,self.name, self.version,
                                           self.release, self.arch)
        return val

    def dropCachedData(self):
        self.__mode_cache = {}

        self.prco = { misc.share_data('obsoletes'): (),
                      misc.share_data('conflicts'): (),
                      misc.share_data('requires') : (),
                      misc.share_data('provides') : () }
        self.__prcoPopulated = False

        YumAvailablePackage.dropCachedData(self)

    def returnPrco(self, prcotype, printable=False):
        if not self.__prcoPopulated:
            self._populatePrco()
            self.__prcoPopulated = True
        return YumAvailablePackage.returnPrco(self, prcotype, printable)

    def _get_hdr(self):
        return self.hdr

    def _populatePrco(self):
        "Populate the package object with the needed PRCO interface."

        tag2prco = { "OBSOLETE": "obsoletes",
                     "CONFLICT": "conflicts",
                     "REQUIRE":  "requires",
                     "PROVIDE":  "provides" }
        hdr = self._get_hdr()
        for tag in tag2prco:
            name = hdr[getattr(rpm, 'RPMTAG_%sNAME' % tag)]
            name = map(misc.share_data, name)
            if name is None:
                continue

            lst = hdr[getattr(rpm, 'RPMTAG_%sFLAGS' % tag)]
            flag = map(rpmUtils.miscutils.flagToString, lst)
            flag = map(misc.share_data, flag)

            lst = hdr[getattr(rpm, 'RPMTAG_%sVERSION' % tag)]
            vers = map(rpmUtils.miscutils.stringToVersion, lst)
            vers = map(lambda x: (misc.share_data(x[0]), misc.share_data(x[1]),
                                  misc.share_data(x[2])), vers)

            prcotype = tag2prco[tag]
            self.prco[prcotype] = map(misc.share_data, zip(name,flag,vers))
    
    def tagByName(self, tag):
        warnings.warn("tagByName() will go away in a furture version of Yum.\n",
                      Errors.YumFutureDeprecationWarning, stacklevel=2)
        try:
            return getattr(self, tag)
        except AttributeError:
            raise Errors.MiscError, "Unknown header tag %s" % tag

    def __getattr__(self, thing):
        return self.hdr[thing]

    def doepoch(self):
        tmpepoch = self.hdr['epoch']
        if tmpepoch is None:
            epoch = '0'
        else:
            epoch = str(tmpepoch)
        
        return epoch

    def returnLocalHeader(self):
        return self.hdr
    

    def _loadFiles(self):
        files = self.hdr['filenames']
        fileflags = self.hdr['fileflags']
        filemodes = self.hdr['filemodes']
        filetuple = zip(files, filemodes, fileflags)
        if not self._loadedfiles:
            for (fn, mode, flag) in filetuple:
                #garbage checks
                if mode is None or mode == '':
                    if not self.files.has_key('file'):
                        self.files['file'] = []
                    self.files['file'].append(fn)
                    continue
                if not self.__mode_cache.has_key(mode):
                    self.__mode_cache[mode] = stat.S_ISDIR(mode)
          
                if self.__mode_cache[mode]:
                    if not self.files.has_key('dir'):
                        self.files['dir'] = []
                    self.files['dir'].append(fn)
                else:
                    if flag is None:
                        if not self.files.has_key('file'):
                            self.files['file'] = []
                        self.files['file'].append(fn)
                    else:
                        if (flag & 64):
                            if not self.files.has_key('ghost'):
                                self.files['ghost'] = []
                            self.files['ghost'].append(fn)
                            continue
                        if not self.files.has_key('file'):
                            self.files['file'] = []
                        self.files['file'].append(fn)
            self._loadedfiles = True
            
    def returnFileEntries(self, ftype='file'):
        """return list of files based on type"""
        self._loadFiles()
        return YumAvailablePackage.returnFileEntries(self,ftype)
    
    def returnChangelog(self):
        # note - if we think it is worth keeping changelogs in memory
        # then create a _loadChangelog() method to put them into the 
        # self._changelog attr
        if len(self.hdr['changelogname']) > 0:
            return zip(self.hdr['changelogtime'],
                       self.hdr['changelogname'],
                       self.hdr['changelogtext'])
        return []

    def returnChecksums(self):
        raise NotImplementedError()
        
class _CountedReadFile:
    """ Has just a read() method, and keeps a count so we can find out how much
        has been read. Implemented so we can get the real size of the file from
        prelink. """
    
    def __init__(self, fp):
        self.fp = fp
        self.read_size = 0

    def read(self, size):
        ret = self.fp.read(size)
        self.read_size += len(ret)
        return ret

class _PkgVerifyProb:
    """ Holder for each "problem" we find with a pkg.verify(). """
    
    def __init__(self, type, msg, ftypes, fake=False):
        self.type           = type
        self.message        = msg
        self.database_value = None
        self.disk_value     = None
        self.file_types     = ftypes
        self.fake           = fake

    def __cmp__(self, other):
        if other is None:
            return 1
        type2sort = {'type' :  1, 'symlink' : 2, 'checksum' : 3, 'size'    :  4,
                     'user' :  4, 'group'   : 5, 'mode' : 6, 'genchecksum' :  7,
                     'mtime' : 8, 'missing' : 9, 'permissions-missing'     : 10,
                     'state' : 11, 'missingok' : 12, 'ghost' : 13}
        ret = cmp(type2sort[self.type], type2sort[other.type])
        if not ret:
            for attr in ['disk_value', 'database_value', 'file_types']:
                x = getattr(self,  attr)
                y = getattr(other, attr)
                if x is None:
                    assert y is None
                    continue
                ret = cmp(x, y)
                if ret:
                    break
        return ret

# From: lib/rpmvf.h ... not in rpm *sigh*
_RPMVERIFY_MD5      = (1 << 0)
_RPMVERIFY_FILESIZE = (1 << 1)
_RPMVERIFY_LINKTO   = (1 << 2)
_RPMVERIFY_USER     = (1 << 3)
_RPMVERIFY_GROUP    = (1 << 4)
_RPMVERIFY_MTIME    = (1 << 5)
_RPMVERIFY_MODE     = (1 << 6)
_RPMVERIFY_RDEV     = (1 << 7)

_installed_repo = FakeRepository('installed')
_installed_repo.cost = 0
class YumInstalledPackage(YumHeaderPackage):
    """super class for dealing with packages in the rpmdb"""
    def __init__(self, hdr):
        fakerepo = _installed_repo
        YumHeaderPackage.__init__(self, fakerepo, hdr)
        
    def verify(self, patterns=[], deps=False, script=False,
               fake_problems=True, all=False):
        """verify that the installed files match the packaged checksum
           optionally verify they match only if they are in the 'pattern' list
           returns a tuple """
        def _ftype(mode):
            """ Given a "mode" return the name of the type of file. """
            if stat.S_ISREG(mode):  return "file"
            if stat.S_ISDIR(mode):  return "directory"
            if stat.S_ISLNK(mode):  return "symlink"
            if stat.S_ISFIFO(mode): return "fifo"
            if stat.S_ISCHR(mode):  return "character device"
            if stat.S_ISBLK(mode):  return "block device"
            return "<unknown>"

        statemap = {rpm.RPMFILE_STATE_REPLACED : 'replaced',
                    rpm.RPMFILE_STATE_NOTINSTALLED : 'not installed',
                    rpm.RPMFILE_STATE_WRONGCOLOR : 'wrong color',
                    rpm.RPMFILE_STATE_NETSHARED : 'netshared'}

        fi = self.hdr.fiFromHeader()
        results = {} # fn = problem_obj?

        # Use prelink_undo_cmd macro?
        prelink_cmd = "/usr/sbin/prelink"
        have_prelink = os.path.exists(prelink_cmd)

        for filetuple in fi:
            #tuple is: (filename, fsize, mode, mtime, flags, frdev?, inode, link,
            #           state, vflags?, user, group, md5sum(or none for dirs) 
            (fn, size, mode, mtime, flags, dev, inode, link, state, vflags, 
                       user, group, csum) = filetuple
            if patterns:
                matched = False
                for p in patterns:
                    if fnmatch.fnmatch(fn, p):
                        matched = True
                        break
                if not matched: 
                    continue

            ftypes = []
            if flags & rpm.RPMFILE_CONFIG:
                ftypes.append('configuration')
            if flags & rpm.RPMFILE_DOC:
                ftypes.append('documentation')
            if flags & rpm.RPMFILE_GHOST:
                ftypes.append('ghost')
            if flags & rpm.RPMFILE_LICENSE:
                ftypes.append('license')
            if flags & rpm.RPMFILE_PUBKEY:
                ftypes.append('public key')
            if flags & rpm.RPMFILE_README:
                ftypes.append('README')
            if flags & rpm.RPMFILE_MISSINGOK:
                ftypes.append('missing ok')
            # not in python rpm bindings yet
            # elif flags & rpm.RPMFILE_POLICY:
            #    ftypes.append('policy')
                
            if all:
                vflags = -1

            if state != rpm.RPMFILE_STATE_NORMAL:
                if state in statemap:
                    ftypes.append("state=" + statemap[state])
                else:
                    ftypes.append("state=<unknown>")
                if fake_problems:
                    results[fn] = [_PkgVerifyProb('state',
                                                  'state is not normal',
                                                  ftypes, fake=True)]
                continue

            if flags & rpm.RPMFILE_MISSINGOK and fake_problems:
                results[fn] = [_PkgVerifyProb('missingok', 'missing but ok',
                                              ftypes, fake=True)]
            if flags & rpm.RPMFILE_MISSINGOK and not all:
                continue # rpm just skips missing ok, so we do too

            if flags & rpm.RPMFILE_GHOST and fake_problems:
                results[fn] = [_PkgVerifyProb('ghost', 'ghost file', ftypes,
                                              fake=True)]
            if flags & rpm.RPMFILE_GHOST and not all:
                continue

            # do check of file status on system
            problems = []
            if os.path.exists(fn):
                # stat
                my_st = os.lstat(fn)
                my_st_size = my_st.st_size
                my_user  = pwd.getpwuid(my_st[stat.ST_UID])[0]
                my_group = grp.getgrgid(my_st[stat.ST_GID])[0]

                if mode < 0:
                    # Stupid rpm, should be unsigned value but is signed ...
                    # so we "fix" it via. this hack
                    mode = (mode & 0xFFFF)

                ftype    = _ftype(mode)
                my_ftype = _ftype(my_st.st_mode)

                if vflags & _RPMVERIFY_RDEV and ftype != my_ftype:
                    prob = _PkgVerifyProb('type', 'file type does not match',
                                          ftypes)
                    prob.database_value = ftype
                    prob.disk_value = my_ftype
                    problems.append(prob)

                if (ftype == "symlink" and my_ftype == "symlink" and
                    vflags & _RPMVERIFY_LINKTO):
                    fnl    = fi.FLink() # fi.foo is magic, don't think about it
                    my_fnl = os.readlink(fn)
                    if my_fnl != fnl:
                        prob = _PkgVerifyProb('symlink',
                                              'symlink does not match', ftypes)
                        prob.database_value = fnl
                        prob.disk_value     = my_fnl
                        problems.append(prob)

                check_content = True
                if 'ghost' in ftypes:
                    check_content = False
                if my_ftype == "symlink" and ftype == "file":
                    # Don't let things hide behind symlinks
                    my_st_size = os.stat(fn).st_size
                elif my_ftype != "file":
                    check_content = False
                check_perms = True
                if my_ftype == "symlink":
                    check_perms = False

                if (check_content and vflags & _RPMVERIFY_MTIME and
                    my_st.st_mtime != mtime):
                    prob = _PkgVerifyProb('mtime', 'mtime does not match',
                                          ftypes)
                    prob.database_value = mtime
                    prob.disk_value     = my_st.st_mtime
                    problems.append(prob)

                if check_perms and vflags & _RPMVERIFY_USER and my_user != user:
                    prob = _PkgVerifyProb('user', 'user does not match', ftypes)
                    prob.database_value = user
                    prob.disk_value = my_user
                    problems.append(prob)
                if (check_perms and vflags & _RPMVERIFY_GROUP and
                    my_group != group):
                    prob = _PkgVerifyProb('group', 'group does not match',
                                          ftypes)
                    prob.database_value = group
                    prob.disk_value     = my_group
                    problems.append(prob)

                my_mode = my_st.st_mode
                if 'ghost' in ftypes: # This is what rpm does
                    my_mode &= 0777
                    mode    &= 0777
                if check_perms and vflags & _RPMVERIFY_MODE and my_mode != mode:
                    prob = _PkgVerifyProb('mode', 'mode does not match', ftypes)
                    prob.database_value = mode
                    prob.disk_value     = my_st.st_mode
                    problems.append(prob)

                # Note that because we might get the _size_ from prelink,
                # we need to do the checksum, even if we just throw it away,
                # just so we get the size correct.
                if (check_content and
                    ((have_prelink and vflags & _RPMVERIFY_FILESIZE) or
                     (csum and vflags & _RPMVERIFY_MD5))):
                    try:
                        my_csum = misc.checksum('md5', fn)
                        gen_csum = True
                    except Errors.MiscError:
                        # Don't have permission?
                        gen_csum = False

                    if csum and vflags & _RPMVERIFY_MD5 and not gen_csum:
                        prob = _PkgVerifyProb('genchecksum',
                                              'checksum not available', ftypes)
                        prob.database_value = csum
                        prob.disk_value     = None
                        problems.append(prob)
                        
                    if gen_csum and my_csum != csum and have_prelink:
                        #  This is how rpm -V works, try and if that fails try
                        # again with prelink.
                        (ig, fp,er) = os.popen3([prelink_cmd, "-y", fn])
                        # er.read(1024 * 1024) # Try and get most of the stderr
                        fp = _CountedReadFile(fp)
                        my_csum = misc.checksum('md5', fp)
                        my_st_size = fp.read_size

                    if (csum and vflags & _RPMVERIFY_MD5 and gen_csum and
                        my_csum != csum):
                        prob = _PkgVerifyProb('checksum',
                                              'checksum does not match', ftypes)
                        prob.database_value = csum
                        prob.disk_value     = my_csum
                        problems.append(prob)

                # Size might be got from prelink ... *sigh*.
                if (check_content and vflags & _RPMVERIFY_FILESIZE and
                    my_st_size != size):
                    prob = _PkgVerifyProb('size', 'size does not match', ftypes)
                    prob.database_value = size
                    prob.disk_value     = my_st.st_size
                    problems.append(prob)

            else:
                try:
                    os.stat(fn)
                    perms_ok = True # Shouldn't happen
                except OSError, e:
                    perms_ok = True
                    if e.errno == errno.EACCES:
                        perms_ok = False

                if perms_ok:
                    prob = _PkgVerifyProb('missing', 'file is missing', ftypes)
                else:
                    prob = _PkgVerifyProb('permissions-missing',
                                          'file is missing (Permission denied)',
                                          ftypes)
                problems.append(prob)

            if problems:
                results[fn] = problems
                
        return results
        
                             
class YumLocalPackage(YumHeaderPackage):
    """Class to handle an arbitrary package from a file path
       this inherits most things from YumInstalledPackage because
       installed packages and an arbitrary package on disk act very
       much alike. init takes a ts instance and a filename/path 
       to the package."""

    def __init__(self, ts=None, filename=None):
        if ts is None:
            raise Errors.MiscError, \
                 'No Transaction Set Instance for YumLocalPackage instance creation'
        if filename is None:
            raise Errors.MiscError, \
                 'No Filename specified for YumLocalPackage instance creation'
                 
        self.pkgtype = 'local'
        self.localpath = filename
        self._checksum = None

        
        try:
            hdr = rpmUtils.miscutils.hdrFromPackage(ts, self.localpath)
        except RpmUtilsError:
            raise Errors.MiscError, \
                'Could not open local rpm file: %s' % self.localpath
        
        fakerepo = FakeRepository(filename)
        fakerepo.cost = 0
        YumHeaderPackage.__init__(self, fakerepo, hdr)
        self.id = self.pkgid
        self._stat = os.stat(self.localpath)
        self.filetime = str(self._stat[-1])
        self.packagesize = str(self._stat[6])
        self.arch = self.isSrpm()
        self.pkgtup = (self.name, self.arch, self.epoch, self.ver, self.rel)
        
    def isSrpm(self):
        if self.tagByName('sourcepackage') == 1 or not self.tagByName('sourcerpm'):
            return 'src'
        else:
            return self.tagByName('arch')
        
    def localPkg(self):
        return self.localpath
    
    def _do_checksum(self, checksum_type='sha'):
        if not self._checksum:
            self._checksum = misc.checksum(checksum_type, self.localpath)
            
        return self._checksum    

    checksum = property(fget=lambda self: self._do_checksum())    
    

