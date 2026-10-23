# Feasibility report

> **Update (2026-10):** the SECO findings below are wrong. SECO publishes the
> same 2 Garantex EVM addresses as EU, in each subject's `other-information`
> (a sibling of `<identity>`, which the original adapter didn't scan), and
> the list has 8,667 targets, not 24,483 records. The adapter has since been
> fixed; see `src/sanctions_address_lists/sources/seco.py`.

Findings are based on directly inspecting each source's live machine-readable
data during development (not documentation alone), except where noted.

## Which sources work

| Source | Adapter works? | Live-verified? |
|---|---|---|
| US OFAC | Yes | Yes — fetched and parsed the live `SDN.XML` (19,202 records) |
| EU | Yes | Yes — fetched and parsed the live consolidated XML (25MB, 6,234 records) |
| UK | Yes | Yes — fetched and parsed the live `UK-Sanctions-List.xml` (6,334 designations) |
| UN | Yes | Yes — fetched and parsed the live consolidated list via the redirect endpoint (1,011 records) |
| Switzerland SECO | Yes | Yes — fetched and parsed the live consolidated XML (24,483 `identity` records); see the connectivity history below |

## Which sources contain EVM addresses (live-verified counts)

| Source | Dedicated crypto field | Free-text fallback field(s) scanned | Live address count found |
|---|---|---|---|
| US OFAC | Yes (`idType` = "Digital Currency Address - *") | none (not needed) | **104** — real `0x…` addresses tagged ETH, ARB, BSC, ETC in live data |
| UK | Yes (`CryptoWalletAddresses/CryptoWalletAddress`), structurally present but currently empty | `OtherInformation`, `UKStatementofReasons` | **6** — all found via the free-text fallback, none via the structured field |
| EU | No | every `<remark>` descendant within a record | **2** — the Garantex exchange designation, ETH addresses stated in its remark |
| UN | No | `COMMENTS*` | 0 — no crypto content found anywhere in the live dataset |
| Switzerland SECO | No (per XSD) | `remark`, `other-information` | **0** — confirmed against live data (24,483 records, no crypto content found) |

An important correctness fix surfaced during live verification: EU's
`<remark>` elements are not always direct children of `sanctionEntity` — the
Garantex designation's remark is nested one level deeper, under a
`nameAlias`. The adapter originally only checked direct children and missed
it (0 live addresses found); it now scans every `<remark>` descendant within
a record's subtree, which correctly finds both real addresses. This is kept
here as a reminder that "no dedicated field" sources need their free-text
scan scope verified against live data, not just against the documented
schema shape.

Only OFAC has substantial current coverage. UK's fallback scan already found
real, newly-published addresses that the (currently empty) structured field
would have missed entirely — validating the "scan official free-text fields,
not just typed fields" design directive. EU's coverage depends entirely on
regex-scanning prose, which is inherently less stable than a typed field.
SECO currently has zero addresses, matching its schema having no crypto
field at all.

## Switzerland SECO connectivity history

SECO is fully supported (see the tables above) — this section is kept as a
resolved incident record, since the symptom could recur and shouldn't be
mistaken for a code regression if it does:

- The adapter's URL (`www.sesam.search.admin.ch/sesam-search-web/pages/downloadXmlGesamtliste.xhtml`)
  is confirmed correct — it matches the live link on SECO's own search page,
  and [OpenSanctions' `ch_seco_sanctions` dataset](https://www.opensanctions.org/datasets/ch_seco_sanctions/)
  fetches this exact endpoint on a 2-hour cadence. The endpoint was never
  dead, moved, or misnamed.
- Early fetch attempts — from this project's dev sandbox, and separately from
  a maintainer's own machine — timed out at the **TCP connection** stage: no
  SYN-ACK, no HTTP response, no TLS handshake, just silence. A control
  request to a different `admin.ch` host (`www.seco.admin.ch`, used for the
  XSD schema) connected immediately from the same sandbox, ruling out a
  blanket network or DNS problem.
- **Root cause, confirmed**: the maintainer was connecting through a VPN.
  Disabling the VPN immediately resolved it — a live run then succeeded,
  fetching the real `consolidated-list_2026-08-18.xml` (HTTP 200) and
  correctly parsing all 24,483 `identity` records (0 addresses, as expected
  from the XSD's schema). This confirms both the endpoint and this adapter's
  XSD-derived parsing logic (`identity`/`remark`/`other-information` element
  names) are correct against real data.
- This is consistent with SECO's infrastructure blocking some VPN/proxy exit
  IP ranges rather than a general anti-automation or cloud/datacenter block —
  worth keeping in mind if this source fails again from a CI runner or a
  network that happens to route through a VPN/proxy.

`SecoAdapter.GATES_BUILD` is now `True` and it publishes output files like
every other source; the earlier `"unverified"`-forcing override in its
`run()` has been removed since the code is now confirmed correct against
live data.

## Upstream-format and maintenance risks

- **OFAC** changed its XML namespace in 2024; the adapter matches elements by
  local name (ignoring namespace) specifically to tolerate this. Currency
  suffixes on `idType` (`ETH`, `ARB`, `BSC`, `ETC`, `USDT`, …) have grown over
  time and will likely keep growing — the adapter filters by address
  *format*, not by an enumerated currency list, so new EVM-compatible
  suffixes are picked up automatically without a code change.
- **EU / UN / SECO** free-text scanning is inherently more fragile than a
  typed field, and (as the nested-remark bug above shows) requires scanning
  the full subtree of a record, not just its direct children, to reliably
  find everything a source actually publishes. If a source ever adopts a
  dedicated crypto field, the adapter should be updated to prefer it — this
  is exactly what happened historically with UK/OFSI.
- **UK**'s `CryptoWalletAddress` field is new and currently empty; worth
  periodic re-checking since it's the most likely source to "switch on" real
  structured data next, on top of its already-productive free-text fallback.
- **UN**'s stable entry point 302-redirects to a signed, time-limited Azure
  Blob URL; the adapter follows the redirect rather than hardcoding the blob
  URL, and records the actual fetched URL only in the manifest (not in the
  reproducible address files).

## Recommendation per source

- **OFAC**: productionize as-is. Real data, low structural risk, highest
  value.
- **UK**: productionize as-is. The free-text fallback already found real
  data the structured field didn't have; low cost to keep both paths running.
- **EU**: productionize with monitoring. Real data exists only via
  regex-on-prose extraction — add periodic manual review of `keyword_nearby`
  log output to catch both false positives and prose-format drift, and
  periodically re-check whether remarks appear in other locations besides
  `sanctionEntity` and `nameAlias`.
- **UN**: keep running for completeness; low expected future value given no
  structured field and no historical free-text hits, but the cost of keeping
  it is low and it contributes a correctly-empty, well-validated output
  rather than a silent gap.
- **Switzerland SECO**: productionize as-is — live-verified, 24,483 records
  parsed correctly. Watch for recurrence of the VPN-related connectivity
  issue (see above) if the daily publish job or a contributor's machine
  suddenly can't reach it again; don't assume the code broke without
  checking the network path first.
