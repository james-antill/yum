#!/usr/bin/python
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
# Copyright 2002 Duke University 

import string
import rpm
import os
import os.path
import sys
import gzip
import archwork
import fnmatch
import pkgaction
import callback
import rpmUtils
import time

from urlgrabber import close_all, urlgrab, URLGrabError, retrygrab

from i18n import _

def stripENVRA(str):
    archIndex = string.rfind(str, '.')
    arch = str[archIndex+1:]
    relIndex = string.rfind(str[:archIndex], '-')
    rel = str[relIndex+1:archIndex]
    verIndex = string.rfind(str[:relIndex], '-')
    ver = str[verIndex+1:relIndex]
    epochIndex = string.find(str, ':')
    epoch = str[:epochIndex]
    name = str[epochIndex + 1:verIndex]
    return (epoch, name, ver, rel, arch)

def stripEVR(str):
    epochIndex = string.find(str, ':')
    epoch = str[:epochIndex]
    relIndex = string.rfind(str, '-')
    rel = str[relIndex+1:]
    verIndex = string.rfind(str[:relIndex], '-')
    ver = str[epochIndex+1:relIndex]  
    return (epoch, ver, rel)

def stripNA(str):
    archIndex = string.rfind(str, '.')
    arch = str[archIndex+1:]
    name = str[:archIndex]
    return (name, arch)

def getENVRA(header):
    if header[rpm.RPMTAG_EPOCH] == None:
        epoch = '0'
    else:
        epoch = '%s' % header[rpm.RPMTAG_EPOCH]
    name = header[rpm.RPMTAG_NAME]
    ver = header[rpm.RPMTAG_VERSION]
    rel = header[rpm.RPMTAG_RELEASE]
    arch = header[rpm.RPMTAG_ARCH]
    return (epoch, name, ver, rel, arch)

def str_to_version(str):
    i = string.find(str, ':')
    if i != -1:
        epoch = string.atol(str[:i])
    else:
        epoch = '0'
    j = string.find(str, '-')
    if j != -1:
        if str[i + 1:j] == '':
            version = None
        else:
            version = str[i + 1:j]
        release = str[j + 1:]
    else:
        if str[i + 1:] == '':
            version = None
        else:
            version = str[i + 1:]
        release = None
    return (epoch, version, release)

def HeaderInfoNevralLoad(filename, nevral, serverid):
    in_file = open(filename, 'r')
    info = in_file.readlines()
    in_file.close()
    archlist = archwork.compatArchList()
    for line in info:
        (envraStr, rpmpath) = string.split(line, '=')
        (epoch, name, ver, rel, arch) = stripENVRA(envraStr)
        rpmpath = string.replace(rpmpath, '\n', '')
        if arch in archlist:
            if not nameInExcludes(name):
                if conf.pkgpolicy == 'last':
                    nevral.add((name, epoch, ver, rel, arch, rpmpath, serverid), 'a')
                else:
                    if nevral.exists(name, arch):
                        (e1, v1, r1) = nevral.evr(name, arch)
                        (e2, v2, r2) = (epoch, ver, rel)    
                        rc = rpmUtils.compareEVR((e1, v1, r1), (e2, v2, r2))
                        if (rc < 0):
                            # ooo  the second one is newer - push it in.
                            nevral.add((name, epoch, ver, rel, arch, rpmpath, serverid), 'a')
                    else:
                        nevral.add((name, epoch, ver, rel, arch, rpmpath, serverid), 'a')


def nameInExcludes(name):
    # this function should take a name and check it against the excludes
    # list to see if it shouldn't be in there
    # return true if it is in the Excludes list
    # return false if it is not in the Excludes list
    for exclude in conf.excludes:
        if name == exclude or fnmatch.fnmatch(name, exclude):
            return 1
    return 0

def rpmdbNevralLoad(nevral):
    rpmdbdict = {}
    serverid = 'db'
    rpmloc = 'in_rpm_db'
    hdrs = ts.dbMatch()
    for hdr in hdrs:
        (epoch, name, ver, rel, arch) = getENVRA(hdr)
        # deal with multiple versioned dupes and dupe entries in localdb
        if not rpmdbdict.has_key((name, arch)):
            rpmdbdict[(name, arch)] = (epoch, ver, rel)
        else:
            (e1, v1, r1) = (rpmdbdict[(name, arch)])
            (e2, v2, r2) = (epoch, ver, rel)    
            rc = rpmUtils.compareEVR((e1,v1,r1), (e2,v2,r2))
            if (rc <= -1):
                rpmdbdict[(name, arch)] = (epoch, ver, rel)
            elif (rc == 0):
                log(4, 'dupe entry in rpmdb %s %s' % (name, arch))
    for value in rpmdbdict.keys():
        (name, arch) = value
        (epoch, ver, rel) = rpmdbdict[value]
        nevral.add((name, epoch, ver, rel, arch, rpmloc, serverid), 'n')

def readHeader(rpmfn):
    if string.lower(rpmfn[-4:]) == '.rpm':
        fd = os.open(rpmfn, os.O_RDONLY)
        h = ts.hdrFromFdno(fd)
        os.close(fd)
        if h[rpm.RPMTAG_SOURCEPACKAGE]:
            return 'source'
        else:
            return h
    else:
        try:
            fd = gzip.open(rpmfn, 'r')
            try: 
                h = rpm.headerLoad(fd.read())
            except rpm.error, e:
                errorlog(0,_('Damaged Header %s') % rpmfn)
                return None
        except IOError,e:
            fd = open(rpmfn, 'r')
            try:
                h = rpm.headerLoad(fd.read())
            except rpm.error, e:
                errorlog(0,_('Damaged Header %s') % rpmfn)
                return None
        except ValueError, e:
            return None
    fd.close()
    return h

def returnObsoletes(headerNevral, rpmNevral, uninstNAlist):
    obsoleting = {} # obsoleting[pkgobsoleting]=[list of pkgs it obsoletes]
    obsoleted = {} # obsoleted[pkgobsoleted]=[list of pkgs obsoleting it]
    for (name, arch) in uninstNAlist:
        # DEBUG print '%s, %s' % (name, arch)
        header = headerNevral.getHeader(name, arch)
        obs = header[rpm.RPMTAG_OBSOLETES]
        del header

        # if there is one its a nonversioned obsolete
        # if there are 3 its a versioned obsolete
        # nonversioned are obvious - check the rpmdb if it exists
        # then the pkg obsoletes something in the rpmdb
        # versioned obsolete - obvalue[0] is the name of the pkg
        #                      obvalue[1] is >,>=,<,<=,=
        #                      obvalue[2] is an e:v-r string
        # get the two version strings - labelcompare them
        # get the return value - then run through
        # an if/elif statement regarding obvalue[1] and determine
        # if the pkg obsoletes something in the rpmdb
        if obs:
            for ob in obs:
                obvalue = string.split(ob)
                obspkg = obvalue[0]
                if rpmNevral.exists(obspkg):
                    if len(obvalue) == 1: #unversioned obsolete
                        if not obsoleting.has_key((name, arch)):
                            obsoleting[(name, arch)] = []
                        obsoleting[(name, arch)].append(obspkg)
                        if not obsoleted.has_key(obspkg):
                            obsoleted[obspkg] = []
                        obsoleted[obspkg].append((name, arch))
                        log(4, '%s obsoleting %s' % (name, obspkg))
                    elif len(obvalue) == 3:
                        obscomp = obvalue[1]
                        obsver = obsvalue[2]
                        (e1, v1, r1) = rpmNevral.evr(name, arch)
                        (e2, v2, r2) = str_to_version(obsver)
                        rc = rpmUtils.compareEVR((e1, v1, r1), (e2, v2, r2))
                        if obscomp == '>':
                            if rc >= 1:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                        elif obscomp == '>=':
                            if rc >= 1:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                            elif rc == 0:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                        elif obscomp == '=':
                            if rc == 0:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                        elif obscomp == '<=':
                            if rc == 0:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                            elif rc <= -1:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                        elif obscomp == '<':
                            if rc <= -1:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
    return obsoleting, obsoleted

def getupdatedhdrlist(headernevral, rpmnevral):
    "returns (name, arch) tuples of updated and uninstalled pkgs"
    uplist = []
    newlist = []
    simpleupdate = []
    complexupdate = []
    uplist_archdict = {}
        # this is all hard and goofy to deal with pkgs changing arch
        # if we have the package installed
        # if the pkg isn't installed then it's a new pkg
        # else
        #   if there isn't more than on available arch from the hinevral 
        #       then compare it to the installed one 
        #   if there is more than one installed or available:
        #   compare highest version and bestarch (in that order of precdence) 
        #   of installed pkgs to highest version and bestarch of available pkgs
        
        # best bet is to chew through the pkgs and throw out the new ones early
        # then deal with the ones where there are a single pkg installed and a 
        # single pkg available
        # then deal with the multiples
        # write a sub-function that takes (nevral, name) and returns list of
        # archs that have the highest version
        
        # additional tricksiness - include a config option to make it ONLY 
        # update identical matches so glibc.i386 can only be updated by 
        # glibc.i386 not by glibc.i686 - this is for the anal and bizare
        
    for (name, arch) in headernevral.NAkeys():
        if not rpmnevral.exists(name):
            newlist.append((name, arch))
        else:
            hdrarchs = archwork.availablearchs(headernevral, name)
            rpmarchs = archwork.availablearchs(rpmnevral, name)
            if len(hdrarchs) > 1 or len(rpmarchs) > 1:
                if name not in complexupdate:
                    log(4, 'putting %s in complex update list' % name)
                    complexupdate.append(name)
            else:
                log(4, 'putting %s in simple update list' % name)
                simpleupdate.append((name, arch))
    # we have our lists to work with now

    # simple cases
    for (name, arch) in simpleupdate:
        # try to be as precise as possible
        if conf.exactarch:
            # make the default case false
            exactmatch = 0
        else:
            # make the default case true
            exactmatch = 1
        
        if rpmnevral.exists(name, arch):
            exactmatch = 1
            (rpm_e, rpm_v, rpm_r) = rpmnevral.evr(name, arch)
        else:
            (rpm_e, rpm_v, rpm_r) = rpmnevral.evr(name)
            
        if exactmatch:
            rc = rpmUtils.compareEVR(headernevral.evr(name), (rpm_e, rpm_v, rpm_r))
            if rc > 0:
                uplist.append((name, arch))
        
    # complex cases
    for name in complexupdate:
        hdrarchs = bestversion(headernevral, name)
        rpmarchs = bestversion(rpmnevral, name)
        hdr_best_arch = archwork.bestarch(hdrarchs)
        log(5, 'Best ver+arch avail for %s is %s' % (name, hdr_best_arch))
        rpm_best_arch = archwork.bestarch(rpmarchs)
        log(5, 'Best ver+arch installed for %s is %s' % (name, rpm_best_arch))
        
        # dealing with requests to only update exactly what is installed
        # we clearly want to update the stuff that is installed
        # and compare it to best that is available - but only if the arch 
        # matches so we go through the lists of rpmarchs of bestversion, check 
        # for them in hdrarchs - if they are there compare them and mark them
        # accordingly
        # if for some reason someone has two pkgs of the same version but 
        # different arch installed and we happen to have both available then
        # they'll get both - if anyone can point out a situation when this is 
        # "legal" let me know.

        if conf.exactarch:
            for arch in rpmarchs:
                if arch in hdrarchs:
                    log(5, 'Exact match in complex for %s - %s' % (name, arch))
                    rc = rpmUtils.compareEVR(headernevral.evr(name, arch), rpmnevral.evr(name, arch))
                    if rc > 0:
                        uplist.append((name, arch))
                else:
                    log(5, 'Inexact match in complex for %s - %s' % (name, arch))
        else:
            rc = rpmUtils.compareEVR(headernevral.evr(name, hdr_best_arch), rpmnevral.evr(name, rpm_best_arch))
            if rc > 0:
                uplist.append((name, hdr_best_arch))

    nulist=uplist+newlist
    return (uplist, newlist, nulist)


def bestversion(nevral, name):
    """this takes a nevral and a pkg name - it iterates through them to return
       the list of archs having the highest version number - so if someone has
       package foo.i386 and foo.i686 then we'll get a list of i386 and i686 returned
       minimum of one thing returned"""
    # first we get a list of the archs
    # determine the best e-v-r
    # then we determine the archs that have that version and append them to a list
    returnarchs = []
    
    archs = archwork.availablearchs(nevral, name)
    currentarch = archs[0]
    for arch in archs[1:]:
        rc = rpmUtils.compareEVR(nevral.evr(name, currentarch), nevral.evr(name, arch))
        if rc < 0:
            currentarch = arch
        elif rc == 0:
            pass
        elif rc > 0:
            pass
    (best_e, best_v, best_r) = nevral.evr(name, currentarch)
    log(3, 'Best version for %s is %s:%s-%s' % (name, best_e, best_v, best_r))
    
    for arch in archs:
        rc = rpmUtils.compareEVR(nevral.evr(name, arch), (best_e, best_v, best_r))
        if rc == 0:
            returnarchs.append(arch)
        elif rc > 0:
            log(4, 'What the hell, we just determined it was the bestversion')
    
    log(7, returnarchs)
    return returnarchs
    
def formatRequire (name, version, flags):
    string = name
        
    if flags:
        if flags & (rpm.RPMSENSE_LESS | rpm.RPMSENSE_GREATER | rpm.RPMSENSE_EQUAL):
            string = string + ' '
        if flags & rpm.RPMSENSE_LESS:
            string = string + '<'
        if flags & rpm.RPMSENSE_GREATER:
            string = string + '>'
        if flags & rpm.RPMSENSE_EQUAL:
            string = string + '='
            string = string + ' %s' % version
    return string


def actionslists(nevral):
    install_list = []
    update_list = []
    erase_list = []
    updatedeps_list = []
    erasedeps_list = []
    for (name, arch) in nevral.NAkeys():
        if nevral.state(name, arch) in ('i', 'iu'):
            install_list.append((name, arch))
        if nevral.state(name, arch) == 'u':
            update_list.append((name, arch))
        if nevral.state(name, arch) == 'e':
            erase_list.append((name, arch))
        if nevral.state(name, arch) == 'ud':
            updatedeps_list.append((name, arch))
        if nevral.state(name, arch) == 'ed':
            erasedeps_list.append((name, arch))
    
    return install_list, update_list, erase_list, updatedeps_list, erasedeps_list
    
def printactions(i_list, u_list, e_list, ud_list, ed_list):
    log(2, _('I will do the following:'))
    for pkg in i_list:
        (name,arch) = pkg
        log(2, _('[install: %s.%s]') % (name, arch))
    for pkg in u_list:
        (name,arch) = pkg
        log(2, _('[update: %s.%s]') % (name, arch))
    for pkg in e_list:
        (name,arch) = pkg
        log(2, _('[erase: %s.%s]') % (name, arch))
    if len(ud_list) > 0:
        log(2, _('I will install/upgrade these to satisfy the depedencies:'))
        for pkg in ud_list:
            (name, arch) = pkg
            log(2, _('[deps: %s.%s]') %(name, arch))
    if len(ed_list) > 0:
        log(2, 'I will erase these to satisfy the depedencies:')
        for pkg in ed_list:
            (name, arch) = pkg
            log(2, '[deps: %s.%s]' %(name, arch))

def filelogactions(i_list, u_list, e_list, ud_list, ed_list):
    i_log = 'Installed: '
    u_log = 'Updated: '
    e_log = 'Erased: '
        
    for (name, arch) in i_list:
        filelog(1, i_log + name + '.' + arch)
    for (name, arch) in u_list+ud_list:
        filelog(1, u_log + name + '.' + arch)
    for (name, arch) in e_list+ed_list:
        filelog(1, e_log + name + '.' + arch)
        

def shortlogactions(i_list, u_list, e_list, ud_list, ed_list):
    i_log = 'Installed: '
    u_log = 'Updated: '
    e_log = 'Erased: '
    
    for (name, arch) in i_list:
        i_log=i_log + ' ' + name + '.' + arch
    for (name, arch) in u_list+ud_list:
        u_log=u_log + ' ' + name + '.' + arch
    for (name, arch) in e_list+ed_list:
        e_log=e_log + ' ' + name + '.' + arch
    if len(i_list) > 0:
        log(1, i_log)
    if len(u_list+ud_list) > 0:
        log(1, u_log)
    if len(e_list+ed_list) > 0:
        log(1, e_log)
        


def userconfirm():
    """gets a yes or no from the user, defaults to No"""
    choice = raw_input('Is this ok [y/N]: ')
    if len(choice) == 0:
        return 1
    else:
        if choice[0] != 'y' and choice[0] != 'Y':
            return 1
        else:
            return 0
        


def nasort((n1, a1), (n2, a2)):
    if n1 > n2:
        return 1
    elif n1 == n2:
        return 0
    else:
        return -1
        
def getfilelist(path, ext, list):
    # get all the files matching the 3 letter extension that is ext in path, 
    # recursively
    # append them to list
    # return list
    # ignore symlinks
    dir_list = os.listdir(path)
    for d in dir_list:
        if os.path.isdir(path + '/' + d):
            list = getfilelist(path + '/' + d, ext, list)
        else:
            if string.lower(d[-4:]) == '%s' % (ext):
                if not os.path.islink( path + '/' + d): 
                    newpath = os.path.normpath(path + '/' + d)
                    list.append(newpath)
    return(list)

def clean_up_headers():
    serverlist = conf.servers
    for serverid in serverlist:
        hdrdir = conf.serverhdrdir[serverid]
        hdrlist = getfilelist(hdrdir, '.hdr', [])
        # remove header.info file too
        headerinfofile = os.path.join(conf.cachedir, serverid, 'header.info')
        log(4, 'Deleting header.info for %s' % serverid)
        os.unlink(headerinfofile)
        for hdr in hdrlist:
            log(4, 'Deleting Header %s' % hdr)
            os.unlink(hdr)
            


def clean_up_packages():
    serverlist = conf.servers
    for serverid in serverlist:
        rpmdir = conf.serverpkgdir[serverid]
        rpmlist = getfilelist(rpmdir, '.rpm', [])
        for rpm in rpmlist:
            log(4, 'Deleting Package %s' % rpm)
            os.unlink(rpm)
    

def clean_up_old_headers(rpmDBInfo, HeaderInfo):
    serverlist = conf.servers
    hdrlist = []
    for serverid in serverlist:
        hdrdir = conf.serverhdrdir[serverid]
        hdrlist = getfilelist(hdrdir, '.hdr', hdrlist)
    for hdrfn in hdrlist:
        hdr = readHeader(hdrfn)
        (e, n, v, r, a) = getENVRA(hdr)
        if rpmDBInfo.exists(n, a):
            (e1, v1, r1) = rpmDBInfo.evr(n, a)
            rc = rpmUtils.compareEVR((e1, v1, r1), (e, v, r))
            # if the rpmdb has an equal or better rpm then delete
            # the header
            if (rc >= 0):
                log(5, 'Deleting Header %s' % hdrfn)
                os.unlink(hdrfn)
        if not HeaderInfo.exists(n, a):
            # if its not in the HeaderInfo nevral anymore just kill it
            log(5, 'Deleting Header %s' % hdrfn)
            os.unlink(hdrfn)
            

def printtime():
    return time.strftime('%m/%d/%y %H:%M:%S ', time.localtime(time.time()))

def get_groups_from_servers(serveridlist):
    """takes a list of serverids - returns a list of servers that either:
       gave us yumcomps.xml or for whom we had a cached one"""
       
    log(2, 'Getting groups from servers')
    validservers = []
    for serverid in serveridlist:
        remotegroupfile = conf.remoteGroups(serverid)
        localgroupfile = conf.localGroups(serverid)
        if not conf.cache:
            log(3, 'getting groups from server: %s' % serverid)
            try:
                localgroupfile = retrygrab(remotegroupfile, localgroupfile, copy_local=1)
            except URLGrabError, e:
                log(3, 'Error getting file %s' % remotegroupfile)
                log(3, '%s' % e)
        else:
            if os.path.exists(localgroupfile):
                log(2, 'using cached groups from server: %s' % serverid)
        if os.path.exists(localgroupfile):
            log(3, 'Got a file - yay')
            validservers.append(serverid)
    return validservers
        
def get_package_info_from_servers(serveridlist, HeaderInfo):
    # this should hand back a list of servers that we could contact
    # that HeaderInfoNevralLoad then deals with - it would speed up the 
    # operation, even
    log(2, 'Gathering package information from servers')
    for serverid in serveridlist:
        servername = conf.servername[serverid]
        serverheader = conf.remoteHeader(serverid)
        servercache = conf.servercache[serverid]
        log(2, 'Getting headers from: %s' % (servername))
        log(4, 'Putting them into: %s' % (servercache))
        localpkgs = conf.serverpkgdir[serverid]
        localhdrs = conf.serverhdrdir[serverid]
        localheaderinfo = conf.localHeader(serverid)
        if not os.path.exists(servercache):
            os.mkdir(servercache)
        if not os.path.exists(localpkgs):
            os.mkdir(localpkgs)
        if not os.path.exists(localhdrs):
            os.mkdir(localhdrs)
        if not conf.cache:
            log(3, 'getting header.info from server')
            try:
                # FIXME this should do a test here too of the headerinfo file
                # if it is empty then just ignore the repo and move along
                headerinfofn = retrygrab(serverheader, localheaderinfo, copy_local=1)
            except URLGrabError, e:
                errorlog(0, 'Error getting file %s' % serverheader)
                errorlog(0, '%s' % e)
                sys.exit(1)
        else:
            log(3, 'using cached header.info file')
            headerinfofn = localheaderinfo
        log(4,'headerinfofn: ' + headerinfofn)
        HeaderInfoNevralLoad(headerinfofn, HeaderInfo, serverid)

def checkheader(headerfile, name, arch):
    #return true(1) if the header is good
    #return  false(0) if the header is bad
    # test is fairly rudimentary - read in header - read two portions of the header
    h = readHeader(headerfile)
    if h == None:
        return 0
    else:
        if name != h[rpm.RPMTAG_NAME] or arch != h[rpm.RPMTAG_ARCH]:
            return 0
    return 1

def download_headers(HeaderInfo, nulist):
    for (name, arch) in nulist:
        # if header exists - it gets checked
        # if header does not exist it gets downloaded then checked
        # this should happen in a loop - up to 3 times
        # if we can't get a good header after 3 tries we bail.
        checkpass = 1
        LocalHeaderFile = HeaderInfo.localHdrPath(name, arch)
        RemoteHeaderFile = HeaderInfo.remoteHdrUrl(name, arch)
        while checkpass <= 3:
            if os.path.exists(LocalHeaderFile):
                log(5, 'cached %s' % LocalHeaderFile)
            else:
                log(2, 'getting %s' % LocalHeaderFile)
                try:
                    hdrfn = retrygrab(RemoteHeaderFile, LocalHeaderFile, copy_local=1)
                except URLGrabError, e:
                    errorlog(0, 'Error getting file %s' % RemoteHeaderFile)
                    errorlog(0, '%s' % e)
                    sys.exit(1)
                HeaderInfo.setlocalhdrpath(name, arch, hdrfn)
            if checkheader(LocalHeaderFile, name, arch):
                    break
            else:
                log(3, 'damaged header %s try - %d' % (LocalHeaderFile, checkpass))
                checkpass = checkpass + 1
                os.unlink(LocalHeaderFile)
                good = 0
    close_all()
                
def take_action(cmds, nulist, uplist, newlist, obsoleting, tsInfo, HeaderInfo, rpmDBInfo, obsoleted):
    from yummain import usage
    
    basecmd = cmds.pop(0)
    
    if conf.uid != 0:
        if basecmd in ['install','update','clean','upgrade','erase', 'groupupdate', 'groupupgrade', 'groupinstall']:
            errorlog(0, _('You need to be root to perform these commands'))
            sys.exit(1)
    
    if basecmd == 'install':
        if len(cmds) == 0:
            errorlog(0, _('Need to pass a list of pkgs to install'))
            usage()
        else:
            pkgaction.installpkgs(tsInfo, nulist, cmds, HeaderInfo, rpmDBInfo, 1)
    
    elif basecmd == 'provides':
        if len(cmds) == 0:
            errorlog(0, _('Need a provides to match'))
            usage()
        else:
            log(2, _('Looking in available packages for a providing package'))
            pkgaction.whatprovides(cmds, nulist, HeaderInfo,0)
            log(2, _('Looking in installed packages for a providing package'))
            pkgaction.whatprovides(cmds, nulist, rpmDBInfo,1)
        sys.exit(0)
    
    elif basecmd == 'update':
        if len(cmds) == 0:
            pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, 'all', 1)
        else:
            pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, cmds, 1)
            
    elif basecmd == 'upgrade':
        if len(cmds) == 0:
            pkgaction.upgradepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, obsoleted, obsoleting, 'all')
        else:
            pkgaction.upgradepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, obsoleted, obsoleting, cmds)
    
    elif basecmd in ('erase', 'remove'):
        if len(cmds) == 0:
            errorlog (0, _('Need to pass a list of pkgs to erase'))
            usage()
        else:
            pkgaction.erasepkgs(tsInfo, rpmDBInfo, cmds)
    
    elif basecmd == 'check-update':
        if len(uplist) > 0:
            pkgaction.listpkginfo(uplist, 'all', HeaderInfo, 1)
            sys.exit(100)
        else:
            sys.exit(0)
            
    elif basecmd in ['list', 'info']:
        if basecmd == 'list':
            short = 1
        else:
            short = 0
        if len(cmds) == 0:
            pkgaction.listpkginfo(nulist, 'all', HeaderInfo, short)
            sys.exit(0)
        else:
            if cmds[0] == 'updates':
                pkgaction.listpkginfo(uplist, 'updates', HeaderInfo, short)
            elif cmds[0] == 'available':
                pkgaction.listpkginfo(newlist, 'all', HeaderInfo, short)
            elif cmds[0] == 'installed':
                pkglist = rpmDBInfo.NAkeys()
                pkgaction.listpkginfo(pkglist, 'all', rpmDBInfo, short)
            elif cmds[0] == 'extras':
                pkglist=[]
                for (name, arch) in rpmDBInfo.NAkeys():
                    if not HeaderInfo.exists(name, arch):
                        pkglist.append((name,arch))
                if len(pkglist) > 0:
                    pkgaction.listpkginfo(pkglist, 'all', rpmDBInfo, short)
                else:
                    log(2, _('No Packages installed not included in a repository'))
            else:    
                log(2, _('Looking in Available Packages:'))
                pkgaction.listpkginfo(nulist, cmds, HeaderInfo, short)
                log(2, _('Looking in Installed Packages:'))
                pkglist = rpmDBInfo.NAkeys()
                pkgaction.listpkginfo(pkglist, cmds, rpmDBInfo, short)
        sys.exit(0)
    elif basecmd == 'grouplist':
        pkgaction.listgroups(cmds)
        sys.exit(0)
    
    elif basecmd == 'groupupdate':
        if len(cmds) == 0:
            errorlog(0, _('Need a list of groups to update'))
            sys.exit(1)
        installs, updates = pkgaction.updategroups(rpmDBInfo, nulist, uplist, cmds)
        if len(updates) > 0:
            pkglist = []
            for (group, pkg) in updates:
                pkglist.append(pkg)
            pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, pkglist, 0)
        if len(installs) > 0:
            pkglist = []
            for (group, pkg) in installs:
                pkglist.append(pkg)
            pkgaction.installpkgs(tsInfo, nulist, pkglist, HeaderInfo, rpmDBInfo, 0)
            
    elif basecmd == 'groupinstall':
        if len(cmds) == 0:
            errorlog(0, _('Need a list of groups to update'))
            sys.exit(1)
        instpkglist = pkgaction.installgroups(rpmDBInfo, nulist, uplist, cmds)
        if len(instpkglist) > 0:
            pkgaction.installpkgs(tsInfo, nulist, instpkglist, HeaderInfo, rpmDBInfo, 0)
        
            
    elif basecmd == 'clean':
        if len(cmds) == 0 or cmds[0] == 'all':
            log(2, _('Cleaning packages and old headers'))
            clean_up_packages()
            clean_up_old_headers(rpmDBInfo, HeaderInfo)
        elif cmds[0] == 'packages':
            log(2, _('Cleaning packages'))
            clean_up_packages()
        elif cmds[0] == 'headers':
            log(2, _('Cleaning all headers'))
            clean_up_headers()
        elif cmds[0] == 'oldheaders':
            log(2, _('Cleaning old headers'))
            clean_up_old_headers(rpmDBInfo, HeaderInfo)
        else:
            errorlog(0, _('Invalid clean option %s') % cmds[0])
            sys.exit(1)
        sys.exit(0)    
    else:
        usage()

def create_final_ts(tsInfo):
    # download the pkgs to the local paths and add them to final transaction set
    tsfin = rpm.TransactionSet('/')
    for (name, arch) in tsInfo.NAkeys():
        pkghdr = tsInfo.getHeader(name, arch)
        rpmloc = tsInfo.localRpmPath(name, arch)
        serverid = tsInfo.serverid(name, arch)
        state = tsInfo.state(name, arch)
        if state in ('u', 'ud', 'iu', 'i'): # inst/update
            if os.path.exists(rpmloc):
                log(4, 'Using cached %s' % (os.path.basename(rpmloc)))
            else:
                log(2, 'Getting %s' % (os.path.basename(rpmloc)))
                try:
                    localrpmpath = retrygrab(tsInfo.remoteRpmUrl(name, arch), rpmloc, copy_local=0) 
                except URLGrabError, e:
                    errorlog(0, 'Error getting file %s' % tsInfo.remoteRpmUrl(name, arch))
                    errorlog(0, '%s' % e)
                    sys.exit(1)
                tsInfo.setlocalrpmpath(name, arch, localrpmpath)
            # we now actually have the rpm and we know where it is - so use it
            rpmloc = tsInfo.localRpmPath(name, arch)
            if conf.servergpgcheck[serverid]:
                rc = rpmUtils.checkSig(rpmloc, serverid)
                if rc == 1:
                    errorlog(0, _('Error: Could not find the GPG Key necessary to validate pkg %s') % rpmloc)
                    errorlog(0, _('Error: You may want to run yum clean or remove the file: \n %s') % rpmloc)
                    errorlog(0, _('Error: You may also check that you have the correct GPG keys installed'))
                    sys.exit(1)
                elif rc == 2:
                    errorlog(0, _('Error Reading Header on %s') % rpmloc)
                    errorlog(0, _('Error: You may want to run yum clean or remove the file: \n %s') % rpmloc)
                    sys.exit(1)
                elif rc == 3:
                    errorlog(0, _('Error: Untrusted GPG key on %s') % rpmloc)
                    errorlog(0, _('Error: You may want to run yum clean or remove the file: \n %s') % rpmloc)
                    errorlog(0, _('Error: You may also check that you have the correct GPG keys installed'))
                    sys.exit(1)
            else:
                if not rpmUtils.checkRpmMD5(rpmloc):
                    errorlog(0, _('Error: MD5 Signature check failed for %s') % rpmloc)
                    errorlog(0, _('Error: You may want to run yum clean or remove the file: \n %s') % rpmloc)
                    sys.exit(1)
            if state == 'i':
                tsfin.addInstall(pkghdr, (pkghdr, rpmloc), 'i')
            else:
                tsfin.addInstall(pkghdr, (pkghdr, rpmloc), 'u')
        elif state == 'a':
            pass
        elif state == 'e' or state == 'ed':
            tsfin.addErase(name)
    close_all()
    return tsfin

def diskspacetest(diskcheckts):
        diskcheckts.setFlags(rpm.RPMTRANS_FLAG_TEST)
        diskcheckts.setProbFilter(~rpm.RPMPROB_FILTER_DISKSPACE)
        tserrors = diskcheckts.run(callback.install_callback, '')
    if tserrors:
        diskerrors = []
        othererrors = []
        for (descr, (type, mount, need)) in tserrors:
            if type == rpm.RPMPROB_DISKSPACE:
                diskerrors.append(descr)
            else:
                othererrors.append(descr)
        if len(diskerrors) > 0:
            log(2, 'Error: Disk space Error')
            errorlog(0, 'You appear to have insufficient disk space to handle these packages')
            for error in diskerrors:
                errorlog(1, '%s' % error)
        if len(othererrors) > 0:
            log(2, 'Error reported but not a disk space error')
            errorlog(0, 'Unknown error testing transaction set:')
            for error in othererrors:
                errorlog(1, '%s' % error)
        sys.exit(1)


def descfsize(size):
    """The purpose of this function is to accept a file size in bytes,
    and describe it in a human readable fashion."""
    if size < 1000:
        return "%d bytes" % size
    elif size < 1000000:
        size = size / 1000.0
        return "%.2f kB" % size
    elif size < 1000000000:
        size = size / 1000000.0
        return "%.2f MB" % size
    else:
        size = size / 1000000000.0
        return "%.2f GB" % size
