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
#
# Copyright 2008 Red Hat
#
# James Antill <james@fedoraproject.org>

"""
Best providers (best_providers.xml) parsing.
"""

import os
import sys
import gzip

import Errors
from yum.yumRepo import YumRepository

try:
    from xml.etree import cElementTree
except ImportError:
    import cElementTree
iterparse = cElementTree.iterparse

# _best_provider_mdtype = 'best_provider'
_best_provider_mdtype = 'best_provider'

class BestProvidersErrorParseFail(Errors.YumBaseError):
    """ An exception thrown for an unparsable best_provide file. """
    pass

class BestProviders(object):

    def __init__(self, next=None, obj=None):
        if next is None:
            next = set()
        self.next = next
        self.providers = {}
        if obj is not None:
            self.parse(obj)

    def _debug_dump(self, done=None):
        """ Return a dump of the data, for quick "testing". """

        if done is None:
            done = set()
        ret = "%s\n" % ('-' * 79)
        for provide in sorted(self.providers):
            if provide in done:
                continue
            done.add(provide)
            ret += '  Provide: %s\n' % provide
            pkgnames = self.providers[provide]
            ret += '           %s\n' % ", ".join(sorted(pkgnames))
        for bprov in self.next:
            ret += bprov._debug_dump(done)
        return ret

    def add_provider(self, provide, pkgname):
        """ Add a pkgname as a best provider of a requirement. """
        self.providers.setdefault(provide, set()).add(pkgname)
        for bprov in self.next:
            bprov.add_provider(provide, pkgname)

    def add_next(self, next):
        """ Add another BestProviders lookup. """
        self.next.add(next)

    def get_providers(self, provide):
        """ Given a provide, return all the pkgnames. """

        if provide in self.providers:
            return self.providers[provide]
        ret = set()
        for bprov in self.next:
            ret.update(bprov.get_providers(provide))
        return ret

    def filter_providers(self, requirement, providers):
        """ Given a list of providers, filter to thse that are best. """

        best_providers = self.get_providers(requirement)
        return set(providers).intersection(best_providers)

    def parse(self, obj):
        """ Given a repo, filename or file. Parse the best providers data. """
        if type(obj) in (type(''), type(u'')):
            infile = obj.endswith('.gz') and gzip.open(obj) or open(obj, 'rt')
        elif isinstance(obj, YumRepository):
            if _best_provider_mdtype not in obj.repoXML.repoData:
                return None
            infile = gzip.open(obj.retrieveMD(_best_provider_mdtype))
        else:   # obj is a file object
            infile = obj

        added = 0
        for event, elem in iterparse(infile):
            if elem.tag == 'bestprovider':
                provides = set()
                pkgnames = set()
                for child in elem:
                    if child.tag == 'provide':
                        if child.text in provides:
                            msg = "Multiple provides of %s" % child.text
                            raise BestProvidersErrorParseFail, msg
                        provides.add(child.text)
                    if child.tag == 'pkgname':
                        if child.text in pkgnames:
                            msg = "Multiple package names of %s" % child.text
                            raise BestProvidersErrorParseFail, msg
                        pkgnames.add(child.text)
                if not provides:
                    raise BestProvidersErrorParseFail, "No provides"
                if not pkgnames:
                    raise BestProvidersErrorParseFail, "No package names"
                for provide in provides:
                    for pkgname in pkgnames:
                        added += 1
                        self.add_provider(provide, pkgname)
        return added

def main():
    """ BestProviders test function. """

    def usage():
        print >> sys.stderr, "Usage: %s <best_provider> ..." % sys.argv[0]
        sys.exit(1)

    if len(sys.argv) < 2:
        usage()

    last = BestProviders()
    rest = []
    for filename in sys.argv[1:]:
        if not os.path.exists(filename):
            print "No such file:", filename
            continue

        last = BestProviders(set([last]), filename)

    for filename in reversed(sys.argv[1:]):
        print "File:", filename
        print last._debug_dump()
        print ''
        for last in last.next:
            pass

if __name__ == '__main__':
    main()
