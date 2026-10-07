# Third-party dependencies

Harness Courier's own source is distributed under the MIT License in `LICENSE`. External dependencies retain their own licenses. The source release contains no downloaded third-party source trees or binaries.

| Component | Use | Upstream |
| --- | --- | --- |
| psutil | Windows process and listener ownership checks | https://github.com/giampaolo/psutil |
| websocket-client | Local CDP transport | https://github.com/websocket-client/websocket-client |
| Cua Driver, optional | Background computer input integration | https://github.com/trycua/cua |
| KimiCU, legacy optional | Compatibility diagnostics only | Supplied separately by its distributor |

Cua's upstream root `LICENSE.md` identifies MIT and copyright Cua AI, Inc. This repository includes only our integration scripts, policy and fixture, not the Driver. The optional integration expects version 0.33.3; that is not a claim that it is the latest upstream release. Review the exact downloaded release's license and notices before redistributing it. The root license does not substitute for checking component-specific notices.

psutil and websocket-client are installed separately by the package manager. If distributing them or a standalone executable containing them, include the notices from the exact dependency versions being shipped.

Codex, Kimi Code and ZCode are external applications. They are not included or relicensed by this project. Product names identify integrations and do not imply endorsement.

References: [Cua root license](https://github.com/trycua/cua/blob/main/LICENSE.md), [MIT template](https://opensource.org/license/mit).
