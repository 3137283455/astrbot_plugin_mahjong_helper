from pathlib import Path
import sys,json,hashlib,argparse
from dataclasses import dataclass
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image,ImageOps
@dataclass(frozen=True)
class BookSpec:
    code: str
    title: str
    filename: str
    count: int
    answer_threshold: int


BOOKS = (
    BookSpec("H300", "何切300", "何切300.pdf", 300, 1800),
    BookSpec("H301", "何切301", "何切301.pdf", 301, 2300),
)
DETECT_SCALE = 1.3
OUTPUT_SCALE = 2.0


def separated_peaks(values: np.ndarray, threshold: int, separation: int = 70) -> list[int]:
    working = values.copy()
    result: list[int] = []
    for _ in range(5):
        index = int(working.argmax())
        if int(working[index]) < threshold:
            break
        result.append(index)
        working[max(0, index - separation) : index + separation + 1] = 0
    return sorted(result)


def detect_labels(image: Image.Image, answer_threshold: int) -> list[tuple[str, int]]:
    pixels = np.asarray(image.convert("RGB"))
    height, width = pixels.shape[:2]
    dark = (pixels[:, :, 0] < 80) & (pixels[:, :, 1] < 80) & (pixels[:, :, 2] < 80)
    magenta = (
        (pixels[:, :, 0] > 140)
        & (pixels[:, :, 1] < 130)
        & (pixels[:, :, 2] > 90)
        & ((pixels[:, :, 0].astype(int) - pixels[:, :, 1].astype(int)) > 50)
    )
    q_score = np.convolve(
        dark[:, int(width * 0.07) : int(width * 0.34)].sum(axis=1),
        np.ones(35, dtype=int),
        mode="same",
    )
    a_score = np.convolve(
        magenta[:, int(width * 0.58) : int(width * 0.92)].sum(axis=1),
        np.ones(35, dtype=int),
        mode="same",
    )
    labels = [("question", y) for y in separated_peaks(q_score, 1200)]
    labels += [("answer", y) for y in separated_peaks(a_score, answer_threshold)]
    return sorted(labels, key=lambda item: item[1])



parser=argparse.ArgumentParser(description='Inventory PDF diagrams, labels and text geometry')
parser.add_argument('--source-dir',type=Path,required=True)
parser.add_argument('--output-dir',type=Path,required=True)
args=parser.parse_args()
ROOT=args.source_dir.resolve()
OUT=args.output_dir.resolve()
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'labels').mkdir(exist_ok=True)

def longest_run(row):
    edges=np.diff(np.r_[False,row,False].astype(int))
    starts=np.where(edges==1)[0];ends=np.where(edges==-1)[0]
    if not len(starts):return (0,0)
    index=int(np.argmax(ends-starts));return int(starts[index]),int(ends[index])

def badge_box(rgb,kind,center):
    h,w=rgb.shape[:2]
    left,right=(int(w*.07),int(w*.34)) if kind=='question' else (int(w*.58),int(w*.92))
    top,bottom=max(0,center-30),min(h,center+31)
    cut=rgb[top:bottom,left:right]
    r,g,b=[cut[:,:,i].astype(int) for i in range(3)]
    mask=(r<80)&(g<80)&(b<80) if kind=='question' else (r>140)&(g<130)&(b>90)&((r-g)>50)
    runs=[(*longest_run(row),y) for y,row in enumerate(mask)]
    a,z,y=max(runs,key=lambda v:v[1]-v[0])
    if z-a<25:raise ValueError(('no badge',kind,center,z-a))
    rows=np.where(mask[:,a:z].sum(axis=1)>(z-a)*.35)[0]
    return (left+a,top+int(rows.min()),left+z,top+int(rows.max())+1)

books=[]
for spec in BOOKS:
    pdf=pdfium.PdfDocument(str(ROOT/spec.filename))
    counts={'question':0,'answer':0};pages=[]
    with pdfplumber.open(ROOT/spec.filename) as textdoc:
        for pi in range(6,len(pdf)):
            p=pdf[pi];w,h=p.get_size()
            im=p.render(scale=DETECT_SCALE).to_pil().convert('RGB');rgb=np.array(im)
            labels=detect_labels(im,spec.answer_threshold)
            images=[]
            for obj in p.get_objects():
                if obj.type==3:
                    l,b,r,t=obj.get_bounds();images.append([l,h-t,r,h-b])
            chars=[{'text':c['text'],'box':[c['x0'],c['top'],c['x1'],c['bottom']],'size':c['size']}
                   for c in textdoc.pages[pi].chars if c.get('size',0)>=9 and c['top']<798 and c['text'].strip()]
            headings=[c for c in chars if c['size']>=14]
            heading=min((c['box'][1] for c in headings),default=None)
            anchors=[]
            for kind,center in labels:
                counts[kind]+=1;number=counts[kind];box=badge_box(rgb,kind,center)
                name=f'{spec.code}-{number:03d}-{kind}'
                digit=im.crop((int(box[0]+(box[2]-box[0])*.34),box[1],box[2],box[3]))
                pixels=np.asarray(digit);foreground=np.all(pixels>150,axis=2)
                cleaned=Image.fromarray(np.where(foreground,0,255).astype('uint8'))
                cleaned=ImageOps.expand(cleaned.resize((cleaned.width*5,cleaned.height*5)),border=24,fill=255)
                cleaned.convert('RGB').save(OUT/'labels'/f'{name}.png')
                badge=[v/DETECT_SCALE for v in box]
                x=(badge[0]+badge[2])/2;y=(badge[1]+badge[3])/2
                containing=[b for b in images if b[0]<=x<=b[2] and b[1]<=y<=b[3]]
                image=min(containing,key=lambda b:(b[2]-b[0])*(b[3]-b[1])) if containing else None
                anchors.append({'id':name,'kind':kind,'number':number,'center':center/DETECT_SCALE,
                                'badge':badge,'image':image})
            pages.append({'page':pi+1,'size':[w,h],'heading':heading,'chars':chars,'images':images,'anchors':anchors})
            if (pi+1)%40==0:print(spec.code,pi+1,counts,flush=True)
    assert counts=={'question':spec.count,'answer':spec.count},counts
    books.append({'code':spec.code,'filename':spec.filename,'title':spec.title,'count':spec.count,'source_sha256':hashlib.sha256((ROOT/spec.filename).read_bytes()).hexdigest(),'pages':pages})
(OUT/'source-scan.json').write_text(json.dumps(books,ensure_ascii=False),encoding='utf-8')
print('SCANNED',sum(b['count'] for b in books),flush=True)

