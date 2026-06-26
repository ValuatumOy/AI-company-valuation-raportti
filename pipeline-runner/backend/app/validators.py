"""Run a user-supplied Python validator string in a restricted subprocess.

Contract the user code must satisfy:

    def validate(output: dict, context: dict) -> dict:
        return {"passed": bool, "checks": [{"name","passed","detail"}, ...]}

This is the user's own code on the user's machine: the subprocess + timeout is
to catch infinite loops and mistakes, not to sandbox hostile code. Network is
discouraged by convention; we don't hard-block it.
"""
import json
import subprocess
import sys
import tempfile

TIMEOUT_S = 5

_HARNESS = '''
import json, sys, traceback

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def _emit(r):
    sys.stdout.write("___VRESULT___" + json.dumps(r, ensure_ascii=False, default=str))

_payload = json.load(sys.stdin)
output = _payload["output"]
context = _payload["context"]

# ---- user validator code below ----
{user_code}
# ---- end user code ----

try:
    _r = validate(output, context)
    if not isinstance(_r, dict) or "passed" not in _r:
        _r = {"passed": False, "checks": [
            {"name": "contract", "passed": False,
             "detail": "validate() must return {'passed': bool, 'checks': [...]}"}]}
    _emit(_r)
except Exception:
    _emit({"passed": False, "checks": [
        {"name": "validator_exception", "passed": False,
         "detail": traceback.format_exc()}]})
'''


def run_validator(code: str, output, context) -> dict:
    src = _HARNESS.replace("{user_code}", code or "")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(src)
        path = f.name
    stdin = json.dumps({"output": output, "context": context}, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [sys.executable, path],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "checks": [
                {
                    "name": "timeout",
                    "passed": False,
                    "detail": f"validator exceeded {TIMEOUT_S}s",
                }
            ],
        }
    out = proc.stdout
    marker = "___VRESULT___"
    if marker in out:
        try:
            return json.loads(out.split(marker, 1)[1])
        except json.JSONDecodeError:
            pass
    return {
        "passed": False,
        "checks": [
            {
                "name": "validator_crash",
                "passed": False,
                "detail": (proc.stderr or out or "no output")[:4000],
            }
        ],
    }
