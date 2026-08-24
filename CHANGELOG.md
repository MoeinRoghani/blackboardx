# Changelog

## [0.3.0](https://github.com/MoeinRoghani/blackboardx/compare/v0.2.1...v0.3.0) (2026-08-24)


### ⚠ BREAKING CHANGES

* **model:** `create_model` no longer accepts `agents`. Register each agent with `control.register_agent` after creating the model. Registering now wakes the agent, and an agent registered during a run is woken with everything already on the board rather than only subsequent changes.

### Features

* **board:** a storage protocol so the board can be substituted ([#57](https://github.com/MoeinRoghani/blackboardx/issues/57)) ([4de8a91](https://github.com/MoeinRoghani/blackboardx/commit/4de8a911bae5b672a0c3ec38eaefa49af6729474)), closes [#55](https://github.com/MoeinRoghani/blackboardx/issues/55)
* **control:** an agent declares the regions it hears about and the levels it writes ([#63](https://github.com/MoeinRoghani/blackboardx/issues/63)) ([192936e](https://github.com/MoeinRoghani/blackboardx/commit/192936efe8acf0f45d3327dad9c05515ca7432b4)), closes [#61](https://github.com/MoeinRoghani/blackboardx/issues/61)
* **model:** agents register themselves rather than being named at creation ([#60](https://github.com/MoeinRoghani/blackboardx/issues/60)) ([0af78e3](https://github.com/MoeinRoghani/blackboardx/commit/0af78e3f60860680ffd84164ee8e78fcc4e314bb)), closes [#58](https://github.com/MoeinRoghani/blackboardx/issues/58)


### Documentation

* the blackboard service design ([#54](https://github.com/MoeinRoghani/blackboardx/issues/54)) ([3fb20d7](https://github.com/MoeinRoghani/blackboardx/commit/3fb20d7d6a4b94ac0605fe489bb0b6369a949439)), closes [#53](https://github.com/MoeinRoghani/blackboardx/issues/53)

## [0.2.1](https://github.com/MoeinRoghani/blackboardx/compare/v0.2.0...v0.2.1) (2026-08-17)


### Bug Fixes

* **ci:** reach the publish workflow when a token-created release triggers nothing ([#39](https://github.com/MoeinRoghani/blackboardx/issues/39)) ([2db8f8a](https://github.com/MoeinRoghani/blackboardx/commit/2db8f8ad4b56bc2c51f16a50931b4f49df856f55)), closes [#38](https://github.com/MoeinRoghani/blackboardx/issues/38)
* **docs:** keep the decision records off the published site ([#51](https://github.com/MoeinRoghani/blackboardx/issues/51)) ([d8de96c](https://github.com/MoeinRoghani/blackboardx/commit/d8de96cbacf3f008f8fa376ef1bfcc7afb02ef6f)), closes [#50](https://github.com/MoeinRoghani/blackboardx/issues/50)
* **docs:** show the site navigation on the landing page ([#49](https://github.com/MoeinRoghani/blackboardx/issues/49)) ([e31ce4a](https://github.com/MoeinRoghani/blackboardx/commit/e31ce4a9484c9193c9b1fa105c722c4604ea0051)), closes [#48](https://github.com/MoeinRoghani/blackboardx/issues/48)


### Documentation

* a documentation site built from the repository ([#43](https://github.com/MoeinRoghani/blackboardx/issues/43)) ([f106b05](https://github.com/MoeinRoghani/blackboardx/commit/f106b05c4385feccdedb014b76c3548388ce6773)), closes [#42](https://github.com/MoeinRoghani/blackboardx/issues/42)
* the repository name in every reference to it ([#47](https://github.com/MoeinRoghani/blackboardx/issues/47)) ([d39a817](https://github.com/MoeinRoghani/blackboardx/commit/d39a8171a4d325b2c7497259cf2ccfd296fe3587)), closes [#46](https://github.com/MoeinRoghani/blackboardx/issues/46)

## [0.2.0](https://github.com/MoeinRoghani/blackboard/compare/v0.1.0...v0.2.0) (2026-08-17)


### Features

* **board:** add the board with levels, registers, and one total order ([#19](https://github.com/MoeinRoghani/blackboard/issues/19)) ([b7f65bf](https://github.com/MoeinRoghani/blackboard/commit/b7f65bf0989782f9e3bdfe9a1cd9ef0a3e19c5a6)), closes [#9](https://github.com/MoeinRoghani/blackboard/issues/9)
* **control:** admission, the write path, and the audit ([#26](https://github.com/MoeinRoghani/blackboard/issues/26)) ([e60733f](https://github.com/MoeinRoghani/blackboard/commit/e60733f226b02a0c25734ffa32aaa67b508f17cc)), closes [#21](https://github.com/MoeinRoghani/blackboard/issues/21)
* **control:** completion, budgets, and run outcomes ([#31](https://github.com/MoeinRoghani/blackboard/issues/31)) ([417c652](https://github.com/MoeinRoghani/blackboard/commit/417c652cfc7b843a3817edfe8dd0781d536e9587)), closes [#23](https://github.com/MoeinRoghani/blackboard/issues/23)
* **control:** registry, notification, and acknowledgment ([#27](https://github.com/MoeinRoghani/blackboard/issues/27)) ([97abf74](https://github.com/MoeinRoghani/blackboard/commit/97abf74a7877a09edfb21bef98915cb329439b0d)), closes [#22](https://github.com/MoeinRoghani/blackboard/issues/22)
* **model:** model creation from the six declarations ([#32](https://github.com/MoeinRoghani/blackboard/issues/32)) ([ab08957](https://github.com/MoeinRoghani/blackboard/commit/ab08957b683b2eec3a21cf134659648c9e19b966)), closes [#24](https://github.com/MoeinRoghani/blackboard/issues/24)


### Documentation

* absolute license link for the PyPI project page ([#35](https://github.com/MoeinRoghani/blackboard/issues/35)) ([10b96aa](https://github.com/MoeinRoghani/blackboard/commit/10b96aa3b48357141334c0edd0303d082e256451)), closes [#34](https://github.com/MoeinRoghani/blackboard/issues/34)
* ADR 0001, project scope ([#18](https://github.com/MoeinRoghani/blackboard/issues/18)) ([20446b8](https://github.com/MoeinRoghani/blackboard/commit/20446b8cfd7d30c65c74027a30af4df710c85e7b)), closes [#8](https://github.com/MoeinRoghani/blackboard/issues/8)
* CONTRIBUTING ([#16](https://github.com/MoeinRoghani/blackboard/issues/16)) ([901a1be](https://github.com/MoeinRoghani/blackboard/commit/901a1be59e0ecd6e61071c61a1cc8fb976ae43cb)), closes [#7](https://github.com/MoeinRoghani/blackboard/issues/7)
* README ([#20](https://github.com/MoeinRoghani/blackboard/issues/20)) ([7c9912a](https://github.com/MoeinRoghani/blackboard/commit/7c9912a384320f993843d92aaccebba1e10fdf8f)), closes [#6](https://github.com/MoeinRoghani/blackboard/issues/6)
* the release and publish procedure ([#37](https://github.com/MoeinRoghani/blackboard/issues/37)) ([bba2d6b](https://github.com/MoeinRoghani/blackboard/commit/bba2d6b21d8abc9da3e5eb1629f46e845aae5078)), closes [#36](https://github.com/MoeinRoghani/blackboard/issues/36)
