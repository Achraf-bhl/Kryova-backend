# Fatigue data: what exists, what it costs, and what Kryova may take from it (master plan E21.5)

Written 2026-09-15. The register in code is `app/fatigue/entitlements.py`, and
`tests/test_fatigue_entitlements.py` fails when a fatigue module cites a document the register
does not carry. This file is the prose beside it. **Nothing here is legal advice.** Nobody outside
the repository has reviewed it.

## The question the plan asked

E21.5 recorded that the research pass had returned "nothing verified" on the Eurocode detail
categories and the FKM guideline: their licence terms, whether they are free, and what may be
implemented rather than reproduced. That was an assumption with no source behind it.

## What was found

| Document | Held how | What the code takes | Price read | Terms read |
|---|---|---|---|---|
| EN 1993-1-9:2005 (+AC2) | Third-party copy of a Public.Resource.Org compilation | Tables 8.3–8.5, B.1 and 3.1, Figure 7.1, §7–§8 | No | No |
| IIW-1823-07 (Hobbacher 2008) | Third-party copy | §2.2.3.4 and Table 2.2-2 (hot-spot extrapolation) | No | No |
| NACA TN 2805 (1952) | NASA NTRS | Formula (1) and Figure 3's Neuber constant | Free | No |
| FKM Guideline, 7th ed. 2020, EN | Not held | Nothing directly | **EUR 320.00 incl. VAT, 232 pp.** | Not on the shop page |
| FKM methods through pyLife | Not held | FKM-Goodman mean-stress correction; extended Neuber rule | — | pyLife is Apache-2.0 (2.3.1 metadata); the guidelines were not read |
| BS 7608 | Not held | Nothing | No | No |

Sources, all read on 2026-09-15 unless the register says otherwise:

- **FKM EN price:** `https://www.vdmashop.de/en/Analytical-Strength-Assessment-7th.-Ed.-2020-EN/107457`.
  The page also gives product 107457 and ISBN 978-3-8163-0745-7. That price is what a visitor who
  is not logged in sees; the shop says members see their own prices after logging in.
- **The principle:** TRIPS Art. 9(2), quoted from
  `https://www.wto.org/english/docs_e/legal_e/27-trips_04_e.htm`: *"Copyright protection shall
  extend to expressions and not to ideas, procedures, methods of operation or mathematical
  concepts as such."*
- **Eurocodes as national standards:** the JRC's "L3 The Eurocodes" leaflet on
  `eurocodes.jrc.ec.europa.eu`.
- **C-588/21 P (5 March 2024):** read only as the Court's summary reproduced by INSIGHT EU
  Monitoring. That summary says the four harmonised toy-safety standards in question "form part of
  EU law owing to their legal effects", and that there is an overriding public interest in their
  disclosure. **The judgment itself was not retrieved**, because curia and EUR-Lex both refused
  the fetch. Whether its reasoning reaches a Eurocode was not established.

## What this settles, and what it does not

**Settled:**
- The FKM static guideline has a public price, and nothing has been taken from it.
- BS 7608 is not held.
- Every document the fatigue code does take from is named, dated and located, and a test holds
  the code to that list.

**Found:** FKM methods already reach the product through pyLife. pyLife's implementation is
Apache-2.0, and the guidelines behind it have not been read. The register records that instead of
the "nothing taken from FKM" the plan implied.

**Not settled, and each needs counsel or the publisher rather than another reading session:**

1. **Tables.** `weld_catalogue.py` encodes EN 1993-1-9's category numbers and the conditions
   attached to them. TRIPS 9(2) puts methods outside copyright. It does not say whether a table
   of detail categories is a method or an expression.
2. **Third-party copies.** Two of the documents were read from copies not hosted by their
   publishers.
3. **FKM's terms.** The shop page states none. The question is for VDMA Verlag.
4. **Other terms.** The terms of use of NTRS and IIW were not looked up.
