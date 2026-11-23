# 04 — Licensing (Mirage / Dreamtype, trial fonts, OFL)

> Compiled 2026-11-23. Not legal advice.

## Mirage (the font in this repo)

The files in `Mirage/` are **TRIAL** builds from Dreamtype ("Magic as a Service™ GmbH"). The name table points to https://dreamtype.xyz/legal/licence-agreement (read: version v251110, Nov 10 2025). The trials page is https://dreamtype.xyz/trials.

Relevant clauses (quoted):

**Trials**
- **§2.3.8:** the Trials licence "grants full access to all available licence types, with the sole restriction that the Fonts may only be used for non-commercial purposes."
- **§4.2:** trials may be used for pitches "as long as the work remains in the draft stage and is not published or shared publicly".
- **§4.3:** trials "may not be shared to third parties."

**Derivative works and modification**
- **§7.3:** "The Licensee agrees not to create derivative works based on the Fonts or any part thereof, nor to use the Fonts in combination with software or hardware designed to generate such derivative works."
- **§7.1:** no modifications to the source code "unless such changes are necessary for the intended use of the software."

**Sharing and access**
- **§8.1:** no making Fonts available "by … embedding them in software, tools, or platforms that would grant access to unauthorised users."
- **§10.5:** no use to train AI/ML systems.

**Reading:**
- §7.3 is not limited to paid licences, and appears to cover trials too.
- A tool whose purpose is generating variations is exactly "software … designed to generate such derivative works".
- **Running this tool on Mirage likely breaches the licence, even privately and non-commercially, unless Dreamtype gives written permission.**

**Options:**
1. Email Dreamtype (support@dreamtype.xyz) and ask permission for experimental or derivative use. Some foundries grant this or offer custom licences.
2. Develop and test against **OFL fonts**. Rename outputs to avoid Reserved Font Names.
3. Keep Mirage purely as a private visual reference and never commit outputs. This is still a grey area under §7.3.

## General pattern

- Trial EULAs almost always forbid modification (Type Supply, Fontfabric, Grilli, Dalton Maag, TypeType).
- Commercial EULAs often forbid derivative works, sometimes including format conversion.

## Implications for the tool

- Show a notice: "Only process fonts whose licence permits modification."
- Process fonts **on the user's device**, not on a server, so the tool never receives copies of licensed fonts (this also helps with §8.1-style clauses).
- **Auto-rename outputs by default** (new family name, unique ID, PostScript name), to satisfy the OFL Reserved Font Name rules.
- Bundle 1–2 OFL fonts as defaults and demos.

## OFL candidates for development

Any OFL serif or sans from Google Fonts or GitHub works; check each font's RFN line. Examples:
- Source Serif 4 / Source Sans 3 (RFN "Source")
- IBM Plex (RFN "Plex")
- Inter (no RFN)
- EB Garamond
- Fraunces (variable, which helps testing)
- Instrument Serif
