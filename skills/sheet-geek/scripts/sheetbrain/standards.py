"""Well-known standard code sets: countries, US states and Canadian provinces,
currencies and apparel sizes. A column written in one of them explains itself
(FL, CAD, XL) and is never a code to ask the owner about; a currency column is
a question about the money's unit instead.

A column is in a set when 90% or more of its rows hold the set's codes and
either its header names what the set is ('Ship State', 'Currency') or its codes
are unlikely to all land in the set by chance: the chance a random code of that
width is in the set, multiplied over the distinct codes, is at most 1%. Two
letter country codes fill about a third of all two letter codes, so three of
them are not evidence; five of them are. Under a header that says status they
never are.
"""
from __future__ import annotations

import re

COUNTRIES_2 = set("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO
JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR
MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO
RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV
TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW UK
""".split())

COUNTRIES_3 = set("""
ABW AFG AGO AIA ALA ALB AND ARE ARG ARM ASM ATA ATF ATG AUS AUT AZE BDI BEL BEN BES BFA BGD BGR BHR BHS BIH BLM
BLR BLZ BMU BOL BRA BRB BRN BTN BVT BWA CAF CAN CCK CHE CHL CHN CIV CMR COD COG COK COL COM CPV CRI CUB CUW CXR
CYM CYP CZE DEU DJI DMA DNK DOM DZA ECU EGY ERI ESH ESP EST ETH FIN FJI FLK FRA FRO FSM GAB GBR GEO GGY GHA GIB
GIN GLP GMB GNB GNQ GRC GRD GRL GTM GUF GUM GUY HKG HMD HND HRV HTI HUN IDN IMN IND IOT IRL IRN IRQ ISL ISR ITA
JAM JEY JOR JPN KAZ KEN KGZ KHM KIR KNA KOR KWT LAO LBN LBR LBY LCA LIE LKA LSO LTU LUX LVA MAC MAF MAR MCO MDA
MDG MDV MEX MHL MKD MLI MLT MMR MNE MNG MNP MOZ MRT MSR MTQ MUS MWI MYS MYT NAM NCL NER NFK NGA NIC NIU NLD NOR
NPL NRU NZL OMN PAK PAN PCN PER PHL PLW PNG POL PRI PRK PRT PRY PSE PYF QAT REU ROU RUS RWA SAU SDN SEN SGP SGS
SHN SJM SLB SLE SLV SMR SOM SPM SRB SSD STP SUR SVK SVN SWE SWZ SXM SYC SYR TCA TCD TGO THA TJK TKL TKM TLS TON
TTO TUN TUR TUV TWN TZA UGA UKR UMI URY USA UZB VAT VCT VEN VGB VIR VNM VUT WLF WSM YEM ZAF ZMB ZWE
""".split())

# the US states, the District of Columbia and the territories, then the Canadian provinces and territories
STATES = set("""
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA
RI SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI AS MP
AB BC MB NB NL NS NT NU ON PE QC SK YT
""".split())

CURRENCIES = set("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD CAD CDF CHF
CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG
HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA
MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD
RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH
UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL
""".split())

SIZES = {"XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL", "4XL"}

# (name, codes, what a header that names the set says)
SETS = [
    ("country", COUNTRIES_2, re.compile(r"\b(country|countries|nation|ctry|cntry)\b", re.I)),
    ("country", COUNTRIES_3, re.compile(r"\b(country|countries|nation|ctry|cntry)\b", re.I)),
    ("state", STATES, re.compile(r"\b(state|states|province|provinces|prov)\b", re.I)),
    ("currency", CURRENCIES, re.compile(r"\b(currency|currencies|ccy|cur|curr)\b", re.I)),
    ("size", SIZES, re.compile(r"\b(sizes?|sz)\b", re.I)),
]
IN_SET = 0.90       # share of a column's rows that must hold the set's codes
CHANCE = 0.01       # without a header that names the set: how likely its codes all land in it by chance
# a header that says the column is a status ('Status', 'Stage', 'Flag') outweighs chance: PA, CA and OK
# there are codes to explain, whatever else they spell
_STATUS_WORD = re.compile(r"\b(status|stage|flag)\b", re.I)


def _density(codes: set) -> dict:
    """{width: the share of all codes of that width (capital letters and digits) that are in the set}."""
    out: dict = {}
    for c in codes:
        out[len(c)] = out.get(len(c), 0) + 1
    return {w: n / (26 ** w) for w, n in out.items()}


_DENSITY = [_density(codes) for _name, codes, _hint in SETS]


def standard_set(header: str, counter) -> str | None:
    """The standard set a column's values are written in ('country', 'state',
    'currency' or 'size'), or None. counter: {value: rows}."""
    total = sum(counter.values())
    if not total:
        return None
    vals = {str(k).strip().upper(): n for k, n in counter.items() if k is not None}
    for (name, codes, hint), dens in zip(SETS, _DENSITY):
        hit = {v: n for v, n in vals.items() if v in codes}
        if sum(hit.values()) < IN_SET * total:
            continue
        if hint.search(str(header or "")):
            return name
        if _STATUS_WORD.search(str(header or "")):
            continue
        chance = 1.0
        for v in hit:
            chance *= dens.get(len(v), 1.0)
        if chance <= CHANCE:
            return name
    return None
