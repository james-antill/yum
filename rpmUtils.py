#!/usr/bin/python -tt

import rpm
import types
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
    ts.sigChecking('md5')
    fdno = os.open(package, os.O_RDONLY)
    try:
        ts.hdrFromFdno(fdno)
    except rpm.error, e:
        os.close(fdno)
        ts.sigChecking('default')
        return 0
    os.close(fdno)
    ts.sigChecking('default')
    return 1

def checkSig(package, serverid=None):
    """ take a package, check it's sigs, return 0 if they are all fine, return 
    1 if the gpg key can't be found, 3 if the key is not trusted, 2 if the 
    header is in someway damaged"""
    ts.sigChecking('default')
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
    def rpmOutToStr(arg):
        if type(arg) != types.StringType and arg != None:
            arg = str(arg)
        return arg
    e1 = rpmOutToStr(e1)
    v1 = rpmOutToStr(v1)
    r1 = rpmOutToStr(r1)
    e2 = rpmOutToStr(e2)
    v2 = rpmOutToStr(v2)
    r2 = rpmOutToStr(r2)
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
    return name


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
            #FIXME should raise a yum error here
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
    """for operating on hdrs in and out of the rpmdb
       if the first arg is a string then it's a filename
       otherwise it's an rpm hdr"""
    def __init__(self, header):
        if header is types.StringType:
            try:
                fd = gzip.open(header, 'r')
                try: 
                    h = rpm.headerLoad(fd.read())
                except rpm.error, e:
                    errorlog(0,_('Damaged Header %s') % header)
                    h = None
            except IOError,e:
                fd = open(header, 'r')
                try:
                    self.hdr = rpm.headerLoad(fd.read())
                except rpm.error, e:
                    errorlog(0,_('Damaged Header %s') % header)
                    h = None
            except ValueError, e:
                h = None
            fd.close()
        else:
            h = header
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
    
class Rpm_Ts_Work:
    """This should operate on groups of headers/matches/etc in the rpmdb - ideally it will 
    operate with a list of the Base objects above, so I can refer to any one object there
    not sure the best way to do this yet, more thinking involved"""
    def __init__(self, dbPath='/'):
        self.ts = rpm.TransactionSet(dbPath)
        
        self.methods = ['addInstall', 'addErase', 'run', 'check', 'order', 'hdrFromFdno',
                   'closeDB', 'dbMatch', 'setFlags', 'setVSFlags', 'setProbFilter']
                   
    def __getattr__(self, attribute):
        if attribute in self.methods:
            return getattr(self.ts, attribute)
        else:
            raise AttributeError, attribute
            
    def match(self, tag, search):
        """hands back a list of Header_Work objects"""
        hwlist = []
        hdrlist = self.ts.dbMatch(tag, search)
        for hdr in hdrlist:
            hdrobj = Header_Work(hdr)
            _hwlist.appened(hdrobj)
        return hwlist
    
    
    def sigChecking(self, sig):
        """pass type of check you want to occur, default is to have them off"""
        if sig == 'md5':
            #turn off everything but md5 - and we need to the check the payload
            self.ts.setVSFlags(~(rpm.RPMVSF_NOMD5|rpm.RPMVSF_NEEDPAYLOAD))
        elif sig == 'none':
            # turn off everything - period
            self.ts.setVSFlags(~(rpm._RPMVSF_NOSIGNATURES))
        elif sig == 'default':
            # set it back to the default
            self.ts.setVSFlags(rpm.RPMVSF_DEFAULT)
        else:
            raise AttributeError, sig
