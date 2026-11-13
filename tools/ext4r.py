import struct
class Ext4:
    def __init__(s,path):
        s.f=open(path,'rb'); sb=s.rd(1024,1024)
        s.magic,=struct.unpack_from('<H',sb,0x38); assert s.magic==0xEF53
        s.bsize=1024<<struct.unpack_from('<I',sb,0x18)[0]
        s.ipg,=struct.unpack_from('<I',sb,0x28); s.isz,=struct.unpack_from('<H',sb,0x58)
        s.incompat,=struct.unpack_from('<I',sb,0x60)
        s.dsz=struct.unpack_from('<H',sb,0xFE)[0] if s.incompat&0x80 else 32
        s.blocks=struct.unpack_from('<I',sb,0x4)[0]|((struct.unpack_from('<I',sb,0x150)[0]<<32) if s.incompat&0x80 else 0)
        s.first_db,=struct.unpack_from('<I',sb,0x14)
        s.gdt=(s.first_db+1)*s.bsize
    def rd(s,off,n): s.f.seek(off); return s.f.read(n)
    def inode(s,ino):
        g,i=divmod(ino-1,s.ipg)
        gd=s.rd(s.gdt+g*s.dsz,s.dsz)
        it=struct.unpack_from('<I',gd,8)[0]|((struct.unpack_from('<I',gd,0x28)[0]<<32) if s.dsz>=64 else 0)
        return s.rd(it*s.bsize+i*s.isz,s.isz)
    def size(s,ino_b): return struct.unpack_from('<I',ino_b,4)[0]|(struct.unpack_from('<I',ino_b,0x6C)[0]<<32)
    def mode(s,ino_b): return struct.unpack_from('<H',ino_b,0)[0]
    def extents(s,node):
        magic,ent,mx,depth=struct.unpack_from('<HHHH',node,0); assert magic==0xF30A,hex(magic)
        out=[]
        for e in range(ent):
            o=12+12*e
            if depth==0:
                lblk,ln,hi,lo=struct.unpack_from('<IHHI',node,o)
                if ln>32768: ln-=32768  # uninitialized
                out.append((lblk,(hi<<32)|lo,ln))
            else:
                lblk,lo,hi=struct.unpack_from('<IIH',node,o)
                out+=s.extents(s.rd(((hi<<32)|lo)*s.bsize,s.bsize))
        return out
    def data(s,ino):
        b=s.inode(ino); sz=s.size(b); flags,=struct.unpack_from('<I',b,0x20)
        iblock=b[0x28:0x28+60]
        if (s.mode(b)&0xF000)==0xA000 and sz<60 and not flags&0x80000: return iblock[:sz]
        if flags&0x10000000: return iblock[:sz]  # inline data (first 60 bytes only)
        assert flags&0x80000,'non-extent inode'
        buf=bytearray(sz)
        for lblk,pblk,ln in s.extents(iblock):
            st=lblk*s.bsize
            if st>=sz: continue
            chunk=s.rd(pblk*s.bsize,min(ln*s.bsize,sz-st)); buf[st:st+len(chunk)]=chunk
        return bytes(buf)
    def listdir(s,ino):
        d=s.data(ino); p=0; out=[]
        while p+8<=len(d):
            ino2,rl,nl,ft=struct.unpack_from('<IHBB',d,p)
            if rl<8: break
            if ino2: out.append((d[p+8:p+8+nl].decode(errors='replace'),ino2,ft))
            p+=rl
        return out
    def walk(s,ino=2,path=''):
        for name,i,ft in s.listdir(ino):
            if name in ('.','..'): continue
            full=path+'/'+name
            yield full,i,ft
            if ft==2: yield from s.walk(i,full)
    def lookup(s,path):
        ino=2
        for part in [p for p in path.split('/') if p]:
            ino=dict((n,i) for n,i,_ in s.listdir(ino))[part]
        return ino
