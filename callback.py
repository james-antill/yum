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
# Copyright 2002 Duke University 


import rpm
import os
import sys

from i18n import _

callbackfilehandles = {}
def install_callback(what, bytes, total, h, user):
    if what == rpm.RPMCALLBACK_TRANS_PROGRESS:
        pass
        
    elif what == rpm.RPMCALLBACK_TRANS_STOP:
        pass
        
    elif what == rpm.RPMCALLBACK_TRANS_START:
        pass
        
    elif what == rpm.RPMCALLBACK_INST_OPEN_FILE:
        hdr = None
        if h != None:
            hdr, rpmloc = h
            handle = '%s:%s.%s-%s-%s' % (hdr[rpm.RPMTAG_EPOCH],
              hdr[rpm.RPMTAG_NAME], hdr[rpm.RPMTAG_VERSION],
              hdr[rpm.RPMTAG_RELEASE], hdr[rpm.RPMTAG_ARCH])
            fd = os.open(rpmloc, os.O_RDONLY)
            callbackfilehandles[handle]=fd
            return fd
        else:
            print _("No header - huh?")
  
    elif what == rpm.RPMCALLBACK_INST_CLOSE_FILE:
        hdr = None
        if h != None:
            hdr, rpmloc = h
            handle = '%s:%s.%s-%s-%s' % (hdr[rpm.RPMTAG_EPOCH],
              hdr[rpm.RPMTAG_NAME], hdr[rpm.RPMTAG_VERSION],
              hdr[rpm.RPMTAG_RELEASE], hdr[rpm.RPMTAG_ARCH])
        os.close(callbackfilehandles[handle])
        fd = 0

    elif what == rpm.RPMCALLBACK_INST_PROGRESS:
        if h != None:
            pkg, rpmloc = h
            if total == 0:
                percent = 0
            else:
                percent = (bytes*100L)/total
            if conf.debuglevel >= 2:
                sys.stdout.write("\r%s %d %% done" % (pkg[rpm.RPMTAG_NAME], percent))
                if bytes == total:
                    print " "
            

        
    
