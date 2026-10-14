"""Report presentation context. Monetary values always retain source currency."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import re


@dataclass(frozen=True)
class ReportLocale:
    language: str = "fi"
    country: str = "FI"
    currency: str = "EUR"

    @property
    def unit(self):
        return "t" + self.currency


_locale = ContextVar("report_locale", default=ReportLocale())


def current():
    return _locale.get()


def locale_from(data):
    data = data or {}
    if not isinstance(data, dict):
        raise ValueError("Report context must be an object")
    meta = data.get("meta") or {}
    language = meta.get("report_language", "fi")
    country = meta.get("country_code", "FI")
    currency = meta.get("currency", "EUR")
    if language not in ("fi", "en") or country not in ("FI", "DK", "US") or currency not in ("EUR", "DKK", "USD"):
        raise ValueError("Unsupported report language, country or currency")
    ctx = ReportLocale(language, country, currency)
    for unit in (data.get("unit"), meta.get("unit")):
        if unit and unit != ctx.unit:
            raise ValueError(f"Report currency {currency} conflicts with monetary unit {unit}")
    validate_report_units(data, currency)
    return ctx


@contextmanager
def report_context(data):
    token = _locale.set(locale_from(data))
    try:
        yield current()
    finally:
        _locale.reset(token)


def localized_input(index=0):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            data = args[index] if len(args) > index else kwargs.get("input_data", kwargs.get("report"))
            with report_context(data):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def money_scale(value):
    ctx = current()
    amount = abs(value or 0)
    if amount >= 1_000_000:
        label = "mrd. €" if ctx.language == "fi" and ctx.currency == "EUR" else "bn " + ctx.currency
        return 1_000_000.0, label, 2
    if amount >= 10_000:
        label = "M€" if ctx.language == "fi" and ctx.currency == "EUR" else "M " + ctx.currency
        return 1_000.0, label, 1
    return 1.0, ctx.unit, 0


def number(value, decimals=0):
    if value is None:
        return "–"
    if current().language == "en":
        return f"{value:,.{decimals}f}"
    sign = "-" if value < 0 else ""
    whole = f"{abs(value):,.{decimals}f}"
    return sign + whole.replace(",", " ").replace(".", ",")


def t(text):
    """Translate authored presentation literals, never source company/prose fields."""
    ctx = current()
    out = EN.get(text, text) if ctx.language == "en" else text
    if ctx.country != "FI":
        out = out.replace("Business ID", "Company identifier").replace("Y-tunnus", "Yhtiön tunniste")
    out = out.replace("tEUR", ctx.unit)
    if ctx.currency != "EUR" or ctx.language == "en":
        out = out.replace("mrd. €", "bn " + ctx.currency).replace("M€", "M " + ctx.currency)
        out = out.replace("€/henkilö", ctx.currency + "/henkilö").replace("EUR/employee", ctx.currency + "/employee")
    return out


def tf(text, *values):
    return t(text).format(*values)


def validate_report_units(report, currency=None):
    currency = currency or current().currency
    pattern = re.compile(r"\b(?:t|M\s*|bn\s*)(EUR|DKK|USD)\b|\b(EUR|DKK|USD)/employee\b|(?P<euro>€)", re.I)

    def check(value, path="report"):
        if isinstance(value, dict):
            for key, val in value.items():
                # Names, source URLs and identifiers are factual text, not unit labels.
                if key in ("company_name", "url", "source", "source_url", "industry", "industry_display_en",
                           "domicile", "source_id", "source_identifier", "identifier", "business_id", "y_tunnus"):
                    continue
                if key in ("currency", "unit") and val in ("EUR", "DKK", "USD") and val != currency:
                    raise ValueError(f"Report currency {currency} conflicts with {val} label at {path}.{key}")
                check(val, path + "." + str(key))
        elif isinstance(value, list):
            for val in value:
                check(val, path)
        elif isinstance(value, str):
            for match in pattern.finditer(value):
                found = "EUR" if match.group("euro") else (match.group(1) or match.group(2)).upper()
                if found != currency:
                    raise ValueError(f"Report currency {currency} conflicts with {found} label at {path}")
    check(report)


EN = {
    "Luvut ovat tallennettu lähde-ennuste. Saatavilla oleva ennustejakso on {0}–{1}. EBIT-marginaali on jakson alussa {2} ja lopussa {3}.": "The figures are the stored source forecast. The available forecast period is {0}–{1}. EBIT margin is {2} at the start of the period and {3} at the end.",
    "(lähde: <a class=\"src\" href=\"{0}\">{1}</a>{2})": "(source: <a class=\"src\" href=\"{0}\">{1}</a>{2})",
    "{0} tEUR": "{0} tEUR",
    "Arvostusväli": "Valuation range",
    "Matala": "Low",
    "Korkea": "High",
    "Arvion luottamustaso": "Valuation confidence",
    "EVA = NOPLAT − WACC × sijoitettu pääoma.": "EVA = NOPLAT − WACC × invested capital.",
    "(tukee kasvua)": "(supports growth)",
    "(rajoittaa kasvua)": "(constrains growth)",
    "liikevaihtopoikkeaman tarkistus": "revenue anomaly review",
    "lähderekisteri": "source register",
    "osakemäärä": "number of shares",
    "osakekohtainen DCF-arvo": "DCF value per share",
    "liiketoimintaprofiili": "business profile",
    "kasvuarvio": "growth assessment",
    "arvonmääritysmoottori": "valuation engine",
    "käyttäjän antamat lisätiedot": "additional information provided by the user",
    "julkinen lähde": "public source",
    "tEUR": "tEUR",
    "{0} M€": "{0} M€",
    "Oman pääoman arvo · skenaarioiden odotusarvo": "Equity value · scenario expected value",
    "Konservatiivinen perusskenaario ei tue positiivista omistaja-arvoa. Mahdollinen arvo on optio- tai strategista arvoa — arvoa, joka perustuu epävarman tulevaisuuden mahdollisuuden toteutumiseen, ei nykyiseen kassavirtaan. Se kuvataan skenaarioissa eikä esitetä yrityksen arvona.": "The conservative base case does not support positive equity value. Any value represents option or strategic value: an uncertain future opportunity rather than current cash flow. This is described in the scenarios.",
    "Skenaarioiden todennäköisyyksillä painotettu odotusarvo. Todennäköisyydet ovat AI:n muodostamia, muokattavia oletuksia. Konservatiivinen perusskenaario ja haarukan ääripäät esitetään alla.": "Expected value weighted by scenario probabilities. The probabilities are editable assumptions produced by AI. The conservative base case and the range endpoints are shown below.",
    "Arvopäivä": "Valuation date",
    "Raportin päivä": "Report date",
    "Käyttötarkoitus": "Purpose",
    "Tarkoitetut käyttäjät": "Intended users",
    "Arvon standardi": "Standard of value",
    "Arvostettava omistusosuus": "Ownership interest being valued",
    "Valuutta / yksikkö": "Currency / unit",
    "DCF eli diskontattu kassavirta": "DCF: discounted cash flow",
    "Menetelmä, jossa yhtiön ennustetut tulevat kassavirrat muunnetaan nykyarvoon. Mitä kauempana tulevaisuudessa ja mitä epävarmempi kassavirta on, sitä vähemmän se painaa arvossa.": "A method that converts the company's forecast future cash flows into present value. Cash flows further in the future or subject to greater uncertainty contribute less to value.",
    "WACC eli pääoman tuottovaatimus": "WACC: weighted average cost of capital",
    "Korko, jolla tulevat kassavirrat diskontataan nykyhetkeen. Kuvaa tuottoa, jonka sijoittaja vaatii yhtiön riskitasolla — korkeampi tuottovaatimus tarkoittaa matalampaa arvoa.": "The rate used to discount future cash flows to the present. It reflects the return investors require for the company's risk. A higher required return means a lower value.",
    "EVA eli lisäarvomenetelmä": "EVA: economic value added",
    "Arvottaa yhtiön sen mukaan, kuinka paljon tulos ylittää tai alittaa pääoman kustannuksen. Perustuu samaan ennusteeseen kuin DCF, joten menetelmät eivät ole toisistaan riippumattomia.": "Values the company according to how much its earnings exceed or fall short of the cost of capital. It uses the same forecast as DCF, so the methods are not independent.",
    "Yritysarvo (EV)": "Enterprise value (EV)",
    "Koko liiketoiminnan arvo ennen rahoitusrakennetta. Omistajien osuus saadaan, kun yritysarvosta vähennetään korolliset velat ja lisätään kassavarat.": "The value of the entire business before its financing structure. Equity value is obtained by deducting interest-bearing debt and adding cash.",
    "Oman pääoman arvo eli omistaja-arvo": "Equity value",
    "Osakkeenomistajille kuuluva osuus yhtiön arvosta — luku, jota tämä raportti ensisijaisesti arvioi.": "The portion of company value attributable to shareholders, which this report primarily estimates.",
    "Perusskenaario (base case)": "Base case",
    "Toteutuneisiin lukuihin ja toimitettuun ennusteeseen perustuva laskelma ilman optimistisia tai pessimistisiä lisäoletuksia.": "A calculation based on actual results and the supplied forecast, without additional optimistic or pessimistic assumptions.",
    "Odotusarvo": "Expected value",
    "Skenaarioiden todennäköisyyksillä painotettu keskiarvo. Todennäköisyydet ovat muokattavia oletuksia, eivät ennuste.": "The probability-weighted average of the scenarios. Probabilities are editable assumptions, not a forecast.",
    "Omistaja-arvon alaraja": "Equity value floor",
    "Osakeyhtiön omistajan vastuu on rajattu, joten omistaja-arvo ei voi olla alle 0 tEUR, vaikka laskelma antaisi negatiivisen luvun.": "Shareholders have limited liability, so equity value is floored at 0 tEUR even if the calculation produces a negative result.",
    "Terminaaliarvo": "Terminal value",
    "Ennustejakson jälkeisten vuosien yhteenlaskettu arvo yhtenä lukuna. Muodostaa usein suuren osan DCF-arvosta.": "The combined value of the years beyond the forecast period. It often accounts for a large share of DCF value.",
    "Arvostuskertoimet ja verrokit": "Valuation multiples and peers",
    "Vertailukelpoisten yhtiöiden hinnoittelu (esim. EV/liikevaihto), jota käytetään ristiintarkistuksena omalle laskelmalle.": "The pricing of comparable companies (for example EV/revenue), used to cross-check the valuation.",
    "Käänteislaskelma": "Reverse valuation",
    "Laskelma siitä, millaista tulevaa kassavirtaa esimerkiksi rahoituskierroksen valuaatio edellyttäisi ollakseen perusteltu.": "A calculation of the future cash flow required to justify a valuation, for example in a funding round.",
    "Luottamustaso": "Confidence level",
    "Raportin oma arvio siitä, kuinka vankalla pohjalla arvio on — määräytyy datan kattavuudesta ja menetelmien yhtenäisyydestä.": "The report's assessment of how well supported the valuation is, based on data coverage and consistency of the methods.",
    " siltaa yritysarvon oman pääoman arvoon.": " bridges enterprise value to equity value.",
    " Toteutuneet vuodet nimellisinä, ennustevuodet diskontattuina.": " Actual years are nominal; forecast years are discounted.",
    "Vastuuvapaus": "Disclaimer",
    "{0} · AI-Arvonmääritysraportti": "{0} · AI Valuation Report",
    "mrd. €": "mrd. €",
    "M€": "M€",
    "{0}–{1} tEUR": "{0}–{1} tEUR",
    "Oman pääoman arvo · konservatiivinen perusskenaario": "Equity value · conservative base case",
    ". Menetelmät ja painot perustellaan arvonmääritysosiossa.": ". The methods and weights are explained in the valuation section.",
    "Skenaarioiden odotusarvo": "Scenario expected value",
    "Konservatiivinen perusskenaario": "Conservative base case",
    "Sijoitettu|pääoma": "Invested|capital",
    "Yritysarvo|(EVA)": "Enterprise value|(EVA)",
    "Oma|pääoma (EVA)": "Equity|value (EVA)",
    "Sijoitettuun pääomaan lisätään ennustejakson ja terminaalin diskontattu lisäarvo (EVA). ": "Invested capital plus the discounted economic value added (EVA) from the forecast period and terminal period. ",
    " Raportin arvo (DCF) {0} {1}; ero EVA-menetelmään selitetään tekstissä.": " Report value (DCF) {0} {1}; the difference from EVA is explained in the text.",
    " EVA-menetelmän arvo vastaa raportin DCF-arvoa.": " EVA value equals the report's DCF value.",
    "Ennustetut kassavirrat": "Forecast cash flows",
    "yhtiön toteutuneista luvuista ja ennusteesta": "from the company's actual results and forecast",
    "tuottovaatimuksella (WACC)": "at the required return (WACC)",
    "Yritysarvo": "Enterprise value",
    "koko liiketoiminnan arvo": "value of the entire business",
    "− velat + kassa": "− debt + cash",
    "Oman pääoman arvo": "Equity value",
    "Skenaariopainotus": "Scenario weighting",
    "kolme skenaariota todennäköisyyksin": "three scenarios with probabilities",
    "kannen ja tiivistelmän johtava luku": "the leading figure on the cover and in the summary",
    "AI-Arvonmääritysraportti": "AI Valuation Report",
    "Chrome/Chromium ei löytynyt — PDF-vienti ei käytettävissä.": "Chrome/Chromium was not found — PDF export is unavailable.",
    "tilinpäätösdata": "financial statement data",
    "Arvonmäärityksen tulos": "Valuation result",
    "Pessimistinen skenaario{0}": "Pessimistic scenario{0}",
    "Konservatiivinen perusskenaario{0}": "Conservative base case{0}",
    "Optimistinen skenaario{0}": "Optimistic scenario{0}",
    "Konservatiivinen perusskenaario on laskettu yhtiön toteutuneista luvuista ja ennusteista": "The conservative base case is calculated from the company's actual results and forecasts",
    "Viimeisin tilinpäätös": "Latest financial statements",
    "Yhtiö": "Company",
    "Korolliset|velat": "Interest-bearing|debt",
    "Kassa": "Cash",
    "Muut|erät": "Other|items",
    "Nettokassa": "Net cash",
    "Liikevaihto": "Revenue",
    "Oma pääoma": "Equity",
    "Tase": "Balance sheet",
    "Verottajan malli": "Finnish tax authority reference",
    "Yhtiötaso (virallinen tilinpäätösdata)": "Company level (official financial statements)",
    "Konsernitaso (virallinen tilinpäätösdata)": "Consolidated level (official financial statements)",
    "Tausta ja liiketoiminta": "Business and background",
    "Mitä yhtiö tekee, miten se on kehittynyt ja mitä ennuste olettaa — pohja, jolle arvonmääritys rakentuu.": "What the company does, how it has developed and what the forecast assumes: the basis for the valuation.",
    "Arvonmääritys": "Valuation",
    "Menetelmät, laskelmat, herkkyydet ja riskit — miten arvio muodostuu ja mikä sitä liikuttaisi.": "Methods, calculations, sensitivities and risks: how the valuation is derived and what could change it.",
    "hylätty": "Rejected",
    "Arvostusväli — sama jana läpi raportin": "Valuation range — consistent throughout the report",
    "Painotettu arvo {0} tEUR": "Weighted value {0} tEUR",
    "Sijoitetun pääoman rivi on laskentamoottorin pääomakustannus jaettuna WACCilla. Viimeisen sarakkeen kumulatiivinen summa on tulevien EVA-erien nykyarvo yhteensä (ennustejakso ja terminaali).": "The invested capital row is the engine's capital charge divided by WACC. The final column's cumulative sum is the total present value of future EVA (forecast and terminal periods).",
    "Päämenetelmä: vapaat kassavirrat ja terminaaliarvo Valuatumin DCF-moottorista.": "Primary method: free cash flows and terminal value from Valuatum's DCF engine.",
    "Täsmäytysnäkökulma samaan ennusteeseen ja WACCiin; ei erillinen painotettava menetelmä.": "Reconciliation using the same forecast and WACC; not a separately weighted method.",
    "EVA rakentaa oman pääoman arvon eri suunnasta kuin DCF: yhtiöön jo sitoutunut pääoma (investoitu pääoma) plus tulevien EVA-erien nykyarvo (ennustejakso ja terminaali) on yritysarvo, josta korolliset velat ja kassa vievät oman pääoman arvoon. Alla olevat erät ovat laskentamoottorin tuottamia sellaisenaan.": "EVA approaches equity value from a different direction to DCF: invested capital plus the present value of future EVA (forecast and terminal periods) gives enterprise value, which is bridged to equity value through debt and cash. The items below come directly from the calculation engine.",
    "EVA-erittelyn komponentit eivät täsmää EVA-moottorin omaan arvoon tai jokin komponentti puuttuu lähdedatasta, joten täsmäytyssummaa ei esitetä. Raportin arvostus perustuu DCF-menetelmään; EVA on tässä raportissa DCF:n rinnakkaisnäkymä, ei itsenäinen menetelmäarvo.": "The EVA components do not reconcile to the EVA engine's own value, or a component is missing from source data, so no reconciliation total is shown. The report's valuation uses DCF; EVA provides a parallel view rather than an independent method value.",
    " Huom: arvonmäärityshetken sijoitettu pääoma ({0} tEUR), josta EVA-arvo lähtee, poikkeaa vuoden {1} pääomakustannuksen pohjasta ({2} tEUR).": " Note: invested capital at the valuation date ({0} tEUR), the starting point for EVA value, differs from the capital charge base for {1} ({2} tEUR).",
    "Tuottoarvo on 0, koska kolmen viime vuoden keskimääräinen nettotulos on negatiivinen.": "Earnings value is 0 because average net earnings over the last three years are negative.",
    "Substanssiarvo on 0, koska oma pääoma ilman pääomalainoja on negatiivinen ({0} tEUR) — verottajan mallissa pääomalainat luetaan velkaan, minkä vuoksi luku poikkeaa raportin tasearvosta, jossa pääomalainat sisältyvät omaan pääomaan.": "Net asset value is 0 because equity excluding capital loans is negative ({0} tEUR). The Finnish tax authority model treats capital loans as debt, unlike the report's balance sheet value that includes them in equity.",
    "Arvon ristiintarkistus: DCF/EVA vs. verottajan malli": "Valuation cross-check: DCF/EVA versus the Finnish tax authority model",
    "Päämenetelmä": "Primary method",
    "Täsmäytys samaan ennusteeseen ja WACCiin, ei erillinen paino": "Reconciliation to the same forecast and WACC, without a separate weight",
    "PV, diskontatut EVA:t (ennustejakso)": "PV of discounted EVA (forecast period)",
    "Jatkuvan arvon (terminaali-EVA) nykyarvo": "Present value of terminal EVA",
    "Oman pääoman arvo (DCF, käytetty raportissa)": "Equity value (DCF, used in this report)",
    "EVA-moottorin oma arvo ({0} tEUR) poikkeaa DCF:n oman pääoman arvosta ({1} tEUR). Ero kertoo mallien välisestä erosta lähdedatassa — se ei ole raportissa tasattu piiloon.": "The EVA engine's own value ({0} tEUR) differs from DCF equity value ({1} tEUR). This difference originates in the source models and has not been hidden by adjusting the report.",
    "Mitä EVA kertoo lisää": "What EVA adds",
    "EVA näyttää, tuottaako yhtiö sitoutuneelle pääomalleen enemmän kuin sen tuottovaatimus (ROIC vs WACC). Positiivinen EVA tarkoittaa, että yhtiö luo arvoa vuoden aikana, negatiivinen että se tuhoaa sitä. Tämä arvonluontinäkymä ei näy pelkästä kassavirtaluvusta.": "EVA shows whether returns on invested capital exceed the required return (ROIC versus WACC). Positive EVA indicates value creation during the year; negative EVA indicates value destruction. Cash flow alone does not show this perspective.",
    "- Pääomakustannus": "- Capital charge",
    "EVA-laskelma vuosittain": "Annual EVA calculation",
    "Menetelmä": "Method",
    "Arvo": "Value",
    "Verottajan mallissa (Verohallinnon arvostusohje) tuottoarvo on kolmen viime vuoden keskimääräinen nettotulos pääomitettuna 15 %:n tuottovaatimuksella ja substanssiarvo on yhtiön oma pääoma ilman pääomalainoja. Käypä arvo on näiden keskiarvo, kun tuottoarvo ylittää substanssiarvon, muutoin substanssiarvo. Malli on karkea julkinen vertailukohta (perintö- ja lahjaverotus), ei tämän raportin ensisijainen menetelmä: DCF/EVA on tarkempi, koska se käyttää yhtiön omia ennusteita ja WACCia yksittäisen keskituloksen sijaan.": "Under the Finnish Tax Administration's valuation guidance, earnings value is average net earnings over the last three years capitalized at 15%, and net asset value is equity excluding capital loans. The reference value is their average when earnings value exceeds net asset value; otherwise it is net asset value. This is a rough reference for Finnish inheritance and gift taxation, not the report's primary valuation method. DCF/EVA uses the company's own forecasts and WACC rather than a single average earnings figure.",
    "DCF ja EVA ovat sama arvo kahdella eri tavalla laskettuna. DCF diskonttaa yhtiön tuottaman vapaan kassavirran pääoman tuottovaatimuksella (WACC). EVA mittaa saman asian toisin: paljonko voittoa jää sen jälkeen, kun sitoutuneelle pääomalle on veloitettu sama tuottovaatimus. Koska molemmat nojaavat samaan ennusteeseen, samaan WACCiin ja samaan pääomapohjaan, ne antavat aina saman lopputuloksen — alla ne antavat saman oman pääoman arvon.": "DCF and EVA calculate the same value in two ways. DCF discounts free cash flow at the weighted average cost of capital (WACC). EVA measures profit after charging that same required return on invested capital. With the same forecast, WACC and capital base, both produce the same equity value below.",
    "Arvonmäärityksen päämenetelmä ja täsmäytys": "Primary valuation method and reconciliation",
    "DCF ja EVA perustuvat samaan ennustepolkuun, WACCiin ja velka-/kassasiltaan. Näillä samoilla oletuksilla EVA ei ole toinen riippumaton arvonmääritysmenetelmä, vaan DCF:n laskennallinen täsmäytysnäkökulma.": "DCF and EVA use the same forecast, WACC and debt/cash bridge. Under these assumptions EVA is a mathematical reconciliation to DCF rather than a second independent valuation method.",
    "Menetelmien ekvivalenssi": "Method equivalence",
    "Painotettu base case ei keskiarvoista DCF:ää ja EVA:a. Raportin ankkuriarvo on {0} tEUR, ja EVA:n tehtävä on osoittaa saman ennusteen sisäinen johdonmukaisuus.": "The weighted base case does not average DCF and EVA. The report's anchor value is {0} tEUR; EVA demonstrates the internal consistency of the same forecast.",
    "Investoitu pääoma": "Invested capital",
    "ei saatavilla lähdedatassa": "not available in source data",
    "Pääomapohjan muutoksen nykyarvo": "Present value of capital base change",
    "− Korolliset velat": "− Interest-bearing debt",
    "+ Kassa": "+ Cash",
    "EVA-moottorin oma arvo ennen normalisointia": "EVA engine value before normalization",
    "EVA-täsmäytys oman pääoman arvoon": "EVA reconciliation to equity value",
    "Erä": "Item",
    "Sijoitettu pääoma (pääomakustannuksen pohja)": "Invested capital (capital charge base)",
    "Raportin arvo (DCF/EVA)": "Report value (DCF/EVA)",
    "Tuottoarvo (3 v:n keskitulos / 15 %)": "Earnings value (3-year average earnings / 15%)",
    "Substanssiarvo (oma pääoma ilman pääomalainoja)": "Net asset value (equity excluding capital loans)",
    "Verottajan käypä arvo": "Finnish tax authority reference value",
    "Näkökulma": "Perspective",
    "Liiketoiminnan muut tuotot": "Other operating income",
    "Henkilöstökulut": "Personnel costs",
    "Liiketoiminnan muut kulut": "Other operating expenses",
    "Käyttökate (EBITDA)": "EBITDA",
    "Poistot ja arvonalentumiset": "Depreciation and impairment",
    "Liiketulos (EBIT)": "Operating profit (EBIT)",
    "Liiketulos ilman kertaeriä": "Operating profit excluding exceptional items",
    "Kertaerät liiketuloksessa": "Exceptional operating items",
    "Nettotulos": "Net earnings",
    "Kehittämismenot": "Development costs",
    "Aineettomat hyödykkeet": "Intangible assets",
    "Aineelliset hyödykkeet": "Tangible assets",
    "Taseen loppusumma": "Total assets",
    "Oma pääoma (ilman pääomalainoja)": "Equity (excluding capital loans)",
    "Oma pääoma (sis. pääomalainat)": "Equity (including capital loans)",
    "Pääomalainat": "Capital loans",
    "Lainat omistajayhteisöiltä": "Loans from associated companies",
    "Ostovelat": "Trade payables",
    "Korolliset velat": "Interest-bearing debt",
    "Korottomat velat": "Non-interest-bearing debt",
    "Ennuste on Valuatumin laskentamoottorin deterministinen perusura — ei tekoälyn eikä yhtiön johdon näkemys.": "The forecast is the deterministic base path from Valuatum's calculation engine, rather than an AI or company management forecast.",
    "Tämä on mallin oletus, ei yhtiökohtainen näkemys: sen voi korvata omalla ennusteella, jolloin oma viimeisen vuoden marginaali jatkuu terminaalissa.": "This is a model assumption rather than a company-specific view. It can be replaced with your own forecast, whose final-year margin then continues into the terminal period.",
    "Mistä ennuste tulee": "Forecast origin",
    "Tuloslaskelma": "Income statement",
    "Taseen päärivit": "Balance sheet key items",
    " (ennuste)": " (forecast)",
    "Liikevaihto- ja EBIT-ennuste on tilaajan antama: Valuatumin perusuraa on muokattu tilaajan omilla luvuilla, ja laskentamoottori on johtanut niistä muut rivit (kassavirta, tase, verot). Viimeisen ennustevuoden {0} EBIT-marginaali {1} jatkuu terminaalissa muuttumattomana, joten se painaa arvossa enemmän kuin yksikään muu yksittäinen vuosi.": "The revenue and EBIT forecast was provided by the customer: Valuatum's base path was edited with customer figures, and the calculation engine derived the other rows (cash flow, balance sheet and taxes). The final forecast year's ({0}) EBIT margin of {1} continues unchanged into the terminal period and therefore carries more weight in the valuation than any other individual year.",
    "Lähtökohta on viimeisin toteutunut tilikausi {0} (EBIT-% {1}).": "The starting point is the latest actual fiscal year {0} (EBIT margin {1}).",
    "Uran taustalla on mallin yleinen oletus, että pääoman tuotto lähestyy pääoman kustannusta (WACC {0}): ylituotto ei säily ikuisesti, koska kilpailu syö sen — eikä alituotto jää pysyväksi.": "The path reflects the model's general assumption that return on capital approaches its cost (WACC {0}): competition erodes excess returns, and underperformance does not persist indefinitely.",
    "Sijoitetun pääoman tuotto (NOPLAT / pääomakanta) on ennusteen alussa {0} ja lopussa {1}.": "Return on invested capital (NOPLAT / capital base) is {0} at the start of the forecast and {1} at the end.",
    "Ura päätyy pääoman kustannuksen alapuolelle, koska mallin pääomakanta kasvaa ennustejaksolla {0} → {1} tEUR kertyvistä voittovaroista eikä marginaaliuraa lasketa uudelleen kasvaneelle pääomalle.": "The path ends below the cost of capital because the model's capital base grows from retained earnings over the forecast period ({0} → {1} tEUR), while the margin path is not recalculated for the larger capital base.",
    ". Todellinen tuottovaatimus voisi olla korkeampi, jolloin oman pääoman arvo olisi konservatiivista perusskenaariota matalampi.": ". The actual required return could be higher, giving an equity value below the conservative base case.",
    " ovat maltilliset tähän riskitasoon nähden": " are modest relative to this risk level",
    ". Lisäksi tavoitepääomarakenne D/(D+E) {0} on ristiriidassa negatiivisen oman pääoman kanssa (kiertokulkuoletus)": ". The target capital structure D/(D+E) of {0} also conflicts with negative equity (steady-state assumption)",
    "- Maksetut verot": "- Taxes paid",
    "- Rahoituskulujen verovaikutus": "- Tax effect of financial expenses",
    "+ Rahoitustuottojen verovaikutus": "+ Tax effect of financial income",
    "- Käyttöpääoman muutos": "- Change in working capital",
    "Liiketoiminnan kassavirta": "Operating cash flow",
    "Operatiivinen vapaa kassavirta": "Free operating cash flow",
    "+/- Muut erät": "+/- Other items",
    "Vapaa kassavirta (FCFF)": "Free cash flow to firm (FCFF)",
    "PV, ennustejakso yhteensä": "PV of forecast period",
    "Osakkuusyhtiöt / muut erät": "Associated companies / other items",
    "Vähemmistöosuudet": "Minority interests",
    "oma pääoma on negatiivinen koko ennustejakson": "equity is negative throughout the forecast period",
    "vieraan pääoman kustannus {0}": "cost of debt {0}",
    "DCF-laskelma: FCFF ja nykyarvo vuosittain": "DCF calculation: annual FCFF and present value",
    "Arvon muodostuminen: yritysarvosta oman pääoman arvoon": "Value bridge: enterprise value to equity value",
    "Yritysarvosta oman pääoman arvoon": "Enterprise value to equity value",
    "Mistä diskontattu FCFF tulee?": "Where discounted FCFF comes from",
    "Taulukko seuraa Valuatumin DCF-rivejä: EBITistä edetään veroihin, käyttöpääoman muutokseen, investointeihin ja muihin eriin, joista muodostuu vapaa kassavirta (FCFF). Diskontattu FCFF on saman DCF-taulukon nykyarvorivi, ja ennustejakson diskontattujen FCFF:ien summa on {0} {1}. Tekoäly ei laske DCF:ää uudelleen, vaan esittää Valuatum-moottorin antamat rivit. Miinusmerkillä (−) alkavat rivit vähennetään kassavirrasta; tappiollisena vuonna rivi “− Maksetut verot” voi silti näyttää positiivista lukua, jolloin kyse on verohyödystä, joka kasvattaa vapaata kassavirtaa.": "The table follows Valuatum's DCF rows: EBIT, taxes, working capital changes, investments and other items produce free cash flow to firm (FCFF). Discounted FCFF is the present value row from the same DCF table; its forecast-period sum is {0} {1}. AI presents the engine's rows without recalculating DCF. Rows beginning with a minus sign (−) are deducted from cash flow. In a loss-making year, the “− Taxes paid” row may be positive, representing a tax benefit that increases free cash flow.",
    "Terminaaliarvon nykyarvo": "Present value of terminal value",
    "Oman pääoman arvo ennen lattiaa": "Equity value before the floor",
    "Diskonttokorko (WACC) tulee valuaatiomoottorista, eikä sitä ole tässä raportissa oikaistu yhtiön luottoriskillä. Huomioi jännite: ": "The discount rate (WACC) comes from the valuation engine and has not been adjusted for the company's credit risk in this report. Consider the tension: ",
    "DCF-erittelyä ei voitu koota": "DCF detail could not be assembled",
    "Henkilöstö": "Headcount",
    "Liiketulos / henkilö": "EBIT / employee",
    "Nettotulos / henkilö": "Net earnings / employee",
    "Henkilöstö (arvio henkilöstökuluista)": "Headcount (estimated from personnel costs)",
    "Liikevaihto / henkilö": "Revenue / employee",
    "Jalostusarvo / henkilö": "Value added / employee",
    "Henkilökulut / henkilö": "Personnel costs / employee",
    "Käyttökate / henkilö": "EBITDA / employee",
    "Henkilöstötehokkuus": "Employee efficiency",
    "Henkilöstö: lkm, muut rivit €/henkilö": "Headcount: persons; other rows: EUR/employee",
    "Henkilöstömäärä korjattu": "Headcount adjusted",
    "Lähdedatan ilmoittama henkilöstömäärä vuosilta {0} on epäuskottava suhteessa henkilöstökuluihin. Ilmoitettu määrä näytetään sellaisenaan; henkilöä kohden lasketut tunnusluvut näiltä vuosilta perustuvat henkilöstökuluista johdettuun arvioon (noin {1} tEUR/henkilö).": "The reported headcount for {0} is implausible relative to personnel costs. The reported figure is shown as supplied; per-employee ratios for these years use an estimate derived from personnel costs (approximately {1} tEUR/employee).",
    " tEUR": " tEUR",
    "Herkkyys: WACC x pitkän aikavälin kasvu (oman pääoman arvo, tEUR) — likimääräinen malli, kalibroitu konservatiivisen perusskenaarion lukuihin": "Sensitivity: WACC × long-term growth (equity value, tEUR) — approximate model calibrated to the conservative base case",
    "Herkkyys: liikevaihto x EBIT-% (oman pääoman arvo, tEUR) — likimääräinen malli, olettaa kassavirran skaalautuvan suoraan EBIT:n mukana": "Sensitivity: revenue × EBIT margin (equity value, tEUR) — approximate model assuming cash flow scales directly with EBIT",
    "Oman pääoman arvo vaihtoehtoisella terminaali-EBIT-marginaalilla": "Equity value at an alternative terminal EBIT margin",
    "Konservatiivinen perusskenaario olettaa terminaalivuoden EBIT-marginaaliksi {0}. Jos terminaalimarginaali jää yhtiön parhaaseen toteutuneeseen tasoon {1}, DCF:n oman pääoman arvo on likimäärin {2} — arvon herkkyys terminaalioletukselle on siis suuri. Laskelma on likimääräinen: se skaalaa vain terminaaliarvon marginaalin suhteessa eikä muuta ennustejakson kassavirtoja.": "The conservative base case assumes a terminal EBIT margin of {0}. If it remains at the company's best actual margin of {1}, approximate DCF equity value is {2}, illustrating substantial sensitivity to the terminal assumption. This approximation scales only terminal value in proportion to the margin and leaves forecast-period cash flows unchanged.",
    "{0} (konservatiivinen perusskenaario)": "{0} (conservative base case)",
    "Menetelmävalinnan pisteytys": "Method selection scoring",
    "<div class=\"scen scen-{0}\"><div class=\"scen-h\"><span class=\"scen-name\">{1}</span><span class=\"scen-fig\"><span class=\"scen-val {2}\">{3}</span><span class=\"scen-p\">p = {4} %</span></span></div><div class=\"drivers-strip\"><div class=\"drivers-lab\">Ajurit — näitä muuttamalla arvo muuttuu</div><div class=\"drivers-row\">{5}</div></div><div class=\"scen-tables\">{6}{7}</div></div>": "<div class=\"scen scen-{0}\"><div class=\"scen-h\"><span class=\"scen-name\">{1}</span><span class=\"scen-fig\"><span class=\"scen-val {2}\">{3}</span><span class=\"scen-p\">p = {4} %</span></span></div><div class=\"drivers-strip\"><div class=\"drivers-lab\">Drivers — changing these changes the value</div><div class=\"drivers-row\">{5}</div></div><div class=\"scen-tables\">{6}{7}</div></div>",
    "<div class=\"band\"><div class=\"brand\">{0} · Yritysanalyysi · AI</div><div class=\"kicker\">Arvonmääritysraportti</div></div>": "<div class=\"band\"><div class=\"brand\">{0} · Company analysis · AI</div><div class=\"kicker\">Valuation report</div></div>",
    "<div style=\"margin:0 0 7mm\"><div class=\"lab\">Yhtiö</div><div class=\"co\" style=\"font-size:24pt;line-height:1.15;margin:1mm 0\">{0}</div><div class=\"sub\">{1}</div></div><div class=\"meta\" style=\"grid-template-columns:repeat(2,1fr)\"><div><div class=\"lab\">Y-tunnus</div><div class=\"val\">{2}</div></div><div><div class=\"lab\">Raportin päivä</div><div class=\"val\">{3}</div></div></div>": "<div style=\"margin:0 0 7mm\"><div class=\"lab\">Company</div><div class=\"co\" style=\"font-size:24pt;line-height:1.15;margin:1mm 0\">{0}</div><div class=\"sub\">{1}</div></div><div class=\"meta\" style=\"grid-template-columns:repeat(2,1fr)\"><div><div class=\"lab\">Business ID</div><div class=\"val\">{2}</div></div><div><div class=\"lab\">Report date</div><div class=\"val\">{3}</div></div></div>",
    "<div class=\"footnote\"><p><b>Voit muuttaa oletuksia.</b> Skenaarioiden todennäköisyydet ja ennusteparametrit ovat muokattavissa Valuatumin järjestelmässä — muutokset päivittävät arvion, ja raportin voi tuottaa uudelleen omilla odotuksilla.</p></div>": "<div class=\"footnote\"><p><b>You can edit the assumptions.</b> Scenario probabilities and forecast parameters can be edited in Valuatum. Changes update the valuation, and the report can be generated again using your assumptions.</p></div>",
    "<a class=\"toc-row\" href=\"#sec-glossary\"><span class=\"tn\">·</span><span class=\"tt\">SANASTO</span></a>": "<a class=\"toc-row\" href=\"#sec-glossary\"><span class=\"tn\">·</span><span class=\"tt\">GLOSSARY</span></a>",
    "<section class=\"page\">{0}<div class=\"pbody\">{1}<div class=\"sec-head\"><span class=\"sec-num\" style=\"background:var(--green);color:#fff\">·</span><div class=\"sh-t\"><h2>Sisällys</h2><div class=\"sh-sub\">AI-Arvonmääritysraportti</div></div></div><div class=\"sec-rule\"></div><div class=\"toc\">{2}</div></div>{3}</section>": "<section class=\"page\">{0}<div class=\"pbody\">{1}<div class=\"sec-head\"><span class=\"sec-num\" style=\"background:var(--green);color:#fff\">·</span><div class=\"sh-t\"><h2>Contents</h2><div class=\"sh-sub\">AI Valuation Report</div></div></div><div class=\"sec-rule\"></div><div class=\"toc\">{2}</div></div>{3}</section>",
    "<section class=\"page\" id=\"sec-glossary\">{0}<div class=\"pbody\"><div class=\"sec-head\"><span class=\"sec-num\" style=\"background:var(--green);color:#fff\">·</span><div class=\"sh-t\"><h2>Sanasto</h2><div class=\"sh-sub\">Raportissa käytetyt keskeiset termit arkikielellä</div></div></div><div class=\"sec-rule\"></div><div class=\"two-col\" style=\"row-gap:10px\">{1}</div></div>{2}</section>": "<section class=\"page\" id=\"sec-glossary\">{0}<div class=\"pbody\"><div class=\"sec-head\"><span class=\"sec-num\" style=\"background:var(--green);color:#fff\">·</span><div class=\"sh-t\"><h2>Glossary</h2><div class=\"sh-sub\">Key report terms in plain language</div></div></div><div class=\"sec-rule\"></div><div class=\"two-col\" style=\"row-gap:10px\">{1}</div></div>{2}</section>",
    "<div class=\"chart-host\" style=\"break-inside:avoid;margin:4mm 0 5mm\"><div style=\"display:flex;justify-content:space-between;align-items:baseline;border-bottom:1.5px solid {0};padding-bottom:1.5mm\"><span style=\"{1};font-size:11pt\">Arvon muodostuminen EVA-menetelmällä</span><span style=\"{2};letter-spacing:.04em\">{3}</span></div><p style=\"{4};font-size:8pt;margin:2.5mm 0 1mm;max-width:62%\">{5}</p>{6}<div style=\"display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid {7};padding-top:3mm;margin-top:2mm\"><span style=\"{8};font-size:9.5pt\">Diskontattu EVA vuosittain</span><span style=\"{9}\">{10}</span></div>{11}<p style=\"{12};font-size:7pt;margin-top:3mm\">{13}</p></div>": "<div class=\"chart-host\" style=\"break-inside:avoid;margin:4mm 0 5mm\"><div style=\"display:flex;justify-content:space-between;align-items:baseline;border-bottom:1.5px solid {0};padding-bottom:1.5mm\"><span style=\"{1};font-size:11pt\">Value derivation using EVA</span><span style=\"{2};letter-spacing:.04em\">{3}</span></div><p style=\"{4};font-size:8pt;margin:2.5mm 0 1mm;max-width:62%\">{5}</p>{6}<div style=\"display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid {7};padding-top:3mm;margin-top:2mm\"><span style=\"{8};font-size:9.5pt\">Annual discounted EVA</span><span style=\"{9}\">{10}</span></div>{11}<p style=\"{12};font-size:7pt;margin-top:3mm\">{13}</p></div>",
    "<div><div class=\"section-lab\" style=\"margin-bottom:4mm\">Miten arvo muodostuu — laskennan periaate</div>{0}<p class=\"muted\" style=\"font-size:7.5pt;margin-top:4mm\">Kaavio kuvaa pääperiaatteen ja tämän yhtiön luvut. Käytetyt menetelmät, niiden painot ja perustelut esitetään seuraavissa osioissa; termit selitetään sanasto-liitteessä.</p></div>": "<div><div class=\"section-lab\" style=\"margin-bottom:4mm\">How value is derived — calculation principle</div>{0}<p class=\"muted\" style=\"font-size:7.5pt;margin-top:4mm\">The diagram shows the calculation principle and this company’s figures. The following sections explain the methods, weights and rationale. Terms are explained in the glossary.</p></div>",
    "<section class=\"page appendix-divider\">{0}<div class=\"pbody\" style=\"display:flex;align-items:center;justify-content:center;flex:1 1 auto\"><div style=\"max-width:110mm;text-align:center\"><div style=\"font-size:8pt;text-transform:uppercase;letter-spacing:.14em;color:var(--lime-deep);font-weight:700;margin-bottom:8px\">Liite</div><h2 style=\"font-size:22pt;margin-bottom:10px\">Liitteet</h2><p class=\"muted\">Ennusteen täydet vuositason luvut, lähderekisteri ja metodologiakuvaus. Tukiaineistoa raportin pääsisällölle, ei sen osa.</p></div></div>{1}</section>": "<section class=\"page appendix-divider\">{0}<div class=\"pbody\" style=\"display:flex;align-items:center;justify-content:center;flex:1 1 auto\"><div style=\"max-width:110mm;text-align:center\"><div style=\"font-size:8pt;text-transform:uppercase;letter-spacing:.14em;color:var(--lime-deep);font-weight:700;margin-bottom:8px\">Appendix</div><h2 style=\"font-size:22pt;margin-bottom:10px\">Appendices</h2><p class=\"muted\">Full annual forecast figures, source register and methodology. Supporting material for the main report.</p></div></div>{1}</section>",
    "<p class=\"hero-desc\" style=\"margin-top:2mm\">Konservatiivinen perusskenaario <b>{0}</b>.</p>": "<p class=\"hero-desc\" style=\"margin-top:2mm\">Conservative base case <b>{0}</b>.</p>",
    "<div style=\"margin-top:9mm\"><div class=\"section-lab\">Arvion haarukka skenaarioittain</div><div class=\"chart\">{0}</div><div class=\"chart-labels\">{1}</div></div>": "<div style=\"margin-top:9mm\"><div class=\"section-lab\">Valuation range by scenario</div><div class=\"chart\">{0}</div><div class=\"chart-labels\">{1}</div></div>",
    "<div><h4 class=\"blk\">Menetelmäpainot</h4><div style=\"max-width:150px\">{0}</div></div>": "<div><h4 class=\"blk\">Method weights</h4><div style=\"max-width:150px\">{0}</div></div>",
    "<div><h4 class=\"blk\">Menetelmien arvot</h4>{0}</div>": "<div><h4 class=\"blk\">Method values</h4>{0}</div>",
    "<p class=\"muted\" style=\"font-style:italic\">Tietoa ei ollut saatavilla tähän osioon.</p>": "<p class=\"muted\" style=\"font-style:italic\">No information was available for this section.</p>",
    "<text x=\"{0}\" y=\"{1}\" font-size=\"7.5\" fill=\"{2}\">ennuste alkaa</text>": "<text x=\"{0}\" y=\"{1}\" font-size=\"7.5\" fill=\"{2}\">forecast begins</text>",
    "<p class=\"hero-desc\" style=\"margin-top:2mm\">Odotusarvo muodostuu konservatiivisesta perusskenaariosta <b>{0}</b>: perusskenaario {1} {2} {3} pessimistisen skenaarion vaikutus {4} {5} {6} optimistisen skenaarion vaikutus.</p>": "<p class=\"hero-desc\" style=\"margin-top:2mm\">Expected value starts from the conservative base case <b>{0}</b>: base case {1} {2} {3} pessimistic scenario impact {4} {5} {6} optimistic scenario impact.</p>",
    "<text x=\"{0}\" y=\"{1}\" font-size=\"8.5\" fill=\"{2}\">ennuste</text>": "<text x=\"{0}\" y=\"{1}\" font-size=\"8.5\" fill=\"{2}\">forecast</text>",
    "<span style=\"color:#1B6B3F;font-weight:600\">Skenaarioiden odotusarvo</span>": "<span style=\"color:#1B6B3F;font-weight:600\">Scenario expected value</span>",
    "Tämä raportti on tuotettu automaattisesti yleiseen tietoon perustuvana analyysinä. Se ei ole sijoitusneuvontaa, tilintarkastusta, käyvän arvon lausunto (fairness opinion) eikä sellaisenaan sovellu vero- tai oikeusriitojen perusteeksi ilman asiantuntijan erillistä arviota. {0} ei vastaa raportin perusteella tehdyistä päätöksistä.": "This report was produced automatically as an analysis based on general information. It is not investment advice, an audit or a fairness opinion, and is not suitable as the sole basis for tax or legal disputes without a separate expert assessment. {0} is not liable for decisions made on the basis of this report.",
    "Kohtalainen": "Moderate",
    "Markkinoitavuus": "Marketability",
    "Jatkuvan toiminnan oletus": "Going concern assumption",
    "Y-tunnus": "Business ID",
    "Toimiala": "Industry",
    "Kotipaikka": "Domicile",
    "Lukujen taso": "Reporting level",
    "Perustiedot": "Company information",
    "Toimeksianto": "Engagement",
    "Myyntikate": "Gross profit",
    "Korkokulut": "Interest expenses",
    "Vaihto-omaisuus": "Inventories",
    "Myyntisaamiset": "Trade receivables",
    "Rahat ja pankkisaamiset": "Cash and equivalents",
    "Lainat rahoituslaitoksilta": "Loans from financial institutions",
    "Saadut ennakot": "Advances received",
    "Rooli": "Role",
    "Diskontattu EVA": "Discounted EVA",
    "Kumulatiivinen diskontattu EVA": "Cumulative discounted EVA",
    "EVA-erittely": "EVA breakdown",
    "EVA- ja DCF-mallin ero": "Difference between EVA and DCF models",
    "+ Poistot": "+ Depreciation",
    "+ Korottomien rahoitusvelkojen muutos": "+ Change in non-interest-bearing financial liabilities",
    "- Bruttoinvestoinnit": "- Gross capital expenditure",
    "Diskontattu FCFF": "Discounted FCFF",
    "Kumulatiivinen diskontattu FCFF": "Cumulative discounted FCFF",
    "Edellisen vuoden osingot": "Previous year dividends",
    "luottoluokitus {0}": "credit rating {0}",
    "konkurssiriski {0}": "bankruptcy risk {0}",
    "likviditeettipreemio {0}": "liquidity premium {0}",
    "kohonnut luottoriski": "elevated credit risk",
    ", mutta ": ", while ",
    "diskonttokoron komponentit": "discount rate components",
    "Diskonttokoron ja luottoriskin suhde": "Discount rate and credit risk",
    "Terminaali-EVA": "Terminal EVA",
    "Terminaali-EBIT-%": "Terminal EBIT margin",
    "{0} (paras toteutunut)": "{0} (best actual)",
    "laskee": "decreases",
    "nousee": "increases",
    " tasaisesti": " steadily",
    "EBIT-marginaali {0} {1} ({2}) → {3} ({4}), ja viimeisen vuoden taso jatkuu terminaalissa muuttumattomana.": "EBIT margin {0} from {1} ({2}) to {3} ({4}); the final-year level continues unchanged in the terminal period.",
    "Perusluvut": "Base figures",
    "Avainluvut": "Key figures",
    "asiakkaan ilmoittamat signaalit": "customer-reported signals",
    "markkinasignaalit": "market signals",
    "tunnusluvut": "key ratios",
    "luottoriskitiedot": "credit risk data",
    "Jatkuvan arvon arvo toteutumisvuonna ": "Continuing value in realization year ",
    "− Ennustettu nettovelka vuonna ": "− Forecast net debt in year ",
    "= Oman pääoman arvo vuonna ": "= Equity value in year ",
    "Diskonttaus nykyhetkeen (WACC {0} %, {1} v, kerroin {2})": "Discount to present (WACC {0}%, {1} years, factor {2})",
    "− Laimennusvaikutus {0} %": "− Dilution effect {0}%",
    "Omistaja-arvon lattia": "Equity value floor",
    "Optimistinen omistaja-arvo": "Optimistic equity value",
    "{0} % × {1} tEUR": "{0}% × {1} tEUR",
    " = {0} tEUR": " = {0} tEUR",
    "Optimistisen skenaarion arvon johto (deterministinen)": "Optimistic scenario value derivation (deterministic)",
    "Optimistisen skenaarion arvo on laskettu deterministisesti yllä olevista oletuksista: oman pääoman arvo muodostetaan toteutumisvuonna (jatkuva arvo miinus saman vuoden ennustettu nettovelka) ja diskontataan kokonaisuutena nykyhetkeen. Välivuosien rahoitustarvetta ei vähennetä erikseen, koska toteutumisvuoden nettovelka sisältää jo sen rahoituksen. Oletukset ovat AI:n muodostamia ja käyttäjän muokattavissa.": "The optimistic scenario value is calculated deterministically from the assumptions above: equity value in the realization year (continuing value less forecast net debt in that same year) is discounted as a whole to the present. Interim financing needs are not deducted separately because realization-year net debt already includes their financing. Assumptions are produced by AI and can be edited by the user.",
    "Tunnusluku": "Metric",
    "Pessimistinen": "Pessimistic",
    "Konservatiivinen": "Conservative",
    "Optimistinen": "Optimistic",
    "Arvo (tEUR)": "Value (tEUR)",
    "Todennäköisyys (%)": "Probability (%)",
    "Skenaariovertailu": "Scenario comparison",
    "Valuaatiomoottorin dcf-lohkosta puuttuu vuosi-, FCFF- tai diskontattu FCFF -sarja, joten vuosittaista FCFF-erittelyä ei voitu rakentaa. Tarkista vaiheen 0 datan valuation_engine.dcf-kentät.": "The calculation engine is missing the year, FCFF or discounted FCFF series required to construct an annual cash flow breakdown."
}
