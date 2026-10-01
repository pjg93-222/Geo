Geist Sans and Geist Mono (variable, weight 100-900), version 1.400.

Provenance: copied byte for byte from the copy already installed on this computer, at
  C:\Users\pgoff\.claude\plugins\marketplaces\caveman\extension\fonts\
Nothing was downloaded and no package was installed. sha256 of the files here equals sha256 of those sources:
  geist-sans.woff2  46525b05bbf4527d263f8624ca7024c7d0dce3c64430267d30c31c6dd3821e4d
  geist-mono.woff2  3ef2c50dc1042ebfa89ca66bb6901e9dde5737970547c21b4de7a44dfb2ab948

Licence: Geist is published by Vercel under the SIL Open Font License 1.1. The licence text is not inside these woff2
files (their name tables are stripped), so keep it with the upstream release if the fonts ever leave this copy.

PENDING PAUL'S OK (IMPLEMENTATION_PLAN Q3). The stylesheet falls back to Segoe UI Variable Text / Segoe UI if these two
files are removed (see --sans in ../styles/tokens.css); layout does not depend on them, only exact glyph widths do.
