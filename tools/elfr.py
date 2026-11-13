import struct
def parse(d):
    assert d[:4]==b'\x7fELF' and d[4]==2
    e_shoff,=struct.unpack_from('<Q',d,0x28); e_shentsize,e_shnum,e_shstrndx=struct.unpack_from('<HHH',d,0x3A)
    secs=[]
    for i in range(e_shnum):
        o=e_shoff+i*e_shentsize
        name,typ,flags,addr,off,size,link,info,align,entsize=struct.unpack_from('<IIQQQQIIQQ',d,o)
        secs.append(dict(name=name,type=typ,addr=addr,off=off,size=size,link=link,entsize=entsize))
    shstr=secs[e_shstrndx]
    def sname(n): e=d.index(b'\0',shstr['off']+n); return d[shstr['off']+n:e].decode()
    for s in secs: s['n']=sname(s['name'])
    by={s['n']:s for s in secs}
    dynstr=by.get('.dynstr')
    def ds(o): e=d.index(b'\0',dynstr['off']+o); return d[dynstr['off']+o:e].decode(errors='replace')
    needed=[];soname=None
    if '.dynamic' in by:
        s=by['.dynamic']
        for i in range(s['size']//16):
            tag,val=struct.unpack_from('<qQ',d,s['off']+i*16)
            if tag==1: needed.append(ds(val))
            if tag==14: soname=ds(val)
    syms=[]
    s=by.get('.dynsym')
    if s:
        for i in range(s['size']//24):
            st_name,st_info,st_other,st_shndx,st_value,st_size=struct.unpack_from('<IBBHQQ',d,s['off']+i*24)
            syms.append((ds(st_name),st_info>>4,st_info&15,st_shndx,st_value,st_size))
    return dict(secs=secs,needed=needed,soname=soname,syms=syms)
