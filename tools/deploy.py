#!/usr/bin/env python3
"""중등교육과 웹 배포 — 승인된 신청을 저장소에 반영한다.

사용법:
    python3 tools/deploy.py batch.json [--repo .] [--today 2026-11-26]

batch.json 형식:
{
  "base": "https://계정.github.io/저장소/",
  "items": [
    {"uid": "...", "id": "...", "kind": "new|update|remove", "slug": "credit-calc",
     "title": "...", "team": "...", "owner": "...", "ext": "...", "desc": "...",
     "audience": "schools|public", "until": "2027-03-31", "createdAt": "...",
     "html": "<!DOCTYPE html>..."}
  ]
}

하는 일:
  - new/update: <slug>/index.html, <slug>/meta.json 작성 (저장소 맨 위)
      · 바닥글(data-cne-footer)이 없으면 표준 바닥글을 넣는다
      · 대상이 '학교·교사'면 검색 노출 막기(noindex)를 넣는다
  - remove: <slug>/ 삭제
  - 고치기·내리기는 처음 올린 사람(uid)만 가능 (item에 "admin": true면 예외)
  - 주민등록번호·휴대전화 번호 형태가 있으면 막음, sha256이 있으면 내용 일치 확인(불일치 → status "retry")
  - 첫 화면(index.html)과 catalog.json을 */meta.json으로 다시 만든다
  - 결과(JSON)를 표준 출력으로 낸다: 항목별 url, 상태, 오류 / 게시 기간 지난 페이지
"""
import argparse, datetime as dt, hashlib, html as H, json, re, shutil, sys
from pathlib import Path

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TEAMS = ["과장실", "중등교육팀", "국제교육팀", "진로진학팀"]
AUD = {"schools": "학교·교사", "public": "누구나"}
MAX = 240_000
BLOCK = [(re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"), "주민등록번호 형태"),
         (re.compile(r"(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"), "휴대전화 번호")]
RESERVED = {"tools", "assets", "apps", "index", "catalog", "readme", "admin", "api", "static"}


def footer(m):
    contact = " · ".join(x for x in [m.get("team"), m.get("owner"), m.get("ext")] if x)
    return (
        '\n<footer data-cne-footer style="margin:48px 0 0;padding:20px 16px;border-top:1px solid #ccc;'
        'font:14px/1.6 system-ui,sans-serif;color:#555;text-align:center">'
        f"충청남도교육청 중등교육과 · 문의 {H.escape(contact)}<br>"
        f"게시 {H.escape(m['updated'][:10])} · 이 페이지는 Claude의 도움을 받아 만들었습니다."
        "</footer>\n"
    )


def prepare(page, m):
    if "data-cne-footer" not in page:
        i = page.lower().rfind("</body>")
        page = page[:i] + footer(m) + page[i:] if i >= 0 else page + footer(m)
    if m.get("audience") == "schools" and 'name="robots"' not in page:
        i = page.lower().find("</head>")
        tag = '<meta name="robots" content="noindex">\n'
        page = page[:i] + tag + page[i:] if i >= 0 else tag + page
    return page


def check(it):
    s = it.get("slug", "")
    if not (3 <= len(s) <= 40 and SLUG.match(s)):
        return "주소 이름 형식이 맞지 않음"
    if s in RESERVED:
        return "쓸 수 없는 주소 이름(예약어)"
    if it.get("kind") not in ("new", "update", "remove"):
        return "종류 값이 맞지 않음"
    if it["kind"] != "remove":
        page = it.get("html") or ""
        if not re.search(r"<!doctype html|<html[\s>]", page, re.I):
            return "HTML 문서가 아님"
        if len(page.encode()) > MAX:
            return "240KB 초과"
        if re.search(r"claude\.use\(|window\.claude", page):
            return "Claude 안에서만 되는 기능(claude.use)을 씀 — 밖에서는 작동하지 않음"
        for rx, label in BLOCK:
            if rx.search(page):
                return f"{label}가 들어 있어 자동 게시를 막음 — 지우고 다시 내 주세요"
    return None


def index_page(pages):
    cards = "\n".join(
        f'<a class="c" href="{H.escape(p["slug"])}/"><b>{H.escape(p["title"])}</b>'
        f'<span>{H.escape(p.get("desc", ""))}</span>'
        f'<small>{H.escape(p.get("team", ""))} · {H.escape(p.get("owner", ""))} · {H.escape(p["updated"][:10])}</small></a>'
        for p in pages
    ) or "<p>아직 게시된 페이지가 없습니다.</p>"
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>충남교육청 중등교육과 공개 페이지</title>
<style>body{{margin:0;font:16px/1.6 system-ui,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;background:#f4f2ee;color:#141210}}
main{{max-width:960px;margin:0 auto;padding:40px 16px}}h1{{font-size:30px;margin:0 0 6px}}p.l{{color:#5f5a53;margin:0 0 24px}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}}
.c{{display:flex;flex-direction:column;gap:4px;background:#fff;border:1px solid #d8d3cb;border-radius:10px;padding:16px;color:inherit;text-decoration:none}}
.c:hover,.c:focus-visible{{border-color:#c8371e;outline:none}}.c span{{color:#5f5a53;font-size:15px}}.c small{{color:#8a847b}}</style></head>
<body><main><h1>중등교육과 공개 페이지</h1><p class="l">충청남도교육청 중등교육과 직원들이 학교와 함께 쓰려고 만든 페이지입니다.</p>
<div class="g">
{cards}
</div></main></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    batch = json.loads(Path(a.batch).read_text(encoding="utf-8"))
    base = batch.get("base", "").rstrip("/") + "/"
    apps = repo
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")
    out = []
    for it in batch.get("items", []):
        r = {"uid": it.get("uid"), "id": it.get("id"), "slug": it.get("slug"), "kind": it.get("kind")}
        err = check(it)
        d = apps / it.get("slug", "_") if not err else None
        if not err and it["kind"] == "new" and d.exists():
            err = "이미 있는 주소 이름"
        if not err and it["kind"] in ("update", "remove") and not d.exists():
            err = "게시된 페이지가 없음"
        if not err and it["kind"] in ("update", "remove") and not it.get("admin"):
            owner = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("uid") if (d / "meta.json").exists() else None
            if owner and owner != it.get("uid"):
                err = "본인이 올린 페이지만 고치거나 내릴 수 있음"
        if not err and it["kind"] != "remove" and it.get("sha256"):
            if hashlib.sha256(it["html"].encode("utf-8")).hexdigest() != it["sha256"]:
                r.update(status="retry", error="옮기는 중 내용이 달라짐(해시 불일치) — 다음 실행 때 다시 시도")
                out.append(r)
                continue
        if err:
            r.update(status="error", error=err)
            out.append(r)
            continue
        if it["kind"] == "remove":
            shutil.rmtree(d)
            r.update(status="removed")
        else:
            old = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
            m = {k: it.get(k, "") for k in ("slug", "title", "team", "owner", "ext", "desc", "audience", "until")}
            m.update(created=old.get("created", now), updated=now, uid=it.get("uid"),
                     history=(old.get("history", []) + [{"id": it.get("id"), "kind": it["kind"], "at": now}])[-20:])
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.html").write_text(prepare(it["html"], m), encoding="utf-8")
            (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
            r.update(status="live", url=f"{base}{it['slug']}/")
        out.append(r)

    pages = []
    for f in sorted(apps.glob("*/meta.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        m["url"] = f"{base}{m['slug']}/"
        pages.append(m)
    pages.sort(key=lambda p: p["updated"], reverse=True)
    (repo / "index.html").write_text(index_page(pages), encoding="utf-8")
    (repo / "catalog.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
    (repo / ".nojekyll").touch()
    expired = [p["slug"] for p in pages if p.get("until") and p["until"] < a.today]
    catalog = [{k: p.get(k, "") for k in ("slug", "title", "team", "owner", "desc", "audience", "url", "updated", "created", "uid")}
               for p in pages]
    for c in catalog:
        c["updatedAt"] = c.pop("updated")
    json.dump({"results": out, "catalog": catalog, "expired": expired}, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
