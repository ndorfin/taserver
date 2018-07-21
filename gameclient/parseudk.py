#!/usr/bin/env python3
#
# Copyright (C) 2018  Maurice van der Pot <griffon26@kfk4ever.com>
#
# This file is part of taserver
# 
# taserver is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
# 
# taserver is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public License
# along with taserver.  If not, see <http://www.gnu.org/licenses/>.
#

from bitarray import bitarray
import string
import struct
import sys
import time
import traceback

class ParseError(Exception):
    pass

def toint(bits):
    zerobytes = bytes( (0,0,0,0) )
    longbytes = (bits.tobytes() + zerobytes)[0:4]
    return struct.unpack('<L', longbytes)[0]

def getnbits(n, bits):
    return bits[0:n], bits[n:]

def getstring(bits):
    stringbytes = bits.tobytes()
    result = []
    for b in stringbytes:
        if b != 0:
            result.append(chr(b))
        else:
            break

    return ''.join(result), bits[(len(result) + 1) * 8:]

class PacketWriter():
    def __init__(self, outfile):
        self.offset = 0
        self.outfile = outfile
        self.indentlevels = []

    def _writeindentedline(self, something):
        self.outfile.write(self.offset * ' ' + something + '\n')
        
    def writefield(self, bits, description):
        if bits:
            self._writeindentedline('%s %s' % (bits.to01(), description))
            self.offset += len(bits)
        else:
            self._writeindentedline('x %s' % description)
            self.offset += 1
        self.indentlevels.append(self.offset)

    def writerest(self, bits):
        self._writeindentedline(bits.to01() + '\n')
        self.offset = 0
        self.indentlevels = []

    def writeline(self, line):
        if self.offset != 0:
            raise RuntimeError('Cannot write line in the middle of another')
        self.outfile.write(line + '\n')

    def exdent(self, count):
        self.indentlevels = self.indentlevels[:-count]
        self.offset = self.indentlevels[-1]

def findshiftedstrings(bindata, i):
    emptychar = ' '
    continuationchar = '.'
    shiftedbytes = bindata[i:].tobytes()
    linechars = []
    stringchars = []
    for b in shiftedbytes:
        if b == 0:
            if len(stringchars) > 3:
                linechars.extend(stringchars + [continuationchar] * ((len(stringchars) + 1) * 7 + 1))
                stringchars = []
            else:
                linechars.extend([emptychar] * (len(stringchars) + 1) * 8)
                stringchars = []
            
        elif chr(b) in string.ascii_letters + string.digits + string.punctuation + ' ':
            stringchars.append(chr(b))
            
        else:
            linechars.extend([emptychar] * len(stringchars) * 8)
            stringchars = []
            linechars.extend([emptychar] * 8)

    if len(stringchars) > 3:
        linechars.extend(stringchars + [continuationchar] * ((len(stringchars) + 1) * 7 + 1))
    else:
        linechars.extend([emptychar] * (len(stringchars) + 1) * 8)

    result = ''.join(linechars)
    if result.strip() == '':
        return None
    else:
        return result

def main():
    infilename = 'serverpacketbindump.txt'
    outfilename = 'serverpacketbindump_parsed.txt'

    with open(infilename, 'rt') as infile:
        with open(outfilename, 'wt') as outfile:
            packetwriter = PacketWriter(outfile)
            
            for line in infile.readlines():

                line = line.strip()

                if not line:
                    continue

                packetsizestr, bindatastr = line.split()

                packetsize = int(packetsizestr)
                bindata = bitarray(bindatastr, endian='little')

                shiftedstrings = [findshiftedstrings(bindata, i) for i in range(8)]

                seqnrbits, bindata = getnbits(14, bindata)
                seqnr = toint(seqnrbits)
                packetwriter.writefield(seqnrbits, '(seqnr = %d)' % seqnr)

                flag1bits, bindata = getnbits(1, bindata)
                flag1 = toint(flag1bits)
                packetwriter.writefield(flag1bits, '(flag1)')
                if flag1 == 1:
                    
                    num1bits, bindata = getnbits(14, bindata)
                    num1 = toint(num1bits)
                    packetwriter.writefield(num1bits, '(num1 = %d)' % num1)

                    flag2bits, bindata = getnbits(1, bindata)
                    flag2 = toint(flag2bits)
                    packetwriter.writefield(flag2bits, '(flag2)')
                    if flag2 == 1:

                        num2bits, bindata = getnbits(14, bindata)
                        num2 = toint(num2bits)
                        packetwriter.writefield(num2bits, '(num2 = %d)' % num2)

                else:
                    unknownbits, bindata = getnbits(12, bindata)
                    packetwriter.writefield(unknownbits, '')

                    counterbits, bindata = getnbits(5, bindata)
                    counter = toint(counterbits)
                    packetwriter.writefield(counterbits, '(counter = %d)' % counter)

                    unknownbits, bindata = getnbits(17, bindata)
                    packetwriter.writefield(unknownbits, '')

                    nrofitemsbits, bindata = getnbits(5, bindata)
                    nrofitems = toint(nrofitemsbits)
                    packetwriter.writefield(nrofitemsbits, '(nr of items = %d)' % nrofitems)

                    for i in range(nrofitems):
                        part1bits, bindata = getnbits(168, bindata)
                        packetwriter.writefield(part1bits, '')

                        part1name, bindata = getstring(bindata)
                        packetwriter.writefield(None, '(%s)' % part1name)

                        part2bits, bindata = getnbits(128, bindata)
                        packetwriter.writefield(part2bits, '')
                        
                        part2name, bindata = getstring(bindata)
                        packetwriter.writefield(None, '(%s)' % part2name)

                        packetwriter.exdent(4)

                    terminatorbits, bindata = getnbits(2, bindata)
                    packetwriter.writefield(terminatorbits, '(terminator)')
                
                packetwriter.writerest(bindata)
                
                for i, shiftedstring in enumerate(shiftedstrings):
                    if shiftedstring:
                        packetwriter.writeline('%s%s (shifted by %d bits)' % (' ' * i, shiftedstring, i))
                
if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        time.sleep(5)
        sys.exit(-1)