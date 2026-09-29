#!/usr/bin/env python3
"""Bring verified builder folders into the confirmatory test's layout without the
operator reading them: workbooks to src/<id>/, owner_brief.txt to briefs/<id>.txt,
key.json to keys/<id>.json, and one businesses.json entry per business in the
answer runner's format. Prints only ids, file counts and hashes.

    /usr/bin/python3 import_biz.py <builds.json>
    builds.json: [{"id": "...", "dir": "<verified builder folder>", "pass": true}, ...]
"""
import hashlib
import json
import os
import shutil
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(path):
    builds = json.load(open(path))
    bpath = os.path.join(R, "businesses.json")
    B = json.load(open(bpath)) if os.path.exists(bpath) else {}
    sums = []
    for b in builds:
        if not b.get("pass"):
            print(f"{b['id']}: not imported (did not pass verification)")
            continue
        key = json.load(open(os.path.join(b["dir"], "key.json")))
        files = [os.path.basename(f) for f in key["files"]]
        src = os.path.join(R, "src", b["id"])
        os.makedirs(src, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(b["dir"], f), os.path.join(src, f))
            sums.append(f"{hashlib.sha256(open(os.path.join(src, f), 'rb').read()).hexdigest()}  {b['id']}/{f}")
        for sub in ("briefs", "keys"):
            os.makedirs(os.path.join(R, sub), exist_ok=True)
        shutil.copy2(os.path.join(b["dir"], "owner_brief.txt"), os.path.join(R, "briefs", f"{b['id']}.txt"))
        shutil.copy2(os.path.join(b["dir"], "key.json"), os.path.join(R, "keys", f"{b['id']}.json"))
        qs = key["eval_questions"]
        assert len(qs) == 3 and all({"ask", "answer", "how"} <= set(q) for q in qs), f"{b['id']}: key format"
        B[b["id"]] = {"set": "confirm", "files": files, "open": key["open_request"],
                      "plain": [{"ask": q["ask"], "answer": q["answer"], "how": q["how"]} for q in qs]}
        print(f"{b['id']}: {len(files)} workbook(s), {len(key['owner_facts'])} owner facts")
    json.dump(B, open(bpath, "w"), indent=1, ensure_ascii=False)
    open(os.path.join(R, "src", "SHA256SUMS"), "a").write("\n".join(sums) + ("\n" if sums else ""))
    print(f"businesses.json: {len(B)} businesses")


if __name__ == "__main__":
    main(sys.argv[1])
