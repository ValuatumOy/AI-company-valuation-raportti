"""Stage 0 company-data fetcher.

Runs the Valuatum kit for a given FID → the full FAKTAT input_data (the
valuation_engine.dcf/eva/wacc + actuals + forecast blocks the report needs).
This is what makes self-serve generation possible: a run created with just an
`identifier` (the Valuatum FID) fetches its own stage-0 data here instead of
requiring a hand-pasted JSON. The operator can still paste input_data directly.
"""


async def fetch_company_data(identifier: str, params: dict) -> dict:
    """identifier: a Valuatum FID (numeric). params: {company_name, company_code,
    industry_*, actuals?, estimates?}. Returns FAKTAT input_data, or raises."""
    from app import valuatum

    params = params or {}
    try:
        fid = int(str(identifier).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"Stage 0: tunniste ei ole kelvollinen Valuatum-FID: {identifier!r}"
        )
    company_name = params.get("company_name") or f"Yhtiö {fid}"
    data = None
    warnings = []
    async for ev in valuatum.export_stream(
        company_name=company_name,
        fid=fid,
        actuals=int(params.get("actuals") or 15),
        estimates=int(params.get("estimates") or 10),
        company_code_override=params.get("company_code"),
        industry_text=params.get("industry_text"),
        industry_code=params.get("industry_code"),
        industry_id=params.get("industry_id"),
        industry_tree=params.get("industry_tree"),
        skip_estimate_generation=bool(params.get("skip_estimate_generation")),
    ):
        if ev.get("step") == "error":
            raise RuntimeError(ev.get("message") or "Valuatum-haku epäonnistui")
        if ev.get("step") == "ready":
            data = ev.get("json")
            warnings = list(ev.get("warnings") or [])
    if data is None:
        raise RuntimeError("Valuatum-haku ei tuottanut dataa (ei 'ready'-tapahtumaa)")
    if warnings:
        data["fetch_warnings"] = warnings
    return data
