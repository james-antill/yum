import os
import sys
import rpm

    
# these functions swiped from up2date.py, copyright Red Hat, Inc.
def install_lilo(kernelList):
    import lilocfg
    ret = lilocfg.installNewImages(kernelList,test=0)
    return ret


def install_grub(kernelList):
    import grubcfg
    ret  = grubcfg.installNewImages(kernelList,test=0)
    return ret


#FIXME - db open should not suck - read from clientStuff
def openrpmdb(option=0, dbpath=None):
    dbpath = "/var/lib/rpm/"
    rpm.addMacro("_dbpath", dbpath)

    #log.log_me("Opening rpmdb in %s with option %s" % (dbpath,option))
    try:
        db = rpm.opendb(option)
    except rpm.error, e:
        raise RpmError(_("Could not open RPM database for reading.  Perhaps it is already in use?"))

    return db


# mainly here to make conflicts resolution cleaner
def findDepLocal(db, dep):
    header = None
    if dep[0] == '/':
        # Treat it as a file dependency
        try:
            # FIXME - file dep this needs to be fixed
            hdr_arry = db.Match(dep)
        except:
            hdr_arry = []
            
        for n in hdr_arry:
            header = db[n]
            break
    else:
        # Try it first as a package name
        try:
            hdr_arry = db.Match('name', 'dep')
        except:
            hdr_arry = []
        for n in hdr_arry:
            header = db[n]
            break
        else:
            try:
                hdr_arry = db.Match('provides',dep)
            except:
                hdr_arry = []
            for n in hdr_arry:
                header = db[n]
                break
            
    if header != None:
        return header
    else:
        return None
