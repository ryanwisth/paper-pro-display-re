"""Simulate how the reMarkable Paper Pro Gallery 3 panel renders an sRGB image, using the
forward model fitted from a photo of the panel (model.json). Usage: panel_sim.py in.png out.png [--size 810x1080]
Output is in 'photographed, white-balanced' space: compare it to the SOURCE image the same way."""
import sys, json, cv2, numpy as np
from PIL import Image
HERE = __file__.rsplit('/',1)[0]
M = json.load(open(HERE+'/model.json'))
def simulate(rgb_u8):
    lab = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2Lab).astype(np.float32)
    L = lab[...,0]*100/255; a = lab[...,1]-128; b = lab[...,2]-128
    C = np.hypot(a,b); H = np.degrees(np.arctan2(b,a)) % 360
    # tone
    tx = [0.0]+M['tone_x']+[100.0]; ty = [M['tone_y'][0]-1.0]+M['tone_y']+[M['tone_y'][-1]+2.0]
    L2 = np.interp(L, tx, ty)
    # per-hue params (circular interp over 24 bands)
    hc = np.array(M['hue_centers']); 
    def hinterp(vals):
        v = np.array(vals); xs = np.concatenate([hc-360, hc, hc+360]); ys = np.concatenate([v,v,v]); return np.interp(H, xs, ys)
    ratio = hinterp(M['chroma_ratio']); ceil = hinterp(M['chroma_ceiling']); shift = hinterp(M['hue_shift_deg'])
    # hue shifts are only trustworthy where the panel produced measurable chroma; damp elsewhere
    conf = np.clip((ceil-9)/12, 0, 1)      # 0 at ceiling<=9, 1 at >=21
    shift = shift*0.5*conf
    C2 = ceil*np.tanh(ratio*C/np.maximum(ceil,1e-3))   # smooth saturation toward the panel ceiling
    H2 = np.radians(H+shift)
    out = np.stack([L2*255/100, C2*np.cos(H2)+128, C2*np.sin(H2)+128], -1)
    out = np.clip(out,0,255).astype(np.uint8)
    rgb = cv2.cvtColor(out, cv2.COLOR_Lab2RGB)
    # panel = dithered, slightly soft; mimic with a touch of blur
    return cv2.GaussianBlur(rgb,(0,0),0.8)
if __name__=='__main__':
    im = np.asarray(Image.open(sys.argv[1]).convert('RGB'))
    size = (810,1080)
    if len(sys.argv)>3 and sys.argv[3]=='--size' : size = tuple(int(v) for v in sys.argv[4].split('x'))
    if im.shape[1]!=size[0]: im = cv2.resize(im, size, interpolation=cv2.INTER_AREA)
    Image.fromarray(simulate(im)).save(sys.argv[2])
