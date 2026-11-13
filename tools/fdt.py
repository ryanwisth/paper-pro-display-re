import struct
def parse(d, base=0):
    magic,tot,off_struct,off_str,off_rsv,ver,lcv,bootcpu,sz_str,sz_struct=struct.unpack('>10I',d[base:base+40])
    assert magic==0xd00dfeed
    S=base+off_struct; strs=base+off_str
    p=S; stack=[]; out=[]
    def nm(o):
        e=d.index(b'\0',strs+o); return d[strs+o:e].decode()
    while True:
        tok,=struct.unpack('>I',d[p:p+4]); p+=4
        if tok==1:
            e=d.index(b'\0',p); name=d[p:e].decode(); p=(e+1+3)&~3
            stack.append(name); out.append(('node','/'.join(stack),None))
        elif tok==2: stack.pop()
        elif tok==3:
            ln,no=struct.unpack('>II',d[p:p+8]); p+=8
            val=d[p:p+ln]; p=(p+ln+3)&~3
            out.append(('prop','/'.join(stack)+'/'+nm(no),val))
        elif tok==4: continue
        elif tok==9: break
    return out,tot
def show(v):
    if v and v[-1:]==b'\0' and all(32<=c<127 or c==0 for c in v): return repr(v.rstrip(b'\0').decode(errors='replace'))
    if len(v)%4==0 and len(v)<=32: return ' '.join(hex(x) for x in struct.unpack('>%dI'%(len(v)//4),v))
    return '<%d bytes %s>'%(len(v),v[:16].hex())
