# Third-Party Licenses & Notices

PhishLens Edge builds on open-source research code. This file lists every
third-party component used in this repository together with its license.
Full license texts are kept in the upstream checkouts; the checkouts
themselves are **not** published in this repository (see `.gitignore`) —
clone them from the URLs below.

## Research pipelines (code reused / reproduced)

| Component | Upstream | License |
|---|---|---|
| **Phishpedia** | https://github.com/lindsey98/Phishpedia (USENIX Security 2021) | **CC0-1.0** |
| **PhishIntention** | https://github.com/lindsey98/PhishIntention (USENIX Security 2022) | **CC0-1.0** |
| **PhishVLM** | https://github.com/code-philia/PhishVLM | no LICENSE file in upstream — used for research reference only; its inference path is replaced by a local VLM |

## Python runtime & ML stack

| Component | License |
|---|---|
| PyTorch / torchvision (2.1.0+cpu) | BSD-3-Clause (https://github.com/pytorch/pytorch/blob/main/LICENSE) |
| detectron2 (0.6) | Apache-2.0 (https://github.com/facebookresearch/detectron2/blob/main/LICENSE) |
| numpy | BSD-3-Clause |
| Pillow | HPND (MIT-CMU derivative) |
| FastAPI | MIT (https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| uvicorn | BSD-3-Clause (https://github.com/encode/uvicorn/blob/master/LICENSE.md) |
| pydantic | MIT (https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| tldextract | BSD-3-Clause (https://github.com/john-kurkowski/tldextract/blob/master/LICENSE) |
| PyYAML | MIT (https://github.com/yaml/pyaml/blob/master/LICENSE) |

## Fonts used in the competition brief

| Font | License |
|---|---|
| Source Han Serif CN (Noto Serif CJK) | SIL Open Font License 1.1 (https://github.com/adobe-fonts/source-han-serif/blob/master/LICENSE.txt) |
| Inter / default Typst fonts | SIL OFL 1.1 / per-font notices |

## Data & trademarks

- Phishing URLs: OpenPhish feed (https://openphish.com/phishing_feeds.html),
  used for research and evaluation only.
- Brand logos in `brand_library/logos/` are the trademarks of their respective
  owners (Alibaba, Tencent, banks, 12306, CHSI, etc.). They are used **solely
  for academic research and local evaluation**; they are not part of the
  project's own licensed assets and must not be redistributed commercially
  without the owners' permission.

## CPU wheels used during development (not published in this repo)

| Wheel | Source |
|---|---|
| torch-2.1.0+cpu / torchvision-0.16.0+cpu (cp311, win_amd64) | official PyTorch CPU index (https://download.pytorch.org/whl/cpu) |
| detectron2-0.6+18f6958pt2.1.0cpu-cp311-cp311-win_amd64.whl | community Windows build (MiroPsota) — verify its provenance before use; upstream detectron2 does not ship Windows wheels |