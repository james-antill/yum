#!/usr/bin/python -tt
# -*- coding: utf-8 -*-
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

from misc import to_unicode, to_str, to_utf8
import os

def dummy_wrapper(str):
    '''
    Dummy Translation wrapper, just returning the same string.
    '''
    return str

__current_map__ = {}
def unicode_wrapper(str):
    '''
    Translation wrapper for english, just returning the same string but as
    "weird" unicode chars, for testing.
    '''
    if not __current_map__ or '%' in str:
        return to_unicode(str)

    ret = u''
    for byte in str:
        if byte in __current_map__:
            byte = __current_map__[byte]
        ret += to_unicode(byte)
    return ret

""" Use "hook" (left+right+retroflex) latin letters. """
unicode_hook_map = {'B' : 'Ɓ', 'C' : 'Ƈ', 'c' : 'ƈ', 'D' : 'Ɗ',
                    'F' : 'Ƒ', 'f' : 'ƒ', 'G' : 'Ɠ', 'K' : 'Ƙ', 'k' : 'ƙ',
                    'N' : 'Ɲ', 'P' : 'Ƥ', 'p' : 'ƥ', 'T' : 'Ƭ', 't' : 'ƭ',
                    'V' : 'Ʋ', 'Y' : 'Ƴ', 'y' : 'ƴ', 'Z' : 'Ȥ', 'z' : 'ȥ',
                    'b' : 'ɓ', 'd' : 'ɗ', 'g' : 'ɠ', 'h' : 'ɦ', 'l' : 'ɭ',
                    'm' : 'ɱ', 'n' : 'ɳ', 's' : 'ʂ', 'v' : 'ʋ'}

try: 
    '''
    Setup the yum translation domain and make _() translation wrapper
    available.
    using ugettext to make sure translated strings are in Unicode.
    '''
    import gettext
    t = gettext.translation('yum', fallback=True)
    _ = t.ugettext
except:
    '''
    Something went wrong so we make a dummy _() wrapper there is just
    returning the same text
    '''
    # We should have the same "types" for the return values
    _ = unicode_wrapper

if '_YUM_DEBUG_I18N_WRAP' in os.environ:
    vals = set(os.environ['_YUM_DEBUG_I18N_WRAP'].strip().split(","))
    if 'hook' in vals:
        __current_map__.update(unicode_hook_map)
    _ = unicode_wrapper
