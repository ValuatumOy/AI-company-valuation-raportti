"""Per-run report context; no network calls, conversions or shared mutations."""
import copy
import json
import re

LANGUAGES = {"fi", "en"}
COUNTRIES = {"FI", "DK", "US"}
CURRENCIES = {"EUR", "DKK", "USD"}


def has_report_context(input_data=None, params=None):
    if any(key in (params or {}) for key in ("report_language", "country_code", "source_currency")):
        return True
    if not isinstance(input_data, dict):
        return False
    meta = input_data.get("meta")
    return any(key in input_data for key in ("actuals", "forecast", "valuation_engine")) or (
        isinstance(meta, dict) and any(key in meta for key in ("report_language", "country_code")))


def resolve_context(input_data=None, params=None):
    if isinstance(input_data, str):
        try:
            input_data = json.loads(input_data)
        except ValueError:
            input_data = None
    if not isinstance(input_data, dict):
        input_data = None
    meta = ((input_data or {}).get("meta") or {}) if has_report_context(input_data, params) else {}
    if not isinstance(meta, dict):
        raise ValueError("input_data.meta must be an object")
    params = params or {}
    language = params.get("report_language", meta.get("report_language", "fi"))
    country = params.get("country_code", meta.get("country_code", "FI"))
    currency = params.get("source_currency", meta.get("currency"))
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError("report_language must be fi or en")
    if not isinstance(country, str) or country not in COUNTRIES:
        raise ValueError("country_code must be FI, DK or US")
    if currency is None:
        if country != "FI":
            raise ValueError("Foreign reports require verified source_currency")
        currency = "EUR"
    if not isinstance(currency, str) or currency not in CURRENCIES:
        raise ValueError("source_currency must be EUR, DKK or USD")
    for key, value in (("country_code", country), ("currency", currency)):
        if meta.get(key) is not None and meta[key] != value:
            raise ValueError(f"Conflicting input_data.meta.{key}")
    return {"report_language": language, "country_code": country,
            "currency": currency, "unit": "t" + currency}


def normalize_input(input_data, params=None):
    if not has_report_context(input_data, params):
        return copy.deepcopy(input_data)
    context = resolve_context(input_data, params)
    data = copy.deepcopy(input_data)
    def check_units(value, path):
        if isinstance(value, dict):
            unit = value.get("unit")
            if isinstance(unit, str) and any(code in unit for code in CURRENCIES) \
                    and unit != context["unit"] and not unit.startswith(context["unit"] + "/"):
                raise ValueError(f"Conflicting {path}.unit: expected {context['unit']}")
            for key, child in value.items():
                check_units(child, f"{path}.{key}")
        elif isinstance(value, list):
            for i, child in enumerate(value):
                check_units(child, f"{path}[{i}]")
    check_units(data, "input_data")
    for block_name in (None, "meta", "actuals", "forecast", "valuation_engine"):
        block = data if block_name is None else data.get(block_name)
        if isinstance(block, dict):
            if block.get("unit") is not None and block["unit"] != context["unit"]:
                raise ValueError(f"Conflicting {block_name or 'input_data'}.unit: "
                                 f"expected {context['unit']}")
            block["unit"] = context["unit"]
    if context["currency"] != "EUR" and not any(
            (input_data.get(k) or {}).get("unit") == context["unit"]
            for k in ("meta", "actuals", "forecast", "valuation_engine")) \
            and input_data.get("unit") != context["unit"]:
        raise ValueError("Foreign monetary input must explicitly use thousands of source currency")
    data.setdefault("meta", {}).update(context)
    for peer in data.get("peers") or []:
        if isinstance(peer, dict):
            peer_currency = peer.get("currency") or (peer.get("meta") or {}).get("currency")
            peer_unit = peer.get("unit") or (peer.get("meta") or {}).get("unit")
            if context["currency"] != "EUR" and (peer_currency != context["currency"]
                                                  or peer_unit != context["unit"]):
                raise ValueError("Foreign peer monetary values require matching currency and unit")
    return data


def adapt_template(template, context):
    """Adapt stored templates before substitution, so source text stays intact."""
    language, currency = context["report_language"], context["currency"]
    if currency != "EUR":
        template = template.replace("tEUR", context["unit"]).replace("M€", "M " + currency)
    if context["country_code"] != "FI":
        template = re.sub(r"(?m)^5b\. SUOMALAISET VERROKKIEHDOKKAAT.*?(?=^6\.)",
                          '5b. No automatic Finnish peer lookup is available for this country. '
                          'Leave `finnish_peer_candidates` empty; use only supplied comparable data.\n',
                          template, flags=re.S)
        template = template.replace("Suomen-markkinan", "yhtiön todellisen kohdemarkkinan")
        template = template.replace("sekä suomeksi että englanniksi", "in the company's local language and English")
        template = template.replace("yritykset Suomi", "companies in " + context["country_code"])
        template = re.sub(r"`finnish_peer_candidates` on eri lista.*?ulkomaisia\.",
                          "`finnish_peer_candidates` must remain empty for this foreign demo.", template)
    if language != "en":
        return template
    replacements = {
        '"language": "fi"': '"language": "en"',
        "suomenkielinen AI-arvonmääritysraportti": "English-language AI valuation report",
        "suomenkielisestä AI-arvonmääritysraportista": "English-language AI valuation report",
        "hyvää, ymmärrettävää suomea": "clear, understandable English",
        "ymmärrettävää suomea": "understandable English",
        "Käytä suomalaista lukumuotoilua: 9,5 %": "Use English decimal formatting: 9.5 %",
        "Suomalainen lukumuotoilu: 9,5 %": "English decimal formatting: 9.5 %",
        '"Matala | Kohtalainen | Korkea"': '"Low | Moderate | High"',
        '"Heikko | Kohtalainen | Hyvä"': '"Weak | Moderate | Good"',
        '"Pessimistinen"': '"Pessimistic"',
        '"Konservatiivinen"': '"Conservative"',
        '"Optimistinen"': '"Optimistic"',
        '"TIIVISTELMÄ"': '"SUMMARY"',
        '"Oman pääoman arvo (odotusarvo) | Skenaarioiden odotusarvo | (tyhjä jos ei määritettävissä)"':
            '"Equity value (expected value) | Scenario expected value | (empty if unavailable)"',
    }
    for old, new in replacements.items():
        template = template.replace(old, new)
    template = template.replace("numerot muotoillaan suomalaisiksi automaattisesti renderöinnissä",
                                "numbers use the selected report locale when rendered")
    template = re.sub(r'("language"\s*:\s*"en",\s*"meta"\s*:\s*\{)',
                      lambda m: m.group(0) + '\n    "report_language": "en",'
                      + f'\n    "country_code": "{context["country_code"]}",'
                      + f'\n    "currency": "{currency}",', template)
    # Remove the numbered instruction that forbids English financial terminology.
    template = re.sub(r"(?m)^35\. Suomenkieliset termit:.*$",
                      "35. Use plain English financial terminology; explain technical terms on first use.",
                      template)
    return template


def system_directive(context):
    if context["report_language"] == "fi" and context["country_code"] == "FI" \
            and context["currency"] == "EUR":
        return None
    unit, country = context["unit"], context["country_code"]
    language = "English" if context["report_language"] == "en" else "Finnish"
    return (
        f"You are writing a valuation report in {language}. This per-run locale instruction "
        "takes precedence over conflicting language, example wording, geography and currency "
        "instructions in the legacy template. Keep its JSON keys, block types and numerical "
        "grounding rules. All natural-language values must use the report language, including "
        "metadata, headings, cover labels, confidence, data quality, scoring explanations, "
        "scenario names, source descriptions, clarifications/questions and machine_readable "
        "descriptions. Translate every prescribed verbatim phrase and example; never copy "
        "Finnish prose into an English report. Use English scenario names Pessimistic, "
        "Conservative and Optimistic when writing English. Use (source: domain) citations "
        "and (see section N) references in English. "
        f"Country is {country}; source currency is {context['currency']}; all monetary "
        f"report inputs and values are thousands of that currency ({unit}), including legacy "
        "keys ending _teur. Preserve supplied numbers; no FX conversion or currency relabelling. "
        "Use spaces for thousands grouping and a decimal point for English decimals. "
        "Keep the supplied country and currency in report metadata. Use the supplied company "
        "identifier (CVR for Denmark, issuer/CIK/ISIN for US), not a fabricated Finnish business ID. "
        "Ground identity, market geography and research in this company and its actual markets. "
        "Do not assume Finnish market size or Finnish peers for foreign companies. "
        + ("Do not apply Finnish corporate tax, Finnish tax-authority valuation rules or "
           "Finnish inheritance/gift-tax comparisons. Only use an explicitly supplied source "
           "tax assumption; if absent, state the limitation without inventing tax rates. "
           if country != "FI" else "")
        + "Return one complete valid JSON object. A financial report must state in its "
        "disclaimer that it is not investment advice. Do not invent forecasts or valuation "
        "engine values when absent."
    )


def adapt_validator(code, context):
    if not code or context["report_language"] != "en":
        return code
    # Stored seeded validators have language-specific display-text checks.
    code = code.replace('"konservatiiv"', '"conservativ"')
    code = code.replace('"sijoitusneuvo"', '"investment advi"')
    code = code.replace(r'\(lähde:', r'\(source:')
    number_definition = (r'_NUM_RE = re.compile(r"[−-]?(?:\d{1,3}(?:(?:" + _SEP '
                         r'+ r"|,)\d{3})+|\d+)(?:\.\d+)?\s*%?")')
    code = re.sub(r"(?m)^_NUM_RE = re\.compile\(.*$", lambda _: number_definition, code)
    code = code.replace('replace(",", ".")', 'replace(",", "")')
    return code
