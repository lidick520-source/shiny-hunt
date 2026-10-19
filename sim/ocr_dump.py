"""Run Apple Vision OCR (the assistant's own OCR class) over every real frame once."""
import json, sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))   # the project folder
def main():
    from PIL import Image
    from mac_backend import OCR
    ocr = OCR()
    out = {}
    for f in sorted((HERE / 'frames').glob('*.png')):
        r = ocr.scan(Image.open(f).convert('RGB'))
        out[f.name] = {'text': r['text'], 'lines': r['lines']}
        print(f.name, r['text'][:60].replace('\n', ' '))
    (HERE / 'ocr_cache.json').write_text(json.dumps(out, ensure_ascii=False, indent=1))
    ocr.close()
    print('DONE', len(out))
if __name__ == '__main__':
    main()
