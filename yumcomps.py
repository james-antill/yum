#!/usr/bin/python

import rpm
import comps
import sys
import rpmUtils

overwrite_groups = 0

# debug
ts = rpm.TransactionSet()


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
        self.optional_pkgs = {}
        self.mandatory_pkgs = {}
        self.default_pkgs = {}
        self.pkgs_needed = {}
        self.grouplist = []
        self.optional_metapkgs = {}
        self.default_metapkgs = {}
        
        
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
                self.mandatory_pkgs[groupname] = []
                self.sub_groups[groupname] = []
                self.optional_pkgs[groupname] = []
                self.default_pkgs[groupname] = []
                self.pkgs_needed[groupname] = []
                
            # if we're overwriting groups - kill all the originals
            if overwrite_groups:
                self.group_installed[groupname]=0
                self.mandatory_pkgs[groupname] = []
                self.sub_groups[groupname] = []
                self.optional_pkgs[groupname] = []
                self.default_pkgs[groupname] = []
                self.pkgs_needed[groupname] = []

            packageobj = thisgroup.packages
            pkgs = packageobj.keys()
                            
            for pkg in pkgs:
                (type, name) = packageobj[pkg]
                if type == u'mandatory':
                    self.mandatory_pkgs[groupname].append(name)
                elif type == u'optional':
                    self.optional_pkgs[groupname].append(name)
                elif type == u'default':
                    self.default_pkgs[groupname].append(name)
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
        rpmdb_pkgs = self._get_installed()
        for groupname in self.grouplist:
            if len(self.mandatory_pkgs[groupname]) > 0:
                groupinstalled = 1
                for reqpkg in self.mandatory_pkgs[groupname]:
                    if not rpmdb_pkgs.has_key(reqpkg):
                        groupinstalled = 0
                self.group_installed[groupname]=groupinstalled
            else:
                groupinstalled = 0
                for anypkg in self.optional_pkgs[groupname] + self.default_pkgs[groupname]:
                    if rpmdb_pkgs.has_key(anypkg):
                        groupinstalled = 1
                self.group_installed[groupname]=groupinstalled


    def _get_installed(self):
        installed_pkgs = {}
        mi = ts.dbMatch()
        for hdr in mi:
            installed_pkgs[hdr['name']]=1
        
        return installed_pkgs
        
        
    def _dumppkgs(self, reqgroup=None):
        if reqgroup is None:
            groups = self.visible_groups
        elif reqgroup is "all_installed":
            groups = []
            for grp in self.group_installed.keys():
                if self.group_installed[grp] and grp in self.visible_groups:
                    groups.append(grp)
        else:
            groups = [reqgroup]
            
        for group in groups:
            print 'Group: %s' % group
            for item in self.mandatory_pkgs[group]:
                print '   %s *' % item
            for item in self.default_pkgs[group]:
                print '   %s +' % item
            for item in self.optional_pkgs[group]:
                print '   %s' % item
                


def main():
    compsgrpfun = Groups_Info()
    compsgrpfun.add('./comps.xml')
    compsgrpfun._dumppkgs('all_installed')


if __name__ == '__main__':
    main()
