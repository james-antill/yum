#!/usr/bin/python

import rpm
import comps
import sys
import rpmUtils

overwrite_groups = 0

# goals
# be able to list which groups a user has installed based on whether or
# not mandatory pkgs are installed and whether all the metapkgs are installed
# (consider making metapkgs groups with settings in this model)
# so groups have end up being default, optional or mandatory
# - all groups (as listed in the xml file are default only)
# so installgroup X installs all the default+mandatory pkgs and any group

# determine if groupreqs are included on install

# is installed? - group reqs not consulted - metapkgs and pkgs in mandatory 
# install - groupreqs installed too - metapkgs in default or better and pkgs in default or better
# update - if pkg in group installed (any class of pkg) check for update, all mandatory pkgs and metapkgs will be updated/installed
# erase - only pkgs in group - not subgroups nor metapkgs
# 
class Groups_Info:
    def __init__(self):
        self.group_installed = {}
        self.sub_groups = {}
        self.visible_groups = []
        self.optionalpkgs = {}
        self.mandatorypkgs = {}
        self.defaultpkgs = {}
        self.pkgs_needed = {}
        self.grouplist = []
        self.optionalmetapkgs = {}
        self.defaultmetapkgs = {}
        
        
    def add(self, filename):
        """This method takes a filename and populates the above 
        dicts"""
        compsobj = comps.Comps(filename)
        groupsobj = compsobj.groups
        groups = groupsobj.keys()
        # should populate for all groups but only act on uservisible groups only
        
        for groupname in groups:
            thisgroup = groupsobj[groupname]
            
            if thisgroup.user_visible:
                self.visible_groups.append(groupname)
            
            # make all the key entries if we don't already have them
            if groupname not in self.grouplist:
                self.grouplist.append(groupname)
                self.group_installed[groupname]=0
                self.mandatorypkgs[groupname] = []
                self.sub_groups[groupname] = []
                self.optionalpkgs[groupname] = []
                self.defaultpkgs[groupname] = []
                self.pkgs_needed[groupname] = []
                
            # if we're overwriting groups - kill all the originals
            if overwrite_groups:
                self.group_installed[groupname]=0
                self.mandatorypkgs[groupname] = []
                self.sub_groups[groupname] = []
                self.optionalpkgs[groupname] = []
                self.defaultpkgs[groupname] = []
                self.pkgs_needed[groupname] = []

            packageobj = thisgroup.packages
            pkgs = packageobj.keys()
                            
            for pkg in pkgs:
                (type, name) = packageobj[pkg]
                if type == u'mandatory':
                    self.mandatorypkgs[groupname].append(name)
                elif type == u'optional':
                    self.optionalpkgs[groupname].append(name)
                elif type == u'default':
                    self.defaultpkgs[groupname].append(name)
                else:
                    print '%s not optional, default or mandatory - ignoring' % name
                
            for sub_group_id in thisgroup.groups.keys():
                for sub_groupname in groups:
                    if sub_group_id == groupsobj[sub_groupname].id:
                        self.sub_groups[groupname].append(sub_groupname)
        # now we have the data populated
        # time to vet it against the rpmdb
        self._installedgroups()
        
    def _installedgroups(self):
            rpmdbpkgs = self._get_installed()
            for groupname in self.grouplist:
                groupinstalled = 1
                for reqpkg in self.mandatorypkgs[groupname]:
                    if not rpmdbpkgs.has_key(reqpkg):
                        groupinstalled = 0
                self.group_installed[groupname]=groupinstalled


    def _get_installed(self):
        installedpkgs = {}
        mi = ts.dbMatch()
        for hdr in mi:
            installedpkgs[hdr['name']]=1
        
        return installedpkgs
        
        
    def _dumppkgs(self, reqgroup=None):
        if reqgroup is None:
            groups = self.visible_groups
        else:
            groups = [reqgroup]
            
        for group in groups:
            print 'Group: %s' % group
            for item in self.mandatorypkgs[group]:
                print '   %s *' % item
            for item in self.defaultpkgs[group]:
                print '   %s +' % item
            for item in self.optionalpkgs[group]:
                print '   %s' % item
                


def main():
    compsgrpfun = Groups_Info()
    compsgrpfun.add('./comps.xml')
    compsgrpfun.add('./othercomps.xml')
    compsgrpfun._dumppkgs()


if __name__ == '__main__':
    main()
