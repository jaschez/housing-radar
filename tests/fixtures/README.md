# Fixtures

Real responses captured on 2026-10-10. They are the only test inputs the code did not
write itself, which is why the detector and extractor tests rely on them.

| File | What | Changes |
|---|---|---|
| `datadome-idealista.html` | DataDome interstitial (403) returned to a datacenter address | session values redacted |
| `imperva-milanuncios.html` | Imperva "Pardon our interruption" page (403) | long tokens redacted |
| `waf-cloudfront-403.html` | CloudFront WAF refusal | reconstructed from the observed text; see the comment inside |
| `sana-pisos-com.html` | pisos.com results page, healthy | trimmed to title, ld+json, result cards and pagination |
| `sana-fotocasa-listado.html` | Fotocasa results page, healthy | only the `__initial_props__` JSON; phones, client ids and extra photos removed |

Wall fixtures keep the *shape* of vendor signals (cookie names, reference formats) and
lose their values: redacting a signal away would make the test pass for the wrong reason.
