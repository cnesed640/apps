#!/usr/bin/env python3
"""read_asset가 남긴 큰 글자 파일 결과(JSON)에서 원문만 꺼내 저장한다.
사용: python3 tools/extract_text_asset.py <결과.json> <저장경로> [기대 sha256]
"""
import hashlib, json, re, sys
src, dst = sys.argv[1], sys.argv[2]
want = sys.argv[3] if len(sys.argv) > 3 else ''
data = json.load(open(src, encoding='utf-8'))
texts = [b.get('text', '') for b in data if isinstance(b, dict) and b.get('type') == 'text'] if isinstance(data, list) else [str(data)]
body = None
for t in texts:
    m = re.search(r'<(untrusted-document-[A-Za-z0-9]+)>\n?(.*?)\n?</\1>', t, re.S)
    if m:
        body = m.group(2); break
if body is None:
    sys.exit('원문 구간을 찾지 못함')
raw = body.encode('utf-8')
got = hashlib.sha256(raw).hexdigest()
if want and got != want:
    # 감싸기 줄바꿈 차이를 한 번 더 시도
    for alt in (body + '\n', body.rstrip('\n')):
        if hashlib.sha256(alt.encode()).hexdigest() == want:
            raw = alt.encode(); got = want; break
open(dst, 'wb').write(raw)
print(got, 'OK' if (not want or got == want) else 'MISMATCH')
sys.exit(0 if (not want or got == want) else 2)
