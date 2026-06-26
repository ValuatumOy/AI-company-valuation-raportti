# Vaihe 0 – FAKTAT schema validator.
# A malformed FAKTAT block poisons every downstream stage, so fail loudly.

def validate(output: dict, context: dict) -> dict:
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    required_top = [
        "meta", "actuals", "forecast", "valuation_engine",
        "key_ratios", "credit_risk", "peers", "client_reported_signals", "flags",
    ]
    for k in required_top:
        chk(f"top-level: {k}", k in output, "missing" if k not in output else "")

    meta = output.get("meta", {}) or {}
    for k in ["company_name", "y_tunnus", "industry", "unit", "level"]:
        chk(f"meta.{k}", k in meta and meta[k] is not None, "missing/null")

    chk("flags is array", isinstance(output.get("flags"), list))
    chk("peers is array", isinstance(output.get("peers"), list))

    # Every numeric block that has its own years array must keep series the same
    # length as that years array. We check the common ones structurally.
    def years_ok(block_name, block):
        if not isinstance(block, dict):
            return
        years = block.get("years")
        if not isinstance(years, list):
            return
        n = len(years)
        for key, val in block.items():
            if key == "years":
                continue
            if isinstance(val, list) and val and all(
                isinstance(x, (int, float, type(None))) for x in val
            ):
                chk(
                    f"{block_name}.{key} length == years",
                    len(val) == n,
                    f"len {len(val)} != years {n}",
                )

    years_ok("actuals", output.get("actuals"))
    years_ok("forecast", output.get("forecast"))
    ve = output.get("valuation_engine", {}) or {}
    years_ok("valuation_engine.dcf", ve.get("dcf"))

    # unit must be explicit somewhere
    chk("unit explicit", bool(meta.get("unit")), "meta.unit required")

    # Magnitude sanity: a tEUR field holding a raw-EUR value (1000x off) is a
    # silent, fatal unit error. No company here has a single figure > 1e9 tEUR.
    def _max_abs(block):
        m = 0.0
        if isinstance(block, dict):
            for v in block.values():
                if isinstance(v, list):
                    for x in v:
                        if isinstance(x, (int, float)) and not isinstance(x, bool):
                            m = max(m, abs(x))
        return m

    big = max(_max_abs(output.get("actuals")), _max_abs(output.get("forecast")))
    chk("figures within plausible magnitude (no EUR/tEUR unit slip)", big < 1e9,
        f"largest figure {big:.0f} — likely a unit error" if big >= 1e9 else "ok")

    return {"passed": all(c["passed"] for c in checks), "checks": checks}
