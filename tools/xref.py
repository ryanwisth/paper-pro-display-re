import re,sys,bisect
def load(dis_path, so_path):
    so=open(so_path,'rb').read()
    lines=open(dis_path).read().splitlines()
    ins=[]  # (addr, mnemonic, ops)
    labels={}
    for L in lines:
        m=re.match(r'^([0-9a-f]+) <(.+)>:$',L)
        if m: labels[int(m.group(1),16)]=m.group(2); continue
        m=re.match(r'^\s+([0-9a-f]+):\s+(\S+)\s*(.*)$',L)
        if m: ins.append((int(m.group(1),16),m.group(2),m.group(3)))
    return so,ins,labels
def cstr(so,a):
    if a<=0 or a>=len(so): return None
    e=so.find(b'\0',a)
    if e<0 or e-a>300: return None
    s=so[a:e]
    if len(s)<3 or any(c<9 or c>126 for c in s): return None
    return s.decode()
def xrefs(so,ins):
    page={}; out=[]
    for a,mn,ops in ins:
        if mn=='adrp':
            m=re.match(r'(x\d+), 0x([0-9a-f]+)',ops)
            if m: page[m.group(1)]=int(m.group(2),16)
        elif mn=='add':
            m=re.match(r'(x\d+), (x\d+), #0x([0-9a-f]+)$',ops) or re.match(r'(x\d+), (x\d+), #(\d+)$',ops)
            if m and m.group(2) in page:
                imm=int(m.group(3),16) if '0x' in ops.split('#')[1] else int(m.group(3))
                out.append((a,page[m.group(2)]+imm))
        elif mn=='bl' or mn=='b':
            pass
    return out
def func_start(ins_addrs, ins, idx):
    # walk back to a prologue-ish instruction after a ret
    j=idx
    while j>0:
        a,mn,ops=ins[j-1]
        if mn in ('ret',) or (mn=='b' and j>1): break
        j-=1
    return ins[j][0]
if __name__=='__main__':
    so,ins,labels=load(sys.argv[1],sys.argv[2])
    pats=sys.argv[3:]
    X=xrefs(so,ins)
    addrs=[i[0] for i in ins]
    for a,t in X:
        s=cstr(so,t)
        if s and any(p in s for p in pats):
            print(hex(a),'->',hex(t),repr(s))
