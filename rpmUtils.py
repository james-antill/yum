#!/usr/bin/python -tt

import rpm
import os
import gzip
import sys

def checkheader(headerfile, name, arch):
    #return true(1) if the header is good
    #return false(0) if the header is bad
    # test is fairly rudimentary - read in header - read two portions of the header
    h = Header_Work(headerfile)
    if h == None:
        return 0
    else:
        if name != h.name() or arch != h.arch():
            return 0
    return 1

def checkRpmMD5(package):
    ts.setVSFlags(~(rpm.RPMVSF_NOMD5|rpm.RPMVSF_NEEDPAYLOAD))
    fdno = os.open(package, os.O_RDONLY)
    try:
        h = ts.hdrFromFdno(fdno)
    except rpm.error, e:
        os.close(fdno)
        del h
        return 0
    os.close(fdno)
    del h
    return 1

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
        raise RpmError(_("Could not open RPM database for reading. Perhaps it is already in use?"))
    return db

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
        fd = os.open(rpmfn, os.O_RDONLY)
        try:
            self.hdr = ts.hdrFromFdno(fd)
        except RpmError, e:
            errorlog(0, 'Error opening rpm %s - error %s' % (rpmfn, e))
            sys.exit(1)
        os.close(fd)
    

class RPM_Base_Work:

    def _getTag(self, tag):
        return self.hdr[tag]
    
    def name(self):
        return self._getTag('name')
        
    def arch(self):
        return self._getTag('arch')
        
    def evr(self)
        e = self._getTag('epoch')
        v = self._getTag('version')
        r = self._getTag('release')
        return (e, v, r)
        
    def nevra(self)
        n = self._getTag('name')
        e = self._getTag('epoch')
        v = self._getTag('version')
        r = self._getTag('release')
        a = self._getTag('arch')
        return (n, e, v, r, a)
        
