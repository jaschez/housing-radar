"""Signal tables: data, not logic.

Three classes of signal, and the difference between them is the whole design:

  STRONG   Matches the <title> or unambiguous instruction text. A page only says
           "Just a moment..." when it IS the wall. Fires on its own.
  HEADERS  Headers a bot manager sets only when it mitigates. `cf-mitigated` is
           definitive; `set-cookie: _abck=` is not, because Akamai also sets it on healthy
           pages. Hence the `definitive` field.
  WEAK     Vendor scripts, resource paths and cookie names. They appear on EVERY page of a
           protected site, healthy ones included, so they only count when the page also
           has no content.

Deliberately absent: response-size thresholds. Classifying by bytes fingerprints one
site's configuration and means nothing on another.
"""
import re

I = re.IGNORECASE

# (vendor, signal, regex). Fires on its own.
STRONG = [
    ("cloudflare", "cf-just-a-moment", re.compile(r"<title>\s*just a moment[.\s]*</title>", I)),
    ("cloudflare", "cf-attention-required", re.compile(r"<title>\s*attention required![^<]*</title>", I)),
    ("cloudflare", "cf-enable-js-and-cookies", re.compile(r"enable javascript and cookies to continue", I)),
    ("cloudflare", "cf-checking-browser", re.compile(r"checking your browser before accessing", I)),
    ("radware", "radware-title", re.compile(r"<title>[^<]*radware bot manager[^<]*</title>", I)),
    ("radware", "solve-captcha-instruction", re.compile(r"solve this captcha to proceed", I)),
    ("imperva", "incap-title", re.compile(r"<title>[^<]*(incapsula|imperva)[^<]*</title>", I)),
    ("imperva", "incap-incident-id", re.compile(r"request unsuccessful\.\s*incapsula incident id", I)),
    ("imperva", "imperva-pardon", re.compile(r"<title>[^<]*pardon our interruption[^<]*</title>", I)),
    ("perimeterx", "px-denied-title", re.compile(r"<title>[^<]*access to this page has been denied[^<]*</title>", I)),
    # Akamai's title is too generic; the shape of its reference number is not.
    ("akamai", "akamai-reference", re.compile(r"reference\s*#\d+\.[0-9a-f]+\.\d+\.[0-9a-f]+", I)),
    ("datadome", "datadome-blocked-title", re.compile(r"<title>[^<]*you have been blocked[^<]*</title>", I)),
    # Vendor-less interstitial titles. Anchored to <title> on purpose: on their own,
    # "security check" matches ordinary prose on a banking page.
    ("unknown", "generic-verifying-human",
     re.compile(r"<title>[^<]*(verifying you are human|are you a human|are you human)[^<]*</title>", I)),
    ("unknown", "generic-one-more-step", re.compile(r"<title>[^<]*one more step[^<]*</title>", I)),
    ("unknown", "generic-security-check", re.compile(r"<title>[^<]*security check[^<]*</title>", I)),
]

# (vendor, signal, regex, definitive). Matched against lowercased "name: value" lines.
HEADERS = [
    ("cloudflare", "cf-mitigated", re.compile(r"^cf-mitigated:\s*challenge"), True),
    ("kasada", "kpsdk-header", re.compile(r"^x-kpsdk-(st|ct|cd):"), False),
    ("imperva", "incap-x-iinfo", re.compile(r"^x-iinfo:"), False),
    ("imperva", "incap-x-cdn", re.compile(r"^x-cdn:\s*incapsula"), False),
    ("datadome", "datadome-header", re.compile(r"^x-datadome(-cid)?:"), False),
    ("datadome", "datadome-cookie", re.compile(r"^set-cookie:\s*datadome="), False),
    ("akamai", "akamai-abck-cookie", re.compile(r"^set-cookie:\s*(_abck|bm_sz)="), False),
    ("perimeterx", "px-cookie", re.compile(r"^set-cookie:\s*_px(hd|vid|_)?="), False),
    ("radware", "radware-cookie", re.compile(r"^set-cookie:\s*rdwr_"), False),
]

# Only count on a page without content.
WEAK = [
    ("cloudflare", "cf-challenge-platform",
     re.compile(r"(cdn-cgi/challenge-platform|cf-browser-verification|__cf_chl)", I)),
    ("hcaptcha", "hcaptcha-widget",
     re.compile(r"class=[\"'][^\"']*\bh-captcha\b[^\"']*[\"']|hcaptcha\.com/1/api\.js", I)),
    ("recaptcha", "recaptcha-widget",
     re.compile(r"class=[\"'][^\"']*\bg-recaptcha\b[^\"']*[\"']|google\.com/recaptcha/api\.js", I)),
    ("datadome", "datadome-captcha", re.compile(r"geo\.captcha-delivery\.com|js\.datadome\.co", I)),
    # The DataDome interstitial measured on 2026-10-10 carries neither of the above: it
    # inlines its configuration as `var dd={'rt':'i',...}`.
    ("datadome", "datadome-dd-var", re.compile(r"var\s+dd\s*=\s*\{", I)),
    ("imperva", "incap-resource", re.compile(r"_Incapsula_Resource\?", I)),
    ("imperva", "incap-cookie", re.compile(r"\b(visid_incap_|incap_ses_)\d*", I)),
    ("perimeterx", "px-access-denied", re.compile(r"access to this page has been denied", I)),
    ("perimeterx", "px-cdn", re.compile(r"captcha\.px-cdn\.net|client\.perimeterx\.net", I)),
    ("akamai", "akamai-sensor", re.compile(r"/akam/\d+/|_abck|bm_sz", I)),
    ("kasada", "kasada-sdk", re.compile(r"kpsdk|149e9513-01fa-4fb0-aad4-566afd725d1b", I)),
]

# Image captcha: labelled differently because the response would be different. This
# project does not solve captchas; the label only keeps the log honest.
CAPTCHA_IMAGE = [
    ("radware", "radware-captcha-img", re.compile(r"<img[^>]+(id|class)=[\"'][^\"']*captcha[^\"']*[\"']", I)),
    ("unknown", "captcha-img-src", re.compile(r"<img[^>]+src=[\"'][^\"']*captcha[^\"']*[\"']", I)),
    ("unknown", "captcha-input", re.compile(r"<input[^>]+name=[\"'][^\"']*captcha[^\"']*[\"']", I)),
]

# Statuses a bot manager falls back on. Never enough on their own.
BLOCK_CODES = frozenset({401, 403, 405, 406, 409, 418, 429, 503})
