#!/usr/bin/python -tt

import rpm
import os
import gzip
import sys
from i18n import _

def checkheader(headerfile, name, arch):
    #return true(1) if the header is good
    #return false(0) if the header is bad
    # test is fairly rudimentary - read in header - read two portions of the header
    h = Header_Work(headerfile)
    if h is None:
        return 0
    else:
        if name != h.name() or arch != h.arch():
            return 0
    return 1

def checkRpmMD5(package):
    """take a package, check it out by trying to open it, return 1 if its good
       return 0 if it's not"""
    ts.setVSFlags(~(rpm.RPMVSF_NOMD5|rpm.RPMVSF_NEEDPAYLOAD))
    fdno = os.open(package, os.O_RDONLY)
    try:
        ts.hdrFromFdno(fdno)
    except rpm.error, e:
        os.close(fdno)
        return 0
    os.close(fdno)
    return 1

def checkSig(package, serverid=None):
    """ take a package, check it's sigs, return 0 if they are all fine, return 
    1 if the gpg key can't be found, 3 if the key is not trusted, 2 if the 
    header is in someway damaged"""
    ts.setVSFlags(0)
    fdno = os.open(package, os.O_RDONLY)
    try:
        ts.hdrFromFdno(fdno)
    except rpm.error, e:
        if str(e) == "public key not availaiable":
            return 1
        if str(e) == "public key not available":
            return 1
        if str(e) == "public key not trusted":
            return 3
        if str(e) == "error reading package header":
            return 2
    os.close(fdno)
    return 0
    
def compareEVR((e1, v1, r1), (e2, v2, r2)):
    # return 1: a is newer than b 
    # 0: a and b are the same version 
    # -1: b is newer than a 
    rc = rpm.labelCompare((e1, v1, r1), (e2, v2, r2))
    log(6, '%s, %s, %s vs %s, %s, %s = %s' % (e1, v1, r1, e2, v2, r2, rc))
    return rc
    

def formatRequire (name, version, flags):
    if flags:
        if flags & (rpm.RPMSENSE_LESS | rpm.RPMSENSE_GREATER | rpm.RPMSENSE_EQUAL):
            name = name + ' '
        if flags & rpm.RPMSENSE_LESS:
            name = name + '<'
        if flags & rpm.RPMSENSE_GREATER:
            name = name + '>'
        if flags & rpm.RPMSENSE_EQUAL:
            name = name + '='
            name = name + ' %s' % version
    return string


def openrpmdb():
    try:
        db = rpm.TransactionSet('/')
    except rpm.error, e:
        errorlog(0, _("Could not open RPM database for reading. Perhaps it is already in use?"))
    return db



class RPM_Base_Work:

    def _getTag(self, tag):
        if self.hdr is None:
            errorlog(0, _('Got an empty Header, something has gone wrong'))
            sys.exit(1)
        return self.hdr[tag]
    
    def isSource(self):
        if self._getTag('sourcepackage') == 1:
            return 1
        else:
            return 0
        
    def name(self):
        return self._getTag('name')
        
    def arch(self):
        return self._getTag('arch')
        
    def epoch(self):
        return self._getTag('epoch')
    
    def version(self):
        return self._getTag('version')
        
    def release(self):
        return self_getTag('release')
        
    def evr(self):
        e = self._getTag('epoch')
        v = self._getTag('version')
        r = self._getTag('release')
        return (e, v, r)
        
    def nevra(self):
        n = self._getTag('name')
        e = self._getTag('epoch')
        v = self._getTag('version')
        r = self._getTag('release')
        a = self._getTag('arch')
        return (n, e, v, r, a)
    
    def writeHeader(self, headerdir, compress):
    # write the header out to a file with the format: name-epoch-ver-rel.arch.hdr
    # return the name of the file it just made - no real reason :)
        (name, epoch, ver, rel, arch) = self.nevra()
        if epoch is None:
            epoch = '0'
        headerfn = "%s/%s-%s-%s-%s.%s.hdr" % (headerdir, name, epoch, ver, rel, arch)
        if compress:
            headerout = gzip.open(headerfn, "w")
        else:
            headerout = open(headerfn, "w")
        headerout.write(self.hdr.unload(1))
        headerout.close()
        return(headerfn)

class Header_Work(RPM_Base_Work):

    def __init__(self, hdrfn):
        try:
            fd = gzip.open(hdrfn, 'r')
            try: 
                h = rpm.headerLoad(fd.read())
            except rpm.error, e:
                errorlog(0,_('Damaged Header %s') % rpmfn)
                h = None
        except IOError,e:
            fd = open(hdrfn, 'r')
            try:
                self.hdr = rpm.headerLoad(fd.read())
            except rpm.error, e:
                errorlog(0,_('Damaged Header %s') % hdrfn)
                h = None
        except ValueError, e:
            h = None
        fd.close()
        self.hdr = h


class RPM_Work(RPM_Base_Work):
    def __init__(self, rpmfn):
        ts.setVSFlags(~(rpm._RPMVSF_NOSIGNATURES))
        fd = os.open(rpmfn, os.O_RDONLY)
        try:
            self.hdr = ts.hdrFromFdno(fd)
        except rpm.error, e:
            errorlog(0, _('Error opening rpm %s - error %s') % (rpmfn, e))
            self.hdr = None
        os.close(fd)
    

class RPM_DB_Work:
    """ This should operate on groups of headers/matches/etc in the rpmdb - ideally it will 
    operate with a list of the Base objects above, so I can refer to any one object there
    not sure the best way to do this yet, more thinking involved"""
    def __init__(self, ts):
        self.ts = ts
        
    # pass in ts to use - have a match function to abstract the concept
    # this should really just be used for matches and grabbing info from the rpmdb
    # put exclusions for gpg keys here, etc.
    
    def match(tag, search):
        """hands back a list of Header_Work objects"""
        _hwlist = []
        mi = self.ts.dbMatch(tag, search)
        
        
