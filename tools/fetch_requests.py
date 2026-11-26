#!/usr/bin/env python3
"""큰 페이지·여러 파일 신청 처리 — GitHub 자동 작업이 실행한다.

requests/<신청id>.json 을 하나씩 읽어
  1. link(구글 드라이브 공유 링크, Dropbox, 일반 https 주소)에서 파일을 내려받고
  2. zip이면 안전하게 풀고(index.html이 있는 폴더를 찾음), html이면 index.html 한 장으로 두고
  3. tools/deploy.py 로 검사·게시한 뒤
  4. results/<신청id>.json 에 결과를 남기고 요청 파일을 지운다.
결과는 정기 작업(Claude)이 신청함에 되돌려 적는다.

요청 파일 형식:
{"uid","id","kind":"new|update|remove","slug","title","team","owner","ext","desc",
 "audience","until","createdAt","link"}
"""
import json, re, shutil, subprocess, sys, tempfile, urllib.parse, urllib.request, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = "https://cnesed640.github.io/apps/"
MAX_DOWNLOAD = 50 * 1024 * 1024
MAX_UNZIP = 100 * 1024 * 1024
MAX_ENTRIES = 400
FIELDS = ("uid", "id", "kind", "slug", "title", "team", "owner", "ext", "desc", "audience", "until", "createdAt")


def direct_url(link):
    """공유 링크를 바로 내려받을 수 있는 주소로 바꾼다."""
    u = urllib.parse.urlparse(link.strip())
    if u.scheme != "https":
        raise ValueError("https:// 로 시작하는 링크만 받을 수 있음")
    host = u.netloc.lower()
    if host.endswith("drive.google.com") or host.endswith("docs.google.com"):
        m = re.search(r"/d/([A-Za-z0-9_-]{10,})", u.path)
        q = urllib.parse.parse_qs(u.query)
        fid = m.group(1) if m else (q.get("id") or [None])[0]
        if not fid:
            raise ValueError("구글 드라이브 파일 링크에서 파일 ID를 찾지 못함 (폴더 링크가 아니라 파일 링크를 주세요)")
        return f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
    if host.endswith("dropbox.com"):
        q = urllib.parse.parse_qs(u.query)
        q["dl"] = ["1"]
        return urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(q, doseq=True)))
    return link.strip()


def download(link, dest):
    req = urllib.request.Request(direct_url(link), headers={"User-Agent": "Mozilla/5.0 cne-apps-publisher"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        total = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD:
                raise ValueError("내려받는 파일이 50MB를 넘음")
            f.write(chunk)
    return dest


def unpack(path, work):
    """내려받은 파일을 게시할 폴더로 만든다. 그 폴더 경로를 돌려준다."""
    head = Path(path).read_bytes()[:512]
    site = Path(work) / "site"
    site.mkdir()
    if head.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) > MAX_ENTRIES:
                raise ValueError(f"압축 안 파일이 {len(infos)}개로 너무 많음")
            if sum(i.file_size for i in infos) > MAX_UNZIP:
                raise ValueError("압축을 풀면 100MB를 넘음")
            for i in infos:
                name = i.filename.replace("\\", "/")
                parts = [p for p in name.split("/") if p not in ("", ".")]
                if not parts or ".." in parts or name.startswith("/"):
                    raise ValueError(f"올바르지 않은 파일 경로: {name}")
                if parts[0] == "__MACOSX" or parts[-1] in (".DS_Store", "Thumbs.db"):
                    continue
                target = site.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        # index.html이 맨 위에 없고, 폴더 하나만 있으면 그 폴더를 맨 위로 본다
        if not (site / "index.html").exists():
            kids = [k for k in site.iterdir()]
            if len(kids) == 1 and kids[0].is_dir() and (kids[0] / "index.html").exists():
                return kids[0]
        return site
    text = head.decode("utf-8", errors="ignore").lower()
    if "<html" in text or "<!doctype html" in text:
        shutil.copy(path, site / "index.html")
        return site
    if b"<html" in head.lower() or b"google" in head.lower():
        raise ValueError("파일 대신 웹페이지가 내려옴 — 공유 설정을 '링크가 있는 모든 사용자'로 바꿔 주세요")
    raise ValueError("zip 파일이나 HTML 파일이 아님")


def main():
    reqs = sorted((REPO / "requests").glob("*.json"))
    if not reqs:
        print("처리할 요청 없음")
        return
    (REPO / "results").mkdir(exist_ok=True)
    for rf in reqs:
        try:
            req = json.loads(rf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"{rf.name}: 읽기 실패 {e}")
            rf.unlink()
            continue
        item = {k: req.get(k, "") for k in FIELDS}  # admin 같은 다른 필드는 받지 않는다
        result = {"uid": item["uid"], "id": item["id"], "slug": item["slug"], "kind": item["kind"]}
        with tempfile.TemporaryDirectory() as work:
            try:
                if item["kind"] != "remove":
                    got = download(req.get("link", ""), Path(work) / "download.bin")
                    item["dir"] = str(unpack(got, work))
                batch = Path(work) / "batch.json"
                batch.write_text(json.dumps({"base": BASE, "items": [item]}, ensure_ascii=False), encoding="utf-8")
                out = subprocess.run([sys.executable, str(REPO / "tools" / "deploy.py"), str(batch), "--repo", str(REPO)],
                                     capture_output=True, text=True, check=True)
                r = json.loads(out.stdout)["results"][0]
                result.update({k: r[k] for k in ("status", "url", "error") if k in r})
            except Exception as e:
                result.update(status="error", error=str(e)[:300])
        (REPO / "results" / rf.name).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        rf.unlink()
        print(f"{rf.name}: {result.get('status')} {result.get('url') or result.get('error', '')}")


if __name__ == "__main__":
    main()
