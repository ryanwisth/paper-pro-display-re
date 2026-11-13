"""Region statistics of a rectified panel photo against its source image -> measurements.json.
Usage: python3 measure.py SOURCE.png   (run in the folder holding rect_photo_810.png from rectify.py)"""
import sys, cv2, numpy as np, json
from PIL import Image
src = np.asarray(Image.open(sys.argv[1]).convert('RGB'))
src = cv2.resize(src,(810,1080),interpolation=cv2.INTER_AREA)
rect = np.asarray(Image.open('rect_photo_810.png').convert('RGB'))
# blur both to suppress misregistration / moire / dither
k=9
sb = cv2.GaussianBlur(src,(0,0),k); rb = cv2.GaussianBlur(rect,(0,0),k)
# crop margins (edges may be inexact) 
m=25
sb=sb[m:-m,m:-m]; rb=rb[m:-m,m:-m]
def to_lab(x): return cv2.cvtColor(x, cv2.COLOR_RGB2Lab).astype(np.float32)  # L 0..255, a/b offset 128
sl = to_lab(sb); rl = to_lab(rb)
sL = sl[...,0]*100/255; rL = rl[...,0]*100/255
sa,sbb = sl[...,1]-128, sl[...,2]-128; ra,rbb = rl[...,1]-128, rl[...,2]-128
sC = np.hypot(sa,sbb); rC = np.hypot(ra,rbb)
sH = (np.degrees(np.arctan2(sbb,sa))+360)%360; rH=(np.degrees(np.arctan2(rbb,ra))+360)%360
out={}
# 1) Tone: L_photo vs L_src binned
bins=np.linspace(0,100,11)
tone=[]
for i in range(10):
    sel=(sL>=bins[i])&(sL<bins[i+1])
    if sel.sum()>200: tone.append((float(bins[i]),float(bins[i+1]),int(sel.sum()),float(np.median(rL[sel])),float(np.percentile(rL[sel],25)),float(np.percentile(rL[sel],75))))
out['tone_L_src_bin -> L_photo(median,q25,q75)']=tone
# photo neutrals: bezel not in frame; use overall stats
out['L_src range p1/p50/p99']=[float(np.percentile(sL,p)) for p in (1,50,99)]
out['L_photo range p1/p50/p99']=[float(np.percentile(rL,p)) for p in (1,50,99)]
# 2) Chroma retention by hue band (src hue), only where src chroma is meaningful
hb=np.arange(0,361,30)
chroma=[]
for i in range(12):
    sel=(sH>=hb[i])&(sH<hb[i+1])&(sC>15)
    if sel.sum()>300:
        ratio=np.median(rC[sel]/np.maximum(sC[sel],1))
        dh=np.median(((rH[sel]-sH[sel]+540)%360)-180)
        chroma.append(dict(hue_lo=int(hb[i]),hue_hi=int(hb[i+1]),n=int(sel.sum()),src_C_med=float(np.median(sC[sel])),photo_C_med=float(np.median(rC[sel])),C_ratio=float(ratio),hue_shift_deg=float(dh),src_L=float(np.median(sL[sel])),photo_L=float(np.median(rL[sel]))))
out['chroma_by_src_hue']=chroma
# 3) Max chroma envelope the panel reached per hue (photo hue)
env=[]
for i in range(12):
    sel=(rH>=hb[i])&(rH<hb[i+1])
    if sel.sum()>300: env.append(dict(hue_lo=int(hb[i]),hue_hi=int(hb[i+1]),photo_C_p99=float(np.percentile(rC[sel],99)),src_C_p99_same_px=float(np.percentile(sC[sel],99))))
out['panel_chroma_envelope_by_photo_hue']=env
# 5) global linear fit L_photo = a + b*L_src ; and chroma ratio global
A=np.vstack([np.ones(sL.size),sL.ravel()]).T
coef,_,_,_=np.linalg.lstsq(A,rL.ravel(),rcond=None)
out['global_tone_fit a,b']=[float(coef[0]),float(coef[1])]
sel=sC>10
out['global_C_ratio_median']=float(np.median(rC[sel]/sC[sel]))
out['photo_white_balance_check: mean rgb of top-5% L pixels (src vs photo)']=[[int(v) for v in sb.reshape(-1,3)[np.argsort(sL.ravel())[-int(sL.size*0.05):]].mean(0)],[int(v) for v in rb.reshape(-1,3)[np.argsort(sL.ravel())[-int(sL.size*0.05):]].mean(0)]]
json.dump(out,open('measurements.json','w'),indent=1)
print(json.dumps(out,indent=1))
