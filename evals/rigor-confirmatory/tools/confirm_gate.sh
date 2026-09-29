#!/bin/zsh
# Gate one build attempt of the confirmatory brains: code check, then the two checkers.
# usage: confirm_gate.sh <run>   (brains under rigor2/build_v02/<run>/<business>/out/)
set -e
T=${0:A:h}; R=${T:h}; RUN=$1
/usr/bin/python3 - "$R" "$RUN" <<'PY'
import json, sys
R, run = sys.argv[1:3]
B = json.load(open(f"{R}/businesses.json"))
spec = {b: {"brain_files": [f"{R}/build_v02/{run}/{b}/out/{f}" for f in v["files"]], "home": f"{R}/build_v02/{run}/{b}/home"}
        for b, v in B.items()}
json.dump(spec, open(f"{R}/spec_{run}.json", "w"), indent=1)
PY
cd $R && ~/lab/spreadsheet-brain/.venv/bin/python $T/check_build.py $R/spec_$RUN.json src > $R/check_$RUN.json
cd $T && /usr/bin/python3 gate.py $RUN $R/spec_$RUN.json $R/briefs > $R/gate_$RUN.log 2>&1
/usr/bin/python3 - "$R" "$RUN" <<'PY'
import json, sys
R, run = sys.argv[1:3]
cb = {r["business"]: r for r in json.load(open(f"{R}/check_{run}.json"))}
g = json.load(open(f"{R}/gate/{run}/summary.json"))
out = {}
for b in cb:
    out[b] = {"code_gate": cb[b]["pass_code_gate"], "checkers": g[b]["pass"], "pass": cb[b]["pass_code_gate"] and g[b]["pass"],
              "lint_flags": cb[b]["lint_flags"], "substantive": cb[b]["max_substantive"], "prompts": cb[b]["max_prompts"]}
json.dump(out, open(f"{R}/gate_result_{run}.json", "w"), indent=1)
# only pass or fail per business reaches the operator
print(json.dumps({b: ("PASS" if v["pass"] else "FAIL") for b, v in out.items()}, indent=1))
PY
