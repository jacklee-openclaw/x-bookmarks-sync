#!/usr/bin/env python3
import html
import json
import re
import sys
import urllib.request

url = sys.argv[1]

def clean_html(raw: str) -> str:
    txt = re.sub(r'<script[\s\S]*?</script>', ' ', raw, flags=re.I)
    txt = re.sub(r'<style[\s\S]*?</style>', ' ', txt, flags=re.I)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    return html.unescape(re.sub(r'\s+', ' ', txt)).strip()

def bad_text(t: str) -> bool:
    s = (t or '').lower()
    if len((t or '').strip()) < 120:
        return True
    bad = [
        '环境异常', '完成验证后即可继续访问', 'javascript is not available',
        'enable javascript', 'don’t miss what’s happening', "don't miss what's happening"
    ]
    return any(x in s for x in bad)

title = ''
full_text = ''
source = 'http-static'

try:
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read(800000).decode('utf-8','ignore')
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.I|re.S)
    if m:
        title = html.unescape(re.sub(r'\s+',' ',m.group(1))).strip()
    full_text = clean_html(raw)[:20000]
except Exception:
    pass

if bad_text(full_text):
    try:
        purl='https://r.jina.ai/http://' + url.replace('https://','').replace('http://','')
        req=urllib.request.Request(purl,headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req,timeout=20) as r:
            ptxt=r.read(800000).decode('utf-8','ignore').strip()
        if ptxt and len(ptxt) > len(full_text):
            full_text=ptxt[:20000]
            source='r.jina.ai'
            tm=re.search(r'^Title:\s*(.+)$', ptxt, re.M)
            if tm and not title:
                title=tm.group(1).strip()
    except Exception:
        pass

excerpt = (full_text[:280] if full_text else '')
corpus = (url + ' ' + title + ' ' + excerpt).lower()
rules = {
  'ai':['ai','llm','agent','gpt','openai','anthropic','模型','智能体'],
  'eda':['eda','asic','rtl','timing','cadence','synopsys','chip','芯片'],
  'verification':['verification','uvm','formal','assertion','coverage','验证'],
  'career':['career','interview','management','leader','hiring','职业','面试'],
  'tools':['tool','automation','script','workflow','效率','自动化','工具','github','gitlab']
}
cat='misc'
for k,arr in rules.items():
    if any(x in corpus for x in arr):
        cat=k
        break

safe_name = re.sub(r'[\\/:*?"<>|]+','-', (title or '网页收藏')).strip().strip('.') or '网页收藏'
print(json.dumps({
  'title': (title or '网页收藏').replace('\n',' '),
  'safe_name': safe_name[:100],
  'excerpt': (excerpt or '（未抓取到摘要，可后续补充）').replace('\n',' '),
  'full_text': (full_text or '（未抓取到原文全文）').replace('\r',' '),
  'category': cat,
  'source': source,
  'incomplete': bad_text(full_text),
}, ensure_ascii=False))
